"""Data access for the production KQL endpoint, with a Redis cache in front.

`app.kql` is the only source of rows: the per-tick aggregate metrics in
``agg_dom`` and the instrument dimension in ``symbols_icmarkets`` live in the
same Fabric KQL database. This module owns the connection lifecycle, the
instrument catalogue (ticker, asset class, tick count) and the tick fetch, and
puts every KQL result in Redis so a dashboard request never re-scans a table an
earlier request has already read.

The cache window is fixed. One KQL read covers `CACHE_LOOKBACK_HOURS` (8 by
default) of ticks per symbol; the point in time and the lookback the caller asks
for only *narrow* that window in memory. Selecting 30 minutes after 8 hours
therefore costs no query at all, and the UI cannot ask for more history than the
cache holds - which is what makes a 4-hour timeline with a 240 minute lookback at
its earliest point work: 4 + 4 hours is exactly the window.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

import pandas as pd

from . import cache, kql
from .assets import CLASS_LABELS, classify_asset_class, family_label, family_of, sort_key
from .config import Settings, get_settings
from .errors import DataSourceError
from .frames import from_ticks

log = logging.getLogger(__name__)


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
    #: lookback control to it.
    lookback_minutes: int | None = None
    #: The point-in-time controls, echoed with the window so the UI can build
    #: them from the API: a `timeline_minutes` long timeline in
    #: `timeline_step_minutes` steps, and lookback choices from
    #: `lookback_min_minutes` up to `lookback_max_minutes`.
    timeline_minutes: int | None = None
    timeline_step_minutes: int | None = None
    lookback_min_minutes: int | None = None
    lookback_max_minutes: int | None = None


_status = Status()
_instruments: dict[str, Instrument] | None = None
#: Summary of the last completed refresh cycle, for the health payload.
_last_refresh: dict | None = None


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
    st = Status(
        lookback_minutes=s.cache_lookback_minutes,
        timeline_minutes=max(1, s.timeline_minutes),
        timeline_step_minutes=max(1, s.timeline_step_minutes),
        lookback_min_minutes=max(1, s.lookback_min_minutes),
        lookback_max_minutes=s.max_lookback_minutes,
    )
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
# Refresh cycle
# --------------------------------------------------------------------------

def last_refresh() -> dict | None:
    """Summary of the last completed refresh cycle, or None if none has run."""
    return _last_refresh


def cached_symbols(settings: Settings | None = None) -> list[str]:
    """The symbol entries the cache holds right now, as key suffixes.

    Read out of Redis rather than tracked in memory: another process may have
    written an entry, and a symbol nobody has asked for in a while should drop
    out of the refresh set on its own when its TTL expires instead of being
    re-read from KQL forever.
    """
    s = settings or get_settings()
    marker = cache.key(s, "ticks") + ":"
    return sorted(
        k[len(marker):]
        for k in cache.keys(s, s.kql_database, s.kql_table, "ticks")
        if k.startswith(marker) and len(k) > len(marker)
    )


def _slide_window(cached: pd.DataFrame, added: pd.DataFrame,
                  window_minutes: int, limit: int) -> pd.DataFrame:
    """Append the new slice to the cached window and purge what fell out.

    The cache may only ever hold `window_minutes` (eight hours) of a symbol, so
    each cycle

    * concatenates the freshly read rows with the cached ones, keeping the
      newer copy where the two overlap (the slice is read from the cached
      newest timestamp, which is inclusive), and
    * purges every row older than `window_minutes` before the newest row -
      on a 30-minute cycle that is exactly the earliest half hour - then caps
      the result at `limit`, keeping the newest rows.

    The purge is anchored on the newest row rather than the wall clock, so a
    stalled feed keeps its data instead of being eaten away while no ticks
    arrive; the window only ever *holds* eight hours either way.
    """
    if added is not None and not added.empty and "timestamp" in added.columns:
        cached = pd.concat([cached, added], ignore_index=True)
    if cached.empty:
        return cached

    ts = pd.to_datetime(cached["timestamp"], errors="coerce", utc=True)
    merged = cached.assign(timestamp=ts)
    merged = merged.loc[~ts.isna()]
    merged = merged.drop_duplicates(subset="timestamp", keep="last")
    merged = merged.sort_values("timestamp", kind="stable")
    cutoff = merged["timestamp"].max() - pd.Timedelta(minutes=int(window_minutes))
    return merged.loc[merged["timestamp"] >= cutoff].tail(int(limit)).reset_index(drop=True)


def refresh_cache(settings: Settings | None = None) -> dict:
    """Slide the fixed window forward one interval and replace the cached entries.

    This is what keeps the fixed window *current*: the dashboard always serves
    data from within `CACHE_REFRESH_MINUTES` of the feed, while any duration
    inside the window stays free to change. Two things are refreshed -

    * the instrument catalogue, so the selectors and the health counts see new
      instruments and fresh tick counts, and
    * the window for every symbol already in the cache, plus the configured
      default ticker, so the first paint after a restart is warm.

    A symbol already cached is refreshed **incrementally**: only the rows newer
    than its newest cached row are read from KQL, the earliest rows that have
    aged out of the eight-hour window are purged, and the merged window is
    written back (`_slide_window`). A symbol with no entry (cold start, TTL
    expiry) is read over the whole window instead. A symbol whose read fails
    keeps its previous entry rather than being evicted: the cycle is an
    optimisation, and a partial failure must not throw away data that is
    merely older. Returns a JSON-able summary, which ``/api/health`` publishes.
    """
    global _last_refresh
    s = settings or get_settings()
    started = pd.Timestamp.utcnow()
    errors: list[str] = []

    try:
        instruments(refresh=True)          # rebuilds the process copy too
    except DataSourceError as exc:
        errors.append(f"catalogue: {exc}")

    targets = set(cached_symbols(s))
    try:
        default_id = default_symbol_id(only_traded=True)
    except DataSourceError as exc:
        # The catalogue is what names the default ticker; when it cannot be
        # read, the cached symbols are still worth refreshing.
        errors.append(f"default symbol: {exc}")
        default_id = None
    if default_id:
        targets.add(default_id)

    refreshed = rows = purged = 0
    for name in sorted(targets):
        symbol = None if name == "all" else name
        ck = cache.key(s, "ticks", name)
        cached = cache.get_frame(ck, s)

        # A readable cached entry means an incremental slide; anything else
        # (absent, empty, unparseable timestamps) falls back to the full read.
        start = None
        cached_ts = None
        if cached is not None and not cached.empty and "timestamp" in cached.columns:
            cached_ts = pd.to_datetime(cached["timestamp"], errors="coerce", utc=True)
            if not cached_ts.isna().all():
                start = cached_ts.max().isoformat()

        try:
            if start is not None:
                added = load_raw(symbol, limit=s.max_ticks, start=start)
                raw = _slide_window(cached, added, s.cache_lookback_minutes, s.max_ticks)
                if not raw.empty:
                    # Rows the cache held and the purge has now dropped: the
                    # earliest slice ageing out of the fixed window.
                    cutoff = raw["timestamp"].max() - pd.Timedelta(
                        minutes=s.cache_lookback_minutes)
                    purged += int((cached_ts < cutoff).sum())
            else:
                raw = load_raw(symbol, limit=s.max_ticks,
                               lookback_minutes=s.cache_lookback_minutes)
                raw = _slide_window(raw, None, s.cache_lookback_minutes, s.max_ticks)
        except DataSourceError as exc:
            errors.append(f"{name}: {exc}")
            continue
        if cache.set_frame(ck, raw, s.cache_ttl_seconds, s):
            refreshed += 1
            rows += int(len(raw))

    finished = pd.Timestamp.utcnow()
    _last_refresh = {
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "seconds": round((finished - started).total_seconds(), 2),
        "symbols": refreshed,
        "rows": rows,
        "purged": purged,
        "window_minutes": s.cache_lookback_minutes,
        "interval_minutes": s.cache_refresh_minutes,
        "errors": errors,
    }
    log.info("Cache refresh: %s", _last_refresh)
    return _last_refresh


# --------------------------------------------------------------------------
# Ticks
# --------------------------------------------------------------------------

def load_raw(symbol: str | None = None, *, limit: int | None = None,
             lookback_minutes: int | None = None,
             start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Raw aggregate rows from KQL: the cache's window, unfiltered.

    This is the one place a dashboard read touches the engine. Every caller
    goes through :func:`load_ticks_cached`, which stores what this returns so
    the next request is served from Redis.

    With neither `start` nor `end` the read is the whole fixed window. With an
    explicit `start` it is only the slice after that timestamp - which is how
    the refresh cycle appends the newest half hour without re-reading the eight
    hours it already holds.
    """
    s = get_settings()
    if start is None and end is None and lookback_minutes is None:
        lookback_minutes = s.cache_lookback_minutes
    with kql.connect(s) as client:
        return kql.fetch_ticks(
            client, symbol=symbol, limit=int(limit or s.max_ticks),
            lookback_minutes=lookback_minutes, start=start, end=end,
            settings=s,
        )


def _narrow(raw: pd.DataFrame, lookback_minutes: int, limit: int,
            end_time: object = None) -> pd.DataFrame:
    """The requested slice of the cached window: anchored, then capped.

    Without `end_time` the anchor is the newest row, which keeps the result the
    most recent session rather than an arbitrary slice of history. With it the
    anchor is the chosen point in time: the slice *ends* there, so stepping back
    along the timeline returns the window that led up to that moment. A point
    ahead of the data is clamped to the newest row, and an unparseable one is
    refused rather than silently re-anchored on the newest row.

    Doing the work in pandas rather than KQL is what makes a point or lookback
    change free once the window is cached.
    """
    if raw.empty:
        return raw
    ts = pd.to_datetime(raw["timestamp"], utc=True)
    end = ts.max()
    if end_time is not None:
        try:
            want = pd.Timestamp(end_time)
        except (TypeError, ValueError) as exc:
            raise DataSourceError(f"Unreadable point in time: {end_time!r}.") from exc
        if want.tzinfo is None:
            want = want.tz_localize("UTC")
        # A point in the future (the timeline runs up to "now", the cache only
        # to the newest row) still means "up to the latest data".
        end = min(end, want.tz_convert("UTC"))
    cutoff = end - pd.Timedelta(minutes=int(lookback_minutes))
    return raw.loc[(ts >= cutoff) & (ts <= end)].tail(int(limit))


def load_ticks(symbol: str | None = None, *, limit: int | None = None,
               lookback_minutes: int | None = None,
               end_time: object = None) -> pd.DataFrame:
    """Canonical ticks for one symbol, read through the cache."""
    return load_ticks_cached(symbol, limit=limit, lookback_minutes=lookback_minutes,
                             end_time=end_time)

def load_ticks_cached(symbol: str | None, limit: int | None = None,
                      lookback_minutes: int | None = None,
                      end_time: object = None) -> pd.DataFrame:
    """Canonical ticks, served from Redis and narrowed to the request.

    The cached entry is the *whole* fixed window for a symbol, so the point in
    time and the lookback only trim the in-memory copy. A second request for the
    same symbol therefore never re-queries KQL, whatever it asks for.
    """
    s = get_settings()
    minutes = clamp_lookback(lookback_minutes, s)
    cap = int(limit or s.max_ticks)
    ck = cache.key(s, "ticks", (symbol or "").strip() or "all")

    raw = cache.get_frame(ck, s)
    if raw is None:
        raw = load_raw(symbol, limit=s.max_ticks, lookback_minutes=s.cache_lookback_minutes)
        cache.set_frame(ck, raw, s.cache_ttl_seconds, s)

    frame = from_ticks(_narrow(raw, minutes, cap, end_time))
    if frame.empty:
        window = f"in the last {minutes} minute(s)"
        if end_time is not None:
            window = f"ending at {end_time} within the last {minutes} minute(s)"
        raise DataSourceError(
            f"No usable aggregate rows for {symbol or 'any instrument'} {window}."
        )
    return frame


def reset() -> None:
    """Drop every cached fragment (catalogue, ticks, health probe).

    Redis holds the bulk of it, so the per-symbol tick keys and the catalogue
    key are deleted too; otherwise a refresh would rebuild them and still read
    the stale copy back. The refresh stamp goes with them: with an empty cache
    there is nothing the last cycle can vouch for.
    """
    global _instruments, _status, _last_refresh
    s = get_settings()
    cache.delete(s, cache.key(s, "catalogue"))
    for inst in (_instruments or {}):
        cache.delete(s, cache.key(s, "ticks", inst))
    cache.delete(s, cache.key(s, "ticks", "all"))
    _instruments = None
    _last_refresh = None
    _status = Status()
    return status(refresh=True)