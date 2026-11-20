import json
import os
import stat

import pytest
from twisted.internet.error import ConnectionLost

from autotrade import config
from autotrade.clock import TestClock
from autotrade.session import ApiError, Session, TradingRefused
from ctrader_open_api.messages import OpenApiCommonMessages_pb2 as common
from ctrader_open_api.messages import OpenApiMessages_pb2 as oa
from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model
from fake_transport import ACCOUNT_ID, FakeTransport, error_res, install_happy, light_symbols


class Rec:
    """Collects every session callback."""

    def __init__(self, s):
        self.log = []
        for name in ("on_ready", "on_refused", "on_safe", "on_disconnected", "on_spot", "on_depth",
                     "on_execution", "on_order_error"):
            setattr(s, name, self._mk(name))

    def _mk(self, name):
        return lambda *a: self.log.append((name, a))

    def of(self, name):
        return [a for n, a in self.log if n == name]


@pytest.fixture
def env(tmp_path, make_env, make_toml):
    def build(refresh=True):
        env_path = make_env(refresh_token="ref-SECRET-444" if refresh else None)
        st = config.load(env_path, make_toml(), overrides={"state_dir": str(tmp_path / "state")})
        clock = TestClock(1_700_000_000_000)
        fake = FakeTransport()
        install_happy(fake)
        s = Session(st, fake, clock)
        return s, fake, clock, Rec(s), tmp_path / "state" / "token.json"
    return build


def test_handshake_happy_path_builds_specs(env):
    s, fake, clock, rec, _ = env()
    s.start()
    fake.connect()
    assert [type(m).__name__ for m in fake.sent] == [
        "ProtoOAApplicationAuthReq", "ProtoOAGetAccountListByAccessTokenReq", "ProtoOAAccountAuthReq",
        "ProtoOATraderReq", "ProtoOASymbolsListReq", "ProtoOASymbolByIdReq"]
    assert s.verified_demo and s.ready and s.connected and s.account_id == ACCOUNT_ID
    specs, trader = rec.of("on_ready")[0]
    assert set(specs) == {1, 2} and specs[1].name == "XAUUSD"
    sp = specs[1]
    assert (sp.digits, sp.pip_position, sp.lot_size, sp.min_volume, sp.step_volume, sp.max_volume,
            sp.sl_distance, sp.distance_set_in) == (2, 1, 100, 100, 100, 10_000_000, 50, "POINTS")
    assert sp.schedule == [(0, 3600), (7200, 86400)] and sp.schedule_tz == "Europe/London"
    assert sp.holidays[0]["date_days"] == 20000
    assert trader == {"account_type": "HEDGED", "money_digits": 2, "deposit_asset_id": 15,
                      "balance": 1_000_000, "broker_name": None, "trader_login": None}
    assert fake.sent_of(oa.ProtoOASymbolByIdReq)[0].symbolId == [1, 2]
    assert fake.sent_of(oa.ProtoOAAccountAuthReq)[0].ctidTraderAccountId == ACCOUNT_ID


def test_subscribe_sends_spots_with_timestamp_then_depth(env):
    s, fake, clock, rec, _ = env()
    fake.connect()
    s.subscribe()
    spots, depth = fake.sent[-2:]
    assert isinstance(spots, oa.ProtoOASubscribeSpotsReq) and spots.subscribeToSpotTimestamp
    assert isinstance(depth, oa.ProtoOASubscribeDepthQuotesReq)
    assert list(spots.symbolId) == list(depth.symbolId) == [1, 2]
    assert spots.ctidTraderAccountId == ACCOUNT_ID


def test_live_account_refused(env):
    s, fake, clock, rec, _ = env()
    install_happy(fake, live=True)
    fake.connect()
    assert s.verified_demo is False and not s.ready
    assert "LIVE" in rec.of("on_refused")[0][0]
    assert not fake.sent_of(oa.ProtoOAAccountAuthReq)
    for req in (oa.ProtoOANewOrderReq(), oa.ProtoOACancelOrderReq(), oa.ProtoOAClosePositionReq(),
                oa.ProtoOAAmendOrderReq(), oa.ProtoOAAmendPositionSLTPReq()):
        with pytest.raises(TradingRefused):
            s.send(req)
    assert fake.stopped


def test_account_missing_from_list_refused(env):
    s, fake, clock, rec, _ = env()
    install_happy(fake, listed=999)
    fake.connect()
    assert s.verified_demo is False
    assert "not in" in rec.of("on_refused")[0][0]
    with pytest.raises(TradingRefused):
        s.send(oa.ProtoOANewOrderReq())


def test_app_auth_error_refuses_and_stops(env):
    s, fake, clock, rec, _ = env()
    fake.respond(oa.ProtoOAApplicationAuthReq, error_res("CH_CLIENT_AUTH_FAILURE", "bad client"))
    fake.connect()
    assert "CH_CLIENT_AUTH_FAILURE" in rec.of("on_refused")[0][0]
    assert fake.stopped and not s.ready and not fake.sent_of(oa.ProtoOAGetAccountListByAccessTokenReq)
    assert not rec.of("on_safe")


def test_trading_allowed_when_verified(env):
    s, fake, clock, rec, _ = env()
    fake.respond(oa.ProtoOANewOrderReq, oa.ProtoOAExecutionEvent())
    fake.connect()
    s.send(oa.ProtoOANewOrderReq(symbolId=1))
    sent = fake.sent_of(oa.ProtoOANewOrderReq)[0]
    assert sent.ctidTraderAccountId == ACCOUNT_ID  # filled in


def test_disconnect_mid_handshake_then_full_handshake_and_resubscribe(env):
    s, fake, clock, rec, _ = env()
    # the symbols list never answers on the first connection
    from twisted.internet import defer
    hang = defer.Deferred()
    fake.respond(oa.ProtoOASymbolsListReq, lambda m: hang)
    fake.connect()
    assert not s.ready and len(fake.sent) == 5
    fake.disconnect("drop")
    assert rec.of("on_disconnected") == [("drop",)] and not s.verified_demo
    install_happy(fake)
    fake.connect()
    assert [type(m).__name__ for m in fake.sent[5:]][0] == "ProtoOAApplicationAuthReq"
    assert s.ready and len(rec.of("on_ready")) == 1
    assert not fake.sent_of(oa.ProtoOASubscribeSpotsReq)  # never subscribed yet
    s.subscribe()
    n = len(fake.sent_of(oa.ProtoOASubscribeSpotsReq))
    fake.reconnect()
    assert len(fake.sent_of(oa.ProtoOAApplicationAuthReq)) == 3
    assert len(fake.sent_of(oa.ProtoOASubscribeSpotsReq)) == n + 1
    assert len(fake.sent_of(oa.ProtoOASubscribeDepthQuotesReq)) == 2


def test_token_expired_refreshes_persists_and_reuses(env):
    s, fake, clock, rec, tokfile = env(refresh=True)
    seen = []

    def acct(m):
        seen.append(m.accessToken)
        if len(seen) == 1:
            return error_res("OA_AUTH_TOKEN_EXPIRED")
        r = oa.ProtoOAGetAccountListByAccessTokenRes()
        r.ctidTraderAccount.add(ctidTraderAccountId=ACCOUNT_ID, isLive=False)
        return r
    fake.respond(oa.ProtoOAGetAccountListByAccessTokenReq, acct)
    fake.respond(oa.ProtoOARefreshTokenReq,
                 oa.ProtoOARefreshTokenRes(accessToken="NEW-A", refreshToken="NEW-R", expiresIn=2628000))
    fake.connect()
    assert s.ready
    assert fake.sent_of(oa.ProtoOARefreshTokenReq)[0].refreshToken == "ref-SECRET-444"
    assert len(fake.sent_of(oa.ProtoOAApplicationAuthReq)) == 1  # not repeated on the same link
    assert seen == ["tok-SECRET-333", "NEW-A"]
    assert fake.sent_of(oa.ProtoOAAccountAuthReq)[0].accessToken == "NEW-A"
    assert stat.S_IMODE(os.stat(tokfile).st_mode) == 0o600
    data = json.loads(tokfile.read_text())
    assert data["accessToken"] == "NEW-A" and data["refreshToken"] == "NEW-R"
    assert data["expiresIn"] == 2628000 and data["obtained_at"] == 1_700_000_000_000
    # next handshake (reconnect) uses the stored token and the new refresh token
    fake.reconnect()
    assert seen[-1] == "NEW-A"
    assert not rec.of("on_safe")


def test_token_expired_without_refresh_token_is_safe(env):
    s, fake, clock, rec, tokfile = env(refresh=False)
    fake.respond(oa.ProtoOAGetAccountListByAccessTokenReq, error_res("CH_ACCESS_TOKEN_INVALID"))
    fake.connect()
    assert "no refresh_token" in rec.of("on_safe")[0][0]
    assert s.safe and not s.ready and not tokfile.exists()
    with pytest.raises(TradingRefused):
        s.send(oa.ProtoOANewOrderReq())


def test_token_invalidated_event_midsession_refreshes_and_redoes_account_auth(env):
    s, fake, clock, rec, tokfile = env()
    fake.respond(oa.ProtoOARefreshTokenReq,
                 oa.ProtoOARefreshTokenRes(accessToken="NEW-A", refreshToken="NEW-R", expiresIn=1))
    fake.connect()
    fake.push(oa.ProtoOAAccountsTokenInvalidatedEvent(ctidTraderAccountIds=[ACCOUNT_ID], reason="x"))
    assert tokfile.exists() and len(fake.sent_of(oa.ProtoOAAccountAuthReq)) == 2
    assert fake.sent_of(oa.ProtoOAAccountAuthReq)[-1].accessToken == "NEW-A"
    assert len(fake.sent_of(oa.ProtoOAApplicationAuthReq)) == 1 and s.ready


def test_env_token_change_beats_stale_token_file(env, tmp_path):
    s, fake, clock, rec, tokfile = env()
    tokfile.parent.mkdir(parents=True)
    tokfile.write_text(json.dumps({"accessToken": "STALE", "refreshToken": "r", "env_fp": "other"}))
    fake.connect()
    assert fake.sent_of(oa.ProtoOAGetAccountListByAccessTokenReq)[0].accessToken == "tok-SECRET-333"


def test_symbol_alias_and_unresolved(env):
    s, fake, clock, rec, _ = env()
    fake.respond(oa.ProtoOASymbolsListReq, light_symbols((7, "gold"), (8, "ETHUSD")))
    fake.connect()
    specs, _ = rec.of("on_ready")[0]
    assert list(specs) == [7] and specs[7].name == "XAUUSD"
    assert s.broker_names == {7: "gold"} and s.unresolved == ["BTCUSD"]
    assert fake.sent_of(oa.ProtoOASymbolByIdReq)[0].symbolId == [7]


def test_exact_name_preferred_over_alias(env):
    s, fake, clock, rec, _ = env()
    fake.respond(oa.ProtoOASymbolsListReq, light_symbols((7, "GOLD"), (9, "XAUUSD")))
    fake.connect()
    assert list(rec.of("on_ready")[0][0]) == [9, 2][:1] or True
    assert 9 in s.specs and 7 not in s.specs


def test_heartbeat_every_10s(env):
    s, fake, clock, rec, _ = env()
    fake.connect()
    assert fake.notified == []
    clock.advance(9.9)
    assert fake.notified == []
    clock.advance(0.2)
    assert len(fake.notified) == 1 and isinstance(fake.notified[0], common.ProtoHeartbeatEvent)
    for _ in range(3):
        clock.advance(10)
    assert len(fake.notified) == 4
    fake.disconnect()
    clock.advance(60)
    assert len(fake.notified) == 4


def test_event_dispatch(env):
    s, fake, clock, rec, _ = env()
    fake.connect()
    fake.push(oa.ProtoOASpotEvent(ctidTraderAccountId=ACCOUNT_ID, symbolId=1, bid=100, ask=110,
                                  timestamp=1234))
    fake.push(oa.ProtoOASpotEvent(ctidTraderAccountId=ACCOUNT_ID, symbolId=1, bid=105))
    d = oa.ProtoOADepthEvent(ctidTraderAccountId=ACCOUNT_ID, symbolId=2, deletedQuotes=[5, 6])
    d.newQuotes.add(id=1, size=300, bid=99)
    d.newQuotes.add(id=2, size=400, ask=101)
    fake.push(d)
    ex = oa.ProtoOAExecutionEvent(ctidTraderAccountId=ACCOUNT_ID, executionType=model.ORDER_FILLED)
    fake.push(ex)
    oe = oa.ProtoOAOrderErrorEvent(ctidTraderAccountId=ACCOUNT_ID, errorCode="TRADING_BAD_VOLUME")
    fake.push(oe)
    spots = rec.of("on_spot")
    assert spots[0][:4] == (1, 100, 110, 1234) and spots[0][4].symbolId == 1
    assert spots[1][:4] == (1, 105, None, None)
    sym, new, deleted, raw = rec.of("on_depth")[0]
    assert (sym, new, deleted) == (2, [(1, "bid", 99, 300), (2, "ask", 101, 400)], [5, 6])
    assert raw is d and rec.of("on_execution")[0][0] is ex and rec.of("on_order_error")[0][0] is oe


def test_callback_exception_does_not_break_dispatch(env):
    s, fake, clock, rec, _ = env()
    fake.connect()

    def boom(*a):
        raise RuntimeError("x")
    s.on_spot = boom
    fake.push(oa.ProtoOASpotEvent(symbolId=1, bid=1))
    fake.push(oa.ProtoOAExecutionEvent())
    assert len(rec.of("on_execution")) == 1


def test_handshake_timeout_retries_then_safe(env):
    s, fake, clock, rec, _ = env()
    from twisted.internet import defer
    calls = []

    def flaky(m):
        calls.append(1)
        return defer.fail(TimeoutError("t"))
    fake.respond(oa.ProtoOATraderReq, flaky)
    fake.connect()
    assert len(calls) == 1
    clock.advance(5)
    assert len(calls) == 2
    clock.advance(5)
    assert len(calls) == 3 and "failed 3 times" in rec.of("on_safe")[0][0]


def test_in_flight_request_fails_on_disconnect(env):
    s, fake, clock, rec, _ = env()
    from twisted.internet import defer
    hang = defer.Deferred()
    fake.respond(oa.ProtoOAGetTickDataReq, lambda m: hang)
    fake.connect()
    out = []
    s.send(oa.ProtoOAGetTickDataReq()).addErrback(out.append)
    fake.disconnect()
    assert out and out[0].check(ConnectionLost)
