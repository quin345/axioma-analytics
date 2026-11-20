"""Two bot instances (ax5m on 5-minute bars, ax1m on 1-minute bars) on ONE fake cTrader account.

The account broadcasts every execution event to every connected bot, as the Open API does for all
sessions authorised on an account. Each instance must open its own positions (HEDGED), recognise
only events/positions carrying its own label, reconcile/adopt/flatten only those, and never touch
the other's; positions without a label are left alone unless the default instance claims them.
Also: order labels/comments/clientOrderIds per instance, separate state dirs, the NETTED-account guard.
"""
import json

import pytest
from twisted.internet import defer

from autotrade import config, instance
from autotrade.clock import TestClock
from autotrade.config import ConfigError, Settings
from autotrade.execution import BrokerPosition, DemoBroker, OrderManager, State
from autotrade.journal import Journal
from autotrade.types import ExecMode, ExecReport, IntentKind, RunMode, Side
from helpers_exec import SLOT, SPEC, SPEC2, intent, quote

S5 = Settings()                                             # the default instance: ax5m, 5m
S1 = Settings(instance_name="ax1m", instance_timeframe="1m", state_dir="state/ax1m", log_dir="logs/ax1m")


class FakeAccount:
    """A demo account: positions keep the label of the order that opened them (ProtoOAPosition.tradeData.label)."""

    def __init__(self, clock, account_type="HEDGED"):
        self.clock, self.account_type = clock, account_type
        self.positions: dict[int, BrokerPosition] = {}
        self.listeners: list = []
        self.sent: list = []
        self._id = 100

    def add_position(self, symbol_id, label, side=Side.BUY, volume=200, open_ms=1_000):
        self._id += 1
        self.positions[self._id] = BrokerPosition(self._id, symbol_id, side, volume, 2000.0, label, open_ms)
        return self._id

    def connect(self, om):
        self.listeners.append(om.on_report)

    def _broadcast(self, reports):
        def go():
            for r in reports:
                for cb in list(self.listeners):
                    cb(r)
        self.clock.call_later(0.01, go)

    def by_label(self):
        return sorted((p.label or "", p.symbol_id) for p in self.positions.values())


class AccountBroker:
    """One bot's connection to the FakeAccount (what DemoBroker is to the real one)."""
    name = "demo"
    money_digits = 2

    def __init__(self, acct, who):
        self.acct, self.who = acct, who

    @property
    def account_type(self):
        return self.acct.account_type

    def place_entry(self, spec, side, volume, mode, limit_price, expiry_ms, stop_rel, coid, label, comment):
        a = self.acct
        a.sent.append(("new", self.who, dict(symbol=spec.symbol_id, side=side, volume=volume, coid=coid,
                                             label=label, comment=comment, stop=stop_rel)))
        a._id += 1
        oid = a._id
        pid = a.add_position(spec.symbol_id, label, side, volume, a.clock.now_ms())
        base = dict(order_id=oid, position_id=pid, client_order_id=coid, label=label, symbol_id=spec.symbol_id,
                    side=side, status=None, error_code=None, gross_profit=None, commission=-10, swap=None,
                    money_digits=2, ts_ms=0)
        a._broadcast([ExecReport("ORDER_ACCEPTED", filled_volume=0, price=None, **base),
                      ExecReport("ORDER_FILLED", filled_volume=volume, price=2000.0, **base)])
        return defer.succeed(None)

    def close(self, position_id, volume):
        a = self.acct
        a.sent.append(("close", self.who, position_id))
        p = a.positions.pop(position_id)
        a._id += 1
        a._broadcast([ExecReport("ORDER_FILLED", a._id, position_id, None, p.label, p.symbol_id,
                                 Side.SELL if p.side is Side.BUY else Side.BUY, volume, 2000.5, None, None,
                                 100, -10, 0, 2, 0)])
        return defer.succeed(None)

    def cancel(self, order_id):
        self.acct.sent.append(("cancel", self.who, order_id))
        return defer.succeed(None)

    def reconcile(self):
        return defer.succeed((list(self.acct.positions.values()), []))


def bot(tmp_path, acct, settings, clock, name):
    j = Journal(tmp_path / f"{name}.db", tmp_path / f"{name}.jsonl")
    j.start_run(RunMode.DEMO, settings)
    om = OrderManager(AccountBroker(acct, name), j, clock, settings, RunMode.DEMO, ExecMode.TAKER)
    acct.connect(om)
    return om


@pytest.fixture
def shared(tmp_path):
    clock = TestClock((SLOT + 2) * 1000)       # SLOT is a 5-minute (so also a 1-minute) boundary
    acct = FakeAccount(clock)
    return clock, acct, bot(tmp_path, acct, S5, clock, "ax5m"), bot(tmp_path, acct, S1, clock, "ax1m")


def _with_z(i, z):
    import dataclasses
    return dataclasses.replace(i, z=z)


def enter(om, clock, spec=SPEC, side=Side.BUY):
    i = _with_z(intent(IntentKind.ENTER, side, sym=spec.symbol_id, sid=f"{spec.symbol_id}-{SLOT}"), 1.5)
    om.handle(i, spec, quote(sym=spec.symbol_id), clock.now_ms())


def test_both_instances_trade_the_same_symbol_independently(shared):
    clock, acct, om5, om1 = shared
    enter(om5, clock)
    enter(om1, clock)
    assert om5.state_of(41) is State.PENDING_NEW and om1.state_of(41) is State.PENDING_NEW
    clock.advance(0.1)                          # every fill is broadcast to both bots
    assert om5.state_of(41) is State.OPEN and om1.state_of(41) is State.OPEN
    assert acct.by_label() == [("ax1m", 41), ("ax5m", 41)]          # HEDGED: two positions, one per bot
    lab = {p.position_id: p.label for p in acct.positions.values()}
    assert lab[om5.position_view(41).position_id] == "ax5m" and lab[om1.position_view(41).position_id] == "ax1m"
    new = {who: d for kind, who, d in acct.sent if kind == "new"}
    assert new["ax5m"]["coid"] == f"ax5m-XAUUSD-{SLOT}-E" and new["ax1m"]["coid"] == f"ax1m-XAUUSD-{SLOT}-E"
    assert new["ax5m"]["comment"] == f"ax5m poc_reversion_4h z=+1.50 sig=41-{SLOT}"
    assert new["ax1m"]["comment"] == f"ax1m poc_reversion_1h z=+1.50 sig=41-{SLOT}"
    assert new["ax5m"]["label"] == "ax5m" and new["ax1m"]["label"] == "ax1m"
    # exit times follow each instance's slot: next 5-minute vs next 1-minute close + grace
    assert om5.position_view(41).exit_at_ms == (SLOT + 300 + 2) * 1000
    assert om1.position_view(41).exit_at_ms == (SLOT + 60 + 2) * 1000


def test_other_instances_events_never_move_a_pending_order(shared):
    clock, acct, om5, om1 = shared
    om5.handle(_with_z(intent(IntentKind.ENTER, Side.BUY, sid="x"), 1.2), SPEC, quote(), clock.now_ms())
    acct.listeners.remove(om5.on_report)        # om5's own reports are delayed (not delivered yet)
    enter(om1, clock)
    clock.advance(0.1)
    assert om1.state_of(41) is State.OPEN and om5.state_of(41) is State.PENDING_NEW
    assert om5.position_view(41) is None


def test_flatten_kill_and_shutdown_of_one_never_touch_the_other(shared):
    clock, acct, om5, om1 = shared
    enter(om5, clock)
    enter(om1, clock)
    enter(om5, clock, SPEC2)
    enter(om1, clock, SPEC2)
    clock.advance(0.1)
    assert len(acct.positions) == 4
    for reason in ("kill", "shutdown", "manual", "risk"):
        om1.flatten_all(reason)
    clock.advance(0.1)
    assert acct.by_label() == [("ax5m", 41), ("ax5m", 42)]
    assert all(who == "ax1m" for kind, who, _ in acct.sent if kind == "close")
    assert om1.open_count() == 0 and om5.state_of(41) is State.OPEN and om5.state_of(42) is State.OPEN
    om5.flatten_all("kill")
    clock.advance(0.1)
    assert acct.positions == {}


def test_restart_reconcile_adopts_only_own_labels(tmp_path):
    clock = TestClock((SLOT + 2) * 1000)
    acct = FakeAccount(clock)
    p5 = acct.add_position(41, "ax5m", open_ms=(SLOT + 1) * 1000)
    p_legacy = acct.add_position(42, "axq-poc4h-v1", open_ms=(SLOT + 1) * 1000)   # opened before instances
    p1 = acct.add_position(41, "ax1m", open_ms=(SLOT + 1) * 1000)
    p_none = acct.add_position(43, None)                                         # manual, no label
    p_other = acct.add_position(41, "manual-hedge")
    om5, om1 = bot(tmp_path, acct, S5, clock, "a5"), bot(tmp_path, acct, S1, clock, "a1")
    om5.startup_reconcile({41: SPEC, 42: SPEC2})
    om1.startup_reconcile({41: SPEC, 42: SPEC2})
    assert om5.position_view(41).position_id == p5 and om5.position_view(42).position_id == p_legacy
    assert om1.position_view(41).position_id == p1 and om1.position_view(42) is None
    assert {p.position_id for p in om1.foreign[0]} == {p5, p_legacy, p_none, p_other}
    assert {p.position_id for p in om5.foreign[0]} == {p1, p_none, p_other}
    assert not [s for s in acct.sent if s[0] == "close"]          # nothing past its exit time: nothing closed
    # a flatten of either closes exactly its own
    om1.flatten_all("manual")
    clock.advance(0.1)
    assert [s[2] for s in acct.sent if s[0] == "close"] == [p1]
    om5.flatten_all("manual")
    clock.advance(0.1)
    assert sorted(acct.positions) == sorted([p_none, p_other])    # unlabelled / foreign left alone


def test_unlabelled_positions_go_only_to_the_default_instance_when_claimed(tmp_path):
    clock = TestClock((SLOT + 2) * 1000)
    acct = FakeAccount(clock)
    p_none = acct.add_position(41, None, open_ms=1_000)           # long past its exit time
    claim5 = Settings(instance_claim_unlabelled=True)
    om1 = bot(tmp_path, acct, S1, clock, "a1")
    om1.startup_reconcile({41: SPEC})
    assert om1.position_view(41) is None and p_none in acct.positions
    om5 = bot(tmp_path, acct, claim5, clock, "a5")
    om5.startup_reconcile({41: SPEC})
    clock.advance(0.1)
    assert [s[1:] for s in acct.sent if s[0] == "close"] == [("a5", p_none)]   # adopted, past exit_at: closed
    with pytest.raises(ConfigError, match="default instance only"):
        config.validate(Settings(instance_name="ax1m", instance_timeframe="1m", state_dir="s1", log_dir="l1",
                                 instance_claim_unlabelled=True))


# ---- what goes over the wire ----

class StubSession:
    def __init__(self, account_type="HEDGED"):
        self.verified_demo, self.account_id = True, 123
        self.trader_info = {"account_type": account_type, "money_digits": 2}
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
        return defer.succeed(None)


@pytest.mark.parametrize("settings,key", [(S5, "poc_reversion_4h"), (S1, "poc_reversion_1h")])
@pytest.mark.parametrize("account_type", ["HEDGED", "NETTED"])
def test_every_outgoing_order_carries_the_instance(tmp_path, settings, key, account_type):
    from ctrader_open_api.messages import OpenApiMessages_pb2 as oa
    clock = TestClock((SLOT + 2) * 1000)
    s = StubSession(account_type)
    j = Journal(tmp_path / "j.db", tmp_path / "j.jsonl")
    j.start_run(RunMode.DEMO, settings)
    om = OrderManager(DemoBroker(s), j, clock, settings, RunMode.DEMO, ExecMode.TAKER)
    name = settings.instance_name
    om.handle(_with_z(intent(IntentKind.ENTER, Side.SELL, sid=f"41-{SLOT}"), -1.234), SPEC, quote(), clock.now_ms())
    entry = s.sent[0]
    assert isinstance(entry, oa.ProtoOANewOrderReq)
    assert entry.label == name and entry.clientOrderId == f"{name}-XAUUSD-{SLOT}-E"
    assert entry.comment == f"{name} {key} z=-1.23 sig=41-{SLOT}" and len(entry.comment) <= 512
    assert entry.relativeStopLoss > 0                  # the protective stop rides on the labelled entry order
    coid = entry.clientOrderId
    from helpers_exec import rep
    om.on_report(rep("ORDER_ACCEPTED", order_id=11, client_order_id=coid, label=name))
    om.on_report(rep("ORDER_FILLED", order_id=11, client_order_id=coid, position_id=7, side=Side.SELL,
                     filled_volume=200, price=2000.0, label=name))
    om.flatten_all("kill")
    exit_ = s.sent[-1]
    if account_type == "NETTED":                       # a closing market order: labelled too
        assert isinstance(exit_, oa.ProtoOANewOrderReq) and exit_.label == name
        assert exit_.clientOrderId == f"{name}-XAUUSD-{SLOT}-X" and exit_.comment == f"{name} exit sig=41-{SLOT}"
    else:                                              # ClosePositionReq has no label field: it names the
        assert isinstance(exit_, oa.ProtoOAClosePositionReq) and exit_.positionId == 7   # own position
        assert "label" not in exit_.DESCRIPTOR.fields_by_name
    assert {r[0] for r in j.db.execute("SELECT label FROM orders")} == {name}


def test_protobuf_has_the_fields_the_labels_rely_on():
    from ctrader_open_api.messages import OpenApiMessages_pb2 as oa
    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as mm
    f = oa.ProtoOANewOrderReq.DESCRIPTOR.fields_by_name
    assert {"label", "comment", "clientOrderId", "relativeStopLoss"} <= set(f)
    assert {"label", "comment"} <= set(mm.ProtoOATradeData.DESCRIPTOR.fields_by_name)   # on positions and orders
    assert "label" not in mm.ProtoOADeal.DESCRIPTOR.fields_by_name     # deals: attributed via positionId
    for msg in (oa.ProtoOAClosePositionReq, oa.ProtoOAAmendPositionSLTPReq, oa.ProtoOACancelOrderReq):
        assert "label" not in msg.DESCRIPTOR.fields_by_name and "positionId" in msg.DESCRIPTOR.fields_by_name \
            or "orderId" in msg.DESCRIPTOR.fields_by_name


# ---- configuration and state separation ----

def test_shipped_configs_are_two_separate_instances(make_env):
    s5 = config.load(make_env(), config.ROOT / "config.toml")
    s1 = config.load(make_env(), config.ROOT / "config.1m.toml")
    assert (s5.instance_name, s5.instance_timeframe, s5.state_path(), s5.log_path()) == \
        ("ax5m", "5m", config.ROOT / "state", config.ROOT / "logs")
    assert (s1.instance_name, s1.instance_timeframe, s1.state_path(), s1.log_path()) == \
        ("ax1m", "1m", config.ROOT / "state" / "ax1m", config.ROOT / "logs" / "ax1m")
    assert s5.own_labels == {"ax5m", "axq-poc4h-v1"} and s1.own_labels == {"ax1m"}
    assert not s1.owns_label(None) and not s5.owns_label(None)
    assert s1.data_env_path() == s5.data_env_path()


@pytest.mark.parametrize("toml,match", [
    ('[instance]\nname = "ax1m"\ntimeframe = "1m"\n[strategy]\nlimit_ttl_s = 45\n', "own state_dir"),
    ('state_dir = "s"\nlog_dir = "l"\n[instance]\nname = "ax1m"\nlegacy_labels = ["axq-poc4h-v1"]\n', "belong to"),
    ('state_dir = "s"\nlog_dir = "l"\n[instance]\nname = "ax1m"\nlegacy_labels = ["ax5m"]\n', "belong to"),
    ('label = "axq-poc4h-v1"\n', "replaced by \\[instance\\]"),
    ('[instance]\ntimeframe = "15m"\n', "unknown timeframe"),
    ('[instance]\nname = "has space"\n', "instance.name"),
    ('state_dir = "s"\nlog_dir = "l"\n[instance]\nname = "ax1m"\ntimeframe = "1m"\n', "limit_ttl_s"),
    ('state_dir = "s"\nlog_dir = "l"\n[instance]\nname = "ax1m"\ntimeframe = "1m"\n[strategy]\nlimit_ttl_s = 45\n'
     '[data]\nfreshness_s = 30\n', "freshness_s"),
])
def test_instance_config_errors(make_env, make_toml, toml, match):
    with pytest.raises(ConfigError, match=match):
        config.load(make_env(), make_toml(toml))


def test_state_dir_marker_and_process_lock(tmp_path):
    import dataclasses
    import fcntl
    a = dataclasses.replace(S1, state_dir=str(tmp_path / "st"), log_dir=str(tmp_path / "lg"))
    instance.claim(a)
    assert json.loads((tmp_path / "st" / "INSTANCE.json").read_text()) == {"name": "ax1m", "timeframe": "1m"}
    instance.claim(a)                                   # same process: idempotent
    other = dataclasses.replace(S5, state_dir=str(tmp_path / "st"), log_dir=str(tmp_path / "lg"))
    with pytest.raises(ConfigError, match="belongs to instance 'ax1m'"):
        instance.check(other)
    instance.release(a)
    with open(tmp_path / "st" / "instance.lock", "a") as fh:   # another process holds the lock
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ConfigError, match="already running"):
            instance.claim(a)
    instance.claim(a)
    instance.release(a)
