"""Unit tests for the tick analytics layer (no data source required)."""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from app import analytics
from app.frames import from_snapshot


def _snapshots(n: int = 5000, seed: int = 42) -> pd.DataFrame:
    """Raw gold-shaped snapshots with a random-walk mid price."""
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2026-10-03T10:00:00", periods=n, freq="1s", tz="UTC")
    mid = 1.0850 + np.cumsum(rng.normal(0, 2e-5, n))
    half = rng.uniform(0.5e-5, 2e-5, n)
    bid, ask = mid - half, mid + half
    return pd.DataFrame({
        "timestamp": ts,
        "symbolId": "1",
        "total_bid": rng.integers(50, 500, n).astype(float),
        "total_ask": rng.integers(50, 500, n).astype(float),
        "best_bid": bid,
        "best_ask": ask,
        "imbalance": rng.normal(0, 50, n),
        "imbalance_ratio": rng.uniform(-1, 1, n),
        "vwap_bid": bid - 1e-5,
        "vwap_ask": ask + 1e-5,
        "vwap_spread": ask - bid + 2e-5,
        "rel_spread": (ask - bid) / mid,
        "rel_vwap_spread": (ask - bid + 2e-5) / mid,
    })


@pytest.fixture(scope="module")
def frame():
    return analytics.enrich(analytics.prepare(from_snapshot(_snapshots())))


# ---------- reader / prepare ----------

def test_reader_produces_the_canonical_frame(frame):
    assert {"ts", "symbol", "bid", "ask", "last", "volume", "mid"} <= set(frame.columns)


def test_prepare_derives_mid_from_last_only():
    raw = pd.DataFrame({"ts": pd.date_range("2026-01-01", periods=4, freq="1s", tz="UTC"),
                        "last": [1.0, 1.1, 1.2, 1.15]})
    assert analytics.prepare(raw)["mid"].tolist() == [1.0, 1.1, 1.2, 1.15]


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


# ---------- bars / microstructure ----------
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
