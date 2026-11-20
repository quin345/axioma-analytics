# 5-minute research metrics (`metrics_5m`)

`webapp/export_flat.py` records every metric the terminal computes in the
**`metrics_5m` table of the local SQLite database** (`webapp/data/axioma.db`,
`DB_PATH`), next to `vap_run`: **one row per instrument per 5-minute slot**. It
calls the same `analytics.py` functions the `/api/*` routes use, on the same
10-second buckets, so a stored value matches what that card would show over
that slot.

## Where it lives

| Piece | Where |
|---|---|
| Table | `metrics_5m` in `webapp/data/axioma.db` (gitignored) |
| Storage code | `webapp/db.py`: `ensure_metrics_table`, `save_metrics`, `metrics_summary` |
| Job | `webapp/export_flat.py` |
| Kusto reads | `queries.symbols_range`, `series_range`, `tick_times_range` (fixed UTC intervals) |
| Tests | `webapp/tests/test_backend.py` (`test_export_flat_*`) |

The primary key is `(ctid_trader_account, symbol_id, slot_end)`, and writes are
`INSERT OR REPLACE` on it, so a rerun or an overlapping backfill replaces rows
instead of duplicating them. Each run is one transaction (WAL mode), so a
reader never sees a half-written run. `computed_at` stamps when a row was
written. An index on `slot_end` serves cross-sectional reads.

The table is created from the exporter's column list (`export_flat.COLUMNS`).
A metric added there later is added to the table with `ALTER TABLE` on the next
write, and older rows hold NULL for it.

## Time convention (no look-ahead)

All times are UTC. A slot is the half-open interval `[slot_start, slot_end)`,
and every value in a row uses only data stamped before `slot_end`. The VAP
columns are trailing windows that end at `slot_end`.

The live job exports the **last two completed slots**, so the second run
re-captures any ticks that landed late.

**Empty slots.** Every instrument that ticked anywhere in the exported range
gets a row for **every** slot, so the grid is complete. A slot with no ticks
has `has_data = 0`, `events = 0` and NULL prices. Filter on `has_data == 1`
before computing returns. A live run when no instrument ticked at all (the
whole market shut) writes nothing.

## Columns

| Group | Columns |
|---|---|
| Identity | `slot_start`, `slot_end`, `broker`, `ctid_trader_account`, `symbol_id`, `symbol_name`, `standardized_name`, `asset_class` (from the `symbol_mapping` snapshot; blank if unmapped) |
| Activity | `has_data`, `events` (depth updates), `book_buckets` (10s buckets with a two-sided book) |
| Price (mid) | `mid_open`, `mid_high`, `mid_low`, `mid_close`, `log_return`, `change_pct` |
| Spread | `spread_mean`, `spread_median`, `spread_min`, `spread_max`, `rel_spread_mean` |
| Volatility | `realised_vol_pct`: annualised, from 10s log returns within the slot (as `/api/kpis`) |
| Autocorrelation | `acf_lag1..3` (10s returns), `acf_band` (95% bound) |
| Order flow | `flow_buy_events`, `flow_sell_events`, `flow_imbalance` (tick-rule proxy, -1 to +1), `book_imbalance` |
| Burst impact | `burst_threshold`, `burst_count`, `burst_impact_60s_bps` |
| Tick timing | `tick_distinct_timestamps`, `tick_shared_timestamp_share`, `tick_median_gap_ms` |
| Volume-at-price | for `W` in 60/120/180/240: `vap_Wm_poc`, `_va_low`, `_va_high`, `_volume_events`, `_hvn_count`, `_hvn_top_price`, `_coverage` |

Types: identity and timestamps are `TEXT` (ISO-8601 UTC, `...Z`), counts are
`INTEGER` (including `symbol_id`), and every other metric is `REAL`. Prices are
in display units (divided by `PRICE_SCALE`). NULL means "undefined" (for
example, too few points for an autocorrelation).

### Caveats

* **No traded volume exists in the feed.** It is depth-of-market only, so every
  "volume" is a **count of book updates** (hence `volume_events`), and the order
  flow is the tick-rule proxy the terminal labels as such.
* Statistics over one 5-minute slot use at most 30 points of 10-second data, so
  the per-slot volatility and autocorrelation are noisy. Aggregate them across
  slots rather than reading them one at a time.
* `vap_Wm_coverage` < 1 means the window reaches back before the feed's first
  tick, so the profile is built on less history than its name says.
  `export_flat.py --coverage` prints the feed's range.

## Running it

```bash
cd webapp
python export_flat.py                         # last 2 completed slots (cron)
python export_flat.py --start 2026-11-05      # backfill to now
python export_flat.py --symbol 136 --account 49208255 --start 2026-11-13
python export_flat.py --list                  # slots on record per instrument (no Kusto)
python export_flat.py --coverage              # feed's first/last tick
python export_flat.py --timeframe 1m --start 2026-11-05 --chunk-minutes 240   # 1-minute bars
```

`--chunk-minutes` (default 60) sets how many slots one Kusto query covers.
Exit code is 0 unless every instrument failed.

## 1-minute bars (`metrics_1m`)

`--timeframe 1m` (default `5m`) writes the same schema at one-minute resolution
to **`metrics_1m`**, same primary key and upsert semantics. The slot is
`[slot_end - 1 min, slot_end)` on 10-second buckets (6 per slot), and every
per-slot metric is computed exactly as for 5m. The VAP lookbacks keep the
1/4, 1/2, 1 ratios of the longest window, now 1 h: the columns are
`vap_15m_*`, `vap_30m_*`, `vap_60m_*` (no 120m/180m/240m). The timeframes are
defined in `export_flat.TIMEFRAMES`; the default 5m behaviour and `metrics_5m`
are unchanged. `--list --timeframe 1m` summarises the table.

There is no cron for 1m. Refresh it by hand (idempotent upsert), e.g. daily:

```bash
cd webapp
python export_flat.py --timeframe 1m --start 2026-11-19 --chunk-minutes 240
```

With `--start` the VAP `coverage` is measured against the table-wide first tick,
so for an instrument whose own stream starts later than the feed's first tick
the early windows read `coverage = 1` although they hold less history.

## Schedule (every 5 minutes, on the grid)

The job runs on the same grid as the VAP recorder and honours `VAP_GRID_DELAY`.

```cron
# /etc/cron.d/axioma-flat
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
0,5,10,15,20,25,30,35,40,45,50,55 * * * * azureuser set -a; . /home/azureuser/axioma-analytics/.env; set +a; cd /home/azureuser/axioma-analytics/webapp && /home/azureuser/axioma-analytics/venv/bin/python export_flat.py >> /var/log/axioma-flat.log 2>&1
```

Create the log once:
`sudo touch /var/log/axioma-flat.log && sudo chown azureuser /var/log/axioma-flat.log`.

## Loading it

```python
import sqlite3, pandas as pd
# Read-only, so a research session can never lock or alter the live record.
conn = sqlite3.connect("file:webapp/data/axioma.db?mode=ro", uri=True)
df = pd.read_sql("SELECT * FROM metrics_5m WHERE has_data = 1 ORDER BY slot_end", conn,
                 parse_dates=["slot_start", "slot_end"])
panel = df.pivot_table(index="slot_end", columns="standardized_name", values="log_return")
```

```bash
sqlite3 -readonly webapp/data/axioma.db \
  "SELECT slot_end, standardized_name, mid_close, realised_vol_pct FROM metrics_5m
    WHERE slot_end >= '2026-11-13' ORDER BY slot_end LIMIT 10"
```

To hand someone a file, dump the table:
`sqlite3 -header -csv -readonly webapp/data/axioma.db "SELECT * FROM metrics_5m" > metrics_5m.csv`.
