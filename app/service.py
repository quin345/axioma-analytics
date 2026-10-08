"""Data access for the production KQL endpoint, with a Redis cache in front.

`app.kql` is the only source of rows: the per-tick aggregate metrics in
``agg_dom`` and the instrument dimension in ``symbols_icmarkets`` live in the
same Fabric KQL database. This module owns the connection lifecycle, the
instrument catalogue (ticker, asset class, tick count) and the tick fetch, and
puts every KQL result in Redis so a dashboard request never re-scans a table an
earlier request has already read.

The cache window is fixed. One KQL read covers `CACHE_LOOKBACK_HOURS` (4 by
default) of ticks per symbol; the duration the caller asks for only *narrows*
that window in memory. Selecting 30 minutes after 4 hours therefore costs no
query at all, and the UI cannot ask for more history than the cache holds.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from . import cache, kql
from .assets import CLASS_LABELS, classify_asset_class, family_label, family_of, sort_key
from .config import Settings, get_settings
from .errors import DataSourceError
from .frames import from_ticks


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
    #: Rows in the KQL ``symbols_icmarkets`` dimension, or ``None`` when unreadable.
    dimension_rows: int | None = None
    #: Redis cache reachability. ``None`` while it has not been probed.
    cache_connected: bool | None = None
    cache_error: str | None = None
    #: The fixed window the cache holds, echoed so the UI can bound its own
    #: duration control to it.
    lookback_minutes: int | None = None


_status = Status()
_instruments: dict[str, Instrument] | None = None


# --------------------------------------------------------------------------
# Window
# --------------------------------------------------------------------------

def clamp_lookback(minutes: int | None, settings: Settings | None = None) -> int:
    """The requested duration, clamped to the fixed cache window.

    The UI may narrow the window but never widen it: a request for more history
    than the cache holds is served with everything there is rather than
    triggering a second, deeper KQL read.
    """
    s = settings or get_settings()
    if minutes is None:
        return s.cache_lookback_minutes
    try:
        want = int(minutes)
    except (TypeError, ValueError):
        return s.cache_lookback_minutes
    return max(1, min(want, s.cache_lookback_minutes))


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------

def status(*, refresh: bool = False) -> Status:
    """Probe the KQL endpoint and the cache. Cached briefly so the UI stays fast.

    Rows *and* names come from KQL now, so there is a single source to check;
    the cache is reported alongside it because a reachable cache is what keeps
    the dashboard from re-querying on every request, but a cache outage never
    makes the app unhealthy - reads simply fall through to KQL.
    """
    global _status
    if not refresh and _status.server_time:
        return _status

    s = get_settings()
    st = Status(lookback_minutes=s.cache_lookback_minutes)
    try:
        with kql.connect(s) as client:
            st.server_time = kql.server_time(client, s)
            st.row_count, st.latest = kql.tick_stats(client, s)
            st.dimension_rows = kql.symbol_count(client, s)
        st.connected, st.error = True, None
        if not st.row_count:
            st.hints = ["The KQL metrics table is empty. Run the ingest pipeline."]
        elif not st.dimension_rows:
            st.hints = ["The KQL instrument dimension is empty. Run the metadata notebook."]
    except DataSourceError as exc:
        st.connected, st.error = False, str(exc)
        st.hints = ["The KQL endpoint is unavailable. Check the configured identity."]

    st.cache_connected = cache.ping(s)
    if not st.cache_connected:
        st.cache_error = (
            "The Redis cache is unreachable; analytics still work but every "
            "request re-reads KQL."
        )
        st.hints.append(st.cache_error)

    _status = st
    return _status


def traded_count() -> int:
    """Every instrument with tick metrics, classified or not.

    Unlike `_sorted_instruments` this keeps the unclassifiable ones, so it
    reports the true size of the feed rather than what the selectors show.
    """
    return sum(1 for i in instruments().values() if i.ticks > 0)


def unclassified_count() -> int:
    """Traded instruments the KQL dimension does not describe."""
    return sum(1 for i in instruments().values() if i.ticks > 0 and not i.asset_class)


def coverage_note() -> str | None:
    """Warning when the metrics hold instruments the dimension cannot name.

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
        f"{missing} instrument(s) in the metrics have no row in "
        f"symbols_icmarkets and are not shown in the selectors. They come from "
        f"a feed the icmarkets dimension does not cover."
    )


# --------------------------------------------------------------------------
# Instrument catalogue
# --------------------------------------------------------------------------

def _build_catalogue(*, refresh: bool = False) -> dict[str, Instrument]:
    """Merge the KQL instrument dimension with the KQL tick counts.

    Both queries scan whole tables, so the merged catalogue goes to Redis and
    the next request - in this process or another - reuses it instead of
    repeating them.
    """
    s = get_settings()
    ck = cache.key(s, "catalogue")
    if refresh:
        cache.delete(s, ck)
    else:
        stored = cache.get_json(ck, s)
        if stored is not None:
            return {str(k): Instrument(**v) for k, v in stored.items()}

    with kql.connect(s) as client:
        dim = kql.symbol_catalogue(client, s)
        counts = kql.symbol_tick_counts(client, s)

    out: dict[str, Instrument] = {}
    for row in dim.itertuples():
        symbol_id = _text(row.symbolId)
        if not symbol_id:
            continue
        # KQL returns typed nulls; normalise those to empty strings. The
        # asset-class NAME is not in symbols_icmarkets (only the category id),
        # so the classifier falls back to the description and the ticker.
        name = _text(row.symbolName)
        description = _text(row.description)
        out[symbol_id] = Instrument(
            symbol_id=symbol_id,
            name=name,
            description=description,
            asset_class=classify_asset_class(name, None, _text(row.symbolCategoryId), description),
            ticks=int(counts.get(symbol_id, 0)),
        )
    # A symbol present in the rows but missing from the dimension still
    # belongs in the selector; classify it from its id alone.
    for symbol_id, ticks in counts.items():
        if symbol_id not in out:
            out[symbol_id] = Instrument(symbol_id=symbol_id, name="", description="",
                                        asset_class=classify_asset_class(symbol_id),
                                        ticks=int(ticks))

    cache.set_json(ck, {k: asdict(v) for k, v in out.items()}, s.cache_ttl_seconds, s)
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
    _instruments = _build_catalogue(refresh=refresh)
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

def load_raw(symbol: str | None = None, *, limit: int | None = None,
             lookback_minutes: int | None = None) -> pd.DataFrame:
    """Raw aggregate rows from KQL: the cache's window, unfiltered.

    This is the one place a dashboard read touches the engine. Every caller
    goes through :func:`load_ticks_cached`, which stores what this returns so
    the next request is served from Redis.
    """
    s = get_settings()
    with kql.connect(s) as client:
        return kql.fetch_ticks(
            client, symbol=symbol, limit=int(limit or s.max_ticks),
            lookback_minutes=(s.cache_lookback_minutes if lookback_minutes is None
                              else lookback_minutes),
            settings=s,
        )


def _narrow(raw: pd.DataFrame, lookback_minutes: int, limit: int) -> pd.DataFrame:
    """The tail of the cached window: anchored on the newest row, then capped.

    Anchoring on the newest row keeps the result the most recent session rather
    than an arbitrary slice of history, and doing it in pandas rather than KQL
    is what makes a duration change free once the window is cached.
    """
    if raw.empty:
        return raw
    ts = pd.to_datetime(raw["timestamp"], utc=True)
    cutoff = ts.max() - pd.Timedelta(minutes=int(lookback_minutes))
    return raw.loc[ts >= cutoff].tail(int(limit))


def load_ticks(symbol: str | None = None, *, limit: int | None = None,
               lookback_minutes: int | None = None) -> pd.DataFrame:
    """Canonical ticks for one symbol, read through the cache."""
    return load_ticks_cached(symbol, limit=limit, lookback_minutes=lookback_minutes)

def load_ticks_cached(symbol: str | None, limit: int | None = None,
                      lookback_minutes: int | None = None) -> pd.DataFrame:
    """Canonical ticks, served from Redis and narrowed to the request.

    The cached entry is the *whole* fixed window for a symbol, so the duration
    and the row cap only trim the in-memory copy. A second request for the same
    symbol therefore never re-queries KQL, whatever duration it asks for.
    """
    s = get_settings()
    minutes = clamp_lookback(lookback_minutes, s)
    cap = int(limit or s.max_ticks)
    ck = cache.key(s, "ticks", (symbol or "").strip() or "all")

    raw = cache.get_frame(ck, s)
    if raw is None:
        raw = load_raw(symbol, limit=s.max_ticks, lookback_minutes=s.cache_lookback_minutes)
        cache.set_frame(ck, raw, s.cache_ttl_seconds, s)

    frame = from_ticks(_narrow(raw, minutes, cap))
    if frame.empty:
        raise DataSourceError(
            f"No usable aggregate rows for {symbol or 'any instrument'} "
            f"in the last {minutes} minute(s)."
        )
    return frame


def reset() -> None:
    """Drop every cached fragment (catalogue, ticks, health probe).

    Redis holds the bulk of it, so the per-symbol tick keys and the catalogue
    key are deleted too; otherwise a refresh would rebuild them and still read
    the stale copy back.
    """
    global _instruments, _status
    s = get_settings()
    cache.delete(s, cache.key(s, "catalogue"))
    for inst in (_instruments or {}):
        cache.delete(s, cache.key(s, "ticks", inst))
    cache.delete(s, cache.key(s, "ticks", "all"))
    _instruments = None
    _status = Status()
    return status(refresh=True)