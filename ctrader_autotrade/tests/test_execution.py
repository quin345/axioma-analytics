import random

import pytest
from twisted.internet import defer

from autotrade.execution import (DemoBroker, OrderManager, RefusedError, State, clamp_stop_rel, min_stop_rel,
                                 normalise_volume, round_price, to_exec_report, BrokerPosition, BrokerOrder)
from autotrade.types import ExecMode, IntentKind, RunMode, Side, SymbolSpec

from helpers_exec import (LABEL, SLOT, SPEC, SPEC2, StubBroker, intent, make, quote, rep)

COID = f"ax5m-XAUUSD-{SLOT}-E"


def now(clock):
    return clock.now_ms()


def enter_market(tmp_path, **kw):
    om, b, j, clock = make(tmp_path, **kw)
    om.handle(intent(), SPEC, quote(), now(clock))
    return om, b, j, clock


def fill_entry(om, clock, vol=200, price=2000.1, pid=7, oid=11):
    om.on_report(rep("ORDER_ACCEPTED", order_id=oid, client_order_id=COID))
    om.on_report(rep("ORDER_FILLED", order_id=oid, client_order_id=COID, position_id=pid, side=Side.BUY,
                     filled_volume=vol, price=price, ts_ms=now(clock)))


def trades(j):
    return j.db.execute("SELECT reason, entry_price, exit_price, pnl_bps_price, pnl_net FROM trades").fetchall()


def test_market_entry_open_time_exit(tmp_path):
    om, b, j, clock = enter_market(tmp_path)
    assert om.state_of(41) is State.PENDING_NEW
    call = b.of("place")[0][1]
    assert call["volume"] == 200 and call["coid"] == COID and call["label"] == LABEL and call["stop"] == 50000
    assert call["mode"] is ExecMode.TAKER and call["comment"] == "ax5m poc_reversion_4h sig=sig1"
    fill_entry(om, clock)
    assert om.state_of(41) is State.OPEN
    pv = om.position_view(41)
    assert pv.side is Side.BUY and pv.volume == 200 and pv.entry_price == 2000.1
    assert pv.exit_at_ms == (SLOT + 302) * 1000 and pv.position_id == 7
    clock.advance(300)                      # exit_at reached; strategy silent -> backstop after 10 s
    assert om.state_of(41) is State.OPEN
    clock.advance(10)
    assert om.state_of(41) is State.EXIT_PENDING
    assert b.of("close") == [("close", 7, 200)]
    om.on_report(rep("ORDER_FILLED", order_id=12, position_id=7, side=Side.SELL, filled_volume=200,
                     price=2001.1, gross_profit=200, commission=-4, swap=0, money_digits=2,
                     ts_ms=now(clock)))
    assert om.state_of(41) is State.IDLE and om.position_view(41) is None
    (reason, ep, xp, bps, net), = trades(j)
    assert reason == "time" and ep == 2000.1 and xp == 2001.1
    assert bps == pytest.approx(1 / 2000.1 * 1e4) and net == pytest.approx(1.96)


def test_strategy_exit_and_hold(tmp_path):
    om, b, j, clock = enter_market(tmp_path)
    fill_entry(om, clock)
    om.handle(intent(IntentKind.HOLD), SPEC, quote(), now(clock))
    assert om.position_view(41).exit_at_ms == (SLOT + 602) * 1000
    om.handle(intent(IntentKind.EXIT, reason="session"), SPEC, quote(), now(clock))
    assert om.state_of(41) is State.EXIT_PENDING and len(b.of("close")) == 1
    om.on_report(rep("ORDER_FILLED", position_id=7, side=Side.SELL, filled_volume=200, price=2000.0,
                     gross_profit=-20, commission=-2, swap=0, money_digits=2))
    assert trades(j)[0][0] == "session"


def test_maker_partial_then_expiry(tmp_path):
    om, b, j, clock = make(tmp_path, exec_mode=ExecMode.MAKER)
    om.handle(intent(limit=2000_00500 * 1000 // 1000, expiry=(SLOT + 122) * 1000), SPEC, quote(), now(clock))
    call = b.of("place")[0][1]
    assert call["mode"] is ExecMode.MAKER and call["expiry"] == (SLOT + 122) * 1000
    om.on_report(rep("ORDER_ACCEPTED", order_id=11, client_order_id=COID))
    assert om.state_of(41) is State.WORKING
    om.on_report(rep("ORDER_PARTIAL_FILL", order_id=11, client_order_id=COID, position_id=7, side=Side.BUY,
                     filled_volume=100, price=2000.0))
    assert om.state_of(41) is State.PARTIAL and om.position_view(41).volume == 100
    om.on_report(rep("ORDER_EXPIRED", order_id=11, client_order_id=COID))
    assert om.state_of(41) is State.OPEN and om.position_view(41).volume == 100
    assert not b.of("close")                  # exits on schedule, not at once
    clock.advance(320)
    assert b.of("close") == [("close", 7, 100)]


def test_maker_expiry_safety_cancel_and_idle(tmp_path):
    om, b, j, clock = make(tmp_path, exec_mode=ExecMode.MAKER)
    om.handle(intent(limit=200000000, expiry=(SLOT + 122) * 1000), SPEC, quote(), now(clock))
    om.on_report(rep("ORDER_ACCEPTED", order_id=11, client_order_id=COID))
    clock.advance(120 + 5)
    assert om.state_of(41) is State.CANCEL_PENDING and b.of("cancel") == [("cancel", 11)]
    om.on_report(rep("ORDER_CANCELLED", order_id=11, client_order_id=COID))
    assert om.state_of(41) is State.IDLE and not trades(j)


def test_cancel_rejected_then_filled(tmp_path):
    om, b, j, clock = make(tmp_path, exec_mode=ExecMode.MAKER)
    om.handle(intent(limit=200000000, expiry=(SLOT + 122) * 1000), SPEC, quote(), now(clock))
    om.on_report(rep("ORDER_ACCEPTED", order_id=11, client_order_id=COID))
    om.handle(intent(IntentKind.EXIT, reason="kill"), SPEC, quote(), now(clock))
    assert om.state_of(41) is State.CANCEL_PENDING
    om.on_report(rep("ORDER_CANCEL_REJECTED", order_id=11, client_order_id=COID))
    assert om.state_of(41) is State.CANCEL_PENDING
    om.on_report(rep("ORDER_FILLED", order_id=11, client_order_id=COID, position_id=7, side=Side.BUY,
                     filled_volume=200, price=2000.0))
    # filled -> OPEN, then the queued exit (kill) starts at once
    assert om.state_of(41) is State.EXIT_PENDING and b.of("close") == [("close", 7, 200)]


def test_cancel_rejected_plain_goes_open(tmp_path):
    om, b, j, clock = make(tmp_path, exec_mode=ExecMode.MAKER)
    om.handle(intent(limit=200000000, expiry=(SLOT + 122) * 1000), SPEC, quote(), now(clock))
    om.on_report(rep("ORDER_ACCEPTED", order_id=11, client_order_id=COID))
    clock.advance(125)
    om.on_report(rep("ORDER_CANCEL_REJECTED", order_id=11, client_order_id=COID))
    om.on_report(rep("ORDER_FILLED", order_id=11, client_order_id=COID, position_id=7, side=Side.BUY,
                     filled_volume=200, price=2000.0))
    assert om.state_of(41) is State.OPEN


def test_order_rejected_and_order_error(tmp_path):
    om, b, j, clock = enter_market(tmp_path)
    om.on_report(rep("ORDER_REJECTED", order_id=11, client_order_id=COID, error_code="NO_MONEY"))
    assert om.state_of(41) is State.IDLE
    kinds = [r[0] for r in j.db.execute("SELECT kind FROM risk_events")]
    assert "order_reject" in kinds
    # OrderErrorEvent (no clientOrderId): attributed to the one pending symbol
    clock.advance(300)
    om.handle(intent(), SPEC, quote(), now(clock))
    assert om.state_of(41) is State.PENDING_NEW
    om.on_report(rep("ORDER_ERROR", label=None, symbol_id=None, error_code="TRADING_BAD_VOLUME"))
    assert om.state_of(41) is State.IDLE
    tos = [r[0] for r in j.db.execute("SELECT to_state FROM transitions")]
    assert tos.count("IDLE") >= 2


def test_no_reply_reconcile_finds_order_by_client_id(tmp_path):
    om, b, j, clock = enter_market(tmp_path)
    b.recon = ([], [BrokerOrder(55, 41, Side.BUY, 200, COID, LABEL, "MARKET")])
    clock.advance(5)
    assert om.state_of(41) is State.WORKING and b.of("reconcile")
    assert len(b.of("place")) == 1          # never resent


def test_no_reply_reconcile_finds_position_or_nothing(tmp_path):
    om, b, j, clock = enter_market(tmp_path)
    b.recon = ([BrokerPosition(9, 41, Side.BUY, 200, 2000.5, LABEL, 1)], [])
    clock.advance(5)
    assert om.state_of(41) is State.OPEN and om.position_view(41).position_id == 9
    (tmp_path / "b").mkdir()
    om2, b2, j2, clock2 = enter_market(tmp_path / "b")
    clock2.advance(5)
    assert om2.state_of(41) is State.IDLE and len(b2.of("place")) == 1
    clock2.advance(300)
    om2.handle(intent(), SPEC, quote(), now(clock2))       # a new slot is a new clientOrderId
    assert len(b2.of("place")) == 2


def test_idempotent_client_order_id(tmp_path):
    om, b, j, clock = enter_market(tmp_path)
    om.on_report(rep("ORDER_REJECTED", client_order_id=COID))
    om.handle(intent(), SPEC, quote(), now(clock))        # same slot, same id: refused
    assert len(b.of("place")) == 1 and om.state_of(41) is State.IDLE


def test_startup_overdue_position_closed_and_orders_cancelled(tmp_path):
    om, b, j, clock = make(tmp_path)
    opened = (SLOT - 600) * 1000
    b.recon = ([BrokerPosition(9, 41, Side.BUY, 200, 2000.5, LABEL, opened),
                BrokerPosition(10, 41, Side.SELL, 100, 2000.5, "someone-else", opened)],
               [BrokerOrder(77, 41, Side.BUY, 100, "x", LABEL), BrokerOrder(78, 41, Side.BUY, 100, "y", "foreign")])
    om.startup_reconcile({41: SPEC})
    assert b.of("cancel") == [("cancel", 77)]
    assert b.of("close") == [("close", 9, 200)]
    assert om.state_of(41) is State.EXIT_PENDING


def test_startup_adopts_fresh_position_and_exits_on_schedule(tmp_path):
    om, b, j, clock = make(tmp_path)
    b.recon = ([BrokerPosition(9, 41, Side.BUY, 200, 2000.5, LABEL, (SLOT + 1) * 1000)], [])
    om.startup_reconcile({41: SPEC})
    assert om.state_of(41) is State.OPEN and not b.of("close")
    clock.advance(315)
    assert b.of("close") == [("close", 9, 200)]


def test_foreign_label_ignored(tmp_path):
    om, b, j, clock = enter_market(tmp_path)
    om.on_report(rep("ORDER_FILLED", label="manual", order_id=999, position_id=1, side=Side.BUY,
                     filled_volume=200, price=1.0))
    om.on_report(rep("ORDER_FILLED", label=None, order_id=998, filled_volume=5, price=1.0))
    assert om.state_of(41) is State.PENDING_NEW


def test_netted_close_is_opposite_order(tmp_path):
    om, b, j, clock = enter_market(tmp_path, broker=StubBroker("NETTED"))
    fill_entry(om, clock)
    om.handle(intent(IntentKind.EXIT), SPEC, quote(), now(clock))
    assert not b.of("close")
    last = b.of("place")[-1][1]
    assert last["side"] is Side.SELL and last["volume"] == 200 and last["mode"] is ExecMode.TAKER
    assert last["coid"] == f"ax5m-XAUUSD-{SLOT}-X" and last["stop"] is None
    om.on_report(rep("ORDER_FILLED", client_order_id=last["coid"], position_id=7, side=Side.SELL,
                     filled_volume=200, price=2000.0, gross_profit=-20, commission=-2, swap=0, money_digits=2))
    assert om.state_of(41) is State.IDLE


def test_flip_sequencing(tmp_path):
    om, b, j, clock = enter_market(tmp_path)
    fill_entry(om, clock)
    clock.advance(300)
    om.handle(intent(IntentKind.FLIP, side=Side.SELL, sid="sig2"), SPEC, quote(ts=0), now(clock))
    assert om.state_of(41) is State.EXIT_PENDING and len(b.of("place")) == 1    # entry waits for close fill
    om.on_report(rep("ORDER_FILLED", position_id=7, side=Side.SELL, filled_volume=200, price=2000.0,
                     gross_profit=-20, commission=-2, swap=0, money_digits=2))
    places = b.of("place")
    assert len(places) == 2 and places[1][1]["side"] is Side.SELL
    assert places[1][1]["coid"] == f"ax5m-XAUUSD-{SLOT + 300}-E"
    assert om.state_of(41) is State.PENDING_NEW and trades(j)[0][0] == "flip"


def test_server_stop_closes_position(tmp_path):
    om, b, j, clock = enter_market(tmp_path)
    fill_entry(om, clock)
    om.on_report(rep("ORDER_FILLED", order_id=88, position_id=7, side=Side.SELL, filled_volume=200,
                     price=1990.0, gross_profit=-2000, commission=-2, swap=0, money_digits=2))
    assert om.state_of(41) is State.IDLE and trades(j)[0][0] == "stop"


def test_exit_rejected_retries(tmp_path):
    om, b, j, clock = enter_market(tmp_path)
    fill_entry(om, clock)
    om.handle(intent(IntentKind.EXIT), SPEC, quote(), now(clock))
    om.on_report(rep("ORDER_REJECTED", position_id=7, error_code="MARKET_CLOSED"))
    assert om.state_of(41) is State.OPEN
    clock.advance(5)
    assert len(b.of("close")) == 2 and om.state_of(41) is State.EXIT_PENDING


def test_max_positions_and_one_per_symbol(tmp_path):
    om, b, j, clock = make(tmp_path, max_positions=1)
    om.handle(intent(), SPEC, quote(), now(clock))
    om.handle(intent(sym=42), SPEC2, quote(sym=42), now(clock))
    om.handle(intent(), SPEC, quote(), now(clock))
    assert len(b.of("place")) == 1


def test_never_two_positions_property(tmp_path):
    rng = random.Random(7)
    om, b, j, clock = make(tmp_path, max_positions=2)
    pos = {}
    for step in range(300):
        clock.advance(rng.choice([1, 5, 60, 301]))
        sym, spec = rng.choice([(41, SPEC), (42, SPEC2)])
        k = rng.choice([IntentKind.ENTER, IntentKind.ENTER, IntentKind.EXIT, IntentKind.FLIP, IntentKind.HOLD])
        om.handle(intent(k, side=rng.choice(list(Side)), sym=sym), spec, quote(sym=sym), now(clock))
        # randomly answer the newest broker call
        for c in list(b.calls)[-2:]:
            r = rng.random()
            if c[0] == "place" and r < 0.7:
                d = c[1]
                om.on_report(rep("ORDER_FILLED", symbol_id=d["spec"].symbol_id, client_order_id=d["coid"],
                                 order_id=step + 1000, position_id=step + 5000 if "-E" in d["coid"] else 1,
                                 side=d["side"], filled_volume=d["volume"], price=1.0,
                                 gross_profit=0 if "-X" in d["coid"] else None, commission=0, swap=0))
            elif c[0] == "close" and r < 0.7:
                for s in (41, 42):
                    pv = om.position_view(s)
                    if pv and pv.position_id == c[1]:
                        om.on_report(rep("ORDER_FILLED", symbol_id=s, position_id=c[1], side=Side.SELL,
                                         filled_volume=c[2], price=1.0, gross_profit=0, commission=0, swap=0))
        assert om.open_count() <= 2
        for s in (41, 42):
            pv = om.position_view(s)
            assert pv is None or pv.volume > 0
    # ids sent are unique
    ids = [c[1]["coid"] for c in b.of("place")]
    assert len(ids) == len(set(ids))


def test_demo_mode_requires_demo_broker(tmp_path):
    from autotrade.simbroker import SimBroker
    om, b, j, clock = make(tmp_path)
    with pytest.raises(ValueError):
        OrderManager(StubBroker(), j, clock, om.settings, RunMode.DRY_RUN, ExecMode.TAKER)
    with pytest.raises(ValueError):
        OrderManager(SimBroker(clock), j, clock, om.settings, RunMode.DEMO, ExecMode.TAKER)


# --- volume / price normalisation ---

def test_volume_rounding_and_clamping():
    s = SymbolSpec("X", 1, 2, 1, 100, 100, 50, 1000, 10, "POINTS", [], "UTC", [])
    assert normalise_volume(s, 1.0, 2) == 200
    assert normalise_volume(s, 0.5, 2) == 100
    assert normalise_volume(s, 0.7, 2) == 100            # 140 -> down to step 100? 140//50*50 = 100
    assert normalise_volume(s, 0.8, 2) == 150            # 160 -> 150
    assert normalise_volume(s, 0.5, 1) == 100            # 50 < min, mult >= 0.5 -> min_volume
    assert normalise_volume(s, 0.3, 1) == 0              # below half: no order
    assert normalise_volume(s, 1.0, 100) == 1000         # clamped to max


def test_price_and_stop_rounding():
    assert round_price(SPEC, 200012345) == 200012000
    pct = SymbolSpec("X", 1, 2, 1, 100, 1, 1, 10, 100, "PERCENTAGE", [], "UTC", [])
    assert min_stop_rel(SPEC, 200000000) == 10 * 1000
    assert min_stop_rel(pct, 200000000) == 2_000_000      # 1% of price
    assert clamp_stop_rel(SPEC, 500, 200000000) == 10000   # lifted to the broker minimum
    assert clamp_stop_rel(SPEC, 50001, 200000000) == 51000  # rounded up to a tick
    assert clamp_stop_rel(SPEC, None, 200000000) is None


# --- DemoBroker / protobuf ---

class StubSession:
    def __init__(self, verified, trader=None):
        self.verified_demo, self.account_id = verified, 123
        self.trader_info = trader or {"account_type": "HEDGED", "money_digits": 2}
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
        return defer.succeed(msg)


def test_demo_broker_refuses_when_not_verified():
    s = StubSession(False)
    br = DemoBroker(s)
    with pytest.raises(RefusedError):
        br.place_entry(SPEC, Side.BUY, 100, ExecMode.TAKER, None, None, None, "c", LABEL, "x")
    for call in (lambda: br.cancel(1), lambda: br.close(1, 100), br.reconcile):
        with pytest.raises(RefusedError):
            call()
    assert s.sent == []


def test_demo_broker_messages():
    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as mm
    s = StubSession(True, {"account_type": "NETTED", "money_digits": 2})
    br = DemoBroker(s)
    assert br.account_type == "NETTED" and br.money_digits == 2
    br.place_entry(SPEC, Side.SELL, 200, ExecMode.MAKER, 200012345, 1_800_000_120_000, 50000, "cid", LABEL, "sg")
    br.place_entry(SPEC, Side.BUY, 200, ExecMode.TAKER, None, None, None, "cid2", LABEL, "sg")
    br.cancel(5)
    br.close(6, 200)
    m, t, c, cl = s.sent[0], s.sent[1], s.sent[2], s.sent[3]
    assert (m.ctidTraderAccountId, m.symbolId, m.volume, m.clientOrderId, m.label) == (123, 41, 200, "cid", LABEL)
    assert m.orderType == mm.LIMIT and m.timeInForce == mm.GOOD_TILL_DATE and m.tradeSide == mm.SELL
    assert m.limitPrice == 2000.12 and m.expirationTimestamp == 1_800_000_120_000 and m.relativeStopLoss == 50000
    assert t.orderType == mm.MARKET and not t.HasField("relativeStopLoss") and not t.HasField("limitPrice")
    assert c.orderId == 5 and (cl.positionId, cl.volume) == (6, 200)
    with pytest.raises(RefusedError):
        br.place_entry(SPEC, Side.BUY, 200, ExecMode.MAKER, None, None, None, "c3", LABEL, "")


def test_to_exec_report_normaliser():
    from ctrader_open_api.messages import OpenApiMessages_pb2 as m
    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as mm
    ev = m.ProtoOAExecutionEvent()
    ev.ctidTraderAccountId = 123
    ev.executionType = mm.ORDER_FILLED
    ev.order.orderId = 11
    ev.order.clientOrderId = "cid"
    ev.order.orderStatus = mm.ORDER_STATUS_FILLED
    ev.order.tradeData.symbolId = 41
    ev.order.tradeData.volume = 200
    ev.order.tradeData.tradeSide = mm.SELL
    ev.order.tradeData.label = LABEL
    ev.position.positionId = 7
    ev.deal.dealId = 1
    ev.deal.orderId = 11
    ev.deal.positionId = 7
    ev.deal.volume = 200
    ev.deal.filledVolume = 200
    ev.deal.symbolId = 41
    ev.deal.executionPrice = 2000.5
    ev.deal.executionTimestamp = 1234
    ev.deal.tradeSide = mm.SELL
    ev.deal.dealStatus = mm.FILLED
    ev.deal.commission = -3
    cpd = ev.deal.closePositionDetail
    cpd.entryPrice, cpd.grossProfit, cpd.swap, cpd.commission, cpd.moneyDigits = 2000.0, 150, 1, -3, 2
    r = to_exec_report(ev, 2)
    assert (r.execution_type, r.order_id, r.position_id, r.client_order_id, r.label) == \
        ("ORDER_FILLED", 11, 7, "cid", LABEL)
    assert (r.symbol_id, r.side, r.filled_volume, r.price, r.status) == \
        (41, Side.SELL, 200, 2000.5, "ORDER_STATUS_FILLED")
    assert (r.gross_profit, r.commission, r.swap, r.money_digits, r.ts_ms) == (150, -3, 1, 2, 1234)
    ev2 = m.ProtoOAExecutionEvent()
    ev2.executionType = mm.ORDER_ACCEPTED
    ev2.order.orderId = 5
    ev2.order.tradeData.symbolId = 41
    ev2.order.tradeData.tradeSide = mm.BUY
    ev2.order.tradeData.volume = 100
    r2 = to_exec_report(ev2, 2)
    assert r2.gross_profit is None and r2.label is None and r2.filled_volume == 0 and r2.money_digits == 2
