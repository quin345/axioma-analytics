"""Risk manager and session calendar (DESIGN.md section 5). Pure: time comes from the context.

Session schedule semantics (cTrader Open API, ProtoOAInterval / ProtoOAHoliday):
- `schedule` intervals are OPEN trading intervals, [start, end) in seconds from Sunday 00:00
  wall-clock in `schedule_tz`. A gold-like daily break is simply a gap between two intervals.
  An interval with end < start (or end > 604800) wraps over the week end. Empty schedule =
  always open (24/7).
- Offsets are wall-clock, so DST shifts the UTC instant automatically.
- Holidays close the market. `holidayDate` is days since 1970-01-01 (values > 1e11 are read as
  ms, > 1e7 as seconds). `isRecurring` repeats month/day every year. `startSecond`/`endSecond`
  are seconds of the day (holiday's own tz, else schedule_tz); both absent/zero = whole day;
  end <= start = until end of day. Entries may be dicts or objects with these attribute names.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from .resetday import day_label, day_start_ms, next_reset_ms
from .types import Intent, IntentKind, PositionView, RiskContext, SymbolSpec

WEEK_S = 7 * 86400
MIN_BARS_SINCE_OPEN = 12  # rolling_z min_periods
SCAN_DAYS = 8
UTC = timezone.utc


def _utc_date(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, UTC).date()


def _get(h, name, default=None):
    return h.get(name, default) if isinstance(h, dict) else getattr(h, name, default)


def _holiday_date(v) -> date:
    v = int(v)
    if v > 100_000_000_000:
        return datetime.fromtimestamp(v / 1000, UTC).date()
    if v > 10_000_000:
        return datetime.fromtimestamp(v, UTC).date()
    return date(1970, 1, 1) + timedelta(days=v)


def _local_to_ms(tz: ZoneInfo, naive: datetime) -> int:
    return int(naive.replace(tzinfo=tz).timestamp() * 1000)


class SessionCalendar:
    """Open/closed state, time to next close and bars since open for one symbol."""

    def __init__(self, spec: SymbolSpec):
        self.tz = ZoneInfo(spec.schedule_tz or "UTC")
        ivs: list[tuple[int, int]] = []
        for s, e in spec.schedule or []:
            if e == s:
                continue
            if e < s or e > WEEK_S:
                ivs.append((s, WEEK_S))
                ivs.append((0, e % WEEK_S))
            else:
                ivs.append((s, e))
        self.intervals = sorted(ivs)
        self.always_open = not self.intervals
        self.holidays = list(spec.holidays or [])

    # -- internals ---------------------------------------------------------------------
    def _week_seconds(self, ms: int) -> int:
        d = datetime.fromtimestamp(ms / 1000, self.tz)
        return ((d.weekday() + 1) % 7) * 86400 + d.hour * 3600 + d.minute * 60 + d.second

    def _holiday_windows(self, ms: int) -> list[tuple[int, int]]:
        """Closed windows (utc ms) for dates within SCAN_DAYS+2 of ms."""
        out = []
        center = _utc_date(ms)
        lo, hi = center - timedelta(days=SCAN_DAYS + 2), center + timedelta(days=SCAN_DAYS + 2)
        for h in self.holidays:
            tzname = _get(h, "scheduleTimeZone")
            tz = ZoneInfo(tzname) if tzname else self.tz
            base = _holiday_date(_get(h, "holidayDate", 0))
            if _get(h, "isRecurring", False):
                days = []
                for y in range(lo.year, hi.year + 1):
                    try:
                        days.append(date(y, base.month, base.day))
                    except ValueError:  # Feb 29 in a non-leap year
                        pass
            else:
                days = [base]
            ss, es = _get(h, "startSecond"), _get(h, "endSecond")
            for d in days:
                if not lo <= d <= hi:
                    continue
                mid = datetime(d.year, d.month, d.day)
                day_end = mid + timedelta(days=1)
                if not ss and not es:
                    s, e = mid, day_end
                else:
                    s = mid + timedelta(seconds=ss or 0)
                    e = mid + timedelta(seconds=es or 0)
                    if e <= s:
                        e = day_end
                out.append((_local_to_ms(tz, s), _local_to_ms(tz, e)))
        return out

    def _in_holiday(self, ms: int, windows=None) -> bool:
        if not self.holidays:
            return False
        return any(s <= ms < e for s, e in (windows or self._holiday_windows(ms)))

    def _boundaries(self, ms: int, days_back: int, days_fwd: int):
        """(closes, opens) candidate instants (utc ms) in [ms-days_back, ms+days_fwd] days."""
        lo, hi = ms - days_back * 86_400_000, ms + days_fwd * 86_400_000
        closes: set[int] = set()
        opens: set[int] = set()
        d = datetime.fromtimestamp(ms / 1000, self.tz)
        sunday = (d - timedelta(days=(d.weekday() + 1) % 7)).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        for w in range(-2, 3):
            ws = sunday + timedelta(weeks=w)
            for s, e in self.intervals:
                opens.add(_local_to_ms(self.tz, ws + timedelta(seconds=s)))
                closes.add(_local_to_ms(self.tz, ws + timedelta(seconds=e)))
        for s, e in self._holiday_windows(ms):
            closes.add(s)
            opens.add(e)
        keep = lambda xs: sorted(x for x in xs if lo <= x <= hi)
        return keep(closes), keep(opens)

    # -- public ------------------------------------------------------------------------
    def is_open(self, now_ms: int) -> bool:
        if not self.always_open:
            t = self._week_seconds(now_ms)
            if not any(s <= t < e for s, e in self.intervals):
                return False
        return not self._in_holiday(now_ms)

    def open_predicate(self, around_ms: int):
        """is_open for instants within a few days of around_ms, holiday windows computed once (the KQL
        gap check samples hundreds of instants per slot)."""
        windows = self._holiday_windows(around_ms) if self.holidays else []

        def is_open(ms: int) -> bool:
            if not self.always_open:
                t = self._week_seconds(ms)
                if not any(s <= t < e for s, e in self.intervals):
                    return False
            return not any(s <= ms < e for s, e in windows)
        return is_open

    def minutes_to_close(self, now_ms: int) -> Optional[float]:
        """Minutes until the next close; 0.0 if already closed; None if none within 8 days."""
        if not self.is_open(now_ms):
            return 0.0
        if self.always_open and not self.holidays:
            return None
        closes, _ = self._boundaries(now_ms, 0, SCAN_DAYS)
        for c in closes:
            if c > now_ms and not self.is_open(c):
                return (c - now_ms) / 60_000.0
        return None

    def last_open_ms(self, now_ms: int) -> Optional[int]:
        """Start of the current open stretch; None if closed, always open or not found."""
        if not self.is_open(now_ms) or (self.always_open and not self.holidays):
            return None
        _, opens = self._boundaries(now_ms, SCAN_DAYS, 0)
        for o in reversed(opens):
            if o <= now_ms and self.is_open(o) and not self.is_open(o - 1000):
                return o
        return None

    def bars_since_open(self, now_ms: int, bar_ms: int = 300_000) -> int:
        """Completed 5 min bars since the session opened (huge when open for 8+ days/24-7)."""
        if not self.is_open(now_ms):
            return 0
        o = self.last_open_ms(now_ms)
        if o is None:
            return 10 ** 9
        return int((now_ms - o) // bar_ms)


class RiskManager:
    """Entry gate and flatten decisions. Holds breaker latches; ctx carries all measurements."""

    def __init__(self, settings):
        self.s = settings
        self.force_flatten = False
        self.force_reason = ""
        self._day: Optional[int] = None   # start (utc ms) of the current trading day, see resetday
        self._loss_pause_until = 0
        self._loss_handled = 0       # consec_losses value already paused for
        self._reject_pause_until = 0
        self._daily_latched_until = 0
        self._daily_reason = ""

    # -- helpers -----------------------------------------------------------------------
    def _roll_day(self, now_ms: int) -> None:
        d = day_start_ms(now_ms, self.s)
        if self._day is not None and d != self._day:
            self._daily_latched_until = 0
            self._daily_reason = ""
            self.force_flatten = False
            self.force_reason = ""
        self._day = d

    def latch_daily(self, reason: str, now_ms: int) -> None:
        """Block entries until the next daily reset (the kill switch's daily_max_loss)."""
        self._roll_day(now_ms)
        self._daily_reason = reason
        self._daily_latched_until = next_reset_ms(now_ms, self.s)

    def daily_latched(self, now_ms: int) -> bool:
        self._roll_day(now_ms)
        return now_ms < self._daily_latched_until

    def _daily_breach(self, ctx: RiskContext) -> str:
        s = self.s
        if ctx.pnl_today_pct <= -s.daily_loss_pct:
            return "daily_loss_pct"
        if ctx.pnl_today_bps <= -s.daily_loss_bps:
            return "daily_loss_bps"
        return ""

    def _disconnect_long(self, ctx: RiskContext) -> bool:
        """Open position and no data for more than flatten_after_disconnect_s.

        Uses ctx.disconnect_s (measured by the runner) when given; otherwise falls back to the
        last_data_ms proxy, which also covers the window right after a reconnect before data flows.
        """
        if ctx.open_positions <= 0:
            return False
        if ctx.disconnect_s is not None:
            return ctx.disconnect_s > self.s.flatten_after_disconnect_s
        if ctx.last_data_ms is None:
            return False
        return ctx.now_ms - ctx.last_data_ms > self.s.flatten_after_disconnect_s * 1000

    def _flag(self, reason: str) -> None:
        self.force_flatten = True
        self.force_reason = reason

    # -- public ------------------------------------------------------------------------
    def check(self, intent: Intent, ctx: RiskContext) -> tuple[bool, str, float]:
        """(ok, reason, size_mult). Only ENTER and FLIP entry legs are gated."""
        s = self.s
        self._roll_day(ctx.now_ms)
        # latches update on every call, whatever the intent
        if ctx.kill_file:
            self._flag("kill")
        if self._disconnect_long(ctx):
            self._flag("disconnect")
        elif self.force_reason == "disconnect":  # kill and daily flags are sticky; this is not
            self.force_flatten, self.force_reason = False, ""
        if self._daily_latched_until <= ctx.now_ms and self._daily_breach(ctx):
            self._daily_reason = self._daily_breach(ctx)
            self._daily_latched_until = next_reset_ms(ctx.now_ms, s)
        if ctx.consec_losses < s.max_consec_losses:
            self._loss_handled = 0
        elif ctx.consec_losses > self._loss_handled:
            self._loss_handled = ctx.consec_losses
            self._loss_pause_until = ctx.now_ms + s.pause_after_losses_min * 60_000
        if (ctx.rejects_window >= s.reject_breaker_count
                and ctx.now_ms >= self._reject_pause_until):
            self._reject_pause_until = ctx.now_ms + s.reject_breaker_window_min * 60_000

        if intent.kind not in (IntentKind.ENTER, IntentKind.FLIP):
            return True, "ok", intent.size_mult

        def block(reason: str) -> tuple[bool, str, float]:
            return False, reason, 0.0

        if ctx.kill_file:
            return block("kill")
        if ctx.pause_file:
            return block("pause_file")
        if ctx.now_ms < self._daily_latched_until:
            return block(self._daily_reason)
        if ctx.now_ms < self._loss_pause_until:
            return block("consec_losses_pause")
        if ctx.now_ms < self._reject_pause_until:
            return block("reject_breaker")
        if self.force_flatten:
            return block(self.force_reason or "force_flatten")
        if not ctx.session_open:
            return block("session_closed")
        if ctx.minutes_to_close is not None and ctx.minutes_to_close < s.no_entry_before_close_min:
            return block("near_close")
        if ctx.bars_since_open < MIN_BARS_SINCE_OPEN:
            return block("warmup_after_open")
        if ctx.last_data_ms is None or ctx.now_ms - ctx.last_data_ms > s.stale_s * 1000:
            return block("stale_data")
        if ctx.quote is None:
            return block("no_quote")
        med = ctx.spread_median_48
        if med is not None and not math.isnan(med) and med > 0:
            if (ctx.quote.ask - ctx.quote.bid) > s.spread_guard_x_median * med:
                return block("spread_guard")
        held = ctx.open_positions - (1 if intent.kind is IntentKind.FLIP else 0)
        if held >= s.max_positions:
            return block("max_positions")
        if ctx.trades_today >= s.max_trades_day:
            return block("max_trades_day")
        if ctx.orders_last_min >= s.max_orders_min:
            return block("max_orders_min")
        return True, "ok", intent.size_mult

    def must_flatten(self, ctx: RiskContext,
                     position: Optional[PositionView]) -> tuple[bool, str]:
        """Forced exit reasons. KILL is reported even without a position (runner must exit)."""
        s = self.s
        self._roll_day(ctx.now_ms)
        if ctx.kill_file:
            self._flag("kill")
            return True, "kill"
        if position is None:
            return False, ""
        if self._daily_breach(ctx):
            reason = self._daily_breach(ctx)
            if self._daily_latched_until <= ctx.now_ms:
                self._daily_reason = reason
                self._daily_latched_until = next_reset_ms(ctx.now_ms, s)
            return True, reason
        if not ctx.session_open:
            return True, "session_closed"
        if ctx.minutes_to_close is not None and ctx.minutes_to_close <= s.flat_before_close_min:
            return True, "session_close"
        if self._disconnect_long(ctx):
            self._flag("disconnect")
            return True, "disconnect"
        return False, ""

    def state(self) -> dict:
        """Snapshot for the journal."""
        return {
            "day": day_label(self._day, self.s) if self._day is not None else None,
            "force_flatten": self.force_flatten,
            "force_reason": self.force_reason,
            "loss_pause_until_ms": self._loss_pause_until,
            "reject_pause_until_ms": self._reject_pause_until,
            "daily_latched_until_ms": self._daily_latched_until,
            "daily_reason": self._daily_reason,
        }
