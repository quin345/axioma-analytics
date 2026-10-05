"""Tests for the gold snapshot reader (no data source required)."""
from __future__ import annotations

import pandas as pd
import pytest

from app.db import TICK_COLUMNS
from app.frames import from_snapshot


def _rows():
    t0 = pd.Timestamp("2026-10-03T10:00:00", tz="UTC")
    return pd.DataFrame([
        {"timestamp": t0, "symbolId": "1", "total_bid": 100.0, "total_ask": 60.0,
         "best_bid": 1.0850, "best_ask": 1.0852, "imbalance": 40.0,
         "imbalance_ratio": 0.25, "vwap_bid": 1.0848, "vwap_ask": 1.0856,
         "vwap_spread": 0.0008, "rel_spread": 0.0002, "rel_vwap_spread": 0.0007},
        {"timestamp": t0 + pd.Timedelta(seconds=1), "symbolId": "1",
         "total_bid": 90.0, "total_ask": 90.0, "best_bid": 1.0851, "best_ask": 1.0853,
         "imbalance": 0.0, "imbalance_ratio": 0.0, "vwap_bid": 1.0849,
         "vwap_ask": 1.0855, "vwap_spread": 0.0006, "rel_spread": 0.0002,
         "rel_vwap_spread": 0.0006},
    ])


def test_maps_best_quotes_to_canonical_frame():
    out = from_snapshot(_rows())
    assert len(out) == 2
    assert list(out.columns) == TICK_COLUMNS
    assert out["bid"].iloc[0] == pytest.approx(1.0850)
    assert out["ask"].iloc[0] == pytest.approx(1.0852)
    assert (out["ask"] > out["bid"]).all()
    assert out["symbol"].tolist() == ["1", "1"]


def test_keeps_pipeline_imbalance_and_spread():
    out = from_snapshot(_rows())
    assert out["signed_volume"].iloc[0] == pytest.approx(40.0)
    assert out["rel_spread"].iloc[0] == pytest.approx(0.0002)
    assert out["imbalance_ratio"].iloc[0] == pytest.approx(0.25)


def test_volume_is_resting_size_in_lots():
    """The feed reports resting size per 100 lots; canonical volume is lots."""
    out = from_snapshot(_rows())
    assert out["volume"].iloc[0] == pytest.approx(1.6)
    assert out["bid_depth"].iloc[0] == pytest.approx(100.0)
    assert out["ask_depth"].iloc[0] == pytest.approx(60.0)


def test_signed_volume_falls_back_to_depth_difference():
    rows = _rows()
    rows["imbalance"] = None            # pipeline did not supply it
    out = from_snapshot(rows)
    assert out["signed_volume"].iloc[0] == pytest.approx(40.0)


def test_drops_one_sided_rows():
    rows = _rows()
    rows.loc[0, "best_ask"] = None       # one-sided book: no mid possible
    out = from_snapshot(rows)
    assert len(out) == 1


def test_drops_crossed_books():
    rows = _rows()
    rows.loc[0, "best_bid"] = 1.0860    # bid above ask: desynchronised
    out = from_snapshot(rows)
    assert len(out) == 1


def test_drops_non_positive_prices():
    rows = _rows()
    rows.loc[0, "best_bid"] = 0.0
    out = from_snapshot(rows)
    assert len(out) == 1


def test_sorted_by_timestamp():
    rows = _rows().iloc[::-1].reset_index(drop=True)
    out = from_snapshot(rows)
    assert out["ts"].is_monotonic_increasing


def test_empty_input_returns_canonical_columns():
    out = from_snapshot(pd.DataFrame(columns=TICK_COLUMNS))
    assert out.empty
    assert list(out.columns) == TICK_COLUMNS


def test_readers_feed_the_analytics_pipeline():
    from app import analytics
    report = analytics.build_report(from_snapshot(_rows()))
    assert report["summary"]["ticks"] == 2