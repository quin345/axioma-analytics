"""KQL access to the production order-book metrics table (Fabric Eventhouse).

``ctrader_dom`` holds the whole pipeline - ``dom_stream_raw -> dom_book_flat ->
agg_dom`` - plus the ``symbols_icmarkets`` instrument dimension, and it is the
dashboard's only source of data. The metric column names are unchanged from the
old SQL table, so `app.frames.from_ticks` is untouched by the move; only the
transport (KQL instead of T-SQL) and the endpoint differ.

Authentication is Entra ID: the VM's managed identity authenticates the client
directly, with a service principal, then an ``az login`` session, as fallbacks.
`azure-kusto-data` mints and refreshes the token itself, so no secret is handed
around here.
"""
from __future__ import annotations

import logging
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
from .errors import DataSourceError

log = logging.getLogger(__name__)

#: A plain Kusto entity name; anything else is bracket-escaped.
_PLAIN_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Columns of one raw tick-metric row, unchanged from the SQL table.
METRIC_COLUMNS = [
    "timestamp", "symbolId", "total_bid", "total_ask", "best_bid", "best_ask",
    "imbalance", "imbalance_ratio", "vwap_bid", "vwap_ask", "vwap_spread",
    "rel_spread", "rel_vwap_spread",
]

#: Columns of one ``symbols_icmarkets`` row, as the metadata notebook ingests it
#: (``symbolId`` is written as a BIGINT, so it is projected back to a long here).
SYMBOL_COLUMNS = ["symbolId", "symbolName", "symbolCategoryId", "description"]

#: The window suffixes `metrics_5m` pins every windowed metric with, and the
#: number of minutes each stands for. `docs/metrics_5m.md` is the contract: the
#: column set is read **by name**, so it is declared here rather than inferred
#: from the result schema - a renamed or missing column must fail the read, not
#: silently return a panel of nulls.
WINDOW_SUFFIXES: tuple[tuple[str, int], ...] = (
    ("1h", 60), ("2h", 120), ("3h", 180), ("4h", 240),
)

#: The windowed scalar metrics, in the order `metrics_5m` materialises them.
#:
#: OHLC (`open`/`high`/`low`/`close`) is deliberately **not** here: the table the
#: dashboard reads ships the tick-level bars and the session extremes through
#: `/api/analytics` instead, so a window column for them was dropped from the
#: schema. Declaring a column the table does not have would read as `null` and
#: blank the tiles that `/api/analytics` fills correctly - see
#: `metrics.SCALAR_FIELDS` and the overlay in `app/static/app.js`.
WINDOW_METRICS = (
    "ticks", "vwap", "total_volume",
    "realized_vol_bps", "avg_spread_bps", "ofi_imbalance",
)

#: Derived per window by the KQL, but trivial functions of the columns above.
WINDOW_DERIVED = ("change_pct",)

#: Nested `dynamic` columns: the volume-at-price ladder and its point of control.
WINDOW_NESTED = ("vap", "poc")


def window_columns() -> list[str]:
    """Every windowed column of `metrics_5m`, `<metric>_<suffix>`.

    Pinned so the projection names each column explicitly. `evaluate pivot` is
    deliberately avoided: its output schema is data-derived, which is exactly
    the kind of instability this app cannot tolerate in a read it serves.
    """
    cols: list[str] = []
    for suffix, _ in WINDOW_SUFFIXES:
        for name in (*WINDOW_METRICS, *WINDOW_DERIVED, *WINDOW_NESTED):
            cols.append(f"{name}_{suffix}")
    return cols


def window_suffix(minutes: int) -> str | None:
    """The `metrics_5m` suffix carrying a window of `minutes`, if it has one.

    The table only ever ships the four fixed windows, so a request for anything
    else has no column to read and resolves to None - the caller clamps it to
    the nearest available window rather than guessing.
    """
    for suffix, span in WINDOW_SUFFIXES:
        if span == int(minutes):
            return suffix
    return None


def best_window(minutes: int) -> str:
    """The nearest available window to `minutes`, never wider than requested.

    A window the table does not precompute is served with the widest one it does
    that is no wider than the ask: `metrics_5m` never ships a window the cache
    cannot serve, and silently returning a *wider* one would report a span the
    user did not choose.
    """
    want = int(minutes)
    choices = [span for _, span in WINDOW_SUFFIXES if span <= want]
    span = max(choices) if choices else min(s for _, s in WINDOW_SUFFIXES)
    return window_suffix(span) or WINDOW_SUFFIXES[0][0]


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


def symbol_count(client: KustoClient, settings: Settings | None = None) -> int:
    """Rows in the ``symbols_icmarkets`` dimension, for the health payload."""
    s = settings or get_settings()
    df = query(client, f"{table_ref(s.symbol_table)}\n| count", settings=s)
    return int(df["Count"].iloc[0]) if not df.empty else 0


# --------------------------------------------------------------------------
# Precomputed window metrics (`metrics_5m`)
# --------------------------------------------------------------------------

#: The column the refresh stamps each row with, used to keep the newest version
#: of a `(symbolId, as_of)` stop when the refresh appends rather than replaces.
#: The table the dashboard reads writes `computed_at`; the older name is tried
#: second so a table built to the previous contract still dedupes.
METRICS_STAMPS: tuple[str, ...] = ("computed_at", "ingested_at")

#: The table accumulates a version per `(symbolId, as_of)` every cycle, because
#: the refresh appends rather than replacing. `arg_max(<stamp>, *)` keeps the
#: newest row per stop, which is what makes "latest" well-defined - see the
#: *Dedup* note in `docs/metrics_5m.md`.
_METRICS_ANCHOR = """
declare query_parameters(sym:string = "");
{obj}
| where isnotnull(symbolId) and isnotnull(as_of)
{symbol_match}
| summarize mx = max(as_of)
"""


def _metrics_fetch(obj: str, stamp: str | None) -> str:
    """The grid read, optionally deduplicated on `stamp`.

    `stamp=None` is the last resort: a table appended once per stop has nothing
    to dedupe, so a plain ordered scan is the correct read rather than a failure.
    """
    head = (
        'declare query_parameters(sym:string = "", start:datetime = datetime(null));\n'
        f"{obj}\n"
        "| where isnotnull(symbolId) and isnotnull(as_of)\n"
        "{symbol_match}\n"
        "| where isnull(start) or as_of >= start\n"
    )
    if not stamp:
        return head + "| order by symbolId asc, as_of asc"
    return (
        head
        + f"| summarize arg_max({stamp}, *) by symbolId, as_of\n"
        + f"| project-away {stamp}\n"
        + "| order by symbolId asc, as_of asc"
    )

def metrics_anchor(client: KustoClient, *, symbol: str | None = None,
                   settings: Settings | None = None) -> object:
    """The newest `as_of` stop in `metrics_5m` for one symbol, or None.

    Anchoring on the table's own newest stop rather than the wall clock is what
    keeps the grid aligned with the data: the table is rebuilt on the cache
    cadence, so a stalled job leaves the newest stop where it was and the
    dashboard steps back from there instead of offering empty slots.
    """
    s = settings or get_settings()
    obj = table_ref(s.metrics_table)
    params = {"sym": str(symbol).strip() if symbol else ""}
    df = query(client, _METRICS_ANCHOR.format(obj=obj, symbol_match=_SYMBOL_MATCH),
               params, settings=s)
    if df.empty or pd.isna(df["mx"].iloc[0]):
        return None
    return df["mx"].iloc[0]


def fetch_metrics(client: KustoClient, *, symbol: str | None = None,
                  start: str | None = None, settings: Settings | None = None) -> pd.DataFrame:
    """Precomputed window-metric rows for one symbol, oldest `as_of` first.

    The whole point-in-time grid comes back in one read - 49 stops by default,
    every window already aggregated - so the dashboard's point-in-time and
    window controls cost nothing: they pick a row and a column set out of what
    is already in the process. `start` narrows to the stops at or after that
    timestamp, which is how the refresh cycle pulls only the newest slice
    instead of re-reading the grid it already holds.

    The table is rebuilt by appending, so the newest row per `(symbolId,
    as_of)` is kept - which is why the read deduplicates on a stamp column
    rather than assuming one row per stop. The stamp is discovered by trying
    `METRICS_STAMPS` in order and falling back to the plain ordered scan: a
    missing stamp is a schema difference, not an outage, and a table appended
    once per stop has nothing to dedupe. Probing costs one extra query only
    when a name is absent, so the steady state is a single read.
    """
    s = settings or get_settings()
    obj = table_ref(s.metrics_table)
    params: dict[str, Any] = {"sym": str(symbol).strip() if symbol else ""}
    if start:
        params["start"] = _kusto_datetime(start)

    for stamp in (*METRICS_STAMPS, None):
        csl = _metrics_fetch(obj, stamp).format(symbol_match=_SYMBOL_MATCH)
        try:
            return query(client, csl, params, settings=s)
        except DataSourceError as exc:
            # An unknown column name is the only failure worth retrying: any
            # other error (auth, throttle, a genuine outage) must surface.
            if stamp is None or f"'{stamp}'" not in str(exc):
                raise
            log.info("metrics_5m has no %s column; trying the next dedup stamp.",
                     stamp)
    raise DataSourceError(
        f"Could not read the precomputed metrics from {s.metrics_table}."
    )


def metrics_stats(client: KustoClient, settings: Settings | None = None) -> tuple[int, str | None]:
    """(distinct stops, newest `as_of`) of the precomputed table, for health."""
    s = settings or get_settings()
    df = query(
        client,
        f"{table_ref(s.metrics_table)}\n"
        "| summarize stops = dcount(as_of), latest = max(as_of)",
        settings=s,
    )
    if df.empty:
        return 0, None
    latest = df["latest"].iloc[0]
    return int(df["stops"].iloc[0]), (None if latest is None or pd.isna(latest) else str(latest))


def symbol_catalogue(client: KustoClient, settings: Settings | None = None) -> pd.DataFrame:
    """One row per instrument: id, ticker, category id and description.

    Replaces the SQL ``gold.symbols_icmarkets`` read. The table is a snapshot of
    the cTrader symbol list, so ``take_any`` picks the single row per id; ids the
    engine cannot cast to a long are dropped, matching the dimension's own type.
    """
    s = settings or get_settings()
    return query(
        client,
        f"""
        {table_ref(s.symbol_table)}
        | where isnotnull(symbolId)
        | extend symbolId = tolong(symbolId)
        | where isnotnull(symbolId)
        | summarize symbolName = take_any(tostring(symbolName)),
                    symbolCategoryId = take_any(tolong(symbolCategoryId)),
                    description = take_any(tostring(description))
                    by symbolId
        | project {", ".join(SYMBOL_COLUMNS)}
        """,
        settings=s,
    )


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
