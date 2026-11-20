"""Transport layer between Session and the Open API socket (DESIGN.md section 3).

`Transport` is the narrow interface Session needs. `OpenApiTransport` wraps
ctrader_open_api.Client (auto-reconnect with exponential backoff); tests use
tests/fake_transport.FakeTransport. No handshake logic lives here.

Message delivery contract: `on_message` callbacks receive only unsolicited messages
(spot/depth/execution events, token events, unsolicited errors). Responses are delivered
through the Deferred returned by `send`.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Protocol

from twisted.application.internet import backoffPolicy
from twisted.internet import defer
from twisted.internet.error import ConnectionLost

log = logging.getLogger(__name__)


class Transport(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...

    def send(self, msg: Any, timeout: float = 5) -> "defer.Deferred":
        """Send a request; the Deferred fires with the response (already extracted) or fails
        (timeout, or ConnectionLost if the link drops first)."""

    def notify(self, msg: Any) -> None:
        """Fire-and-forget send (heartbeats); no response is awaited."""

    def on_connected(self, cb: Callable[[], None]) -> None: ...
    def on_disconnected(self, cb: Callable[[Any], None]) -> None: ...
    def on_message(self, cb: Callable[[Any], None]) -> None: ...


class OpenApiTransport:
    """ctrader_open_api.Client with 1 s -> 60 s (x2) reconnect backoff and send->ack latency logs."""

    def __init__(self, host: str, port: int, clock, client: Optional[Any] = None):
        self._clock = clock
        self._pending: set = set()
        self._connected_cbs: list = []
        self._disconnected_cbs: list = []
        self._message_cbs: list = []
        if client is None:
            from ctrader_open_api import Client, TcpProtocol
            client = Client(host, port, TcpProtocol,
                            retryPolicy=backoffPolicy(initialDelay=1, maxDelay=60, factor=2),
                            clock=clock.reactor)
        self._client = client
        client.setConnectedCallback(self._on_client_connected)
        client.setDisconnectedCallback(self._on_client_disconnected)
        client.setMessageReceivedCallback(self._on_client_message)

    # --- Transport interface ---

    def start(self) -> None:
        self._client.startService()

    def stop(self) -> None:
        self._client.stopService()

    def on_connected(self, cb) -> None:
        self._connected_cbs.append(cb)

    def on_disconnected(self, cb) -> None:
        self._disconnected_cbs.append(cb)

    def on_message(self, cb) -> None:
        self._message_cbs.append(cb)

    def send(self, msg, timeout: float = 5):
        from ctrader_open_api import Protobuf
        out: defer.Deferred = defer.Deferred()
        name = type(msg).__name__
        t0 = self._clock.now_ms()
        self._pending.add(out)

        def ok(proto_message):
            self._pending.discard(out)
            if out.called:
                return
            try:
                payload = Protobuf.extract(proto_message)
            except Exception as exc:  # unknown payload type
                out.errback(exc)
                return
            log.info("send->ack %s %d ms", name, self._clock.now_ms() - t0)
            out.callback(payload)

        def bad(failure):
            self._pending.discard(out)
            if not out.called:
                log.info("send->fail %s after %d ms: %s", name, self._clock.now_ms() - t0,
                         failure.getErrorMessage() or failure.type.__name__)
                out.errback(failure)

        inner = self._client.send(msg, responseTimeoutInSeconds=timeout)
        inner.addCallbacks(ok, bad)
        return out

    def notify(self, msg) -> None:
        d = self._client.whenConnected(failAfterFailures=1)
        d.addCallbacks(lambda protocol: protocol.send(msg), lambda failure: None)

    # --- client callbacks ---

    def _on_client_connected(self, client) -> None:
        for cb in list(self._connected_cbs):
            cb()

    def _on_client_disconnected(self, client, reason) -> None:
        # The Client drops its own response deferreds silently on disconnect; fail ours now so
        # callers do not wait for the timeout.
        for d in list(self._pending):
            self._pending.discard(d)
            if not d.called:
                d.errback(ConnectionLost("disconnected"))
        for cb in list(self._disconnected_cbs):
            cb(reason)

    def _on_client_message(self, client, message) -> None:
        from ctrader_open_api import Protobuf
        mid = getattr(message, "clientMsgId", None)
        if mid and mid in getattr(client, "_responseDeferreds", {}):
            return  # a response: delivered via the send() Deferred
        try:
            payload = Protobuf.extract(message)
        except Exception as exc:
            log.warning("undecodable message (payloadType=%s): %s", message.payloadType, exc)
            return
        if type(payload).__name__ == "ProtoHeartbeatEvent":
            return
        for cb in list(self._message_cbs):
            cb(payload)
