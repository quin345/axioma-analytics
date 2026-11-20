# Findings: the 1-minute hypothesis screen (2026-11-20)

**Decision:** the 1-minute bar export (`metrics_1m`) was taken off cron on 2026-11-20.
The signals are real in the data but too small to pay for trading costs. The
`metrics_1m` table and the published page (`/research/screen-1m/`) stay as the
record. Nothing from this screen is traded.

## What was tested

The same 14 pre-registered ideas as the 5-minute screen, rescaled to 1-minute
bars with every 4-hour window shortened to 1 hour (POC/value-area windows
15/30/60 min, rolling z-window 60 bars, IC block 1 h; horizons 1/3/6/12 bars =
1/3/6/12 min). The hypothesis list was committed before any 1-minute data was
screened (`research/pipeline/hypotheses.py`, commit 3d3713d).

Data: KQL `agg_dom` exported to `metrics_1m`, 2026-11-05 to 2026-11-20, about
12,964 XAUUSD and 11,146 BTCUSD minutes with data on the older accounts, plus 300
minutes each for the account 48664595 streams (all in the holdout). Split at
2026-11-12 15:59 UTC with a 12-bar embargo.

## Results

Three ideas were picked on discovery data and confirmed once on the holdout:

| Pick | Horizon | Discovery IC (t) | Holdout IC (t) |
|---|---|---|---|
| `poc_reversion_1h` (price vs 1 h POC) | 1 min | +0.144 (19.9) | +0.138 (16.8) |
| `activity_predicts_vol` | 1 min | +0.135 (13.9) | +0.147 (12.1) |
| `reversal_1m` | 12 min | +0.103 (13.8) | +0.094 (10.3) |

Other signs: momentum (`momentum_3m`, `momentum_12m`) was strongly negative,
which points to short-term mean reversion. `range_reversal_12m` and
`poc_reversion_15m` passed discovery but were not among the top three.

## Economics: why it is not tradable

Taker test on the holdout (|z| > 1, enter at the touch, hold = horizon, half the
spread each way):

| Pick | Trades | Gross | Net | Cost per trade |
|---|---|---|---|---|
| `poc_reversion_1h` (1 min hold) | 4,212 | -0.16 bp | -0.84 bp | 0.68 bp |
| `reversal_1m` (12 min hold) | 644 | +0.54 bp | -0.14 bp | 0.68 bp |
| `activity_predicts_vol` | n/a (no direction) | | | 1.21x future volatility, top vs bottom signal fifth |

- A 1-minute move is about a basis point, so a fixed 0.68 bp cost removes most
  of it. The same cost is a smaller share of a 5-minute move, which is why the
  5-minute screen is closer to break-even.
- `poc_reversion_1h` ranks bars well overall (high IC) but the |z| > 1 trades
  lose even before cost: the extremes do not pay.
- `reversal_1m` is nearest to break-even and still loses.
- The only result that holds on its own is the volatility one, and a volatility
  forecast has no direction to trade.

## Cautions

- About two weeks of data, two instruments and one market period. Large
  t-values on short, overlapping minute data overstate the confidence.
- The first run included empty rows for streams that did not exist yet. After
  removing them and re-running, the same three picks were confirmed;
  `reversal_1m` moved from +0.05 to -0.14 bp net.
- `vap_*_coverage` is measured against the table-wide first tick, so it reads
  1 for streams that started late (documented in `docs/flat-export.md`).

## If this is revisited

- A filter that trades only when the expected move is well above the cost (for
  example `reversal_1m` with a larger |z| or a wider-than-usual move).
- Maker (limit) execution to cut the cost, noting the 5-minute work found that
  resting limits fill after adverse moves on cTrader.
- More history before trusting any 1-minute number.
- To restart collection: add back the 1-minute line from `docs/flat-export.md`
  (`export_flat.py --timeframe 1m --recent 5` every minute), after backfilling
  the gap by hand.
