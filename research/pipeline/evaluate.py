"""Signal evaluation: IC, rank IC, quantile spreads, hit rate, autocorrelation."""
import numpy as np
import pandas as pd

from . import INSTRUMENT


def _ic_stats(x, y):
    m = x.notna() & y.notna()
    n = int(m.sum())
    if n < 10:
        return {"n": n, "ic": np.nan, "rank_ic": np.nan, "t_stat": np.nan, "hit_rate": np.nan}
    x, y = x[m], y[m]
    ic = x.corr(y)
    rank_ic = x.rank().corr(y.rank())
    t = ic * np.sqrt((n - 2) / max(1e-12, 1 - ic ** 2)) if pd.notna(ic) else np.nan
    nz = (x != 0) & (y != 0)
    hit = float((np.sign(x[nz]) == np.sign(y[nz])).mean()) if nz.any() else np.nan
    return {"n": n, "ic": ic, "rank_ic": rank_ic, "t_stat": t, "hit_rate": hit}


def ic_table(df, signals, targets):
    """One row per (instrument, signal, target), plus a pooled 'ALL' row per signal/target."""
    rows = []
    groups = [(k, g) for k, g in df.groupby(INSTRUMENT)] + [(("ALL", "ALL"), df)]
    for (account, sym), g in groups:
        name = g["standardized_name"].iloc[0] if account != "ALL" and "standardized_name" in g else "ALL"
        for s in signals:
            for t in targets:
                rows.append({"ctid_trader_account": account, "symbol_id": sym, "instrument": name,
                             "signal": s, "target": t, **_ic_stats(g[s], g[t])})
    return pd.DataFrame(rows)


def quantile_returns(df, signal, target, q=5):
    """Mean forward return by signal quantile (per instrument, so scales are comparable)."""
    d = df[INSTRUMENT + [signal, target]].dropna()
    if d.empty:
        return pd.DataFrame()
    d["q"] = d.groupby(INSTRUMENT)[signal].transform(
        lambda s: pd.qcut(s.rank(method="first"), q, labels=False, duplicates="drop") + 1 if len(s) >= q else np.nan)
    out = d.groupby("q")[target].agg(["mean", "count"]).reset_index()
    out.insert(0, "target", target)
    out.insert(0, "signal", signal)
    return out


def signal_autocorr(df, signal, lags=(1, 3, 6, 12)):
    """Turnover proxy: how persistent the signal is per instrument (higher = slower)."""
    res = {}
    for lag in lags:
        vals = []
        for _, g in df.groupby(INSTRUMENT):
            s = g.sort_values("slot_end")[signal]
            vals.append(s.corr(s.shift(lag)))  # pairwise, so grid gaps (NaN) are skipped
        vals = [v for v in vals if pd.notna(v)]
        res[f"ac_{lag}"] = float(np.mean(vals)) if vals else np.nan
    return {"signal": signal, **res}
