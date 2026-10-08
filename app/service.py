"""Data access for the production data endpoints.

Owns the connection lifecycle, the cached instrument catalogue (ticker, asset
class, tick count) and the tick fetch. The aggregate rows come from the KQL
endpoint (`app.kql`); the instrument dimension stays on the SQL analytics
endpoint (`app.db`). Caching keeps the dashboard responsive: the catalogue
changes only when a new instrument is listed, and repeated dashboard requests
should not re-scan the tables.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import pandas as pd

from . import kql
from .assets import CLASS_LABELS, classify_asset_class, family_label, family_of, sort_key
from .config import Settings, get_settings
from .db import DataSourceError, connect, qualified, query, symbol_catalogue
from .frames import from_snapshot

_CACHE_TTL = 300.0
_MAX_CACHE_ENTRIES = 32


@dataclass
class Instrument:
    """One tradable instrument, with everything the UI needs to show it."""
    symbol_id: str
    name: str
    description: str
    asset_class: str
    ticks: int

    @property
    def display(self) -> str:
        return self.name or self.symbol_id

    @property
    def family(self) -> str:
        return family_of(self.asset_class)

    def to_dict(self) -> dict:
        label = CLASS_LABELS.get(self.asset_class, "Unclassified")
        return {
            "symbol": self.symbol_id,
            "name": self.display,
            "description": self.description,
            "asset_class": self.asset_class,
            "asset_class_label": label,
            "family": self.family,
            "family_label": family_label(self.family) if self.family else "",
            "ticks": self.ticks,
        }


@dataclass
class Status:
    connected: bool = False
    error: str | None = None
    hints: list[str] = field(default_factory=list)
    server_time: str | None = None
    row_count: int | None = None
    latest: str | None = None
    #: Rows in the SQL instrument dimension, or ``None`` when it is unreadable.
    dimension_rows: int | None = None


class _TTLCache:
    """Minimal thread-safe TTL cache with a hard entry cap."""

    def __init__(self, ttl: float = _CACHE_TTL, capacity: int = _MAX_CACHE_ENTRIES) -> None:
        self._ttl, self._cap = ttl, capacity
        self._data: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            stamped, value = item
            if time.time() - stamped > self._ttl:
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: str, value) -> None:
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
_instruments: dict[str, Instrument] | None = None


def _endpoint_key(s: Settings) -> str:
    return (f"{s.kql_host}|{s.kql_database}.{s.kql_table}"
            f"|{s.host}|{s.gold_database}.{s.gold_schema}.{s.symbol_table}")


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------

def status(*, refresh: bool = False) -> Status:
    """Probe both sources. Cached briefly so the UI stays responsive.

    The rows are read from KQL and the names for them from SQL, so a dashboard
    is only usable when *both* answer. Probing just one of them reported
    ``connected`` while the selectors still failed, which is worse than saying
    plainly which side is down.
    """
    global _status
    if not refresh and _status.server_time:
        return _status

    s = get_settings()
    st = Status()
    try:
        with kql.connect(s) as client:
            st.server_time = kql.server_time(client, s)
            st.row_count, st.latest = kql.snapshot_stats(client, s)
        st.connected, st.error = True, None
        if not st.row_count:
            st.hints = ["The KQL aggregate table is empty. Run the ingest pipeline."]
    except DataSourceError as exc:
        st.connected, st.error = False, str(exc)
        st.hints = ["The KQL endpoint is unavailable. Check the configured identity."]

    try:
        with connect(s) as conn:
            probe = query(
                conn,
                f"SELECT COUNT_BIG(*) AS n FROM "
                f"{qualified(s.gold_database, s.gold_schema, s.symbol_table)}",
            )
            st.dimension_rows = int(probe["n"].iloc[0]) if not probe.empty else 0
    except DataSourceError as exc:
        st.connected, st.error = False, st.error or str(exc)
        st.hints.append(
            "The instrument dimension is unavailable. Check that the SQL "
            "analytics endpoint can still read the gold tables."
        )

    _status = st
    return _status


def traded_count() -> int:
    """Every instrument with snapshots, classified or not.

    Unlike `_sorted_instruments` this keeps the unclassifiable ones, so it
    reports the true size of the snapshot rather than what the selectors show.
    """
    return sum(1 for i in instruments().values() if i.ticks > 0)


def unclassified_count() -> int:
    """Traded instruments the gold dimension does not describe."""
    return sum(1 for i in instruments().values() if i.ticks > 0 and not i.asset_class)


def coverage_note() -> str | None:
    """Warning when the snapshot holds instruments the dimension cannot name.

    ``agg_dom`` is shared and accumulates rows from every feed that has ever
    written to it, while ``symbols_icmarkets`` describes only icmarkets. Any
    other feed's instruments therefore cannot be named or classified. They are
    left out of the selectors, so say so rather than leaving the count to be
    discovered by its absence.
    """
    missing = unclassified_count()
    if not missing:
        return None
    return (
        f"{missing} instrument(s) in the snapshot have no row in "
        f"symbols_icmarkets and are not shown in the selectors. They come from "
        f"a feed the icmarkets dimension does not cover."
    )


# --------------------------------------------------------------------------
# Instrument catalogue
# --------------------------------------------------------------------------

def _build_catalogue() -> dict[str, Instrument]:
    """Merge the SQL instrument dimension with the KQL tick counts."""
    with connect() as conn:
        dim = symbol_catalogue(conn)
    with kql.connect() as client:
        counts = kql.symbol_tick_counts(client)
    out: dict[str, Instrument] = {}
    for row in dim.itertuples():
        symbol_id = str(row.symbolId).strip()
        if not symbol_id:
            continue
        # pandas reads SQL NULL as NaN; normalise those to empty strings.
        name = _text(row.symbolName)
        description = _text(row.description)
        out[symbol_id] = Instrument(
            symbol_id=symbol_id,
            name=name,
            description=description,
            asset_class=classify_asset_class(name, _text(row.assetClassName),
                                             row.symbolCategoryId, description),
            ticks=int(counts.get(symbol_id, 0)),
        )
    # A symbol present in the rows but missing from the dimension still
    # belongs in the selector; classify it from its id alone.
    for symbol_id, ticks in counts.items():
        if symbol_id not in out:
            out[symbol_id] = Instrument(symbol_id=symbol_id, name="", description="",
                                         asset_class=classify_asset_class(symbol_id),
                                         ticks=int(ticks))
    return out


def _text(value: object) -> str:
    """A pandas cell as a stripped string; NULL/NaN becomes empty."""
    if value is None:
        return ""
    try:
        if value != value:        # NaN is the only value unequal to itself
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def instruments(*, refresh: bool = False) -> dict[str, Instrument]:
    """{symbolId: Instrument} for every instrument in the dimension."""
    global _instruments
    if _instruments is not None and not refresh:
        return _instruments
    _instruments = _build_catalogue()
    return _instruments


def _default_key() -> str:
    """Configured default ticker, normalised for comparison."""
    try:
        return (get_settings().default_symbol or "").strip().upper()
    except Exception:
        return "XAUUSD"


def default_symbol_id(only_traded: bool = True) -> str | None:
    """Symbol id of the configured default ticker, when it is selectable."""
    want = _default_key()
    if not want:
        return None
    rows = [i for i in instruments().values() if i.asset_class]
    if only_traded:
        rows = [i for i in rows if i.ticks > 0]
    for i in rows:
        if (i.name or "").strip().upper() == want:
            return i.symbol_id
    for i in rows:
        if i.symbol_id.strip().upper() == want:
            return i.symbol_id
    return None


def _sorted_instruments(only_traded: bool) -> list[Instrument]:
    """Instruments for the selectors, default ticker first, then best-traded.

    Unclassified instruments are excluded. They carry ticks but no row in the
    icmarkets dimension, so they have no ticker and no asset class; offering
    them in the selectors produced a wall of bare ids grouped under
    "Unclassified" and let a user pick an instrument that cannot be
    classified at all. The count of such instruments is still reported by
    `unclassified_count`, so the coverage gap stays visible in the health
    hints rather than in the pickers.
    """
    rows = [i for i in instruments().values() if i.asset_class]
    if only_traded:
        rows = [i for i in rows if i.ticks > 0]
    default_id = default_symbol_id(only_traded=only_traded)
    rows.sort(key=lambda i: (
        0 if (default_id is not None and i.symbol_id == default_id) else 1,
        -i.ticks, i.display or i.symbol_id))
    return rows


def symbol_list(only_traded: bool = True) -> list[dict]:
    """Flat symbol payload for the selector, best-traded first."""
    return [i.to_dict() for i in _sorted_instruments(only_traded)]


def grouped_symbols(only_traded: bool = True) -> list[dict]:
    """Symbols bucketed by asset class, for the grouped selector."""
    buckets: dict[str, list[dict]] = {}
    for inst in _sorted_instruments(only_traded):
        buckets.setdefault(inst.asset_class, []).append(inst.to_dict())

    out: list[dict] = []
    for asset_class in sorted(buckets, key=sort_key):
        symbols = buckets[asset_class]
        family = family_of(asset_class)
        out.append({
            "key": asset_class,
            "label": CLASS_LABELS.get(asset_class, "Unclassified"),
            "family": family,
            "family_label": family_label(family) if family else "",
            "count": len(symbols),
            "ticks": sum(s["ticks"] for s in symbols),
            "symbols": symbols,
        })
    return out


def class_summary(only_traded: bool = True) -> list[dict]:
    """Asset-class rollup: how many instruments (and ticks) per class."""
    counts: dict[str, int] = {}
    ticks: dict[str, int] = {}
    for inst in _sorted_instruments(only_traded):
        counts[inst.asset_class] = counts.get(inst.asset_class, 0) + 1
        ticks[inst.asset_class] = ticks.get(inst.asset_class, 0) + inst.ticks
    out = []
    for key in sorted(counts, key=sort_key):
        family = family_of(key)
        out.append({
            "key": key,
            "label": CLASS_LABELS.get(key, "Unclassified"),
            "family": family,
            "family_label": family_label(family) if family else "",
            "count": counts[key],
            "ticks": ticks[key],
        })
    return out


def taxonomy() -> list[dict]:
    """The static class list, so the client can render its own labels."""
    return [
        {"key": key, "label": label, "family": family_of(key),
         "family_label": family_label(family_of(key))}
        for key, label in sorted(CLASS_LABELS.items(), key=lambda kv: sort_key(kv[0]))
    ]


def lookup(symbol_id: str) -> Instrument | None:
    """The instrument record for one symbol id."""
    return instruments().get(str(symbol_id).strip())


# --------------------------------------------------------------------------
# Ticks
# --------------------------------------------------------------------------

def load_ticks(symbol: str | None = None, *, limit: int | None = None,
               lookback_minutes: int = 5) -> pd.DataFrame:
    """Canonical ticks for one symbol. Raises DataSourceError when empty."""
    s = get_settings()
    with kql.connect(s) as client:
        raw = kql.fetch_ticks(client, symbol=symbol, limit=int(limit or s.max_ticks),
                              lookback_minutes=lookback_minutes, settings=s)
    frame = from_snapshot(raw)
    if frame.empty:
        raise DataSourceError(
            f"No usable aggregate rows for {symbol or 'any instrument'} "
            f"in the last {lookback_minutes} minute(s)."
        )
    return frame


def load_ticks_cached(symbol: str | None, limit: int | None, lookback_minutes: int) -> pd.DataFrame:
    """Cached tick fetch keyed on endpoint + parameters."""
    key = f"ticks|{_endpoint_key(get_settings())}|{symbol}|{limit}|{lookback_minutes}"
    hit = _cache.get(key)
    if hit is not None:
        return hit
    frame = load_ticks(symbol, limit=limit, lookback_minutes=lookback_minutes)
    _cache.set(key, frame)
    return frame


def reset() -> None:
    """Drop every cached fragment (catalogue, ticks, health probe)."""
    global _instruments, _status
    _cache.clear()
    _instruments = None
    _status = Status()
    s = get_settings()
    st = Status()
    try:
        with kql.connect(s) as client:
            st.server_time = kql.server_time(client, s)
            st.row_count, st.latest = kql.snapshot_stats(client, s)
        st.connected, st.error = True, None
        if not st.row_count:
            st.hints = ["The KQL aggregate table is empty. Run the ingest pipeline."]
    except DataSourceError as exc:
        st.connected, st.error = False, str(exc)
        st.hints = ["Data source unavailable. Check the configured credentials."]
    _status = st
    return _status