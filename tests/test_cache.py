"""Tests for the Redis cache helpers (no Redis required).

The endpoint is Redis Enterprise, which answers the first command on a fresh
connection with a cluster redirect while it routes the client to the shard that
owns the key. redis-py only follows those redirects for `RedisCluster`, so the
cache layer does it itself - these tests pin that behaviour, the key layout the
refresh cycle relies on, and the "a cache failure is never an error" contract.
"""
from __future__ import annotations

import pandas as pd
import pytest
import redis

from app import cache
from app.config import Settings
from app.errors import DataSourceError


@pytest.fixture
def settings() -> Settings:
    return Settings(kql_host="https://kql.example.invalid", kql_database="ctrader_dom",
                    kql_table="agg_dom", redis_host="cache.example.invalid",
                    redis_port=10000, cache_key_prefix="axioma")


@pytest.fixture
def fake(monkeypatch, settings):
    """A stand-in for `client()` that records the commands it is given."""
    class Fake:
        def __init__(self, behaviour=None):
            self.calls: list[tuple] = []
            self.behaviour = behaviour or (lambda name, *a, **k: None)

        def _run(self, name, *args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self.behaviour(name, *args, **kwargs)

        def __getattr__(self, name):
            return lambda *a, **k: self._run(name, *a, **k)

    f = Fake()
    monkeypatch.setattr(cache, "client", lambda settings=None: f)
    return f


# --- Key layout ---------------------------------------------------------

def test_the_key_namespaces_the_kql_object(settings):
    assert cache.key(settings, "ticks", "41") == "axioma:ctrader_dom:agg_dom:ticks:41"


def test_the_listing_pattern_is_scoped_to_the_object(settings, fake):
    fake.behaviour = lambda name, *a, **k: [b"axioma:ctrader_dom:agg_dom:ticks:41"]

    found = cache.keys(settings, "ctrader_dom", "agg_dom", "ticks")

    assert found == ["axioma:ctrader_dom:agg_dom:ticks:41"]
    assert fake.calls[0][1] == ("axioma:ctrader_dom:agg_dom:ticks*",)


def test_a_listing_failure_is_an_empty_listing(fake):
    """The cycle then has nothing to refresh; reads still fall through to KQL."""
    def boom(name, *a, **k):
        raise redis.ConnectionError("connection reset")

    fake.behaviour = boom

    assert cache.keys(Settings(redis_host="cache.example.invalid"), "ticks") == []


# --- Cluster redirects --------------------------------------------------

def test_a_moved_redirect_is_followed_once():
    """Without this, the first command on a fresh connection is skipped."""
    attempts: list[int] = []

    def command(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise redis.ResponseError("MOVED 9608 20.70.0.151:8501")
        return "ok"

    assert cache._call(command) == "ok"
    assert len(attempts) == 2


def test_an_ask_redirect_is_followed_once():
    attempts: list[int] = []

    def command(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise redis.ResponseError("ASK 9608 20.70.0.151:8501")
        return "ok"

    assert cache._call(command) == "ok"
    assert len(attempts) == 2


def test_another_response_error_is_not_retried():
    attempts: list[int] = []

    def command(*args, **kwargs):
        attempts.append(1)
        raise redis.ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value")

    with pytest.raises(redis.ResponseError):
        cache._call(command)
    assert len(attempts) == 1


def test_delete_follows_a_redirect_instead_of_warning(fake, settings):
    attempts: list[int] = []

    def behaviour(name, *a, **k):
        attempts.append(1)
        if len(attempts) == 1:
            raise redis.ResponseError("MOVED 9608 20.70.0.151:8501")
        return 1

    fake.behaviour = behaviour

    cache.delete(settings, "axioma:ctrader_dom:agg_dom:catalogue")

    assert len(attempts) == 2


# --- Frames and documents -----------------------------------------------

def test_a_frame_round_trips_through_parquet(fake):
    frame = pd.DataFrame({"ts": pd.date_range("2026-10-08", periods=3, freq="1min", tz="UTC"),
                          "mid": [1.0, 1.1, 1.2]})
    stored: dict = {}

    def behaviour(name, *a, **k):
        if name == "set":
            stored[a[0]] = a[1]
            return True
        if name == "get":
            return stored.get(a[0])
        return None

    fake.behaviour = behaviour

    assert cache.set_frame("k", frame, 60, Settings()) is True
    back = cache.get_frame("k", Settings())

    assert list(back.columns) == ["ts", "mid"]
    assert len(back) == 3


def test_an_unreadable_frame_is_a_miss(fake):
    fake.behaviour = lambda name, *a, **k: b"not a parquet file"
    assert cache.get_frame("k", Settings()) is None


def test_a_read_failure_is_a_miss(fake):
    def boom(name, *a, **k):
        raise redis.ConnectionError("connection reset")

    fake.behaviour = boom

    assert cache.get_frame("k", Settings()) is None
    assert cache.get_json("k", Settings()) is None


def test_a_write_failure_reports_false(fake):
    def boom(name, *a, **k):
        raise redis.ConnectionError("connection reset")

    fake.behaviour = boom

    assert cache.set_json("k", {"a": 1}, 60, Settings()) is False


def test_an_unconfigured_endpoint_raises(settings, monkeypatch):
    """`client()` is the one place that refuses to guess an endpoint."""
    monkeypatch.setattr(cache, "get_settings", lambda: Settings(redis_host=""))

    with pytest.raises(DataSourceError):
        cache.client()
