# Dashboard cards, KPIs & metrics — data sources and formulas

Inventory of every KPI tile and card on the AXIOMA dashboard, the database
table each reads from, and the formula behind each metric. Generated from the
source in `app/` and `app/static/`. For the end-to-end methodology — data
sourcing, normalisation, windowing semantics, consistency rules and known
limitations — see [`docs/methodology.md`](methodology.md).

## Data model at a glance

The app reads a single Fabric **KQL (Eventhouse) database** — `ctrader_dom`
(`KQL_DATABASE`) — that contains the metric tables plus the raw pipeline
stages:

| Table | Grain | Purpose | Env var |
|---|---|---|---|
| `dom_stream_raw` | raw DOM events | ingested, **not** queried by the dashboard | — |
| `dom_book_flat` | reconstructed full order book | ingested, **not** queried | — |
| `agg_dom` | one row per `(symbolId, ctidTraderAccountId, tick)` | per-tick derived order-book metrics — the analytics read | `KQL_TABLE` |
| `metrics_5m` | one row per `(symbolId, ctidTraderAccountId, as_of)` | precomputed window metrics (1h/2h/3h/4h) incl. volume ladder | `METRICS_TABLE` |

Instrument metadata lives outside KQL, in local **PostgreSQL**: the
`symbols_metadata` view (one row per `(symbolId, ctrader_account_id)`) supplies
ticker, category and description (`SYMBOL_TABLE` / `PG_SYMBOLS_TABLE`). Each
feed row's `(symbolId, ctidTraderAccountId)` composite key maps onto one of
those rows, so the metadata it resolves to is broker-agnostic — name, category
and broker come from the account's own row, never from a per-broker table.

Two serving paths feed the dashboard:

- **`/api/analytics`** — tick-derived. Reads `agg_dom`, projects it to a canonical
  tick frame, then recomputes every metric in pandas (`app/analytics.py`). This is
  the source of **all headline KPI tiles** and most chart cards.
- **`/api/metrics`** — lookup. Reads the precomputed `metrics_5m` grid (pushed to
  Redis by the refresh cycle) and returns the chosen window's **pre-binned
  volume-at-price ladder** without a re-scan or re-binning (`app/metrics.py`). It
  supplies only that ladder overlay; all headline tiles come from `/api/analytics`.

### Raw columns read from `agg_dom` (`kql.METRIC_COLUMNS`)

`timestamp`, `symbolId`, `total_bid`, `total_ask`, `best_bid`, `best_ask`,
`imbalance`, `imbalance_ratio`, `vwap_bid`, `vwap_ask`, `vwap_spread`, `rel_spread`,
`rel_vwap_spread`.

The read itself is scoped by the composite key: `symbolId` and
`ctidTraderAccountId` travel as declarative query parameters
(`kql._SYMBOL_MATCH` / `kql._ACCOUNT_MATCH`), so one id traded under two feeds
can never mix rows inside a single frame.

### Canonical tick frame (`frames.from_ticks` -> `analytics`)

`agg_dom` is projected onto: `ts | symbol | bid | ask | last | volume | mid`
(plus depth/imbalance/spread columns). Key normalisations:

- `bid = best_bid`, `ask = best_ask`, `last = (bid + ask) / 2`.
- `volume = (total_bid + total_ask) / 100` — resting sizes arrive in units of 100
  lots, scaled to lots (`frames.VOLUME_SCALE = 100`).
- `mid = (bid + ask) / 2` (fallback `last`) — `analytics.prepare`.
- `spread = ask - bid`; `spread_bps = spread / mid * 10000`.
- `tick_dir = sign(mid.diff())`; `price_change = mid.diff()`;
  `interarrival_ms = ts.diff() * 1000`; `signed_volume = volume * tick_dir` (or the
  pipeline `imbalance` when present) — `analytics.enrich`.
- Rows with no bid/ask, non-positive quotes, or a genuinely crossed book
  (`ask <= bid`) are dropped; a **majority**-crossed (mirrored) frame is repaired by
  swapping quotes rather than discarded.

---
## KPI strip (headline tiles)

Rendered by `renderKpis(summary, microstructure)` in `app/static/app.js`.
**Source table: `agg_dom`** (via `/api/analytics` -> `analytics.summary` +
`analytics.microstructure`). Every tile is a different view of one tick frame, so
they agree by construction (pinned by `test_summary_tiles_agree_with_each_other`).

| KPI tile | Field | Table | Formula |
|---|---|---|---|
| **Ticks** | `summary.ticks` | `agg_dom` | `count` of ticks in the window |
| **Last** | `summary.close` | `agg_dom` | `mid` of the last tick; hint shows `start -> end` timestamps |
| **Change** | `summary.change_pct` | `agg_dom` | `(last - first) / first * 100` where first/last are the first/last `mid`; hint shows absolute `change = last - first` |
| **High / Low** | `summary.high` / `summary.low` | `agg_dom` | `max(mid)` / `min(mid)` over the window |
| **Realised vol** | `summary.realized_vol_bps` | `agg_dom` | `sqrt(sum(mid.pct_change()^2) / duration_s) * 10000`; hint: `annualised_vol_pct = vol_bps * sqrt(252*24*3600) / 10000 * 100` |
| **Avg spread** | `summary.avg_spread` | `agg_dom` | `mean(spread_bps)`; hint: `p95_spread = quantile(spread_bps, 0.95)` |
| **Ticks / min** | `summary.ticks_per_minute` | `agg_dom` | `ticks / (duration_s / 60)`; hint: `avg_interarrival_ms = mean(ts.diff()*1000)` |
| **Order flow** | `microstructure.trade_sign.imbalance` | `agg_dom` | `(buy_ticks - sell_ticks) / (buy_ticks + sell_ticks)`, from `tick_dir > 0` / `< 0` (Lee-Ready tick test) |
| **Volume** (if any) | `summary.total_volume` | `agg_dom` | `sum(volume)`; hint: `large_trades` = count of `volume > 95th percentile` |

---
## Cards by section

Each card is rendered by a chart function in `app/static/app.js` from a block of
`analytics.build_report` (`/api/analytics`, table `agg_dom`) unless noted. All 15
cards are click/keyboard disclosures with a detail body.

### Overview

| Card (`data-card`) | Title | Report block | Table | Formula / definition |
|---|---|---|---|---|
| `price` | Price, volume & spread | `ohlcv` | `agg_dom` | Ticks resampled to OHLCV bars. Per bar: `o=first(mid)`, `h=max(mid)`, `l=min(mid)`, `c=last(mid)`, `v=sum(volume)`, `n=count`, `spread=mean(spread_bps)`, `vwap=sum(mid*volume)/sum(volume)`, `chg=(c - prev_close)/prev_close*100`. Empty bins dropped. |
| `chg` | Price change vs. window start | `price_change` | `agg_dom` | `(mid / first_mid - 1) * 100`; reference = first tick. Reports `min_pct`/`max_pct` and their timestamps. |
| `hist` | Return distribution | `distribution` | `agg_dom` | Histogram of per-tick returns `mid.pct_change()*10000` (bps). Stats: mean/std/median/min/max bps, `skew`, `excess_kurtosis = kurt - 3`, `p01/p99`, `jarque_bera = (n/6)(skew^2 + ((kurt-3)^2)/4)`, `bullish_ratio = share(rets>0)`. |

### Microstructure

| Card (`data-card`) | Title | Report block | Table | Formula / definition |
|---|---|---|---|---|
| `ofi` | Order-flow imbalance | `microstructure.order_flow` | `agg_dom` | Rolling `ofi = sum(signed_volume)`, normalised `imbalance = ofi / sum(|signed_volume|)` clipped to `[-1, 1]`. Falls back to mean `tick_dir` when there is no volume. |
| `depth` | Book depth & imbalance | `depth` (`book_depth`) | `agg_dom` | Only when depth columns exist. Uses `bid_depth=total_bid`, `ask_depth=total_ask`; `share = bid/(bid+ask)`, `ratio = (bid-ask)/(bid+ask)`, avg/max depths, depth histogram. Returns `{available:false}` otherwise. |
| `acf` | Return autocorrelation | `behaviour` | `agg_dom` | Autocorrelation of `mid.pct_change()` over lags 1-10; plus `efficiency_ratio = |last-first| / sum(|diff(mid)|)`, Hurst via rescaled range, Lo-MacKinlay `variance_ratio = var(k-step return)/(k*var(1-step))`. |
| `large` | Large trades & market impact | `microstructure.large_trades` | `agg_dom` | Trades with `volume > 95th percentile`; each shows `impact_bps_1tick = (next_mid - mid)/mid * 10000` and direction from `tick_dir`. |

---
### Liquidity & activity

| Card (`data-card`) | Title | Report block | Table | Formula / definition |
|---|---|---|---|---|
| `profile` | Volume at price | `volume_profile` | `agg_dom` (overlaid from `metrics_5m`) | `mid` binned across `[min,max]` into `bins` ladders. POC = busiest bin; value area = 70% band around POC (greedy expansion, `VALUE_AREA_SHARE=0.70`); high-volume nodes = local peaks above the 80th-percentile volume. When `metrics_5m` is active the **pre-binned ladder** (`vap_*`) is overlaid so no re-binning is needed. |
| `spread` | Bid-ask spread (bps) | `microstructure.spread` / `rolling.spread` | `agg_dom` | Per-tick `spread_bps` series (and a rolling mean). |
| `iarr` | Tick inter-arrival | `microstructure.interarrival` | `agg_dom` | `interarrival_ms = ts.diff()*1000` series. |
| `hour` | Activity by hour | `hourly` | `agg_dom` | Ticks bucketed by hour in the display zone: per hour `ticks`, `volume`, `mean_return_bps`, `volatility_bps = std(rets)*10000`, `avg_spread_bps`. |
| `vol` | Rolling volatility | `rolling` | `agg_dom` | `log(mid/mid.shift(1))` rolling-window std, annualised: `roll_std * sqrt(ticks_per_year) * 100` where `ticks_per_year = (ticks/duration_s) * 252*24*3600`. Also rolling spread and cumulative return. |

### Data

| Card (`data-card`) | Title | Report block | Table | Formula / definition |
|---|---|---|---|---|
| `ticks` | Latest ticks | `ticks` (`tick_table`) | `agg_dom` | The most recent raw ticks (`tail(limit)`) with `ts, bid, ask, mid, volume, spread_bps, tick_dir`. |
| `coverage` | Instrument coverage | health + `/api/asset-classes` | `symbols_metadata` (PostgreSQL) | Per asset-class instrument rollup. Class derived from `symbolCategoryId` via `assets.classify_asset_class` (FX sub-classed into majors/crosses/exotics). Tag shows `symbol_count` traded, `classified_count` classified. |

### Calendar

| Card (`data-card`) | Title | Source | Table | Formula / definition |
|---|---|---|---|---|
| `calendar` | Data calendar | `calendarDays(report)` in `app.js` | `agg_dom` (via `ohlcv` bars) | OHLCV bars collapsed to one record per display-zone day: `open`=first bar open, `close`=last bar close, `high`/`low`=max/min, `ticks`=sum of bar `n`, `volume`=sum of bar `v`, `spread`=mean, `change = (close - open)/open * 100`, `busiest`=hour with most ticks. |

---
## `metrics_5m` volume-at-price ladder (`/api/metrics`, table `metrics_5m`)

`metrics_5m` holds, per `(symbolId, ctidTraderAccountId, as_of)` stop and per fixed window
(`1h/2h/3h/4h`, suffixes `_1h`...`_4h`), the **pre-binned volume-at-price ladder**
(`vap_<w>`) and its point of control (`poc_<w>`). A dashboard request is a lookup:
pick the row for the `as_of` stop and the `vap_*` column for the chosen window
(`metrics.volume_profile`). This is the **only** thing `/api/metrics` serves, and
the **only** thing the dashboard overlays from it — the precomputation genuinely
pays here because the ladder ships already binned (no re-binning in pandas). Full
KQL reference: `docs/metrics_5m.md`.

Nested columns the app reads:

| Column | Type | Definition | Table |
|---|---|---|---|
| `vap_<w>` | dynamic | pre-binned volume-at-price ladder (price/volume bins) | `metrics_5m` |
| `poc_<w>` | real | point of control (busiest bin centre) — re-derived from the ladder in the app | `metrics_5m` |

The volume-profile panel built from `vap_*` (`metrics.volume_profile`) returns the
same shape as the tick-derived profile (`bins` descending by price, `poc`,
`value_area`, `high_volume_nodes`), so the UI renders either source unchanged.

> **The scalar window metrics are not read.** `metrics_5m` also ships `ticks`,
> `vwap`, `total_volume`, `realized_vol_bps`, `avg_spread_bps`, `ofi_imbalance` and
> `change_pct` per window, but the app does **not** project them
> (`kql.window_columns()` is just `WINDOW_NESTED`) and `/api/metrics` no longer
> reports them. Every headline tile comes from `/api/analytics` — one tick frame —
> because the two bundles answer different windows (`metrics_5m` snaps `as_of`
> down to its 5-minute stop, the tick path ends at the instant asked for). The
> scalars are dead weight in the grid and can be dropped from the table once
> nothing else consumes them (see `docs/metrics_5m.md`).

### `metrics_5m` contract (from `docs/metrics_5m.md`)

| Property | Value |
|---|---|
| Grain | one row per `(symbolId, ctidTraderAccountId, as_of)` |
| Point-in-time grid | last 4 h in 5-minute steps (49 stops, `as_of` inclusive) |
| Windows (fixed) | 60 / 120 / 180 / 240 minutes, each ending at `as_of` |
| Columns the app reads | `vap_1h`...`vap_4h` (dynamic), `poc_1h`...`poc_4h` (real) |
| Scalar columns | `ticks`/`vwap`/`total_volume`/`realized_vol_bps`/`avg_spread_bps`/`ofi_imbalance` × 4 + `change_pct` × 4 — **built by the KQL, not read by the app** |
| Source table | `ctrader_dom.agg_dom` |
| Row stamp | `computed_at` (legacy fallback `ingested_at`) |
| OHLC | **not** in this table — session extremes stay tick-derived (`agg_dom`) |

---

## Source-code map

| Concern | File | Table |
|---|---|---|
| KQL access & column contracts | `app/kql.py` | `agg_dom`, `metrics_5m` |
| Postgres instrument metadata | `app/meta.py` | `symbols_metadata` |
| Table names / windows / cache settings | `app/config.py` | — |
| Per-tick reader & normalisation | `app/frames.py` | `agg_dom` |
| Tick-derived analytics (KPIs + chart blocks) | `app/analytics.py` | `agg_dom` |
| Precomputed window metrics lookup | `app/metrics.py` | `metrics_5m` |
| Service/cache orchestration | `app/service.py`, `app/cache.py` | all |
| Asset-class taxonomy | `app/assets.py` | `symbols_metadata` |
| Dashboard rendering (KPIs + cards) | `app/static/app.js`, `app/static/index.html` | — |
| `metrics_5m` KQL reference | `docs/metrics_5m.md` | `metrics_5m` |
