"""Vectorised single-instrument long/short backtest with spread costs.

Position at t is decided from the signal at t and earns the next-bar return
fwd_ret_1 (t -> t+1), so there is no look-ahead. Each change of position pays
half the relative spread per unit traded.
"""
import numpy as np
import pandas as pd

from . import INSTRUMENT
from .timeframe import TF_5M

BARS_PER_YEAR = TF_5M.bars_per_year  # 5-minute bars, 24h markets


def positions(signal, threshold=0.0):
    pos = np.sign(signal.where(signal.abs() > threshold, 0.0)).fillna(0.0)
    return pos


def backtest(df, signal, threshold=0.0, cost=True, tf=TF_5M):
    rows, curves = [], []
    for (account, sym), g in df.groupby(INSTRUMENT):
        g = g.sort_values("slot_end")
        pos = positions(g[signal], threshold)
        ret = g["fwd_ret_1"].fillna(0.0)
        trades = pos.diff().abs().fillna(pos.abs())
        half_spread = (g["rel_spread_mean"].ffill().fillna(0.0) / 2) if cost else 0.0
        pnl = pos * ret - trades * half_spread
        curves.append(pd.DataFrame({"slot_end": g["slot_end"], "ctid_trader_account": account,
                                    "symbol_id": sym, "signal": signal, "position": pos, "pnl": pnl,
                                    "equity": pnl.cumsum()}))
        std = pnl.std()
        rows.append({
            "ctid_trader_account": account, "symbol_id": sym, "signal": signal, "threshold": threshold,
            "bars": len(pnl), "exposure": float((pos != 0).mean()), "trades": float(trades.sum()),
            "total_log_ret": float(pnl.sum()),
            "sharpe_ann": float(pnl.mean() / std * np.sqrt(tf.bars_per_year)) if std > 0 else np.nan,
            "max_drawdown": float((pnl.cumsum() - pnl.cumsum().cummax()).min()),
        })
    return pd.DataFrame(rows), pd.concat(curves, ignore_index=True) if curves else pd.DataFrame()
