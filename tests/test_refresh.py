"""Tests for the background refresh cycle (no data source required).

The fixed window is only as current as the cycle that re-reads it: the app
starts a task that refreshes immediately and then every
``CACHE_REFRESH_MINUTES``. These tests pin the properties that make it safe to
leave running unattended - the first cycle does not wait, a failing cycle does
not end the loop, and a cycle that is switched off starts nothing.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app import refresh, service
from app.config import Settings


def _settings(**overrides) -> Settings:
    """A four-hour window on a 5-minute cycle, with any field overridden."""
    base = dict(kql_host="https://kql.example.invalid", cache_lookback_hours=4,
                cache_refresh_minutes=5, cache_refresh_offset_seconds=15)
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


def test_start_honours_the_configured_interval_and_offset(monkeypatch):
    started: list[tuple] = []

    async def fake_run(interval, refresh_fn=None, stop=None, offset_seconds=0.0):
        started.append((interval, offset_seconds))

    monkeypatch.setattr(refresh, "run", fake_run)

    async def main():
        task = refresh.start(_settings(cache_refresh_minutes=5))
        await task
        return task

    assert _run(main()) is not None
    assert started == [(300, 15)]


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


# ----------------------------------------------------------------------
# Scheduling: cycles land on the clock marks, not on the start time
# ----------------------------------------------------------------------

def _at(hour: int, minute: int, second: int = 0) -> float:
    """A POSIX timestamp for that local wall-clock time today."""
    today = time.localtime()
    return time.mktime((today.tm_year, today.tm_mon, today.tm_mday,
                        hour, minute, second, 0, 0, -1))


def _on_a_slot(now: float, delay: float, hour: int, minute: int, second: int = 15) -> bool:
    """Does `now + delay` land on that local wall-clock time?"""
    target = time.localtime(now + delay)
    return (target.tm_hour, target.tm_min, target.tm_sec) == (hour, minute, second)


def test_a_five_minute_cycle_waits_just_past_the_next_mark():
    """1:29:00 waits 75 s for 1:30:15 - runs sit just past the 5-min marks."""
    now = _at(1, 29, 0)
    delay = refresh.seconds_until_next_slot(300, now, offset_seconds=15)

    assert delay == 75.0
    assert _on_a_slot(now, delay, 1, 30)


def test_a_cycle_past_the_slot_waits_for_the_next_one():
    """1:30:30 waits until 1:35:15 - it does not re-fire inside the slot."""
    now = _at(1, 30, 30)
    delay = refresh.seconds_until_next_slot(300, now, offset_seconds=15)

    assert delay == 285.0
    assert _on_a_slot(now, delay, 1, 35)


def test_the_example_mark_lands_fifteen_seconds_past():
    """4:45:00 waits 15 s for 4:45:15 - the settle delay past the mark."""
    now = _at(4, 45, 0)
    delay = refresh.seconds_until_next_slot(300, now, offset_seconds=15)

    assert delay == 15.0
    assert _on_a_slot(now, delay, 4, 45)


def test_landing_exactly_on_a_slot_waits_a_full_interval():
    """On the slot already, the next run is the *next* slot, not a double-fire."""
    assert refresh.seconds_until_next_slot(300, _at(2, 0, 15), offset_seconds=15) == 300.0
    # Without an offset the old behaviour holds: on the mark waits a full interval.
    assert refresh.seconds_until_next_slot(1800, _at(2, 0, 0)) == 1800.0


def test_the_delay_always_falls_within_one_interval():
    for minute in (0, 7, 15, 29, 30, 45, 59):
        delay = refresh.seconds_until_next_slot(300, _at(3, minute, 15), offset_seconds=15)
        assert 0 < delay <= 300


def test_an_interval_of_zero_never_schedules_a_slot():
    assert refresh.seconds_until_next_slot(0) == 0.0


def test_the_loop_waits_for_the_clock_not_the_interval(monkeypatch):
    """`run` asks for the next slot after each cycle, not a fixed sleep."""
    seen: list[tuple] = []
    stop = asyncio.Event()

    def fake_slot(interval: float, now: float | None = None, offset_seconds: float = 0.0) -> float:
        seen.append((interval, offset_seconds))
        stop.set()
        return 42.0

    monkeypatch.setattr(refresh, "seconds_until_next_slot", fake_slot)

    _run(refresh.run(300, lambda: None, stop=stop, offset_seconds=15))

    assert seen == [(300, 15)]              # the wait came from the scheduler
