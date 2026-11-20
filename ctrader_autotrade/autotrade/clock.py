"""Injectable clock. Production wraps the Twisted reactor; tests use twisted.internet.task.Clock."""
from __future__ import annotations

import time
from typing import Any, Callable, Optional


class Clock:
    """now_ms() wall time plus call_later scheduling on a Twisted IReactorTime."""

    def __init__(self, reactor: Optional[Any] = None, time_fn: Callable[[], float] = time.time):
        self._reactor = reactor
        self._time_fn = time_fn

    @property
    def reactor(self) -> Any:
        if self._reactor is None:
            from twisted.internet import reactor
            self._reactor = reactor
        return self._reactor

    def now_ms(self) -> int:
        return int(self._time_fn() * 1000)

    def call_later(self, delay_s: float, fn: Callable, *args, **kwargs):
        """Schedule fn after delay_s seconds; returns the IDelayedCall."""
        return self.reactor.callLater(delay_s, fn, *args, **kwargs)


class TestClock(Clock):
    """Deterministic clock over twisted.internet.task.Clock; advance() moves time and fires calls."""
    __test__ = False

    def __init__(self, start_ms: int = 0):
        from twisted.internet.task import Clock as TwistedClock
        self._tw = TwistedClock()
        self._start_ms = start_ms
        super().__init__(self._tw, lambda: self._start_ms / 1000 + self._tw.seconds())

    def advance(self, seconds: float) -> None:
        self._tw.advance(seconds)
