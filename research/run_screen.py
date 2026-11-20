"""Hypothesis screen over metrics_5m or metrics_1m: score every pre-registered
hypothesis on a discovery sample, pick the top N, then test only those once on
held-out data.

    venv/bin/python research/run_screen.py [--timeframe {5m,1m}] [--db PATH] [--out DIR]
                                           [--split 0.6] [--top 3] [--fdr 0.10] [--block-hours H]

--timeframe 5m (default) reads metrics_5m and writes research/output/screen; 1m reads
metrics_1m and writes research/output/screen_1m. --block-hours defaults to the
timeframe's aggregation window (4 h on 5m, 1 h on 1m); see pipeline/timeframe.py.

The data comes from KQL via webapp/export_flat.py (metrics_5m / metrics_1m in axioma.db);
this script only reads the local database, read-only. See pipeline/screen.py
for the protocol and pipeline/hypotheses.py for the hypotheses.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from pipeline import INSTRUMENT, clean, data, features  # noqa: E402
from pipeline import screen, timeframe  # noqa: E402
from pipeline.hypotheses import hypotheses_for  # noqa: E402


def _coverage(df):
    rows = []
    for _, g in df.groupby(INSTRUMENT):
        live = g[g["has_data"].astype(bool)]
        rows.append({"instrument": g["standardized_name"].dropna().iloc[0] if g["standardized_name"].notna().any() else "?",
                     "bars_with_data": len(live),
                     "first": str(live["slot_end"].min()), "last": str(live["slot_end"].max()),
                     "days_with_data": int(live["slot_end"].dt.floor("D").nunique())})
    return rows


def default_out(tf):
    return config.OUTPUT_DIR / ("screen" if tf.key == "5m" else f"screen_{tf.key}")


def run(db, out, frac=0.6, top=3, fdr=0.10, block_hours=None, log=print, tf=timeframe.TF_5M):
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    block_hours = tf.block_hours if block_hours is None else block_hours
    hyps = hypotheses_for(tf)
    raw = data.load_metrics(db, tf)
    if raw.empty:
        log(f"no {tf.table} rows in {db}; run webapp/export_flat.py --start ... first")
        return {}
    feats = screen.add_hypothesis_signals(features.build_features(clean.clean_metrics(raw, tf), tf=tf), hyps)
    block = pd.Timedelta(hours=block_hours)
    disc, hold, cut = screen.split(feats, frac, tf=tf)
    coverage = _coverage(feats)
    log(f"loaded {len(raw)} rows; split at {cut} "
        f"(discovery {int(disc['has_data'].sum())} bars with data, holdout {int(hold['has_data'].sum())})")

    disc_table = screen.score(disc, hyps, block=block, tf=tf)
    ranked, picks = screen.select(disc_table, hyps, top=top, fdr=fdr, discovery=disc)
    hold_table = screen.score(hold, hyps, block=block, tf=tf)  # every hypothesis, for reference only
    confirmed = screen.confirm(hold, picks, hyps, block=block, tf=tf)
    econ = screen.economics(hold, picks, hyps, tf=tf)
    robust = screen.robustness(hold, picks, disc, hyps, block=block, tf=tf)
    final = (picks.merge(confirmed, on=["hypothesis", "horizon_bars"]).merge(econ, on="hypothesis")
             .merge(robust, on="hypothesis"))

    disc_table.to_csv(out / "discovery_scores.csv", index=False)
    hold_table.to_csv(out / "holdout_scores_reference.csv", index=False)
    ranked.to_csv(out / "ranking.csv", index=False)
    result = {
        "split": {"cut": str(cut), "fraction": frac, "embargo_bars": tf.embargo_bars,
                  "block_hours": block_hours, "fdr": fdr, "top": top},
        "timeframe": {**tf.to_dict(), "hypotheses": len(hyps), "baselines": sum(h.baseline for h in hyps)},
        "coverage": coverage,
        "picks": _records(final, hyps, tf),
    }
    (out / "top_hypotheses.json").write_text(json.dumps(result, indent=2, default=_json))
    (out / "report.md").write_text(report(result, ranked, hyps, tf))

    log("\nranking on discovery (best horizon per hypothesis):")
    log(ranked[["rank", "hypothesis", "target", "horizon_bars", "ic", "t", "q", "sign_consistent",
                "passed_discovery", "redundant_with", "picked"]].to_string(index=False, float_format="%.4f"))
    log("\ntop picks, tested once on holdout:")
    cols = ["hypothesis", "horizon_bars", "ic", "holdout_ic", "holdout_t", "holdout_p", "verdict"]
    log(final[cols].to_string(index=False, float_format="%.4f"))
    log(f"\nwrote {out}")
    return result


def _json(v):
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return str(v)


def _records(df, hyps, tf):
    by_key = {h.key: h for h in hyps}
    recs = df.replace({np.nan: None}).to_dict(orient="records")
    for r in recs:
        h = by_key[r["hypothesis"]]
        r.update({"statement": h.statement, "mechanism": h.mechanism, "control": h.control,
                  "horizon_minutes": tf.minutes(r["horizon_bars"])})
    return recs


def _f(v, fmt="{:+.3f}"):
    return "n/a" if v is None or (isinstance(v, float) and not np.isfinite(v)) else fmt.format(v)


def report(result, ranked, hyps=None, tf=timeframe.TF_5M):
    s = result["split"]
    hyps = hyps or hypotheses_for(tf)
    lines = ["# Hypothesis screen" + ("" if tf.key == "5m" else f" ({tf.key} bars)"), "",
             f"Split at **{s['cut']}** ({int(s['fraction'] * 100)}% of bars with data in discovery; "
             f"{s['embargo_bars']}-bar embargo). Rank IC per {s['block_hours']} h block, t across blocks; "
             f"BH FDR {s['fdr']} on discovery; top {s['top']} tested once on holdout "
             f"(Bonferroni).", "", "## Data", "",
             "| Instrument | Bars with data | Days | First | Last |", "|---|---|---|---|---|"]
    lines += [f"| {c['instrument']} | {c['bars_with_data']} | {c['days_with_data']} | {c['first']} | {c['last']} |"
              for c in result["coverage"]]
    lines += ["", f"## Top {s['top']}", ""]
    for i, p in enumerate(result["picks"], 1):
        lines += [f"### {i}. `{p['hypothesis']}` ({p['horizon_minutes']} min, {p['target']}) - {p['verdict']}", "",
                  f"**Claim:** {p['statement']}  ",
                  f"**Mechanism:** {p['mechanism']}" + (f"  \n**Controls for:** `{p['control']}`" if p['control'] else ""),
                  "",
                  "| | IC | t | p (one-sided) | blocks |", "|---|---|---|---|---|",
                  f"| Discovery | {_f(p['ic'])} | {_f(p['t'], '{:.2f}')} | {_f(p['p'], '{:.4f}')} | {p['blocks']} |",
                  f"| Holdout | {_f(p['holdout_ic'])} | {_f(p['holdout_t'], '{:.2f}')} | "
                  f"{_f(p['holdout_p'], '{:.4f}')} | {p['holdout_blocks']} |", "",
                  f"Discovery q-value {_f(p.get('q'), '{:.4f}')}; sign consistent across instruments: "
                  f"{p['sign_consistent']}; passed discovery: {p['passed_discovery']}. "
                  f"Holdout IC by instrument: "
                  + ", ".join(f"{k} {_f(v)}" for k, v in (p['holdout_ic_by_instrument'] or {}).items()) + "."]
        if p["target"] == "ret":
            lines += ["", f"Taker test on holdout (|z| > 1, hold {p['horizon_minutes']} min): "
                      f"{_f(p.get('trades'), '{:.0f}')} trades, gross {_f(p.get('gross_avg_bps'), '{:+.2f}')} bp, "
                      f"net {_f(p.get('net_avg_bps'), '{:+.2f}')} bp per trade "
                      f"(cost {_f(p.get('cost_per_trade_bps'), '{:.2f}')} bp).", "",
                      f"Robustness (holdout): IC holding the last bar's return fixed "
                      f"{_f(p.get('ic_ctrl_last_return'))} (t {_f(p.get('t_ctrl_last_return'), '{:.1f}')}); "
                      f"IC skipping one bar {_f(p.get('ic_skip_one_bar'))} "
                      f"(t {_f(p.get('t_skip_one_bar'), '{:.1f}')})."]
        else:
            lines += ["", f"Future volatility, top vs bottom signal quintile"
                      + (" within current-vol terciles" if p["control"] else "")
                      + f": {_f(p.get('vol_ratio_top_vs_bottom'), '{:.2f}')}x.", "",
                      f"Robustness (holdout): IC holding current and hour-of-day volatility fixed "
                      f"{_f(p.get('ic_ctrl_vol_and_hour'))} (t {_f(p.get('t_ctrl_vol_and_hour'), '{:.1f}')})."]
        lines.append("")
    lines += ["## Full discovery ranking", "",
              "| Rank | Hypothesis | Target | Horizon | IC | t | q | Same sign everywhere | Passed | Duplicate of | Picked |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in ranked.iterrows():
        dup = r.get("redundant_with")
        lines.append(f"| {r['rank']} | `{r['hypothesis']}` | {r['target']} | {tf.minutes(r['horizon_bars'])} min | "
                     f"{_f(r['ic'])} | {_f(r['t'], '{:.2f}')} | {_f(r['q'], '{:.4f}')} | "
                     f"{r['sign_consistent']} | {r['passed_discovery']} | {dup if isinstance(dup, str) else ''} | "
                     f"{r['picked']} |")
    base = [h for h in hyps if h.baseline]
    if base:
        lines += ["", "Baselines (scored, never picked): " + ", ".join(f"`{h.key}`" for h in base)
                  + ". See `discovery_scores.csv`."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--timeframe", choices=sorted(timeframe.TIMEFRAMES), default="5m")
    p.add_argument("--db", default=str(config.db_path()))
    p.add_argument("--out", default=None, help="default: output/screen (5m) or output/screen_1m (1m)")
    p.add_argument("--split", type=float, default=0.6, help="fraction of bars in discovery")
    p.add_argument("--top", type=int, default=3)
    p.add_argument("--fdr", type=float, default=0.10)
    p.add_argument("--block-hours", type=float, default=None, help="default: the timeframe's window (5m: 4, 1m: 1)")
    a = p.parse_args(argv)
    tf = timeframe.get(a.timeframe)
    run(a.db, a.out or default_out(tf), a.split, a.top, a.fdr, a.block_hours, tf=tf)


if __name__ == "__main__":
    main()
