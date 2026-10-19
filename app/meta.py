"""Broker-agnostic instrument metadata from local PostgreSQL.

``symbols_metadata`` keeps the same symbol columns the dashboard has always
used (``symbolId, symbolName, symbolCategoryId, description``) but is no
longer per-broker: every row is bound to the cTrader account it came from
(``ctrader_account_id``) and each account id maps to its broker
(``broker_name``). Both bindings live on the same row so the read is one
filtered scan:

    symbols_metadata(symbolId, symbolName, symbolCategoryId, description,
                     ctrader_account_id, broker_name)

``Settings`` optionally scopes the read (``CTRADER_ACCOUNT_ID`` /
``BROKER_NAME``); blank means "all accounts / all brokers". One row per
(symbolId, account) survives - the newest write wins - and the frame the
callers get keeps the legacy column names, so classification and selectors
are untouched by the move.
"""
from __future__ import annotations

import logging

import pandas as pd

from .config import Settings, get_settings
from .errors import DataSourceError

log = logging.getLogger(__name__)

#: Columns the dashboard reads, as the metadata writer ingests them
#: (``symbolId`` arrives as a BIGINT-equivalent, so it is coerced to long).
SYMBOL_COLUMNS = ["symbolId", "symbolName", "symbolCategoryId", "description"]

#: Scope columns binding each symbol row to its cTrader account and broker.
SCOPE_COLUMNS = ["ctrader_account_id", "broker_name"]


def _connect(settings: Settings):
    """A DBAPI connection to the local metadata database."""
    try:
        import psycopg2
    except ImportError as exc:
        raise DataSourceError(
            "PostgreSQL metadata driver is missing: pip install psycopg2-binary."
        ) from exc
    try:
        return psycopg2.connect(
            host=settings.pg_host,
            port=settings.pg_port,
            dbname=settings.pg_database,
            user=settings.pg_user,
            password=settings.pg_password,
            connect_timeout=int(settings.pg_timeout_seconds),
        )
    except Exception as exc:
        raise DataSourceError(f"Postgres metadata is unreachable: {exc}.") from exc


def _table(settings: Settings) -> str:
    name = (settings.pg_symbols_table or "").strip() or "symbols_metadata"
    if not name.replace("_", "").replace("-", "").isalnum() and "." not in name:
        raise DataSourceError(f"Unsafe Postgres metadata table: {name!r}.")
    parts = [p.strip(' "') for p in name.split(".")]
    return ".".join(f'"{p}"' for p in parts if p)


def symbol_catalogue(settings: Settings | None = None) -> pd.DataFrame:
    """One row per (symbol, account): id, ticker, category, description + scope.

    The broker's own category catalogue (``symbols_category``) maps the
    broker-specific ``symbolCategoryId`` to an asset class id, and
    ``asset_classes`` maps that id to the broker's asset-class label (for
    example "Metals (Pro)"). Including both keeps every broker's classification
    authoritative instead of reusing one broker's numeric category codes.
    """
    s = settings or get_settings()
    # ``symbols`` may be the configured view/table; the broker catalogues are
    # always the two physical tables. The view exposes the account as
    # ``ctrader_account_id`` (the same value as ``ctidTraderAccountId``), so the
    # category join can key on it directly.
    symbols = _table(s)
    wanted = (
        ", ".join(f'symbols."{c}"' for c in SYMBOL_COLUMNS + SCOPE_COLUMNS)
        + ', sc."assetClassId" AS asset_class_id, ac."name" AS asset_class_name'
    )
    clauses: list[str] = []
    params: list[object] = []
    if (s.ctrader_account_id or "").strip():
        clauses.append('symbols."ctrader_account_id" = %s')
        params.append(s.ctrader_account_id.strip())
    if (s.broker_name or "").strip():
        clauses.append('LOWER(symbols."broker_name") = LOWER(%s)')
        params.append(s.broker_name.strip())
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        f"SELECT {wanted} FROM {symbols} AS symbols "
        "LEFT JOIN symbols_category sc "
        '  ON symbols."ctrader_account_id" = sc."ctidTraderAccountId" '
        '  AND symbols."symbolCategoryId" = sc."symbolCategoryId" '
        "LEFT JOIN asset_classes ac "
        '  ON sc."ctidTraderAccountId" = ac."ctidTraderAccountId" '
        '  AND sc."assetClassId" = ac."assetClassId" '
        f"{where} "
        'ORDER BY symbols."symbolId", symbols."ctrader_account_id"'
    )
    try:
        with _connect(s) as conn:
            frame = pd.read_sql(sql, conn, params=params or None)
    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError(f"Postgres metadata read failed: {exc}.") from exc
    if frame.empty:
        return pd.DataFrame(
            columns=SYMBOL_COLUMNS + SCOPE_COLUMNS + ["asset_class_id", "asset_class_name"]
        )
    frame["symbolId"] = pd.to_numeric(frame["symbolId"], errors="coerce")
    frame = frame.dropna(subset=["symbolId"])
    # Newest write per (symbol, account) wins; rows arrive oldest-first. The
    # cast lands after the reset so the column is replaced on a frame pandas
    # no longer tracks as a filtered view of the read.
    frame = frame.drop_duplicates(
        subset=["symbolId", "ctrader_account_id"], keep="last"
    ).reset_index(drop=True)
    frame["symbolId"] = frame["symbolId"].astype("int64")
    return frame


def symbol_count(settings: Settings | None = None) -> int:
    """Rows in the scoped metadata table, for the health payload."""
    s = settings or get_settings()
    clauses: list[str] = []
    params: list[object] = []
    if (s.ctrader_account_id or "").strip():
        clauses.append("ctrader_account_id = %s")
        params.append(s.ctrader_account_id.strip())
    if (s.broker_name or "").strip():
        clauses.append("LOWER(broker_name) = LOWER(%s)")
        params.append(s.broker_name.strip())
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT COUNT(*) AS n FROM {_table(s)}{where}"
    try:
        with _connect(s) as conn:
            frame = pd.read_sql(sql, conn, params=params or None)
    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError(f"Postgres metadata read failed: {exc}.") from exc
    if frame.empty:
        return 0
    try:
        return int(frame["n"].iloc[0])
    except (TypeError, ValueError):
        return 0


def ping(settings: Settings | None = None) -> bool:
    """True when the metadata database answers."""
    s = settings or get_settings()
    try:
        with _connect(s) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except DataSourceError as exc:
        log.warning("Postgres metadata ping failed: %s", exc)
        return False
