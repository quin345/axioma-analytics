"""1-minute instance parity: bars from KQL-shaped buckets == export_flat.build_rows(tf=TIMEFRAMES["1m"]),
FeatureEngine(tf=TF_1M) == the research batch path with tf=TF_1M (synthetic and, when present, the
real metrics_1m rows), and the 5-minute engine is bit-for-bit the pre-instance pipeline."""
import math
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from autotrade import kql_source as kq
from autotrade._research import clean, export_flat as ef, features, hypotheses, screen
from autotrade.bars import replay_bars, slot_bar
from autotrade.features import FeatureEngine
from autotrade.timeframe import TF_1M, TF_5M
from autotrade.types import Bar

T0 = 1_800_000_000_000
DB = Path("/root/axioma-analytics/webapp/data/axioma.db")
COLS = ["poc_dist_240", "range_pos_12", "events_z", "z_poc", "z_range"]
BAR_FIELDS = ["mid_open", "mid_high", "mid_low", "mid_close", "spread_mean", "spread_median", "rel_spread_mean",
              "realised_vol_pct", "vap_240m_poc", "vap_240m_va_low", "vap_240m_va_high", "vap_240m_volume_events"]


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _events(seed=11, hours=2, hole=(70 * 60_000, 72 * 60_000 + 30_000)):
    rnd = random.Random(seed)
    ev, mid, ts, seq = [], 3_600_000_000, T0 - hours * 3600_000, 0
    while ts < T0:
        ts += rnd.choice((300, 1200, 2500, 4000))
        if hole and T0 - hours * 3600_000 + hole[0] < ts < T0 - hours * 3600_000 + hole[1]:
            continue
        mid += rnd.randint(-400, 400)
        seq += 1
        ev.append({"symbol_id": 7, "ts_ms": ts, "seq": seq, "best_bid": mid - 15,
                   "best_ask": mid + 15 + rnd.randint(0, 6)})
    return ev


def test_one_minute_bars_from_kql_buckets_match_export_flat():
    ev = _events()
    lo = ev[0]["ts_ms"] // 10_000 * 10_000
    r = kq.KqlRequest("agg_dom", "1", (7,), lo, T0)
    rows = kq.reference_rows(r, ev)
    kqlb = kq.parse_result(r, rows).buckets[7]
    last_ts = {}
    for e in ev:
        b = e["ts_ms"] // 10_000 * 10_000
        last_ts[b] = max(last_ts.get(b, 0), e["ts_ms"])
    series = [{"bucket": _iso(x["ms"]), "timestamp": _iso(last_ts[x["ms"]]), "best_bid": x["best_bid"],
               "best_ask": x["best_ask"], "rel_spread": 1e-5, "events": x["events"]}
              for x in rows if x["row_kind"] == "b"]
    tf_e = ef.TIMEFRAMES["1m"]
    ends = [datetime.fromtimestamp(ms / 1000, tz=timezone.utc) for ms in range(T0 - 70 * 60_000, T0 + 1, 60_000)]
    data_start = datetime.fromtimestamp(lo / 1000, tz=timezone.utc)
    exp = ef.build_rows({}, series, [], ends, 100000, data_start, tf=tf_e)
    got = replay_bars(7, kqlb, int(ends[0].timestamp() * 1000), T0, lo, tf=TF_1M)
    assert len(got) == len(exp) == 71
    assert sum(1 for e in exp if not e["has_data"]) >= 1                 # the 1-minute hole is in there
    for b, e in zip(got, exp):
        assert b.events == e["events"] and b.has_data == bool(e["has_data"]) and b.approx_share == 0.0
        assert b.vap_coverage == e["vap_60m_coverage"]
        for k in BAR_FIELDS:
            x = getattr(b, k)
            y = e[k.replace("vap_240m_", "vap_60m_")]
            y = float("nan") if y is None else float(y)
            assert math.isnan(x) == math.isnan(y), (b.slot_end, k)
            if not math.isnan(x):
                assert abs(x - y) <= 1e-9 * max(1.0, abs(y)), (b.slot_end, k, x, y)
    # the 1-minute bar is not the 5-minute one: slot of 1 minute, VAP window of 60 minutes
    end = datetime.fromtimestamp(T0 / 1000, tz=timezone.utc)
    b1, b5 = slot_bar(7, end, kqlb, tf=TF_1M), slot_bar(7, end, kqlb)
    assert b1.events < b5.events and b1.vap_240m_volume_events < b5.vap_240m_volume_events


def _bar(i, slot_min, rnd, **kw):
    mid = 100.0 + math.sin(i / 9.0) + rnd.random() * 0.3
    base = dict(symbol_id=1, slot_end=datetime(2026, 11, 18, tzinfo=timezone.utc) + pd.Timedelta(minutes=slot_min * i),
                has_data=True, events=80 + rnd.randint(0, 60), mid_open=mid - .05, mid_high=mid + .2,
                mid_low=mid - .2, mid_close=mid, spread_mean=.02, spread_median=.02, rel_spread_mean=2e-4,
                realised_vol_pct=5.0 + rnd.random(), vap_240m_poc=100.0 + rnd.random() * .2, vap_240m_va_low=99.4,
                vap_240m_va_high=100.8, vap_240m_volume_events=1000.0, approx_share=0.0)
    base.update(kw)
    return Bar(**base)


def _metrics(bars, tf):
    """The bars as a research metrics_<tf> frame (the long VAP window from the bar, shorter ones NaN)."""
    return FeatureEngine.frame([(b, True) for b in bars], tf)


def _batch(df, rtf, poc_key, range_key, zw):
    f = features.build_features(clean.clean_metrics(df, tf=rtf), tf=rtf)
    f = screen.add_hypothesis_signals(f, tf=rtf)                      # every hypothesis of the timeframe
    g = f.groupby(["ctid_trader_account", "symbol_id"])
    f["z_poc"] = g[f"hyp_{poc_key}"].transform(lambda s: features._rolling_z(s, zw))
    f["z_range"] = g[f"hyp_{range_key}"].transform(lambda s: features._rolling_z(s, zw))
    return f.set_index("slot_end")


def _compare(eng, recs, batch, poc_col):
    maxd, mism, vals = {c: 0.0 for c in COLS}, 0, 0
    for b in recs:
        row = eng.append(b)
        if not b.has_data:
            continue
        ref = batch.loc[pd.Timestamp(b.slot_end)]
        for c in COLS:
            x, y = getattr(row, c), ref[poc_col if c == "poc_dist_240" else c]
            if math.isnan(x) != bool(pd.isna(y)):
                mism += 1
            elif not math.isnan(x):
                maxd[c] = max(maxd[c], abs(x - y))
                vals += 1
    return maxd, mism, vals


def test_one_minute_engine_matches_research_batch_tf_1m():
    rnd = random.Random(3)
    bars = [_bar(i, 1, rnd) for i in range(260) if not 150 <= i < 158]     # an 8-minute hole: NaN grid rows
    batch = _batch(_metrics(bars, TF_1M), TF_1M.research, "poc_reversion_1h", "range_reversal_12m", 60)
    eng = FeatureEngine(tf=TF_1M)
    eng.load(bars[:120])
    maxd, mism, vals = _compare(eng, bars[120:], batch, "poc_dist_60")
    assert mism == 0 and max(maxd.values()) < 1e-9 and vals > 100, (maxd, mism)
    # the 1-minute z uses 60 bars: the 5-minute engine (48 bars, 4 h keys) gives other numbers
    eng5 = FeatureEngine()
    eng5.load([_bar(i, 5, random.Random(3)) for i in range(120)])
    assert eng.append(_bar(260, 1, rnd)).sig_poc != eng5.append(_bar(120, 5, rnd)).sig_poc


def test_five_minute_engine_is_the_pre_instance_pipeline_bit_for_bit():
    """The research calls the 5-minute bot made before timeframes existed (no tf arguments, HYPOTHESES
    filtered by key, _rolling_z default window) give exactly (==) the same FeatureRows."""
    rnd = random.Random(8)
    bars = [_bar(i, 5, rnd) for i in range(220) if not 100 <= i < 104]
    old_sig = [h for h in hypotheses.HYPOTHESES if h.key in ("poc_reversion_4h", "range_reversal_1h")]
    eng = FeatureEngine()
    assert eng.tf is TF_5M and eng.history == 150
    for i, b in enumerate(bars):
        row = eng.append(b)
        if i < 150:
            continue
        items = [(x, True) for x in bars[max(0, i - 149):i + 1]]
        df = FeatureEngine.frame(items)
        f = features.build_features(clean.clean_metrics(df))
        f = screen.add_hypothesis_signals(f, old_sig)
        g = f.groupby(["ctid_trader_account", "symbol_id"])
        last = f.iloc[-1]
        z = g["hyp_poc_reversion_4h"].transform(features._rolling_z).iloc[-1]
        zr = g["hyp_range_reversal_1h"].transform(features._rolling_z).iloc[-1]
        for got, want in ((row.poc_dist_240, last["poc_dist_240"]), (row.sig_poc, last["hyp_poc_reversion_4h"]),
                          (row.z_poc, z), (row.z_range, zr), (row.events_z, last["events_z"]),
                          (row.range_pos_12, last["range_pos_12"])):
            assert (math.isnan(got) and math.isnan(want)) or got == want


ACCOUNTS_1M = {"XAUUSD-old": ("49208255", 136), "XAUUSD": ("48664595", 41), "BTCUSD": ("48664595", 10026)}


def _to_bar(r):
    nz = lambda x: float("nan") if x is None or pd.isna(x) else float(x)     # noqa: E731
    return Bar(symbol_id=int(r["symbol_id"]), slot_end=pd.Timestamp(r["slot_end"]).to_pydatetime(),
               has_data=bool(r["has_data"]) and r["mid_close"] is not None and not pd.isna(r["mid_close"]),
               events=int(r["events"] or 0), mid_open=nz(r["mid_open"]), mid_high=nz(r["mid_high"]),
               mid_low=nz(r["mid_low"]), mid_close=nz(r["mid_close"]), spread_mean=nz(r["spread_mean"]),
               spread_median=nz(r["spread_median"]), rel_spread_mean=nz(r["rel_spread_mean"]),
               realised_vol_pct=nz(r["realised_vol_pct"]), vap_240m_poc=nz(r["vap_60m_poc"]),
               vap_240m_va_low=nz(r["vap_60m_va_low"]), vap_240m_va_high=nz(r["vap_60m_va_high"]),
               vap_240m_volume_events=nz(r["vap_60m_volume_events"]), approx_share=0.0)


@pytest.mark.skipif(not DB.exists(), reason="axioma.db not present")
def test_p2_one_minute_engine_matches_research_on_metrics_1m():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='metrics_1m'").fetchone():
            pytest.skip("no metrics_1m table")
        frames = {n: pd.read_sql("SELECT * FROM (SELECT * FROM metrics_1m WHERE ctid_trader_account=? AND "
                                 "symbol_id=? ORDER BY slot_end DESC LIMIT 400) ORDER BY slot_end",
                                 conn, params=a) for n, a in ACCOUNTS_1M.items()}
    finally:
        conn.close()
    checked = 0
    for name, m in frames.items():
        if len(m) < TF_1M.history_bars + 30:
            continue
        rtf = TF_1M.research
        f = features.build_features(clean.clean_metrics(m, tf=rtf), tf=rtf)
        f = screen.add_hypothesis_signals(f, tf=rtf)
        f["z_poc"] = f["hyp_poc_reversion_1h"].transform(lambda s: features._rolling_z(s, 60))
        f["z_range"] = f["hyp_range_reversal_12m"].transform(lambda s: features._rolling_z(s, 60))
        batch = f.set_index("slot_end")
        recs = [_to_bar(r) for r in m.to_dict("records")]
        n0 = len(recs) - 40
        eng = FeatureEngine(tf=TF_1M)
        eng.load(recs[n0 - TF_1M.history_bars:n0])
        maxd, mism, vals = _compare(eng, recs[n0:], batch, "poc_dist_60")
        print("P2 1m", name, maxd, "nan mismatches", mism, "values", vals)
        assert mism == 0 and max(maxd.values()) < 1e-9, (name, maxd, mism)
        checked += 1
    assert checked >= 1
