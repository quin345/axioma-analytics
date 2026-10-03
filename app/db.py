"""Fabric DW access: connection, catalog discovery, and tick fetching."""
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


class WarehouseError(RuntimeError):
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
        return {
            "schema": self.schema,
            "table": self.table,
            "qualified": self.label,
            "kind": self.kind,
            "database": self.database,
            "columns": self.columns,
            "tick_columns": self.column_map.to_dict(),
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
        raise WarehouseError("SQL_ANALYTICS_ENDPOINT is not set")

    attrs: dict | None = None
    try:
        cs = connection_string(s)
        if not s.has_credentials:
            token = get_access_token()
            if token is None:
                raise WarehouseError(
                    "No credentials configured. Set FABRIC_TENANT_ID / FABRIC_CLIENT_ID / "
                    "FABRIC_CLIENT_SECRET in .env, or run `az login`."
                )
            # Not valid together with Authentication / Trusted_Connection in the string.
            attrs = {_ACCESS_TOKEN_ATTR: token.value}
    except TokenError as exc:
        raise WarehouseError(str(exc)) from exc

    try:
        conn = pyodbc.connect(cs, autocommit=True, attrs_before=attrs) if attrs else \
            pyodbc.connect(cs, autocommit=True)
    except pyodbc.Error as exc:
        raise WarehouseError(
            f"Could not connect to the Fabric SQL analytics endpoint: {exc}\n"
            "Check that (1) the principal has at least the Viewer role on the Fabric "
            "workspace holding the SQL analytics endpoint, and (2) the endpoint host is correct."
        ) from exc
    try:
        yield conn
    finally:
        conn.close()


# Fabric OneLake denies access at the *external policy* layer, not via a SQL
# role - and such objects are then hidden from the catalog entirely, so they look
# like they do not exist. Detect it and say so explicitly.
_PERMISSION_MARKERS = ("permission", "external policy", "was denied")


def is_permission_error(message: str) -> bool:
    m = str(message).lower()
    return any(marker in m for marker in _PERMISSION_MARKERS)


def permission_hint(message: str) -> str:
    """Actionable next step when a query is refused by OneLake policy."""
    if not is_permission_error(message):
        return ""
    return (
        "The object exists but this principal cannot read it. In Fabric, Lakehouse "
        "tables are also gated by OneLake security, not just the workspace role. "
        "Grant the service principal access to the table's folder under "
        "Workspace > prod_axioma > OneLake access control (e.g. Tables/dbo)."
    )


def query(conn: pyodbc.Connection, sql: str, params: tuple | None = None) -> pd.DataFrame:
    cur = conn.cursor()
    try:
        cur.execute(sql, params) if params else cur.execute(sql)
        cols = [c[0] for c in cur.description] if cur.description else []
        return pd.DataFrame.from_records(cur.fetchall(), columns=cols)
    except pyodbc.Error as exc:
        raise WarehouseError(f"Query failed: {exc}\nSQL: {sql[:400]}") from exc
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
        except WarehouseError:
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


def discover_tables(conn: pyodbc.Connection, *, min_score: int = 3,
                    databases: list[str] | None = None) -> list[TableInfo]:
    """Score schema.table across every database and return tick-like tables.

    Scans all databases because INFORMATION_SCHEMA is scoped to the current
    database, and a workspace endpoint exposes one database per DW/Lakehouse.
    """
    dbs = databases if databases is not None else list_databases(conn)
    grouped: dict[tuple[str, str, str], list[str]] = {}

    for db in dbs:
        try:
            df = query(conn, f"""
                SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME
                FROM {ident(db)}.INFORMATION_SCHEMA.COLUMNS
                ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION""")
        except WarehouseError:
            continue
        if df.empty:
            continue
        for schema, table, col in zip(df["TABLE_SCHEMA"], df["TABLE_NAME"], df["COLUMN_NAME"]):
            if str(schema).lower() in _SKIP_SCHEMAS:
                continue
            grouped.setdefault((db, str(schema), str(table)), []).append(str(col))

    found: list[TableInfo] = []
    for (db, schema, table), cols in grouped.items():
        cm = build_column_map(cols)
        l2 = is_l2_table(cols)
        kind = classify_book_table(cols)
        # DOM events and aggregated book snapshots are valid tick sources even
        # though they have no plain bid/ask columns - they are rebuilt at read time.
        if not cm.valid and kind not in ("l2", "agg", "levels"):
            continue
        score = 3 + (1 if (cm.bid and cm.ask) else 0) + (1 if cm.symbol else 0) + (1 if cm.volume else 0)
        if l2:
            score += 2
        if score >= min_score:
            found.append(TableInfo(schema=schema, table=table, columns=cols,
                                   column_map=cm, is_l2=l2, _kind=kind, database=db))

    found.sort(key=lambda t: (not t.is_l2, t.database, t.schema, t.table))
    return found


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
    except WarehouseError:
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
    except WarehouseError:
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
        except WarehouseError:
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