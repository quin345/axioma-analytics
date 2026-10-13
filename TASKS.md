# Changelog

Notable changes to Axioma Analytics, newest first.

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
