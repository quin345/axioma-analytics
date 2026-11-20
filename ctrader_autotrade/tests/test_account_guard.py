"""Start-up account guard (runner.Bot._account_guard): on a NETTED account positions of one symbol net
together, so a second instance is refused unless it is alone on its own account (instance.netted_sole);
any instance is refused when another bot's position sits on its symbols. HEDGED accounts pass, with an
exposure sanity check in the journal."""
import pytest

from autotrade.runner import EXIT_REFUSED
from autotrade.types import RunMode
from ctrader_open_api.messages import OpenApiMessages_pb2 as oa
from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model
from fake_transport import ACCOUNT_ID
from test_e2e import BASE
from test_kql_e2e import KqlHarness
from test_one_minute_e2e import OneMinuteHarness


def trader(account_type):
    tr = oa.ProtoOATraderRes(ctidTraderAccountId=ACCOUNT_ID)
    tr.trader.ctidTraderAccountId = ACCOUNT_ID
    tr.trader.balance, tr.trader.depositAssetId, tr.trader.moneyDigits = 1_000_000, 15, 2
    tr.trader.accountType = account_type
    return tr


def reconcile(*positions):
    rec = oa.ProtoOAReconcileRes()
    for pid, sid, label in positions:
        p = rec.position.add(positionId=pid, price=2000.0)
        p.tradeData.symbolId, p.tradeData.volume, p.tradeData.tradeSide = sid, 200, model.BUY
        p.tradeData.openTimestamp = BASE
        if label:
            p.tradeData.label = label
    return rec


def run(x, account_type, *positions):
    x.fake.respond(oa.ProtoOATraderReq, trader(account_type))
    x.fake.respond(oa.ProtoOAReconcileReq, reconcile(*positions))
    x.start()
    return x


@pytest.fixture
def five(tmp_path, make_env, make_toml):
    return lambda: KqlHarness(tmp_path, make_env, make_toml, mode=RunMode.DEMO)


@pytest.fixture
def one(tmp_path, make_env):
    return lambda **kw: OneMinuteHarness(tmp_path, make_env, mode=RunMode.DEMO, **kw)


def kinds(x):
    return [k for (k,) in x.q("SELECT kind FROM risk_events")]


def test_netted_account_refuses_the_second_instance(one):
    x = run(one(), model.NETTED)
    assert x.bot.done.called and x.bot.exit_code == EXIT_REFUSED
    detail = x.q("SELECT detail FROM risk_events WHERE kind='refused'")[0][0]
    assert "NETTED" in detail and "netted_sole" in detail
    assert not x.fake.sent_of(oa.ProtoOANewOrderReq) and not x.fake.sent_of(oa.ProtoOAClosePositionReq)


def test_netted_sole_lets_the_one_minute_bot_run_on_its_own_account(one):
    x = run(one(overrides={"instance_netted_sole": True}), model.NETTED)
    assert not x.bot.done.called and x.bot.trading_ready
    x.bot.shutdown("test", flatten=False)


def test_netted_default_instance_runs_alone_but_not_next_to_another_bot(five, one):
    x = run(five(), model.NETTED)
    assert not x.bot.done.called and x.bot.trading_ready          # unchanged behaviour when it is alone
    x.bot.shutdown("test", flatten=False)
    y = run(one(overrides={"instance_netted_sole": True}), model.NETTED, (9, 1, "ax5m"))
    assert y.bot.exit_code == EXIT_REFUSED                        # the 5m bot's position sits on XAUUSD
    assert "['ax5m']" in y.q("SELECT detail FROM risk_events WHERE kind='refused'")[0][0]
    assert not y.fake.sent_of(oa.ProtoOAClosePositionReq)         # never touched


def test_hedged_account_runs_both_and_checks_exposure(five, one):
    x = run(one(), model.HEDGED, (9, 1, "ax5m"), (10, 1, "axq-poc4h-v1"), (11, 2, None))
    assert not x.bot.done.called and x.bot.trading_ready
    assert x.bot.om.open_count() == 0                             # none of these is the 1-minute bot's
    assert not x.fake.sent_of(oa.ProtoOAClosePositionReq)
    assert "exposure_warning" not in kinds(x)
    x.bot.shutdown("test", flatten=False)
    y = run(five(), model.HEDGED, (9, 1, "ax5m"), (12, 2, "ax1m"))
    assert y.bot.om.position_view(1).position_id == 9 and y.bot.om.position_view(2) is None
    y.bot.shutdown("test", flatten=False)


def test_exposure_warning_when_two_instances_could_exceed_max_exposure(one):
    from test_e2e import symbol_res

    def small(m):
        res = symbol_res(list(m.symbolId))
        for s in res.symbol:
            s.maxExposure = 500                                   # 200 open + 2 x 200 more > 500
        return res
    x = one()
    x.fake.respond(oa.ProtoOASymbolByIdReq, small)
    x = run(x, model.HEDGED, (9, 1, "ax5m"))
    assert not x.bot.done.called                                  # a warning, never a block
    rows = x.q("SELECT symbol_id, detail FROM risk_events WHERE kind='exposure_warning'")
    assert [r[0] for r in rows] == [1] and "maxExposure 500" in rows[0][1]
    x.bot.shutdown("test", flatten=False)
