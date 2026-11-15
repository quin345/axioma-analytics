"""Candidate signals. Positive value = expect price up. Inputs are point-in-time features."""
import numpy as np

SIGNALS = {
    "flow_momentum": lambda f: f["flow_imbalance_z"],
    "book_pressure": lambda f: f["book_imbalance_z"],
    "flow_3bar": lambda f: f["flow_imbalance_3"],
    "short_reversal": lambda f: -f["ret_1"],
    "poc_reversion_60": lambda f: -f["poc_dist_60"],
    "poc_reversion_240": lambda f: -f["poc_dist_240"],
}


def add_signals(features, names=None, zscore=True):
    """Add sig_* columns. With zscore=True each signal is a trailing rolling z-score
    per instrument, so one backtest threshold (in std units) fits every signal."""
    from . import INSTRUMENT
    from .features import _rolling_z

    out = features.sort_values(INSTRUMENT + ["slot_end"]).copy()
    for name in names or SIGNALS:
        raw = SIGNALS[name](out).replace([np.inf, -np.inf], np.nan)
        if zscore:
            raw = raw.groupby([out[c] for c in INSTRUMENT]).transform(_rolling_z)
        out[f"sig_{name}"] = raw
    return out.reset_index(drop=True)


def signal_columns(df):
    return [c for c in df.columns if c.startswith("sig_")]
