"""P4 drift check (DESIGN.md section 4): research feature code over the bot's own `bars` table
versus the FeatureRows the bot journalled in `signals`.

The bot computes features on a rolling 150-bar window with explicit live flags; this recomputes
them over all stored bars with the batch research path (FeatureEngine.research_features, i.e.
clean_metrics + build_features + signals + rolling z). Any difference is window truncation or
live-flag handling, not a formula difference (formulas are shared). events_z is expected to differ
over windows that contain warm-up bars (the bot blanks them; the bars table does not record the
flag), so it is reported separately.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .features import FeatureEngine
from .types import Bar

NAN = float("nan")
COLS = ("z_poc", "z_range", "events_z")


def _bar(r) -> Bar:
    def f(x):
        return NAN if x is None or pd.isna(x) else float(x)
    return Bar(symbol_id=int(r.symbol_id), slot_end=datetime.fromtimestamp(r.slot_end_ms / 1000, tz=timezone.utc),
               has_data=bool(r.has_data), events=int(r.events or 0), mid_open=f(r.mid_open),
               mid_high=f(r.mid_high), mid_low=f(r.mid_low), mid_close=f(r.mid_close),
               spread_mean=f(r.spread_mean), spread_median=f(r.spread_median),
               rel_spread_mean=f(r.rel_spread_mean), realised_vol_pct=f(r.realised_vol_pct),
               vap_240m_poc=f(r.vap_poc), vap_240m_va_low=f(r.vap_va_low), vap_240m_va_high=f(r.vap_va_high),
               vap_240m_volume_events=f(r.vap_volume_events), approx_share=f(r.approx_share) or 0.0)


def parity_report(conn, tf=None) -> dict:
    """Per symbol: compared rows, max abs difference per column, NaN-pattern mismatches, side agreement.
    tf: the instance timeframe (autotrade/timeframe.py; default 5m)."""
    from .timeframe import TF_5M
    tf = tf or TF_5M
    bars = pd.read_sql_query("SELECT * FROM bars ORDER BY symbol_id, slot_end_ms", conn)
    sigs = pd.read_sql_query("SELECT symbol_id, slot_end_ms, z_poc, z_range, events_z FROM signals", conn)
    if bars.empty or sigs.empty:
        return {"symbols": {}, "rows": 0}
    items = [(_bar(r), r.approx_share < 1.0) for r in bars.itertuples()]
    f = FeatureEngine.research_features(FeatureEngine.frame(items, tf), tf)
    se = pd.to_datetime(f["slot_end"], utc=True)
    f["slot_end_ms"] = (se - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(milliseconds=1)
    ref = f.set_index(["symbol_id", "slot_end_ms"])
    out = {}
    for sid, g in sigs.groupby("symbol_id"):
        stats = {"rows": 0, "max_abs": {c: 0.0 for c in COLS}, "nan_mismatch": {c: 0 for c in COLS},
                 "side_agree": 0, "side_total": 0}
        for r in g.itertuples():
            key = (int(sid), int(r.slot_end_ms))
            if key not in ref.index:
                continue
            stats["rows"] += 1
            for c in COLS:
                a, b = getattr(r, c), ref.at[key, c]
                a_nan, b_nan = a is None or pd.isna(a), pd.isna(b)
                if a_nan != b_nan:
                    stats["nan_mismatch"][c] += 1
                elif not a_nan:
                    stats["max_abs"][c] = max(stats["max_abs"][c], abs(float(a) - float(b)))
            za, zb = r.z_poc, ref.at[key, "z_poc"]
            if not (pd.isna(za) or pd.isna(zb)) and (abs(za) > 1 or abs(zb) > 1):
                stats["side_total"] += 1
                stats["side_agree"] += int(np.sign(za) == np.sign(zb) and abs(za) > 1 and abs(zb) > 1)
        out[int(sid)] = stats
    return {"symbols": out, "rows": sum(s["rows"] for s in out.values())}


def render(rep: dict) -> str:
    L = ["autotrade parity (P4): research features over the bars table vs journalled features",
         f"rows compared: {rep['rows']}"]
    for sid, s in sorted(rep["symbols"].items()):
        L.append(f"symbol {sid}: rows={s['rows']}")
        for c in COLS:
            L.append(f"  {c:<10} max|diff|={s['max_abs'][c]:.3e}  nan_mismatch={s['nan_mismatch'][c]}")
        if s["side_total"]:
            L.append(f"  signal side agreement (|z|>1 on either side): "
                     f"{s['side_agree']}/{s['side_total']} = {s['side_agree'] / s['side_total']:.1%}")
    if rep["rows"] == 0:
        L.append("nothing to compare (no bars/signals journalled yet)")
    return "\n".join(L)
