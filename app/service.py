"""Data access layer: resolves a tick source and falls back to synthetic data.

Keeps the HTTP layer free of warehouse concerns and centralises caching so
repeated dashboard requests don't re-scan the (columnar) warehouse.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import synthetic
from .config import get_settings
from .db import (TableInfo, WarehouseError, connect, discover_tables, fetch_ticks,
                 get_symbols, list_all_tables, list_databases, query,
                 find_symbol_dimension, symbol_labels)
from .books import from_agg, from_levels
from .l2 import L2Options, last_stats, reconstruct
from .schema import build_column_map, normalise

_SYSTEM_SCHEMAS = {"sys", "queryinsights", "_rsc", "information_schema"}
_CACHE_TTL = 60.0
_MAX_CACHE_ENTRIES = 64
_DELTA_META_COLUMNS = {"_rid", "_ts"}


@dataclass
class SourceInfo:
    """A place ticks can be read from, live or synthetic."""
    key: str
    label: str
    symbols: list[str]
    table: dict | None = None
    synthetic: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "symbols": self.symbols,
            "table": self.table, "synthetic": self.synthetic, "note": self.note,
        }


@dataclass
class Status:
    connected: bool = False
    synthetic: bool = False
    error: str | None = None
    hints: list[str] = field(default_factory=list)
    server_time: str | None = None
    tables: list[dict] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)


class _TTLCache:
    def __init__(self, ttl: float = _CACHE_TTL, capacity: int = _MAX_CACHE_ENTRIES) -> None:
        self._ttl, self._cap = ttl, capacity
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            ts, value = item
            if time.time() - ts > self._ttl:
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if len(self._data) >= self._cap:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                self._data.pop(oldest, None)
            self._data[key] = (time.time(), value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_cache = _TTLCache()
_status = Status()
_LAST_L2_STATS: dict = {}
_SYMBOL_LABELS: dict[str, str] = {}
_SYMBOL_DIM: str = ""



def _table_info(t: dict) -> TableInfo:
    return TableInfo(schema=t["schema"], table=t["table"], columns=t["columns"],
                     column_map=build_column_map(t["columns"]),
                     is_l2=(t.get("kind") == "l2") or bool(t.get("is_l2")),
                     _kind=t.get("kind") or "",
                     database=t.get("database", ""))


def _probe_table(conn, ti: TableInfo) -> dict:
    """Cheap health check: is the table empty, and does it expose real columns?

    OneLake-backed tables can surface only delta-log columns (_rid/_ts), which
    means the payload schema is not yet available through this endpoint.
    """
    out: dict[str, Any] = {"row_count": None, "payload_columns": [], "is_metadata_only": False}
    try:
        n = query(conn, f"SELECT COUNT_BIG(*) AS n FROM {ti.qualified}")
        out["row_count"] = int(n["n"].iloc[0])
    except WarehouseError:
        return out
    payload = [c for c in ti.columns if c.strip().lower() not in _DELTA_META_COLUMNS]
    out["payload_columns"] = payload
    out["is_metadata_only"] = not payload
    return out


def status(*, refresh: bool = False) -> Status:
    """Probe the warehouse. Cached briefly so the UI stays responsive."""
    if not refresh and _status.server_time:
        return _status
    st = get_settings()
    _status.hints = []
    try:
        with connect() as conn:
            _status.server_time = str(query(conn, "SELECT CURRENT_TIMESTAMP AS t")["t"].iloc[0])

            # Every user table in every database on this endpoint.
            all_tables = [t for t in list_all_tables(conn)
                          if t.schema.lower() not in _SYSTEM_SCHEMAS]
            tick_keys = {f"{t.database}|{t.schema}.{t.table}" for t in discover_tables(conn)}
            described = []
            for t in all_tables:
                d = t.to_dict()
                d["is_tick_source"] = f"{t.database}|{t.schema}.{t.table}" in tick_keys
                if d["is_tick_source"]:
                    d.update(_probe_table(conn, t))
                else:
                    d.update({"row_count": None, "payload_columns": [], "is_metadata_only": False})
                described.append(d)
            _status.tables = described
            _status.databases = list_databases(conn)

            live = [t for t in described if t["is_tick_source"]]
            empty = [t for t in live if t.get("row_count") == 0]
            if not live:
                _status.hints.append(
                    "Connected, but no table matched a known tick/book shape "
                    "(timestamp plus bid/ask, or a book snapshot schema)."
                )
            elif empty:
                _status.hints.append(
                    f"{len(empty)} tick source(s) are empty: "
                    + ", ".join(t["qualified"] for t in empty)
                    + ". Run the ingest pipeline, or use the Synthetic source."
                )
            if len(_status.databases) > 1:
                _status.hints.append(
                    "Endpoint spans " + str(len(_status.databases)) + " databases: "
                    + ", ".join(_status.databases) + "."
                )
        _status.connected, _status.error, _status.synthetic = True, None, False
    except WarehouseError as exc:
        _status.connected, _status.error, _status.synthetic = False, str(exc), st.allow_synthetic
        _status.hints = [
            "Add FABRIC_CLIENT_ID / FABRIC_CLIENT_SECRET / FABRIC_TENANT_ID to .env.",
            "Grant the service principal a Contributor role on the Fabric workspace. "
            "Viewer works for the SQL layer, but OneLake security filters Viewers "
            "and hides whole tables (they then look like they do not exist).",
        ]
    return _status


def sources() -> list[SourceInfo]:
    """Every tick source the UI can offer: live tables plus synthetic."""
    out: list[SourceInfo] = []
    badges = {"agg": "aggregated book", "levels": "L2 price levels",
              "l2": "raw L2 events", "flat": "flat ticks"}
    for t in status().tables:
        if not t.get("is_tick_source"):
            continue
        kind = t.get("kind") or "flat"
        out.append(SourceInfo(key=t["qualified"],
                              label=f"{t['qualified']}  ({badges.get(kind, kind)})",
                              symbols=[], table=t))
    if not out or get_settings().allow_synthetic:
        out.append(SourceInfo(key="synthetic", label="Synthetic (demo)",
                              symbols=synthetic.available_symbols(), synthetic=True,
                              note="Generated tick data - not from the warehouse."))
    return out


def symbols_for(source_key: str) -> list[str]:
    """Best-effort symbol list for a source key."""
    for t in status().tables:
        if t.get("qualified") == source_key:
            try:
                with connect() as conn:
                    return [x["symbol"] for x in get_symbols(conn, _table_info(t), limit=300)]
            except WarehouseError:
                return []
    if source_key == "synthetic":
        return synthetic.available_symbols()
    return []


def load_ticks(source: str = "synthetic", *, symbol: str | None = None,
               limit: int | None = None, lookback_days: int = 3) -> pd.DataFrame:
    """Load canonical ticks for a source/symbol. Raises WarehouseError on live failure."""
    s = get_settings()
    limit = int(limit or s.max_ticks)

    if source == "synthetic" or source.startswith("synthetic"):
        return synthetic.generate((symbol or "EURUSD").upper(), ticks=min(limit, 60_000))

    match = next((t for t in status().tables if t.get("qualified") == source), None)
    if match is None:
        raise WarehouseError(f"Unknown tick source: {source}")

    ti = _table_info(match)
    with connect() as conn:
        raw, cm = fetch_ticks(conn, ti, symbol=symbol, limit=limit, lookback_days=lookback_days)

    kind = ti.kind
    if kind == "agg":
        # gold.agg_dom_book_snapshot already carries best bid/ask per snapshot.
        frame = from_agg(raw)
    elif kind == "levels":
        # dbo.silver_dom_book_snapshot is one row per resting level.
        frame = from_levels(raw, depth_levels=int(s.l2_depth_levels))
    elif kind == "l2":
        # Raw DOM deltas: replay them to rebuild the book.
        frame = reconstruct(raw, L2Options(top_levels=int(s.l2_depth_levels)))
        _LAST_L2_STATS.update(last_stats())
    else:
        _LAST_L2_STATS.clear()
        frame = normalise(raw, cm, has_symbol=bool(cm.symbol))

    if not frame.empty and symbol:
        frame = frame[frame["symbol"].astype(str) == str(symbol)]
    if frame.empty:
        raise WarehouseError(
            f"No usable ticks for {symbol or 'this table'} in the last {lookback_days} day(s)."
        )
    return frame


def load_ticks_cached(source: str, symbol: str | None, limit: int | None,
                      lookback_days: int) -> tuple[pd.DataFrame, bool]:
    """Cached wrapper. Returns (frame, was_synthetic)."""
    key = f"ticks|{source}|{symbol}|{limit}|{lookback_days}"
    hit = _cache.get(key)
    if hit is not None:
        return hit
    try:
        result = (load_ticks(source, symbol=symbol, limit=limit, lookback_days=lookback_days),
                  source.startswith("synthetic"))
    except WarehouseError:
        if not get_settings().allow_synthetic:
            raise
        frame = synthetic.generate((symbol or "EURUSD").upper(), ticks=min(limit or 20_000, 60_000))
        result = (frame, True)
    _cache.set(key, result)
    return result


def symbol_map() -> dict[str, str]:
    """{symbolId: symbolName} from gold.symbols_pepperstone, cached for the session."""
    global _SYMBOL_LABELS, _SYMBOL_DIM
    if not _SYMBOL_LABELS:
        try:
            with connect() as conn:
                all_t = [t for t in list_all_tables(conn) if t.schema.lower() not in _SYSTEM_SCHEMAS]
                dim = find_symbol_dimension(conn, all_t)
                if dim is not None:
                    _SYMBOL_DIM = dim.label
                    _SYMBOL_LABELS = symbol_labels(conn, dim)
        except WarehouseError:
            _SYMBOL_LABELS = {}
    return _SYMBOL_LABELS


def decorate(symbols: list[dict]) -> list[dict]:
    """Attach the human-readable ticker to each symbol entry."""
    labels = symbol_map()
    out = []
    for row in symbols:
        sid = str(row.get("symbol"))
        out.append({**row, "name": labels.get(sid), "display": labels.get(sid) or sid})
    return out


def l2_stats() -> dict:
    """Counters from the last DOM replay (events seen, resets, dropped)."""
    return dict(_LAST_L2_STATS)


def clear_cache() -> None:
    _cache.clear()
