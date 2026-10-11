# `metrics_5m` — precomputed window metrics (KQL reference)

> **Status:** reference / source-of-truth for the KQL that lives in the metrics
> repo. This app (`axioma-analytics`) does **not** run this query; it only reads
> the resulting table. Keep this document beside the KQL so the Python side
> (`app/analytics.py`) and the KQL stay in sync.

## Why this exists

Today `app/analytics.py` recomputes every metric in pandas on **every** request.
`app/service.py` / `app/cache.py` only cache the **raw ticks** (`agg_dom` rows)
in Redis, so the heavy work — OHLC, realised vol, order-flow, the volume-at-price
ladder, drawdown, distributions — is redone each time a dashboard loads, even for
the same point in time and window.

`metrics_5m` moves that work **server-side and one time per window**: a KQL table
holds, for every instrument and every 5-minute point in time, the headline metrics
already aggregated over fixed **1 h / 2 h / 3 h / 4 h** trailing windows. That
table is what gets pushed to **Redis**. A dashboard request becomes a lookup of a
handful of pre-computed columns, never a re-scan of `agg_dom` and never a re-derive
in pandas.

## The contract

| Property | Value | Mirrors |
|---|---|---|
| Grain | one row per `(symbolId, as_of)` | — |
| Point-in-time grid | last **4 h** in **5-minute** steps (49 stops, `as_of` inclusive) | `TIMELINE_MINUTES=240`, `TIMELINE_STEP_MINUTES=5` |
| Windows (fixed) | **60 / 120 / 180 / 240** minutes, each ending at `as_of` | replaces the old 5–240 min slider |
| Scalar columns | **10 metrics × 4 windows = 40** columns (`<metric>_1h … <metric>_4h`) | `app/analytics.py` summary/OFI/spread/vol |
| Nested columns | `dynamic` objects per window (`vap_1h…`, `dist_1h…`, …) | `volume_profile`, `return_distribution` |
| Source table | `ctrader_dom.agg_dom` (per-tick derived order-book metrics) | `kql.KQL_TABLE` |
| Scan span | **8 h** of ticks (4 h timeline + 4 h widest window) | `CACHE_LOOKBACK_HOURS=8` |
| Refresh | rebuilt on the same clock cadence as the cache today | `CACHE_REFRESH_MINUTES=30` |

Row shape (one 5-minute `as_of` stop for one symbol):

```
symbolId : long
as_of    : datetime            // the point in time this row is "as of"
ticks_1h … ticks_4h            : long      // 4 cols
open_1h  … open_4h             : real      // 4
high_1h  … high_4h             : real      // 4
low_1h   … low_4h              : real      // 4
close_1h … close_4h            : real      // 4
vwap_1h  … vwap_4h             : real      // 4
total_volume_1h … _4h          : real      // 4
realized_vol_bps_1h … _4h      : real      // 4
avg_spread_bps_1h … _4h        : real      // 4
ofi_imbalance_1h … _4h         : real      // 4   -> 40 scalar columns
vap_1h   … vap_4h              : dynamic   // volume-at-price ladder (nested)
dist_1h  … dist_4h             : dynamic   // return-distribution histogram (nested)
```

### The 10 windowed metrics

| Metric | Type | Definition (over the window ending at `as_of`) | `analytics.py` analogue |
|---|---|---|---|
| `ticks` | long | tick count | `summary.ticks` |
| `open` | real | first mid by time (`arg_min(mid, timestamp)`) | `summary.open` |
| `high` | real | `max(mid)` | `summary.high` |
| `low` | real | `min(mid)` | `summary.low` |
| `close` | real | last mid by time (`arg_max(mid, timestamp)`) | `summary.close` |
| `vwap` | real | `sum(mid*volume)/sum(volume)` | `summary.vwap` |
| `total_volume` | real | `sum(volume)` | `summary.total_volume` |
| `realized_vol_bps` | real | `sqrt(Σ ret² / duration_s) × 10000` | `summary.realized_vol_bps` |
| `avg_spread_bps` | real | `avg(spread_bps)` | `summary.avg_spread` |
| `ofi_imbalance` | real | `sum(signed_volume)/sum(abs(signed_volume))` ∈ [-1,1] | `microstructure.order_flow` |

`change_pct`, `tick_rate`, `median_spread`, `p95_spread`, `depth_imbalance` are
trivial functions of the 40 columns above and are derived **after** the pivot (see
*Derived columns*), so they cost nothing to add without another table scan.
---

## The KQL

The query has five stages. Keep them as one stored function `Metrics5m()` so the
refresh job and any backfill share it.

### Stage 0 — parameters and anchors

```kql
// Grid geometry (mirror app/config.py; change here, change there).
let timeline_minutes   = 240;   // TIMELINE_MINUTES
let timeline_step_min  = 5;     // TIMELINE_STEP_MINUTES
let win1 = 60; let win2 = 120; let win3 = 180; let win4 = 240;  // fixed windows

// The 5-minute point-in-time grid, anchored on each symbol's newest tick.
let steps = range k from 0 to timeline_minutes step timeline_step_min;
let sym_anchor =
    agg_dom
    | summarize anchor = max(timestamp) by symbolId;
let grid =
    sym_anchor
    | extend dummy = 1
    | join kind=inner (steps | extend dummy = 1) on dummy
    | extend as_of = anchor - k * 1m
    | project symbolId, as_of, dummy
    | project-away dummy;

// The four windows as rows, so one join carries all of them.
let WINDOWS = datatable(win: string, minutes: int) [
    "1h", win1, "2h", win2, "3h", win3, "4h", win4 ];
```

### Stage 1 — normalise the tick stream (mirror `frames.from_ticks`)

This is the KQL twin of `app/frames.py`. It must stay in lock-step with it: same
`mid`, same `volume` scale, same directional signal, same one-sided drop.

```kql
let TICKS =
    agg_dom
    // 8 h of ticks: 4 h timeline + the 4 h widest window from its earliest stop.
    | where timestamp >= ago(8h)
    | where isnotnull(symbolId) and isnotnull(timestamp)
    | where best_bid > 0 and best_ask > 0            // drop one-sided / null quotes
    | extend mid   = (best_bid + best_ask) / 2.0
    | extend volume = (total_bid + total_ask) / 100.0 // VOLUME_SCALE = 100 (frames.py)
    // abs() keeps this correct for the mirrored/majority-crossed book: mid is
    // swap-invariant and only the spread sign flips, which we discard.
    | extend spread_bps = abs(best_ask - best_bid) / mid * 10000.0
    // Directional signal: pipeline imbalance, else resting-size skew (frames.py).
    | extend signed_volume = coalesce(imbalance, total_bid - total_ask)
    | sort by symbolId asc, timestamp asc, eventSeq asc
    | serialize
        prev_mid = prev(mid)
    | extend ret = iif(isnull(prev_mid) or prev_mid == 0, real(null),
                       (mid - prev_mid) / prev_mid)
    | project symbolId, timestamp, eventSeq, mid, volume,
              spread_bps, signed_volume, ret;
```

`ret` is computed once per tick on the whole per-symbol stream, exactly as
`analytics.py` computes returns on the whole prepared frame and then slices. So the
first in-window return still uses the tick just before the window opens — matching
the pandas `pct_change()` behaviour, and making `realized_vol_bps` faithful.


### Stage 2 — as-of window join (the fan-out)

One inner join produces one row per `(symbolId, as_of, win, tick)`. `eventSeq`
breaks the sub-millisecond ties `agg_dom` shares on `timestamp`.

```kql
let JOINED =
    grid
    | extend dummy = 1
    | join kind=inner (WINDOWS | extend dummy = 1) on dummy
    | project-away dummy
    | join kind=inner hint.shufflekey = symbolId (
        TICKS
      ) on symbolId
    | where timestamp between (as_of - minutes * 1m .. as_of);
```

> **Cost note.** This is a bounded cross product: ≤49 `as_of` stops × 4 windows ×
> the ticks inside each window, for one symbol at a time (the `shufflekey` keeps
> it per-partition). It runs on the refresh cadence, never per request, so the
> cost is paid once per cycle — the whole point of `metrics_5m`.

### Stage 3 — scalar metrics per window

Aggregate to one row per `(symbolId, as_of, win)`, then **spread `win` into
columns with explicit conditional aggregation**. We do *not* use `evaluate pivot`
for this: its output schema is data-dependent (per the plugin docs), whereas the
40 column names are a hard contract the app reads by name.

```kql
let SCALARS =
    JOINED
    | summarize
        ticks           = count(),
        open            = arg_min(mid, timestamp),
        high            = max(mid),
        low             = min(mid),
        close           = arg_max(mid, timestamp),
        vwap            = iif(sum(volume) > 0, sum(mid * volume) / sum(volume), avg(mid)),
        total_volume    = sum(volume),
        avg_spread_bps  = avg(spread_bps),
        ofi_imbalance   = iif(sum(abs(signed_volume)) > 0,
                              sum(signed_volume) / sum(abs(signed_volume)), real(null)),
        sum_retsq       = sum(pow(ret, 2)),
        dur_s           = (max(timestamp) - min(timestamp)) / 1s
      by symbolId, as_of, win
    | extend realized_vol_bps =
        iif(dur_s > 0, sqrt(sum_retsq / dur_s) * 10000.0, real(null))
    | project-away sum_retsq, dur_s
    // ---- spread the four windows into 40 stable-named columns ----
    | summarize
        ticks_1h = take_anyif(ticks, win == "1h"), ticks_2h = take_anyif(ticks, win == "2h"),
        ticks_3h = take_anyif(ticks, win == "3h"), ticks_4h = take_anyif(ticks, win == "4h"),
        open_1h  = take_anyif(open,  win == "1h"), open_2h  = take_anyif(open,  win == "2h"),
        open_3h  = take_anyif(open,  win == "3h"), open_4h  = take_anyif(open,  win == "4h"),
        high_1h  = take_anyif(high,  win == "1h"), high_2h  = take_anyif(high,  win == "2h"),
        high_3h  = take_anyif(high,  win == "3h"), high_4h  = take_anyif(high,  win == "4h"),
        low_1h   = take_anyif(low,   win == "1h"), low_2h   = take_anyif(low,   win == "2h"),
        low_3h   = take_anyif(low,   win == "3h"), low_4h   = take_anyif(low,   win == "4h"),
        close_1h = take_anyif(close, win == "1h"), close_2h = take_anyif(close, win == "2h"),
        close_3h = take_anyif(close, win == "3h"), close_4h = take_anyif(close, win == "4h"),
        vwap_1h  = take_anyif(vwap,  win == "1h"), vwap_2h  = take_anyif(vwap,  win == "2h"),
        vwap_3h  = take_anyif(vwap,  win == "3h"), vwap_4h  = take_anyif(vwap,  win == "4h"),
        total_volume_1h = take_anyif(total_volume, win == "1h"),
        total_volume_2h = take_anyif(total_volume, win == "2h"),
        total_volume_3h = take_anyif(total_volume, win == "3h"),
        total_volume_4h = take_anyif(total_volume, win == "4h"),
        realized_vol_bps_1h = take_anyif(realized_vol_bps, win == "1h"),
        realized_vol_bps_2h = take_anyif(realized_vol_bps, win == "2h"),
        realized_vol_bps_3h = take_anyif(realized_vol_bps, win == "3h"),
        realized_vol_bps_4h = take_anyif(realized_vol_bps, win == "4h"),
        avg_spread_bps_1h  = take_anyif(avg_spread_bps,  win == "1h"),
        avg_spread_bps_2h  = take_anyif(avg_spread_bps,  win == "2h"),
        avg_spread_bps_3h  = take_anyif(avg_spread_bps,  win == "3h"),
        avg_spread_bps_4h  = take_anyif(avg_spread_bps,  win == "4h"),
        ofi_imbalance_1h   = take_anyif(ofi_imbalance,   win == "1h"),
        ofi_imbalance_2h   = take_anyif(ofi_imbalance,   win == "2h"),
        ofi_imbalance_3h   = take_anyif(ofi_imbalance,   win == "3h"),
        ofi_imbalance_4h   = take_anyif(ofi_imbalance,   win == "4h")
      by symbolId, as_of;
```


### Stage 4 — nested objects (the ladder, as an example)

The volume-at-price ladder is a `dynamic` array per window. Build it on the same
`JOINED` fan-out, bin mid into `nbins` price buckets, and carry POC. Value-area
(70 %) and high-volume nodes are a light pass over this already-materialised
ladder — see *Design notes*.

```kql
let LADDER =
    JOINED
    | summarize lo = min(mid), hi = max(mid),
                pts = make_list(pack("p", mid, "v", volume))
      by symbolId, as_of, win
    | extend nbins = 40, binw = iif(hi > lo, (hi - lo) / nbins, real(null))
    | mv-expand pts
    | extend idx = iif(isnull(binw), 0,
                       toint(floor((todouble(pts.p) - lo) / binw)))
    | extend idx = iif(idx >= nbins, nbins - 1, iif(idx < 0, 0, idx))
    | extend center = iif(isnull(binw), lo, lo + (idx + 0.5) * binw)
    | summarize vol = sum(todouble(pts.v)), cnt = count(), center = any(center)
      by symbolId, as_of, win, idx
    | order by idx asc
    | summarize ladder = make_list(pack("price", center, "volume", vol, "ticks", cnt)),
                poc    = arg_max(center, vol)
      by symbolId, as_of, win
    // spread the four ladders into vap_1h … vap_4h (nested dynamic columns)
    | summarize vap_1h = take_anyif(ladder, win == "1h"), vap_2h = take_anyif(ladder, win == "2h"),
                vap_3h = take_anyif(ladder, win == "3h"), vap_4h = take_anyif(ladder, win == "4h"),
                poc_1h = take_anyif(poc,    win == "1h"), poc_2h = take_anyif(poc,    win == "2h"),
                poc_3h = take_anyif(poc,    win == "3h"), poc_4h = take_anyif(poc,    win == "4h")
      by symbolId, as_of;
```

`dist_*` (return-distribution histogram) follows the identical shape on `ret`
instead of `mid`; `ofi_series_*`, `spread_series_*` likewise as `make_list` of
`pack("t", timestamp, "v", …)`. Any per-tick series that `analytics.py` currently
resamples can be stored the same way — the ladder is the template.

### Stage 5 — assemble and materialise

```kql
// The stored function body:
let Metrics5m() {
    SCALARS
    | join kind=inner (LADDER) on symbolId, as_of
    // ---- derived columns (cheap; functions of the 40 scalars) ----
    | extend change_pct_1h = iif(open_1h > 0, (close_1h - open_1h) / open_1h * 100.0, real(null))
    | extend change_pct_2h = iif(open_2h > 0, (close_2h - open_2h) / open_2h * 100.0, real(null))
    | extend change_pct_3h = iif(open_3h > 0, (close_3h - open_3h) / open_3h * 100.0, real(null))
    | extend change_pct_4h = iif(open_4h > 0, (close_4h - open_4h) / open_4h * 100.0, real(null))
    | project symbolId, as_of,
              ticks_1h, ticks_2h, ticks_3h, ticks_4h,
              open_1h, open_2h, open_3h, open_4h,
              high_1h, high_2h, high_3h, high_4h,
              low_1h, low_2h, low_3h, low_4h,
              close_1h, close_2h, close_3h, close_4h,
              vwap_1h, vwap_2h, vwap_3h, vwap_4h,
              total_volume_1h, total_volume_2h, total_volume_3h, total_volume_4h,
              realized_vol_bps_1h, realized_vol_bps_2h, realized_vol_bps_3h, realized_vol_bps_4h,
              avg_spread_bps_1h, avg_spread_bps_2h, avg_spread_bps_3h, avg_spread_bps_4h,
              ofi_imbalance_1h, ofi_imbalance_2h, ofi_imbalance_3h, ofi_imbalance_4h,
              change_pct_1h, change_pct_2h, change_pct_3h, change_pct_4h,
              vap_1h, vap_2h, vap_3h, vap_4h,
              poc_1h, poc_2h, poc_3h, poc_4h
    ;
};

// Refresh: replace the recent slice, keyed by (symbolId, as_of).
// Run on the cache cadence (CACHE_REFRESH_MINUTES) or backfill with a wider range.
.set-or-append metrics_5m <|
    Metrics5m()
    | where as_of >= ago(4h);
```

**Dedup.** Every cycle recomputes the same `(symbolId, as_of)` stops, so the table
accumulates versions. Either
- `.set-or-append` only the newest slice and let the reader keep the latest per
  `as_of`, or
- expose a deduplicating view the app reads:
  ```kql
  .create function Metrics5mLatest() {
      metrics_5m
      | summarize arg_max(ingested_at, *) by symbolId, as_of
  }
  ```
  (add an `ingested_at` column in Stage 5 so "latest" is well-defined).

A **materialized view** over `agg_dom` is *not* suitable here: the as-of self-join
and `serialize`/window steps are outside the materialized-view operator subset. Use
a scheduled refresh (Fabric notebook / eventstream / update policy) that runs
`Metrics5m()` and appends.


---

## How the app consumes it (Redis push)

No code change is required to *produce* this — but the read path flips from "cache
raw ticks, recompute" to "cache the finished metrics, look up":

1. **Refresh job** (`app/refresh.py`, on `CACHE_REFRESH_MINUTES`): run
   `Metrics5m()` and write the result per symbol into Redis, replacing today's
   `axioma:ticks:<symbol>` Parquet blob with an `axioma:metrics:<symbol>` blob
   (49 `as_of` rows × ~60 columns). Same TTL (`REDIS_TTL_SECONDS=450`), same
   clock cadence (5-minute slots, 15 s past the mark). A cache miss still falls back to running `Metrics5m()` directly,
   preserving the existing "KQL failure sets `connected:false`, cache failure does
   not" contract in `app/service.py`.
2. **`/api/analytics`** (`app/service.py`): read the cached `metrics_5m` frame for
   the symbol, pick the requested `as_of` row and the requested window column set
   (`_1h/_2h/_3h/_4h`). The nested `vap_*` ladder is already in the blob, so the
   volume-profile panel needs **no** KQL call and no re-binning.
3. **Window control** (`app/static/index.html`): the 5–240 min slider collapses to
   the four fixed choices 60/120/180/240. `window_min_minutes` / `window_max_minutes`
   and the `/api/health` echoes stay the single source of the offered set;
   `metrics_5m` always ships all four. The point-in-time field is unchanged (5-min
   grid, 4 h).

This keeps the existing guarantee intact: a page load never triggers a KQL scan,
and the widest selectable combination (4 h timeline + 4 h window = 8 h) is exactly
what the table covers.

---

## Design notes & limitations

- **`evaluate pivot` avoided for the 40 columns.** Its output schema is
  data-derived and the plugin docs warn against it for automation. The app reads
  these columns **by name**, so we pin them with explicit `take_anyif` aggregation.
  `evaluate pivot(win, take_any(), …)` is the terse alternative if a stable
  `OutputSchema` is ever declared.
- **Value area (70 %) and high-volume nodes** are a sequential greedy expansion
  over the ladder — awkward in pure KQL. The ladder is fully materialised in
  `vap_*`, so compute these from the cached array in memory (a sort over ~40
  bins), which is **not** a table re-scan. POC is already in KQL (`arg_max`). If
  you insist on doing it in KQL, it needs an `mv-apply` running-accumulation pass
  over the price-sorted ladder.
- **Mirrored / majority-crossed book.** `mid` and `abs(spread)` are swap-invariant,
  so this query does not need the per-frame mirror repair `frames.from_ticks` does;
  the directional signal stays the pipeline's own `imbalance`, which is
  orientation-correct. A genuinely one-off crossed tick still contributes to
  spread/vol — if that matters, add `| where best_ask > best_bid` in Stage 1 to
  match the strict drop.
- **Realised vol** uses simple per-tick returns and `Σret²/duration_s`, identical
  to `analytics.summary`. Annualisation (`× sqrt(252·24·3600)`) is a multiply and
  is applied on read, not stored.
- **`summarize ... by symbolId, as_of, win` then re-`summarize`.** The second
  summarize assumes one row per `(symbolId, as_of, win)` — true here because each
  group is a distinct window. If a symbol has zero ticks in a window, that
  `(symbolId, as_of, win)` group simply produces no row and its `*_Xh` column is
  null — the same "no usable rows" case the app already surfaces.
- **Keep in sync.** `timeline_minutes`, `timeline_step_min`, the four window
  minutes, `VOLUME_SCALE`, and the `mid`/`volume`/`signed_volume` definitions are
  duplicated between this KQL and `app/frames.py` / `app/config.py`. Change both
  sides together.

