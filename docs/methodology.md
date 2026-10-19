# Axioma Analytics — Methodology

> **Status:** normative reference. This document describes *how* the platform
> sources, normalises and computes everything it serves — the end-to-end
> methodology, from the raw DOM feed to the numbers on the dashboard. It sits
> beside two companion documents that cover narrower ground:
>
> * [`docs/dashboard_metrics.md`](dashboard_metrics.md) — the inventory of KPI
>   tiles and cards (what the UI renders, and from which block),
> * [`docs/metrics_5m.md`](metrics_5m.md) — the KQL producer contract for the
>   precomputed `metrics_5m` table (this app only *reads* that table).
>
> Where this document states a formula, the code named beside it is the
> source of truth; when the two drift, fix the drift deliberately and update
> both.

---

## 1. Purpose and scope

Axioma Analytics computes microstructure analytics over the **production
aggregate DOM feed** and serves them through a single FastAPI process with a
static dashboard in front. The methodology answers four questions:

1. **Where does the data come from, and what does a row mean?** (§3, §5)
2. **How fresh is it, and how is it kept fresh without re-scanning storage on
   every request?** (§4, §6)
3. **How is every metric computed, including its edge cases?** (§7, §8)
4. **What guarantees hold across the two serving paths, and what are the known
   limitations?** (§9, §12–§14)

Out of scope: deployment topology and DNS (see `README.md`), and the KQL that
*builds* `metrics_5m` (see `docs/metrics_5m.md`).

---

## 2. Architecture at a glance

```
DOM event feed ──▶ ctrader_dom (Fabric KQL / Eventhouse)
                    ├─ agg_dom            per-tick derived order-book metrics
                    └─ metrics_5m         precomputed window grid (ladder)
                            │  (Entra ID: managed identity → SP → az login)
                            ▼
                   app/kql.py  ── reads ──▶ Redis (Entra ID, Parquet + JSON, TTL)
                            │                     ▲
                            ▼                     │ slide every 5 min
                   app/service.py ◀──── app/refresh.py (clock-aligned cycle)
                       ▲
   symbols_metadata ───┘  local PostgreSQL (app/meta.py): broker-agnostic,
   one row per (symbolId, ctrader_account_id); the KQL facts'
   (symbolId, ctidTraderAccountId) composite key maps each
   fact row onto its metadata row (name, class, broker)
                            │
              ┌─────────────┴──────────────┐
              ▼                            ▼
      /api/analytics                 /api/metrics
      tick-derived, pandas           precomputed ladder lookup
      (app/analytics.py)             (app/metrics.py)
              └─────────────┬──────────────┘
                            ▼
                app/static/index.html + app.js
```

Two serving paths, deliberately kept disjoint:

| Path | Source | Engine | What it answers |
|---|---|---|---|
| `/api/analytics` | `agg_dom` (via the tick cache) | pandas, recomputed per request | every headline tile and chart block — one tick frame, one window |
| `/api/metrics` | `metrics_5m` (via the grid cache) | row/column lookup | the pre-binned volume-at-price ladders for the four fixed windows |

Module map:

| Concern | Module |
|---|---|
| Settings, window geometry, display zone | `app/config.py` |
| KQL access, column contracts, query discipline | `app/kql.py` |
| Broker-agnostic instrument metadata (local PostgreSQL) | `app/meta.py` |
| Tick projection to the canonical frame | `app/frames.py` |
| Tick-derived analytics | `app/analytics.py` |
| Precomputed ladder lookup | `app/metrics.py` |
| Cache client (Redis, Entra, Parquet/JSON) | `app/cache.py` |
| Orchestration: catalogue, status, window slide | `app/service.py` |
| Background refresh cycle | `app/refresh.py` |
| Asset-class taxonomy | `app/assets.py` |
| HTTP surface | `app/main.py` |

---

## 3. Data sourcing methodology

### 3.1 One source of truth

The app reads exactly **one** KQL (Eventhouse) database: `ctrader_dom`
(`KQL_DATABASE`). There is no environment switcher, no catalog discovery and
no synthetic/demo fallback. Storage internals (endpoint host, database, table
names) are never exposed to the browser — `/api/health` reports availability
and coverage only.

### 3.2 Upstream provenance

`agg_dom` rows are **derived per-tick metrics**, not point-in-time snapshots.
They are computed by the pipeline from a full order-book reconstruction:

```
dom_stream_raw ──▶ dom_book_flat ──▶ agg_dom
(raw DOM events)   (reconstructed     (derived per-tick metrics:
                    full order book)  top-of-book, resting size,
                                      imbalance, spreads)
```

This matters methodologically: a row is the state of the book *as reconciled
at that tick*, so every downstream statistic (spread, imbalance, resting size)
inherits the reconstruction's fidelity, not merely the exchange's last print.

### 3.3 Tables and grains

| Table | Grain | Role in the methodology | Env var |
|---|---|---|---|
| `dom_stream_raw` | raw DOM events | ingested, **not queried** by the app | — |
| `dom_book_flat` | reconstructed book | ingested, **not queried** | — |
| `agg_dom` | one row per `(symbolId, ctidTraderAccountId, tick)` | the analytics read (both for live computation and for the pipeline twin of the KQL) | `KQL_TABLE` |
| `metrics_5m` | one row per `(symbolId, ctidTraderAccountId, as_of)` | precomputed four-window grid; supplies the volume-at-price ladder only | `METRICS_TABLE` |

Instrument metadata (ticker, category, description) is **not** in KQL: it lives
in local PostgreSQL as the `symbols_metadata` view — one row per
`(symbolId, ctrader_account_id)` (`SYMBOL_TABLE` / `PG_SYMBOLS_TABLE`). The
feed's `(symbolId, ctidTraderAccountId)` composite key is what maps a fact row
onto that row, and the row's `broker_name` makes the mapping broker-agnostic:
same symbol id, different account, different metadata.

Columns projected from `agg_dom` (`kql.METRIC_COLUMNS`): `timestamp`,
`symbolId`, `total_bid`, `total_ask`, `best_bid`, `best_ask`, `imbalance`,
`imbalance_ratio`, `vwap_bid`, `vwap_ask`, `vwap_spread`, `rel_spread`,
`rel_vwap_spread`.

`symbolId` and `ctidTraderAccountId` are both KQL `long`s; queries therefore
cast the *parameters* (`symbolId == tolong(sym)`,
`ctidTraderAccountId == tolong(acct)`), never the columns. A blank or
non-numeric parameter selects no rows instead of failing — an unknown symbol
or account degrades to "no data", not to an error.

### 3.4 Authentication

Identity is Entra ID, resolved in a fixed order — and the **same** identity
authenticates both KQL and Redis (`app/kql._credential`, `app/cache._credential`):

| Order | Mode | Condition | Property |
|---|---|---|---|
| 1 | Managed identity | `FABRIC_MANAGED_IDENTITY=true` | headless; token minted/refreshed by the SDK from the instance metadata endpoint; wins over any secret so a leftover credential cannot change the auth path |
| 2 | Service principal | `FABRIC_*` credentials present | headless; used only when managed identity is off |
| 3 | `az login` session | neither of the above | local debugging only; unsuitable for a service |

Access itself is a **Fabric workspace role** (Contributor, granted via
`scripts/grant_workspace_access.py` or the portal — Fabric workspace roles are
not Azure RBAC) plus **Redis Data Contributor** on the cache. Azure CLI
credential is the only interactive path.

### 3.5 Query discipline

* **Declarative query parameters.** Every query sends its inputs through
  `ClientRequestProperties` (`kql.query`), so query text never interpolates
  user input — the same injection posture as SQL placeholders.
* **Read-by-name projections.** Column lists (`METRIC_COLUMNS`,
  `_METRICS_PROJECTION`, `WINDOW_NESTED`) are pinned in code, never inferred
  from the result schema. A renamed or missing column **fails the read
  loudly** rather than silently returning a panel of nulls.
* **Anchored lookback.** A lookback read first resolves the newest row
  (`kql._ANCHOR`: `summarize max(timestamp)`), then reads from
  `newest − lookback`. The window is therefore *the most recent session*, not
  an arbitrary slice of history that would drift as the feed advances.
* **No `evaluate pivot`.** Its output schema is data-derived; the app reads
  windowed columns by explicit name instead (see `docs/metrics_5m.md`).

---

## 4. Cache and freshness methodology

### 4.1 Why a cache, and what it holds

KQL is expensive to re-scan, so **every** query result is cached in Redis
before a dashboard request can touch the engine. The cache is an
*optimisation*, never a source of truth: a miss (or a Redis outage) reads KQL
and repopulates.

| Entry | Key shape | Payload | TTL |
|---|---|---|---|
| Instrument catalogue | `axioma:<db>:<table>:catalogue` | JSON | `REDIS_TTL_SECONDS` (450 s) |
| Per-instrument tick window | `axioma:<db>:<table>:ticks:<symbolId>[:<account>]` (or `:all`) | Parquet bytes | same |
| Per-symbol metrics grid | `axioma:<db>:metrics:<metrics_table>:<symbol>` | Parquet bytes | same |
| Built ladder bundles | `…:metrics:report:<symbol>:<stop>` | JSON | same |

The tick key carries the resolved `ctidTraderAccountId` half of the composite
(`ticks:<symbolId>:<account>`) so two feeds sharing one id never share a
window; when the account cannot be resolved (catalogue unreachable) the entry
falls back to the bare `<symbolId>` key and reads every account for that id — a
degradation, not an error.

Frames travel as **Parquet**: timestamps and floats round-trip with dtypes
intact and nulls lossless (`cache.get_frame`/`set_frame`), and an unreadable
payload is discarded and treated as a miss rather than an error.

Redis is authenticated with Entra ID only (no access key): a token for the
`https://redis.azure.com/.default` scope is handed to redis-py through a
`CredentialProvider`, refreshed per connection, using the token's `oid` claim
as the AUTH username (`cache.EntraCredentialProvider`).

### 4.2 The fixed window and clamp semantics

The cache holds a **fixed window** of `CACHE_LOOKBACK_HOURS` (8 h default =
480 min) per symbol. Eight hours is not arbitrary — it is exactly the widest
combination the controls can request: `TIMELINE_MINUTES` (240) + the widest
aggregate window (`WINDOW_CHOICES_MINUTES` max = 240) = 480.

Request semantics follow one rule: **narrow freely, never widen**.

* `/api/analytics` clamps `window_minutes` (`service.clamp_window`) — asking
  for more returns the whole cached window instead of a `422`, so bookmarked
  URLs and stale tabs keep working.
* Choices wider than the cache are dropped from the published set
  (`Settings.window_choices`), and the control never becomes empty (the cache
  window itself is offered as the last resort).
* A client can therefore **never trigger a second, deeper KQL read** by asking
  for more history.

### 4.3 The sliding refresh cycle

Freshness is maintained by `app/refresh.py` + `service.refresh_cache`, not by
reads. The cycle:

1. **Schedules on the clock, not on process start.** Slots are wall-clock
   multiples of `CACHE_REFRESH_MINUTES` (5) counted from local midnight plus
   `CACHE_REFRESH_OFFSET_SECONDS` (15), so runs land at :00:15, :05:15, …
   Whatever time the service restarts, operators can predict when KQL work
   happens. The 15 s offset lets the upstream feed settle past the mark so the
   read never races immature data (`refresh.seconds_until_next_slot`).
2. **Runs immediately on start**, so a restart warms the cache instead of
   leaving the first visitor to pay for the scan.
3. **Refreshes the instrument catalogue** (selectors and health counts see new
   instruments and fresh tick counts), then every symbol *already in the
   cache* — and nothing else. The target set is exactly what Redis holds, so a
   cold cache is not warmed by the cycle for symbols nobody has asked for.
4. **Refreshes incrementally.** For a cached symbol only rows *newer than its
   newest cached row* are read (`load_raw(start=…)`), then
   `service._slide_window` merges them: newest row per timestamp wins, and
   every row older than `window_minutes` before the newest is purged. The purge
   is anchored on the **newest row, not the wall clock**, so a stalled feed
   keeps its data instead of being eaten away while no ticks arrive.
5. **Never evicts on partial failure.** A symbol whose KQL read fails keeps its
   previous entry — a partial failure must not throw away data that is merely
   older. Failures are collected into the cycle's `errors` list, published by
   `/api/health`, and the loop survives (a raised cycle is logged and the next
   slot still runs).
6. **Refreshes the `metrics_5m` grids** alongside (`metrics.refresh_symbol`),
   reading only stops newer than the newest cached one, merging with
   "newest row per `(symbolId, ctidTraderAccountId, as_of)` wins"
   (`metrics.slide`), and purging that symbol's built ladder bundles so a
   rewritten stop cannot serve a stale bundle.
7. **Bounded participation.** The target set is read from Redis
   (`cached_symbols`), so a symbol nobody asks for drops out on its own when
   its TTL expires; the set is bounded by real use, not by the dimension size.
   A cold cache stays empty until a request lazily loads it — the cycle never
   invents targets.

`REDIS_TTL_SECONDS` (450 s = 1.5 cycles) is deliberately above the interval,
so one missed cycle still leaves the cache populated. `CACHE_REFRESH_MINUTES=0`
switches the cycle off (lazy caching), or `scripts/refresh_cache.py` runs one
cycle from cron/systemd (`interval=0` → single pass, non-zero exit on failure).

### 4.4 Degradation rules

| Failure | Behaviour |
|---|---|
| Redis miss / outage / corrupt entry | fall through to KQL, repopulate; `cache_connected: false` reported, **app stays healthy** |
| KQL failure | `connected: false`, `DataSourceError` → HTTP 502; this is the only thing that marks the app unhealthy |
| Refresh cycle failing / off | `cache_refreshed_at` stops advancing; reads still work, they just fall through to KQL |
| Pipeline backfill older than the cached newest row | *not* picked up by the incremental slide; only a full re-read (delete the tick keys / TTL expiry) picks it up — documented operational caveat |

The invariant: **a cache failure is a performance event; a KQL failure is an
availability event.** The two are never conflated in status.

---

## 5. The canonical tick frame

Every tick-derived metric operates on one frame, so they agree by
construction. Building it correctly is the most important step in the
methodology.

### 5.1 Projection (`frames.from_ticks`)

`agg_dom` rows are projected onto the canonical frame:

```
ts | symbol | bid | ask | last | volume | bid_depth | ask_depth | signed_volume
   | imbalance | imbalance_ratio | vwap_bid | vwap_ask | vwap_spread
   | rel_spread | rel_vwap_spread
```

| Canonical field | Definition | Note |
|---|---|---|
| `ts` | `timestamp` parsed as UTC | coercion failures are dropped (`dropna(subset=["ts", …])`) |
| `symbol` | `symbolId` as a stripped string | one normalisation point for the per-frame key — the composite's account half is resolved upstream, so a frame always holds one `(symbolId, ctidTraderAccountId)` pair's rows |
| `bid` / `ask` | `best_bid` / `best_ask` | numeric coercion; must be `> 0` |
| `last` | `(bid + ask) / 2` | the feed's own trade print is not used; a tick metric only guarantees the quote |
| `bid_depth` / `ask_depth` | `total_bid` / `total_ask` | resting size at top of book |
| `volume` | `(total_bid + total_ask) / 100` | **`VOLUME_SCALE = 100`** — resting sizes arrive in units of 100 lots, scaled to lots *before* anything downstream (KPI totals, profile, imbalance) reads it |
| `signed_volume` | pipeline `imbalance` when present, else `bid_depth − ask_depth` | the directional signal is the pipeline's own when it exists |
| spreads | pipeline `vwap_*`/`rel_*` columns passed through | the app derives its own quote spread too (§5.3) |

**What "volume" means here.** A per-tick metric row cannot know traded
quantity — it knows the resting size at the top of book. So `volume` is a
*resting-size proxy* (bid + ask resting size, scaled), and every "volume" on
the dashboard inherits that meaning. This is stated as a known limitation
(§14).

### 5.2 Row hygiene (the repair rules)

Applied in order, and each exists because the alternative empties data that is
mostly good:

1. **One-sided book** (missing bid or ask): dropped — there is no mid price,
   so the row cannot be faked.
2. **Non-positive quotes** (`bid ≤ 0` or `ask ≤ 0`): dropped.
3. **Majority-crossed frame**: if `> 50 %` of rows have `bid > ask`, the feed
   itself is mirrored (the pipeline wrote `best_bid`/`best_ask` the wrong way
   round) rather than a few rows being desynchronised. The quotes are **swapped
   back** (and the pipeline's `rel_spread` sign flipped with them); `mid` is
   identical either way — only the spread sign, which is exactly what a
   mirrored feed gets wrong. A single mirrored row in an otherwise healthy
   frame is still a minority and is dropped as noise
   (`test_majority_crossed_frame_is_repaired_not_dropped`,
   `test_minority_crossed_rows_are_still_dropped`).
4. **Genuinely crossed ticks** (remaining `bid ≥ ask`): dropped as
   desynchronised.

### 5.3 Preparation (`analytics.prepare`)

* timestamps coerced to UTC; rows sorted by `ts`;
* duplicate timestamps deduped, **last row wins** (the newest write is the
  corrected one);
* `mid` backfilled: quoted midpoint `(bid+ask)/2` first, then `last`;
* rows missing `ts` or `mid` dropped; `volume` coerced with `fillna(0.0)`;
* `spread = ask − bid`, `spread_bps = spread / mid × 10 000` (basis points —
  the comparable-across-instruments unit used everywhere).

### 5.4 Enrichment (`analytics.enrich`)

| Column | Definition |
|---|---|
| `mid_prev` | `mid.shift(1)` |
| `tick_dir` | `sign(mid.diff().fillna(0))` — the tick-rule direction (−1/0/+1) |
| `price_change` | `mid.diff()` (absolute) |
| `depth_proxy` | `1 / spread` (zero spread → NaN): wider quote ⇒ thinner top of book |
| `interarrival_ms` | `ts.diff() × 1000` |
| `signed_volume` | `volume × tick_dir`, unless the frame already carries the pipeline's own signed volume from `from_ticks` |

---

## 6. Request windowing and point-in-time semantics

`/api/analytics` answers *what happened in the aggregate window ending at the
chosen point in time*, entirely in memory over the cached rows
(`service.load_ticks_cached` → `service._narrow`). The request's `symbol` may
be a bare `symbolId` or the composite `symbolId|account`; either way the entry
it hits is keyed by the resolved pair (§4.1), so two feeds sharing one id can
never share a window. Then:

1. **Clamp the window** to the fixed cache window (`clamp_window`) — never a
   rejection, never a second KQL read.
2. **Anchor:**
   * no `as_of` → anchor is the **newest cached row** (most recent session);
   * `as_of` present → anchor is `min(newest row, as_of)`, so the window *ends
     at that moment*; a point in the future means "up to the latest data"; a
     point older than the cache re-anchors there (and if the resulting slice is
     empty, a clear `DataSourceError` names the window);
   * an **unparseable** `as_of` is refused, never silently re-anchored.
3. **Slice** `[anchor − window, anchor]`, then cap at `limit` (newest rows,
   `MAX_TICKS` = 200 000 default).
4. Project to the canonical frame (§5) and compute (§7).

Because the narrowing happens *after* the cache read, changing the point in
time or the window is free — no storage is touched for it. `meta` echoes the
resolved `window_minutes`, `as_of`, `rows_analysed`, `latest_tick` and
`generated_at` so the UI can state exactly which slice a report describes.

---

## 7. Tick-derived analytics (`app/analytics.py`)

`analytics.build_report` chains `clean(enrich(prepare(frame)))` and returns
one bundle: `summary`, `ohlcv`, `microstructure`, `volume_profile`,
`rolling`, `distribution`, `price_change`, `behaviour`, `hourly`, `depth`,
`ticks`. Every block below operates on that single enriched frame.

Notation: `mid_t` = mid at tick *t*, `T` = number of ticks,
`duration_s = ts_last − ts_first` (seconds), `r_t = mid_t / mid_{t−1} − 1`,
`bps = ×10 000`.

### 7.1 Summary (`analytics.summary`) — the KPI strip

One object, so the tiles cannot contradict each other
(`test_summary_tiles_agree_with_each_other`).

| Field | Formula / definition |
|---|---|
| `ticks` | `T`, count of rows in the window |
| `open` / `close` | `mid_0` / `mid_{T−1}` |
| `high` / `low` | `max(mid)` / `min(mid)` |
| `change` | `close − open` (absolute) |
| `change_pct` | `(close − open) / open × 100` (guarded: `open = 0` → `None`) |
| `vwap` | `Σ(mid × volume) / Σ(volume)` when any volume `> 0`, else `mean(mid)` |
| `realized_vol_bps` | `√( Σ r_t² / duration_s ) × 10 000` — per-second realised volatility in bps; `None` when `duration_s = 0` or no returns |
| `annualised_vol_pct` | `realized_vol_bps × √(252·24·3600) / 10 000 × 100` — 24/7 market annualisation factor |
| `avg/median/p95/max/min_spread` | quantiles of `spread_bps` |
| `total_volume` | `Σ volume` (resting-size proxy, §5.1) |
| `ticks_per_minute` | `T / (duration_s / 60)` |
| `avg/median/max_interarrival_ms` | moments of `interarrival_ms` |
| `buy_ticks` / `sell_ticks` / `unchanged_ticks` | counts by `tick_dir` sign |
| `large_trades` | count of `volume > p95(volume_positive)` (§7.3) |
| `session_span` | contiguous sessions split where the inter-tick gap `> 30 min` |
| `avg_tick_size` | `mean(|price_change|)` |

**Volatility is scaled by observed tick rate**, not by an assumed sampling
frequency: realised vol accumulates squared per-tick returns over the window's
duration, so symbols with different tick densities remain comparable.

### 7.2 OHLCV bars (`analytics.ohlcv`)

Ticks are resampled on `TIMEFRAMES` (`1s … 1d`, mapped to pandas offsets) and
each bar is aggregated from the mid series:

```
o = first(mid)   h = max(mid)   l = min(mid)   c = last(mid)
v = Σ volume     n = count      spread = mean(spread_bps)
vwap = Σ(mid×volume) / Σ(volume)      chg = (c − prev_close)/prev_close × 100
```

* bins with zero ticks are **dropped** — charts never receive all-null bars;
* bars capped at `max_bars = 1500` (newest kept);
* OHLC consistency (`o/h/l/c` ordering, `n` positive) is pinned by
  `test_ohlc_bars_are_consistent`.

### 7.3 Microstructure (`analytics.microstructure`)

**Order-flow imbalance (OFI).** Signed volume `s_t` (pipeline imbalance, else
`volume × tick_dir`) is rolled over
`window = max(1, T // 1200)` ticks so the series is capped at ~1200 points:

```
ofi_t   = rolling_sum(s)
imb_t   = ofi_t / rolling_sum(|s|)   ∈ [−1, 1]   (float overshoot clipped)
```

If the frame carries no volume at all, the panel degrades to a **tick-direction
imbalance**: rolling mean of `tick_dir`. The panel therefore always renders
(`test_order_flow_within_bounds` pins the bound).

**Spread evolution.** The raw per-tick `spread_bps` series, last ≤1200 points.

**Inter-arrival.** The raw `interarrival_ms` series — the liquidity/activity
burst signal.

**Trade sign (Lee-Ready-style tick test).** Because the feed gives quotes, not
aggressor flags, direction is inferred from the mid move (`tick_dir`):
`buy_ticks`, `sell_ticks`, `unchanged_ticks`, `buy_ratio`, `sell_ratio`,
`imbalance = (buy − sell)/(buy + sell)`. This is the KPI "Order flow" tile.

**Large trades & market impact.** Threshold = **95th percentile of strictly
positive volumes** (requires `> 20` positive observations, else `∞` = none).
Each qualifying tick reports the *immediate* impact of the next tick:

```
impact_bps_1tick = (mid_{t+1} − mid_t) / mid_t × 10 000
direction        = sign(tick_dir) → "buy" / "sell" / "flat"
```

Last 200 events are returned.

### 7.4 Volume profile (`analytics.volume_profile`)

Volume-at-price: where activity clusters = support/resistance zones.

1. **Bin** `mid` over `[min(mid), max(mid)]` into `bins` equal-width price
   bins (default 60; `np.digitize` with clipped index), summing `volume` and
   counting ticks per bin.
2. **Degenerate frame** (`high ≤ lo`, i.e. flat price): a single bin holding
   all volume, POC = that price — no NaN, no divide-by-zero.
3. **POC** = `argmax(volume)` over the ascending ladder.
4. **Value area (70 %)** — `analytics.value_area`, greedy outward expansion
   from the POC: at each step absorb the busier of the two adjacent bins until
   `VALUE_AREA_SHARE (0.70)` of total volume is covered or the ladder ends.
   Bins **must be ascending** for adjacency to mean anything; the POC/HVN/value
   area are computed ascending, and only the returned `bins` are inverted
   (the ladder reads top-to-bottom from the highest price).
5. **High-volume nodes** = local maxima (`v_i ≥ v_{i−1}` and `v_i ≥ v_{i+1}`)
   whose volume is `≥ p80` of bin volumes; top 5 by volume, presented
   descending by price.

The **same** `value_area` function is reused by the precomputed path (§8.4),
so a pandas-binned profile and a table-read ladder can never disagree about
where the value area sits.

### 7.5 Rolling metrics (`analytics.rolling_metrics`)

Over a tick window `w` (default 50, UI `window` query param, clamped `≥ 2`):

* **rolling volatility**: `std(log mid_t / mid_{t−1}) over w × √(ticks_per_year) × 100`
  where `ticks_per_year = max(1, (T / duration_s) × 252·24·3600)` — annualised
  by *observed* tick rate (falls back to `√(252·24·3600)` for `T < 2`);
* **rolling spread**: `rolling_mean(spread_bps)`;
* **cumulative return**: `(mid_t / mid_0 − 1) × 100`.

Series capped at 1200 points.

### 7.6 Return distribution (`analytics.return_distribution`)

Per-tick returns in **bps** (`r_t × 10 000`), histogrammed over `bins`
(capped at 40 in the bundle). Reported moments and tests:

| Stat | Formula |
|---|---|
| `mean_bps`, `std_bps`, `median_bps`, `min_bps`, `max_bps` | sample moments of the bps returns |
| `skew` | `E[(x−μ)³] / σ³` (population form) |
| `excess_kurtosis` | `E[(x−μ)⁴] / σ⁴ − 3` |
| `p01_bps` / `p99_bps` | 1st / 99th percentiles (tail width) |
| `jarque_bera` | `(n/6) × (skew² + (kurt−3)²/4)` — statistic only, no p-value (no scipy dependency) |
| `bullish_ratio` | share of returns `> 0` |

Guards: `T < 5` or `σ = 0` → empty histogram + empty stats (never NaN).

### 7.7 Price change from a fixed reference (`analytics.price_change`)

`pct_t = (mid_t / mid_0 − 1) × 100` where `mid_0` is **the first tick of the
window** — a fixed baseline that never moves, so the path starts at 0 %, runs
either side of it, and two windows are comparable. This deliberately reports a
*neutral* change, **not** a drawdown (nothing is measured against a running
peak).

* `reference_ts` / `reference_mid` name the baseline;
* `change_pct` is the final value; `min_pct`/`max_pct` carry their timestamps;
* `mid_0 = 0` (or missing) makes the percentage undefined → an **empty path**,
  never infinities (`test_price_change_survives_a_zero_reference`);
* series decimated by `step = max(1, T // 1200)`.

### 7.8 Behaviour (`analytics.behaviour`) — trend vs. mean-reversion

Requires `T ≥ 30` and a non-zero return std, else empty structures.

* **Autocorrelation**: `acf_k = corr(r_t, r_{t+k})` for
  `k = 1 … min(10, ⌊len(r)/3⌋)`, `acf_0 = 1`; NaN/Inf → `None`.
* **Efficiency ratio** (Kaufman): `|mid_last − mid_first| / Σ|Δmid|` ∈ [0, 1]
  — 1 = a straight-line move, 0 = pure noise (`test_behaviour_bounds`).
* **Hurst exponent** (rescaled range, single-scale): on demeaned cumulative
  deviations of `r`, `H = R / (S × √n)` with `R = max(cum) − min(cum)` and
  `S = std(r)`; `< 0.5` mean-reverting, `> 0.5` trending. Requires `n ≥ 40`.
  (A single-scale R/S estimate — see §14.)
* **Variance ratio** (Lo–MacKinlay style): for lags
  `k = 2 … min(20, ⌊n/5⌋)`, `vr_k = var(summed k-step returns) / (k × var(1-step))`
  — `≈ 1` consistent with a random walk, `< 1` mean-reverting.

### 7.9 Hourly profile (`analytics.hourly_profile`)

Ticks are bucketed by **wall-clock hour in `DISPLAY_TIMEZONE`**
(`America/New_York` default; an unknown zone falls back to NY at config load).
Per hour: `ticks`, `volume`, `mean_return_bps`, `volatility_bps =
std(rets) × 10 000`, `avg_spread_bps`. The zone is echoed as `timezone` so the
UI labels the axis; data itself stays UTC end-to-end (§11).

### 7.10 Book depth (`analytics.book_depth`)

Present only when the reader produced depth columns; otherwise
`{available: false}` — a panel never fabricates numbers.

With `bid_depth = total_bid`, `ask_depth = total_ask`:

* `share = bid / (bid + ask)` (share of resting size on the bid),
* `ratio = (bid − ask) / (bid + ask)` (depth imbalance ∈ [−1, 1]),
* avg/max per side, `avg_levels_bid/ask` when level counts exist,
* histogram of total resting size (25 bins), and a ≤1200-point
  `imbalance_series`.

### 7.11 Tick table (`analytics.tick_table`)

The most recent ≤250 rows of `ts, bid, ask, mid, volume, spread_bps,
tick_dir`, timestamps formatted ISO-8601 with microseconds — raw ground truth
for the derived panels.

---

## 8. The precomputed path (`app/metrics.py`, table `metrics_5m`)

### 8.1 What the grid is

One row per `(symbolId, ctidTraderAccountId, as_of)`; `as_of` runs over the last **4 h in 5-minute
stops** (49 stops); each row carries the four fixed trailing windows
**60/120/180/240 min** ending at `as_of`, as `<metric>_<1h|2h|3h|4h>` columns.
The app projects only `vap_*` (dynamic ladder) and `poc_*` (real) —
`kql.WINDOW_NESTED`; the scalar columns are built by the KQL producer but
**not read** here (§9). Full contract: `docs/metrics_5m.md`.

### 8.2 Read discipline

* Deduplication: the producer *appends*, so the read keeps the newest row per
  composite stop — `arg_max(stamp, *) by symbolId, ctidTraderAccountId, as_of` —
  with the stamp discovered in order `computed_at → ingested_at → none`
  (`kql.METRICS_STAMPS`); only an unknown *column* error triggers the
  fallback — auth/throttle/outage errors surface.
* Account scoping: the read carries the composite's `ctidTraderAccountId`
  half (resolved from the catalogue), so one id traded under two feeds cannot
  return interleaved stops; blank means any account.
* Incremental refresh: `metrics.refresh_symbol` reads only stops at/after the
  newest cached one, scoped to that same resolved account; `metrics.slide`
  merges "newest row per pair-stop wins" and caps
  the grid at `MAX_TICKS` rows, dropping oldest stops first.
* A failed grid read keeps the previous entry (same partial-failure rule as
  ticks), and rewriting a grid purges that symbol's built bundles.

### 8.3 Stop and window resolution

* **Stop** (`metrics.pick_stop`): an arbitrary `as_of` resolves to the newest
  stop **at or before** it — the same "as of" semantics as the tick path. A
  point ahead of the data clamps to the newest stop; omitted = newest. Stops
  are published (`metrics.stops`) so the UI builds the timeline from what
  actually exists — a stalled producer leaves gaps, and a picker built on real
  stops cannot offer an empty slot.
* **Window** (`kql.best_window`): a requested window the table does not ship is
  answered with the widest shipped window **no wider than the ask** (90 → 60),
  and both `requested_window_minutes` and `selected_window_minutes` are
  reported so the UI never claims a span it was not given. The default (no
  parameter) is the shortest window, 60 — matching the dashboard's default.

### 8.4 Ladder → profile panel (`metrics.volume_profile`)

The ladder arrives pre-binned, so **no re-binning happens**; only the derived
marks are computed in memory (a sort over ~40 bins — not a table re-scan):

* **POC is re-derived from the ladder** (`argmax` of bin volumes). The stored
  `poc_*` column is a second, independent argmax and historically shipped
  swapped arguments (a volume in a price column); the ladder is therefore the
  source of truth and the stored value is only the fallback when no bin traded.
* **Value area** runs through the shared `analytics.value_area` (byte-identical
  across both paths).
* **High-volume nodes**: same rule as §7.4 (local maxima ≥ p80, top 5).
* Output shape is identical to the tick-derived profile — bins descending by
  price, `poc`, `value_area`, `high_volume_nodes` — so the UI renders either
  source unchanged.

### 8.5 Bundle caching

`metrics.report` builds all four windows' profiles from one row (the UI can
switch window without a request) and caches the bundle in Redis keyed by the
**resolved** stop (`report_key`), so two requests resolving to the same stop
share one entry while different stops never collide. `requested_as_of` and
`generated_at` are stamped on after the cache read. Refreshing a grid purges
that symbol's bundles (`metrics.purge_reports`).

---

## 9. Dual-source consistency rules

Two serving paths exist, and the methodology keeps them from contradicting
each other. Three rules, each pinned by a test:

1. **One frame per strip.** Every headline tile comes from
   `analytics.summary` — one tick frame, one window. The `/api/metrics`
   contribution is *only* the volume-at-price ladder overlay. (`test_the_headline_tiles_read_one_window_not_two`)
   The reason is not cosmetic: the bundles answer *different windows* —
   `metrics_5m` snaps `as_of` down to its 5-minute stop, the tick path ends at
   the instant asked for — so mixing them shows 6,226 ticks beside 150.8/min.
2. **The tiles are arithmetic, not independent reads.** `ticks_per_minute ==
   ticks / span_minutes`, `change_pct == change / open × 100`, so a broken
   identity fails a test instead of rendering a contradiction
   (`test_summary_tiles_agree_with_each_other`).
3. **The ladder overlay is the one thing precomputation buys.** The scalar
   columns of `metrics_5m` (`ticks`, `vwap`, `total_volume`,
   `realized_vol_bps`, `avg_spread_bps`, `ofi_imbalance`, `change_pct` × 4) are
   still built by the producer but **not projected** by the app — dead weight
   in the grid, and eligible for removal from the table once nothing else
   consumes them.

Because both profile paths share `value_area` and the same node rule, a panel
cannot change shape when the data source does.

---

## 10. Instrument classification methodology

The pipeline publishes the class on each symbol row; the app **consumes the
pipeline's classification first** rather than guessing
(`symbols_metadata → assets.classify_asset_class`).

1. **Broker class label** → stable client key (`assets.BROKER_CLASSES`). The
   label is the broker's own wording, read from its catalogue
   (`symbols_category.assetClassId` → `asset_classes.name`, joined on the
   account so each broker keeps its own numbering), with the account qualifier
   in parentheses stripped: Forex→`fx`, Metals→`metal`, Indices→`index`,
   Oil/Energies→`energy`, Cryptocurrencies/Crypto Currency→`crypto`,
   Commodities→`commodity`, Futures/Forwards/Futures Commodities→`future`,
   Bonds/Treasuries→`bond`, Currency Index/Commodities Indices→`index`. This is
   what makes a broker-specific number safe: acgmarkets' category 10 is
   *Metals* ("Metals (Pro)") even though the numeric fallback below reads 10 as
   the Oil id. Equity and ETF labels are deliberately unmapped — the taxonomy
   has no single-name share class.
2. **FX is sub-divided** (the pipeline lumps it; a dashboard benefits from the
   split): `fx_major` (the seven majors: EURUSD, GBPUSD, USDJPY, AUDUSD,
   USDCHF, USDCAD, NZDUSD), `fx_exotic` (any leg in the exotic set), else
   `fx_cross`. The pair is read off the base ticker, so a broker's account
   suffix (`EURUSD.pro`) cannot demote a major to a cross.
3. **Fallback** (`classify_by_name`) when the dimension row carries no usable
   category — archive entries, new listings, an unreloaded dimension — using
   the same signals cTrader exposes, in order: raw `symbolCategoryId` map →
   metal/energy/index/soft/bond token matches → futures name patterns
   (`^[A-Z]{1,3}[FGHJKMNQUVXZ]\d{2}(_CFD)?$` and `ROOT_<month><yy>`) or
   "FORWARD" in the description → crypto bases/quote pairs → FX heuristics
   (" VS " in description, or a 6-letter alphabetic ticker).
4. Anything still unresolved is **`Unclassified`**: excluded from the
   selectors (they carry no ticker, so a wall of bare ids helps nobody) but
   still counted — `unclassified_count` and the health hints keep the coverage
   gap visible.
5. Classes roll up into broad **families** (`fx`, `commodities`, `indices`,
   `rates`, `crypto`, `derivatives`) for grouped pickers, and the display order
   is fixed most-liquid-first (`assets.sort_key`).

Known asymmetry, surfaced rather than hidden: `agg_dom` accumulates rows from
**every** feed that writes to it, while `symbols_metadata` describes only the
scoped account(s)/broker(s) — a `(symbolId, ctidTraderAccountId)` pair with no
metadata row cannot be named or classified, so it stays out of the selectors
instead of appearing as a bare id, and the health hint (`uncovered_count`) says
how many there are.

---

## 11. Time, units and display conventions

| Concern | Convention |
|---|---|
| Storage & transport | **UTC everywhere** — `agg_dom.timestamp`, the canonical `ts`, every API field (`isoformat()`), the cached Parquet |
| Display | every wall-clock rendering (clock, time badges, *Point in time* field, hourly profile, calendar) uses `DISPLAY_TIMEZONE` (IANA, default `America/New_York`), published by `/api/health` so one server setting drives the whole UI; an invalid zone falls back to NY at config load, never a crash |
| Price returns | percent (`× 100`) for change-style metrics; per-tick returns in **bps** (`× 10 000`) for distributions and spreads |
| Volatility | `realized_vol_bps` accumulates squared returns per second; annualisation `√(252·24·3600)` (a 24/7 market) applied on read, never stored |
| Volume | **lots**, after `VOLUME_SCALE = 100` (feed reports units of 100 lots); it is a resting-size proxy (§5.1) |
| Spreads | `spread_bps` in basis points of mid — the cross-instrument comparable unit |
| Sessions | gaps `> 30 min` split `session_span` into distinct sessions |
| Timeline | `TIMELINE_MINUTES=240` in `TIMELINE_STEP_MINUTES=5` steps (49 stops), windows `WINDOW_CHOICES_MINUTES = 60/120/180/240` — all published by `/api/health`, never hard-coded in the browser |

---

## 12. Numerical robustness and payload hygiene

* **Strict JSON.** `json.dumps` happily emits bare `NaN`, which the browser's
  `JSON.parse` rejects; so every payload passes through `analytics.clean()`,
  which recursively maps `NaN/Inf/NaT → None` at the boundary rather than
  trusting each leaf (`_f` does the same for a single value).
* **Divide-by-zero guards** are explicit everywhere: `open = 0` → `change_pct
  = None`; flat price → single-bin profile; `σ = 0` → empty distribution and
  empty behaviour block; `duration_s = 0` → `None` vol; zero total depth → no
  share; zero/missing opening mid → empty price-change path. A degenerate
  flat-price series must never crash or emit infinities
  (`test_flat_series_does_not_crash`).
* **Empty frames** degrade to empty structures with stable shapes (`{"bins":
  [], "poc": None, …}`), so the UI renders an empty panel rather than a broken
  one; an empty *request* window raises `DataSourceError` (HTTP 502) naming the
  window — "no rows for what you asked" is not "empty analytics".
* **Bounds by construction**: normalised OFI clipped to `[−1, 1]` (float
  overshoot like `-1.0000000000000007`), efficiency ratio ∈ `[0, 1]`, depths
  non-negative after coercion.
* **Series decimation**: chart series are capped (~1200 points, 1500 bars,
  250 tick rows) so a dense session cannot produce an unbounded payload.
* **Config robustness**: booleans accept a fixed truthy set, integer lists are
  de-duped/sorted, the cache window can never be degenerate
  (`CACHE_LOOKBACK_HOURS=0` → 1-minute minimum), and the window-choice set
  always contains at least one servable value.

---

## 13. Validation: the invariants that pin this methodology

`python -m pytest tests -q` — the KQL layer and the Redis client are both
stubbed, so the suite runs without network or cloud credentials. The
methodology's guarantees are test-pinned:

| Guarantee | Test (file) |
|---|---|
| The KPI strip is one frame; tiles cannot contradict | `test_the_headline_tiles_read_one_window_not_two` (`test_maintenance.py`) |
| Tile arithmetic identities hold (`ticks_per_minute`, `change_pct`) | `test_summary_tiles_agree_with_each_other` (`test_analytics.py`) |
| OHLC bars are internally consistent | `test_ohlc_bars_are_consistent` (`test_analytics.py`) |
| Normalised OFI stays in `[−1, 1]` | `test_order_flow_within_bounds` (`test_analytics.py`) |
| Volume profile conserves mass; POC inside the price range | `test_volume_profile_poc_inside_range` (`test_analytics.py`, `sum(bins) ≈ Σ volume`) |
| Value area computed ascending, survives the ladder inversion | `test_volume_profile_value_area_survives_the_inversion` (`test_analytics.py`) |
| Both paths run the *same* value-area expansion; the marked POC is the tallest bin | `test_the_profile_from_the_table_runs_the_shared_value_area`, `test_the_poc_is_the_busiest_bin_whatever_the_stored_column_says` (`test_metrics.py`) |
| Price change is anchored on the window's first tick | `test_price_change_is_anchored_on_the_window_start` (`test_analytics.py`) |
| Zero/missing reference never leaks NaN/Inf | `test_price_change_survives_a_zero_reference` (`test_analytics.py`) |
| Flat (zero-volatility) series does not crash or divide by zero | `test_flat_series_does_not_crash` (`test_analytics.py`) |
| Majority-crossed frames repaired; minority crossed rows dropped; a lone mirrored row repaired | `test_majority_crossed_frame_is_repaired_not_dropped`, `test_minority_crossed_rows_are_still_dropped`, `test_single_row_mirrored_window_is_repaired`, `test_drops_crossed_books` (`test_frames.py`) |
| Behaviour metrics stay in range; distribution stats bounded | `test_behaviour_bounds`, `test_return_distribution_stats` (`test_analytics.py`) |
| Classification from pipeline category *and* from fallback signals | `test_assets.py` |
| A wider window is clamped to the cache, never widened; cache window never degenerate | `test_a_wider_window_is_clamped_to_the_cache` (`test_service.py`), `test_the_widest_window_is_clamped_to_the_cache`, `test_cache_window_is_never_degenerate` (`test_config.py`) |
| Clock-aligned slots + 15 s offset, immediate first pass, repeat, failure survival | `test_a_five_minute_cycle_waits_just_past_the_next_mark`, `test_the_example_mark_lands_fifteen_seconds_past`, `test_the_first_cycle_does_not_wait`, `test_a_failing_cycle_is_logged_and_the_next_one_still_runs` (`test_refresh.py`) |
| The slide purges the earliest slice, anchored on the newest row; a failing symbol keeps its entry | `test_the_cycle_purges_the_earliest_30_minutes`, `test_the_slide_purges_anchored_on_the_newest_row`, `test_a_failing_symbol_keeps_its_previous_entry` (`test_service.py`) |
| Cold read → one KQL call; warm reads from Redis; cache outage falls through | `test_a_cold_symbol_reads_kql_once_and_caches_the_full_window`, `test_a_warm_symbol_is_served_from_redis`, `test_a_cache_outage_still_returns_data` (`test_service.py`) |
| `metrics_5m` projection pinned to the four windows; dedup on the stamp column | `test_the_window_columns_pin_the_four_trailing_windows` (`test_metrics.py`), `test_fetch_metrics_dedupes_on_computed_at` (`test_kql.py`) |
| The nested ladder survives the Parquet round-trip through Redis | `test_the_ladder_survives_a_parquet_round_trip` (`test_metrics.py`) |
| Strict JSON serialisability of every payload | `test_build_report_is_json_serialisable` (`test_analytics.py`) |

---

## 14. Known limitations and assumptions

Stated explicitly so no reading of the numbers overreaches:

1. **"Volume" is a resting-size proxy.** `total_bid + total_ask` (scaled to
   lots) is top-of-book resting size, not executed quantity. VWAP, volume
   profile, "large trades" and total volume all inherit this meaning: they
   describe *where liquidity sat*, not what traded.
2. **Trade sign is inferred, not observed.** The tick rule
   (`sign(mid.diff())`) is a Lee-Ready-style estimate; without aggressor flags,
   a quote update that moves the mid is indistinguishable from a print.
3. **Single-scale Hurst.** The R/S estimate uses one window; it is indicative,
   not a multi-scale Hurst estimate.
4. **Jarque–Bera is the statistic only** — no p-value, deliberately (no scipy
   dependency).
5. **The incremental slide cannot see backfills older than the cached newest
   row.** A pipeline backfill only lands once the keys are deleted or the TTL
   expires (documented in the README troubleshooting table).
6. **The refresh cycle assumes a single worker.** It runs inside the one
   uvicorn process (`run.py`, no `--workers`), so there is no distributed lock;
   multiple workers would duplicate the cycle.
7. **`agg_dom` is shared across feeds** while the dimension is scoped to the
   configured account(s)/broker(s) (§10) — pairs outside that scope have no
   metadata row, so they are held out of the selectors and counted by
   `uncovered_count` instead.
8. **The two paths answer slightly different windows** (5-minute stop vs.
   exact instant) — which is precisely why scalar overlays between them are
   forbidden (§9).
9. **`metrics_5m` scalar columns are currently unread**; they remain in the
   table only for other consumers, and both docs mark them droppable.
10. **A one-row window is a whole frame for the mirror rule** — a lone crossed
    row in a sparse feed is repaired (a majority of one), not dropped, so
    sparse symbols still render.

---

## 15. Change control: keeping the sides in sync

The methodology is intentionally expressed in three places (computed here,
precomputed there, described everywhere). When you change one, change all:

| Concept | Where it lives |
|---|---|
| Composite grain `(symbolId, ctidTraderAccountId)` → broker-agnostic metadata | `app/kql.py`, `app/service.py`, `app/meta.py` **and** the grain rows in `docs/metrics_5m.md` / `docs/dashboard_metrics.md` |
| `VOLUME_SCALE`, `mid`/`volume`/`signed_volume` definitions | `app/frames.py` **and** the Stage-1 normalisation in `docs/metrics_5m.md` (the KQL twin) |
| Grid geometry: `TIMELINE_MINUTES`, `TIMELINE_STEP_MINUTES`, the four window minutes | `app/config.py` **and** `docs/metrics_5m.md` Stage 0 |
| `VALUE_AREA_SHARE`, POC/HVN rules | `app/analytics.py` **and** shared into `app/metrics.py` |
| Window suffixes / column names | `app/kql.py` (`WINDOW_SUFFIXES`, `WINDOW_NESTED`) **and** `docs/metrics_5m.md` |
| Display timezone and window choices | `app/config.py`, published live via `/api/health` (never hard-coded in the UI) |

Operational changes (new configuration knob, changed cadence, new endpoint)
belong in `README.md`; card/tile inventory changes belong in
`docs/dashboard_metrics.md`; producer-contract changes belong in
`docs/metrics_5m.md`. A behavioural change that alters a guarantee in §13 must
update the matching test in the same change.