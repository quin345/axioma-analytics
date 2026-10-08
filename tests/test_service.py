"""Tests for the cache-backed tick path (no data source required).

The window is fixed: one KQL read covers the whole ``CACHE_LOOKBACK_HOURS`` per
symbol and every narrower duration is served from that copy. These tests pin
the two properties the UI depends on - a duration change costs no query, and a
cache outage degrades to KQL rather than to an error.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app import service
from app.config import Settings
from app.errors import DataSourceError


@pytest.fixture
def settings() -> Settings:
    """A four-hour window, so the clamps and the anchoring are unambiguous."""
    return Settings(kql_host="https://kql.example.invalid", kql_database="ctrader_dom",
                    kql_table="agg_dom", redis_host="cache.example.invalid",
                    cache_lookback_hours=4, max_ticks=10000)


@pytest.fixture(autouse=True)
def _offline(monkeypatch, settings):
    """Everything but `get_settings` is faked, so nothing touches the network."""
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    yield


def _window(periods: int = 240, freq: str = "1min", symbol: str = "1") -> pd.DataFrame:
    """Raw aggregate rows over one symbol, oldest first.

    `periods` rows spaced by `freq`: the default is one row per minute, which
    makes a "60 minutes" cut land on an exact row boundary.
    """
    ts = pd.date_range("2026-10-08T00:00:00", periods=periods, freq=freq, tz="UTC")
    n = len(ts)
    mid = 1.0850 + np.linspace(0, 1e-3, n)
    return pd.DataFrame({
        "timestamp": ts,
        "symbolId": symbol,
        "total_bid": np.full(n, 100.0),
        "total_ask": np.full(n, 120.0),
        "best_bid": mid - 1e-5,
        "best_ask": mid + 1e-5,
        "imbalance": np.zeros(n),
        "imbalance_ratio": np.zeros(n),
        "vwap_bid": mid - 1e-5,
        "vwap_ask": mid + 1e-5,
        "vwap_spread": np.full(n, 2e-5),
        "rel_spread": np.full(n, 1e-5),
        "rel_vwap_spread": np.full(n, 1e-5),
    })


# ----------------------------------------------------------------------
# Clamping: the UI may narrow the window, never widen it
# ----------------------------------------------------------------------

def test_no_duration_means_the_whole_window():
    assert service.clamp_lookback(None) == 240


def test_a_narrower_duration_is_kept():
    assert service.clamp_lookback(30) == 30


def test_a_wider_duration_is_clamped_to_the_window():
    """More history than the cache holds is served with what there is."""
    assert service.clamp_lookback(1440) == 240


def test_a_duration_of_zero_becomes_one_minute():
    assert service.clamp_lookback(0) == 1


def test_a_nonsense_duration_falls_back_to_the_window():
    assert service.clamp_lookback("banana") == 240


def test_the_clamp_honours_a_configured_window():
    assert service.clamp_lookback(600, Settings(cache_lookback_hours=1)) == 60


# ----------------------------------------------------------------------
# Narrowing: anchored on the newest row, then capped
# ----------------------------------------------------------------------

def test_narrowing_keeps_the_tail_not_the_head():
    raw = _window(240)
    out = service._narrow(raw, 60, 100000)

    assert out["timestamp"].min() == raw["timestamp"].max() - pd.Timedelta(minutes=60)
    assert out["timestamp"].max() == raw["timestamp"].max()
    assert len(out) == 61          # the boundary minute is included


def test_narrowing_of_the_full_window_changes_nothing():
    raw = _window(240)
    assert len(service._narrow(raw, 240, 100000)) == len(raw)


def test_the_row_cap_is_applied_to_the_narrowed_tail():
    raw = _window(3600, freq="1s")
    out = service._narrow(raw, 60, 10)

    assert len(out) == 10
    assert out["timestamp"].max() == raw["timestamp"].max()


def test_an_empty_window_narrows_to_an_empty_frame():
    assert service._narrow(_window(0), 60, 10).empty


# ----------------------------------------------------------------------
# The Redis-backed tick path
# ----------------------------------------------------------------------

@pytest.fixture
def redis(monkeypatch) -> dict:
    """A dict-backed stand-in for `app.cache`, recording what the service did."""
    store: dict = {"frames": {}, "written": [], "read": [], "deleted": []}

    def get_frame(k, settings=None):
        store["read"].append(k)
        return store["frames"].get(k)

    def set_frame(k, frame, ttl, settings=None):
        store["written"].append((k, ttl))
        store["frames"][k] = frame
        return True

    monkeypatch.setattr(service.cache, "get_frame", get_frame)
    monkeypatch.setattr(service.cache, "set_frame", set_frame)
    monkeypatch.setattr(service.cache, "delete",
                        lambda settings=None, *keys: store["deleted"].extend(keys))
    return store


@pytest.fixture
def kql_rows(monkeypatch) -> dict:
    """A stand-in for the one KQL read, counting how often it is made."""
    calls: dict = {"load_raw": 0}

    def load_raw(symbol=None, *, limit=None, lookback_minutes=None):
        calls["load_raw"] += 1
        calls["symbol"] = symbol
        calls["lookback_minutes"] = lookback_minutes
        return calls["frame"]

    calls["frame"] = _window(240)
    monkeypatch.setattr(service, "load_raw", load_raw)
    return calls


def test_a_cold_symbol_reads_kql_once_and_caches_the_full_window(redis, kql_rows):
    frame = service.load_ticks_cached("1", limit=100000, lookback_minutes=60)

    assert len(frame) == 61
    assert kql_rows["load_raw"] == 1
    # The cached copy is the whole fixed window, whatever duration was asked for.
    key, ttl = redis["written"][0]
    assert key == "axioma:ctrader_dom:agg_dom:ticks:1"
    assert ttl == service.get_settings().cache_ttl_seconds
    assert len(redis["frames"][key]) == 240
    assert kql_rows["lookback_minutes"] == 240


def test_a_warm_symbol_is_served_from_redis(redis, kql_rows):
    service.load_ticks_cached("1", limit=100000, lookback_minutes=240)
    before = kql_rows["load_raw"]

    frame = service.load_ticks_cached("1", limit=100000, lookback_minutes=15)

    assert kql_rows["load_raw"] == before      # no second query
    assert len(frame) == 16


def test_the_same_window_serves_any_duration(redis, kql_rows):
    """The point of the fixed window: switching duration is free."""
    for minutes in (5, 30, 120, 240):
        service.load_ticks_cached("1", limit=100000, lookback_minutes=minutes)

    assert kql_rows["load_raw"] == 1


def test_each_symbol_gets_its_own_cache_entry(redis, kql_rows):
    service.load_ticks_cached("1", limit=100000, lookback_minutes=60)
    kql_rows["frame"] = _window(240, symbol="2")
    service.load_ticks_cached("2", limit=100000, lookback_minutes=60)

    assert [k for k, _ in redis["written"]] == [
        "axioma:ctrader_dom:agg_dom:ticks:1",
        "axioma:ctrader_dom:agg_dom:ticks:2",
    ]
    assert kql_rows["load_raw"] == 2


def test_a_symbol_less_read_is_cached_under_all(redis, kql_rows):
    service.load_ticks_cached(None, limit=100000, lookback_minutes=60)
    assert redis["written"][0][0] == "axioma:ctrader_dom:agg_dom:ticks:all"


def test_a_cache_outage_still_returns_data(monkeypatch, kql_rows):
    """Every cache call fails; the read falls through to KQL rather than raising."""
    monkeypatch.setattr(service.cache, "get_frame", lambda k, settings=None: None)
    monkeypatch.setattr(service.cache, "set_frame", lambda k, f, ttl, settings=None: False)

    frame = service.load_ticks_cached("1", limit=100000, lookback_minutes=60)

    assert len(frame) == 61
    assert kql_rows["load_raw"] == 1


def test_an_empty_window_is_reported_as_a_data_error(redis, kql_rows):
    redis["frames"]["axioma:ctrader_dom:agg_dom:ticks:1"] = _window(0)

    with pytest.raises(DataSourceError) as exc:
        service.load_ticks_cached("1", limit=100000, lookback_minutes=60)

    assert "60 minute" in str(exc.value)


def test_reset_drops_the_catalogue_and_every_symbol_key(monkeypatch, redis):
    monkeypatch.setattr(service, "_instruments",
                        {"1": service.Instrument("1", "EURUSD", "", "fx_major", 5)})
    monkeypatch.setattr(service, "status", lambda **_: service.Status(connected=True))

    service.reset()

    assert sorted(redis["deleted"]) == [
        "axioma:ctrader_dom:agg_dom:catalogue",
        "axioma:ctrader_dom:agg_dom:ticks:1",
        "axioma:ctrader_dom:agg_dom:ticks:all",
    ]
    assert service._instruments is None

