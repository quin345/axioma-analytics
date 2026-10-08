"""Background refresh of the fixed cache window.

The dashboard's window is fixed at ``CACHE_LOOKBACK_HOURS`` and served from
Redis. This module is what keeps that window *current*: every
``CACHE_REFRESH_MINUTES`` (30 by default) it slides the window - the earliest
half hour is purged and the newest half hour is read from KQL - so a page load
shows data from the last half hour without a visitor waiting on a scan.

The cycle is scheduled on the **clock**, not on the process start time: with a
30-minute interval the runs land on the wall-clock marks (:00 and :30 - 1:30,
2:00, 2:30, ...) whatever time the service was restarted, so operators can
reason about when the KQL work happens and the one-shot script
(``scripts/refresh_cache.py``, typically ``*/30`` in cron) lines up with the
in-process cycle instead of drifting against it.

The cycle lives in the app process, which is the right place for it here: the
deployment is a single uvicorn worker (`run.py`, no `--workers`), so there is
exactly one refresher and no distributed lock to coordinate. Set
``CACHE_REFRESH_MINUTES=0`` to switch it off - reads then fall back to lazy,
on-demand caching - or run ``scripts/refresh_cache.py`` from cron/systemd if the
work should not sit in the web process.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from . import service
from .config import Settings, get_settings

log = logging.getLogger(__name__)

#: Marker attribute so a double start cannot leave two cycles running.
_TASK_NAME = "axioma-cache-refresh"


def seconds_until_next_slot(interval_seconds: float, now: float | None = None) -> float:
    """Seconds from `now` to the next wall-clock multiple of the interval.

    Slots are counted from local midnight, so a 1800-second (30 minute)
    interval fires on the clock marks - 1:00, 1:30, 2:00 - rather than
    "30 minutes after whatever the process happened to start". The delay is
    always in ``(0, interval]``: landing exactly on a boundary waits out one
    full interval so the next run happens at the *next* mark instead of
    double-firing. `now` is a POSIX timestamp, injectable for tests.
    """
    interval = float(interval_seconds)
    if interval <= 0:
        return 0.0
    now = time.time() if now is None else float(now)
    local = time.localtime(now)
    midnight = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
    elapsed = now - midnight
    slot = int(elapsed / interval + 1) * interval      # the next boundary after `now`
    delay = slot - elapsed
    return delay if delay > 1e-6 else interval


async def run(interval_seconds: float,
              refresh: Callable[[], object] | None = None,
              stop: asyncio.Event | None = None) -> None:
    """Refresh once, then on each clock slot until `interval_seconds`, until stopped.

    The first cycle runs immediately: that is what warms the cache after a
    restart, and waiting a full interval would leave the first visitor paying
    for the scan. Afterwards the loop sleeps until the next wall-clock slot
    (`seconds_until_next_slot`), so cycles land on marks like 1:30 and 2:00 and
    a slow cycle does not push later ones later. Each cycle runs on a worker
    thread, so the event loop keeps answering requests while KQL is being read.
    A cycle that raises is logged and the next one still runs - a transient KQL
    or Redis failure must not end the cycle and silently freeze the window.

    `interval_seconds` may be fractional (tests drive the loop with a few
    milliseconds) and 0 runs a single cycle, which is what the one-shot script
    wants.
    """
    interval = max(0.0, float(interval_seconds))
    refresh = refresh or service.refresh_cache

    while True:
        try:
            await asyncio.to_thread(refresh)
        except asyncio.CancelledError:
            raise
        except Exception:                       # noqa: BLE001 - keep the loop alive
            log.exception("Cache refresh cycle failed")

        if interval <= 0:
            return
        delay = seconds_until_next_slot(interval)
        if stop is None:
            await asyncio.sleep(delay)
        else:
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                continue
            return


def start(settings: Settings | None = None) -> asyncio.Task | None:
    """Start the cycle on the running loop; None when it is switched off."""
    s = settings or get_settings()
    interval = s.cache_refresh_seconds
    if interval <= 0:
        log.info("Cache refresh cycle is off; the window is cached on demand.")
        return None
    log.info("Cache refresh: every %s min on the clock (:00/:30) over a %s h window; "
             "first slot in %.0f s.",
             s.cache_refresh_minutes, s.cache_lookback_hours,
             seconds_until_next_slot(interval))
    return asyncio.create_task(run(interval), name=_TASK_NAME)


async def shutdown(task: asyncio.Task | None, timeout: float = 10.0) -> None:
    """Cancel the cycle, waiting briefly for an in-flight read to finish.

    Cancelling the task cannot interrupt the worker thread mid-query, so the
    wait is bounded and the process is allowed to exit with that thread running;
    the entry it was writing is either whole or absent, never partial.
    """
    if task is None or task.done():
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
