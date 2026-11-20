"""End to end: FakeTransport drives a full Bot (handshake, warm-up, live stream across slot
boundaries on a TestClock) in DRY_RUN and DEMO.

History: 8 h of stored 10 s buckets are preloaded in the journal, so warm-up rebuilds enough bars for
the real feature engine (the rolling z needs 12+ valid bars). Prices follow a smooth sine; the
scripted path then jumps up in slot 2 (a strong SELL reversion signal at the slot 2 close), stays
a little higher for 40 s (the maker-sim sell limit fills) and falls back, so the position is
time-exited at the slot 3 close.
"""
import math
import random
import sqlite3

import pytest

from autotrade import config
from autotrade.clock import TestClock
from autotrade.journal import Journal
from autotrade.runner import EXIT_OK, EXIT_REFUSED, Bot
from autotrade.types import Bucket, ExecMode, RunMode
from ctrader_open_api.messages import OpenApiMessages_pb2 as oa
from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model
from fake_transport import ACCOUNT_ID, FakeTransport, install_happy

B0 = 1_800_000_000_000            # a 5 minute boundary (ms)
BASE = B0 + 60_000                # start time: 60 s into slot 1
HOURS = 5                     # warmup_hours setting; the fetch is widened to cover history_slots (6.5 h)
PRELOAD_HOURS = 7
PHASE = 2.0                   # slow-sine phase: no history signal at the slot 1 close (full warm-up VAP windows)
LEVEL = {1: 2000.0, 2: 40000.0}   # symbol id -> price level
HALF_SPREAD = {1: 0.2, 2: 4.0}


def path(sid, t_rel_s):
    """Mid price at t_rel_s seconds after B0 (history before 0 is a sine plus fixed noise)."""
    base = LEVEL[sid] * (1 + 0.0004 * math.sin(t_rel_s / 1500.0 + PHASE) + 0.0001 * math.sin(t_rel_s / 170.0))
    jump = 0.0
    if 330 <= t_rel_s < 605:
        jump = 0.015 * LEVEL[sid]
    elif 605 <= t_rel_s < 645:
        jump = 0.0153 * LEVEL[sid]
    return base + jump


def qi(price):
    return int(round(price * 100_000))


def symbol_res(ids):
    """Always-open instruments (the shared builder has a weekly schedule that is mostly closed)."""
    res = oa.ProtoOASymbolByIdRes(ctidTraderAccountId=ACCOUNT_ID)
    for sid in ids:
        res.symbol.add(symbolId=sid, digits=2, pipPosition=1, lotSize=100, minVolume=100, stepVolume=100,
                       maxVolume=10_000_000, slDistance=50, distanceSetIn=model.SYMBOL_DISTANCE_IN_POINTS,
                       scheduleTimeZone="UTC")
    return res


class Harness:
    def __init__(self, tmp_path, make_env, make_toml, mode=RunMode.DRY_RUN, live=False, exec_mode=ExecMode.TAKER,
                 history_slots=30, preload=True, hours=None, overrides=None):
        self.settings = config.load(make_env(), make_toml(), overrides={
            "state_dir": str(tmp_path / "state"), "log_dir": str(tmp_path / "logs"), "warmup_hours": HOURS,
            **(overrides or {})})
        self.state = self.settings.state_path()
        self.state.mkdir(parents=True, exist_ok=True)
        self.clock = TestClock(BASE)
        self.fake = FakeTransport()
        install_happy(self.fake, live=live)
        self.fake.respond(oa.ProtoOASymbolByIdReq, lambda m: symbol_res(list(m.symbolId)))
        self.fake.respond(oa.ProtoOAGetTickDataReq, oa.ProtoOAGetTickDataRes(hasMore=False))
        self.fake.respond(oa.ProtoOAReconcileReq, oa.ProtoOAReconcileRes())
        self.fake.respond(oa.ProtoOANewOrderReq, oa.ProtoOAExecutionEvent())
        self.journal = Journal.from_settings(self.settings)
        if preload:
            self.preload()
        self.bot = Bot(self.settings, mode, exec_mode, self.clock, self.fake, journal=self.journal,
                       history_slots=history_slots, hours=hours)
        self.db_path = self.state / "autotrade.db"

    def preload(self):
        for sid in LEVEL:
            h = HALF_SPREAD[sid]
            start = BASE - PRELOAD_HOURS * 3600_000
            bs = []
            for t in range(start, BASE, 10_000):
                mid = path(sid, (t - B0) / 1000)
                bs.append(Bucket(sid, t, qi(mid - h), qi(mid + h), 20, "store"))
            self.journal.save_buckets(bs)

    def start(self):
        self.bot.start()
        self.fake.connect()
        self.clock.advance(0.1)       # first_subscribe runs on call_later(0)

    def feed(self, seconds):
        """Advance the clock in 5 s steps and push one spot and one depth event per symbol."""
        for _ in range(int(seconds // 5)):
            self.clock.advance(5)
            now = self.clock.now_ms()
            for sid in LEVEL:
                mid, h = path(sid, (now - B0) / 1000), HALF_SPREAD[sid]
                self.fake.push(oa.ProtoOASpotEvent(ctidTraderAccountId=ACCOUNT_ID, symbolId=sid,
                                                   bid=qi(mid - h), ask=qi(mid + h), timestamp=now))
                ev = oa.ProtoOADepthEvent(ctidTraderAccountId=ACCOUNT_ID, symbolId=sid)
                ev.newQuotes.add(id=1, size=100, bid=qi(mid - h))
                ev.newQuotes.add(id=2, size=100, ask=qi(mid + h))
                self.fake.push(ev)

    def feed_until(self, t_rel_s):
        self.feed(t_rel_s - (self.clock.now_ms() - B0) / 1000)

    def q(self, sql, *args):
        con = sqlite3.connect(self.db_path)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()


@pytest.fixture
def h(tmp_path, make_env, make_toml):
    return lambda **kw: Harness(tmp_path, make_env, make_toml, **kw)


def test_dry_run_full_cycle(h):
    x = h()
    x.start()
    bot = x.bot
    # handshake -> warm-up -> subscribe (spots then depth), nothing sent that trades
    assert bot.initialised and bot.trading_ready
    kinds = [type(m).__name__ for m in x.fake.sent]
    assert kinds.index("ProtoOASubscribeSpotsReq") < kinds.index("ProtoOASubscribeDepthQuotesReq")
    assert kinds.index("ProtoOASymbolByIdReq") < kinds.index("ProtoOASubscribeSpotsReq")
    warm_bars = x.q("SELECT COUNT(*) FROM bars")[0][0]
    assert warm_bars >= 55             # 2 symbols x 30 slots rebuilt from the stored buckets
    assert x.q("SELECT mode, is_live FROM runs") == [("DRY_RUN", 0)]

    x.feed_until(1000)                 # past the slot 3 close (900 s) + grace
    slots = [B0 + 300_000 * k for k in (1, 2, 3)]
    # bars journalled at every slot, both symbols
    for s in slots:
        assert x.q("SELECT COUNT(*) FROM bars WHERE slot_end_ms=?", s)[0][0] == 2
    assert x.q("SELECT COUNT(*) FROM bars")[0][0] == warm_bars + 3 * 2
    # one signal row per bar and symbol, features present
    rows = x.q("SELECT slot_end_ms, symbol_id, z_poc, decision, reason, taker_entry FROM signals "
               "ORDER BY slot_end_ms, symbol_id")
    assert len(rows) == 3 * 2
    assert all(r[2] is not None for r in rows)

    # the jump gives a SELL at the slot 2 close, taker and maker shadows filled one bar later
    sig = x.q("SELECT decision, taker_entry, taker_exit, taker_pnl_bps, maker_outcome, maker_fill_price, "
              "cf_return_bps, z_poc FROM signals WHERE slot_end_ms=? AND symbol_id=1", slots[1])[0]
    decision, t_entry, t_exit, t_pnl, m_out, m_px, cf, z = sig
    assert z < -1 and decision == "enter"
    assert t_entry is not None and t_exit is not None and t_pnl is not None
    assert t_pnl > 0                                      # price fell back: a short wins
    assert m_out == "fill" and m_px is not None           # price ticked up after the signal
    assert cf is not None and cf > 0

    # a SimBroker position was opened at the slot 2 close and time-exited at the slot 3 close
    trades = x.q("SELECT symbol_id, side, reason, entry_ms, exit_ms, pnl_bps_price FROM trades")
    assert len(trades) == 2 and {t[0] for t in trades} == {1, 2}
    for sid, side, reason, entry_ms, exit_ms, pnl in trades:
        assert side == "SELL" and reason == "time" and pnl > 0
        assert slots[1] <= entry_ms < slots[1] + 10_000
        assert slots[2] <= exit_ms < slots[2] + 10_000
    # dry-run never trades for real
    assert not [m for m in x.fake.sent if isinstance(m, (oa.ProtoOANewOrderReq, oa.ProtoOAClosePositionReq))]
    assert bot.om.open_count() == 0

    # P4: research features recomputed over the bars table match the journalled ones
    from autotrade import parity
    con = sqlite3.connect(x.db_path)
    rep = parity.parity_report(con)
    con.close()
    assert rep["rows"] == 6
    for st in rep["symbols"].values():
        assert st["max_abs"]["z_poc"] < 1e-9 and st["max_abs"]["z_range"] < 1e-9
        assert st["nan_mismatch"]["z_poc"] == 0
    assert "z_poc" in parity.render(rep)

    # graceful shutdown
    out = []
    bot.shutdown("test").addCallback(out.append)
    assert out == [EXIT_OK] and bot.done.called and x.fake.stopped
    assert x.q("SELECT ended_ms IS NOT NULL FROM runs") == [(1,)]


def test_kill_file_flattens_and_stops(h):
    x = h()
    x.start()
    x.feed_until(610)                  # slot 2 closed at 602 s: SELL open
    assert x.bot.om.open_count() == 2
    (x.state / "KILL").write_text("")
    x.feed(5)                          # the 5 s risk tick sees the file
    assert x.bot.done.called and x.bot.exit_code == EXIT_OK
    trades = x.q("SELECT reason FROM trades")
    assert [t[0] for t in trades] == ["kill", "kill"]
    assert ("kill",) in x.q("SELECT kind FROM risk_events")
    assert x.fake.stopped
    n = len(x.q("SELECT 1 FROM bars"))
    x.clock.advance(600)               # no further slot processing after the stop
    assert len(x.q("SELECT 1 FROM bars")) == n


def test_pause_file_blocks_entries_but_journals(h):
    x = h()
    x.start()
    (x.state / "PAUSE").write_text("")
    x.feed_until(610)
    assert x.bot.om.open_count() == 0
    d = x.q("SELECT decision, reason FROM signals WHERE slot_end_ms=? AND symbol_id=1", B0 + 600_000)[0]
    assert d == ("none", "pause_file")
    # the shadows still ran
    assert x.q("SELECT taker_entry IS NOT NULL FROM signals WHERE slot_end_ms=? AND symbol_id=1",
               B0 + 600_000)[0] == (1,)


def test_demo_refuses_live_account(h):
    x = h(mode=RunMode.DEMO, live=True)
    x.start()
    assert x.bot.done.called and x.bot.exit_code == EXIT_REFUSED
    assert not x.bot.initialised
    assert not [m for m in x.fake.sent if type(m).__name__.endswith(("NewOrderReq", "ReconcileReq"))]
    assert x.q("SELECT is_live FROM runs") == [(1,)]
    assert ("refused",) in x.q("SELECT kind FROM risk_events")


def test_demo_waits_for_parity_gate_on_approximate_bars(h):
    x = h(mode=RunMode.DEMO)
    x.start()
    assert x.fake.sent_of(oa.ProtoOAReconcileReq)          # startup reconcile ran
    x.feed_until(610)
    assert not x.fake.sent_of(oa.ProtoOANewOrderReq)
    d = x.q("SELECT decision, reason FROM signals WHERE slot_end_ms=? AND symbol_id=1", B0 + 600_000)[0]
    assert d == ("none", "approx")
    x.bot.shutdown("test")


def test_demo_sends_orders_through_the_session_once_gate_passed(h):
    x = h(mode=RunMode.DEMO)
    (x.state / "PARITY_OK").write_text("")
    x.start()
    x.feed_until(610)
    orders = x.fake.sent_of(oa.ProtoOANewOrderReq)
    assert len(orders) == 2
    o = [m for m in orders if m.symbolId == 1][0]
    assert o.label == "axq-poc4h-v1" and o.tradeSide == model.SELL and o.relativeStopLoss > 0
    assert o.clientOrderId.startswith("XAUUSD-") and o.comment.startswith("1-")
    x.bot.shutdown("test", flatten=False)


def test_hours_bound_stops_the_bot(h):
    x = h(hours=0.01)                  # 36 s
    x.start()
    x.feed(40)
    assert x.bot.done.called and x.bot.exit_code == EXIT_OK


def test_long_disconnect_flattens_after_reconnect(h):
    x = h()
    x.start()
    x.feed_until(610)
    assert x.bot.om.open_count() == 2
    x.fake.disconnect("network")
    assert not x.bot.trading_ready
    x.clock.advance(130)               # longer than flatten_after_disconnect_s (120)
    x.fake.connect()
    x.clock.advance(0.1)
    assert x.bot.trading_ready and x.bot.initialised
    assert x.bot.om.open_count() == 2  # nothing flattened while offline
    assert len(x.fake.sent_of(oa.ProtoOASubscribeSpotsReq)) == 2   # once at start, once by the session after the reconnect
    x.feed(5)                          # risk tick: outage was 130 s
    assert [t[0] for t in x.q("SELECT reason FROM trades")] == ["disconnect", "disconnect"]
    assert ("reconnected",) in x.q("SELECT kind FROM risk_events")
    x.bot.shutdown("test")
