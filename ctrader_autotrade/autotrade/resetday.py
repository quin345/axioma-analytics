"""The trading day: every "daily" quantity (loss latch, trades_today, pnl_today, kill-switch daily
loss) runs from one reset instant to the next, configured by `daily_reset` (HH:MM) in
`daily_reset_tz` (an IANA zone). The default, 00:00 UTC, is the plain UTC day.

The reset is wall-clock time in the zone, so a DST change moves it in UTC but never skips or
repeats it: the "day" that contains a changeover is 23 or 25 hours long. A reset time that does not
exist on a changeover date (a spring-forward gap) is shifted forward by the gap (zoneinfo fold=0);
an ambiguous one (fall back) happens at its first occurrence. Pure: depends only on the two settings.
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_reset(hhmm: str, tz_name: str) -> tuple[time, ZoneInfo]:
    """(time of day, zone) for a daily_reset / daily_reset_tz pair; ValueError if either is bad."""
    m = _HHMM.match(hhmm) if isinstance(hhmm, str) else None
    if m is None:
        raise ValueError(f"daily_reset must be HH:MM (00:00-23:59), got {hhmm!r}")
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError, TypeError, OSError):
        raise ValueError(f"daily_reset_tz is not an IANA time zone name: {tz_name!r}") from None
    return time(int(m.group(1)), int(m.group(2))), tz


@lru_cache(maxsize=32)
def _parsed(hhmm: str, tz_name: str) -> tuple[time, ZoneInfo]:
    return parse_reset(hhmm, tz_name)


def _reset_on(day, t: time, tz: ZoneInfo) -> int:
    """UTC ms of the reset on a local calendar date."""
    return int(datetime.combine(day, t).replace(tzinfo=tz).timestamp() * 1000)


def _bounds(now_ms: int, settings) -> tuple[int, int]:
    t, tz = _parsed(settings.daily_reset, settings.daily_reset_tz)
    local_day = datetime.fromtimestamp(now_ms / 1000, tz).date()
    d = local_day - timedelta(days=1)
    start = _reset_on(d, t, tz)
    for _ in range(4):                       # walks forward over the (at most 3) candidate dates
        nxt = _reset_on(d + timedelta(days=1), t, tz)
        if nxt > now_ms:
            return start, nxt
        start, d = nxt, d + timedelta(days=1)
    return start, start + 86_400_000         # unreachable for real zones


def day_start_ms(now_ms: int, settings) -> int:
    """UTC ms of the most recent daily reset at or before now_ms."""
    return _bounds(now_ms, settings)[0]


def next_reset_ms(now_ms: int, settings) -> int:
    """UTC ms of the first daily reset strictly after now_ms."""
    return _bounds(now_ms, settings)[1]


def day_label(now_ms: int, settings) -> str:
    """ISO local date (in daily_reset_tz) on which the current trading day started."""
    _, tz = _parsed(settings.daily_reset, settings.daily_reset_tz)
    return datetime.fromtimestamp(day_start_ms(now_ms, settings) / 1000, tz).date().isoformat()
