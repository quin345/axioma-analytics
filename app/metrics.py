"""The precomputed volume-at-price ladders, served as a lookup.

`metrics_5m` holds, for every instrument and every 5-minute point in time, the
volume-at-price ladder already binned over the four fixed trailing windows
(1 h / 2 h / 3 h / 4 h). So a dashboard request is a **lookup of pre-aggregated
columns**: pick the row for the chosen point in time and the `vap_*` column for
the chosen window. Nothing is re-scanned in KQL and the ladder is not re-binned
in pandas, which is the whole point of the table (`docs/metrics_5m.md`).

This serves the one thing `metrics_5m` genuinely saves - the pre-binned
volume-at-price ladder, which the dashboard overlays onto the tick-derived
report. The scalar window metrics the table also ships (`ticks`, `vwap`,
`realized_vol_bps`, ...) are **not** read here: the headline tiles come from
`/api/analytics`, because every tile is a view of one tick frame and the two
bundles answer different windows (`metrics_5m` snaps `as_of` down to its
5-minute stop, the tick path ends at the instant asked for).

Two things make that cheap:

* the refresh cycle (`app.refresh`) pushes each symbol's grid to Redis, so the
  steady state is a Redis read, and
* the grid is one row per `as_of` stop, so stepping along the point-in-time
  timeline and switching window are both free - they pick a different row or a
  different column suffix out of what is already in the process.

The ladder arrives already binned by the KQL, so the volume profile needs no
re-binning; only the value area and the high-volume nodes are computed here,
from the materialised array. A cache miss or an unreadable table falls back to
reading KQL directly, preserving the existing contract: a KQL failure makes the
app unhealthy, a cache failure does not.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from . import cache, kql
from .analytics import VALUE_AREA_SHARE, clean, value_area
from .config import Settings, get_settings
from .errors import DataSourceError

log = logging.getLogger(__name__)

#: How many high-volume nodes the ladder reports.
_HIGH_VOLUME_NODES = 5


def _f(value: Any) -> float | None:
    """A JSON-safe float; NaN/Inf/null become None."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if (np.isnan(v) or np.isinf(v)) else v


def _ladder(cell: Any) -> list[dict]:
    """A `vap_*` dynamic cell as a list of bins, ascending by price.

    Kusto hands a `dynamic` array over as a list of dicts; after a Parquet
    round-trip through Redis it is a numpy object array of the same dicts.
    Anything else - an empty window, a null column - is an empty ladder, which
    the callers already render as an empty panel.
    """
    if cell is None:
        return []
    if isinstance(cell, float) and np.isnan(cell):        # a bare NaN cell
        return []
    rows = list(cell) if isinstance(cell, (list, tuple)) else list(
        np.asarray(cell, dtype=object).ravel()
    )
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        price = _f(row.get("price"))
        if price is None:
            continue
        out.append({
            "price": price,
            "volume": _f(row.get("volume")) or 0.0,
            "ticks": int(row.get("ticks") or 0),
        })
    out.sort(key=lambda b: b["price"])
    return out


def volume_profile(ladder: list[dict], poc: Any = None) -> dict:
    """The volume-profile panel from an already-materialised ladder.

    Same shape as `analytics.volume_profile` - bins descending by price, plus
    `poc`, `value_area` and `high_volume_nodes` - so the UI renders either source
    unchanged. The value area runs through `analytics.value_area`, the single
    implementation both paths share, so a profile read from the table and one
    binned in pandas cannot disagree about where the value area sits.

    The point of control is the **busiest bin of this ladder**. The panel draws
    the ladder and marks its POC bin amber by matching that bin's price, so a
    point that is not the tallest bar would be marked up as one. `metrics_5m`
    also ships a `poc_*` column, but it is a second, independent argmax over the
    same bins and the two can disagree - historically the KQL's `arg_max`
    arguments were swapped, which put a *volume* in that column and marked the
    wrong bar. The ladder is therefore the source of truth; the stored point is
    only what a ladder that traded nothing falls back to.
    """
    if not ladder:
        return {"bins": [], "poc": None, "value_area": None, "high_volume_nodes": []}

    vols = [b["volume"] for b in ladder]
    total = float(sum(vols))
    if total <= 0:
        # No bin has volume, so there is no busiest one to mark; the table's own
        # point, if it shipped one, is all there is to point at.
        return {"bins": list(reversed(ladder)), "poc": _f(poc),
                "value_area": None, "high_volume_nodes": []}

    poc_i = int(np.argmax(vols))
    point = float(ladder[poc_i]["price"])
    stored = _f(poc)
    if stored is not None and stored != point:
        log.debug("metrics_5m poc %s does not name the busiest bin (%s); "
                  "marking the ladder's own.", stored, point)

    cutoff = float(np.percentile(vols, 80))
    nodes = [
        ladder[i] for i in range(1, len(ladder) - 1)
        if vols[i] >= cutoff and vols[i] >= vols[i - 1] and vols[i] >= vols[i + 1]
    ]
    nodes.sort(key=lambda n: n["volume"], reverse=True)

    return clean({
        "bins": list(reversed(ladder)),
        "poc": point,
        "value_area": value_area(ladder, poc_i),
        "high_volume_nodes": sorted(
            nodes, key=lambda n: n["price"], reverse=True)[:_HIGH_VOLUME_NODES],
    })


def _as_utc(value: Any) -> pd.Timestamp | None:
    """A KQL `datetime` cell as a UTC timestamp, or None when it is null."""
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if ts is pd.NaT or pd.isna(ts):
        return None
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _times(frame: pd.DataFrame) -> list[pd.Timestamp | None]:
    """The `as_of` column as UTC timestamps, positionally aligned with the rows."""
    return [_as_utc(v) for v in frame["as_of"]]


def stops(frame: pd.DataFrame) -> list[str]:
    """Every `as_of` stop in the grid, oldest first, as ISO strings.

    Published so the UI can lay out the point-in-time timeline from the data it
    is actually served instead of assuming an even grid: a stalled metrics job
    leaves gaps, and a picker built on the real stops cannot offer a slot with
    nothing behind it.
    """
    if frame is None or frame.empty or "as_of" not in frame.columns:
        return []
    return [t.isoformat() for t in _times(frame) if t is not None]


def pick_stop(frame: pd.DataFrame, as_of: object = None) -> pd.Series | None:
    """The row for the chosen point in time.

    The grid is a discrete set of stops, so an arbitrary instant resolves to the
    newest stop at or before it - the same "as of" semantics as the tick path,
    where a window ends at the chosen moment. A point ahead of the data clamps to
    the newest stop and one before the whole grid to the earliest, so a stale tab
    or a bookmarked URL still resolves rather than failing. An unparseable point
    is refused rather than silently re-anchored on the newest row.
    """
    if frame is None or frame.empty or "as_of" not in frame.columns:
        return None
    times = _times(frame)
    known = [t for t in times if t is not None]
    if not known:
        return None
    newest = max(known)

    if as_of is None:
        return frame.iloc[[i for i, t in enumerate(times) if t == newest][-1]]

    try:
        want = pd.Timestamp(as_of)
    except (TypeError, ValueError) as exc:
        raise DataSourceError(f"Unreadable point in time: {as_of!r}.") from exc
    want = want.tz_localize("UTC") if want.tzinfo is None else want.tz_convert("UTC")

    eligible = [i for i, t in enumerate(times)
                if t is not None and t <= min(want, newest)]
    if not eligible:
        earliest = min(known)
        eligible = [i for i, t in enumerate(times) if t == earliest]
    return frame.iloc[eligible[-1]]


def requested_window(minutes: Any) -> int:
    """The window a request asks for, defaulting to the narrowest the table ships.

    The dashboard defaults to the 1 h window, so an absent parameter means the
    shortest stop rather than the widest - the UI's own default has to match what
    the API assumes, or the first paint would show a different span from the one
    the control is set to.
    """
    if minutes is None:
        return kql.WINDOW_SUFFIXES[0][1]
    try:
        want = int(minutes)
    except (TypeError, ValueError):
        return kql.WINDOW_SUFFIXES[0][1]
    return max(kql.WINDOW_SUFFIXES[0][1], want)


def report_key(symbol: str | None, stop: object,
               settings: Settings | None = None) -> str:
    """The Redis key holding one symbol's built report bundle at one stop.

    Keyed by the *resolved* stop, not the requested instant: two requests that
    resolve to the same `as_of` row share one entry, while different stops never
    collide. The grid is append-mostly, so a stop's row is near-immutable; when
    the refresh cycle does rewrite a stop (`refresh_symbol`), it purges these
    keys for that symbol rather than versioning them.
    """
    s = settings or get_settings()
    try:
        at = _as_utc(stop)
        stamp = at.isoformat() if at is not None else str(stop)
    except Exception:
        stamp = str(stop)
    return metrics_prefix(s) + ":report:" + ((symbol or "").strip() or "all") + ":" + stamp


def report(symbol: str | None = None, *, window_minutes: int | None = None,
           as_of: object = None, settings: Settings | None = None) -> dict:
    """The precomputed volume-at-price bundle for one symbol: every window at one stop.

    All four windows come back together, because they are four ladder column sets
    on one row - so the UI can switch window without a request, exactly as it
    switches point in time. `window_minutes` only decides which one
    `selected_window_minutes` reports.

    This serves the one thing `metrics_5m` genuinely saves - the pre-binned
    volume-at-price ladder, which the dashboard overlays onto the tick-derived
    report. The headline scalar tiles are **not** here: they come from
    `/api/analytics`, because every tile is a view of one tick frame and the two
    bundles answer different windows (`metrics_5m` snaps `as_of` down to its
    5-minute stop, the tick path ends at the instant asked for).

    The built bundle is cached in Redis under `report_key` (one entry per
    resolved stop), so a repeat request for the same symbol and stop is a JSON
    read: no grid deserialise, no profile rebuild. The stop resolution itself
    stays live - it is a row pick over the cached grid - but resolving and then
    hitting the report entry is far cheaper than rebuilding all four volume
    profiles. `requested_as_of` and `generated_at` differ per request, so they
    are stamped on after the cache read rather than stored.
    """
    s = settings or get_settings()
    frame = load(symbol, settings=s)
    if frame.empty:
        raise DataSourceError(
            f"No precomputed metrics for {symbol or 'any instrument'}. "
            "The metrics_5m refresh may not have run yet."
        )
    acct = _account_for(symbol, s)
    if acct and "ctidTraderAccountId" in frame.columns:
        scoped = frame.loc[
            frame["ctidTraderAccountId"].astype(str).str.strip() == acct
        ]
        if not scoped.empty:
            frame = scoped

    row = pick_stop(frame, as_of)
    if row is None:
        raise DataSourceError(f"No precomputed metrics for {symbol or 'any instrument'}.")
    at = _as_utc(row.get("as_of"))

    want = requested_window(window_minutes)
    # A window the table does not ship resolves to the widest one it does that is
    # no wider, and the resolution is reported so the UI never shows a span it was
    # not actually given.
    suffix = kql.best_window(want)
    served = next(span for suf, span in kql.WINDOW_SUFFIXES if suf == suffix)

    stops_list = stops(frame)
    rk = report_key(symbol, at, s)
    try:
        stored = cache.get_json(rk, s)
    except Exception:
        stored = None
    if isinstance(stored, dict) and stored.get("volume_profiles"):
        return clean({
            **stored,
            "selected_window_minutes": served,
            "requested_window_minutes": want,
            "stops": stops_list,
            "meta": {
                **(stored.get("meta") or {}),
                "as_of": at.isoformat() if at is not None else None,
                "requested_as_of": str(as_of) if as_of is not None else None,
                "stops_available": len(stops_list),
                "generated_at": pd.Timestamp.utcnow().isoformat(),
            },
        })

    profiles = {
        str(span): volume_profile(_ladder(row.get(f"vap_{suf}")), row.get(f"poc_{suf}"))
        for suf, span in kql.WINDOW_SUFFIXES
    }

    bundle = clean({
        "volume_profiles": profiles,
        "selected_window_minutes": served,
        "requested_window_minutes": want,
        "window_choices_minutes": [span for _, span in kql.WINDOW_SUFFIXES],
        "value_area_share": VALUE_AREA_SHARE,
        "stops": stops_list,
        "meta": {
            "symbol": str(row.get("symbolId", "")).strip() or (symbol or ""),
            "as_of": at.isoformat() if at is not None else None,
            "requested_as_of": str(as_of) if as_of is not None else None,
            "stops_available": len(stops_list),
            "source": s.metrics_table,
            "generated_at": pd.Timestamp.utcnow().isoformat(),
        },
    })
    try:
        cache.set_json(rk, bundle, s.cache_ttl_seconds, s)
    except Exception:
        pass
    return bundle


# --------------------------------------------------------------------------
# Cache and refresh
# --------------------------------------------------------------------------

def metrics_prefix(settings: Settings | None = None) -> str:
    """The Redis key prefix every per-symbol metrics grid hangs off."""
    s = settings or get_settings()
    return cache.key(s, "metrics", object_name=s.metrics_table)


def metrics_key(symbol: str | None, settings: Settings | None = None) -> str:
    """The Redis key holding one symbol's precomputed grid."""
    s = settings or get_settings()
    return metrics_prefix(s) + ":" + ((symbol or "").strip() or "all")


def load(symbol: str | None = None, *, account: str | None = None,
         settings: Settings | None = None) -> pd.DataFrame:
    """One composite pair's precomputed grid, served from Redis.

    The cached entry is the *whole* grid, so a point-in-time or window change is a
    lookup against what is already in the process. A miss reads KQL and writes
    back, which is what warms the cache on a cold start or after a TTL expiry; a
    cache failure simply falls through to the KQL read rather than erroring.
    `account` is the ``ctidTraderAccountId`` half of the composite the broker
    dimension keys on; blank means any account. When it is blank the KQL read
    is narrowed to the account the catalogue resolved the id to, so an id
    traded under two feeds does not interleave both feeds' stops.
    """
    s = settings or get_settings()
    ck = metrics_key(symbol, s)

    raw = cache.get_frame(ck, s)
    if raw is None:
        acct = (account or "").strip() or _account_for(symbol, s)
        with kql.connect(s) as client:
            raw = kql.fetch_metrics(client, symbol=symbol,
                                    account=acct or None, settings=s)
        if raw is not None and not raw.empty:
            cache.set_frame(ck, raw, s.cache_ttl_seconds, s)
    return raw if raw is not None else pd.DataFrame()


def cached_symbols(settings: Settings | None = None) -> list[str]:
    """The symbol entries the metrics cache holds right now, as key suffixes.

    Read out of Redis rather than tracked in memory, for the same reason as the
    tick path: another process may have written an entry, and a symbol nobody has
    asked for should drop out of the refresh set on its own when its TTL expires
    instead of being re-read from KQL forever.
    """
    s = settings or get_settings()
    marker = metrics_prefix(s) + ":"
    # The grid entries live directly under the prefix (`metrics:<symbol>`); the
    # built report bundles hang one level deeper (`metrics:report:<symbol>:...`)
    # and must not be mistaken for cached symbols by the refresh cycle.
    return sorted(
        k[len(marker):]
        for k in cache.keys(s, s.kql_database, s.metrics_table, "metrics")
        if k.startswith(marker) and len(k) > len(marker)
        and not k[len(marker):].startswith("report:")
    )


def slide(frame: pd.DataFrame, added: pd.DataFrame, limit: int) -> pd.DataFrame:
    """Merge a freshly read slice into the cached grid.

    The grid is keyed on `as_of`, not on when the row arrived, so this is a
    "newest row per stop wins" rather than a concatenate: every cycle recomputes
    the same stops, and the newest computation of a stop is the correct one even
    when the two disagree. Stops past `limit` are dropped oldest-first, so the
    grid holds the recent point-in-time range and cannot grow without bound.
    """
    if added is not None and not added.empty and "as_of" in added.columns:
        frame = pd.concat([frame, added], ignore_index=True)
    if frame is None or frame.empty or "as_of" not in frame.columns:
        return frame
    merged = frame.assign(_ts=_times(frame))
    merged = merged.loc[merged["_ts"].notna()]
    if "ctidTraderAccountId" in merged.columns:
        merged = merged.drop_duplicates(subset=["symbolId", "ctidTraderAccountId", "_ts"], keep="last")
    else:
        merged = merged.drop_duplicates(subset=["symbolId", "_ts"], keep="last")
    merged = merged.sort_values("_ts", kind="stable")
    return merged.drop(columns="_ts").tail(int(limit)).reset_index(drop=True)


def _account_for(symbol: str | None, s) -> str:
    """The account the catalogue resolved one symbol id to, or blank.

    Imported lazily so ``app.service`` (which imports this module) does not
    become a circular import at module load.
    """
    sid = (symbol or "").strip()
    if not sid:
        return ""
    try:
        from . import service as _service
        inst = _service.lookup(sid)
    except Exception:
        return ""
    return (inst.account if inst is not None else "") or ""


def refresh_symbol(symbol: str | None = None,
                   settings: Settings | None = None) -> pd.DataFrame:
    """Re-read one symbol's grid from KQL and replace its cache entry.

    Incremental where it can be: a readable cached entry means only the stops
    newer than the newest cached one are read, so a cycle does not re-read the
    whole grid every half hour. The caller catches a failed read - a symbol whose
    KQL read fails keeps its previous entry, because the cycle is an optimisation
    and a partial failure must not throw away data that is merely older. The
    read is scoped to the catalogue-resolved account, as in :func:`load`.
    """
    s = settings or get_settings()
    ck = metrics_key(symbol, s)
    cached = cache.get_frame(ck, s)

    start = None
    if cached is not None and not cached.empty and "as_of" in cached.columns:
        times = [t for t in _times(cached) if t is not None]
        if times:
            start = max(times).isoformat()

    acct = _account_for(symbol, s)
    with kql.connect(s) as client:
        added = kql.fetch_metrics(client, symbol=symbol,
                                  account=acct or None,
                                  start=start, settings=s)
    grid = slide(cached if cached is not None else pd.DataFrame(), added, s.max_ticks)
    if not grid.empty:
        cache.set_frame(ck, grid, s.cache_ttl_seconds, s)
        # A rewritten grid may have recomputed a stop this report cache already
        # built: drop this symbol's report entries so the next request rebuilds
        # from the fresh row rather than serving the stale bundle.
        purge_reports(symbol, s)
    return grid


def report_prefix(symbol: str | None = None,
                  settings: Settings | None = None) -> str:
    """The Redis key prefix holding one symbol's built report bundles."""
    s = settings or get_settings()
    return metrics_prefix(s) + ":report:" + (((symbol or "").strip() or "all") + ":")


def purge_reports(symbol: str | None = None,
                  settings: Settings | None = None) -> None:
    """Drop one symbol's built report bundles after its grid was rewritten."""
    s = settings or get_settings()
    try:
        stale = [k for k in cache.keys(s, s.kql_database, s.metrics_table,
                                       "metrics", "report")
                 if k.startswith(report_prefix(symbol, s))]
    except Exception:
        return
    if stale:
        cache.delete(s, *stale)

