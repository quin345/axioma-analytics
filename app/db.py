"""Gold database access: connection, snapshot fetch, symbol catalogue.

Every query in the app targets `[ctrader_lakehouse].[gold]` on the production
endpoint. Two kinds of object are read:

* `gold.agg_dom_book_snapshot` - one pre-aggregated row per symbol and
  timestamp, already carrying best bid/ask and resting sizes.
* `gold.symbols_icmarkets`        - the icmarkets instrument dimension,
  supplying the human ticker, the description and the category id that
  `app.assets` turns into an asset class.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import pandas as pd
import pyodbc

from .auth import TokenError, get_access_token
from .config import Settings, get_settings


class DataSourceError(RuntimeError):
    """The gold endpoint could not be reached or a query failed."""


def ident(name: str) -> str:
    """Bracket-quote a catalog identifier."""
    return "[" + str(name).replace("]", "]]") + "]"


def qualified(database: str, schema: str, table: str) -> str:
    """Fully qualified, cross-database safe object name."""
    return f"{ident(database)}.{ident(schema)}.{ident(table)}"


def snapshot_object(settings: Settings | None = None) -> str:
    """Qualified name of the aggregate book-snapshot table."""
    s = settings or get_settings()
    return qualified(s.gold_database, s.gold_schema, s.snapshot_table)


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------

def connection_string(settings: Settings | None = None) -> str:
    """ODBC connection string for the production endpoint.

    Service-principal credentials are embedded here. The `az login` token path
    cannot be expressed as a connection string (the driver rejects a JWT passed
    as PWD), so it is applied separately via `attrs_before` in `connect()`.
    """
    s = settings or get_settings()
    parts = [
        f"DRIVER={{{s.odbc_driver}}}",
        f"SERVER={s.server},{s.port}",
        "Encrypt=yes",
        "TrustServerCertificate=yes",
        f"Connection Timeout={s.connect_timeout}",
    ]
    if s.has_credentials:
        parts += [
            "Authentication=ActiveDirectoryServicePrincipal",
            f"UID={s.client_id}",
            f"PWD={s.client_secret}",
        ]
    return ";".join(parts)


# SQL_COPT_SS_ACCESS_TOKEN - the supported way to hand an AAD token to the driver.
_ACCESS_TOKEN_ATTR = 1256


@contextmanager
def connect(settings: Settings | None = None) -> Iterator[pyodbc.Connection]:
    s = settings or get_settings()
    if not s.host:
        raise DataSourceError(
            "The production endpoint is not configured. Set SQL_ENDPOINT_PROD in .env."
        )

    attrs: dict | None = None
    if not s.has_credentials:
        try:
            token = get_access_token()
        except TokenError as exc:
            raise DataSourceError(str(exc)) from exc
        if token is None:
            raise DataSourceError(
                "No credentials configured. Set the FABRIC_* variables in .env, "
                "or run `az login`."
            )
        # Not valid together with Authentication / Trusted_Connection in the string.
        attrs = {_ACCESS_TOKEN_ATTR: token.value}

    try:
        if attrs:
            conn = pyodbc.connect(connection_string(s), autocommit=True, attrs_before=attrs)
        else:
            conn = pyodbc.connect(connection_string(s), autocommit=True)
    except pyodbc.Error as exc:
        raise DataSourceError(
            f"Could not connect to the production endpoint: {exc}\n"
            "Check that the configured credentials and host are correct, and that "
            "the principal has read access to the workspace."
        ) from exc
    try:
        yield conn
    finally:
        conn.close()


def query(conn: pyodbc.Connection, sql: str, params: tuple | None = None) -> pd.DataFrame:
    """Run one statement and return the rows as a DataFrame."""
    cur = conn.cursor()
    try:
        cur.execute(sql, params) if params else cur.execute(sql)
        cols = [c[0] for c in cur.description] if cur.description else []
        return pd.DataFrame.from_records(cur.fetchall(), columns=cols)
    except pyodbc.Error as exc:
        raise DataSourceError(f"Query failed: {exc}\nSQL: {sql[:400]}") from exc
    finally:
        cur.close()


# --------------------------------------------------------------------------
# Symbol catalogue
# --------------------------------------------------------------------------

_DIM_COLUMNS = ["symbolId", "symbolName", "symbolCategoryId", "description", "assetClassName"]


def symbol_catalogue(conn: pyodbc.Connection, settings: Settings | None = None) -> pd.DataFrame:
    """icmarkets instrument dimension joined to its asset class.

    The asset class comes from the pipeline's own chain
    (``symbols_icmarkets`` -> ``symbols_category_icmarkets`` ->
    ``asset_classes_icmarkets``) rather than being inferred here, so the
    dashboard groups instruments exactly as the broker does.

    The joins are LEFT joins: an instrument whose category is missing from the
    reference tables still appears in the catalogue, with a blank asset class,
    rather than disappearing from the selector.
    """
    s = settings or get_settings()

    def gold(name: str) -> str:
        return qualified(s.gold_database, s.gold_schema, name)

    df = query(conn, f"""
        SELECT s.{ident('symbolId')}, s.{ident('symbolName')},
               s.{ident('symbolCategoryId')}, s.{ident('description')},
               a.{ident('name')} AS {ident('assetClassName')}
        FROM {gold(s.symbol_table)} AS s
        LEFT JOIN {gold('symbols_category_icmarkets')} AS c
               ON c.{ident('symbolCategoryId')} = s.{ident('symbolCategoryId')}
        LEFT JOIN {gold('asset_classes_icmarkets')} AS a
               ON a.{ident('assetClassId')} = c.{ident('assetClassId')}
        WHERE s.{ident('symbolId')} IS NOT NULL""")

    out = df.drop_duplicates(subset=["symbolId"], keep="first")
    out["symbolId"] = out["symbolId"].astype(str).str.strip()
    for col in ("symbolName", "description", "assetClassName"):
        out[col] = out[col].astype("string").str.strip()
    return out[_DIM_COLUMNS].reset_index(drop=True)


def symbol_tick_counts(conn: pyodbc.Connection, settings: Settings | None = None) -> dict[str, int]:
    """{symbolId: snapshot rows} from the gold book-snapshot table."""
    obj = snapshot_object(settings)
    df = query(conn, f"""
        SELECT {ident('symbolId')} AS symbolId, COUNT_BIG(*) AS ticks
        FROM {obj}
        WHERE {ident('symbolId')} IS NOT NULL
        GROUP BY {ident('symbolId')}""")
    return {str(r.symbolId).strip(): int(r.ticks) for r in df.itertuples() if r.symbolId is not None}


# --------------------------------------------------------------------------
# Ticks
# --------------------------------------------------------------------------

#: Columns of the canonical tick frame produced by ``frames.ticks_from_snapshot``.
TICK_COLUMNS = [
    "ts", "symbol", "bid", "ask", "last", "volume",
    "bid_depth", "ask_depth", "signed_volume",
    "imbalance", "imbalance_ratio",
    "vwap_bid", "vwap_ask", "vwap_spread", "rel_spread", "rel_vwap_spread",
]

_SNAPSHOT_COLUMNS = [
    "timestamp", "symbolId", "total_bid", "total_ask", "best_bid", "best_ask",
    "imbalance", "imbalance_ratio", "vwap_bid", "vwap_ask", "vwap_spread",
    "rel_spread", "rel_vwap_spread",
]


def fetch_ticks(conn: pyodbc.Connection, *, symbol: str | None = None,
                start: str | None = None, end: str | None = None,
                limit: int = 50_000, lookback_days: int | None = None) -> pd.DataFrame:
    """Read raw book snapshots for one symbol.

    With ``lookback_days`` and no explicit range, the window is anchored on the
    newest snapshot so the result is the most recent session rather than an
    arbitrary slice of history.
    """
    obj = snapshot_object()
    cols = ", ".join(ident(c) for c in _SNAPSHOT_COLUMNS)
    clauses: list[str] = []
    params: list[Any] = []

    if symbol:
        clauses.append(f"{ident('symbolId')} = ?")
        params.append(str(symbol).strip())

    if lookback_days and not start and not end:
        anchor_where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        try:
            anchor = query(conn, f"SELECT MAX({ident('timestamp')}) AS mx FROM {obj}{anchor_where}",
                           tuple(params) if params else None)
            mx = None if anchor.empty else anchor["mx"].iloc[0]
        except DataSourceError:
            mx = None
        if mx is not None and not pd.isna(mx):
            cutoff = pd.Timestamp(mx) - pd.Timedelta(days=int(lookback_days))
            clauses.append(f"{ident('timestamp')} >= ?")
            params.append(cutoff.tz_localize(None) if cutoff.tzinfo else cutoff)

    if start:
        clauses.append(f"{ident('timestamp')} >= ?")
        params.append(start)
    if end:
        clauses.append(f"{ident('timestamp')} <= ?")
        params.append(end)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (f"SELECT TOP ({int(limit)}) {cols} FROM {obj}{where} "
           f"ORDER BY {ident('timestamp')} ASC")
    return query(conn, sql, tuple(params) if params else None)

