# Hypothesis screen

Split at **2026-11-08 18:05:00+00:00** (60% of bars with data in discovery; 12-bar embargo). Rank IC per 4 h block, t across blocks; BH FDR 0.1 on discovery; top 3 tested once on holdout (Bonferroni).

## Data

| Instrument | Bars with data | Days | First | Last |
|---|---|---|---|---|
| AAA | 1800 | 7 | 2026-11-05 00:05:00+00:00 | 2026-11-11 06:00:00+00:00 |
| BBB | 1800 | 7 | 2026-11-05 00:05:00+00:00 | 2026-11-11 06:00:00+00:00 |

## Top 3

### 1. `reversal_5m` (5 min, ret) - confirmed

**Claim:** A 5-minute move partly reverses over the next bars.  
**Mechanism:** LP quotes overshoot on bursts of updates, then re-centre.

| | IC | t | p (one-sided) | blocks |
|---|---|---|---|---|
| Discovery | +0.347 | 16.64 | 0.0000 | 46 |
| Holdout | +0.347 | 12.84 | 0.0000 | 32 |

Discovery q-value 0.0000; sign consistent across instruments: True; passed discovery: True. Holdout IC by instrument: AAA +0.300, BBB +0.395.

Taker test on holdout (|z| > 1, hold 5 min): 433 trades, gross +5.40 bp, net +5.39 bp per trade (cost 0.01 bp).

Robustness (holdout): IC holding the last bar's return fixed n/a (t n/a); IC skipping one bar -0.111 (t -3.7).

### 2. `range_reversal_1h` (5 min, ret) - confirmed

**Claim:** Price near the top of its 1-hour range falls back, near the bottom rises.  
**Mechanism:** Range-bound intraday trading; extremes attract liquidity on the other side.

| | IC | t | p (one-sided) | blocks |
|---|---|---|---|---|
| Discovery | +0.242 | 13.94 | 0.0000 | 46 |
| Holdout | +0.266 | 10.17 | 0.0000 | 32 |

Discovery q-value 0.0000; sign consistent across instruments: True; passed discovery: True. Holdout IC by instrument: AAA +0.232, BBB +0.301.

Taker test on holdout (|z| > 1, hold 5 min): 634 trades, gross +3.15 bp, net +3.14 bp per trade (cost 0.01 bp).

Robustness (holdout): IC holding the last bar's return fixed +0.098 (t 3.7); IC skipping one bar -0.016 (t -0.9).

### 3. `micro_trend_continuation` (5 min, ret) - failed

**Claim:** When 10-second returns inside a slot trend (positive autocorrelation), the slot's move continues.  
**Mechanism:** Trending micro-structure marks an informed move rather than noise.

| | IC | t | p (one-sided) | blocks |
|---|---|---|---|---|
| Discovery | +0.031 | 1.30 | 0.0965 | 46 |
| Holdout | -0.018 | -0.75 | 0.7733 | 32 |

Discovery q-value 0.3860; sign consistent across instruments: True; passed discovery: False. Holdout IC by instrument: AAA -0.053, BBB +0.017.

Taker test on holdout (|z| > 1, hold 5 min): 309 trades, gross +0.13 bp, net +0.12 bp per trade (cost 0.01 bp).

Robustness (holdout): IC holding the last bar's return fixed -0.023 (t -0.8); IC skipping one bar +0.026 (t 1.0).

## Full discovery ranking

| Rank | Hypothesis | Target | Horizon | IC | t | q | Same sign everywhere | Passed | Duplicate of | Picked |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `reversal_5m` | ret | 5 min | +0.347 | 16.64 | 0.0000 | True | True |  | True |
| 2 | `range_reversal_1h` | ret | 5 min | +0.242 | 13.94 | 0.0000 | True | True |  | True |
| 3 | `micro_trend_continuation` | ret | 5 min | +0.031 | 1.30 | 0.3860 | True | False |  | True |
| 4 | `tick_flow` | ret | 60 min | +0.028 | 1.21 | 0.4044 | True | False |  | False |
| 5 | `cross_asset_lead` | ret | 60 min | +0.004 | 0.32 | 0.7949 | True | False |  | False |
| 6 | `tick_speed_predicts_vol` | vol | 30 min | +0.004 | 0.12 | 0.8810 | False | False |  | False |
| 7 | `activity_predicts_vol` | vol | 5 min | +0.002 | 0.09 | 0.8810 | False | False |  | False |
| 8 | `momentum_15m` | ret | 30 min | -0.122 | -4.89 | 1.0000 | False | False | range_reversal_1h (rho -0.73) | False |
| 9 | `momentum_1h` | ret | 30 min | -0.237 | -6.26 | 1.0000 | False | False | range_reversal_1h (rho -0.82) | False |
| 10 | `poc_reversion_1h` | ret | 5 min | n/a | n/a | n/a | False | False |  | False |
| 11 | `poc_reversion_4h` | ret | 5 min | n/a | n/a | n/a | False | False |  | False |
| 12 | `spread_predicts_vol` | vol | 5 min | n/a | n/a | n/a | False | False |  | False |
| 13 | `wide_spread_reversal` | ret | 5 min | n/a | n/a | n/a | False | False |  | False |

Baselines (scored, never picked): `vol_clustering`. See `discovery_scores.csv`.
