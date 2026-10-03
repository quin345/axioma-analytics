"""Tests for the medallion book readers (no warehouse required)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from app.books import classify, from_agg, from_levels, is_agg_table, is_levels_table

AGG_COLS = ["timestamp", "eventDate", "symbolId", "total_bid", "total_ask", "best_bid",
            "best_ask", "imbalance", "imbalance_ratio", "vwap_bid", "vwap_ask",
            "vwap_spread", "rel_spread", "rel_vwap_spread"]
LVL_COLS = ["symbolId", "quoteId", "timestamp", "side", "price", "size", "eventDate"]


def _agg_rows():
    t0 = pd.Timestamp("2026-10-03T10:00:00", tz="UTC")
    return pd.DataFrame([
        {"timestamp": t0,                    "eventDate": "2026-10-03", "symbolId": "1",
         "total_bid": 100.0, "total_ask": 60.0, "best_bid": 1.0850, "best_ask": 1.0852,
         "imbalance": 40.0, "imbalance_ratio": 0.25, "vwap_bid": 1.0848, "vwap_ask": 1.0856,
         "vwap_spread": 0.0008, "rel_spread": 0.0002, "rel_vwap_spread": 0.0007},
        {"timestamp": t0 + pd.Timedelta(seconds=1), "eventDate": "2026-10-03", "symbolId": "1",
         "total_bid": 90.0, "total_ask": 90.0, "best_bid": 1.0851, "best_ask": 1.0853,
         "imbalance": 0.0, "imbalance_ratio": 0.0, "vwap_bid": 1.0849, "vwap_ask": 1.0855,
         "vwap_spread": 0.0006, "rel_spread": 0.0002, "rel_vwap_spread": 0.0006},
    ])


def test_detects_table_kinds():
    assert is_agg_table(AGG_COLS) and classify(AGG_COLS) == "agg"
    assert is_levels_table(LVL_COLS) and classify(LVL_COLS) == "levels"
    assert classify(["_rid", "_ts"]) == "flat"


def test_from_agg_maps_best_quotes():
    out = from_agg(_agg_rows())
    assert len(out) == 2
    assert out["bid"].iloc[0] == pytest.approx(1.0850)
    assert out["ask"].iloc[0] == pytest.approx(1.0852)
    assert (out["ask"] > out["bid"]).all()
    assert out["symbol"].tolist() == ["1", "1"]


def test_from_agg_keeps_pipeline_imbalance():
    out = from_agg(_agg_rows())
    assert out["signed_volume"].iloc[0] == pytest.approx(40.0)
    assert out["rel_spread"].iloc[0] == pytest.approx(0.0002)


def test_from_agg_drops_one_sided_rows():
    rows = _agg_rows()
    rows.loc[0, "best_ask"] = np.nan          # one-sided book: no mid possible
    out = from_agg(rows)
    assert len(out) == 1
    assert out["ask"].notna().all()


def test_from_agg_empty_input_returns_frame():
    out = from_agg(pd.DataFrame(columns=AGG_COLS))
    assert out.empty and "mid" in out.columns or out.empty


def _level_rows():
    t0 = pd.Timestamp("2026-10-03T10:00:00", tz="UTC")
    return pd.DataFrame([
        {"symbolId": "1", "quoteId": "a", "timestamp": t0, "side": "bid", "price": 1.0850, "size": 100, "eventDate": "2026-10-03"},
        {"symbolId": "1", "quoteId": "b", "timestamp": t0, "side": "bid", "price": 1.0849, "size": 50,  "eventDate": "2026-10-03"},
        {"symbolId": "1", "quoteId": "c", "timestamp": t0, "side": "ask", "price": 1.0852, "size": 80,  "eventDate": "2026-10-03"},
        {"symbolId": "1", "quoteId": "d", "timestamp": t0, "side": "ask", "price": 1.0853, "size": 20,  "eventDate": "2026-10-03"},
        # second snapshot, tighter
        {"symbolId": "1", "quoteId": "e", "timestamp": t0 + pd.Timedelta(seconds=1), "side": "bid", "price": 1.0851, "size": 10, "eventDate": "2026-10-03"},
        {"symbolId": "1", "quoteId": "f", "timestamp": t0 + pd.Timedelta(seconds=1), "side": "ask", "price": 1.0853, "size": 10, "eventDate": "2026-10-03"},
    ])


def test_from_levels_collapses_to_best_quotes():
    out = from_levels(_level_rows())
    assert len(out) == 2
    assert out["bid"].iloc[0] == pytest.approx(1.0850)   # max bid
    assert out["ask"].iloc[0] == pytest.approx(1.0852)   # min ask
    assert (out["ask"] > out["bid"]).all()


def test_from_levels_depth_curves():
    out = from_levels(_level_rows(), depth_levels=5)
    first = out.iloc[0]
    assert first["levels_bid"] == 2 and first["levels_ask"] == 2
    assert first["bid_depth"] == pytest.approx(150.0)
    assert first["ask_depth"] == pytest.approx(100.0)
    assert first["signed_volume"] == pytest.approx(50.0)
    # cumulative curves start at the touch size
    assert first["bid_curve"][0] == pytest.approx(100.0)
    assert first["ask_curve"][0] == pytest.approx(80.0)


def test_from_levels_respects_depth_limit():
    out = from_levels(_level_rows(), depth_levels=1)
    assert out["bid_depth"].iloc[0] == pytest.approx(100.0)
    assert out["ask_depth"].iloc[0] == pytest.approx(80.0)


def test_from_levels_skips_one_sided_snapshots():
    rows = _level_rows()
    rows = rows[~((rows["side"] == "ask") & (rows["timestamp"] == rows["timestamp"].iloc[0]))]
    out = from_levels(rows)
    assert (out["ask"] > out["bid"]).all()
    assert len(out) <= 2


def test_from_levels_skips_crossed_snapshots():
    rows = _level_rows()
    rows.loc[rows["side"] == "bid", "price"] = 9.0     # bid above ask
    out = from_levels(rows)
    assert (out["bid"] < out["ask"]).all()


def test_readers_feed_the_analytics_pipeline():
    from app import analytics
    for frame in (from_agg(_agg_rows()), from_levels(_level_rows())):
        report = analytics.build_report(analytics.enrich(analytics.prepare(frame)))
        json.dumps(report, allow_nan=False)
        assert report["summary"]["ticks"] > 0
