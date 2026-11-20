import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from autotrade._research import clean, features, screen
from autotrade.features import FeatureEngine, HISTORY_BARS
from autotrade.types import Bar

DB = Path("/root/axioma-analytics/webapp/data/axioma.db")
ACCOUNTS = {"XAUUSD": ("49208255", 136), "BTCUSD": ("48449283", 114)}
WINDOW_BARS = 25    # compared slots per window (each append runs the research pipeline, ~60 ms)
N_WINDOWS = 2       # per instrument: the last two data restarts (outage / weekend / break -> data)
SLOW = os.environ.get("AUTOTRADE_SLOW") == "1"  # full-history replay, minutes of CPU: opt in
FULL_BARS = 1500
COLS = ["poc_dist_240", "range_pos_12", "events_z", "z_poc", "z_range"]


def nz(x):
    return float("nan") if x is None else float(x)


def to_bar(r):
    return Bar(symbol_id=int(r["symbol_id"]), slot_end=pd.Timestamp(r["slot_end"]).to_pydatetime(),
               has_data=bool(r["has_data"]) and r["mid_close"] is not None and not pd.isna(r["mid_close"]),
               events=int(r["events"] or 0), mid_open=nz(r["mid_open"]), mid_high=nz(r["mid_high"]),
               mid_low=nz(r["mid_low"]), mid_close=nz(r["mid_close"]), spread_mean=nz(r["spread_mean"]),
               spread_median=nz(r["spread_median"]), rel_spread_mean=nz(r["rel_spread_mean"]),
               realised_vol_pct=nz(r["realised_vol_pct"]), vap_240m_poc=nz(r["vap_240m_poc"]),
               vap_240m_va_low=nz(r["vap_240m_va_low"]), vap_240m_va_high=nz(r["vap_240m_va_high"]),
               vap_240m_volume_events=nz(r["vap_240m_volume_events"]), approx_share=0.0)


def _load_all():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    frames = [pd.read_sql("select * from metrics_5m where ctid_trader_account=? and symbol_id=? order by slot_end",
                          conn, params=(acct, sid)) for acct, sid in ACCOUNTS.values()]
    conn.close()
    return pd.concat(frames, ignore_index=True)


def _batch(allm):
    """research batch path, as run_screen / screen.trade_test do"""
    f = features.build_features(clean.clean_metrics(allm))
    f = screen.add_hypothesis_signals(f)
    f = f.sort_values(["ctid_trader_account", "symbol_id", "slot_end"])
    g = f.groupby(["ctid_trader_account", "symbol_id"])
    f["z_poc"] = g["hyp_poc_reversion_4h"].transform(features._rolling_z)
    f["z_range"] = g["hyp_range_reversal_1h"].transform(features._rolling_z)
    return f


def _compare(eng, recs, batch, acct, sid):
    """Append recs bar by bar; return (n, max abs diff per column, NaN-pattern mismatches, values)."""
    b = batch[(batch.ctid_trader_account == acct) & (batch.symbol_id == sid)].set_index("slot_end")
    maxd = {c: 0.0 for c in COLS}
    mism, zs = 0, []
    for r in recs:
        row = eng.append(to_bar(r))
        key = pd.Timestamp(r["slot_end"])
        for c in COLS:
            x, y = getattr(row, c), b.loc[key, c]
            if math.isnan(x) != bool(pd.isna(y)):
                mism += 1
            elif not math.isnan(x):
                maxd[c] = max(maxd[c], abs(x - y))
        zs.append(row.z_poc)
    return len(recs), maxd, mism, zs


@pytest.mark.skipif(not DB.exists(), reason="axioma.db not present")
def test_p2_engine_matches_research_batch():
    allm = _load_all()
    batch = _batch(allm)
    nan_seen = val_seen = False
    for name, (acct, sid) in ACCOUNTS.items():
        recs = allm[(allm.ctid_trader_account == acct) & (allm.symbol_id == sid)].to_dict("records")
        hd = [bool(r["has_data"]) and r["mid_close"] is not None and not pd.isna(r["mid_close"]) for r in recs]
        starts = [i for i in range(HISTORY_BARS + 20, len(hd) - WINDOW_BARS)
                  if hd[i] and not any(hd[i - 12:i])]
        for i in starts[-N_WINDOWS:] or [len(recs) - WINDOW_BARS]:
            lo = i - 20
            eng = FeatureEngine()
            eng.load([to_bar(r) for r in recs[lo - HISTORY_BARS:lo]])
            n, maxd, mism, zs = _compare(eng, recs[lo:lo + WINDOW_BARS], batch, acct, sid)
            print("P2", name, recs[lo]["slot_end"], n, maxd, "nan mismatches", mism)
            assert mism == 0 and max(maxd.values()) < 1e-9, (name, maxd, mism)
            nan_seen |= any(math.isnan(v) for v in zs)
            val_seen |= any(not math.isnan(v) for v in zs)
    assert nan_seen and val_seen  # windows contain both the NaN pattern and real values


@pytest.mark.skipif(not (SLOW and DB.exists()), reason="set AUTOTRADE_SLOW=1 for the full-history replay")
def test_p2_full_history_replay():
    allm = _load_all()
    batch = _batch(allm)
    for name, (acct, sid) in ACCOUNTS.items():
        recs = allm[(allm.ctid_trader_account == acct) & (allm.symbol_id == sid)].to_dict("records")[-FULL_BARS:]
        eng = FeatureEngine()
        eng.load([to_bar(r) for r in recs[:HISTORY_BARS]])
        n, maxd, mism, _ = _compare(eng, recs[HISTORY_BARS:], batch, acct, sid)
        print("P2 full", name, n, maxd, "nan mismatches", mism)
        assert mism == 0 and max(maxd.values()) < 1e-9


def _bar(i, **kw):
    base = dict(symbol_id=1, slot_end=datetime(2026, 11, 18, 0, 0, tzinfo=timezone.utc) + pd.Timedelta(minutes=5 * i),
                has_data=True, events=100 + i, mid_open=100.0, mid_high=101.0 + i * .01, mid_low=99.0,
                mid_close=100.0 + (i % 7) * .1, spread_mean=.02, spread_median=.02, rel_spread_mean=2e-4,
                realised_vol_pct=5.0, vap_240m_poc=100.2, vap_240m_va_low=99.5, vap_240m_va_high=100.9,
                vap_240m_volume_events=1000.0, approx_share=0.0)
    base.update(kw)
    return Bar(**base)


def test_spread_median_48_in_price_ints_and_live_flag():
    eng = FeatureEngine()
    eng.load([_bar(i, spread_median=0.01 * (1 + i % 3)) for i in range(59)])
    row = eng.append(_bar(59, spread_median=0.01 * (1 + 59 % 3)))
    assert row.spread_median_48 == pytest.approx(np.median([0.01 * (1 + i % 3) for i in range(12, 60)]) * 100000)
    assert row.events_z_live_only is True and not math.isnan(row.events_z)

    eng = FeatureEngine()
    eng.load([_bar(i, approx_share=1.0 if i == 40 else 0.0) for i in range(59)])
    assert eng.append(_bar(59)).events_z_live_only is False  # a non-live bar sits in the last 48 slots
    eng.load([_bar(i) for i in range(60, 120)])
    assert eng.append(_bar(120)).events_z_live_only is True   # it has rolled out of the window


def test_non_live_bars_carry_nan_events_for_z():
    eng = FeatureEngine()
    eng.load([_bar(i, approx_share=1.0) for i in range(30)])
    row = eng.append(_bar(30))
    assert math.isnan(row.events_z)  # fewer than 12 live events in the window


def test_gap_and_load():
    eng = FeatureEngine()
    eng.load([_bar(i) for i in range(50)])
    row = eng.append(_bar(80))  # 30 missing slots become NaN grid rows
    assert row.slot_end == _bar(80).slot_end and not math.isnan(row.poc_dist_240)
