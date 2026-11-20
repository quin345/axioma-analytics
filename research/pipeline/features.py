"""Point-in-time features and forward-return targets.

Every feature at slot_end t uses only bars with slot_end <= t. Targets
fwd_ret_{h} are the log return from t to t + h bars and must never be used as
inputs.
"""
import numpy as np
import pandas as pd

from . import INSTRUMENT
from .timeframe import TF_5M

HORIZONS = TF_5M.horizons  # bars (5-minute preset)
ZWINDOW = TF_5M.zwindow  # 4 hours of 5-minute bars


def _rolling_z(s, window=ZWINDOW):
    mean = s.rolling(window, min_periods=window // 4).mean()
    std = s.rolling(window, min_periods=window // 4).std()
    return (s - mean) / std.replace(0, np.nan)


def _per_instrument(g, horizons, tf=TF_5M):
    g = g.sort_values("slot_end").copy()
    logp = np.log(g["mid_close"])
    for h in horizons:
        g[f"fwd_ret_{h}"] = logp.shift(-h) - logp
    g["ret_1"] = logp - logp.shift(1)
    g["ret_6"] = logp - logp.shift(6)
    zw = tf.zwindow
    g["flow_imbalance_z"] = _rolling_z(g["flow_imbalance"], zw)
    g["book_imbalance_z"] = _rolling_z(g["book_imbalance"], zw)
    g["rel_spread_z"] = _rolling_z(g["rel_spread_mean"], zw)
    g["realised_vol_z"] = _rolling_z(g["realised_vol_pct"], zw)
    g["flow_imbalance_3"] = g["flow_imbalance"].rolling(3, min_periods=1).mean()
    for w in tf.poc_windows:
        poc, lo, hi = g[f"vap_{w}m_poc"], g[f"vap_{w}m_va_low"], g[f"vap_{w}m_va_high"]
        g[f"poc_dist_{w}"] = (g["mid_close"] - poc) / (hi - lo).replace(0, np.nan)
    _extra_features(g, logp, horizons, tf)
    return g


def _optional(g, column):
    """A metrics column, or NaN if this extract does not carry it."""
    return g[column] if column in g else pd.Series(np.nan, index=g.index)


def _extra_features(g, logp, horizons, tf=TF_5M):
    """Inputs for the hypothesis screen (studies of price, spread and activity).

    All point-in-time. `fwd_vol_{h}` is a *target* (mean realised volatility over
    the next h bars, t+1..t+h) and must never be used as an input.
    """
    g["ret_3"] = logp - logp.shift(3)
    g["ret_12"] = logp - logp.shift(12)
    high = _optional(g, "mid_high").rolling(12, min_periods=6).max()
    low = _optional(g, "mid_low").rolling(12, min_periods=6).min()
    g["range_pos_12"] = (g["mid_close"] - low) / (high - low).replace(0, np.nan) - 0.5
    g["events_z"] = _rolling_z(_optional(g, "events"), tf.zwindow)
    g["tick_gap_z"] = _rolling_z(_optional(g, "tick_median_gap_ms"), tf.zwindow)
    g["acf1"] = _optional(g, "acf_lag1")
    vol = g["realised_vol_pct"]
    for h in horizons:
        g[f"fwd_vol_{h}"] = vol.shift(-h).rolling(h, min_periods=max(1, h // 2)).mean()


def _add_cross_returns(df):
    """`cross_ret_1`: the mean last-bar return of the *other* instruments at the
    same slot_end. Same-time data only, so it is point-in-time."""
    ret = df.pivot(index="slot_end", columns=INSTRUMENT, values="ret_1")  # one row per slot after cleaning
    total, count = ret.sum(axis=1, min_count=1), ret.notna().sum(axis=1)
    out = []
    for key, g in df.groupby(INSTRUMENT, sort=True):
        own = ret[key].reindex(g["slot_end"]).to_numpy()
        tot = total.reindex(g["slot_end"]).to_numpy()
        cnt = count.reindex(g["slot_end"]).to_numpy()
        others = cnt - np.isfinite(own)
        with np.errstate(invalid="ignore", divide="ignore"):
            val = np.where(others > 0, (tot - np.nan_to_num(own)) / others, np.nan)
        out.append(pd.Series(val, index=g.index))
    df["cross_ret_1"] = pd.concat(out)
    return df


def build_features(df, horizons=None, tf=TF_5M):
    """df must come from clean.clean_metrics (regular grid per instrument)."""
    horizons = tf.horizons if horizons is None else horizons
    if df.empty:
        return df.copy()
    parts = [_per_instrument(g, horizons, tf) for _, g in df.groupby(INSTRUMENT, sort=True)]
    return _add_cross_returns(pd.concat(parts, ignore_index=True))


def target_columns(df):
    return [c for c in df.columns if c.startswith("fwd_ret_")]
