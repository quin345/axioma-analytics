"""Hypothesis studies: (H1) POC mean reversion as maker/scalping strategy,
(H2) order-flow momentum, (H3) short-term reversal.

Protocol per hypothesis: pick parameters on the first half of the history
(in-sample), freeze them, evaluate on the second half (out-of-sample), compare
with a random-direction benchmark on the same trade times, and check cost
sensitivity. Writes charts + results.json to research/output/studies/.

    venv/bin/python research/studies/run_studies.py
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from study_lib import (COLORS, S, config, decile_returns, ic_by_horizon, load_features, plt,  # noqa: E402
                       poc_sweep, random_benchmark, signal_sweep, split_time, zero_cost)

OUT = config.OUTPUT_DIR / "studies"
MIN_TRADES = 40  # ignore in-sample configs with too few trades to rank


def pick_best(sweep):
    ok = sweep[sweep["trades"] >= MIN_TRADES]
    return ok.sort_values("avg_bps", ascending=False).iloc[0]


def save(fig, name):
    path = OUT / f"{name}.png"
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path.name


def equity_chart(trades_is, trades_oos, cut, title, name):
    fig, ax = plt.subplots(figsize=(10, 4.2))
    for t, lab, col in ((trades_is, "in-sample (parameters chosen here)", COLORS["muted"]),
                        (trades_oos, "out-of-sample (unseen data)", COLORS["accent"])):
        if not t.empty:
            t = t.sort_values("exit_time")
            ax.plot(t["exit_time"], t["pnl_bps"].cumsum() + (0 if lab.startswith("in") else
                    trades_is["pnl_bps"].sum() if not trades_is.empty else 0), color=col, lw=2, label=lab)
    ax.axvline(cut, color=COLORS["accent2"], ls="--", lw=1.2)
    ax.axhline(0, color=COLORS["muted"], lw=0.8)
    ax.set_title(title)
    ax.set_ylabel("cumulative net PnL (bps)")
    ax.legend(loc="best")
    fig.autofmt_xdate()
    return save(fig, name)


def benchmark_chart(sims, actual, title, name):
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    ax.hist(sims, bins=40, color=COLORS["muted"], alpha=0.8, label="random direction, same trades")
    ax.axvline(actual, color=COLORS["pos"] if actual > 0 else COLORS["neg"], lw=3, label=f"strategy: {actual:+.2f} bps")
    ax.axvline(0, color=COLORS["text"], lw=0.8)
    ax.set_title(title)
    ax.set_xlabel("average net PnL per trade (bps)")
    ax.legend(loc="upper left", fontsize=9)
    return save(fig, name)


def bars_chart(labels, values, title, ylabel, name, err=None):
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    cols = [COLORS["pos"] if v > 0 else COLORS["neg"] for v in values]
    ax.bar(labels, values, color=cols, yerr=err, ecolor=COLORS["text"], capsize=3)
    ax.axhline(0, color=COLORS["text"], lw=0.8)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    return save(fig, name)


def ic_chart(ics, name):
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    width = 0.8 / len(ics)
    for k, (lab, df) in enumerate(ics.items()):
        x = np.arange(len(df)) + (k - (len(ics) - 1) / 2) * width
        ax.bar(x, df["rank_ic"], width, label=lab)
    ax.axhspan(-0.03, 0.03, color=COLORS["muted"], alpha=0.15, label="≈ noise band (|t|<2)")
    ax.set_xticks(np.arange(len(df)), [f"{m} min" for m in df["horizon_min"]])
    ax.axhline(0, color=COLORS["text"], lw=0.8)
    ax.set_title("Predictive power (rank IC) by horizon")
    ax.set_ylabel("rank correlation with future return")
    ax.legend(fontsize=9)
    return save(fig, name)


def sweep_heatmap(sweep, name):
    piv = sweep.groupby(["mode", "window"])["avg_bps"].median().unstack()
    fig, ax = plt.subplots(figsize=(6.5, 3.2))
    lim = np.nanmax(np.abs(piv.to_numpy()))
    im = ax.imshow(piv.to_numpy(), cmap="RdYlGn", vmin=-lim, vmax=lim, aspect="auto")
    ax.set_xticks(range(piv.shape[1]), [f"{w}m VAP" for w in piv.columns])
    ax.set_yticks(range(piv.shape[0]), piv.index)
    for (r, c), v in np.ndenumerate(piv.to_numpy()):
        ax.text(c, r, f"{v:+.2f}", ha="center", va="center", color="black", fontweight="bold")
    ax.grid(False)
    ax.set_title("POC: median net bps/trade across 27 settings each")
    fig.colorbar(im, ax=ax, shrink=0.8)
    return save(fig, name)


def robustness(te, key):
    """Share of the in-sample grid that is profitable out-of-sample (after trend removal)."""
    rows = []
    if key == "poc":
        grid = poc_sweep(te)
        return {"oos_profitable_share": float((grid["avg_bps"] > 0).mean())}
    col = {"H2_flow": "flow_signal", "H3_reversal": "reversal_signal"}[key]
    for th in (0.5, 1.0, 1.5, 2.0):
        for h in (1, 3, 6, 12):
            t = drift_adjusted(te, S.simulate_signal(te, col, th, h))
            rows.append(S.summarize(t)["avg_bps"])
    rows = np.array([r for r in rows if r is not None and np.isfinite(r)])
    return {"oos_profitable_share_drift_adjusted": float((rows > 0).mean()) if len(rows) else None}


def _clean(d):
    return {k: (None if isinstance(v, float) and not np.isfinite(v) else
                v.item() if hasattr(v, "item") else v) for k, v in d.items()}


def drift_adjusted(df, trades):
    """Remove each instrument's average 5-minute drift over `df` from every trade:
    a short in a falling market should not get credit for the trend itself."""
    if trades.empty:
        return trades
    d = df[df["has_data"]]
    mu = d.groupby("standardized_name")["ret_1"].mean() * 1e4  # bps per bar
    t = trades.copy()
    bars = ((t["exit_time"] - t["entry_time"]) / pd.Timedelta(minutes=5)).clip(lower=1)
    t["pnl_bps"] = t["pnl_bps"] - t["side"] * t["instrument"].map(mu).fillna(0) * bars
    return t


def evaluate(f, tr, te, cut, key, title, run_is, run_oos, best_params):
    """Common out-of-sample / benchmark / cost / drift evaluation for one strategy."""
    t_is, t_oos = run_is(tr), run_oos(te)
    s_drift = S.summarize(drift_adjusted(te, t_oos), "out-of-sample, trend removed")
    sides = t_oos.groupby("side")["pnl_bps"].mean().to_dict() if not t_oos.empty else {}
    s_is, s_oos = S.summarize(t_is, "in-sample"), S.summarize(t_oos, "out-of-sample")
    s_gross = S.summarize(run_oos(zero_cost(te)), "out-of-sample, zero spread")
    sims = random_benchmark(te, t_oos) if not t_oos.empty else np.array([])
    p_rand = float((sims >= s_oos["avg_bps"]).mean()) if len(sims) else None
    charts = {
        "equity": equity_chart(t_is, t_oos, cut, f"{title}: cumulative net PnL", f"{key}_equity"),
        "benchmark": benchmark_chart(sims, s_oos["avg_bps"] or 0, f"{title}: vs random direction (OOS)",
                                     f"{key}_benchmark") if len(sims) else None,
    }
    by_inst = [_clean(S.summarize(g, inst)) for inst, g in t_oos.groupby("instrument")] if not t_oos.empty else []
    return {"params": _clean(best_params), "in_sample": _clean(s_is), "out_of_sample": _clean(s_oos),
            "out_of_sample_zero_cost": _clean(s_gross), "oos_by_instrument": by_inst,
            "out_of_sample_drift_adjusted": _clean(s_drift),
            "oos_long_avg_bps": sides.get(1), "oos_short_avg_bps": sides.get(-1),
            "oos_robustness": robustness(te, key),
            "random_benchmark_p": p_rand,
            "random_benchmark_mean": float(sims.mean()) if len(sims) else None, "charts": charts}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    f = load_features()
    tr, te, cut = split_time(f)
    d = f[f["has_data"]]
    res = {"data": {
        "instruments": sorted(d["standardized_name"].unique().tolist()),
        "bars_with_data": int(len(d)), "start": str(d["slot_end"].min()), "end": str(d["slot_end"].max()),
        "split": str(cut),
        "median_spread_bps": {k: float(v * 1e4) for k, v in d.groupby("standardized_name")["rel_spread_mean"].median().items()},
        "median_abs_5m_move_bps": {k: float(v * 1e4) for k, v in d.groupby("standardized_name")["ret_1"].apply(lambda s: s.abs().median()).items()},
    }}

    # H1 POC
    sw = poc_sweep(tr)
    sw_gross = poc_sweep(zero_cost(tr))
    sw.to_csv(OUT / "poc_sweep_in_sample.csv", index=False)
    best = pick_best(sw)
    p = S.PocParams(**{k: best[k] for k in ("window", "entry_k", "stop_k", "hold", "mode")})
    p = S.PocParams(int(p.window), float(p.entry_k), float(p.stop_k), int(p.hold), str(p.mode))
    run = lambda df: S.simulate_poc(df, p)  # noqa: E731
    h1 = evaluate(f, tr, te, cut, "poc", "POC reversion", run, run, S.params_dict(p))
    # maker-only view: the "market-making" variant at its best in-sample setting
    bm = pick_best(sw[sw["mode"] == "maker"])
    pm = S.PocParams(int(bm.window), float(bm.entry_k), float(bm.stop_k), int(bm.hold), "maker")
    h1["maker_variant"] = {"params": S.params_dict(pm),
                           "in_sample": _clean(S.summarize(S.simulate_poc(tr, pm))),
                           "out_of_sample": _clean(S.summarize(S.simulate_poc(te, pm)))}
    h1["sweep"] = {"configs": int(len(sw)), "profitable_share_net": float((sw["avg_bps"] > 0).mean()),
                   "profitable_share_gross": float((sw_gross["avg_bps"] > 0).mean()),
                   "median_net_bps": float(sw["avg_bps"].median()),
                   "median_gross_bps": float(sw_gross["avg_bps"].median()),
                   "by_mode": {m: float(g["avg_bps"].median()) for m, g in sw.groupby("mode")}}
    h1["charts"]["heatmap"] = sweep_heatmap(sw, "poc_sweep_heatmap")
    dec = decile_returns(f, "poc_dist_240", "fwd_ret_6")
    h1["charts"]["deciles"] = bars_chart(
        [str(int(b)) for b in dec["bucket"]], dec["mean"].tolist(),
        "Next 30 min return by distance from 240m POC\n(1 = far below POC … 10 = far above)",
        "excess return (bps)", "poc_deciles", err=dec["sem"].tolist())
    res["H1_poc"] = h1

    # H2 flow momentum, H3 short-term reversal
    ics = {}
    for key, col, title in (("H2_flow", "flow_signal", "Order-flow momentum"),
                            ("H3_reversal", "reversal_signal", "Short-term reversal")):
        sw = signal_sweep(tr, col)
        sw.to_csv(OUT / f"{key}_sweep_in_sample.csv", index=False)
        b = pick_best(sw)
        th, hold = float(b["threshold"]), int(b["hold"])
        run = lambda df, c=col, th=th, hold=hold: S.simulate_signal(df, c, th, hold)  # noqa: E731
        res[key] = evaluate(f, tr, te, cut, key, title, run, run, {"threshold_std": th, "hold_bars": hold})
        if key == "H2_flow":
            t = S.simulate_signal(te, col, th, hold)
            br = t.groupby(["instrument", "side"])["pnl_bps"].sum()
            labs = [f"{i}\n{'long' if s > 0 else 'short'}" for i, s in br.index]
            res[key]["charts"]["breakdown"] = bars_chart(
                labs, br.tolist(), "Where the out-of-sample profit came from", "total net bps",
                "H2_flow_breakdown")
            pnl = t["pnl_bps"].sort_values()
            res[key]["top5_share_of_total"] = float(pnl.tail(5).sum() / pnl.sum()) if pnl.sum() else None
            res[key]["daily_bps"] = {str(k): float(v) for k, v in
                                     t.groupby(t["exit_time"].dt.date)["pnl_bps"].sum().items()}
        ic = ic_by_horizon(f, col)
        res[key]["ic"] = ic.to_dict("records")
        ics[title] = ic
    res["charts_common"] = {"ic": ic_chart(ics, "ic_by_horizon")}

    sc = [("POC", res["H1_poc"]), ("Flow", res["H2_flow"]), ("Reversal", res["H3_reversal"])]
    res["charts_common"]["scorecard"] = bars_chart(
        [n for n, _ in sc], [r["out_of_sample"]["avg_bps"] or 0 for _, r in sc],
        "Out-of-sample net PnL per trade", "bps per trade", "scorecard")
    (OUT / "results.json").write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps({k: (v.get("out_of_sample") if isinstance(v, dict) and "out_of_sample" in v else v)
                      for k, v in res.items()}, indent=1, default=str))
    return res


if __name__ == "__main__":
    main()
