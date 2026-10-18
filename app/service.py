"""Data access: KQL ticks with a Redis cache, metadata from local PostgreSQL.

`app.kql` is the source of metric rows: the per-tick aggregates in
``agg_dom`` (and the ``metrics_5m`` grid) live in the Fabric KQL database.
The instrument dimension moved out: ``symbols_metadata`` lives in local
PostgreSQL (``app.meta``) - same symbol columns as before, but
broker-agnostic, one row per (symbol, cTrader account) with each account id
mapped to its broker name. This module owns the connection lifecycle, the
instrument catalogue (ticker, asset class, tick count) and the tick fetch,
and puts every result in Redis so a dashboard request never re-scans a table
an earlier request has already read.

The cache window is fixed. One KQL read covers `CACHE_LOOKBACK_HOURS` (8 by
default) of ticks per symbol; the point in time and the aggregate window the
caller asks for only *narrow* that window in memory. Selecting 30 minutes after
8 hours therefore costs no query at all, and the UI cannot ask for more history
than the cache holds - which is what makes a 4-hour timeline with a 240 minute
window at its earliest point work: 4 + 4 hours is exactly the window.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

import pandas as pd

from . import cache, kql, meta, metrics
from .assets import CLASS_LABELS, classify_asset_class, family_label, family_of, sort_key
from .config import Settings, get_settings
from .errors import DataSourceError
from .frames import from_ticks

log = logging.getLogger(__name__)


@dataclass
class Instrument:
    """One tradable instrument, with everything the UI needs to show it.

    The catalogue is keyed by the ``(symbolId, ctraderAccountId)`` composite
    so the same symbol id bound to several cTrader accounts keeps each
    account's own symbol name *and* its broker; ``symbol_id`` itself stays the
    plain id so tick-cache keys, lookups and the selector payload keep working
    by symbol. ``name`` stays the bare ticker (classification and the
    default-ticker match compare it exactly) while ``broker`` - read from the
    same dimension row, hence the correct one for this account - is appended
    in ``display``, which is what the UI shows as the symbol name.
    """
    symbol_id: str
    name: str
    description: str
    asset_class: str
    ticks: int
    account: str = ""
    #: Broker of the account this row is bound to (``broker_name``), or blank
    #: for an instrument the metadata dimension does not describe.
    broker: str = ""

    @property
    def display(self) -> str:
        """The symbol name with its broker appended: ``XAUUSD (Broker)``.

        The broker comes from the same ``(symbolId, ctraderAccountId)`` row as
        the ticker, so the composite key is what makes it the *right* broker
        for this entry; without an account scope the first row's broker would
        label every account's symbol.
        """
        base = self.name or self.symbol_id
        broker = (self.broker or "").strip()
        return f"{base} ({broker})" if broker else base

    @property
    def family(self) -> str:
        return family_of(self.asset_class)

    def to_dict(self) -> dict:
        label = CLASS_LABELS.get(self.asset_class, "Unclassified")
        return {
            "symbol": self.symbol_id,
            # What the UI shows: the ticker with its broker appended.
            "name": self.display,
            # Bare ticker and broker separately, so a client that needs an
            # exact match (the XAUUSD default) never parses the label back.
            "ticker": self.name,
            "broker": self.broker,
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
    #: Rows in the Postgres ``symbols_metadata`` dimension, or ``None`` when unreadable.
    dimension_rows: int | None = None
    #: Postgres metadata reachability. ``None`` while it has not been probed.
    meta_connected: bool | None = None
    meta_error: str | None = None
    #: Redis cache reachability. ``None`` while it has not been probed.
    cache_connected: bool | None = None
    cache_error: str | None = None
    #: The raw cache window (480), kept for the badge tooltip and for clients
    #: built before the rename to "window": the UI never asks for this much.
    lookback_minutes: int | None = None
    #: The aggregate window the analysis runs over, echoed so the UI can bound
    #: the window control to it.
    window_minutes: int | None = None
    #: The point-in-time controls, echoed with the window so the UI can build
    #: them from the API: a `timeline_minutes` long timeline in
    #: `timeline_step_minutes` steps, and the fixed aggregate-window choices
    #: (`window_choices_minutes`, the four `metrics_5m` windows 60/120/180/240).
    timeline_minutes: int | None = None
    timeline_step_minutes: int | None = None
    window_choices_minutes: list[int] | None = None
    window_min_minutes: int | None = None
    window_max_minutes: int | None = None
    #: Whether the precomputed `metrics_5m` table backs the metrics read path.
    metrics_5m: bool | None = None
    #: Rows in the precomputed table, or ``None`` when it could not be read.
    metrics_rows: int | None = None
    #: Newest `as_of` stop in the precomputed table.
    metrics_latest: str | None = None


_status = Status()
_instruments: dict[str, Instrument] | None = None
#: Summary of the last completed refresh cycle, for the health payload.
_last_refresh: dict | None = None


# --------------------------------------------------------------------------
# Window
# --------------------------------------------------------------------------

def clamp_window(minutes: int | None, settings: Settings | None = None) -> int:
    """The requested aggregate window, clamped to the fixed cache window.

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
        window_minutes=s.cache_lookback_minutes,
        timeline_minutes=max(1, s.timeline_minutes),
        timeline_step_minutes=max(1, s.timeline_step_minutes),
        window_choices_minutes=list(s.window_choices),
        window_min_minutes=max(1, s.window_min_minutes),
        window_max_minutes=s.max_window_minutes,
        metrics_5m=s.use_metrics_5m,
    )
    try:
        with kql.connect(s) as client:
            st.server_time = kql.server_time(client, s)
            st.row_count, st.latest = kql.tick_stats(client, s)
            if s.use_metrics_5m:
                st.metrics_rows, st.metrics_latest = kql.metrics_stats(client, s)
        st.connected, st.error = True, None
        if not st.row_count:
            st.hints = ["The KQL metrics table is empty. Run the ingest pipeline."]
        elif s.use_metrics_5m and not st.metrics_rows:
            # The headline metrics come from this table, so an empty one is a
            # real gap even while the ticks are flowing - reported as a hint
            # rather than an error, because the pandas path still works.
            st.hints = [
                "The precomputed metrics_5m table is empty. Run the Metrics5m "
                "refresh, or set METRICS_SOURCE=ticks to recompute from agg_dom."
            ]
        elif s.use_metrics_5m and not st.metrics_latest:
            st.hints = ["The precomputed metrics_5m table has no as_of stops yet."]
    except DataSourceError as exc:
        st.connected, st.error = False, str(exc)
        st.hints = ["The KQL endpoint is unavailable. Check the configured identity."]

    # Metadata lives in local Postgres now, probed independently of KQL: a
    # metadata outage leaves ticks readable but names/classification stale.
    try:
        st.dimension_rows = meta.symbol_count(s)
        st.meta_connected, st.meta_error = True, None
        if st.connected and not st.dimension_rows:
            st.hints = ["The Postgres symbols_metadata dimension is empty. "
                        "Run the metadata sync."]
    except DataSourceError as exc:
        st.meta_connected, st.meta_error = False, str(exc)
        st.hints.append(f"The Postgres metadata is unavailable: {exc}")

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
    written to it, while ``symbols_metadata`` covers only the scoped
    account(s)/broker(s). Any other feed's instruments therefore cannot be
    named or classified. They are
    left out of the selectors, so say so rather than leaving the count to be
    discovered by its absence.
    """
    missing = unclassified_count()
    if not missing:
        return None
    return (
        f"{missing} instrument(s) in the metrics have no row in "
        f"symbols_metadata and are not shown in the selectors. They come from "
        f"a feed the scoped metadata does not cover."
    )


# --------------------------------------------------------------------------
# Instrument catalogue
# --------------------------------------------------------------------------

def _catalogue_key(symbol_id: str, account: str) -> str:
    """Composite catalogue key: ``symbolId`` plus ``ctraderAccountId``.

    Keeps one entry per ``(symbol, account)`` so the same symbol id bound to
    several cTrader accounts keeps each account's own symbol name instead of
    letting the first row per id silence the rest. The broker rides on the
    same row as the account, so the composite is also what gives every entry
    the broker its own account belongs to.
    """
    return f"{symbol_id}|{account}"


def _build_catalogue(*, refresh: bool = False) -> dict[str, Instrument]:
    """Merge the Postgres symbol dimension with the KQL tick counts.

    Names/classification come from local ``symbols_metadata`` (broker-agnostic,
    scoped by account/broker); tick counts come from KQL. The catalogue is keyed
    by the ``(symbolId, ctraderAccountId)`` composite so the same symbol id
    bound to several cTrader accounts keeps each account's own symbol name
    *and* the broker that account belongs to - the name the UI shows is the
    ticker with that broker appended. Both reads are whole tables, so the
    merged catalogue goes to Redis and the next request - in this process or
    another - reuses it instead of repeating them.
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
        dim = meta.symbol_catalogue(s)
        counts = kql.symbol_tick_counts(client, s)

    out: dict[str, Instrument] = {}
    for row in dim.itertuples():
        symbol_id = _text(row.symbolId)
        account = _text(getattr(row, "ctrader_account_id", ""))
        if not symbol_id:
            continue
        # Postgres returns typed nulls; normalise those to empty strings. One
        # row per (symbol, account) survives in the dimension, so keying by the
        # composite preserves every account's symbol name *and* its broker -
        # both live on the same row, so each entry is labelled with the broker
        # its own account belongs to; counts still come from KQL and are keyed
        # by symbol id alone.
        key = _catalogue_key(symbol_id, account)
        name = _text(row.symbolName)
        broker = _text(getattr(row, "broker_name", ""))
        description = _text(row.description)
        category = _text(getattr(row, "symbolCategoryId", ""))
        out[key] = Instrument(
            symbol_id=symbol_id,
            account=account,
            broker=broker,
            name=name,
            description=description,
            asset_class=classify_asset_class(name, None, category, description),
            ticks=int(counts.get(symbol_id, 0)),
        )
    # A symbol present in the tick rows but missing from the dimension under
    # every account still belongs in the selector; classify it from its id
    # alone and key it with no account scope so it stays reachable by symbol id.
    present = {inst.symbol_id for inst in out.values()}
    for symbol_id, ticks in counts.items():
        if symbol_id not in present:
            out[_catalogue_key(symbol_id, "")] = Instrument(symbol_id=symbol_id, account="", name="", description="",
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
    """The instrument record for one symbol id.

    The catalogue is keyed by the ``(symbolId, ctraderAccountId)`` composite,
    so a bare symbol id resolves to the row bound to the configured account
    (``CTRADER_ACCOUNT_ID``) when one is set, otherwise the first matching row.
    """
    want = str(symbol_id).strip()
    if not want:
        return None
    account = (get_settings().ctrader_account_id or "").strip()
    rows = instruments().values()
    if account:
        for inst in rows:
            if inst.symbol_id == want and inst.account == account:
                return inst
    for inst in rows:
        if inst.symbol_id == want:
            return inst
    return None


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
      on a five-minute cycle that is exactly the earliest slice - then caps
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
    * the window for every symbol already in the cache, plus its precomputed
      `metrics_5m` grid - whatever data is there, no default warming. A cold
      cache stays empty until a request lazily loads it, so the cycle never
      invents targets of its own.

    A symbol already cached is refreshed **incrementally**: only the rows newer
    than its newest cached row are read from KQL, the earliest rows that have
    aged out of the eight-hour window are purged, and the merged window is
    written back (`_slide_window`). A symbol with no entry (cold start, TTL
    expiry) is read over the whole window instead. A symbol whose read fails
    keeps its previous entry rather than being evicted: the cycle is an
    optimisation, and a partial failure must not throw away data that is merely
    older. Returns a JSON-able summary, which ``/api/health`` publishes.
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

    metrics_symbols = metric_rows = 0
    if s.use_metrics_5m:
        # Same target set as the ticks: the symbols the dashboard is actually
        # serving. A metrics read that fails leaves the previous grid in place,
        # exactly as a failed tick read does.
        for name in sorted(targets):
            symbol = None if name == "all" else name
            try:
                grid = metrics.refresh_symbol(symbol, s)
            except DataSourceError as exc:
                errors.append(f"metrics {name}: {exc}")
                continue
            if not grid.empty:
                metrics_symbols += 1
                metric_rows += int(len(grid))

    finished = pd.Timestamp.utcnow()
    _last_refresh = {
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "seconds": round((finished - started).total_seconds(), 2),
        "symbols": refreshed,
        "rows": rows,
        "purged": purged,
        # The precomputed grid pushed alongside the ticks: how many symbols and
        # how many `as_of` stops are now cached for `/api/metrics`.
        "metrics_symbols": metrics_symbols,
        "metrics_rows": metric_rows,
        # The cache window being refreshed (480 by default). Named apart from
        # the health payload's `window_minutes`, which is the *aggregate* window
        # the analysis runs over and is capped by this one.
        "cache_window_minutes": s.cache_lookback_minutes,
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


def _narrow(raw: pd.DataFrame, window_minutes: int, limit: int,
            end_time: object = None) -> pd.DataFrame:
    """The requested slice of the cached window: anchored, then capped.

    Without `end_time` the anchor is the newest row, which keeps the result the
    most recent session rather than an arbitrary slice of history. With it the
    anchor is the chosen point in time: the slice *ends* there, so stepping back
    along the timeline returns the window that led up to that moment. A point
    ahead of the data is clamped to the newest row, and an unparseable one is
    refused rather than silently re-anchored on the newest row.

    Doing the work in pandas rather than KQL is what makes a point or window
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
    cutoff = end - pd.Timedelta(minutes=int(window_minutes))
    return raw.loc[(ts >= cutoff) & (ts <= end)].tail(int(limit))


def load_ticks(symbol: str | None = None, *, limit: int | None = None,
               window_minutes: int | None = None,
               end_time: object = None) -> pd.DataFrame:
    """Canonical ticks for one symbol, read through the cache."""
    return load_ticks_cached(symbol, limit=limit, window_minutes=window_minutes,
                             end_time=end_time)

def load_ticks_cached(symbol: str | None, limit: int | None = None,
                      window_minutes: int | None = None,
                      end_time: object = None) -> pd.DataFrame:
    """Canonical ticks, served from Redis and narrowed to the request.

    The cached entry is the *whole* fixed window for a symbol, so the point in
    time and the aggregate window only trim the in-memory copy. A second request
    for the same symbol therefore never re-queries KQL, whatever it asks for.
    """
    s = get_settings()
    minutes = clamp_window(window_minutes, s)
    cap = int(limit or s.max_ticks)
    ck = cache.key(s, "ticks", (symbol or "").strip() or "all")

    raw = cache.get_frame(ck, s)
    if raw is None:
        raw = load_raw(symbol, limit=s.max_ticks, lookback_minutes=s.cache_lookback_minutes)
        cache.set_frame(ck, raw, s.cache_ttl_seconds, s)

    frame = from_ticks(_narrow(raw, minutes, cap, end_time))
    if frame.empty:
        window = f"in the {minutes} minute(s) aggregate window"
        if end_time is not None:
            window = f"ending at {end_time} within the last {minutes} minute(s)"
        raise DataSourceError(
            f"No usable aggregate rows for {symbol or 'any instrument'} {window}."
        )
    return frame


def reset() -> None:
    """Drop every cached fragment (catalogue, ticks, metrics, health probe).

    Redis holds the bulk of it, so the per-symbol tick keys, the per-symbol
    precomputed grids and the catalogue key are deleted too; otherwise a refresh
    would rebuild them and still read the stale copy back. The refresh stamp goes
    with them: with an empty cache there is nothing the last cycle can vouch for.
    """
    global _instruments, _status, _last_refresh
    s = get_settings()
    cache.delete(s, cache.key(s, "catalogue"))
    # The catalogue is keyed by (symbolId, account), but tick and metrics
    # cache entries live under the plain symbol id, so purge by symbol_id.
    for inst in (_instruments or {}).values():
        cache.delete(s, cache.key(s, "ticks", inst.symbol_id))
        cache.delete(s, metrics.metrics_key(inst.symbol_id, s))
    cache.delete(s, cache.key(s, "ticks", "all"))
    cache.delete(s, metrics.metrics_key(None, s))
    _instruments = None
    _last_refresh = None
    _status = Status()
    return status(refresh=True)