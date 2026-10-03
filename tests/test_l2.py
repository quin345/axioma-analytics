"""Tests for cTrader L2 order-book reconstruction (no warehouse required)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from app.l2 import L2Options, is_l2_table, last_stats, map_l2_columns, reconstruct


def _event(ts, symbol, new=None, deleted=None, digits=2):
    return {
        "symbolId": symbol,
        "newQuotes": json.dumps(new or []),
        "deletedQuotes": json.dumps(deleted or []),
        "digits": float(digits),
        "timestamp": ts,
    }


@pytest.fixture
def simple_book():
    """One symbol, digits=2, two bids and two asks quoted then updated."""
    base = pd.Timestamp("2026-10-03T10:00:00", tz="UTC")
    rows = [
        _event(base, "1", [{"id": "b1", "size": "10", "bid": "100100"},
                           {"id": "a1", "size": "5", "ask": "100300"}]),
        _event(base + pd.Timedelta(seconds=1), "1",
               [{"id": "b2", "size": "7", "bid": "100200"},
                {"id": "a2", "size": "3", "ask": "100400"}]),
        # delete the best ask, so the next best ask becomes the touch
        _event(base + pd.Timedelta(seconds=2), "1", [], ["a1"]),
    ]
    return pd.DataFrame(rows)


def test_detects_l2_schema():
    cols = ["symbolId", "newQuotes", "deletedQuotes", "digits", "timestamp", "id"]
    assert is_l2_table(cols)
    assert not is_l2_table(["_rid", "_ts"])
    assert map_l2_columns(cols)["new_quotes"] == "newQuotes"


def test_prices_are_scaled_by_digits(simple_book):
    ticks = reconstruct(simple_book)
    assert len(ticks) >= 2
    # digits=2 -> 100100 / 100 = 1001.00
    assert ticks["bid"].iloc[0] == pytest.approx(1001.00)
    assert ticks["ask"].iloc[0] == pytest.approx(1003.00)


def test_best_levels_follow_deletions(simple_book):
    ticks = reconstruct(simple_book)
    # First event: bid 1001.00, ask 1003.00
    assert ticks["bid"].iloc[0] == pytest.approx(1001.00)
    assert ticks["ask"].iloc[0] == pytest.approx(1003.00)
    # Second event adds a better bid (1002.00) and a worse ask (1004.00)
    assert ticks["bid"].iloc[1] == pytest.approx(1002.00)
    assert ticks["ask"].iloc[1] == pytest.approx(1003.00)
    # Third deletes a1, so the touch ask becomes 1004.00
    assert ticks["ask"].iloc[2] == pytest.approx(1004.00)


def test_spread_and_mid_are_positive(simple_book):
    ticks = reconstruct(simple_book)
    assert (ticks["ask"] > ticks["bid"]).all()
    assert (ticks["mid"] > 0).all()
    assert (ticks["spread"] > 0).all()


def test_zero_size_removes_a_quote():
    base = pd.Timestamp("2026-10-03T10:00:00", tz="UTC")
    df = pd.DataFrame([
        _event(base, "1", [{"id": "b1", "size": "10", "bid": "100100"},
                           {"id": "a1", "size": "5", "ask": "100300"},
                           {"id": "a2", "size": "4", "ask": "100500"}]),
        _event(base + pd.Timedelta(seconds=1), "1", [{"id": "a1", "size": "0", "ask": "100300"}]),
    ])
    ticks = reconstruct(df)
    assert ticks["ask"].iloc[-1] == pytest.approx(1005.00)


def test_one_sided_book_is_not_emitted():
    base = pd.Timestamp("2026-10-03T10:00:00", tz="UTC")
    df = pd.DataFrame([_event(base, "1", [{"id": "b1", "size": "10", "bid": "100100"}])])
    ticks = reconstruct(df)
    assert ticks.empty


def test_session_gap_resets_the_book():
    """A long silence means new quote ids; stale levels must not leak across it."""
    base = pd.Timestamp("2026-10-03T10:00:00", tz="UTC")
    df = pd.DataFrame([
        _event(base, "1", [{"id": "b1", "size": "10", "bid": "900100"},
                           {"id": "a1", "size": "5", "ask": "900300"}]),
        # 2 hours later: brand new ids at a totally different price level.
        _event(base + pd.Timedelta(hours=2), "1",
               [{"id": "b2", "size": "10", "bid": "100100"},
                {"id": "a2", "size": "5", "ask": "100300"}]),
    ])
    ticks = reconstruct(df, L2Options(session_gap_seconds=1800))
    assert len(ticks) == 2
    # Without the reset the stale 9001.00 bid would survive and cross the book.
    assert ticks["bid"].iloc[-1] == pytest.approx(1001.00)
    assert ticks["ask"].iloc[-1] == pytest.approx(1003.00)
    assert last_stats()["resets"] == 1


def test_crossed_books_are_never_emitted():
    base = pd.Timestamp("2026-10-03T10:00:00", tz="UTC")
    df = pd.DataFrame([
        _event(base, "1", [{"id": "b1", "size": "10", "bid": "100100"},
                           {"id": "a1", "size": "5", "ask": "100300"}]),
        # Bid jumps above the ask - a desynchronised replay.
        _event(base + pd.Timedelta(seconds=1), "1",
               [{"id": "b1", "size": "10", "bid": "100500"}]),
    ])
    ticks = reconstruct(df, L2Options(session_gap_seconds=100000))
    assert (ticks["bid"] < ticks["ask"]).all()
    assert last_stats()["crossed"] == 1


def test_depth_and_imbalance_columns_present(simple_book):
    ticks = reconstruct(simple_book, L2Options(top_levels=5))
    for col in ("book_size", "bid_depth", "ask_depth", "signed_volume"):
        assert col in ticks.columns
    assert (ticks["book_size"] > 0).all()


def test_malformed_json_is_tolerated():
    base = pd.Timestamp("2026-10-03T10:00:00", tz="UTC")
    df = pd.DataFrame([
        _event(base, "1", [{"id": "b1", "size": "10", "bid": "100100"},
                           {"id": "a1", "size": "5", "ask": "100300"}]),
        _event(base + pd.Timedelta(seconds=1), "1"),
    ])
    df.loc[1, "newQuotes"] = "{not json"
    df.loc[1, "deletedQuotes"] = None
    ticks = reconstruct(df)
    assert len(ticks) >= 1  # must not raise


def test_empty_input_returns_empty_frame():
    empty = pd.DataFrame(columns=["symbolId", "newQuotes", "deletedQuotes", "digits", "timestamp"])
    ticks = reconstruct(empty)
    assert ticks.empty
    assert "mid" in ticks.columns
