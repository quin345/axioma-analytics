"""Data source access: connection, table resolution, and tick fetching."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import pandas as pd
import pyodbc

from .auth import TokenError, get_access_token
from .config import Settings, get_settings
from .books import classify as classify_book_table
from .l2 import is_l2_table, map_l2_columns
from .schema import ColumnMap, build_column_map


def ident(name: str) -> str:
    """Bracket-quote a catalog identifier."""
    return "[" + str(name).replace("]", "]]") + "]"


class DataSourceError(RuntimeError):
    pass


@dataclass
class TableInfo:
    schema: str
    table: str
    columns: list[str]
    column_map: ColumnMap
    is_l2: bool = False
    _kind: str = ""
    database: str = ""   # empty means "the connection default database"

    @property
    def qualified(self) -> str:
        """Fully qualified, cross-database safe object name."""
        if self.database:
            return f"{ident(self.database)}.{ident(self.schema)}.{ident(self.table)}"
        return f"{ident(self.schema)}.{ident(self.table)}"

    @property
    def label(self) -> str:
        if self.database:
            return f"{self.database}.{self.schema}.{self.table}"
        return f"{self.schema}.{self.table}"

    @property
    def kind(self) -> str:
        """Reader that applies to this table: agg | levels | l2 | flat."""
        return self._kind or ("l2" if self.is_l2 else "flat")

    def to_dict(self) -> dict:
        """Internal health payload.

        Deliberately omits schema/table/database: physical names stay inside the
        server and are never exposed over the API.
        """
        return {
            "kind": self.kind,
            "column_count": len(self.columns),
            "synthetic": False,
        }


def connection_string(settings: Settings | None = None) -> str:
    """Base connection string.

    Service-principal credentials are embedded here. The `az login` token path
    cannot be expressed as a connection string (the JWT is rejected as a PWD
    value), so it is applied separately via `attrs_before` in `connect()`.
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
        raise DataSourceError("The data source host is not configured")

    attrs: dict | None = None
    try:
        cs = connection_string(s)
        if not s.has_credentials:
            token = get_access_token()
            if token is None:
                raise DataSourceError(
                    "No credentials configured. Set the credential variables in "
                    ".env, or run `az login`."
                )
            # Not valid together with Authentication / Trusted_Connection in the string.
            attrs = {_ACCESS_TOKEN_ATTR: token.value}
    except TokenError as exc:
        raise DataSourceError(str(exc)) from exc

    try:
        conn = pyodbc.connect(cs, autocommit=True, attrs_before=attrs) if attrs else \
            pyodbc.connect(cs, autocommit=True)
    except pyodbc.Error as exc:
        raise DataSourceError(
            f"Could not connect to the data source: {exc}\n"
            "Check that the configured credentials and host are correct, and that "
            "the principal has read access to the workspace."
        ) from exc
    try:
        yield conn
    finally:
        conn.close()


def query(conn: pyodbc.Connection, sql: str, params: tuple | None = None) -> pd.DataFrame:
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
# Catalog discovery
# --------------------------------------------------------------------------

_DISCOVERY_SQL = """
SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME
FROM INFORMATION_SCHEMA.COLUMNS
ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
"""

_SKIP_SCHEMAS = {"sys", "sysinternal", "information_schema", "dbfs", "graph", "guest"}
_SYSTEM_DATABASES = {"master", "tempdb", "model", "msdb"}


def list_databases(conn: pyodbc.Connection) -> list[str]:
    """All user databases on this endpoint.

    A Fabric SQL analytics endpoint fronts every Warehouse/Lakehouse in the
    workspace, and INFORMATION_SCHEMA only sees the *default* database - so the
    other databases must be enumerated explicitly.
    """
    try:
        df = query(conn, "SELECT name FROM sys.databases ORDER BY name")
    except pyodbc.Error:
        return []
    return [str(n) for n in df["name"] if str(n).lower() not in _SYSTEM_DATABASES]


def list_all_tables(conn: pyodbc.Connection) -> list[TableInfo]:
    """Every user table in every database (no tick-signature filtering).

    Used for the catalog view and to locate symbol-dimension tables that are not
    tick sources themselves.
    """
    out: list[TableInfo] = []
    for db in list_databases(conn):
        try:
            df = query(conn, f"""
                SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME
                FROM {ident(db)}.INFORMATION_SCHEMA.COLUMNS
                ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION""")
        except DataSourceError:
            continue
        grouped: dict[tuple[str, str], list[str]] = {}
        for schema, table, col in zip(df["TABLE_SCHEMA"], df["TABLE_NAME"], df["COLUMN_NAME"]):
            if str(schema).lower() in _SKIP_SCHEMAS:
                continue
            grouped.setdefault((str(schema), str(table)), []).append(str(col))
        for (schema, table), cols in grouped.items():
            cm = build_column_map(cols)
            out.append(TableInfo(schema=schema, table=table, columns=cols,
                                 column_map=cm, is_l2=is_l2_table(cols),
                                 _kind=classify_book_table(cols), database=db))
    return out


# Column names used by common symbol-dimension tables (gold/silver layers).
_SYMBOL_ID_ALIASES = ("symbolid", "symbol_id", "instrumentid", "tickerid")
_SYMBOL_NAME_ALIASES = ("symbolname", "symbol_name", "name", "displayname", "ticker")


def find_symbol_dimension(conn: pyodbc.Connection, tables: list[TableInfo] | None = None
                           ) -> TableInfo | None:
    """Locate a symbolId -> symbolName lookup table across all databases."""
    tables = tables if tables is not None else list_all_tables(conn)
    best: TableInfo | None = None
    best_name = ""
    for t in tables:
        lowered = {c.strip().lower(): c for c in t.columns}
        id_col = next((lowered[a] for a in _SYMBOL_ID_ALIASES if a in lowered), None)
        if not id_col:
            continue
        # Prefer the most specific name column (symbolName over generic "name").
        for alias in _SYMBOL_NAME_ALIASES:
            if alias in lowered:
                if len(alias) > len(best_name):
                    best, best_name = t, alias
                break
        if best is not None and best is not t:
            continue
    return best


def symbol_labels(conn: pyodbc.Connection, dim: TableInfo | None = None,
                  *, limit: int = 20_000) -> dict[str, str]:
    """Load {symbolId: symbolName} from the symbol dimension, if one exists."""
    dim = dim or find_symbol_dimension(conn)
    if dim is None:
        return {}
    lowered = {c.strip().lower(): c for c in dim.columns}
    id_col = next((lowered[a] for a in _SYMBOL_ID_ALIASES if a in lowered), None)
    name_col = next((lowered[a] for a in _SYMBOL_NAME_ALIASES if a in lowered), None)
    if not id_col or not name_col:
        return {}
    try:
        df = query(conn, f"SELECT TOP ({int(limit)}) {ident(id_col)} AS id, {ident(name_col)} AS nm "
                          f"FROM {dim.qualified} WHERE {ident(name_col)} IS NOT NULL")
    except DataSourceError:
        return {}
    out: dict[str, str] = {}
    for i, n in zip(df["id"], df["nm"]):
        if i is None or n is None:
            continue
        out[str(i).strip()] = str(n).strip()
    return out


def get_symbols(conn: pyodbc.Connection, ti: TableInfo, *, limit: int = 500) -> list[dict]:
    """Symbols ranked by tick count, for the UI selector."""
    sym_col = None
    if ti.kind in ("agg", "levels"):
        from .books import map_agg_columns, map_level_columns
        mapper = map_agg_columns if ti.kind == "agg" else map_level_columns
        sym_col = mapper(ti.columns).get("symbol")
    elif ti.is_l2:
        sym_col = map_l2_columns(ti.columns).get("symbol")
    sym_col = sym_col or ti.column_map.symbol
    if not sym_col:
        return []
    sql = (
        f"SELECT TOP ({int(limit)}) {ident(sym_col)} AS symbol, COUNT_BIG(*) AS ticks "
        f"FROM {ti.qualified} WHERE {ident(sym_col)} IS NOT NULL "
        f"GROUP BY {ident(sym_col)} ORDER BY COUNT_BIG(*) DESC"
    )
    try:
        df = query(conn, sql)
    except DataSourceError:
        return []
    if df.empty:
        return []
    return [
        {"symbol": str(r.symbol).strip().upper(), "ticks": int(r.ticks)}
        for r in df.itertuples()
        if r.symbol is not None
    ]
    return ";".join(parts)
# --------------------------------------------------------------------------
# Tick retrieval
# --------------------------------------------------------------------------

def fetch_ticks(
    conn: pyodbc.Connection,
    ti: TableInfo,
    *,
    symbol: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 50_000,
    lookback_days: int | None = None,
) -> tuple[pd.DataFrame, ColumnMap]:
    """Pull raw ticks as a DataFrame plus the column map used to build the SQL.

    When `lookback_days` is set and no explicit range is given, the query is
    anchored on the newest tick so you always get the most recent session
    rather than an arbitrary slice of history.
    """
    cm = ti.column_map
    q = ti.qualified
    clauses: list[str] = []
    params: list[Any] = []

    # DOM tables carry their own timestamp/symbol column names.
    l2m = map_l2_columns(ti.columns) if ti.is_l2 else {}
    ts_col = l2m.get("ts") or cm.ts
    sym_col = l2m.get("symbol") or cm.symbol

    if sym_col and symbol:
        clauses.append(f"{ident(sym_col)} = ?")
        params.append(symbol.strip())

    if lookback_days and not start and not end:
        anchor_where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        try:
            anchor = query(conn, f"SELECT MAX({ident(ts_col)}) AS mx FROM {q}{anchor_where}",
                           tuple(params) if params else None)
            mx = None if anchor.empty else anchor["mx"].iloc[0]
        except DataSourceError:
            mx = None
        if mx is not None and not pd.isna(mx):
            cutoff = pd.Timestamp(mx) - pd.Timedelta(days=int(lookback_days))
            clauses.append(f"{ident(ts_col)} >= ?")
            params.append(cutoff.tz_localize(None) if cutoff.tzinfo else cutoff)

    if start:
        clauses.append(f"{ident(ts_col)} >= ?")
        params.append(start)
    if end:
        clauses.append(f"{ident(ts_col)} <= ?")
        params.append(end)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    if ti.is_l2:
        # Only the DOM event columns are needed; fetching * would drag in
        # _rid/_ts delta bookkeeping columns.
        l2m = map_l2_columns(ti.columns)
        wanted = sorted({l2m[f] for f in ("ts", "symbol", "new_quotes", "deleted_quotes", "digits")
                         if l2m.get(f)})
        select = ", ".join(ident(c) for c in wanted)
        order = ident(l2m["ts"])
    else:
        select, order = "*", ident(cm.ts)

    sql = f"SELECT TOP ({int(limit)}) {select} FROM {q}{where} ORDER BY {order} ASC"
    return query(conn, sql, tuple(params) if params else None), cm