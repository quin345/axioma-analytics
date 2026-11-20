"""Configurable daily reset: the helpers, config validation, and every daily quantity on the boundary."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from autotrade.config import ConfigError, Settings, load
from autotrade.journal import Journal
from autotrade.resetday import day_label, day_start_ms, next_reset_ms
from autotrade.risk import RiskManager
from autotrade.types import Intent, IntentKind, Quote, RiskContext, RunMode, Side

H = 3_600_000


def utc(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


def local(tz, *a):
    return int(datetime(*a, tzinfo=ZoneInfo(tz)).timestamp() * 1000)


def S(reset="00:00", tz="UTC"):
    return Settings(daily_reset=reset, daily_reset_tz=tz)


def test_utc_midnight_default():
    s = Settings()
    now = utc(2026, 11, 18, 12, 30)
    assert day_start_ms(now, s) == utc(2026, 11, 18)
    assert next_reset_ms(now, s) == utc(2026, 11, 19)
    assert day_start_ms(utc(2026, 11, 18), s) == utc(2026, 11, 18)       # the reset instant starts a day
    assert next_reset_ms(utc(2026, 11, 18), s) == utc(2026, 11, 19)
    assert day_start_ms(utc(2026, 11, 18) - 1, s) == utc(2026, 11, 17)
    assert day_label(now, s) == "2026-11-18"


def test_utc_non_midnight():
    s = S("05:30")
    assert day_start_ms(utc(2026, 11, 18, 5, 29), s) == utc(2026, 11, 17, 5, 30)
    assert day_start_ms(utc(2026, 11, 18, 5, 30), s) == utc(2026, 11, 18, 5, 30)
    assert next_reset_ms(utc(2026, 11, 18, 5, 30), s) == utc(2026, 11, 19, 5, 30)


def test_new_york_1700_across_fall_back():
    s = S("17:00", "America/New_York")       # DST ends Sunday 2026-11-01 02:00 local
    assert utc(2026, 10, 31, 21) == local("America/New_York", 2026, 10, 31, 17)   # EDT = UTC-4
    assert utc(2026, 11, 1, 22) == local("America/New_York", 2026, 11, 1, 17)     # EST = UTC-5
    now = utc(2026, 11, 1, 12)
    assert day_start_ms(now, s) == utc(2026, 10, 31, 21)
    assert next_reset_ms(now, s) == utc(2026, 11, 1, 22)
    assert (next_reset_ms(now, s) - day_start_ms(now, s)) == 25 * H                # the long day
    assert day_start_ms(utc(2026, 11, 1, 22), s) == utc(2026, 11, 1, 22)
    assert next_reset_ms(utc(2026, 11, 1, 22), s) == utc(2026, 11, 2, 22)
    assert day_start_ms(utc(2026, 11, 1, 21, 59), s) == utc(2026, 10, 31, 21)
    assert day_label(now, s) == "2026-10-31"


def test_new_york_1700_across_spring_forward():
    s = S("17:00", "America/New_York")       # DST starts Sunday 2026-03-08
    now = utc(2026, 3, 8, 12)
    assert day_start_ms(now, s) == utc(2026, 3, 7, 22)
    assert next_reset_ms(now, s) == utc(2026, 3, 8, 21)
    assert (next_reset_ms(now, s) - day_start_ms(now, s)) == 23 * H


def test_tokyo_no_dst():
    s = S("09:00", "Asia/Tokyo")               # UTC+9: 00:00 UTC
    assert day_start_ms(utc(2026, 11, 18, 0, 0), s) == utc(2026, 11, 18)
    assert day_start_ms(utc(2026, 11, 17, 23, 59), s) == utc(2026, 11, 17)
    assert next_reset_ms(utc(2026, 11, 17, 23, 59), s) == utc(2026, 11, 18)
    t = S("00:00", "Asia/Tokyo")               # local midnight = 15:00 UTC the day before
    assert day_start_ms(utc(2026, 11, 18, 14, 59), t) == utc(2026, 11, 17, 15)
    assert next_reset_ms(utc(2026, 11, 18, 14, 59), t) == utc(2026, 11, 18, 15)
    assert day_label(utc(2026, 11, 18, 14, 59), t) == "2026-11-18"


def test_day_start_le_now_lt_next_everywhere():
    s = S("02:30", "America/New_York")          # 02:30 does not exist on 2026-03-08, is ambiguous-adjacent in Nov
    for k in range(0, 400 * 24):
        now = utc(2026, 1, 1) + k * H + 17 * 60_000
        a, b = day_start_ms(now, s), next_reset_ms(now, s)
        assert a <= now < b and 22 * H <= b - a <= 26 * H


@pytest.mark.parametrize("kw", [{"daily_reset": "24:00"}, {"daily_reset": "9:00"}, {"daily_reset": "0000"},
                                {"daily_reset": "12:60"}, {"daily_reset": 9},
                                {"daily_reset_tz": "Mars/Phobos"}, {"daily_reset_tz": ""}])
def test_bad_reset_config_is_config_error(make_env, make_toml, kw):
    with pytest.raises(ConfigError, match="daily_reset"):
        load(make_env(), make_toml(), overrides={"risk": kw})


def test_good_reset_config_loads(make_env, make_toml):
    s = load(make_env(), make_toml('[risk]\ndaily_reset = "17:00"\ndaily_reset_tz = "America/New_York"\n'))
    assert (s.daily_reset, s.daily_reset_tz) == ("17:00", "America/New_York")


# ---- every daily quantity follows the boundary ----

def add_trade(j, exit_ms, bps=-1.0, net=-1.0):
    j.record_trade(symbol_id=1, side=Side.BUY, volume=1, signal_id="s", client_order_id="c", position_id=None,
                   entry_ms=exit_ms - 1000, exit_ms=exit_ms, entry_price=1.0, exit_price=1.0, reason="time",
                   pnl_bps_mid=bps, pnl_bps_price=bps, pnl_net=net, gross=net, commission=0.0, swap=0.0,
                   money_digits=2, adverse_bps=0.0, markout_bps=0.0)


def test_journal_today_queries_use_the_configured_boundary(tmp_path):
    s = S("17:00", "America/New_York")
    j = Journal(tmp_path / "t.db", tmp_path / "t.jsonl")
    j.use_daily_reset(s)
    j.start_run(RunMode.DRY_RUN, s)
    b = utc(2026, 11, 1, 22)                                  # a reset instant (17:00 EST)
    add_trade(j, b - 1, bps=-10, net=-5)                       # previous trading day
    add_trade(j, b + 1, bps=-2, net=-1)
    add_trade(j, b + 2 * H, bps=-3, net=-2)
    now = b + 3 * H                                            # 20:00 EST: calendar UTC day is Nov 2 already
    assert j.trades_today(now) == 2
    assert j.pnl_today_bps(now) == -5
    assert j.pnl_net_today(now) == -3
    assert j.trades_today(b - 1) == 1 and j.pnl_net_today(b - 1) == -5
    # the plain UTC day (no reset configured) would have cut elsewhere
    k = Journal(tmp_path / "u.db", tmp_path / "u.jsonl")
    k.start_run(RunMode.DRY_RUN, s)
    add_trade(k, b - 1)
    add_trade(k, b + 1)
    assert k.trades_today(b + 3 * H) == 0 and k.trades_today(b + H) == 2   # default: cut at UTC midnight
    j.close()
    k.close()


def _ctx(now, **kw):
    d = dict(now_ms=now, quote=Quote(41, 1000, 1010, now), spread_median_48=10.0, last_data_ms=now - 1000,
             session_open=True, minutes_to_close=None, bars_since_open=100, open_positions=0, trades_today=0,
             orders_last_min=0, pnl_today_bps=0.0, pnl_today_pct=0.0, consec_losses=0, rejects_window=0,
             kill_file=False, pause_file=False, seconds_since_reconnect=None)
    d.update(kw)
    return RiskContext(**d)


ENTER = Intent(IntentKind.ENTER, 41, Side.BUY, 1.0, None, None, 1000, "enter", "41-1")


def test_daily_loss_latch_lifts_at_the_configured_reset():
    s = S("17:00", "America/New_York")
    rm = RiskManager(s)
    t0 = utc(2026, 11, 1, 12)                                   # 07:00 EST, long day ends 22:00Z
    assert rm.check(ENTER, _ctx(t0, pnl_today_pct=-1.5))[1] == "daily_loss_pct"
    assert rm.state()["daily_latched_until_ms"] == utc(2026, 11, 1, 22)
    assert rm.state()["day"] == "2026-10-31"
    t1 = utc(2026, 11, 1, 21, 59)                               # UTC midnight is far away; reset is not yet
    assert rm.check(ENTER, _ctx(t1))[1] == "daily_loss_pct"
    t2 = utc(2026, 11, 1, 22)
    assert rm.check(ENTER, _ctx(t2))[0] is True
    assert rm.state()["daily_latched_until_ms"] == 0 and rm.state()["day"] == "2026-11-01"


def test_default_latch_is_still_utc_midnight():
    rm = RiskManager(Settings())
    t0 = utc(2026, 11, 18, 23, 59)
    assert rm.check(ENTER, _ctx(t0, pnl_today_bps=-200))[1] == "daily_loss_bps"
    assert rm.check(ENTER, _ctx(utc(2026, 11, 19)))[0] is True


def test_latch_daily_blocks_until_next_reset():
    s = S("08:15")
    rm = RiskManager(s)
    now = utc(2026, 11, 18, 3)
    rm.latch_daily("daily_max_loss", now)
    assert rm.daily_latched(now) and rm.check(ENTER, _ctx(now))[1] == "daily_max_loss"
    assert rm.check(ENTER, _ctx(utc(2026, 11, 18, 8, 14)))[1] == "daily_max_loss"
    assert rm.check(ENTER, _ctx(utc(2026, 11, 18, 8, 15)))[0] is True
    assert not rm.daily_latched(utc(2026, 11, 18, 8, 15))
