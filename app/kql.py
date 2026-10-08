"""KQL access to the production order-book metrics table (Fabric Eventhouse).

``ctrader_dom.agg_dom`` holds derived per-tick order-book metrics
(``dom_stream_raw -> dom_book_flat -> agg_dom``) and now lives in a KQL database
instead of the SQL analytics endpoint. The column names are unchanged, so
`app.frames.from_ticks` is untouched by the move; only the transport (KQL
instead of T-SQL) and the endpoint differ.

The instrument dimension (``symbols_icmarkets`` and its asset-class chain)
stays on the SQL analytics endpoint and is read by `app.db`.

Authentication mirrors the SQL side: the VM's managed identity authenticates
the client directly, with a service principal, then an ``az login`` session, as
fallbacks. `azure-kusto-data` mints and refreshes the token itself, so no
secret is handed around here.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Iterator

import pandas as pd
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import (AzureCliCredential, ClientSecretCredential,
                            ManagedIdentityCredential)
from azure.kusto.data import (ClientRequestProperties, KustoClient,
                              KustoConnectionStringBuilder)
from azure.kusto.data.exceptions import KustoError

from .config import Settings, get_settings
from .db import DataSourceError

#: A plain Kusto entity name; anything else is bracket-escaped.
_PLAIN_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Columns of one raw tick-metric row, unchanged from the SQL table.
METRIC_COLUMNS = [
    "timestamp", "symbolId", "total_bid", "total_ask", "best_bid", "best_ask",
    "imbalance", "imbalance_ratio", "vwap_bid", "vwap_ask", "vwap_spread",
    "rel_spread", "rel_vwap_spread",
]


def table_ref(name: str) -> str:
    """Kusto reference to a table, bracket-quoted when the name is not plain."""
    name = str(name)
    if _PLAIN_IDENT.match(name):
        return name
    return "['" + name.replace("'", "") + "']"


def _credential(settings: Settings):
    """An `azure-identity` credential matching the configured auth mode.

    Managed identity wins, exactly as on the SQL side, so a leftover secret in
    the environment cannot silently change the auth path.
    """
    if settings.use_managed_identity:
        if settings.managed_identity_client_id:
            return ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
        return ManagedIdentityCredential()
    if settings.has_credentials:
        return ClientSecretCredential(
            tenant_id=settings.tenant_id,
            client_id=settings.client_id,
            client_secret=settings.client_secret,
        )
    return AzureCliCredential()


@contextmanager
def connect(settings: Settings | None = None) -> Iterator[KustoClient]:
    """A Kusto client for the configured endpoint, closed on exit."""
    s = settings or get_settings()
    if not s.kql_host:
        raise DataSourceError(
            "The KQL endpoint is not configured. Set KQL_ENDPOINT_PROD in .env."
        )
    try:
        kcsb = KustoConnectionStringBuilder.with_azure_token_credential(s.kql_host, _credential(s))
        client = KustoClient(kcsb)
    except KustoError as exc:
        raise DataSourceError(f"Could not build the KQL client: {exc}") from exc
    try:
        yield client
    finally:
        try:
            client.close()
        except Exception:
            pass


def query(client: KustoClient, csl: str, params: dict[str, Any] | None = None,
          settings: Settings | None = None) -> pd.DataFrame:
    """Run one KQL query and return its primary result table as a DataFrame.

    `params` are sent as declarative query parameters, so the query text never
    interpolates user input - the injection surface the SQL side closes with
    `?` placeholders is closed the same way here.
    """
    s = settings or get_settings()
    props = ClientRequestProperties()
    for key, value in (params or {}).items():
        if value is None:
            continue
        props.set_parameter(key, value if isinstance(value, str) else str(value))
    try:
        result = client.execute(s.kql_database, csl, props)
    except (KustoError, ClientAuthenticationError) as exc:
        raise DataSourceError(f"KQL query failed: {exc}\nKQL: {csl[:400]}") from exc
    table = result.primary_results[0]
    columns = [c.column_name for c in table.columns]
    return pd.DataFrame([row.to_dict() for row in table], columns=columns)


def _kusto_datetime(value: Any) -> str:
    """An ISO-8601 string Kusto casts to `datetime`, normalised to UTC."""
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC")
    return ts.isoformat()


# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------

def server_time(client: KustoClient, settings: Settings | None = None) -> str:
    """The endpoint's own clock, so the UI can compare it with the newest row."""
    df = query(client, "print server_time = now()", settings=settings)
    return "" if df.empty else str(df["server_time"].iloc[0])


def tick_stats(client: KustoClient, settings: Settings | None = None) -> tuple[int, str | None]:
    """(row count, newest timestamp) for the order-book metrics table."""
    s = settings or get_settings()
    df = query(
        client,
        f"{table_ref(s.kql_table)}\n| summarize n = count(), latest = max(timestamp)",
        settings=s,
    )
    if df.empty:
        return 0, None
    count = int(df["n"].iloc[0])
    latest = df["latest"].iloc[0]
    return count, (None if latest is None or pd.isna(latest) else str(latest))


def symbol_tick_counts(client: KustoClient, settings: Settings | None = None) -> dict[str, int]:
    """{symbolId: tick metric rows} for the whole table.

    ``symbolId`` is a ``long``, so the grouping key is the column itself; the
    string form the rest of the app keys on is applied in Python, which keeps a
    single place responsible for normalising it.
    """
    s = settings or get_settings()
    df = query(
        client,
        f"""
        {table_ref(s.kql_table)}
        | where isnotnull(symbolId)
        | summarize ticks = count() by symbolId""",
        settings=s,
    )
    return {str(r.symbolId).strip(): int(r.ticks) for r in df.itertuples()}


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------

#: `symbolId` is a `long` in the KQL schema (confirmed with `getschema`), so the
#: parameter is cast rather than the column - comparing `tolong(sym)` keeps the
#: column's own type for the engine while a non-numeric or blank parameter
#: simply yields no rows instead of failing.
_SYMBOL_MATCH = '| where sym == "" or symbolId == tolong(sym)'

_ANCHOR = f"""
declare query_parameters(sym:string = "");
{{obj}}
| where isnotnull(symbolId)
{_SYMBOL_MATCH}
| summarize mx = max(timestamp)
"""

_FETCH = f"""
declare query_parameters(sym:string = "", start:datetime = datetime(null), end:datetime = datetime(null));
{{obj}}
| where isnotnull(symbolId)
{_SYMBOL_MATCH}
| where isnull(start) or timestamp >= start
| where isnull(end) or timestamp <= end
| top {{limit}} by timestamp asc
| project {{columns}}
"""


def fetch_ticks(client: KustoClient, *, symbol: str | None = None,
                start: str | None = None, end: str | None = None,
                limit: int = 50_000, lookback_minutes: int | None = None,
                settings: Settings | None = None) -> pd.DataFrame:
    """Read raw tick-metric rows for one symbol, oldest first.

    With ``lookback_minutes`` and no explicit range, the window is anchored on
    the newest row so the result is the most recent session rather than an
    arbitrary slice of history.
    """
    s = settings or get_settings()
    obj = table_ref(s.kql_table)
    params: dict[str, Any] = {"sym": str(symbol).strip() if symbol else ""}

    if lookback_minutes and not start and not end:
        try:
            anchor = query(client, _ANCHOR.format(obj=obj), params, settings=s)
            mx = None if anchor.empty else anchor["mx"].iloc[0]
        except DataSourceError:
            mx = None
        if mx is not None and not pd.isna(mx):
            cutoff = pd.Timestamp(mx) - pd.Timedelta(minutes=int(lookback_minutes))
            params["start"] = _kusto_datetime(cutoff)

    if start:
        params["start"] = start
    if end:
        params["end"] = end

    csl = _FETCH.format(obj=obj, limit=int(limit), columns=", ".join(METRIC_COLUMNS))
    return query(client, csl, params, settings=s)
