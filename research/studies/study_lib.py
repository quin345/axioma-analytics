"""Shared helpers for the hypothesis studies: data split, sweeps, benchmarks, charts."""
import itertools
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from pipeline import INSTRUMENT, clean, data, features, signals  # noqa: E402
from pipeline import strategies as S  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

COLORS = {"bg": "#0b1220", "panel": "#111a2e", "text": "#e6edf7", "muted": "#8da2c0",
          "pos": "#22c55e", "neg": "#ef4444", "accent": "#38bdf8", "accent2": "#f59e0b"}
plt.rcParams.update({
    "figure.facecolor": COLORS["panel"], "axes.facecolor": COLORS["panel"], "savefig.facecolor": COLORS["panel"],
    "axes.edgecolor": COLORS["muted"], "axes.labelcolor": COLORS["text"], "text.color": COLORS["text"],
    "xtick.color": COLORS["muted"], "ytick.color": COLORS["muted"], "axes.grid": True,
    "grid.color": "#22304d", "grid.linewidth": 0.6, "font.size": 11, "axes.titlesize": 13,
    "axes.titleweight": "bold", "legend.frameon": False, "axes.axisbelow": True,
})


def load_features(db=None):
    raw = data.load_metrics_5m(db or config.db_path())
    feats = signals.add_signals(features.build_features(clean.clean_metrics(raw)))
    feats["flow_signal"] = feats["sig_flow_3bar"]
    feats["reversal_signal"] = feats["sig_short_reversal"]
    return feats


def split_time(df, frac=0.5):
    """Chronological split at the same timestamp for all instruments."""
    t = df.loc[df["has_data"], "slot_end"]
    cut = t.min() + (t.max() - t.min()) * frac
    return df[df["slot_end"] <= cut], df[df["slot_end"] > cut], cut


def zero_cost(df):
    out = df.copy()
    out["spread_mean"] = 0.0
    return out


POC_GRID = {"window": (60, 120, 240), "entry_k": (0.25, 0.5, 1.0), "stop_k": (0, 0.5, 1.0),
            "hold": (3, 6, 12), "mode": ("maker", "taker")}


def poc_sweep(df, grid=POC_GRID):
    rows = []
    keys = list(grid)
    for vals in itertools.product(*grid.values()):
        p = S.PocParams(**dict(zip(keys, vals)))
        r = S.summarize(S.simulate_poc(df, p))
        r.update(S.params_dict(p))
        rows.append(r)
    return pd.DataFrame(rows)


def signal_sweep(df, signal, thresholds=(0.5, 1.0, 1.5, 2.0), holds=(1, 3, 6, 12), sign=1):
    rows = []
    d = df.copy()
    d["_s"] = sign * d[signal]
    for th, h in itertools.product(thresholds, holds):
        r = S.summarize(S.simulate_signal(d, "_s", th, h))
        r.update({"threshold": th, "hold": h})
        rows.append(r)
    return pd.DataFrame(rows)


def random_benchmark(df, trades, n_sims=500, seed=7):
    """Same entry times and holding periods as `trades`, but a random side.
    Returns the distribution of mean net bps per simulation."""
    if trades.empty:
        return np.array([])
    rng = np.random.default_rng(seed)
    cost = _cost_bps(df, trades).to_numpy()
    # net = side * mid_move - cost  ->  mid_move = side * (net + cost)
    mid_move = trades["side"].to_numpy() * (trades["pnl_bps"].to_numpy() + cost)
    sides = rng.choice([-1, 1], size=(n_sims, len(trades)))
    return (sides * mid_move - cost).mean(axis=1)


def _cost_bps(df, trades):
    """Round-trip spread cost (bps) for taker trades at their entry/exit bars."""
    hs = df.set_index(INSTRUMENT + ["slot_end"])["spread_mean"] / 2
    mid = df.set_index(INSTRUMENT + ["slot_end"])["mid_close"]
    k0 = list(zip(trades["ctid_trader_account"], trades["symbol_id"], trades["entry_time"]))
    k1 = list(zip(trades["ctid_trader_account"], trades["symbol_id"], trades["exit_time"]))
    c = (hs.reindex(k0).to_numpy() + hs.reindex(k1).to_numpy()) / mid.reindex(k0).to_numpy() * 1e4
    return pd.Series(np.nan_to_num(c), index=trades.index)


def ic_by_horizon(df, col, horizons=(1, 3, 6, 12)):
    rows = []
    for h in horizons:
        m = df[[col, f"fwd_ret_{h}"]].dropna()
        ic = m[col].rank().corr(m[f"fwd_ret_{h}"].rank())
        n_eff = len(m) / h  # overlapping returns -> fewer independent observations
        rows.append({"horizon_min": 5 * h, "rank_ic": ic, "n": len(m),
                     "t_stat": ic * np.sqrt(max(n_eff - 2, 1) / max(1e-12, 1 - ic ** 2))})
    return pd.DataFrame(rows)


def decile_returns(df, col, target="fwd_ret_6", q=10):
    d = df[INSTRUMENT + [col, target]].dropna().copy()
    # remove each instrument's average drift so a trending sample doesn't fake a signal
    d[target] = d[target] - d.groupby(INSTRUMENT)[target].transform("mean")
    d["bucket"] = d.groupby(INSTRUMENT)[col].transform(lambda s: pd.qcut(s.rank(method="first"), q, labels=False) + 1)
    return d.groupby("bucket")[target].agg(["mean", "sem", "count"]).mul([1e4, 1e4, 1]).reset_index()
