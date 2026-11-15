"""Read-only loaders for axioma.db. Never writes to the live record store."""
import sqlite3
from contextlib import closing

import pandas as pd


def connect(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _query(path, sql, params=()):
    with closing(connect(path)) as conn:
        return pd.read_sql_query(sql, conn, params=params)


def load_metrics_5m(path, symbols=None, start=None, end=None):
    """5-minute research bars. `symbols` filters on standardized_name or symbol_name."""
    sql, params = "SELECT * FROM metrics_5m WHERE 1=1", []
    if symbols:
        marks = ",".join("?" * len(symbols))
        sql += f" AND (standardized_name IN ({marks}) OR symbol_name IN ({marks}))"
        params += list(symbols) * 2
    if start:
        sql += " AND slot_end >= ?"
        params.append(start)
    if end:
        sql += " AND slot_end <= ?"
        params.append(end)
    return _query(path, sql + " ORDER BY ctid_trader_account, symbol_id, slot_end", params)


def load_symbols(path):
    return _query(path, "SELECT * FROM symbol_mapping")


def load_vap(path, symbol_id, window_minutes, start=None, end=None):
    """Volume-at-price levels joined to their run (one row per run x price)."""
    sql = ("SELECT r.symbol_id, r.window_minutes, r.as_of, r.poc, r.va_low, r.va_high,"
           " l.price, l.volume, l.ticks FROM vap_run r JOIN vap_level l ON l.run_id = r.id"
           " WHERE r.symbol_id = ? AND r.window_minutes = ?")
    params = [symbol_id, window_minutes]
    if start:
        sql += " AND r.as_of >= ?"
        params.append(start)
    if end:
        sql += " AND r.as_of <= ?"
        params.append(end)
    return _query(path, sql + " ORDER BY r.as_of, l.price", params)
