"""Tests for the background refresh cycle (no data source required).

The fixed window is only as current as the cycle that re-reads it: the app
starts a task that refreshes immediately and then every
``CACHE_REFRESH_MINUTES``. These tests pin the properties that make it safe to
leave running unattended - the first cycle does not wait, a failing cycle does
not end the loop, and a cycle that is switched off starts nothing.
"""
from __future__ import annotations

import asyncio

import pytest

from app import refresh, service
from app.config import Settings


def _settings(**overrides) -> Settings:
    """A four-hour window on a 30-minute cycle, with any field overridden."""
    base = dict(kql_host="https://kql.example.invalid", cache_lookback_hours=4,
                cache_refresh_minutes=30)
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Detach the module from the real environment."""
    monkeypatch.setattr(refresh, "get_settings", _settings)
    yield


def _run(coro):
    """Drive one coroutine to completion without pytest-asyncio."""
    return asyncio.run(coro)


def test_the_first_cycle_does_not_wait():
    """A restart warms the cache instead of leaving the first visitor to pay."""
    calls: list[int] = []
    stop = asyncio.Event()

    def refresh_once():
        calls.append(1)
        stop.set()

    _run(refresh.run(1800, refresh_once, stop=stop))

    assert len(calls) == 1


def test_the_cycle_repeats_until_stopped():
    """The interval is what bounds the staleness of the window."""
    calls: list[int] = []
    stop = asyncio.Event()

    def refresh_once():
        calls.append(1)
        if len(calls) == 3:
            stop.set()

    _run(refresh.run(0.05, refresh_once, stop=stop))

    assert len(calls) == 3


def test_a_failing_cycle_is_logged_and_the_next_one_still_runs(caplog):
    """A transient KQL or Redis failure must not freeze the window."""
    calls: list[int] = []
    stop = asyncio.Event()

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("KQL query failed: Forbidden (403-Forbidden)")
        stop.set()

    _run(refresh.run(0.05, flaky, stop=stop))

    assert len(calls) == 2          # the loop survived the first failure
    assert "Cache refresh cycle failed" in caplog.text
    assert "403-Forbidden" in caplog.text


def test_an_interval_of_zero_runs_one_cycle():
    """Used by the one-shot script, which has no loop to keep alive."""
    calls: list[int] = []
    _run(refresh.run(0, lambda: calls.append(1)))
    assert len(calls) == 1


def test_the_default_refresh_callable_is_the_service_cycle(monkeypatch):
    seen: list[int] = []
    monkeypatch.setattr(service, "refresh_cache", lambda: seen.append(1))

    _run(refresh.run(0, None))

    assert seen == [1]


def test_start_schedules_nothing_when_the_cycle_is_off():
    async def main():
        task = refresh.start(_settings(cache_refresh_minutes=0))
        await refresh.shutdown(task)
        return task

    assert _run(main()) is None


def test_start_honours_the_configured_interval(monkeypatch):
    started: list[int] = []

    async def fake_run(interval, refresh_fn=None, stop=None):
        started.append(interval)

    monkeypatch.setattr(refresh, "run", fake_run)

    async def main():
        task = refresh.start(_settings(cache_refresh_minutes=5))
        await task
        return task

    assert _run(main()) is not None
    assert started == [300]


def test_shutdown_tolerates_a_task_that_is_already_done():
    async def main():
        async def quick():
            return "done"

        task = asyncio.create_task(quick())
        await task
        await refresh.shutdown(task)          # no raise, no wait
        await refresh.shutdown(None)
        return True

    assert _run(main()) is True


def test_shutdown_cancels_a_running_cycle():
    async def main():
        task = asyncio.create_task(refresh.run(1800, lambda: None))
        await asyncio.sleep(0.01)             # let the first cycle pass
        await refresh.shutdown(task)
        return task

    assert _run(main()).cancelled()
