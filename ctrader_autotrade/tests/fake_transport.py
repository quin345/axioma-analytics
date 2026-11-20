"""In-memory Transport for tests: scripted responders, pushed events, simulated disconnects."""
from __future__ import annotations

from typing import Any, Callable

from twisted.internet import defer
from twisted.internet.error import ConnectionLost


class FakeTransport:
    """Implements autotrade.transport.Transport.

    responders: {request class -> callable(msg) -> response proto | Deferred}. A responder may
    raise (the send Deferred fails) or return a ProtoOAErrorRes (delivered as the response).
    A request type without a responder fails with KeyError so tests notice.
    """

    def __init__(self):
        self.responders: dict[type, Callable[[Any], Any]] = {}
        self.sent: list = []        # every request passed to send()
        self.notified: list = []    # fire-and-forget messages (heartbeats)
        self.connected = False
        self.started = False
        self.stopped = False
        self._connected_cbs: list = []
        self._disconnected_cbs: list = []
        self._message_cbs: list = []
        self._pending: list = []

    # --- Transport interface ---
    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True
        if self.connected:
            self.disconnect("stopped")

    def on_connected(self, cb):
        self._connected_cbs.append(cb)

    def on_disconnected(self, cb):
        self._disconnected_cbs.append(cb)

    def on_message(self, cb):
        self._message_cbs.append(cb)

    def send(self, msg, timeout=5):
        self.sent.append(msg)
        if not self.connected:
            return defer.fail(ConnectionLost("not connected"))
        fn = self.responders.get(type(msg))
        if fn is None:
            return defer.fail(KeyError(f"no responder for {type(msg).__name__}"))
        try:
            res = fn(msg)
        except Exception:
            return defer.fail()
        if isinstance(res, defer.Deferred):
            self._pending.append(res)
            return res
        return defer.succeed(res)

    def notify(self, msg):
        self.notified.append(msg)

    # --- test controls ---
    def respond(self, req_type, fn_or_value):
        self.responders[req_type] = fn_or_value if callable(fn_or_value) else (lambda m: fn_or_value)

    def connect(self):
        self.connected = True
        for cb in list(self._connected_cbs):
            cb()

    def disconnect(self, reason="lost"):
        self.connected = False
        for d in self._pending:
            if not d.called:
                d.errback(ConnectionLost("disconnected"))
        self._pending.clear()
        for cb in list(self._disconnected_cbs):
            cb(reason)

    def reconnect(self):
        """Disconnect (if connected) then connect again, as the backoff retry would."""
        if self.connected:
            self.disconnect()
        self.connect()

    def push(self, msg):
        for cb in list(self._message_cbs):
            cb(msg)

    def sent_of(self, req_type) -> list:
        return [m for m in self.sent if type(m) is req_type]


# ---- realistic proto builders shared by the session/warmup tests ----
from ctrader_open_api.messages import OpenApiMessages_pb2 as oa  # noqa: E402
from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model  # noqa: E402

ACCOUNT_ID = 12345


def error_res(code, description="x"):
    return oa.ProtoOAErrorRes(errorCode=code, description=description)


def light_symbols(*pairs):
    res = oa.ProtoOASymbolsListRes(ctidTraderAccountId=ACCOUNT_ID)
    for sid, name in pairs:
        res.symbol.add(symbolId=sid, symbolName=name, enabled=True)
    return res


def symbol_res(ids):
    res = oa.ProtoOASymbolByIdRes(ctidTraderAccountId=ACCOUNT_ID)
    for sid in ids:
        s = res.symbol.add(symbolId=sid, digits=2, pipPosition=1, lotSize=100, minVolume=100,
                           stepVolume=100, maxVolume=10_000_000, slDistance=50,
                           distanceSetIn=model.SYMBOL_DISTANCE_IN_POINTS,
                           scheduleTimeZone="Europe/London")
        s.schedule.add(startSecond=0, endSecond=3600)
        s.schedule.add(startSecond=7200, endSecond=86400)
        s.holiday.add(holidayId=1, name="NY", scheduleTimeZone="UTC", holidayDate=20000,
                      isRecurring=True, startSecond=0, endSecond=86400)
    return res


def install_happy(fake, live=False, listed=ACCOUNT_ID, symbols=((1, "XAUUSD"), (2, "BTCUSD"))):
    """Responders for a normal handshake and subscriptions."""
    fake.respond(oa.ProtoOAApplicationAuthReq, oa.ProtoOAApplicationAuthRes())

    def acct_list(m):
        r = oa.ProtoOAGetAccountListByAccessTokenRes(accessToken=m.accessToken)
        if listed is not None:
            r.ctidTraderAccount.add(ctidTraderAccountId=listed, isLive=live)
        return r
    fake.respond(oa.ProtoOAGetAccountListByAccessTokenReq, acct_list)
    fake.respond(oa.ProtoOAAccountAuthReq, oa.ProtoOAAccountAuthRes(ctidTraderAccountId=ACCOUNT_ID))
    tr = oa.ProtoOATraderRes(ctidTraderAccountId=ACCOUNT_ID)
    tr.trader.ctidTraderAccountId = ACCOUNT_ID
    tr.trader.balance = 1_000_000
    tr.trader.depositAssetId = 15
    tr.trader.accountType = model.HEDGED
    tr.trader.moneyDigits = 2
    fake.respond(oa.ProtoOATraderReq, tr)
    fake.respond(oa.ProtoOASymbolsListReq, light_symbols(*symbols))
    fake.respond(oa.ProtoOASymbolByIdReq, lambda m: symbol_res(list(m.symbolId)))
    fake.respond(oa.ProtoOASubscribeSpotsReq, oa.ProtoOASubscribeSpotsRes())
    fake.respond(oa.ProtoOASubscribeDepthQuotesReq, oa.ProtoOASubscribeDepthQuotesRes())
