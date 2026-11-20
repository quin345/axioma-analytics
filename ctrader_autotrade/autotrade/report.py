"""Forward-test report: the bot's journal against the research holdout (DESIGN.md sections 1 and 8).

Pure over a journal db: `compute_report(conn)` returns a dict, `render(report)` a plain-text
table. Statistics follow research/pipeline/screen.py (block_ics / summarise_ics, imported through
autotrade/_research.py), so the forward IC is directly comparable with the holdout IC.

Definitions:
- sig_poc = -(mid_close - vap_poc) / (va_high - va_low), recomputed from the `bars` table (the
  research `hyp_poc_reversion_4h`), against the next bar's log mid return (consecutive slots only).
- A signal is a signals row with |z_poc| > entry_z; its side is sign(z_poc).
- Taker shadow: enter at the touch at t, leave at the touch at t+1 (net of the spread). Gross is the
  mid_close(t) to mid_close(t+1) return of the same side, from the bars table.
- Maker sim: a limit at our side of the touch at t, filled if a later quote within limit_ttl_s
  crosses it. Pnl per fill = limit to the exit touch at t+1. Adverse selection = mean side-signed
  mid return t to t+1 over filled signals minus the same over unfilled ones (negative = fills
  happen before price moves against us).
"""
from __future__ import annotations

import json
import math
from typing import Optional

import numpy as np
import pandas as pd

from ._research import screen

SLOT_MS = 300_000
# Research holdout references (DESIGN.md section 1).
REF = {"ic": 0.16, "taker_gross_bps": 0.39, "taker_net_bps": -0.31, "maker_bps_per_fill": -2.1,
       "maker_fill_rate": 0.82}


def _f(x) -> Optional[float]:
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else float(x)


def _mean_t(v) -> dict:
    a = np.asarray([x for x in v if x is not None and np.isfinite(x)], dtype=float)
    n = len(a)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "t": None}
    sd = float(a.std(ddof=1)) if n > 1 else None
    t = float(a.mean() / (sd / math.sqrt(n))) if sd else None
    return {"n": n, "mean": float(a.mean()), "sd": sd, "t": t}


def _entry_z(conn, default: float = 1.0) -> float:
    row = conn.execute("SELECT config_json FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    try:
        return float(json.loads(row[0])["entry_z"]) if row else default
    except (ValueError, KeyError, TypeError):
        return default


def _bars_frame(conn, since_ms: Optional[int]) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT symbol_id, slot_end_ms, has_data, mid_close, vap_poc, vap_va_low, vap_va_high, approx_share "
        "FROM bars WHERE slot_end_ms>=? ORDER BY symbol_id, slot_end_ms", conn, params=(since_ms or 0,))
    if df.empty:
        return df
    ok = df["has_data"].astype(bool) & (df["mid_close"] > 0)
    df["mid_close"] = df["mid_close"].where(ok)
    width = (df["vap_va_high"] - df["vap_va_low"]).replace(0, np.nan)
    df["sig_poc"] = -((df["mid_close"] - df["vap_poc"]) / width)
    df["slot_end"] = pd.to_datetime(df["slot_end_ms"], unit="ms", utc=True)
    nxt = df.groupby("symbol_id")["mid_close"].shift(-1)
    gap = df.groupby("symbol_id")["slot_end_ms"].shift(-1) - df["slot_end_ms"]
    df["fwd_ret_1"] = np.where(gap == SLOT_MS, np.log(nxt / df["mid_close"]), np.nan)
    df["next_mid"] = np.where(gap == SLOT_MS, nxt, np.nan)
    return df


def _ic_section(bars: pd.DataFrame) -> dict:
    pooled, per = [], {}
    for sid, g in bars.groupby("symbol_id"):
        ics = screen.block_ics(g, "sig_poc", "fwd_ret_1")
        per[int(sid)] = screen.summarise_ics(ics)
        pooled += ics
    out = screen.summarise_ics(pooled)
    out["per_symbol"] = per
    out["bars"] = int(bars["sig_poc"].notna().sum()) if not bars.empty else 0
    out["approx_bars"] = int((bars["approx_share"] > 0).sum()) if not bars.empty else 0
    return {k: (_f(v) if isinstance(v, float) else v) for k, v in out.items()}


def _signals(conn, since_ms, entry_z, bars) -> pd.DataFrame:
    s = pd.read_sql_query("SELECT * FROM signals WHERE slot_end_ms>=?", conn, params=(since_ms or 0,))
    if s.empty:
        return s
    s["side"] = np.sign(s["z_poc"])
    s["is_signal"] = s["z_poc"].abs() > entry_z
    if not bars.empty:
        m = bars[["symbol_id", "slot_end_ms", "mid_close", "next_mid"]]
        s = s.merge(m, on=["symbol_id", "slot_end_ms"], how="left")
        s["gross_bps"] = s["side"] * (s["next_mid"] - s["mid_close"]) / s["mid_close"] * 1e4
    else:
        s["gross_bps"] = np.nan
    return s


def _taker_section(sig: pd.DataFrame) -> dict:
    d = sig[sig["is_signal"] & sig["taker_pnl_bps"].notna()] if not sig.empty else sig
    return {"net": _mean_t(d["taker_pnl_bps"]) if len(d) else _mean_t([]),
            "gross": _mean_t(d["gross_bps"]) if len(d) else _mean_t([])}


def _maker_section(sig: pd.DataFrame) -> dict:
    d = sig[sig["is_signal"] & sig["maker_outcome"].notna()] if not sig.empty else sig
    n = len(d)
    if n == 0:
        return {"signals": 0, "fills": 0, "fill_rate": None, "pnl_per_fill": _mean_t([]),
                "cf_filled": _mean_t([]), "cf_unfilled": _mean_t([]), "adverse_bps": None}
    f = d[d["maker_outcome"] == "fill"]
    u = d[d["maker_outcome"] != "fill"]
    pnl = f["side"] * (f["taker_exit"] - f["maker_fill_price"]) / f["maker_fill_price"] * 1e4
    cf_f, cf_u = _mean_t(f["cf_return_bps"]), _mean_t(u["cf_return_bps"])
    adverse = None
    if cf_f["mean"] is not None and cf_u["mean"] is not None:
        adverse = cf_f["mean"] - cf_u["mean"]
    return {"signals": n, "fills": len(f), "fill_rate": len(f) / n, "pnl_per_fill": _mean_t(pnl),
            "cf_filled": cf_f, "cf_unfilled": cf_u, "adverse_bps": adverse}


def _trades_section(conn, since_ms) -> dict:
    t = pd.read_sql_query(
        "SELECT t.*, r.mode AS run_mode FROM trades t LEFT JOIN runs r ON r.run_id=t.run_id "
        "WHERE t.exit_ms>=?", conn, params=(since_ms or 0,))
    out = {}
    for mode, g in (t.groupby("run_mode") if not t.empty else []):
        pnl = g["pnl_bps_price"].fillna(g["pnl_bps_mid"])
        out[str(mode)] = {
            "trades": len(g), "pnl_bps": _mean_t(pnl),
            "pnl_net_sum": _f(g["pnl_net"].sum()) if g["pnl_net"].notna().any() else None,
            "pnl_net_mean": _f(g["pnl_net"].mean()) if g["pnl_net"].notna().any() else None,
            "stop_share": float((g["reason"] == "stop").mean()),
            "reasons": {str(k): int(v) for k, v in g["reason"].value_counts().items()},
            "adverse_bps": _f(g["adverse_bps"].mean()) if g["adverse_bps"].notna().any() else None}
    return out


def compute_report(conn, since_ms: Optional[int] = None, entry_z: Optional[float] = None) -> dict:
    """Everything the text report shows, from an open sqlite3 connection to the journal."""
    entry_z = _entry_z(conn) if entry_z is None else entry_z
    bars = _bars_frame(conn, since_ms)
    sig = _signals(conn, since_ms, entry_z, bars)
    runs = conn.execute("SELECT COUNT(*), MIN(started_ms), MAX(COALESCE(ended_ms, started_ms)) FROM runs"
                        ).fetchone()
    decisions = ({str(k): int(v) for k, v in sig["decision"].value_counts().items()} if not sig.empty
                 else {})
    reasons = ({str(k): int(v) for k, v in sig[sig["is_signal"]]["reason"].value_counts().items()}
               if not sig.empty else {})
    risk = {str(k): int(n) for k, n in conn.execute(
        "SELECT kind, COUNT(*) FROM risk_events WHERE ts_ms>=? GROUP BY kind ORDER BY 2 DESC",
        (since_ms or 0,))}
    return {
        "entry_z": entry_z, "since_ms": since_ms,
        "runs": {"n": runs[0], "first_ms": runs[1], "last_ms": runs[2]},
        "bars": int(len(bars)),
        "signals": {"rows": int(len(sig)), "signals": int(sig["is_signal"].sum()) if not sig.empty else 0,
                    "decisions": decisions, "signal_outcomes": reasons,
                    "with_shadow": int(sig["taker_pnl_bps"].notna().sum()) if not sig.empty else 0},
        "ic": _ic_section(bars) if not bars.empty else {"blocks": 0, "ic": None, "t": None, "p": None,
                                                        "per_symbol": {}, "bars": 0, "approx_bars": 0},
        "taker": _taker_section(sig),
        "maker": _maker_section(sig),
        "trades": _trades_section(conn, since_ms),
        "risk_events": risk,
    }


def _n(x, fmt="{:+.2f}") -> str:
    return "n/a" if x is None else fmt.format(x)


def render(r: dict) -> str:
    """Plain-text report."""
    L = []
    add = L.append

    def row(label, value, ref=""):
        add(f"  {label:<34}{value:<26}{ref}")

    add("autotrade forward-test report")
    add("=" * 78)
    runs = r["runs"]
    add(f"runs: {runs['n']}   bars: {r['bars']}   entry |z| > {r['entry_z']}")
    add("expected P&L is negative (DESIGN.md): this tests the signal, not a money-maker")
    sg = r["signals"]
    add("")
    add("signals")
    row("signal rows (one per bar)", sg["rows"])
    row("signals (|z| > entry)", sg["signals"])
    row("with taker/maker shadow resolved", sg["with_shadow"])
    if sg["decisions"]:
        row("decisions", ", ".join(f"{k}={v}" for k, v in sorted(sg["decisions"].items())))
    if sg["signal_outcomes"]:
        row("what happened to signals", ", ".join(f"{k}={v}" for k, v in sorted(sg["signal_outcomes"].items())))
    ic = r["ic"]
    add("")
    add("forward IC: sig_poc vs next-bar mid return, rank IC per 4 h block")
    row("blocks", ic["blocks"], "need ~7 for t = 3")
    row("mean IC", _n(ic["ic"], "{:+.3f}"), f"research holdout {REF['ic']:+.2f}")
    row("t / one-sided p", f"{_n(ic['t'], '{:+.2f}')} / {_n(ic['p'], '{:.3f}')}")
    row("bars with approximated history", f"{ic['approx_bars']} of {ic['bars']}",
        "approx_share > 0")
    for sid, s in sorted(ic["per_symbol"].items()):
        row(f"  symbol {sid}", f"blocks={s['blocks']} ic={_n(_f(s['ic']), '{:+.3f}')}")
    tk = r["taker"]
    add("")
    add("taker shadow: enter at the touch at t, exit at the touch at t+1 (bp per signal)")
    row("signals", tk["net"]["n"])
    row("gross (mid to mid)", _n(tk["gross"]["mean"]), f"research {REF['taker_gross_bps']:+.2f}")
    row("net (touch to touch)", _n(tk["net"]["mean"]),
        f"research {REF['taker_net_bps']:+.2f}   t={_n(tk['net']['t'])}")
    mk = r["maker"]
    add("")
    add("maker sim: limit at our touch, filled when a later quote crosses it within the ttl")
    row("signals / fills", f"{mk['signals']} / {mk['fills']}")
    row("fill rate", _n(mk["fill_rate"], "{:.0%}"), f"research ~{REF['maker_fill_rate']:.0%}")
    row("pnl per fill (bp)", _n(mk["pnl_per_fill"]["mean"]), f"research ~{REF['maker_bps_per_fill']:+.1f}")
    row("mid return, filled (bp)", _n(mk["cf_filled"]["mean"]))
    row("mid return, unfilled (bp)", _n(mk["cf_unfilled"]["mean"]))
    row("adverse selection (bp)", _n(mk["adverse_bps"]), "filled minus unfilled")
    add("")
    add("trades (by run mode: dry_run = sim broker, demo = real demo orders)")
    if not r["trades"]:
        add("  none")
    for mode, t in sorted(r["trades"].items()):
        row(f"{mode}: trades", t["trades"])
        row(f"{mode}: pnl per trade (bp)", _n(t["pnl_bps"]["mean"]), f"t={_n(t['pnl_bps']['t'])}")
        row(f"{mode}: net pnl (account ccy)", _n(t["pnl_net_sum"]),
            f"mean {_n(t['pnl_net_mean'], '{:+.4f}')}")
        row(f"{mode}: stop share", _n(t["stop_share"], "{:.1%}"))
        row(f"{mode}: exit reasons", ", ".join(f"{k}={v}" for k, v in sorted(t["reasons"].items())))
        row(f"{mode}: adverse (bp)", _n(t["adverse_bps"]))
    add("")
    add("risk events")
    if not r["risk_events"]:
        add("  none")
    for k, n in r["risk_events"].items():
        add(f"  {k:<34}{n}")
    return "\n".join(L)
