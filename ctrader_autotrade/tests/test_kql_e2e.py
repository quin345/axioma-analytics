"""End to end with [data] source = "kql": a fake KQL reader serves agg_dom rows with an ingestion
latency; the full Bot runs on a TestClock over the FakeTransport (spots only, no depth, no ticks).

Same price path as test_e2e: a jump in slot 2 gives a SELL at the slot 2 close (B0 + 600 s) that
is time-exited at the slot 3 close (B0 + 900 s).
"""
import sqlite3

import pytest
from twisted.internet import defer

from autotrade import config
from autotrade.clock import TestClock
from autotrade.journal import Journal
from autotrade.kql_source import KqlSource, reference_rows
from autotrade.runner import Bot
from autotrade.types import ExecMode, RunMode
from ctrader_open_api.messages import OpenApiMessages_pb2 as oa
from fake_transport import ACCOUNT_ID, FakeTransport, install_happy
from test_e2e import B0, BASE, HALF_SPREAD, HOURS, LEVEL, path, qi, symbol_res

S2, S3, S4 = B0 + 600_000, B0 + 900_000, B0 + 1_200_000


class FakeKql:
    """agg_dom rows with visibility (ingestion) time; rows(req) answers as the KQL query would."""

    def __init__(self, clock):
        self.clock = clock
        self.events: list[dict] = []
        self.latency_ms = 500
        self.fail = False
        self.hole: tuple = ()          # (from_ms, to_ms): no rows written then (a collector outage)
        self.requests = []
        self._seq = 1_795_000_000_000_000   # wall-clock initialised, not 0

    def add(self, ts):
        if self.hole and self.hole[0] <= ts < self.hole[1]:
            return
        for sid in LEVEL:
            mid, h = path(sid, (ts - B0) / 1000), HALF_SPREAD[sid]
            self._seq += 1
            self.events.append({"symbol_id": sid, "ts_ms": ts, "seq": self._seq, "best_bid": qi(mid - h),
                                "best_ask": qi(mid + h), "visible_ms": ts + self.latency_ms})

    def rows(self, req):
        self.requests.append((self.clock.now_ms(), req))
        if self.fail:
            raise ConnectionError("kusto unreachable")
        now = self.clock.now_ms()
        return reference_rows(req, (e for e in self.events if e["visible_ms"] <= now))


class KqlHarness:
    def __init__(self, tmp_path, make_env, make_toml, mode=RunMode.DRY_RUN, history_h=7, overrides=None,
                 before_start=None):
        self.settings = config.load(make_env(), make_toml(), overrides={
            "state_dir": str(tmp_path / "state"), "log_dir": str(tmp_path / "logs"), "warmup_hours": HOURS,
            "data_source": "kql", **(overrides or {})})
        self.state = self.settings.state_path()
        self.state.mkdir(parents=True, exist_ok=True)
        self.clock = TestClock(BASE)
        self.fake = FakeTransport()
        install_happy(self.fake)
        self.fake.respond(oa.ProtoOASymbolByIdReq, lambda m: symbol_res(list(m.symbolId)))
        self.fake.respond(oa.ProtoOAReconcileReq, oa.ProtoOAReconcileRes())
        self.fake.respond(oa.ProtoOANewOrderReq, oa.ProtoOAExecutionEvent())
        # no GetTickData responder: a tick gap fill would fail loudly
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
                       history_slots=30, kql=self.source)
        self.db_path = self.state / "autotrade.db"

    def start(self):
        self.bot.start()
        self.fake.connect()
        self.clock.advance(0.1)

    def feed(self, seconds):
        """Every 5 s a spot per symbol to the bot and a row per symbol to KQL (visible after the latency).
        The clock moves in 1 s steps: task.Clock runs due calls at the end of a step."""
        for _ in range(int(seconds // 5)):
            for _ in range(5):
                self.clock.advance(1)
            now = self.clock.now_ms()
            for sid in LEVEL:
                mid, h = path(sid, (now - B0) / 1000), HALF_SPREAD[sid]
                self.fake.push(oa.ProtoOASpotEvent(ctidTraderAccountId=ACCOUNT_ID, symbolId=sid,
                                                   bid=qi(mid - h), ask=qi(mid + h), timestamp=now))
            self.kql.add(now)

    def feed_until(self, t_rel_s):
        self.feed(t_rel_s - (self.clock.now_ms() - B0) / 1000)

    def q(self, sql, *args):
        con = sqlite3.connect(self.db_path)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def calls_for(self, slot_ms):
        return [r for at, r in self.kql.requests if r.to_ms == slot_ms]

    def signal(self, slot_ms, sid=1):
        return self.q("SELECT decision, reason FROM signals WHERE slot_end_ms=? AND symbol_id=?", slot_ms, sid)[0]


@pytest.fixture
def h(tmp_path, make_env, make_toml):
    return lambda **kw: KqlHarness(tmp_path, make_env, make_toml, **kw)


def test_warm_up_is_one_query_and_demo_trades_at_the_first_signal(h):
    x = h(mode=RunMode.DEMO)                     # no state/PARITY_OK: KQL bars are exact, nothing to gate
    x.start()
    assert x.bot.initialised and x.bot.trading_ready
    assert len(x.kql.requests) == 1               # the whole history in one call
    r = x.kql.requests[0][1]
    assert r.to_ms == BASE and BASE - r.from_ms >= x.bot.warmup_hours() * 3600_000
    assert r.account == str(ACCOUNT_ID) and r.symbol_ids == (1, 2)
    assert not x.fake.sent_of(oa.ProtoOASubscribeDepthQuotesReq)   # spots only
    assert x.fake.sent_of(oa.ProtoOASubscribeSpotsReq)
    assert not x.fake.sent_of(oa.ProtoOAGetTickDataReq)
    assert x.q("SELECT COUNT(*) FROM bars")[0][0] >= 55
    assert x.q("SELECT MAX(approx_share) FROM bars")[0][0] == 0.0
    x.feed_until(610)
    assert x.signal(S2) == ("enter", "enter")
    assert len(x.fake.sent_of(oa.ProtoOANewOrderReq)) == 2
    assert len(x.calls_for(S2)) == 1             # data complete at the first attempt (t + 3 s)
    at, r2 = x.kql.requests[-1]
    assert S2 + 3000 <= at < S2 + 4000 and r2.from_ms <= S2 - 300_000
    kinds = [k for (k,) in x.q("SELECT kind FROM risk_events")]
    assert "kql_error" not in kinds and "warmup_failed" not in kinds
    x.bot.shutdown("test", flatten=False)


def test_late_rows_are_waited_for_and_included(h):
    x = h()
    x.start()
    x.feed_until(550)
    x.kql.latency_ms = 4000                      # at t + 3 s the rows of the slot's last seconds are not in yet
    x.feed_until(615)
    calls = x.calls_for(S2)
    assert len(calls) == 2                       # retried once (t + 5 s): then a row stamped >= t is visible
    assert [(at - S2) // 1000 for at, r in x.kql.requests if r.to_ms == S2] == [3, 5]
    assert x.signal(S2) == ("enter", "enter")
    # the bar holds every event of the slot (2 symbols x 60 rows per 5 min), including the late ones
    assert x.q("SELECT events FROM bars WHERE slot_end_ms=? AND symbol_id=1", S2)[0][0] == 60


def test_stale_kql_blocks_entries_and_is_journalled(h):
    x = h()
    x.start()
    x.feed_until(550)
    x.kql.latency_ms = 30_000                    # KQL lags 30 s > freshness_s (15 s), the whole retry budget
    x.feed_until(625)
    assert len(x.calls_for(S2)) == 4             # 1 + retries (3) attempts at t+3, +5, +7, +9 s
    assert x.signal(S2) == ("none", "kql_stale")    # the jump is in the data seen, but no entry
    assert x.bot.om.open_count() == 0
    ev = x.q("SELECT detail FROM risk_events WHERE kind='data_gate' AND symbol_id=1")
    assert ev and ev[0][0].startswith("ok -> kql_stale")
    # caught up again: tradable; the stale bar is rebuilt from the complete data
    stale_events = x.q("SELECT events FROM bars WHERE slot_end_ms=? AND symbol_id=1", S2)[0][0]
    x.kql.latency_ms = 500
    x.feed_until(910)
    assert x.q("SELECT events FROM bars WHERE slot_end_ms=? AND symbol_id=1", S2)[0][0] == 60 > stale_events
    assert x.q("SELECT detail FROM risk_events WHERE kind='data_gate' AND symbol_id=1")[-1][0].startswith(
        "kql_stale -> ok")


def test_stale_data_still_time_exits_open_positions(h):
    x = h()
    x.start()
    x.feed_until(610)
    assert x.bot.om.open_count() == 2            # SELL opened at the slot 2 close
    x.kql.latency_ms = 600_000                   # KQL 10 minutes behind
    x.feed_until(940)
    assert x.bot.om.open_count() == 0
    trades = x.q("SELECT reason, exit_ms FROM trades")
    assert len(trades) == 2
    assert all(r == "time" and S3 <= e < S3 + 11_000 for r, e in trades)   # by the strategy, within the deadline
    assert x.q("SELECT decision FROM signals WHERE slot_end_ms=? AND symbol_id=1", S3)[0][0] == "exit"


def test_kql_errors_never_crash_and_recover(h):
    x = h()
    x.start()
    x.feed_until(550)
    x.kql.fail = True
    x.feed_until(625)
    assert not x.bot.done.called and x.bot.trading_ready
    assert len(x.calls_for(S2)) == 4
    assert x.signal(S2)[0] == "none" and x.signal(S2, 2)[0] == "none"
    assert x.bot.om.open_count() == 0
    kinds = [k for (k,) in x.q("SELECT kind FROM risk_events")]
    assert "kql_error" in kinds and "data_gate" in kinds and "slot_error" not in kinds
    x.kql.fail = False
    x.feed_until(910)
    assert x.signal(S3)[1] != "kql_error"
    assert x.q("SELECT events FROM bars WHERE slot_end_ms=? AND symbol_id=1", S2)[0][0] == 60   # rebuilt


def test_warm_up_failure_retries_the_history_at_the_next_slot(h):
    x = h(mode=RunMode.DEMO, before_start=lambda k: setattr(k, "fail", True))
    x.start()
    assert x.bot.initialised and not x.bot._kql_warmed
    assert "warmup_failed" in [k for (k,) in x.q("SELECT kind FROM risk_events")]
    x.feed_until(320)                            # slot 1 fails too: a placeholder bar, no decision possible
    s1 = B0 + 300_000
    assert x.signal(s1) == ("none", "nan_z") and not x.bot._kql_warmed
    assert x.q("SELECT has_data FROM bars WHERE slot_end_ms=? AND symbol_id=1", s1)[0][0] == 0
    x.kql.fail = False
    x.feed_until(610)                            # slot 2: history fetch, bars rebuilt, slot closed
    assert x.bot._kql_warmed
    first = x.calls_for(S2)[0]
    assert S2 - first.from_ms >= x.bot.warmup_hours() * 3600_000
    assert x.signal(S2) == ("enter", "enter")    # ready without waiting 4 hours
    # the placeholder is replaced in the journal by the exact bar
    n1 = sum(1 for e in x.kql.events if e["symbol_id"] == 1 and s1 - 300_000 <= e["ts_ms"] < s1)
    assert x.q("SELECT has_data, events FROM bars WHERE slot_end_ms=? AND symbol_id=1", s1)[0] == (1, n1)


def test_gap_in_the_vap_window_blocks_entries(h):
    hole = (B0 - 3600_000, B0 - 3600_000 + 180_000)      # 3 minutes without rows, market open
    x = h(before_start=lambda k: setattr(k, "hole", hole))
    x.start()
    x.feed_until(610)
    assert x.signal(S2) == ("none", "kql_gap")
    detail = x.q("SELECT detail FROM risk_events WHERE kind='data_gate' AND symbol_id=1")[0][0]
    assert "kql_gap" in detail and "longest 180s" in detail
    # the bar itself is exact: journalled with its events, approx_share 0
    assert x.q("SELECT events, approx_share FROM bars WHERE slot_end_ms=? AND symbol_id=1", S2)[0] == (60, 0.0)


def test_ctrader_fallback_still_subscribes_depth(tmp_path, make_env, make_toml):
    from test_e2e import Harness
    x = Harness(tmp_path, make_env, make_toml)   # data_source = "ctrader"
    x.start()
    assert x.fake.sent_of(oa.ProtoOASubscribeDepthQuotesReq) and x.bot.kql is None
    x.bot.shutdown("test")
