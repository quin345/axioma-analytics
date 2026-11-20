"""Hypothesis screen: score every pre-registered hypothesis, pick the top N on a
discovery sample, then test only those N once on a held-out sample.

Protocol (fixed in code, so it cannot drift toward a nicer answer):

1. **Split.** One chronological cut for all instruments, at the `split`
   quantile of the pooled timestamps that have data. Discovery ends `embargo`
   before the cut so that no forward target in discovery reaches into holdout.
2. **Score.** For each hypothesis x horizon x instrument: Spearman rank IC
   between the signal and the target, computed *per time block* (default 4 h).
   Forward targets overlap bar to bar, so the bars are not independent; the
   blocks are close to it, and the t-stat is taken across blocks. A hypothesis
   with a `control` uses the partial rank correlation with that column held
   fixed. One-sided p-value in the pre-registered direction.
3. **Select.** Per hypothesis, its best horizon on discovery. Eligible if the
   pooled Benjamini-Hochberg q-value (over every non-baseline hypothesis x
   horizon) is at most `fdr` *and* the IC has the predicted sign on every
   instrument with enough blocks. Rank eligible first, then by discovery t.
   Baselines are scored but never picked. A candidate whose signal correlates
   at |rho| >= 0.7 (discovery) with an earlier pick is the same effect measured
   twice, so it is skipped and the next one takes its place.
4. **Confirm.** Each pick is scored on holdout at its frozen horizon.
   "confirmed" needs a one-sided p below 0.05 / N (Bonferroni over the picks).
5. **Economics.** Return hypotheses get a taker trade test on holdout (|z| > 1,
   hold = horizon, half spread paid each way). Volatility hypotheses get the
   ratio of future volatility in the top vs bottom signal quintile, within
   terciles of current volatility.
6. **Robustness** (post-selection, holdout): see `robustness`.
"""
import math

import numpy as np
import pandas as pd

from . import INSTRUMENT
from . import strategies as S
from .features import HORIZONS, _rolling_z
from .hypotheses import HYPOTHESES, evaluate_signal

BLOCK = pd.Timedelta(hours=4)
MIN_BLOCK_OBS = 12      # a block with fewer valid pairs is skipped
MIN_BLOCKS = 3          # an instrument with fewer blocks does not vote on sign
MAX_SIGNAL_CORR = 0.7   # picks closer than this (|Spearman rho| on discovery) are one effect
SLOT = pd.Timedelta(minutes=5)


# --- data preparation ---------------------------------------------------------

def add_hypothesis_signals(features, hypotheses=HYPOTHESES):
    """`hyp_<key>` columns, computed once on the full point-in-time frame."""
    out = features.copy()
    for h in hypotheses:
        out[f"hyp_{h.key}"] = evaluate_signal(h, out)
    return out


def split(df, frac=0.6, embargo_bars=max(HORIZONS)):
    """(discovery, holdout, cut). Same timestamp cut for every instrument."""
    t = df.loc[df["has_data"].astype(bool), "slot_end"].sort_values()
    if t.empty:
        raise ValueError("no bars with data to split")
    cut = t.iloc[min(len(t) - 1, int(len(t) * frac))]
    disc = df[df["slot_end"] <= cut - embargo_bars * SLOT]
    hold = df[df["slot_end"] > cut]
    return disc, hold, cut


# --- statistics ---------------------------------------------------------------

def _partial(rxy, rxz, ryz):
    den = math.sqrt(max(0.0, (1 - rxz ** 2) * (1 - ryz ** 2)))
    return (rxy - rxz * ryz) / den if den > 1e-12 else float("nan")


def block_ics(g, x, y, z=None, block=BLOCK):
    """Rank IC of x vs y per time block, partial on z if given."""
    cols = [x, y] + ([z] if z else [])
    d = g[["slot_end"] + cols].dropna()
    if d.empty:
        return []
    out = []
    for _, b in d.groupby(d["slot_end"].dt.floor(block)):
        if len(b) < MIN_BLOCK_OBS:
            continue
        r = b[cols].rank()
        if (r.std() == 0).any():
            continue
        c = r.corr()
        ic = c.loc[x, y] if not z else _partial(c.loc[x, y], c.loc[x, z], c.loc[y, z])
        if np.isfinite(ic):
            out.append(float(ic))
    return out


def summarise_ics(ics):
    n = len(ics)
    if n == 0:
        return {"blocks": 0, "ic": np.nan, "t": np.nan, "p": np.nan}
    a = np.asarray(ics)
    mean = float(a.mean())
    sd = float(a.std(ddof=1)) if n > 1 else 0.0
    t = mean / (sd / math.sqrt(n)) if sd > 0 else np.nan
    p = 0.5 * math.erfc(t / math.sqrt(2)) if np.isfinite(t) else np.nan  # one-sided, H1: IC > 0
    return {"blocks": n, "ic": mean, "t": t, "p": p}


def target_column(h, horizon):
    return f"fwd_{h.target}_{horizon}"


def score(df, hypotheses=HYPOTHESES, horizons=HORIZONS, block=BLOCK):
    """One row per (hypothesis, horizon, instrument) plus a pooled 'ALL' row."""
    rows = []
    for h in hypotheses:
        for k in horizons:
            y, x = target_column(h, k), f"hyp_{h.key}"
            pooled = []
            for key, g in df.groupby(INSTRUMENT, sort=True):
                ics = block_ics(g, x, y, h.control, block)
                pooled += ics
                name = g["standardized_name"].dropna().iloc[0] if g["standardized_name"].notna().any() else str(key)
                rows.append({"hypothesis": h.key, "target": h.target, "horizon_bars": k,
                             "instrument": name, **summarise_ics(ics)})
            rows.append({"hypothesis": h.key, "target": h.target, "horizon_bars": k,
                         "instrument": "ALL", **summarise_ics(pooled)})
    return pd.DataFrame(rows)


def bh_qvalues(p):
    """Benjamini-Hochberg q-values; NaN p-values stay NaN and are not counted."""
    p = pd.Series(p, dtype=float)
    ok = p.dropna().sort_values()
    m = len(ok)
    if m == 0:
        return pd.Series(np.nan, index=p.index)
    q = (ok * m / np.arange(1, m + 1))[::-1].cummin()[::-1].clip(upper=1.0)
    return q.reindex(p.index)


# --- selection ----------------------------------------------------------------

def select(table, hypotheses=HYPOTHESES, top=3, fdr=0.10, discovery=None, max_corr=MAX_SIGNAL_CORR):
    """The top-N (hypothesis, horizon) picks from a discovery score table.

    With `discovery` (the discovery feature frame, holding `hyp_*` columns), a
    candidate whose signal has |Spearman rho| >= `max_corr` with an earlier pick
    is skipped as a duplicate measurement of the same effect, and the next
    candidate takes its place. Discovery data only, so the holdout stays unseen.
    """
    eligible_keys = {h.key for h in hypotheses if not h.baseline}
    pooled = table[(table["instrument"] == "ALL") & table["hypothesis"].isin(eligible_keys)].copy()
    pooled["q"] = bh_qvalues(pooled["p"])

    per_inst = table[table["instrument"] != "ALL"]
    rows = []
    for key, g in pooled.groupby("hypothesis"):
        best = g.sort_values("t", ascending=False, na_position="last").iloc[0]
        inst = per_inst[(per_inst["hypothesis"] == key) & (per_inst["horizon_bars"] == best["horizon_bars"])
                        & (per_inst["blocks"] >= MIN_BLOCKS)]
        consistent = bool(len(inst)) and bool((inst["ic"] > 0).all())
        passed = bool(np.isfinite(best["q"]) and best["q"] <= fdr and best["t"] > 0 and consistent)
        rows.append({**best.to_dict(), "sign_consistent": consistent, "passed_discovery": passed,
                     "instruments_voting": len(inst)})
    ranked = pd.DataFrame(rows)
    if ranked.empty:
        return ranked, ranked
    ranked = ranked.sort_values(["passed_discovery", "t"], ascending=[False, False], na_position="last")
    ranked["rank"] = np.arange(1, len(ranked) + 1)
    ranked = ranked.reset_index(drop=True)

    corr = None
    if discovery is not None:
        cols = [f"hyp_{k}" for k in ranked["hypothesis"]]
        corr = discovery[cols].corr(method="spearman")
    picked, redundant = [], []
    for key in ranked["hypothesis"]:
        dup = None
        if corr is not None:
            for p in picked:
                rho = corr.loc[f"hyp_{key}", f"hyp_{p}"]
                if np.isfinite(rho) and abs(rho) >= max_corr:
                    dup = f"{p} (rho {rho:+.2f})"
                    break
        redundant.append(dup)
        if dup is None and len(picked) < top:
            picked.append(key)
    ranked["redundant_with"] = redundant
    ranked["picked"] = ranked["hypothesis"].isin(picked)
    picks = ranked[ranked["picked"]].reset_index(drop=True)
    return ranked, picks


def confirm(holdout, picks, hypotheses=HYPOTHESES, alpha=0.05, block=BLOCK):
    """Holdout statistics at each pick's frozen horizon, with a verdict."""
    by_key = {h.key: h for h in hypotheses}
    n = max(1, len(picks))
    rows = []
    for _, pick in picks.iterrows():
        h, k = by_key[pick["hypothesis"]], int(pick["horizon_bars"])
        tab = score(holdout, [h], [k], block)
        pooled = tab[tab["instrument"] == "ALL"].iloc[0]
        per = {r["instrument"]: r["ic"] for _, r in tab[tab["instrument"] != "ALL"].iterrows()}
        if np.isfinite(pooled["p"]) and pooled["p"] < alpha / n:
            verdict = "confirmed"
        elif np.isfinite(pooled["ic"]) and pooled["ic"] > 0:
            verdict = "right sign, not significant"
        else:
            verdict = "failed"
        rows.append({"hypothesis": h.key, "horizon_bars": k, "holdout_blocks": pooled["blocks"],
                     "holdout_ic": pooled["ic"], "holdout_t": pooled["t"], "holdout_p": pooled["p"],
                     "holdout_ic_by_instrument": per, "verdict": verdict,
                     "bonferroni_alpha": alpha / n})
    return pd.DataFrame(rows)


# --- economics ----------------------------------------------------------------

def trade_test(holdout, key, horizon, threshold=1.0):
    """Taker trades on holdout: |rolling z| > threshold, hold `horizon` bars."""
    d = holdout.sort_values(INSTRUMENT + ["slot_end"]).copy()
    d["_z"] = d.groupby(INSTRUMENT)[f"hyp_{key}"].transform(_rolling_z)
    net = S.summarize(S.simulate_signal(d, "_z", threshold, horizon, cost=True))
    gross = S.summarize(S.simulate_signal(d, "_z", threshold, horizon, cost=False))
    return {"trades": net["trades"], "trades_per_day": net["trades_per_day"],
            "gross_avg_bps": gross["avg_bps"], "net_avg_bps": net["avg_bps"],
            "net_win_rate": net["win_rate"], "net_t": net["t_stat"],
            "cost_per_trade_bps": (gross["avg_bps"] - net["avg_bps"]) if net["trades"] else np.nan}


def vol_quintile_ratio(holdout, key, horizon, control):
    """Mean future vol in the top vs bottom signal quintile, within current-vol terciles."""
    y, x = f"fwd_vol_{horizon}", f"hyp_{key}"
    ratios = []
    for _, g in holdout.groupby(INSTRUMENT):
        d = g[[x, y] + ([control] if control else [])].dropna()
        if len(d) < 50:
            continue
        groups = [d] if not control else [b for _, b in d.groupby(
            pd.qcut(d[control].rank(method="first"), 3, labels=False))]
        for b in groups:
            q = pd.qcut(b[x].rank(method="first"), 5, labels=False)
            lo, hi = b.loc[q == 0, y].mean(), b.loc[q == 4, y].mean()
            if lo > 0 and np.isfinite(hi):
                ratios.append(hi / lo)
    return {"vol_ratio_top_vs_bottom": float(np.mean(ratios)) if ratios else np.nan,
            "vol_ratio_cells": len(ratios)}


def robustness(holdout, picks, discovery, hypotheses=HYPOTHESES, block=BLOCK):
    """Post-selection stress tests on holdout, one dict per pick (IC values).

    Return picks:
      * `ic_ctrl_last_return`: partial IC holding the last bar's return fixed,
        so a pick that only re-expresses 5-minute reversal shows up as ~0;
      * `ic_skip_one_bar`: IC against the return from t+1 to t+1+h. Noise in the
        mid snapshot at t makes returns reverse mechanically; skipping a bar
        removes that artefact, so a surviving IC is not snapshot noise.
    Volatility picks:
      * `ic_ctrl_vol_and_hour`: partial IC holding current volatility *and* the
        hour-of-day average volatility (estimated on discovery) fixed, so the
        session cycle cannot pass for prediction.
    """
    by_key = {h.key: h for h in hypotheses}
    hold = holdout.sort_values(INSTRUMENT + ["slot_end"]).copy()
    hold["_neg_ret_1"] = -hold["ret_1"]
    logp = np.log(hold["mid_close"])
    keys = [hold[c] for c in INSTRUMENT]
    for k in HORIZONS:
        hold[f"_fwd_skip_{k}"] = logp.groupby(keys).shift(-(k + 1)) - logp.groupby(keys).shift(-1)
    hour = discovery.groupby(INSTRUMENT + [discovery["slot_end"].dt.hour.rename("_hr")])["realised_vol_pct"] \
        .mean().rename("_hour_vol").reset_index()
    hold["_hr"] = hold["slot_end"].dt.hour
    hold = hold.merge(hour, on=INSTRUMENT + ["_hr"], how="left")
    hold["_vol_and_hour"] = hold["realised_vol_pct"].rank() + hold["_hour_vol"].rank()

    def pooled(x, y, z=None):
        ics = []
        for _, g in hold.groupby(INSTRUMENT):
            ics += block_ics(g, x, y, z, block)
        s = summarise_ics(ics)
        return s["ic"], s["t"]

    rows = []
    for _, pick in picks.iterrows():
        h, k = by_key[pick["hypothesis"]], int(pick["horizon_bars"])
        x, row = f"hyp_{h.key}", {"hypothesis": h.key}
        if h.target == "ret":
            row["ic_ctrl_last_return"], row["t_ctrl_last_return"] = pooled(x, f"fwd_ret_{k}", "_neg_ret_1")
            row["ic_skip_one_bar"], row["t_skip_one_bar"] = pooled(x, f"_fwd_skip_{k}")
        else:
            row["ic_ctrl_vol_and_hour"], row["t_ctrl_vol_and_hour"] = pooled(x, f"fwd_vol_{k}", "_vol_and_hour")
        rows.append(row)
    return pd.DataFrame(rows)


def economics(holdout, picks, hypotheses=HYPOTHESES):
    by_key = {h.key: h for h in hypotheses}
    rows = []
    for _, pick in picks.iterrows():
        h, k = by_key[pick["hypothesis"]], int(pick["horizon_bars"])
        extra = trade_test(holdout, h.key, k) if h.target == "ret" else vol_quintile_ratio(holdout, h.key, k, h.control)
        rows.append({"hypothesis": h.key, **extra})
    return pd.DataFrame(rows)
