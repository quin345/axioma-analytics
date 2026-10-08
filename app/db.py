"""SQL access to the production instrument dimension.

The per-tick aggregate rows moved to the KQL endpoint (see `app.kql`). What
stays on the Fabric SQL analytics endpoint - `[ctrader_lakehouse].[gold]` - is
the instrument metadata:

* `gold.symbols_icmarkets` - the icmarkets instrument dimension, supplying the
  human ticker, the description and the category id that `app.assets` turns
  into an asset class, joined to `symbols_category_icmarkets` and
  `asset_classes_icmarkets`.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

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


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------

def connection_string(settings: Settings | None = None) -> str:
    """ODBC connection string for the production endpoint.

    Three authentication modes, in the order `connect()` applies them:

    * managed identity -> ``Authentication=ActiveDirectoryMSI``; the driver
      fetches the token itself, so nothing is handed to it by hand.
    * service principal -> credentials embedded in the string.
    * ``az login`` token -> cannot be expressed as a connection string (the
      driver rejects a JWT passed as PWD), so it is applied separately via
      ``attrs_before`` in ``connect()``.
    """
    s = settings or get_settings()
    parts = [
        f"DRIVER={{{s.odbc_driver}}}",
        f"SERVER={s.server},{s.port}",
        "Encrypt=yes",
        "TrustServerCertificate=yes",
        f"Connection Timeout={s.connect_timeout}",
    ]
    if s.use_managed_identity:
        # `ActiveDirectoryMSI` is the spelling the Microsoft driver accepts;
        # `ActiveDirectoryManagedIdentity` is rejected as an invalid value.
        parts += ["Authentication=ActiveDirectoryMSI"]
        if s.managed_identity_client_id:
            parts.append(f"ClientId={s.managed_identity_client_id}")
    elif s.has_credentials:
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
    if not s.use_managed_identity and not s.has_credentials:
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


