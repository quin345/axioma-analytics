# Changelog

Notable changes to Axioma Analytics, newest first.

---
---

## 2026-10-12 - Drawdown card replaced by price change from a reference point

Scope: the *Drawdown* card read the `mid` column and divided it by its own
running peak (`mid.cummax()`), so every reading was forced to `<= 0` and the
whole panel described loss from a high. It now reports a neutral percent change
from one fixed reference - the first tick of the window.

### Backend
- [x] `app/analytics.py`: `drawdown()` replaced by `price_change()`. The
      reference is `mid.iloc[0]` for the whole window, so the path starts at 0%
      and runs either side of it; the baseline never moves, which makes two
      windows comparable. Payload: `series[].pct`, `reference_ts`,
      `reference_mid`, `change_pct`, `min_pct`/`max_pct` with the timestamp of
      each extreme.
- [x] A zero/missing opening mid yields `None`s instead of inf, so
      `json.dumps(..., allow_nan=False)` still holds on a degenerate frame.
- [x] `build_report` emits `"price_change"`; `_dd_episode()` (peak/trough/
      recovery) deleted with the metric it served.
- [x] `price_change.change_pct` is asserted equal to `summary.change_pct` - the
      same two numbers, derived from the same `mid` column.

### Frontend
- [x] `app/static/index.html`: card `dd` -> `chg`, canvas `cDD` -> `cChg`, note
      `dd-note` -> `chg-note`, title "Drawdown" -> "Price change vs. window start".
- [x] `app/static/app.js`: the line chart now fills to the origin (`fill:
      {target: "origin"}`) rather than down to a hard `y.max: 0`, and uses the
      neutral accent blue instead of the loss-red; the note reads
      "Change since window start +x.xx% * reference <time>".
- [x] `DETAILS.chg` replaces `DETAILS.dd`: the prose states the fixed-baseline
      rule, and the rows are reference price/time, change since reference, and
      the window's high/low with their timestamps. No episode timeline.

### Docs & tests
- [x] `tests/test_analytics.py`: `test_drawdown_is_non_positive` replaced by
      `test_price_change_is_anchored_on_the_window_start` (first point 0%, last
      equals the reported change, values can go below 0), plus an empty-frame
      case and a zero-reference case that pins the no-NaN guard.
- [x] `README.md`, `docs/metrics_5m.md`, `landing.html`, `unavailable.html` and
      the two `styles.css` comments reworded off drawdown; `tests/test_landing.py`
      now expects "price change" in the explainer.

### Verification
- [x] `pytest` - 394 passed
- [x] `node --check app/static/app.js` clean
- [x] Live 3000-tick run: `change_pct -0.1730` matches `summary.change_pct`,
      `reference_mid 1.08500002`, `series[0].pct == 0.0`, payload strict-JSON safe

**Notes:**
- The card keeps `span2`. The episode timeline that originally justified the
  wide cell is gone, but the reference row (price beside a timestamp) and the
  high/low rows still read better across two thirds of the row.

---

## 2026-10-11 - POC marks the busiest price bin, not whatever `poc_*` holds (dev)

Scope: the volume-profile panel marked a bar that was not the tallest one, and
the *Point of control* row showed a value that was not the busiest price. Two
argmaxes over the same bins were allowed to disagree: `metrics_5m`'s `poc_*`
column and the ladder the chart actually draws.

### Backend
- [x] `app/metrics.py`: `volume_profile` takes the POC from the **busiest bin of
      the materialised ladder** (`argmax(volume)`, returning that bin's price)
      instead of trusting the stored `poc_*` as a price. The stored point is now
      only the fallback for a ladder where no bin traded (total volume 0) - a
      value that is not the busiest bin's price can no longer be marked up as it.
      The value area still runs off the same index through `analytics.value_area`.

### Docs
- [x] `docs/metrics_5m.md` Stage 4: `poc = arg_max(vol, center)` - the criterion
      comes first, so the old `arg_max(center, vol)` was returning the bin's
      **volume** (max price -> its volume), i.e. precisely the "POC is not the
      busiest price bin" symptom. Commented in the KQL so the order is not
      "tidied" back.
- [x] `docs/metrics_5m.md` Stage 3: the same argument-order slip in `open`/`close`
      - `arg_min(mid, timestamp)` / `arg_max(mid, timestamp)` return a *datetime*
      in a `real` column; corrected to `arg_min(timestamp, mid)` /
      `arg_max(timestamp, mid)` and the definition table with them.
- [x] Stage 4 prose and the *Design notes* bullet now state that `poc_*` is the
      centre price of the busiest bin and that the read path re-derives it from
      the ladder, so the two can never disagree about the marked bar.

### Tests
- [x] `tests/test_metrics.py`: `test_the_table_s_own_poc_is_used_and_falls_back_to_the_busiest_bin`
      replaced by `test_the_poc_is_the_busiest_bin_whatever_the_stored_column_says`
      - a stored point naming the busiest bin, a different bin, a *volume* (the
      swapped-`arg_max` output) and `None` all resolve to the busiest price, and
      that price is asserted to be the bin with the largest volume.
- [x] `tests/test_metrics.py`: `test_a_ladder_that_traded_nothing_falls_back_to_the_stored_point`
      pins the one case where the stored point is still what gets marked.

### Verification
- [x] `pytest` - 386 passed
- [x] End-to-end through `metrics.report` with a stored `poc_1h = 20.0` (a volume)
      on a ladder whose busiest bin is `1.0855 vol 20`: the payload reports
      `poc = 1.0855`, the marked bar is the tallest, bins still run
      price-descending.
- [x] `node --check app/static/app.js` clean (UI untouched)

**Notes:**
- The UI marks the POC bar with `b.price === volume_profile.poc`, so the reader
  must hand back exactly a bin price - it does, because the value comes straight
  from the ladder bin rather than from the stored column.
- The `metrics_5m` table still needs rebuilding for `poc_*` (and `open_*`/
  `close_*`) to be correct in the table itself; the read path no longer depends
  on it for the chart marker.

---


## 2026-10-11 - Fixed 4-window control aligned to `metrics_5m` (dev)

Scope: reduce the aggregate *Window* control from the 5–240 min slider to the
four fixed trailing windows the precomputed `metrics_5m` KQL table ships
(1 h / 2 h / 3 h / 4 h), keeping `/api/health` as the single source of the
offered set. The pandas read path is unchanged — the `metrics_5m` table is
still being built, so the app keeps recomputing in memory; only the *control*
and its contract move now.

### Backend
- [x] `app/config.py`: `WINDOW_MIN_MINUTES`/`WINDOW_MAX_MINUTES` (5/240) are
      replaced as the driver of the control by `WINDOW_CHOICES_MINUTES`
      (`60,120,180,240`), parsed/de-duped/sorted by a new `_env_int_list`; a
      `window_choices` property caps the choices at the cache and never returns
      empty. The legacy min/max stay (now 60/240) so an older cached health
      payload still renders a sensible control.
- [x] `app/service.py`: `Status.window_choices_minutes` echoes `window_choices`
- [x] `app/main.py`: `/api/health` publishes `window_choices_minutes`

### UI
- [x] `app/static/app.js`: `fillWindows()` builds `#windowMinutes` from
      `window_choices_minutes` (four fixed options), falling back to the legacy
      min..max slider only when the field is absent; defaults to 1 h
- [x] `app/static/index.html`: *Window* label/title now "1, 2, 3 or 4 hours"

### Docs & tests
- [x] `.env.example`, `README.md`, this file updated
- [x] `tests/test_config.py`: window choices default/override/normalise,
      cache-capping and never-empty; isolation set covers the new env keys
- [x] `tests/test_status.py`, `tests/test_analytics_api.py`: health echoes
      `window_choices_minutes == [60,120,180,240]`

### Verification
- [x] `pytest` - 327 passed; `node --check app/static/app.js` clean

**Notes:**
- The `metrics_5m` table (per `docs/metrics_5m.md`) is the source-of-truth for
  the four windows; the control now offers exactly what it precomputes.
- When the table lands, the read path (`app/refresh.py` / `app/service.py`)
  flips from "cache raw ticks, recompute" to "cache the finished metrics, look
  up" — that is a separate change and needs the table first.

---

## Unreleased (dev)
- **Open:** Calendar is a read-only view of report data; a true task list
  (add/edit, persisted) is undecided — needs a storage choice (localStorage vs
  server endpoint + store).

---

## 2026-10-09 — Point-in-time field, aggregate window, 8 h cache, descending ladder
- Backend: cache lookback default 4 → 8 h; new `TIMELINE_*`/`WINDOW_*` config and a
  `max_window_minutes` ceiling capped at the cache.
- Renamed `clamp_lookback` → `clamp_window`; loads take an `end_time` that ends the
  slice at the chosen point (future clamps to newest, past leaves nothing, bad input
  raises). Empty-window errors name the point.
- API: `/api/analytics` takes `window_minutes` (`lookback_minutes` kept as deprecated
  alias) and `as_of`, both echoed in `meta`; `/api/health` publishes the window range.
- Volume-profile bins and nodes now returned in descending price order (POC/value-area
  still computed ascending).
- UI: header badge → "Last update" (feed-based); cadence strip mirrors the 30-min
  refresh; "Next refresh" snaps to the wall-clock grid; point-in-time control is a
  `<input type="time">` on the 5-min grid; "Lookback" relabelled "Window" built from
  `/api/health`; volume-at-price runs highest-price-first.
- Tests: `pytest` 314 passed.

---

## 2026-10-08 — Clock-slot refresh, sliding 4-hour window, development page
- Refresh cycle sleeps to the next wall-clock mark (:00/:30) instead of a fixed
  interval, so restarts don't drift and `*/30` cron aligns; exact landings wait a
  full interval.
- `refresh_cache()` slides the window per symbol (read rows newer than the cached
  newest, merge/dedupe, purge older than the lookback anchored on the newest row);
  summary gains `purged`.
- New self-contained `/unavailable` "under development" page served at `/`
  (dashboard moved to `/dashboard`; `MAINTENANCE_MODE` still 503s `maintenance.html`).
- Tests: `pytest` 285 passed; new `tests/test_maintenance.py` plus script-sanity
  checks guarding `app.js` helper/function renames.

---

## 2026-10-08 — Keep the 4-hour window fresh: refresh every 30 minutes
- Added `CACHE_REFRESH_MINUTES` (30; 0 disables) and an in-process worker-thread cycle
  that refreshes only cached symbols (+ default) and survives failed cycles.
- `cache.keys()` lists cached entries via `KEYS` (this Redis Enterprise returns empty
  pages for `SCAN`); `scripts/refresh_cache.py` offers a cron/systemd alternative.
- `/api/health` publishes `cache_refresh_minutes`, `cache_refreshed_at`,
  `cached_symbols`; UI tooltip and banner show the cadence.
- Tests: `pytest` 248 passed; new `tests/test_refresh.py`, `test_refresh_script.py`.

---

## 2026-10-08 — One source (KQL) + Redis cache, fixed 4-hour window
- Redis cache (Entra identity, no access key) with catalogue + per-symbol Parquet
  blobs; cache is an optimisation — an outage falls through to KQL.
- Removed the SQL read path: deleted `app/db.py`/`app/auth.py`, dropped `pyodbc`,
  added `redis` + `pyarrow`; `DataSourceError` moved to `app/errors.py`.
- Service reads KQL and serves from Redis under a clamped fixed window; `/api/health`
  publishes `lookback_minutes`, `cache_connected`, `cache_error`.
- UI: "Lookback" number input replaced by a "Duration" `<select>` built from
  `health.lookback_minutes`.
- Tests: `pytest` 222 passed.

---

## 2026-10-08 — Aggregate rows move to Fabric KQL (Eventhouse)
- New `app/kql.py` (Kusto client, declarative-parameter queries, `fetch_ticks` with
  anchored lookback); auth mirrors SQL (managed identity → service principal → `az login`).
- Config split into `kql_host`/`kql_database`/`kql_table` (defaults `ctrader_dom`/`agg_dom`);
  both endpoints now required for `is_configured`; `DEFAULT_SYMBOL` and UI ticker → `XAUUSD`.
- `TICK_COLUMNS` moved into `app/frames.py`; `db.py` trimmed to the SQL instrument dimension.
- Tests: `pytest` 191 passed; verified live (XAUUSD id 41, 16.8k ticks/hour). Casts
  `symbolId == tolong(sym)` since it's a `long`.

---

## 2026-10-08 — Terminology: per-tick metrics, not snapshots
- Corrected `agg_dom` naming to derived per-tick order-book metrics (from
  `dom_stream_raw` → `dom_book_flat`); `from_snapshot` → `from_ticks`,
  `SNAPSHOT_COLUMNS` → `METRIC_COLUMNS`, `latest_snapshot` → `latest_tick`.
- README, landing/dashboard copy, `.env.example` and tests updated; all green.

---

## 2026-10-05 — Dashboard cards, calendar, symbolId
- All 15 cards are click/tap/keyboard disclosures with a detail body and rotating
  chevron; grouped into Overview / Microstructure / Liquidity / Data.
- Month calendar built from the OHLCV bars (day sized by tick count, coloured by
  change; prev/next/Today; defaults to last day with data).
- Removed `symbolId` from the UI; wider type scale, 420px min cards, footnotes so no
  chart needs expanding to read its headline.
- Tests: `pytest` 133 passed; `node --check` clean.

---

## 2026-10-05 — Favicon
- Added `favicon.svg` plus a full raster set (`.ico`, `icon-32/180/512.png`) with link
  and `theme-color` tags in `index.html`.
- New dependency-free `scripts/make_favicon.py`; fixed the alpha divisor (was zeroing
  alpha at 512px) and made output byte-reproducible.

---

## 2026-10-05 — Drawdown card overflow
- `dd` card now spans a full grid row; added `.card.span3` with responsive collapse
  (3 → 2 at 1400px → 1 at 900px).
- Removed `white-space:nowrap` on `.dl dd` and pinned `.dl dt` so wide values wrap
  inside their own cell; raised `.dl` min column 230 → 300px.

---

## 2026-10-05 — Overview block re-layout
- Price card takes the full row; `dd` at 2/3, `hist` at 1/3 below it; markup reordered.
- Fixed the `span3` breakpoint to 1339px (was 1400px) to match the 3-column minimum.

---

## 2026-10-05 — Data freshness
- Added `meta.latest_tick` to `/api/analytics`; freshness strip (cadence, latest tick,
  next refresh, row count) and a header age badge that goes amber past the cadence.
- `REFRESH_MINUTES = 45` drives the strip; fixed the "every hour" wording; `set()` no
  longer clobbers `className`.
