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

## Publishing to the webapp (`/research/`)

`studies/build_report.py` writes a static site to `research/site/` (gitignored): `index.html`,
`report.css`, `charts/`, `slides.html` and `data/`. Flask serves it at `/research/` behind the same
pilot login as the terminal. It's linked from the landing page header and footer and from the
terminal's **Research** tab. Signed-out visitors get the sign-in dialog and land back on the report.

```bash
venv/bin/python research/studies/run_studies.py
venv/bin/python research/studies/build_presentation.py
venv/bin/python research/studies/build_report.py   # live immediately, no restart needed
```
