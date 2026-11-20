# Research pipeline

Quant research over the local record store `webapp/data/axioma.db`, mainly the
5-minute table `metrics_5m` (see `docs/flat-export.md`). It is separate from the
webapp: it never imports Flask or Kusto and opens the database **read-only**
(`mode=ro`), so it cannot block the cron jobs that write to it.

## Layout

| Path | Purpose |
| --- | --- |
| `config.py` | `DB_PATH` (env var, then `.env`, then `webapp/data/axioma.db`) and the output folder |
| `pipeline/data.py` | Loaders: `load_metrics_5m`, `load_symbols`, `load_vap` (VAP levels, loaded on request) |
| `pipeline/clean.py` | UTC timestamps, de-duplication, regular 5-minute grid per instrument; `has_data=0` bars become NaN |
| `pipeline/features.py` | Features that use only past data, plus `fwd_ret_{1,3,6,12}` targets (forward log returns over that many 5-minute bars) |
| `pipeline/signals.py` | Candidate signals (`sig_*`), each a trailing rolling z-score |
| `pipeline/evaluate.py` | IC / rank IC / t-stat / hit rate, quantile returns, signal autocorrelation |
| `pipeline/backtest.py` | Long/short backtest on the next-bar return, paying half the spread per trade |
| `run_pipeline.py` | End-to-end command-line run that writes to `output/` |
| `notebooks/01_data_exploration.ipynb` | Coverage and distribution checks |
| `tests/` | `unittest` tests on a synthetic database (do not need the real data) |

An instrument is (`ctid_trader_account`, `symbol_id`), the same as the `metrics_5m` primary key.

## Usage

```bash
venv/bin/pip install -r research/requirements.txt    # already in venv/
venv/bin/python research/run_pipeline.py                    # all instruments
venv/bin/python research/run_pipeline.py --symbols XAUUSD --start 2026-11-10 --threshold 1.5
venv/bin/python -m unittest discover -s research/tests -t research
```

Outputs (gitignored) go to `research/output/` (override with `--out` or `RESEARCH_OUTPUT_DIR`):
`features.parquet`, `equity.parquet`, `ic.csv`, `quantile_returns.csv`,
`signal_autocorr.csv`, `backtest_summary.csv`.

## Rules that keep results honest

- A feature at `slot_end` t uses only bars with `slot_end <= t`. A test changes future prices and checks that past features stay the same.
- Gaps are kept as NaN rows on the grid, so `shift(1)` always means 5 minutes earlier.
- The position decided at t earns `fwd_ret_1`; `fwd_ret_*` columns are targets only, never inputs.
- Costs: each change in position pays `rel_spread_mean / 2`. There is no slippage or market-impact model.

## Adding a signal

Add a function to `SIGNALS` in `pipeline/signals.py` that takes the feature frame and returns a
Series where positive means price expected up. If it needs a new input, add that feature in
`features.py` using only past data. Re-run the pipeline and compare it in `ic.csv`.

## Caveats

The history is short (a few thousand 5-minute bars on a handful of instruments), so treat IC and
Sharpe figures as exploratory. Annualisation assumes 24h trading (252 × 288 bars).

## Hypothesis studies (`studies/`)

| File | Purpose |
| --- | --- |
| `pipeline/strategies.py` | Trade-level simulators: POC reversion (maker/taker, target, stop, timeout) and threshold factor trades, with conservative fill rules |
| `studies/run_studies.py` | H1 POC reversion, H2 order-flow momentum, H3 short-term reversal: in-sample tuning → frozen out-of-sample test, random-direction benchmark, zero-cost and trend-removed checks; writes charts + `results.json` |
| `studies/build_presentation.py` | Builds a self-contained HTML slide deck (`output/studies/presentation.html`) from `results.json` |

```bash
venv/bin/python research/studies/run_studies.py
venv/bin/python research/studies/build_presentation.py
# open research/output/studies/presentation.html  (← → to navigate, F = full screen, Ctrl+P = PDF)
```

Slide numbers are read from `results.json`, so the deck updates with the data. The written interpretation
(verdicts, explanations) was written for the 2026-11-05 → 11-15 run; review it after re-running on new data.

## Hypothesis screen (`run_screen.py`)

Picks the top N hypotheses from a pre-registered set, honestly: choose on one stretch
of time, then test the picks once on a later stretch that played no part in choosing them.

| File | Purpose |
| --- | --- |
| `pipeline/hypotheses.py` | The hypothesis set: claim, mechanism, signal (positive = predicts a higher target), target (`ret` or `vol`), optional control, baseline flag |
| `pipeline/screen.py` | Split with embargo, block rank IC (partial when controlled), BH FDR, top-N selection, holdout confirmation, trade/volatility economics |
| `run_screen.py` | End-to-end run; writes `output/screen/` |

```bash
# 1. Pull the data from KQL into metrics_5m (webapp side; the research code never talks to Kusto)
cd webapp && ../venv/bin/python export_flat.py --start 2026-11-05T08:00 --chunk-minutes 360 && cd ..
# 2. Screen
venv/bin/python research/run_screen.py            # --split 0.6 --top 3 --fdr 0.10 --block-hours 4
```

Outputs: `report.md` (the top N with discovery vs holdout evidence, then the full ranking),
`top_hypotheses.json`, `ranking.csv`, `discovery_scores.csv`, and `holdout_scores_reference.csv`
(every hypothesis on holdout, **for reference only**: picking from it would defeat the holdout).

Protocol, in order:

1. One chronological cut for all instruments (default 60% of bars with data), with a 1-hour embargo
   so no discovery target reaches into holdout.
2. Spearman IC per 4-hour block and t across blocks, because forward returns overlap. Volatility
   hypotheses use the partial rank IC with current volatility held fixed. p-values are one-sided in
   the pre-registered direction: a strong opposite result counts against a hypothesis.
3. Per hypothesis, its best horizon. A hypothesis passes discovery if BH q <= 0.10 over all
   hypothesis x horizon pairs **and** the IC has the predicted sign on every instrument. Passers rank
   first, then by t. Baselines (`vol_clustering`) are scored but never picked.
4. The picks are scored once on holdout. "confirmed" means one-sided p < 0.05 / N.
5. Return picks get a taker test on holdout (|z| > 1, hold = horizon, half spread each way); volatility
   picks get future vol in the top vs bottom signal quintile within current-vol terciles.

What the feed supports: the LPs quote fixed, symmetric ladder sizes, so depth imbalance and VWAP skew are
almost always zero and book-pressure hypotheses are left out. Signals come from the mid path, the spread,
the quote-update rate and timing. To add a hypothesis, append to `HYPOTHESES` **before** looking at results.

## Publishing to the webapp (`/research/`)

`studies/build_report.py` writes a static site to `research/site/` (gitignored): `index.html`,
`report.css`, `charts/`, `slides.html` and `data/`. Flask serves it at `/research/` behind the same
pilot login as the terminal. It's linked from the landing page header and footer and from the
terminal's **Research** tab. Signed-out visitors get the sign-in dialog and land back on the report.

The report leads with the hypothesis screen (`output/screen/`: top picks, full ranking, method, downloads
copied to `data/screen/`). `results.json` from `run_studies.py` is optional: if it exists the older POC
study sections follow the screen; if not, the page is the screen plus the panel guide. The build exits
non-zero and leaves the existing site untouched if neither input exists.

```bash
venv/bin/python research/run_screen.py
venv/bin/python research/studies/build_report.py   # live immediately, no restart needed
# optional, adds the older study sections and slide deck:
venv/bin/python research/studies/run_studies.py
venv/bin/python research/studies/build_presentation.py
```
