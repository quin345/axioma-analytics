# Tasks

Working checklist for Axioma Analytics. One file, newest work at the top.
Tick a box when the item is done and the change is verified, not just written.

Conventions: `- [ ]` open, `- [x]` done, `- [!]` blocked. Notes go under
**Notes:** so the checklist itself stays scannable.

---

## 2026-10-08 - One source (KQL) + Redis cache, fixed 4-hour window

### Backend
- [x] `app/cache.py`: Redis client on the Entra identity (no access key),
      catalogue + per-symbol Parquet blobs, TTL from `REDIS_TTL_SECONDS`
- [x] `app/errors.py`: `DataSourceError` moved out of `db` so the API layer keeps
      one error type after the SQL path is gone
- [x] `app/config.py`: KQL-only settings; Redis host/port/SSL/timeout,
      `REDIS_TTL_SECONDS`, `CACHE_LOOKBACK_HOURS` (`cache_lookback_minutes`)
- [x] `app/service.py`: reads KQL, serves from Redis, `clamp_lookback` narrows the
      fixed window, cache outage falls through to KQL (`connected` follows KQL only)
- [x] `app/main.py`: `/api/health` publishes `lookback_minutes`,
      `cache_connected`, `cache_error`
- [x] `app/db.py` and `app/auth.py` deleted; `requirements.txt` drops `pyodbc`
      and adds `redis` + `pyarrow`
- [x] `run.py` prints the KQL source and the cache state, not the SQL endpoints

### UI
- [x] "Lookback (minutes)" number input replaced by a "Duration" `<select>`
- [x] Options are built from `health.lookback_minutes`, so no choice can widen
      the cached window or force a second KQL read
- [x] `analyse()` sends the selected `lookback_minutes`; copy says duration, not
      lookback; the health badge tooltip names the window and cache state

### Verification
- [x] `pytest` - 222 passed
- [x] `tests/test_service.py`: cold read -> one KQL call, warm reads cached,
      per-symbol keys, cache-outage fallthrough, empty window raises, `reset()`
- [x] `tests/test_status.py`: the KQL probe gates health, the cache is reported
      but never gating
- [x] `tests/test_config.py`: KQL-only defaults, Redis/Entra/MSI assertions
- [x] Docs: `.env.example`, `README.md`, `scripts/grant_workspace_access.py`
      describe the KQL + Redis path (and the Redis data-access grant)

**Notes:**
- The cache is an optimisation, never a dependency: a Redis outage degrades to
  one KQL query per request, and only a KQL failure sets `connected=false`.
- The window is fixed server-side on purpose. The UI derives its options from
  the published window, and `/api/analytics` clamps rather than rejects, so a
  stale tab or bookmarked URL keeps working instead of returning a 422.

---

## 2026-10-08 - Terminology: per-tick metrics, not snapshots

### Correction
- [x] `agg_dom` holds **derived per-tick order-book metrics**, reconstructed from
      `dom_stream_raw` -> `dom_book_flat`, not point-in-time state snapshots
- [x] `frames.from_snapshot` -> `frames.from_ticks`
- [x] `kql.SNAPSHOT_COLUMNS` -> `kql.METRIC_COLUMNS`
- [x] `kql.snapshot_stats` -> `kql.tick_stats`
- [x] `/api/health` field `latest_snapshot` -> `latest_tick`
- [x] README, landing page, dashboard copy, `.env.example` and tests updated
- [x] `pytest` - all green

**Notes:**
- The source lineage is now stated wherever the table is described: the raw DOM
  event feed (`dom_stream_raw`) is reconstructed into a full order book
  (`dom_book_flat`), and `agg_dom` carries the per-tick metrics derived from
  that reconstruction. Nothing about the row shape or the transport changed -
  this is a wording correction, not a schema move.

---

## 2026-10-08 - Aggregate rows move to Fabric KQL (Eventhouse)

### Transport
- [x] `app/kql.py`: Kusto client, `table_ref`, declarative-parameter queries,
      `server_time`, `tick_stats`, `symbol_tick_counts`, `fetch_ticks`
- [x] Anchored `lookback_minutes` window ported from the SQL anchor query
- [x] Auth mirrors SQL: managed identity -> service principal -> `az login`,
      with `azure-kusto-data` refreshing tokens internally
- [x] `app/db.py` trimmed to the SQL instrument dimension only
- [x] `app/frames.py` owns `TICK_COLUMNS` (no longer imported from `db`)
- [x] `app/service.py`: ticks/counts/probe via `app.kql`, catalogue via SQL

### Configuration
- [x] `kql_host` / `kql_database` / `kql_table` (`KQL_ENDPOINT_PROD`, defaults
      `ctrader_dom` / `agg_dom`); `SNAPSHOT_TABLE` removed
- [x] `is_configured` now requires **both** endpoints; `kql_configured` added
- [x] `DEFAULT_SYMBOL` default and the pinned UI ticker both switched to `XAUUSD`
- [x] `requirements.txt`: `azure-identity`, `azure-kusto-data`
- [x] `.env.example` / `README.md` document the KQL/SQL split

### Verification
- [x] `pytest` - 191 passed
- [x] `tests/test_config.py`: KQL defaults, legacy `KQL_ENDPOINT` fallback, both
      endpoints required
- [x] `tests/test_kql.py`: query text, declarative parameters, row capping,
      lookback anchoring, credential selection
- [x] `tests/test_status.py`: the two-source health probe
- [x] `tests/test_selectors.py` pins the health probe so the endpoint tests stay
      data-source-free (they previously borrowed live connectivity)
- [x] Live probe after the workspace grant: `server_time`, `count()/max(timestamp)`,
      `getschema`, `symbol_tick_counts` and `fetch_ticks` all return 200s
- [x] Schema read from the live table: `symbolId` is a `long`, so the queries now
      cast the parameter (`symbolId == tolong(sym)`) instead of the column
- [x] End-to-end: `/api/symbols` defaults to `XAUUSD` (id 41) and
      `/api/analytics` returns a full bundle over live rows (16.8k ticks/hour,
      1m OHLCV, microstructure)

**Notes:**
- Access is now granted: the identity reads `ctrader_dom` and the live probes
  return 200s. A **403** here means the workspace role was lost; add the identity
  back as Contributor on the workspace or as a Database viewer on the Eventhouse
  database.
- `symbolId` is a `long` (read from the live `getschema`), so the queries cast the
  parameter rather than the column. A blank or non-numeric symbol therefore
  selects no rows instead of failing, which keeps the unfiltered call working.
- `fetch_ticks` uses `top {limit} by timestamp asc` rather than
  `sort ... | take`, so the row cap is applied to the *oldest* rows of the
  window deterministically.
- The instrument catalogue merges the SQL dimension with the **KQL** counts, so
  the dashboard needs both endpoints: a working KQL read with an unreadable
  dimension gives ids with no tickers, and an unreadable KQL endpoint leaves the
  selectors empty. `/api/health` reports each side separately (`row_count` for
  KQL, `dimension_rows` for SQL) and stays 200 with `connected=false` plus a
  per-source hint, so the dashboard shows the unavailable state instead of
  failing on the first request.
- `status()` originally probed KQL only, which reported `connected=true` while
  every selector failed. It now probes the dimension too - that is what the
  `SELECT COUNT_BIG(*)` against `symbols_icmarkets` in `/api/health` is for.
- The gold Delta tables can briefly answer
  `Failed to complete the command because the underlying location does not exist`
  (`24596`) while the SQL analytics endpoint's metadata points at parquet files
  that were rewritten. It cleared on its own during this session; the legacy
  `agg_dom_book_snapshot` fails identically, so it is unrelated to the KQL move.
- The mirroring check in `frames.from_ticks` is exercised by the live data, not
  just by tests: 100% of the rows in a one-hour XAUUSD window have
  `best_bid > best_ask`, `best_bid` sits near the pipeline's `vwap_ask` and
  `best_ask` near its `vwap_bid`, and the pair is ~19x wider than the pipeline's
  own `vwap_spread` (36.6 vs 1.94). The swap is verified against the raw rows
  column by column (frame `bid` = raw `best_ask`, frame `ask` = raw `best_bid`,
  identical mid). Raising the pipeline's `best_*` orientation with its owner is
  still open - nothing in the app can fix the source columns.
- Several rows share one millisecond `timestamp` (15.5% of the window, up to 12
  rows), separated only by `eventSeq`/`eventId`; they are distinct events and are
  kept, which is why tick counts are ~19% higher than distinct timestamps.

---

## 2026-10-05 - Dashboard cards, calendar, symbolId

### Expandable cards
- [x] Make every card a click/tap/keyboard disclosure (`data-card`, `aria-expanded`)
- [x] Do not collapse when a click lands inside the expanded body
- [x] Rotating chevron on the card title; open state uses the accent border
- [x] Detail body per card: prose explaining the metric + a label/value table
- [x] Cover all 15 cards: price, ofi, depth, profile, hist, vol, dd, spread,
      iarr, hour, acf, large, ticks, coverage, calendar

### Calendar
- [x] Month grid built from the same OHLCV bars as the price chart
- [x] Day cell sized by tick count, coloured by that day's change
- [x] Prev/next month and Today (Today = most recent day that has data)
- [x] Click a day to load its detail
- [x] Default to the last day with data, not to today

### Readability and placement
- [x] Group cards into Overview / Microstructure / Liquidity / Data sections
- [x] Widen type scale, card min-width 420px, taller charts, content max-width
- [x] Add missing footnotes so no chart needs expanding to read its headline

### Remove symbolId from the UI
- [x] Price-chart tag shows name and asset class only
- [x] Symbol picker label drops the `(8)` suffix; id stays the option value

### Verification
- [x] `node --check app/static/app.js` clean
- [x] `pytest` - 133 passed
- [x] Live run against the gold endpoint; all 15 detail builders render clean
- [x] Calendar aggregation checked across a month boundary
- [x] Empty-report path does not throw in any builder (fixed `dl(null)`)

**Notes:**
- `tests/test_config.py::test_plain_connection_has_no_authentication_when_unset`
  fails in this checkout and is **pre-existing**. It reads credentials from the
  local `.env`, so it fails whenever those are set. Untouched by this work.
- A 24h lookback yields one calendar day. Use `lookback_minutes=129600` (the field
  max) for a populated month grid.

---

## 2026-10-05 - Favicon

### Assets
- [x] `favicon.svg` - the logo mark as vector, identical geometry to the header
- [x] `favicon.ico` - 16/32/48/64/128/256 PNG-compressed frames
- [x] `icon-32.png`, `icon-180.png`, `icon-512.png` for bookmarks and iOS
- [x] Link tags in `index.html`: svg, ico, png, apple-touch-icon, mask-icon
- [x] `theme-color` set to the app background

### Generator
- [x] `scripts/make_favicon.py` rasterises the mark with no third-party deps
- [x] Fixed alpha divisor: was `size * SS * SS` (whole canvas) instead of
      `SS * SS` (samples per pixel), which zeroed every alpha at 512px
- [x] Output is byte-for-byte reproducible (fixed zlib level)

### Verification
- [x] PNG signature, chunk CRCs and scanline sizes valid at every size
- [x] ICO header and all 6 directory entries correct; 256 stored as 0
- [x] Rendered ink matches the logo (diamond outline, two ascending bars)
- [x] Served by the app with correct MIME types; `pytest` still 133 passed

**Notes:**
- Rasters are antialiased by 4x4 supersampling; SVG is preferred where
  supported, so the ico/png set is only a fallback.
- Re-run `python scripts/make_favicon.py` after changing the header logo.

---

## 2026-10-05 - Drawdown card overflow

- [x] `dd` card spans a full grid row (`span3`) instead of one column
- [x] Added `.card.span3` with responsive collapse (3 -> 2 at 1400px, -> 1 at 900px)
- [x] `.dl dd` no longer `nowrap`: a wide value wraps inside its own cell
- [x] `.dl dt` pinned with `flex:0 0 auto`, `.dl dd` given `margin-left:auto`
      so a wrapped value still hugs the right edge
- [x] `.dl` min column 230px -> 300px to give wide values more room
- [x] Verified against live data: episode timelines are ~79 chars / ~521px
- [x] `node --check` clean; `pytest` 133 passed

**Notes:**
- Root cause was `white-space:nowrap` on `.dl dd`, not the card width alone.
  Widening alone would have hidden it; both were needed.
- Full-width row avoids an empty grid cell, since price (span2) + hist (span1)
  already fills the row above.
- Other cards benefit from the `.dl` fix too (hour-by-hour, episode timelines).

---

## 2026-10-05 - Overview block re-layout

- [x] `price` takes the full row (`span3`), so the anchor chart is full width
- [x] Row below: `dd` at 2/3 (`span2`), `hist` at 1/3
- [x] Reordered the markup so the grid flows: price, drawdown, return distribution
- [x] Fixed the `span3` breakpoint: was 1400px, correct value is 1339px
- [x] Verified card order and spans served by the app; `pytest` 133 passed

**Notes:**
- The grid keeps 3 columns down to a 1340px viewport (420px min track + 16px
  gaps + 48px padding); below that it drops to 2, so `span3` collapses first.
  At the old 1400px breakpoint, `span3` would have overflowed a 2-column grid
  between 1340 and 1400px.
- The drawdown card stays at 2/3, which keeps its episode timeline readable
  now that `.dl dd` wraps rather than spills.

---

## 2026-10-05 - Data freshness

- [x] `meta.latest_tick` added to `/api/analytics`, the max `ts` actually returned
- [x] Freshness strip under the controls: 45-minute cadence, latest tick, next
      refresh, tick row count
- [x] Header gains the last-tick timestamp plus an age badge
- [x] Age badge goes amber past the 45 minute cadence (`tickage stale`)
- [x] `REFRESH_MINUTES = 45` in app.js drives the strip and the hint banner
- [x] Fixed the stale "every hour" wording in the hint banner
- [x] Verified against live data: a `+08:00` timestamp renders as the correct
      UTC instant;50min old flags stale; empty meta does not crash
- [x] `set()` no longer clobbers className when no class is passed
- [x] `pytest` 133 passed; `node --check` clean

**Notes:**
- The timestamp is normalised with `toISOString()`, so a `+08:00` offset from
  the API still displays as UTC. Verified.
- The cadence is stated as a constant in `app.js`; there is no server-side
  config for it, so changing the real pipeline interval means editing one line.
- `latest_tick` describes the loaded window, not the whole table, so it matches
  the data on screen.

---

## Open questions

- [ ] Confirm the calendar is right: it is a read-only view of data already in
      the analytics report. If a *task* list is wanted instead (add/edit items,
      persisted), that is a separate feature and needs a decision on storage:
      browser localStorage, or a server endpoint plus a store.