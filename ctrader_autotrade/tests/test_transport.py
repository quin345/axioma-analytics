import logging

from twisted.internet import defer
from twisted.internet.error import ConnectionLost

from autotrade.clock import TestClock
from autotrade.transport import OpenApiTransport
from ctrader_open_api import Protobuf
from ctrader_open_api.messages import OpenApiCommonMessages_pb2 as common
from ctrader_open_api.messages import OpenApiMessages_pb2 as oa


class FakeClient:
    """Stands in for ctrader_open_api.Client."""

    def __init__(self):
        self._responseDeferreds = {}
        self.sent = []

    def setConnectedCallback(self, cb): self.conn = cb
    def setDisconnectedCallback(self, cb): self.disc = cb
    def setMessageReceivedCallback(self, cb): self.msg = cb
    def startService(self): self.started = True
    def stopService(self): self.stopped = True

    def send(self, message, responseTimeoutInSeconds=5):
        d = defer.Deferred()
        mid = str(len(self.sent) + 1)
        self.sent.append((message, mid, responseTimeoutInSeconds))
        self._responseDeferreds[mid] = d
        return d

    def reply(self, mid, payload):
        m = common.ProtoMessage(payload=payload.SerializeToString(), clientMsgId=mid,
                                payloadType=payload.payloadType)
        self.msg(self, m)  # callback first, as Client._received does
        self._responseDeferreds.pop(mid).callback(m)


def make():
    clock = TestClock(0)
    c = FakeClient()
    return OpenApiTransport("demo.ctraderapi.com", 5035, clock, client=c), c, clock


def test_send_extracts_response_and_logs_latency(caplog):
    t, c, clock = make()
    seen = []
    t.on_message(seen.append)
    out = []
    caplog.set_level(logging.INFO, logger="autotrade.transport")
    t.send(oa.ProtoOATraderReq(ctidTraderAccountId=1), timeout=7).addCallback(out.append)
    assert c.sent[0][2] == 7
    clock.advance(0.25)
    tr = oa.ProtoOATraderRes(ctidTraderAccountId=1)
    tr.trader.ctidTraderAccountId = 1
    tr.trader.balance = 1
    tr.trader.depositAssetId = 1
    c.reply("1", tr)
    assert isinstance(out[0], oa.ProtoOATraderRes)
    assert seen == []  # responses are not broadcast
    assert any("send->ack ProtoOATraderReq 250 ms" in r.message for r in caplog.records)


def test_unsolicited_messages_dispatched_heartbeats_dropped():
    t, c, clock = make()
    seen = []
    t.on_message(seen.append)
    ev = oa.ProtoOASpotEvent(ctidTraderAccountId=1, symbolId=2, bid=5)
    c.msg(c, common.ProtoMessage(payload=ev.SerializeToString(), payloadType=ev.payloadType))
    hb = common.ProtoHeartbeatEvent()
    c.msg(c, common.ProtoMessage(payload=hb.SerializeToString(), payloadType=hb.payloadType))
    assert len(seen) == 1 and seen[0].symbolId == 2


def test_disconnect_fails_pending_and_notifies():
    t, c, clock = make()
    reasons, out = [], []
    t.on_disconnected(reasons.append)
    t.send(oa.ProtoOATraderReq()).addErrback(out.append)
    c.disc(c, "gone")
    assert out[0].check(ConnectionLost) and reasons == ["gone"]
    t.start(); t.stop()
    assert c.started and c.stopped
