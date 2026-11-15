"""End-to-end research run: load -> clean -> features -> signals -> evaluate -> backtest.

    venv/bin/python research/run_pipeline.py [--db PATH] [--out DIR] [--symbols EURUSD,XAUUSD]
                                             [--start ISO] [--end ISO] [--threshold 1.0]
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

import config  # noqa: E402
from pipeline import backtest, clean, data, evaluate, features, signals  # noqa: E402


def run(db, out, symbols=None, start=None, end=None, threshold=1.0, log=print):
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    raw = data.load_metrics_5m(db, symbols, start, end)
    log(f"loaded {len(raw)} rows from {db}")
    if raw.empty:
        log("no data; nothing to do")
        return {}
    bars = clean.clean_metrics(raw)
    feats = signals.add_signals(features.build_features(bars))
    sigs, targets = signals.signal_columns(feats), features.target_columns(feats)
    log(f"{feats.groupby(features.INSTRUMENT).ngroups} instruments, {len(feats)} grid bars, "
        f"{int(feats['has_data'].sum())} with data")

    ic = evaluate.ic_table(feats, sigs, targets)
    quant = pd.concat([evaluate.quantile_returns(feats, s, t) for s in sigs for t in targets], ignore_index=True)
    decay = pd.DataFrame([evaluate.signal_autocorr(feats, s) for s in sigs])
    bt = [backtest.backtest(feats, s, threshold=threshold) for s in sigs]
    summary = pd.concat([b[0] for b in bt], ignore_index=True)
    equity = pd.concat([b[1] for b in bt], ignore_index=True)

    feats.to_parquet(out / "features.parquet", index=False)
    equity.to_parquet(out / "equity.parquet", index=False)
    ic.to_csv(out / "ic.csv", index=False)
    quant.to_csv(out / "quantile_returns.csv", index=False)
    decay.to_csv(out / "signal_autocorr.csv", index=False)
    summary.to_csv(out / "backtest_summary.csv", index=False)

    pooled = ic[(ic["instrument"] == "ALL") & (ic["target"] == "fwd_ret_1")]
    log("\npooled IC vs next-bar return:\n" + pooled[["signal", "n", "ic", "rank_ic", "t_stat", "hit_rate"]]
        .to_string(index=False, float_format="%.4f"))
    log(f"\nbacktest (|signal| > {threshold}, after half-spread costs):\n"
        + summary.groupby("signal")[["total_log_ret", "sharpe_ann", "exposure"]].mean()
        .to_string(float_format="%.4f"))
    log(f"\nwrote outputs to {out}")
    return {"features": feats, "ic": ic, "quantiles": quant, "backtest": summary}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(config.db_path()))
    p.add_argument("--out", default=str(config.OUTPUT_DIR))
    p.add_argument("--symbols", help="comma-separated standardized or broker symbol names")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--threshold", type=float, default=1.0, help="min |signal| to take a position")
    a = p.parse_args(argv)
    run(a.db, a.out, a.symbols.split(",") if a.symbols else None, a.start, a.end, a.threshold)


if __name__ == "__main__":
    main()
