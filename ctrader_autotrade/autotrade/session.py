"""Open API session: handshake on every (re)connect, demo-only guard, token refresh, dispatch.

Handshake (DESIGN.md section 3): ApplicationAuth -> GetAccountListByAccessToken (account must be
listed with isLive == false) -> AccountAuth -> Trader -> SymbolsList -> SymbolById -> on_ready.
The runner then reconciles and warms up and calls subscribe() (spots with timestamps, then
depth). After a reconnect the whole handshake repeats and, once on_ready's work is done, the
session re-subscribes by itself (it remembers that subscribe() was called).

Token values are never logged.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Optional

from twisted.internet import defer, task
from twisted.internet.defer import inlineCallbacks, maybeDeferred, returnValue
from twisted.internet.error import ConnectionLost

from ctrader_open_api.messages import OpenApiCommonMessages_pb2 as common
from ctrader_open_api.messages import OpenApiMessages_pb2 as oa
from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model

from .types import SymbolSpec

log = logging.getLogger(__name__)

HEARTBEAT_S = 10
HANDSHAKE_RETRY_S = 5
HANDSHAKE_RETRIES = 3

TOKEN_ERROR_CODES = {"OA_AUTH_TOKEN_EXPIRED", "CH_ACCESS_TOKEN_INVALID"}

TRADING_MESSAGES = (oa.ProtoOANewOrderReq, oa.ProtoOACancelOrderReq, oa.ProtoOAClosePositionReq,
                    oa.ProtoOAAmendOrderReq, oa.ProtoOAAmendPositionSLTPReq)

_ERROR_TYPES = (oa.ProtoOAErrorRes, common.ProtoErrorRes)


class ApiError(Exception):
    """The server answered a request with ProtoOAErrorRes."""

    def __init__(self, request: str, code: str, description: str):
        super().__init__(f"{request}: {code}: {description}")
        self.request = request
        self.code = code
        self.description = description


class TradingRefused(Exception):
    """A trading message was sent while the account is not verified as demo (or in SAFE mode)."""


class _Stale(Exception):
    """The connection changed under a running handshake."""


def _noop(*args, **kwargs):
    return None


def _env_fp(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]



class Session:
    def __init__(self, settings, transport, clock):
        self.settings = settings
        self._tx = transport
        self._clock = clock
        self._secrets = settings.secrets
        # callbacks (assign after construction); on_ready may return a Deferred
        self.on_ready: Callable[[dict, dict], Any] = _noop
        self.on_refused: Callable[[str], Any] = _noop
        self.on_safe: Callable[[str], Any] = _noop
        self.on_connected: Callable[[], Any] = _noop
        self.on_disconnected: Callable[[str], Any] = _noop
        self.on_spot: Callable[..., Any] = _noop
        self.on_depth: Callable[..., Any] = _noop
        self.on_execution: Callable[[Any], Any] = _noop
        self.on_order_error: Callable[[Any], Any] = _noop

        self.verified_demo = False
        self.account_id: int = self._secrets.account_id
        self.specs: dict[int, SymbolSpec] = {}
        self.broker_names: dict[int, str] = {}
        self.unresolved: list[str] = []
        self.trader_info: dict = {}
        self.safe = False
        self.safe_reason: Optional[str] = None
        self.refused_reason: Optional[str] = None
        self.ready = False
        self.hist_limiter = None  # set lazily by warmup (shared 5 req/s budget)

        self._link_up = False
        self._gen = 0
        self._handshaking = False
        self._halted = False
        self._auto_subscribe = False
        self._refreshed_on_conn = False
        self._hb: Optional[task.LoopingCall] = None
        self._token_file = Path(settings.state_path()) / "token.json"

        transport.on_connected(self._on_link_up)
        transport.on_disconnected(self._on_link_down)
        transport.on_message(self._on_message)

    # ---- public ----

    @property
    def connected(self) -> bool:
        return self._link_up

    def start(self) -> None:
        self._tx.start()

    def stop(self) -> None:
        self._halted = True
        self._tx.stop()

    def send(self, msg, timeout: float = 5):
        """Send a request (raw response). Trading messages need verified_demo and no SAFE mode."""
        if isinstance(msg, TRADING_MESSAGES) and not (self.verified_demo and not self.safe):
            raise TradingRefused(
                f"{type(msg).__name__} refused: verified_demo={self.verified_demo} safe={self.safe}")
        self._fill_account(msg)
        return self._tx.send(msg, timeout=timeout)

    def request(self, msg, timeout: float = 5):
        """Like send() but a ProtoOAErrorRes response fails the Deferred with ApiError."""
        d = self.send(msg, timeout=timeout)
        d.addCallback(self._raise_on_error(type(msg).__name__))
        return d

    @inlineCallbacks
    def subscribe(self):
        """SubscribeSpots (with timestamps) then SubscribeDepthQuotes for every resolved symbol."""
        if not self.ready:
            raise RuntimeError("subscribe() before the handshake completed")
        self._auto_subscribe = True
        ids = sorted(self.specs)
        spots = oa.ProtoOASubscribeSpotsReq(symbolId=ids, subscribeToSpotTimestamp=True)
        depth = oa.ProtoOASubscribeDepthQuotesReq(symbolId=ids)
        for req in (spots, depth):
            try:
                yield self.request(req)
            except ApiError as exc:
                if exc.code != "ALREADY_SUBSCRIBED":
                    raise
        log.info("subscribed to spots+depth for %s", [self.specs[i].name for i in ids])

    # ---- link events ----

    def _on_link_up(self):
        self._gen += 1
        self._link_up = True
        self._refreshed_on_conn = False
        self.ready = False
        self._start_heartbeat()
        self._emit(self.on_connected)
        self._start_handshake(skip_app_auth=False)

    def _on_link_down(self, reason):
        self._gen += 1
        self._link_up = False
        self.ready = False
        self.verified_demo = False
        self._handshaking = False
        self._stop_heartbeat()
        log.warning("disconnected: %s", reason)
        self._emit(self.on_disconnected, str(reason))

    def _start_heartbeat(self):
        self._stop_heartbeat()
        self._hb = task.LoopingCall(lambda: self._tx.notify(common.ProtoHeartbeatEvent()))
        self._hb.clock = self._clock.reactor
        self._hb.start(HEARTBEAT_S, now=False)

    def _stop_heartbeat(self):
        if self._hb is not None and self._hb.running:
            self._hb.stop()
        self._hb = None

    # ---- handshake ----

    def _start_handshake(self, skip_app_auth: bool, attempt: int = 0):
        if self._handshaking or self._halted:
            return
        d = self._run_handshake(skip_app_auth, attempt)
        d.addErrback(lambda f: log.error("handshake crashed: %s", f.getErrorMessage()))

    @inlineCallbacks
    def _run_handshake(self, skip_app_auth: bool, attempt: int):
        gen = self._gen
        self._handshaking = True
        try:
            while True:
                try:
                    outcome = yield self._handshake_once(gen, skip_app_auth)
                except _Stale:
                    return
                except ApiError as exc:
                    if gen != self._gen:
                        return
                    if exc.code in TOKEN_ERROR_CODES:
                        ok = yield self._refresh_token(gen)
                        if ok:
                            skip_app_auth = True  # application is already authorised on this link
                            continue
                        return
                    self._refuse(str(exc))
                    return
                except Exception as exc:  # timeout, link loss
                    if gen != self._gen:
                        return
                    log.warning("handshake attempt %d failed: %s", attempt + 1, exc)
                    if attempt + 1 >= HANDSHAKE_RETRIES:
                        self._enter_safe(f"handshake failed {HANDSHAKE_RETRIES} times: {exc}")
                        return
                    self._handshaking = False
                    self._clock.call_later(HANDSHAKE_RETRY_S, self._retry_handshake, gen, attempt + 1)
                    return
                break
            if outcome != "ready":
                return
        finally:
            if gen == self._gen:
                self._handshaking = False
        # on_ready and optional re-subscribe
        if gen != self._gen:
            return
        self.ready = True
        yield maybeDeferred(self.on_ready, dict(self.specs), dict(self.trader_info))
        if gen == self._gen and self._auto_subscribe and not self.safe:
            try:
                yield self.subscribe()
            except Exception as exc:
                if gen == self._gen:
                    log.error("re-subscribe failed: %s", exc)

    def _retry_handshake(self, gen: int, attempt: int):
        if gen == self._gen and self._link_up:
            self._start_handshake(skip_app_auth=False, attempt=attempt)

    @inlineCallbacks
    def _handshake_once(self, gen: int, skip_app_auth: bool):
        s = self._secrets
        access, _ = self._tokens()
        self.verified_demo = False
        self.refused_reason = None

        def step(req, timeout=5):
            d = self._tx.send(req, timeout=timeout)
            d.addCallback(self._raise_on_error(type(req).__name__))
            return d

        def alive():
            if gen != self._gen:
                raise _Stale()

        if not skip_app_auth:
            yield step(oa.ProtoOAApplicationAuthReq(clientId=s.client_id, clientSecret=s.client_secret))
            alive()
        res = yield step(oa.ProtoOAGetAccountListByAccessTokenReq(accessToken=access))
        alive()
        acct = next((a for a in res.ctidTraderAccount if a.ctidTraderAccountId == self.account_id), None)
        if acct is None:
            self._refuse("configured account id is not in the token's account list")
            return
        if acct.isLive:
            self._refuse("account is a LIVE account (isLive=true); demo only")
            return
        self.verified_demo = True
        yield step(oa.ProtoOAAccountAuthReq(ctidTraderAccountId=self.account_id, accessToken=access))
        alive()
        res = yield step(oa.ProtoOATraderReq(ctidTraderAccountId=self.account_id))
        alive()
        self.trader_info = self._trader_info(res.trader)
        res = yield step(oa.ProtoOASymbolsListReq(ctidTraderAccountId=self.account_id), timeout=15)
        alive()
        resolved = self._resolve_symbols(res.symbol)
        specs: dict[int, SymbolSpec] = {}
        if resolved:
            res = yield step(oa.ProtoOASymbolByIdReq(ctidTraderAccountId=self.account_id,
                                                     symbolId=sorted(resolved)), timeout=15)
            alive()
            by_id = {sym.symbolId: sym for sym in res.symbol}
            for sid, name in resolved.items():
                if sid not in by_id:
                    self.unresolved.append(name)
                    log.warning("SymbolById returned nothing for %s (id %d)", name, sid)
                    continue
                specs[sid] = self._build_spec(name, by_id[sid])
        self.specs = specs
        log.info("handshake ok: account type %s, symbols %s", self.trader_info.get("account_type"),
                 [sp.name for sp in specs.values()])
        returnValue("ready")

    @staticmethod
    def _raise_on_error(request: str):
        def check(res):
            if isinstance(res, _ERROR_TYPES):
                raise ApiError(request, res.errorCode, res.description)
            return res
        return check

    def _fill_account(self, msg):
        fields = msg.DESCRIPTOR.fields_by_name
        if "ctidTraderAccountId" in fields and not msg.HasField("ctidTraderAccountId"):
            msg.ctidTraderAccountId = self.account_id

    def _trader_info(self, trader) -> dict:
        return {
            "account_type": model.ProtoOAAccountType.Name(trader.accountType),
            "money_digits": trader.moneyDigits if trader.HasField("moneyDigits") else None,
            "deposit_asset_id": trader.depositAssetId,
            "balance": trader.balance,
            "broker_name": trader.brokerName if trader.HasField("brokerName") else None,
            "trader_login": trader.traderLogin if trader.HasField("traderLogin") else None,
        }

    def _resolve_symbols(self, light_symbols) -> dict[int, str]:
        """settings symbol name -> broker symbol by exact name, then aliases, case-insensitive."""
        by_name = {}
        for ls in light_symbols:
            by_name.setdefault(ls.symbolName.lower(), ls)
        resolved: dict[int, str] = {}
        self.unresolved = []
        self.broker_names = {}
        for name in self.settings.symbols:
            for cand in [name, *self.settings.aliases.get(name, [])]:
                ls = by_name.get(cand.lower())
                if ls is not None:
                    resolved[ls.symbolId] = name
                    self.broker_names[ls.symbolId] = ls.symbolName
                    break
            else:
                self.unresolved.append(name)
                log.warning("symbol %s not found on the account (aliases tried: %s); skipped",
                            name, self.settings.aliases.get(name, []))
        return resolved

    @staticmethod
    def _build_spec(name: str, sym) -> SymbolSpec:
        def opt(field, default=0):
            return getattr(sym, field) if sym.HasField(field) else default
        dist = "PERCENTAGE" if sym.distanceSetIn == model.SYMBOL_DISTANCE_IN_PERCENTAGE else "POINTS"
        holidays = [{"id": h.holidayId, "name": h.name, "tz": h.scheduleTimeZone,
                     "date_days": h.holidayDate, "recurring": h.isRecurring,
                     "start_s": h.startSecond if h.HasField("startSecond") else None,
                     "end_s": h.endSecond if h.HasField("endSecond") else None}
                    for h in sym.holiday]
        return SymbolSpec(
            name=name, symbol_id=sym.symbolId, digits=sym.digits, pip_position=sym.pipPosition,
            lot_size=opt("lotSize"), min_volume=opt("minVolume"), step_volume=opt("stepVolume"),
            max_volume=opt("maxVolume"), sl_distance=opt("slDistance"), distance_set_in=dist,
            schedule=[(i.startSecond, i.endSecond) for i in sym.schedule],
            schedule_tz=sym.scheduleTimeZone if sym.HasField("scheduleTimeZone") else "UTC",
            holidays=holidays)

    # ---- refusal / SAFE ----

    def _refuse(self, reason: str):
        self.verified_demo = False
        self.refused_reason = reason
        self._halted = True
        log.error("session refused: %s", reason)
        self._emit(self.on_refused, reason)
        self._tx.stop()

    def _enter_safe(self, reason: str):
        if self.safe:
            return
        self.safe = True
        self.safe_reason = reason
        log.error("SAFE mode: %s", reason)
        self._emit(self.on_safe, reason)

    def _emit(self, cb, *args):
        try:
            res = cb(*args)
            if isinstance(res, defer.Deferred):
                res.addErrback(lambda f: log.error("callback error: %s", f.getErrorMessage()))
        except Exception:
            log.exception("callback %s raised", getattr(cb, "__name__", cb))

    # ---- tokens ----

    def _stored_token(self) -> Optional[dict]:
        try:
            data = json.loads(self._token_file.read_text())
        except (OSError, ValueError):
            return None
        if data.get("env_fp") != _env_fp(self._secrets.access_token) or not data.get("accessToken"):
            return None  # .env token changed since the refresh: .env wins
        return data

    def _tokens(self) -> tuple[str, Optional[str]]:
        st = self._stored_token()
        if st:
            return st["accessToken"], st.get("refreshToken") or self._secrets.refresh_token
        return self._secrets.access_token, self._secrets.refresh_token

    def _persist_token(self, res) -> None:
        data = {"accessToken": res.accessToken, "refreshToken": res.refreshToken,
                "expiresIn": res.expiresIn, "obtained_at": self._clock.now_ms(),
                "env_fp": _env_fp(self._secrets.access_token)}
        self._token_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._token_file.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._token_file)

    @inlineCallbacks
    def _refresh_token(self, gen: int):
        """Refresh once per connection; returns True if a new token is stored, else enters SAFE."""
        _, refresh = self._tokens()
        if not refresh:
            self._enter_safe("access token rejected and no refresh_token configured")
            returnValue(False)
        if self._refreshed_on_conn:
            self._enter_safe("access token rejected again right after a refresh")
            returnValue(False)
        self._refreshed_on_conn = True
        log.info("access token rejected; refreshing")
        try:
            res = yield self._tx.send(oa.ProtoOARefreshTokenReq(refreshToken=refresh))
            if isinstance(res, _ERROR_TYPES):
                raise ApiError("ProtoOARefreshTokenReq", res.errorCode, res.description)
            self._persist_token(res)
        except Exception as exc:
            if gen == self._gen:
                self._enter_safe(f"token refresh failed: {exc}")
            returnValue(False)
        returnValue(True)

    # ---- events ----

    def _on_message(self, msg):
        if isinstance(msg, oa.ProtoOASpotEvent):
            self._emit(self.on_spot, msg.symbolId,
                       msg.bid if msg.HasField("bid") else None,
                       msg.ask if msg.HasField("ask") else None,
                       msg.timestamp if msg.HasField("timestamp") else None, msg)
        elif isinstance(msg, oa.ProtoOADepthEvent):
            new = []
            for q in msg.newQuotes:
                if q.HasField("bid"):
                    new.append((q.id, "bid", q.bid, q.size))
                elif q.HasField("ask"):
                    new.append((q.id, "ask", q.ask, q.size))
            self._emit(self.on_depth, msg.symbolId, new, list(msg.deletedQuotes), msg)
        elif isinstance(msg, oa.ProtoOAExecutionEvent):
            self._emit(self.on_execution, msg)
        elif isinstance(msg, oa.ProtoOAOrderErrorEvent):
            self._emit(self.on_order_error, msg)
        elif isinstance(msg, oa.ProtoOAAccountsTokenInvalidatedEvent):
            log.warning("accounts token invalidated: %s", msg.reason)
            self._token_problem()
        elif isinstance(msg, _ERROR_TYPES):
            log.warning("unsolicited error %s: %s", msg.errorCode, msg.description)
            if msg.errorCode in TOKEN_ERROR_CODES:
                self._token_problem()
        elif isinstance(msg, (oa.ProtoOAAccountDisconnectEvent, oa.ProtoOAClientDisconnectEvent)):
            log.warning("%s received", type(msg).__name__)

    def _token_problem(self):
        """Mid-session token loss: refresh, then redo the account part of the handshake."""
        if self._handshaking or not self._link_up or self.safe:
            return
        gen = self._gen
        self._handshaking = True
        d = self._refresh_token(gen)

        def after(ok):
            self._handshaking = False
            if ok and gen == self._gen:
                self.ready = False
                self._start_handshake(skip_app_auth=True)
        d.addCallback(after)
