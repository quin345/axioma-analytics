"""End to end for the 1-minute instance (config.1m.toml): the full Bot on a TestClock with the fake
KQL reader and FakeTransport. Slot closes every minute, decisions within slot_end + data.deadline_s,
KQL grace/retry/freshness/gap gates at 1-minute settings, orders labelled "ax1m"."""
import re
import sqlite3

import pytest
from twisted.internet import defer

from autotrade import config
from autotrade.clock import TestClock
from autotrade.journal import Journal
from autotrade.kql_source import KqlSource
from autotrade.runner import Bot
from autotrade.types import ExecMode, RunMode
from ctrader_open_api.messages import OpenApiMessages_pb2 as oa
from fake_transport import ACCOUNT_ID, FakeTransport, install_happy
from test_e2e import B0, BASE, HALF_SPREAD, LEVEL, path, qi, symbol_res
from test_kql_e2e import FakeKql

ONE_M_TOML = config.ROOT / "config.1m.toml"
M = 60_000
J = B0 + 360_000          # first 1-minute close after the price jump at B0 + 330 s


class OneMinuteHarness:
    def __init__(self, tmp_path, make_env, mode=RunMode.DRY_RUN, history_h=4, overrides=None, before_start=None):
        self.settings = config.load(make_env(), ONE_M_TOML, overrides={
            "state_dir": str(tmp_path / "state1m"), "log_dir": str(tmp_path / "logs1m"),
            "kill_enabled": False, **(overrides or {})})     # the price jump is a big loss on paper
        self.state = self.settings.state_path()
        self.state.mkdir(parents=True, exist_ok=True)
        self.clock = TestClock(BASE)
        self.fake = FakeTransport()
        install_happy(self.fake)
        self.fake.respond(oa.ProtoOASymbolByIdReq, lambda m: symbol_res(list(m.symbolId)))
        self.fake.respond(oa.ProtoOAReconcileReq, oa.ProtoOAReconcileRes())
        self.fake.respond(oa.ProtoOANewOrderReq, oa.ProtoOAExecutionEvent())
        self.fake.respond(oa.ProtoOAClosePositionReq, oa.ProtoOAExecutionEvent())
        self.kql = FakeKql(self.clock)
        if before_start:
            before_start(self.kql)
        for ts in range(BASE - history_h * 3600_000, BASE, 5_000):
            self.kql.add(ts)
        for e in self.kql.events:
            e["visible_ms"] = min(e["visible_ms"], BASE)
        self.source = KqlSource(str(ACCOUNT_ID), reader=self.kql, reactor=self.clock.reactor,
                                defer_fn=lambda fn, *a: defer.maybeDeferred(fn, *a))
        self.journal = Journal.from_settings(self.settings)
        self.bot = Bot(self.settings, mode, ExecMode.TAKER, self.clock, self.fake, journal=self.journal,
                       kql=self.source)
        self.db_path = self.state / "autotrade.db"

    def start(self):
        self.bot.start()
        self.fake.connect()
        self.clock.advance(0.1)

    def feed_until(self, t_rel_s):
        while (self.clock.now_ms() - B0) / 1000 < t_rel_s:
            for _ in range(5):
                self.clock.advance(1)
            now = self.clock.now_ms()
            for sid in LEVEL:
                mid, h = path(sid, (now - B0) / 1000), HALF_SPREAD[sid]
                self.fake.push(oa.ProtoOASpotEvent(ctidTraderAccountId=ACCOUNT_ID, symbolId=sid,
                                                   bid=qi(mid - h), ask=qi(mid + h), timestamp=now))
            self.kql.add(now)

    def q(self, sql, *args):
        con = sqlite3.connect(self.db_path)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def calls_for(self, slot_ms):
        return [at for at, r in self.kql.requests if r.to_ms == slot_ms]

    def signal(self, slot_ms, sid=1):
        return self.q("SELECT decision, reason FROM signals WHERE slot_end_ms=? AND symbol_id=?", slot_ms, sid)[0]


@pytest.fixture
def h(tmp_path, make_env):
    return lambda **kw: OneMinuteHarness(tmp_path, make_env, **kw)


def test_warm_up_is_three_hours_of_one_minute_bars(h):
    x = h(mode=RunMode.DEMO)
    x.start()
    assert x.bot.tf.key == "1m" and x.bot.history_slots == 120 and x.bot.warmup_hours() == 3.0
    assert len(x.kql.requests) == 1
    r = x.kql.requests[0][1]
    assert r.to_ms == BASE and BASE - r.from_ms == 3 * 3600_000
    ends = [e for (e,) in x.q("SELECT slot_end_ms FROM bars WHERE symbol_id=1 ORDER BY 1")]
    assert len(ends) == 120 and all(b - a == M for a, b in zip(ends, ends[1:])) and ends[-1] == BASE
    x.bot.shutdown("test", flatten=False)


def test_slot_closes_every_minute_and_decides_inside_the_budget(h):
    x = h(mode=RunMode.DEMO)
    (x.state / "PARITY_OK").write_text("")
    x.start()
    x.feed_until(800)
    slots = [B0 + k * M for k in range(2, 14)]
    for t in slots:                                   # one complete query per slot, at slot_end + 2 s
        calls = x.calls_for(t)
        assert calls and 2000 <= calls[0] - t < 3000, (t, calls)   # the test clock steps 1 s
        assert x.q("SELECT COUNT(*) FROM signals WHERE slot_end_ms=?", t)[0][0] == 2
    # incremental fetch: [previous fetch end - 1 slot, t) = two minutes of buckets
    r = [r for at, r in x.kql.requests if r.to_ms == J][0]
    assert J - r.from_ms == 2 * M
    z, decision = x.q("SELECT z_poc, decision FROM signals WHERE slot_end_ms=? AND symbol_id=1", J)[0]
    assert z < -1 and decision in ("enter", "flip", "hold")   # SELL at the first 1-minute close after the jump
    orders = x.fake.sent_of(oa.ProtoOANewOrderReq)
    assert orders
    for o in orders:                                  # every order: label, clientOrderId and comment of ax1m
        assert o.label == "ax1m", o
        m = re.fullmatch(r"ax1m-(XAUUSD|BTCUSD)-(\d+)-E", o.clientOrderId)
        c = re.fullmatch(r"ax1m poc_reversion_1h z=([+-]\d+\.\d\d) sig=(\d+)-(\d+)", o.comment)
        assert m and c, (o.clientOrderId, o.comment)
        assert int(c.group(2)) == o.symbolId and c.group(3) == m.group(2)
        assert abs(float(c.group(1))) > 1 and (float(c.group(1)) > 0) == (o.tradeSide == 1)   # BUY = 1
    for coid, ts in x.q("SELECT client_order_id, ts_ms FROM orders"):
        assert 2000 <= ts % M < 6000, (coid, ts % M)  # every order inside slot_end + [grace, deadline)
    x.bot.shutdown("test", flatten=False)


def test_position_is_time_exited_one_minute_later_by_the_strategy(h):
    x = h()                                           # dry run: the sim broker fills
    x.start()
    x.feed_until(1500)
    trades = x.q("SELECT entry_ms, exit_ms, reason FROM trades WHERE symbol_id=1 ORDER BY entry_ms")
    assert trades
    for entry, exit_, reason in trades:
        assert reason in ("time", "flip")
        assert 2000 <= exit_ % M < 6000               # the strategy's decision, never the +12 s backstop
        assert (exit_ - entry) % M < 6000 and exit_ - entry >= M - 6000
    held = x.q("SELECT decision FROM signals WHERE symbol_id=1 AND slot_end_ms=?", J + M)[0][0]
    assert held in ("hold", "exit", "flip")


def test_late_rows_are_retried_each_second(h):
    x = h()
    x.start()
    x.feed_until(290)
    x.kql.latency_ms = 2500                           # rows of the slot's last seconds land after t + 2 s
    x.feed_until(370)
    calls = x.calls_for(J)
    assert [(c - J) // 1000 for c in calls] == [2, 3]
    assert x.q("SELECT z_poc FROM signals WHERE slot_end_ms=? AND symbol_id=1", J)[0][0] < -1
    assert x.signal(J)[0] in ("enter", "flip", "hold")
    assert x.q("SELECT events FROM bars WHERE slot_end_ms=? AND symbol_id=1", J)[0][0] == 12   # every 5 s row


def test_freshness_gate_at_one_minute(h):
    x = h()
    x.start()
    x.feed_until(290)
    x.kql.latency_ms = 12_000                         # 12 s behind: fresh enough for the 5m bot (15 s), not here (5 s)
    x.feed_until(370)
    calls = x.calls_for(J)
    assert [(c - J) // 1000 for c in calls] == [2, 3, 4, 5]   # retries every 1 s, stops before the 6 s deadline
    assert x.signal(J) == ("none", "kql_stale")
    assert x.bot.om.open_count() == 0
    x.kql.latency_ms = 500
    x.feed_until(430)
    assert x.signal(J + M)[1] != "kql_stale"
    assert x.q("SELECT events FROM bars WHERE slot_end_ms=? AND symbol_id=1", J)[0][0] == 12    # repaired


@pytest.mark.parametrize("hole_s,gated", [(40, True), (25, False)])
def test_gap_gate_counts_missing_10s_buckets_in_the_60_min_window(h, hole_s, gated):
    start = J - 30 * M                                # inside the 60 min window of slot J
    x = h(before_start=lambda k: setattr(k, "hole", (start, start + hole_s * 1000)))
    x.start()
    x.feed_until(370)
    assert (x.signal(J) == ("none", "kql_gap")) is gated
    if gated:
        detail = x.q("SELECT detail FROM risk_events WHERE kind='data_gate' AND symbol_id=1")[0][0]
        assert "kql_gap" in detail
    # an hour later the hole has left the 60 min window: tradable again
    x.feed_until(370 + 3600 + 60)
    assert x.signal(J + 61 * M)[1] != "kql_gap"
