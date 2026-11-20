from datetime import datetime, timezone

import pytest

from autotrade.config import Settings
from autotrade.risk import RiskManager, SessionCalendar
from autotrade.types import (Intent, IntentKind, PositionView, Quote, RiskContext, Side,
                             SymbolSpec)


def ms(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


NOW = ms(2026, 11, 18, 12, 0, 0)  # Wednesday


def intent(kind=IntentKind.ENTER):
    return Intent(kind, 41, Side.BUY, 1.0, None, None, 1000, "enter", "41-1")


def ctx(**kw):
    d = dict(now_ms=NOW, quote=Quote(41, 1000, 1010, NOW), spread_median_48=10.0,
             last_data_ms=NOW - 1000, session_open=True, minutes_to_close=None,
             bars_since_open=100, open_positions=0, trades_today=0, orders_last_min=0,
             pnl_today_bps=0.0, pnl_today_pct=0.0, consec_losses=0, rejects_window=0,
             kill_file=False, pause_file=False, seconds_since_reconnect=None)
    d.update(kw)
    return RiskContext(**d)


def pos():
    return PositionView(41, Side.BUY, 2, 2000.0, NOW - 300_000, NOW, None)


def chk(c, kind=IntentKind.ENTER, rm=None, **skw):
    rm = rm or RiskManager(Settings(**skw))
    return rm.check(intent(kind), c)


def test_ok_passthrough():
    ok, reason, mult = chk(ctx())
    assert (ok, reason, mult) == (True, "ok", 1.0)


@pytest.mark.parametrize("kw,reason", [
    (dict(kill_file=True), "kill"),
    (dict(pause_file=True), "pause_file"),
    (dict(session_open=False), "session_closed"),
    (dict(minutes_to_close=9.9), "near_close"),
    (dict(bars_since_open=11), "warmup_after_open"),
    (dict(quote=Quote(41, 1000, 1021, NOW)), "spread_guard"),
    (dict(last_data_ms=NOW - 30_001), "stale_data"),
    (dict(last_data_ms=None), "stale_data"),
    (dict(quote=None), "no_quote"),
    (dict(open_positions=2), "max_positions"),
    (dict(trades_today=200), "max_trades_day"),
    (dict(orders_last_min=10), "max_orders_min"),
    (dict(pnl_today_pct=-1.0), "daily_loss_pct"),
    (dict(pnl_today_bps=-150.0), "daily_loss_bps"),
    (dict(consec_losses=15), "consec_losses_pause"),
    (dict(rejects_window=3), "reject_breaker"),
])
def test_blocks(kw, reason):
    ok, r, mult = chk(ctx(**kw))
    assert (ok, r, mult) == (False, reason, 0.0)


@pytest.mark.parametrize("kw", [
    dict(minutes_to_close=10.0), dict(bars_since_open=12), dict(last_data_ms=NOW - 30_000),
    dict(quote=Quote(41, 1000, 1020, NOW)), dict(open_positions=1), dict(trades_today=199),
    dict(orders_last_min=9), dict(pnl_today_pct=-0.99, pnl_today_bps=-149), dict(consec_losses=14),
    dict(rejects_window=2), dict(spread_median_48=None), dict(spread_median_48=float("nan")),
])
def test_edges_pass(kw):
    assert chk(ctx(**kw))[0] is True


@pytest.mark.parametrize("kind", [IntentKind.NONE, IntentKind.EXIT, IntentKind.HOLD])
def test_non_entries_never_blocked(kind):
    ok, r, _ = chk(ctx(kill_file=True, pause_file=True, session_open=False), kind)
    assert ok and r == "ok"


def test_flip_position_arithmetic():
    assert chk(ctx(open_positions=3), IntentKind.FLIP)[1] == "max_positions"
    assert chk(ctx(open_positions=2), IntentKind.FLIP)[0] is True
    assert chk(ctx(open_positions=2), IntentKind.ENTER)[1] == "max_positions"


def test_kill_flags_force_flatten_even_for_exit():
    rm = RiskManager(Settings())
    rm.check(intent(IntentKind.EXIT), ctx(kill_file=True))
    assert rm.force_flatten and rm.force_reason == "kill"
    # sticky even after the file disappears (same UTC day)
    assert rm.check(intent(), ctx())[1] == "kill"


def test_loss_pause_expires_and_retriggers():
    rm = RiskManager(Settings())
    assert chk(ctx(consec_losses=15), rm=rm)[1] == "consec_losses_pause"
    later = NOW + 59 * 60_000
    assert chk(ctx(consec_losses=15, now_ms=later, last_data_ms=later - 1), rm=rm)[1] \
        == "consec_losses_pause"
    later = NOW + 60 * 60_000
    c = ctx(consec_losses=15, now_ms=later, last_data_ms=later - 1)
    assert chk(c, rm=rm)[0] is True          # same streak: not re-paused
    c = ctx(consec_losses=16, now_ms=later, last_data_ms=later - 1)
    assert chk(c, rm=rm)[1] == "consec_losses_pause"   # another loss re-pauses
    assert chk(ctx(consec_losses=0, now_ms=later + 61 * 60_000,
                   last_data_ms=later + 61 * 60_000 - 1), rm=rm)[0] is True


def test_reject_breaker_window():
    rm = RiskManager(Settings())
    assert chk(ctx(rejects_window=3), rm=rm)[1] == "reject_breaker"
    t = NOW + 9 * 60_000
    assert chk(ctx(now_ms=t, last_data_ms=t - 1), rm=rm)[1] == "reject_breaker"
    t = NOW + 10 * 60_000
    assert chk(ctx(now_ms=t, last_data_ms=t - 1), rm=rm)[0] is True


def test_daily_loss_latches_until_midnight_utc_then_resets():
    rm = RiskManager(Settings())
    assert chk(ctx(pnl_today_pct=-1.2), rm=rm)[1] == "daily_loss_pct"
    t = NOW + 3600_000  # pnl recovered in ctx but latch holds
    assert chk(ctx(now_ms=t, last_data_ms=t - 1), rm=rm)[1] == "daily_loss_pct"
    t = ms(2026, 11, 19, 0, 0, 0)
    assert chk(ctx(now_ms=t, last_data_ms=t - 1), rm=rm)[0] is True
    assert rm.state()["daily_latched_until_ms"] == 0
    assert rm.state()["day"] == "2026-11-19"


def test_daily_reset_clears_kill_flag_but_not_file():
    rm = RiskManager(Settings())
    rm.check(intent(), ctx(kill_file=True))
    t = ms(2026, 11, 19, 0, 0, 1)
    assert rm.check(intent(), ctx(now_ms=t, last_data_ms=t - 1))[0] is True


def test_must_flatten_cases():
    rm = RiskManager(Settings())
    p = pos()
    assert rm.must_flatten(ctx(), p) == (False, "")
    assert rm.must_flatten(ctx(kill_file=True), None) == (True, "kill")
    assert rm.must_flatten(ctx(), None) == (False, "")
    assert rm.must_flatten(ctx(pnl_today_bps=-151), p) == (True, "daily_loss_bps")
    assert rm.must_flatten(ctx(pnl_today_pct=-1.5), p) == (True, "daily_loss_pct")
    assert rm.must_flatten(ctx(session_open=False), p) == (True, "session_closed")
    assert rm.must_flatten(ctx(minutes_to_close=2.0), p) == (True, "session_close")
    assert rm.must_flatten(ctx(minutes_to_close=2.1), p)[0] is False
    c = ctx(open_positions=1, last_data_ms=NOW - 121_000, seconds_since_reconnect=3.0)
    assert RiskManager(Settings()).must_flatten(c, p) == (True, "disconnect")
    c = ctx(open_positions=1, last_data_ms=NOW - 120_000)
    assert RiskManager(Settings()).must_flatten(c, p)[0] is False
    c = ctx(open_positions=0, last_data_ms=NOW - 500_000)
    assert RiskManager(Settings()).must_flatten(c, p)[0] is False


def test_disconnect_s_overrides_last_data_proxy():
    p = PositionView(41, Side.BUY, 100, 1000.0, NOW - 60_000, NOW + 60_000, None)
    stale = NOW - 500_000
    c = ctx(open_positions=1, last_data_ms=stale, disconnect_s=10.0)
    assert RiskManager(Settings()).must_flatten(c, p)[0] is False
    c = ctx(open_positions=1, last_data_ms=NOW - 1000, disconnect_s=121.0)
    assert RiskManager(Settings()).must_flatten(c, p) == (True, "disconnect")
    c = ctx(open_positions=1, last_data_ms=NOW - 1000, disconnect_s=120.0)
    assert RiskManager(Settings()).must_flatten(c, p)[0] is False


def test_disconnect_flag_set_in_check_and_cleared():
    rm = RiskManager(Settings())
    c = ctx(open_positions=1, last_data_ms=NOW - 200_000)
    ok, r, _ = rm.check(intent(), c)
    assert not ok and r == "disconnect"
    assert rm.force_flatten and rm.force_reason == "disconnect"
    rm.check(intent(), ctx())
    assert not rm.force_flatten


def test_daily_latch_from_must_flatten_blocks_entries():
    rm = RiskManager(Settings())
    rm.must_flatten(ctx(pnl_today_pct=-2), pos())
    assert rm.check(intent(), ctx())[1] == "daily_loss_pct"


# ---- session calendar ----------------------------------------------------------------

H = 3600
D = 86400


def week(day, hh, mm=0):
    """seconds from Sunday 00:00; day 0=Sunday"""
    return day * D + hh * H + mm * 60


def sspec(schedule, tz="UTC", holidays=()):
    return SymbolSpec("XAUUSD", 41, 2, 1, 100, 1, 1, 1000, 0, "POINTS", schedule, tz,
                      list(holidays))


# gold-like: Mon-Thu 00:00-21:00 and 22:00-24:00, Fri 00:00-21:00 (UTC); Sunday 22:00 open
GOLD = [(week(0, 22), week(1, 21)), (week(1, 22), week(2, 21)), (week(2, 22), week(3, 21)),
        (week(3, 22), week(4, 21))]
GOLD.append((week(4, 22), week(5, 21)))


def test_always_open_when_empty():
    cal = SessionCalendar(sspec([]))
    assert cal.is_open(NOW) and cal.minutes_to_close(NOW) is None
    assert cal.bars_since_open(NOW) >= 10 ** 6


def test_gold_daily_break_and_weekend():
    cal = SessionCalendar(sspec(GOLD))
    assert cal.is_open(ms(2026, 11, 18, 20, 59, 59))
    assert not cal.is_open(ms(2026, 11, 18, 21, 0, 0))
    assert not cal.is_open(ms(2026, 11, 18, 21, 59, 59))
    assert cal.is_open(ms(2026, 11, 18, 22, 0, 0))
    # Friday 2026-11-20 close at 21:00, closed Saturday, Sunday until 22:00
    assert not cal.is_open(ms(2026, 11, 21, 12))
    assert not cal.is_open(ms(2026, 11, 22, 21, 59))
    assert cal.is_open(ms(2026, 11, 22, 22, 0))


def test_minutes_to_close_and_bars_since_open():
    cal = SessionCalendar(sspec(GOLD))
    assert cal.minutes_to_close(ms(2026, 11, 18, 20, 30)) == pytest.approx(30.0)
    assert cal.minutes_to_close(ms(2026, 11, 18, 21, 30)) == 0.0
    # Friday close, next close across the weekend is Friday itself
    assert cal.minutes_to_close(ms(2026, 11, 20, 20, 50)) == pytest.approx(10.0)
    # after the daily reopen
    assert cal.bars_since_open(ms(2026, 11, 18, 22, 0, 0)) == 0
    assert cal.bars_since_open(ms(2026, 11, 18, 22, 59, 59)) == 11
    assert cal.bars_since_open(ms(2026, 11, 18, 23, 0, 0)) == 12
    assert cal.bars_since_open(ms(2026, 11, 18, 21, 30)) == 0   # closed
    # Sunday open
    assert cal.bars_since_open(ms(2026, 11, 22, 23, 0)) == 12


def test_contiguous_intervals_not_a_close():
    cal = SessionCalendar(sspec([(0, week(3, 0)), (week(3, 0), week(5, 0))]))
    assert cal.minutes_to_close(ms(2026, 11, 18, 23, 0)) == pytest.approx(
        (ms(2026, 11, 20, 0) - ms(2026, 11, 18, 23)) / 60_000)


def test_week_wrap_interval():
    # open Friday 22:00 through Monday 03:00 (wraps over Sunday 00:00)
    cal = SessionCalendar(sspec([(week(5, 22), week(1, 3))]))
    assert cal.is_open(ms(2026, 11, 21, 23))   # Saturday 23:00 (Fri 22:00 start -> Sat)
    assert cal.is_open(ms(2026, 11, 23, 2))    # Monday 02:00
    assert not cal.is_open(ms(2026, 11, 23, 3))
    assert not cal.is_open(ms(2026, 11, 25, 12))


def test_dst_schedule_tz():
    # Mon-Fri open until Friday 17:00 America/New_York
    cal = SessionCalendar(sspec([(week(1, 0), week(5, 17))], tz="America/New_York"))
    # summer (EDT, UTC-4): Fri 2026-07-17 17:00 local = 21:00 UTC
    assert cal.is_open(ms(2026, 7, 17, 20, 59))
    assert not cal.is_open(ms(2026, 7, 17, 21, 0))
    # winter (EST, UTC-5): Fri 2026-11-20 17:00 local = 22:00 UTC
    assert cal.is_open(ms(2026, 11, 20, 21, 59))
    assert not cal.is_open(ms(2026, 11, 20, 22, 0))
    assert cal.minutes_to_close(ms(2026, 11, 20, 21, 0)) == pytest.approx(60.0)
    assert cal.minutes_to_close(ms(2026, 7, 17, 20, 0)) == pytest.approx(60.0)
    # across the US spring-forward week (2026-03-08): Fri 2026-03-13 is EDT -> 21:00 UTC
    assert not cal.is_open(ms(2026, 3, 13, 21, 0))
    assert cal.is_open(ms(2026, 3, 13, 20, 59))


def test_holidays():
    all_week = [(0, WEEK := 7 * D)]
    xmas = dict(holidayDate=(datetime(2026, 12, 25, tzinfo=timezone.utc) -
                             datetime(1970, 1, 1, tzinfo=timezone.utc)).days,
                isRecurring=False, scheduleTimeZone="UTC")
    cal = SessionCalendar(sspec(all_week, holidays=[xmas]))
    assert cal.is_open(ms(2026, 12, 24, 23, 59))
    assert not cal.is_open(ms(2026, 12, 25, 0, 0))
    assert not cal.is_open(ms(2026, 12, 25, 23, 59))
    assert cal.is_open(ms(2026, 12, 26, 0, 0))
    assert cal.minutes_to_close(ms(2026, 12, 24, 23, 0)) == pytest.approx(60.0)
    assert cal.bars_since_open(ms(2026, 12, 26, 1, 0)) == 12
    # not recurring: next year is open
    assert cal.is_open(ms(2027, 12, 25, 12))


def test_recurring_and_partial_holiday_objects():
    class H:
        holidayDate = (datetime(2020, 1, 1) - datetime(1970, 1, 1)).days
        isRecurring = True
        scheduleTimeZone = "UTC"
        startSecond = 8 * 3600
        endSecond = 10 * 3600
    cal = SessionCalendar(sspec([(0, 7 * D)], holidays=[H()]))
    assert cal.is_open(ms(2027, 1, 1, 7, 59))
    assert not cal.is_open(ms(2027, 1, 1, 8, 0))
    assert not cal.is_open(ms(2026, 1, 1, 9, 59))
    assert cal.is_open(ms(2026, 1, 1, 10, 0))
    assert cal.minutes_to_close(ms(2026, 12, 31, 23, 0)) == pytest.approx(9 * 60)
    assert cal.is_open(ms(2026, 1, 2, 9, 0))


def test_holiday_seconds_and_ms_dates_and_leap_day():
    d_ms = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
    d_s = d_ms // 1000
    leap = (datetime(2024, 2, 29) - datetime(1970, 1, 1)).days
    hs = [dict(holidayDate=d_ms, isRecurring=False), dict(holidayDate=d_s, isRecurring=False,
          startSecond=0, endSecond=0),
          dict(holidayDate=leap, isRecurring=True)]
    cal = SessionCalendar(sspec([(0, 7 * D)], holidays=hs))
    assert not cal.is_open(ms(2026, 5, 1, 12))
    assert cal.is_open(ms(2026, 5, 2, 12))
    assert cal.is_open(ms(2026, 2, 28, 12))   # no Feb 29 in 2026: nothing closed
    assert not cal.is_open(ms(2028, 2, 29, 12))


def test_calendar_feeds_risk_context_semantics():
    cal = SessionCalendar(sspec(GOLD))
    t = ms(2026, 11, 18, 20, 55)
    c = ctx(now_ms=t, last_data_ms=t - 1, session_open=cal.is_open(t),
            minutes_to_close=cal.minutes_to_close(t), bars_since_open=cal.bars_since_open(t))
    assert chk(c)[1] == "near_close"
