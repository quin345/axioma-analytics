"""Trade-level simulators on 5-minute bars (mid OHLC + mean spread).

Conservative fill rules, because only bar high/low are known:

* Decisions use information at bar t (slot_end t); orders first fill in bar t+1.
  Exits are checked only from the bar after the entry fill.
* Resting limit BUY at P fills when the ask reaches P: mid_low + half_spread <= P.
  Resting limit SELL at P fills when the bid reaches P: mid_high - half_spread >= P.
* Market orders cross the spread (mid +/- half_spread).
* Target and stop touched in the same bar -> the stop is assumed first.
* Both maker quotes touched in the same bar -> fill order unknown, bar skipped.
* One position per instrument at a time; bars without data are skipped.

PnL is in basis points of the entry price, net of the costs above.
"""
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from . import INSTRUMENT


@dataclass(frozen=True)
class PocParams:
    window: int = 60          # VAP lookback in minutes (60/120/180/240)
    entry_k: float = 0.5      # entry distance from POC, in value-area widths
    stop_k: float = 0.5       # stop distance beyond entry, in value-area widths (0 = no stop)
    hold: int = 6             # max bars in the trade after entry
    mode: str = "maker"       # "maker" (limit entry) or "taker" (market entry)


def _arrays(g):
    g = g.sort_values("slot_end")
    return g, {
        "t": g["slot_end"].to_numpy(),
        "ok": (g["has_data"].astype(bool) & g["mid_close"].notna()).to_numpy(),
        "c": g["mid_close"].to_numpy(), "h": g["mid_high"].to_numpy(), "l": g["mid_low"].to_numpy(),
        "hs": (g["spread_mean"] / 2).ffill().bfill().to_numpy(),
    }


def _exit(a, side, i0, target, stop, hold):
    """Walk bars from i0 until stop (market), target (limit) or timeout (market).
    Returns (exit_index, exit_price, reason), or None if data runs out."""
    for j in range(i0, len(a["c"])):
        if not a["ok"][j]:
            continue
        hs = a["hs"][j]
        if side > 0:
            if stop is not None and a["l"][j] <= stop:
                return j, stop - hs, "stop"
            if a["h"][j] - hs >= target:
                return j, target, "target"
        else:
            if stop is not None and a["h"][j] >= stop:
                return j, stop + hs, "stop"
            if a["l"][j] + hs <= target:
                return j, target, "target"
        if j - i0 + 1 >= hold:
            return j, a["c"][j] - side * hs, "timeout"
    return None


def _trade(key, name, a, i, k, x, side, entry, px, reason):
    return {"ctid_trader_account": key[0], "symbol_id": key[1], "instrument": name,
            "signal_time": a["t"][i], "entry_time": a["t"][k], "exit_time": a["t"][x],
            "side": side, "entry": entry, "exit": px, "reason": reason,
            "bars": x - k, "pnl_bps": side * (px - entry) / entry * 1e4}


def simulate_poc(df, p: PocParams):
    """Mean reversion to the VAP point of control; target = POC at signal time."""
    trades = []
    for key, g in df.groupby(INSTRUMENT):
        g, a = _arrays(g)
        poc = g[f"vap_{p.window}m_poc"].to_numpy()
        width = (g[f"vap_{p.window}m_va_high"] - g[f"vap_{p.window}m_va_low"]).to_numpy()
        name = g["standardized_name"].iloc[0]
        n, i = len(g), 0
        while i < n - 1:
            if not (a["ok"][i] and a["ok"][i + 1] and np.isfinite(poc[i])
                    and np.isfinite(width[i]) and width[i] > 0):
                i += 1
                continue
            c, j = a["c"][i], i + 1
            buy_px, sell_px = poc[i] - p.entry_k * width[i], poc[i] + p.entry_k * width[i]
            side = None
            if p.mode == "maker":
                hs = a["hs"][j]
                buy = c > buy_px and a["l"][j] + hs <= buy_px   # quote stays passive
                sell = c < sell_px and a["h"][j] - hs >= sell_px
                if buy != sell:
                    side, entry, k = (1, buy_px, j) if buy else (-1, sell_px, j)
            elif c <= buy_px:
                side, entry, k = 1, c + a["hs"][i], i
            elif c >= sell_px:
                side, entry, k = -1, c - a["hs"][i], i
            if side is None:
                i += 1
                continue
            stop = entry - side * p.stop_k * width[i] if p.stop_k else None
            res = _exit(a, side, k + 1, poc[i], stop, p.hold)
            if res is None:
                break
            x, px, reason = res
            trades.append(_trade(key, name, a, i, k, x, side, entry, px, reason))
            i = x  # flat again after the exit bar
    return pd.DataFrame(trades)


def simulate_signal(df, signal, threshold=1.0, hold=1, cost=True):
    """Taker trade on a factor: if |signal_t| > threshold, trade its sign at mid_close
    (paying half spread) and exit `hold` bars later at mid_close (paying half spread)."""
    trades = []
    for key, g in df.groupby(INSTRUMENT):
        g, a = _arrays(g)
        s = g[signal].to_numpy()
        name = g["standardized_name"].iloc[0]
        n, i = len(g), 0
        while i < n - 1:
            if not a["ok"][i] or not np.isfinite(s[i]) or abs(s[i]) <= threshold:
                i += 1
                continue
            side = 1 if s[i] > 0 else -1
            x = i + hold
            while x < n and not a["ok"][x]:
                x += 1
            if x >= n:
                break
            entry = a["c"][i] + side * (a["hs"][i] if cost else 0.0)
            px = a["c"][x] - side * (a["hs"][x] if cost else 0.0)
            trades.append(_trade(key, name, a, i, i, x, side, entry, px, "timeout"))
            i = x
    return pd.DataFrame(trades)


def summarize(trades, label=None):
    """Headline statistics for a trade list (pnl in bps)."""
    base = {"label": label, "trades": 0, "win_rate": np.nan, "avg_bps": np.nan, "median_bps": np.nan,
            "total_bps": 0.0, "t_stat": np.nan, "trades_per_day": 0.0, "target_share": np.nan,
            "stop_share": np.nan, "max_dd_bps": 0.0}
    if trades is None or trades.empty:
        return base
    pnl = trades["pnl_bps"]
    days = max(1.0, (trades["exit_time"].max() - trades["entry_time"].min()) / pd.Timedelta(days=1))
    eq = pnl.cumsum()
    base.update({
        "trades": len(pnl), "win_rate": float((pnl > 0).mean()), "avg_bps": float(pnl.mean()),
        "median_bps": float(pnl.median()), "total_bps": float(pnl.sum()),
        "t_stat": float(pnl.mean() / pnl.std() * np.sqrt(len(pnl))) if len(pnl) > 1 and pnl.std() > 0 else np.nan,
        "trades_per_day": len(pnl) / days,
        "target_share": float((trades["reason"] == "target").mean()),
        "stop_share": float((trades["reason"] == "stop").mean()),
        "max_dd_bps": float((eq - eq.cummax()).min()),
    })
    return base


def params_dict(p):
    return asdict(p)
