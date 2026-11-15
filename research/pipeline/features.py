"""Point-in-time features and forward-return targets.

Every feature at slot_end t uses only bars with slot_end <= t. Targets
fwd_ret_{h} are the log return from t to t + h bars and must never be used as
inputs.
"""
import numpy as np
import pandas as pd

from . import INSTRUMENT

HORIZONS = (1, 3, 6, 12)  # bars of 5 minutes
ZWINDOW = 48  # 4 hours


def _rolling_z(s, window=ZWINDOW):
    mean = s.rolling(window, min_periods=window // 4).mean()
    std = s.rolling(window, min_periods=window // 4).std()
    return (s - mean) / std.replace(0, np.nan)


def _per_instrument(g, horizons):
    g = g.sort_values("slot_end").copy()
    logp = np.log(g["mid_close"])
    for h in horizons:
        g[f"fwd_ret_{h}"] = logp.shift(-h) - logp
    g["ret_1"] = logp - logp.shift(1)
    g["ret_6"] = logp - logp.shift(6)
    g["flow_imbalance_z"] = _rolling_z(g["flow_imbalance"])
    g["book_imbalance_z"] = _rolling_z(g["book_imbalance"])
    g["rel_spread_z"] = _rolling_z(g["rel_spread_mean"])
    g["realised_vol_z"] = _rolling_z(g["realised_vol_pct"])
    g["flow_imbalance_3"] = g["flow_imbalance"].rolling(3, min_periods=1).mean()
    for w in (60, 120, 240):
        poc, lo, hi = g[f"vap_{w}m_poc"], g[f"vap_{w}m_va_low"], g[f"vap_{w}m_va_high"]
        g[f"poc_dist_{w}"] = (g["mid_close"] - poc) / (hi - lo).replace(0, np.nan)
    return g


def build_features(df, horizons=HORIZONS):
    """df must come from clean.clean_metrics (regular grid per instrument)."""
    if df.empty:
        return df.copy()
    parts = [_per_instrument(g, horizons) for _, g in df.groupby(INSTRUMENT, sort=True)]
    return pd.concat(parts, ignore_index=True)


def target_columns(df):
    return [c for c in df.columns if c.startswith("fwd_ret_")]
