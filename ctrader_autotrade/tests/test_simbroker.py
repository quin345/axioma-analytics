from autotrade.execution import State
from autotrade.simbroker import SimBroker
from autotrade.types import ExecMode, IntentKind, RunMode, Side

from helpers_exec import LABEL, SLOT, SPEC, intent, make, quote


def setup(tmp_path, exec_mode=ExecMode.TAKER, **kw):
    from autotrade.clock import TestClock
    from autotrade.config import Settings
    from autotrade.execution import OrderManager
    from autotrade.journal import Journal
    clock = TestClock((SLOT + 2) * 1000)
    sim = SimBroker(clock, **kw)
    j = Journal(tmp_path / "s.db", tmp_path / "s.jsonl")
    j.start_run(RunMode.DRY_RUN)
    om = OrderManager(sim, j, clock, Settings(), RunMode.DRY_RUN, exec_mode)
    sim.listener = om.on_report
    return om, sim, j, clock


def feed(om, sim, q):
    om.on_quote(q)
    sim.on_quote(q)


def test_taker_fills_at_touch_and_closes_at_touch(tmp_path):
    om, sim, j, clock = setup(tmp_path)
    q = quote(bid=200000000, ask=200010000, ts=clock.now_ms())
    feed(om, sim, q)
    om.handle(intent(), SPEC, q, clock.now_ms())
    pv = om.position_view(41)
    assert om.state_of(41) is State.OPEN and pv.entry_price == 2000.1 and pv.position_id is None
    q2 = quote(bid=200100000, ask=200110000, ts=clock.now_ms())
    feed(om, sim, q2)
    om.handle(intent(IntentKind.EXIT), SPEC, q2, clock.now_ms())
    assert om.state_of(41) is State.IDLE
    r = j.db.execute("SELECT entry_price, exit_price, pnl_bps_price, pnl_net FROM trades").fetchone()
    assert r[0] == 2000.1 and r[1] == 2001.0 and r[2] > 0 and r[3] > 0     # sell at the bid


def test_taker_sell_at_bid(tmp_path):
    om, sim, j, clock = setup(tmp_path)
    q = quote(ts=1)
    feed(om, sim, q)
    om.handle(intent(side=Side.SELL), SPEC, q, clock.now_ms())
    assert om.position_view(41).entry_price == 2000.0


def test_no_quote_rejects(tmp_path):
    om, sim, j, clock = setup(tmp_path)
    om.handle(intent(), SPEC, None, clock.now_ms())
    assert om.state_of(41) is State.IDLE


def test_maker_fills_on_ask_le_limit_with_trade_through_flag(tmp_path):
    om, sim, j, clock = setup(tmp_path, ExecMode.MAKER)
    q = quote(bid=200000000, ask=200010000, ts=1)
    feed(om, sim, q)
    om.handle(intent(limit=200000000, expiry=(SLOT + 122) * 1000), SPEC, q, clock.now_ms())
    assert om.state_of(41) is State.WORKING
    feed(om, sim, quote(bid=199990000, ask=200005000, ts=2))          # ask above L: no fill
    assert om.state_of(41) is State.WORKING
    feed(om, sim, quote(bid=199980000, ask=200000000, ts=3))          # ask == L: touch only
    assert om.state_of(41) is State.OPEN
    note = sim.fill_notes[f"ax5m-XAUUSD-{SLOT}-E"]
    assert note["trade_through"] is False
    assert om.position_view(41).entry_price == 2000.0                 # filled at L


def test_maker_trade_through_flag_true(tmp_path):
    om, sim, j, clock = setup(tmp_path, ExecMode.MAKER)
    feed(om, sim, quote(ts=1))
    om.handle(intent(limit=200000000, expiry=(SLOT + 122) * 1000), SPEC, quote(), clock.now_ms())
    feed(om, sim, quote(bid=199970000, ask=199990000, ts=2))
    assert sim.fill_notes[f"ax5m-XAUUSD-{SLOT}-E"]["trade_through"] is True


def test_maker_sell_fills_on_bid_ge_limit(tmp_path):
    om, sim, j, clock = setup(tmp_path, ExecMode.MAKER)
    feed(om, sim, quote(ts=1))
    om.handle(intent(side=Side.SELL, limit=200020000, expiry=(SLOT + 122) * 1000), SPEC, quote(), clock.now_ms())
    feed(om, sim, quote(bid=200010000, ask=200020000, ts=2))
    assert om.state_of(41) is State.WORKING
    feed(om, sim, quote(bid=200020000, ask=200030000, ts=3))
    assert om.state_of(41) is State.OPEN


def test_gtd_expiry_by_quote(tmp_path):
    om, sim, j, clock = setup(tmp_path, ExecMode.MAKER)
    feed(om, sim, quote(ts=1))
    om.handle(intent(limit=190000000, expiry=(SLOT + 122) * 1000), SPEC, quote(), clock.now_ms())
    feed(om, sim, quote(ts=(SLOT + 122) * 1000))
    assert om.state_of(41) is State.IDLE and not sim.orders


def test_gtd_expiry_by_tick(tmp_path):
    om, sim, j, clock = setup(tmp_path, ExecMode.MAKER)
    feed(om, sim, quote(ts=1))
    om.handle(intent(limit=190000000, expiry=(SLOT + 122) * 1000), SPEC, quote(), clock.now_ms())
    sim.tick((SLOT + 123) * 1000)
    assert om.state_of(41) is State.IDLE


def test_maker_partial_then_expire_keeps_filled(tmp_path):
    om, sim, j, clock = setup(tmp_path, ExecMode.MAKER, partial_fraction=0.5)
    feed(om, sim, quote(ts=1))
    om.handle(intent(limit=200000000, expiry=(SLOT + 122) * 1000), SPEC, quote(), clock.now_ms())
    feed(om, sim, quote(bid=199990000, ask=199995000, ts=2))
    assert om.state_of(41) is State.PARTIAL and om.position_view(41).volume == 100
    feed(om, sim, quote(bid=199990000, ask=199995000, ts=(SLOT + 130) * 1000))   # past expiry
    assert om.state_of(41) is State.OPEN and om.position_view(41).volume == 100


def test_server_stop_simulated(tmp_path):
    om, sim, j, clock = setup(tmp_path)
    q = quote(ts=1)
    feed(om, sim, q)
    om.handle(intent(stop=50000), SPEC, q, clock.now_ms())        # buy at 2000.1, stop 1999.6
    feed(om, sim, quote(bid=199970000, ask=199980000, ts=2))      # bid above stop
    assert om.state_of(41) is State.OPEN
    feed(om, sim, quote(bid=199950000, ask=199960000, ts=3))      # bid <= 1999.6
    assert om.state_of(41) is State.IDLE
    assert j.db.execute("SELECT reason, exit_price FROM trades").fetchone() == ("stop", 1999.5)


def test_netted_sim_opposite_order_closes(tmp_path):
    om, sim, j, clock = setup(tmp_path, account_type="NETTED")
    q = quote(ts=1)
    feed(om, sim, q)
    om.handle(intent(), SPEC, q, clock.now_ms())
    om.handle(intent(IntentKind.EXIT), SPEC, q, clock.now_ms())
    assert om.state_of(41) is State.IDLE and not sim.positions
    assert j.db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1


def test_reconcile_lists_state(tmp_path):
    om, sim, j, clock = setup(tmp_path, ExecMode.MAKER)
    feed(om, sim, quote(ts=1))
    om.handle(intent(limit=190000000, expiry=(SLOT + 122) * 1000), SPEC, quote(), clock.now_ms())
    pos, ords = sim.reconcile().result
    assert not pos and ords[0].client_order_id == f"ax5m-XAUUSD-{SLOT}-E" and ords[0].label == LABEL
