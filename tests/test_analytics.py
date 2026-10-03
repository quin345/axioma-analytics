"""Unit tests for the tick analytics layer (no data source required)."""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from app import analytics, synthetic
from app.schema import build_column_map, normalise


@pytest.fixture(scope="module")
def frame():
    raw = synthetic.generate("EURUSD", ticks=5000, seed=42)
    return analytics.enrich(analytics.prepare(raw))


# ---------- schema mapping ----------

def test_column_map_matches_common_aliases():
    cm = build_column_map(["EventTime", "Symbol", "BidPrice", "AskPrice", "LastSize"])
    assert cm.ts == "EventTime"
    assert cm.symbol == "Symbol"
    assert cm.bid == "BidPrice"
    assert cm.ask == "AskPrice"
    assert cm.volume == "LastSize"
    assert cm.valid


def test_column_map_rejects_non_tick_tables():
    cm = build_column_map(["_rid", "_ts"])
    assert not cm.valid


def test_normalise_derives_mid_from_quotes():
    raw = pd.DataFrame({
        "t": pd.date_range("2026-01-01", periods=3, freq="1s", tz="UTC"),
        "sym": ["eurusd"] * 3, "b": [1.0849, 1.0850, 1.0851], "a": [1.0851, 1.0852, 1.0853],
    })
    cm = build_column_map(["t", "sym", "b", "a"])
    out = normalise(raw, cm)
    assert len(out) == 3
    assert out["symbol"].iloc[0] == "EURUSD"          # upper-cased
    assert out["mid"].iloc[0] == pytest.approx(1.0850)
    assert out["bid"].notna().all() and out["ask"].notna().all()


# ---------- summary ----------

def test_summary_counts(frame):
    s = analytics.summary(frame)
    assert s["ticks"] == len(frame)
    assert s["high"] >= s["low"]
    assert s["open"] == pytest.approx(float(frame["mid"].iloc[0]))
    assert s["close"] == pytest.approx(float(frame["mid"].iloc[-1]))
    assert s["avg_spread"] > 0
    assert s["buy_ticks"] + s["sell_ticks"] + s["unchanged_ticks"] == len(frame)


def test_summary_handles_empty():
    assert analytics.summary(pd.DataFrame())["ticks"] == 0


def test_prepare_derives_mid_from_last_only():
    raw = pd.DataFrame({"ts": pd.date_range("2026-01-01", periods=4, freq="1s", tz="UTC"),
                        "last": [1.0, 1.1, 1.2, 1.15]})
    out = analytics.prepare(raw)
    assert out["mid"].tolist() == [1.0, 1.1, 1.2, 1.15]


# ---------- bars / microstructure ----------

def test_ohlc_bars_are_consistent(frame):
    r = analytics.ohlcv(frame, "1m")
    bars = r["bars"]
    assert bars, "expected at least one bar"
    for b in bars:
        assert None not in (b["l"], b["o"], b["h"], b["c"]), "empty bars must be dropped"
        assert b["l"] <= b["o"] <= b["h"]
        assert b["l"] <= b["c"] <= b["h"]
    # Bars are chronological.
    assert [b["t"] for b in bars] == sorted(b["t"] for b in bars)


def test_microstructure_trade_sign_balances(frame):
    m = analytics.microstructure(frame)
    ts = m["trade_sign"]
    assert ts["buy_ticks"] + ts["sell_ticks"] + ts["unchanged_ticks"] == len(frame)
    assert -1.0 <= (ts["imbalance"] or 0) <= 1.0


def test_order_flow_within_bounds(frame):
    ofi = analytics.microstructure(frame)["order_flow"]
    assert ofi
    assert all(-1.0 <= p["imbalance"] <= 1.0 for p in ofi)


# ---------- profile / distribution ----------

def test_volume_profile_poc_inside_range(frame):
    vp = analytics.volume_profile(frame, bins=30)
    lo, hi = frame["mid"].min(), frame["mid"].max()
    assert lo <= vp["poc"] <= hi
    assert sum(b["volume"] for b in vp["bins"]) == pytest.approx(frame["volume"].sum(), rel=1e-6)


def test_return_distribution_stats(frame):
    d = analytics.return_distribution(frame, bins=25)
    assert d["histogram"]
    assert d["stats"]["std_bps"] >= 0
    assert 0.0 <= d["stats"]["bullish_ratio"] <= 1.0


def test_drawdown_is_non_positive(frame):
    dd = analytics.drawdown(frame)
    assert dd["max_drawdown_pct"] <= 0
    assert all(p["dd"] <= 0 for p in dd["series"])


def test_behaviour_bounds(frame):
    b = analytics.behaviour(frame)
    assert 0.0 <= (b["efficiency_ratio"] or 0) <= 1.0
    assert len(b["autocorrelation"]) >= 2


def test_hourly_profile_covers_buckets(frame):
    h = analytics.hourly_profile(frame)["hourly"]
    assert h
    assert sum(x["ticks"] for x in h) == len(frame)


# ---------- reporting ----------

def test_build_report_is_json_serialisable(frame):
    report = analytics.build_report(frame, timeframe="1m", window=25)
    json.dumps(report)  # must not raise
    assert set(report) >= {"summary", "ohlcv", "microstructure", "volume_profile",
                           "rolling", "distribution", "drawdown", "behaviour", "hourly"}


def test_no_nan_leaks_into_report(frame):
    """NaN/Inf must become None so the payload is valid JSON for the browser."""
    report = analytics.build_report(frame)
    # Strict serialisation (allow_nan=False) raises if any NaN/Infinity remains.
    json.dumps(report, allow_nan=False)


def test_flat_series_does_not_crash():
    """A zero-volatility series must not produce NaNs or divide-by-zero."""
    n = 300
    ts = pd.date_range("2026-01-01", periods=n, freq="1s", tz="UTC")
    flat = pd.DataFrame({"ts": ts, "symbol": "X", "bid": 1.0, "ask": 1.0001,
                         "last": 1.0, "volume": 0.0})
    d = analytics.enrich(analytics.prepare(flat))
    json.dumps(analytics.build_report(d))
