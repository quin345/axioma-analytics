"""Order execution: report normalisation, DemoBroker and the per-symbol order/position state machine.

State machine (DESIGN.md section 3), one per symbol, at most one position per symbol:

    IDLE -> PENDING_NEW -> WORKING -> (PARTIAL) -> OPEN -> EXIT_PENDING -> CLOSED -> IDLE
    plus RECONCILING (no reply within 5 s) and CANCEL_PENDING (cancel sent; a cancel reject
    means "wait for the fill", then OPEN).

The same OrderManager runs in dry-run: SimBroker (simbroker.py) emits synthetic ExecReports into
`OrderManager.on_report`, so the transitions are identical.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Protocol

from twisted.internet import defer

from .clock import Clock
from .config import Settings
from .types import (ExecMode, ExecReport, Intent, IntentKind, PositionView, Quote, RunMode, Side,
                    SymbolSpec)

log = logging.getLogger(__name__)

PRICE_SCALE = 100_000          # Open API relative values / integer prices are in 1/100000
SLOT_S = 300                   # 5-minute bars
REPLY_TIMEOUT_S = 5.0          # no reply to an order -> RECONCILING
EXIT_BACKSTOP_S = 10.0         # safety exit this long after exit_at if the strategy did not act
HOLD_EXTEND_MS = 5 * 60_000    # rollover
TRADE_REASONS = ("time", "flip", "stop", "kill", "session", "disconnect", "risk", "shutdown", "manual")


class RefusedError(Exception):
    """An order was refused locally (not a verified demo account, bad parameters)."""


# --- normalisation -------------------------------------------------------------------------------

def normalise_volume(spec: SymbolSpec, size_mult: float, base_size_x_min: int) -> int:
    """Volume = base_size_x_min * min_volume * size_mult, rounded DOWN to step_volume, clamped to
    [min_volume, max_volume]. If rounding lands below min_volume the order is lifted to min_volume
    when size_mult >= 0.5 (a "half size" must still trade); for smaller multipliers 0 is returned
    and the caller must not send an order."""
    raw = base_size_x_min * spec.min_volume * size_mult
    step = max(spec.step_volume, 1)
    vol = int(math.floor(raw / step + 1e-9)) * step
    if vol < spec.min_volume:
        vol = spec.min_volume if size_mult >= 0.5 else 0
    return min(vol, spec.max_volume) if vol else 0


def round_price(spec: SymbolSpec, price_int: int) -> int:
    """Round an integer price (1/100000) to the symbol's digits."""
    tick = 10 ** max(5 - spec.digits, 0)
    return int(round(price_int / tick)) * tick


def price_to_float(spec: SymbolSpec, price_int: int) -> float:
    """Integer price to the display float sent in limitPrice (rounded to digits)."""
    return round(round_price(spec, price_int) / PRICE_SCALE, spec.digits)


def min_stop_rel(spec: SymbolSpec, ref_price_int: int) -> int:
    """Smallest relativeStopLoss the broker accepts, in 1/100000 price units.

    POINTS: one point is 10**-digits, i.e. 10**(5-digits) integer units. PERCENTAGE: sl_distance is
    read as hundredths of a percent of the reference price (open question, see report)."""
    if spec.distance_set_in.upper().startswith("PERCENT"):
        return int(math.ceil(ref_price_int * spec.sl_distance / 10_000))
    return spec.sl_distance * 10 ** max(5 - spec.digits, 0)


def clamp_stop_rel(spec: SymbolSpec, stop_rel: Optional[int], ref_price_int: int) -> Optional[int]:
    """The strategy computes stop_rel; here it is only validated: at least the broker minimum, at
    most the price itself, and rounded to whole price ticks. None stays None."""
    if stop_rel is None:
        return None
    tick = 10 ** max(5 - spec.digits, 0)
    rel = max(int(stop_rel), min_stop_rel(spec, ref_price_int))
    rel = int(math.ceil(rel / tick)) * tick
    return min(rel, max(ref_price_int - tick, tick))


def to_exec_report(raw: Any, money_digits: Optional[int] = None) -> ExecReport:
    """Normalise a ProtoOAExecutionEvent."""
    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as mm
    order = raw.order if raw.HasField("order") else None
    pos = raw.position if raw.HasField("position") else None
    deal = raw.deal if raw.HasField("deal") else None
    cpd = deal.closePositionDetail if deal is not None and deal.HasField("closePositionDetail") else None

    def td(msg):
        return msg.tradeData if msg is not None and msg.HasField("tradeData") else None

    otd, ptd = td(order), td(pos)
    label = (otd.label if otd is not None and otd.HasField("label") else None) or \
            (ptd.label if ptd is not None and ptd.HasField("label") else None) or None
    symbol_id = (otd.symbolId if otd is not None else None) or (ptd.symbolId if ptd is not None else None) \
        or (deal.symbolId if deal is not None else None) or None
    side_pb = deal.tradeSide if deal is not None else (otd.tradeSide if otd is not None else None)
    side = None if side_pb is None else (Side.BUY if side_pb == mm.BUY else Side.SELL)
    if deal is not None:
        filled = deal.filledVolume or deal.volume
        price = deal.executionPrice or None
    else:
        filled = order.executedVolume if order is not None else 0
        price = (order.executionPrice or None) if order is not None else None
    ts = 0
    if deal is not None:
        ts = deal.executionTimestamp or deal.utcLastUpdateTimestamp
    if not ts and order is not None:
        ts = order.utcLastUpdateTimestamp
    if not ts and pos is not None:
        ts = pos.utcLastUpdateTimestamp
    md = (cpd.moneyDigits if cpd is not None and cpd.HasField("moneyDigits") else None)
    if md is None and deal is not None and deal.HasField("moneyDigits"):
        md = deal.moneyDigits
    return ExecReport(
        execution_type=mm.ProtoOAExecutionType.Name(raw.executionType),
        order_id=((order.orderId if order is not None else 0) or (deal.orderId if deal is not None else 0)
                  or None),
        position_id=((pos.positionId if pos is not None else 0) or (deal.positionId if deal is not None else 0)
                     or (order.positionId if order is not None else 0) or None),
        client_order_id=((order.clientOrderId if order is not None else "") or None),
        label=label,
        symbol_id=symbol_id,
        side=side,
        filled_volume=int(filled or 0),
        price=price,
        status=(mm.ProtoOAOrderStatus.Name(order.orderStatus) if order is not None else None),
        error_code=(raw.errorCode or None),
        gross_profit=(cpd.grossProfit if cpd is not None else None),
        commission=(cpd.commission if cpd is not None else (deal.commission if deal is not None else None)),
        swap=(cpd.swap if cpd is not None else None),
        money_digits=md if md is not None else money_digits,
        ts_ms=int(ts or 0))


def to_error_report(raw: Any, now_ms: int) -> ExecReport:
    """Normalise a ProtoOAOrderErrorEvent (it carries no clientOrderId or label)."""
    return ExecReport(execution_type="ORDER_ERROR", order_id=raw.orderId or None,
                      position_id=raw.positionId or None, client_order_id=None, label=None, symbol_id=None,
                      side=None, filled_volume=0, price=None, status=None, error_code=raw.errorCode or None,
                      gross_profit=None, commission=None, swap=None, money_digits=None, ts_ms=now_ms)


# --- broker interface -----------------------------------------------------------------------------

@dataclass(frozen=True)
class BrokerPosition:
    position_id: Optional[int]
    symbol_id: int
    side: Side
    volume: int
    price: float                 # display units
    label: Optional[str]
    open_ms: int = 0


@dataclass(frozen=True)
class BrokerOrder:
    order_id: int
    symbol_id: int
    side: Side
    volume: int
    client_order_id: Optional[str]
    label: Optional[str]
    order_type: str = ""
    expiry_ms: Optional[int] = None
    limit_price: Optional[float] = None
    position_id: Optional[int] = None


class Broker(Protocol):
    """What OrderManager needs. `name` is "demo" (real orders) or "sim" (dry-run)."""
    name: str
    account_type: str            # "HEDGED" | "NETTED"
    money_digits: int

    def place_entry(self, spec: SymbolSpec, side: Side, volume: int, mode: ExecMode,
                    limit_price: Optional[int], expiry_ms: Optional[int], stop_rel: Optional[int],
                    client_order_id: str, label: str, comment: str) -> defer.Deferred: ...

    def cancel(self, order_id: int) -> defer.Deferred: ...

    def close(self, position_id: int, volume: int) -> defer.Deferred: ...

    def reconcile(self) -> defer.Deferred: ...   # -> (list[BrokerPosition], list[BrokerOrder])


class SessionLike(Protocol):
    """The part of session.Session that DemoBroker uses."""
    verified_demo: bool
    account_id: int
    trader_info: dict            # Session.trader_info: account_type ("HEDGED"/"NETTED"), money_digits

    def send(self, msg: Any) -> defer.Deferred: ...


def _account_type(v: Any) -> str:
    if isinstance(v, str):
        return "NETTED" if v.upper().startswith("NET") else "HEDGED"
    return "NETTED" if v == 1 else "HEDGED"


def _extract(resp: Any) -> Any:
    """Unwrap a raw ProtoMessage envelope if the session did not already."""
    if hasattr(resp, "position") or not hasattr(resp, "payload"):
        return resp
    from ctrader_open_api import Protobuf
    return Protobuf.extract(resp)


class DemoBroker:
    """Real orders on the demo account over the session. verified_demo is asserted before EVERY send."""
    name = "demo"

    def __init__(self, session: SessionLike):
        self._s = session

    @property
    def account_type(self) -> str:
        return _account_type(self._s.trader_info.get("account_type"))

    @property
    def money_digits(self) -> int:
        return int(self._s.trader_info.get("money_digits") or 2)

    def _send(self, msg: Any) -> defer.Deferred:
        if not getattr(self._s, "verified_demo", False):
            raise RefusedError("refusing to send: session is not a verified demo account")
        return self._s.send(msg)

    def place_entry(self, spec, side, volume, mode, limit_price, expiry_ms, stop_rel,
                    client_order_id, label, comment) -> defer.Deferred:
        from ctrader_open_api.messages import OpenApiMessages_pb2 as m
        from ctrader_open_api.messages import OpenApiModelMessages_pb2 as mm
        if not getattr(self._s, "verified_demo", False):
            raise RefusedError("refusing to send: session is not a verified demo account")
        if volume <= 0:
            raise RefusedError("volume must be positive")
        req = m.ProtoOANewOrderReq()
        req.ctidTraderAccountId = self._s.account_id
        req.symbolId = spec.symbol_id
        req.tradeSide = mm.BUY if side is Side.BUY else mm.SELL
        req.volume = int(volume)
        req.label = label
        req.comment = comment[:512]
        req.clientOrderId = client_order_id
        if mode is ExecMode.MAKER:
            if limit_price is None or expiry_ms is None:
                raise RefusedError("maker order needs limit_price and expiry_ms")
            req.orderType = mm.LIMIT
            req.limitPrice = price_to_float(spec, limit_price)
            req.timeInForce = mm.GOOD_TILL_DATE
            req.expirationTimestamp = int(expiry_ms)
        else:
            req.orderType = mm.MARKET
        if stop_rel:
            req.relativeStopLoss = int(stop_rel)
        return self._send(req)

    def cancel(self, order_id: int) -> defer.Deferred:
        from ctrader_open_api.messages import OpenApiMessages_pb2 as m
        req = m.ProtoOACancelOrderReq()
        req.ctidTraderAccountId = self._s.account_id
        req.orderId = int(order_id)
        return self._send(req)

    def close(self, position_id: int, volume: int) -> defer.Deferred:
        from ctrader_open_api.messages import OpenApiMessages_pb2 as m
        req = m.ProtoOAClosePositionReq()
        req.ctidTraderAccountId = self._s.account_id
        req.positionId = int(position_id)
        req.volume = int(volume)
        return self._send(req)

    def reconcile(self) -> defer.Deferred:
        from ctrader_open_api.messages import OpenApiMessages_pb2 as m
        req = m.ProtoOAReconcileReq()
        req.ctidTraderAccountId = self._s.account_id
        d = self._send(req)
        d.addCallback(self._convert_reconcile)
        return d

    @staticmethod
    def _convert_reconcile(resp: Any):
        from ctrader_open_api.messages import OpenApiModelMessages_pb2 as mm
        res = _extract(resp)

        def side(v):
            return Side.BUY if v == mm.BUY else Side.SELL
        positions = [BrokerPosition(p.positionId, p.tradeData.symbolId, side(p.tradeData.tradeSide),
                                    p.tradeData.volume, p.price, p.tradeData.label or None,
                                    p.tradeData.openTimestamp) for p in res.position]
        orders = [BrokerOrder(o.orderId, o.tradeData.symbolId, side(o.tradeData.tradeSide), o.tradeData.volume,
                              o.clientOrderId or None, o.tradeData.label or None,
                              mm.ProtoOAOrderType.Name(o.orderType),
                              o.expirationTimestamp or None, o.limitPrice or None, o.positionId or None)
                  for o in res.order]
        return positions, orders


# --- order manager --------------------------------------------------------------------------------

class State(Enum):
    IDLE = "IDLE"
    PENDING_NEW = "PENDING_NEW"
    WORKING = "WORKING"
    PARTIAL = "PARTIAL"
    OPEN = "OPEN"
    EXIT_PENDING = "EXIT_PENDING"
    CLOSED = "CLOSED"
    RECONCILING = "RECONCILING"
    CANCEL_PENDING = "CANCEL_PENDING"


_ENTRY_STATES = (State.PENDING_NEW, State.WORKING, State.PARTIAL, State.CANCEL_PENDING)


@dataclass
class _Sym:
    symbol_id: int
    spec: Optional[SymbolSpec] = None
    state: State = State.IDLE
    prev_state: Optional[State] = None      # state to resolve when RECONCILING
    # entry
    side: Optional[Side] = None
    requested: int = 0
    filled: int = 0
    entry_value: float = 0.0                # sum(price * volume)
    entry_comm: int = 0
    coid: Optional[str] = None
    order_id: Optional[int] = None
    position_id: Optional[int] = None
    signal_id: str = ""
    signal_mid: Optional[float] = None
    fill_mid: Optional[float] = None
    entry_ms: int = 0
    exit_at_ms: int = 0
    exec_mode: ExecMode = ExecMode.TAKER
    expiry_ms: Optional[int] = None
    want_exit: Optional[str] = None         # exit as soon as the entry settles
    cancel_rejected: bool = False
    adverse_bps: Optional[float] = None
    # exit
    exit_reason: Optional[str] = None
    exit_coid: Optional[str] = None
    exit_order_id: Optional[int] = None
    closed: int = 0
    exit_value: float = 0.0
    exit_gross: Optional[int] = None
    exit_comm: int = 0
    exit_swap: int = 0
    exit_md: Optional[int] = None
    exit_ms: int = 0
    exit_attempts: int = 0
    pending_flip: Optional[Intent] = None
    flip_now_ms: int = 0
    timers: dict = field(default_factory=dict)


def _mid(q: Optional[Quote]) -> Optional[float]:
    return None if q is None else (q.bid + q.ask) / 2 / PRICE_SCALE


class OrderManager:
    """Per-symbol order/position state machine driven by intents and execution reports."""

    def __init__(self, broker: Broker, journal, clock: Clock, settings: Settings,
                 mode: RunMode, exec_mode: ExecMode):
        if mode is RunMode.DRY_RUN and broker.name != "sim":
            raise ValueError("DRY_RUN requires the sim broker (refusing to run dry with a real broker)")
        if mode is RunMode.DEMO and broker.name != "demo":
            raise ValueError("DEMO mode requires the demo broker")
        self.broker, self.journal, self.clock, self.settings = broker, journal, clock, settings
        self.mode, self.exec_mode = mode, exec_mode
        self._syms: dict[int, _Sym] = {}
        self._quotes: dict[int, Quote] = {}
        self._pending_flat: Optional[str] = None

    # ----- public ---------------------------------------------------------------------------------
    def on_quote(self, quote: Quote) -> None:
        self._quotes[quote.symbol_id] = quote

    def position_view(self, symbol_id: int) -> Optional[PositionView]:
        st = self._syms.get(symbol_id)
        if st is None or st.filled <= 0 or st.state not in (State.OPEN, State.EXIT_PENDING, State.PARTIAL,
                                                            State.CANCEL_PENDING, State.RECONCILING):
            return None
        if st.state is State.RECONCILING and st.prev_state not in (State.EXIT_PENDING, State.CANCEL_PENDING):
            return None
        return PositionView(symbol_id, st.side, st.filled - st.closed, st.entry_value / st.filled,
                            st.entry_ms, st.exit_at_ms, st.position_id if self.mode is RunMode.DEMO else None)

    def state_of(self, symbol_id: int) -> State:
        st = self._syms.get(symbol_id)
        return st.state if st else State.IDLE

    def open_count(self) -> int:
        """Symbols that are not IDLE (working orders count: they will become positions)."""
        return sum(1 for s in self._syms.values() if s.state is not State.IDLE)

    def handle(self, intent: Intent, spec: SymbolSpec, quote: Optional[Quote], now_ms: int) -> None:
        st = self._sym(intent.symbol_id, spec)
        if quote is not None:
            self._quotes[intent.symbol_id] = quote
        k = intent.kind
        if k is IntentKind.NONE:
            return
        if k is IntentKind.HOLD:
            if st.state is State.OPEN:
                st.exit_at_ms += HOLD_EXTEND_MS
                self._arm_exit_timer(st, st.exit_at_ms + EXIT_BACKSTOP_S * 1000 - now_ms)
                self.journal.record_transition(st.symbol_id, "OPEN", "OPEN", st.coid,
                                               f"hold: exit_at={st.exit_at_ms}", now_ms)
            return
        if k is IntentKind.EXIT:
            self._request_exit(st, intent.reason if intent.reason in TRADE_REASONS else "time", now_ms)
        elif k is IntentKind.FLIP:
            if st.state is State.IDLE:
                self._enter(st, intent, now_ms)
            elif st.state is State.OPEN and st.side is not intent.side:
                st.pending_flip, st.flip_now_ms = intent, now_ms
                self._request_exit(st, "flip", now_ms)
            else:
                self.journal.record_transition(st.symbol_id, st.state.value, st.state.value, st.coid,
                                               "flip_ignored", now_ms)
        elif k is IntentKind.ENTER:
            self._enter(st, intent, now_ms)

    def cancel_working(self, reason: str, now_ms: Optional[int] = None) -> None:
        """Cancel own resting entry orders only (a filled part stays open)."""
        now = now_ms or self.clock.now_ms()
        for st in list(self._syms.values()):
            if st.state in (State.WORKING, State.PARTIAL):
                self._cancel_working(st, reason, None, now)

    def flatten_all(self, reason: str, now_ms: Optional[int] = None) -> None:
        """Cancel working orders and close positions (kill, session end, disconnect, shutdown)."""
        now = now_ms or self.clock.now_ms()
        for st in list(self._syms.values()):
            st.pending_flip = None
            self._request_exit(st, reason, now)

    def startup_reconcile(self, specs: Optional[dict] = None) -> defer.Deferred:
        """Adopt own positions (close at once when past exit_at), cancel own resting orders."""
        specs = specs or {}
        d = defer.maybeDeferred(self.broker.reconcile)
        d.addCallback(self._startup_apply, specs)
        return d

    # ----- broker events --------------------------------------------------------------------------
    def on_execution_event(self, raw: Any) -> None:
        self.on_report(to_exec_report(raw, getattr(self.broker, "money_digits", None)))

    def on_order_error(self, raw: Any) -> None:
        self.on_report(to_error_report(raw, self.clock.now_ms()))

    def on_report(self, r: ExecReport) -> None:
        now = r.ts_ms or self.clock.now_ms()
        if r.execution_type == "ORDER_ERROR":
            return self._on_error(r, now)
        st = self._find(r)
        if st is None:
            return                                   # foreign label / unknown: not ours
        et = r.execution_type
        if et in ("SWAP", "DEPOSIT_WITHDRAW", "BONUS_DEPOSIT_WITHDRAW", "ORDER_REPLACED"):
            return
        if self._is_exit_report(st, r):
            self._on_exit_report(st, r, now)
        elif st.state in _ENTRY_STATES or (st.state is State.RECONCILING and st.prev_state in _ENTRY_STATES):
            self._on_entry_report(st, r, now)
        elif st.state is State.OPEN and r.gross_profit is not None:
            self._on_exit_report(st, r, now)         # server-side stop (or external close)
        else:
            self.journal.record_transition(st.symbol_id, st.state.value, st.state.value, r.client_order_id,
                                           f"ignored {et}", now)

    # ----- internals: lookup ----------------------------------------------------------------------
    def _sym(self, symbol_id: int, spec: Optional[SymbolSpec] = None) -> _Sym:
        st = self._syms.setdefault(symbol_id, _Sym(symbol_id))
        if spec is not None:
            st.spec = spec
        return st

    def _find(self, r: ExecReport) -> Optional[_Sym]:
        for st in self._syms.values():
            if r.client_order_id and r.client_order_id in (st.coid, st.exit_coid):
                return st
        for st in self._syms.values():
            if r.order_id is not None and r.order_id in (st.order_id, st.exit_order_id):
                return st
        for st in self._syms.values():
            if r.position_id is not None and st.position_id == r.position_id:
                return st
        if r.label == self.settings.label and r.symbol_id in self._syms:
            st = self._syms[r.symbol_id]
            return st if st.state is not State.IDLE else None
        return None

    def _set(self, st: _Sym, new: State, detail: str = "", now_ms: Optional[int] = None) -> None:
        old = st.state
        st.state = new
        self.journal.record_transition(st.symbol_id, old.value, new.value, st.exit_coid if new in (
            State.EXIT_PENDING,) else st.coid, detail, now_ms or self.clock.now_ms())

    def _cancel_timer(self, st: _Sym, key: str) -> None:
        t = st.timers.pop(key, None)
        if t is not None and t.active():
            t.cancel()

    def _arm(self, st: _Sym, key: str, delay_s: float, fn: Callable, *args) -> None:
        self._cancel_timer(st, key)
        st.timers[key] = self.clock.call_later(max(delay_s, 0.0), fn, st, *args)

    def _arm_exit_timer(self, st: _Sym, delay_s: float) -> None:
        self._arm(st, "exit", delay_s, self._exit_timer)

    # ----- internals: entry -----------------------------------------------------------------------
    def _enter(self, st: _Sym, intent: Intent, now_ms: int) -> None:
        spec, s = st.spec, self.settings
        if st.state is not State.IDLE:
            return self._note(st, now_ms, "enter_ignored: not idle")
        if self.open_count() >= s.max_positions:
            return self._note(st, now_ms, "enter_ignored: max_positions")
        if intent.side is None or spec is None:
            return self._note(st, now_ms, "enter_ignored: no side/spec")
        vol = normalise_volume(spec, intent.size_mult, s.base_size_x_min)
        if vol <= 0:
            return self._note(st, now_ms, "enter_ignored: size below minimum")
        slot_s = now_ms // 1000 // SLOT_S * SLOT_S
        coid = f"{spec.name}-{slot_s}-E"
        if self.journal.has_client_order_id(coid):
            return self._note(st, now_ms, f"enter_ignored: duplicate clientOrderId {coid}")
        q = self._quotes.get(st.symbol_id)
        ref = (q.ask if intent.side is Side.BUY else q.bid) if q else (intent.limit_price or 0)
        mode = self.exec_mode
        limit = expiry = None
        if mode is ExecMode.MAKER:
            if intent.limit_price is None or intent.expiry_ms is None:
                return self._note(st, now_ms, "enter_ignored: maker without limit/expiry")
            limit, expiry = round_price(spec, intent.limit_price), intent.expiry_ms
        stop = clamp_stop_rel(spec, intent.stop_rel, ref) if ref else intent.stop_rel
        st.side, st.requested, st.filled, st.entry_value, st.entry_comm = intent.side, vol, 0, 0.0, 0
        st.coid, st.order_id, st.position_id = coid, None, None
        st.signal_id, st.signal_mid, st.fill_mid = intent.signal_id, _mid(q), None
        st.exec_mode, st.expiry_ms = mode, expiry
        st.exit_at_ms = (slot_s + SLOT_S + s.grace_s) * 1000
        st.want_exit = st.exit_reason = st.exit_coid = st.exit_order_id = None
        st.closed, st.exit_value, st.exit_gross, st.exit_comm, st.exit_swap = 0, 0.0, None, 0, 0
        st.exit_attempts, st.cancel_rejected, st.entry_ms = 0, False, now_ms
        self.journal.record_order(coid, ts_ms=now_ms, symbol_id=st.symbol_id, side=intent.side, volume=vol,
                                  kind="ENTRY", order_type="LIMIT" if limit else "MARKET", limit_price=limit,
                                  expiry_ms=expiry, stop_rel=stop, signal_id=intent.signal_id,
                                  label=s.label, status="SENT")
        log.info("order %s %s %s %s vol=%s limit=%s (%s)", st.spec.name if st.spec else st.symbol_id,
                 intent.side.name, "LIMIT" if limit else "MARKET", mode.value, vol, limit, coid)
        self._set(st, State.PENDING_NEW, f"send {mode.value} {intent.side.name} {vol}", now_ms)
        self._arm(st, "reply", REPLY_TIMEOUT_S, self._reply_timeout, State.PENDING_NEW)
        self._send(st, State.PENDING_NEW, lambda: self.broker.place_entry(
            spec, intent.side, vol, mode, limit, expiry, stop, coid, s.label, intent.signal_id))

    def _note(self, st: _Sym, now_ms: int, text: str) -> None:
        self.journal.record_transition(st.symbol_id, st.state.value, st.state.value, st.coid, text, now_ms)

    def _send(self, st: _Sym, expected: State, call: Callable[[], defer.Deferred]) -> None:
        """Run a broker call; failures revert (refused) or wait for the reply timer."""
        marker = (st.coid, st.exit_coid)
        try:
            d = call()
        except RefusedError as e:
            return self._send_failed(st, expected, marker, e, refused=True)
        except Exception as e:                      # noqa: BLE001
            return self._send_failed(st, expected, marker, e, refused=False)
        if isinstance(d, defer.Deferred):
            d.addErrback(lambda f: self._send_failed(st, expected, marker, f.value,
                                                      refused=f.check(RefusedError) is not None))

    def _send_failed(self, st: _Sym, expected: State, marker, err, refused: bool) -> None:
        now = self.clock.now_ms()
        if st.state is not expected or marker != (st.coid, st.exit_coid):
            return
        self.journal.record_risk_event("send_failed" if not refused else "send_refused",
                                       f"{type(err).__name__}: {err}", st.symbol_id, now)
        if not refused:
            return                                   # the reply timer reconciles
        self._cancel_timer(st, "reply")
        if expected is State.PENDING_NEW:
            self._entry_dead(st, "refused", now)
        elif expected is State.EXIT_PENDING:
            self._exit_failed(st, "refused", now)

    def _reply_timeout(self, st: _Sym, expected: State) -> None:
        if st.state is not expected:
            return
        st.prev_state = expected
        self._set(st, State.RECONCILING, "no reply in 5s")
        self._reconcile(st)

    def _entry_dead(self, st: _Sym, why: str, now_ms: int) -> None:
        """Entry finished with nothing filled: IDLE (or flip bookkeeping cleared)."""
        for k in list(st.timers):
            self._cancel_timer(st, k)
        self._set(st, State.IDLE, why, now_ms)
        st.pending_flip = None

    def _on_entry_report(self, st: _Sym, r: ExecReport, now: int) -> None:
        et = r.execution_type
        if st.coid:
            self.journal.update_order(st.coid, status=et, **({"order_id": r.order_id} if r.order_id else {}))
        if r.order_id and st.order_id is None:
            st.order_id = r.order_id
        if r.position_id and st.position_id is None:
            st.position_id = r.position_id
        if et == "ORDER_ACCEPTED":
            self._cancel_timer(st, "reply")
            if st.state in (State.PENDING_NEW, State.RECONCILING):
                if st.state is State.RECONCILING:
                    st.prev_state = None
                self._set(st, State.WORKING, "accepted", now)
                if st.exec_mode is ExecMode.MAKER and st.expiry_ms:
                    self._arm(st, "expiry", (st.expiry_ms - now) / 1000 + REPLY_TIMEOUT_S, self._expiry_timer)
            if st.want_exit:
                self._request_exit(st, st.want_exit, now)
        elif et in ("ORDER_PARTIAL_FILL", "ORDER_FILLED"):
            self._cancel_timer(st, "reply")
            self._record_entry_fill(st, r, now)
            if et == "ORDER_FILLED" or st.filled >= st.requested:
                self._go_open(st, now)
            else:
                st.prev_state = None
                if st.state is not State.CANCEL_PENDING:
                    self._set(st, State.PARTIAL, f"partial {st.filled}/{st.requested}", now)
        elif et in ("ORDER_CANCELLED", "ORDER_EXPIRED"):
            self._cancel_timer(st, "reply")
            self._cancel_timer(st, "expiry")
            if st.filled > 0:
                self._go_open(st, now)
            else:
                self._entry_dead(st, et.lower(), now)
        elif et == "ORDER_REJECTED":
            self._cancel_timer(st, "reply")
            self.journal.record_risk_event("order_reject", r.error_code or "", st.symbol_id, now)
            if st.filled > 0:
                self._go_open(st, now)
            else:
                self._entry_dead(st, f"rejected {r.error_code or ''}", now)
        elif et == "ORDER_CANCEL_REJECTED":
            self._cancel_rejected(st, now)

    def _cancel_rejected(self, st: _Sym, now: int) -> None:
        """Cancel rejected: the order is probably filling. Wait for the fill (then OPEN)."""
        st.cancel_rejected = True
        self.journal.record_risk_event("cancel_rejected", st.coid or "", st.symbol_id, now)
        self._set(st, State.CANCEL_PENDING, "cancel rejected: waiting for fill", now)
        self._arm(st, "reply", REPLY_TIMEOUT_S, self._reply_timeout, State.CANCEL_PENDING)

    def _record_entry_fill(self, st: _Sym, r: ExecReport, now: int) -> None:
        vol = r.filled_volume
        st.filled += vol
        st.entry_value += (r.price or 0.0) * vol
        st.entry_comm += r.commission or 0
        st.fill_mid = _mid(self._quotes.get(st.symbol_id)) or st.fill_mid
        log.info("fill entry sym=%s %s px=%s vol=%s (%s)", st.symbol_id, r.execution_type, r.price, vol, st.coid)
        self.journal.record_fill(ts_ms=now, symbol_id=st.symbol_id, client_order_id=st.coid,
                                 order_id=r.order_id, position_id=r.position_id, deal=r.execution_type,
                                 side=r.side or st.side, price=r.price, volume=vol, commission=r.commission,
                                 gross=r.gross_profit, swap=r.swap, money_digits=r.money_digits)

    def _go_open(self, st: _Sym, now: int) -> None:
        self._cancel_timer(st, "reply")
        self._cancel_timer(st, "expiry")
        st.prev_state = None
        if st.filled <= 0:
            return self._entry_dead(st, "no fill", now)
        st.entry_ms = now
        adverse = None
        if st.signal_mid and st.fill_mid:
            adverse = st.side.value * (st.fill_mid - st.signal_mid) / st.signal_mid * 1e4
        st.adverse_bps = adverse
        self._set(st, State.OPEN, f"filled {st.filled} @ {st.entry_value / st.filled:.5f}", now)
        self._arm_exit_timer(st, (st.exit_at_ms + EXIT_BACKSTOP_S * 1000 - now) / 1000)
        if st.want_exit:
            self._request_exit(st, st.want_exit, now)

    def _expiry_timer(self, st: _Sym) -> None:
        if st.state in (State.WORKING, State.PARTIAL):
            self._cancel_working(st, "expiry", None, self.clock.now_ms())

    def _cancel_working(self, st: _Sym, why: str, want_exit: Optional[str], now_ms: int) -> None:
        """Cancel the resting order; a filled part stays and is exited on schedule (unless want_exit)."""
        if want_exit:
            st.want_exit = want_exit
        if st.order_id is None:
            return
        self._set(st, State.CANCEL_PENDING, f"cancel for {why}", now_ms)
        self._arm(st, "reply", REPLY_TIMEOUT_S, self._reply_timeout, State.CANCEL_PENDING)
        self._send(st, State.CANCEL_PENDING, lambda: self.broker.cancel(st.order_id))

    # ----- internals: exit ------------------------------------------------------------------------
    def _exit_timer(self, st: _Sym) -> None:
        now = self.clock.now_ms()
        if st.state is not State.OPEN:
            return
        if now < st.exit_at_ms:
            return self._arm_exit_timer(st, (st.exit_at_ms - now) / 1000)
        self._request_exit(st, st.exit_reason or "time", now)

    def _next_exit_coid(self, st: _Sym, now_ms: int) -> str:
        slot_s = now_ms // 1000 // SLOT_S * SLOT_S
        name = st.spec.name if st.spec else st.symbol_id
        for n in range(1000):                       # bounded: never spin on a misbehaving journal
            c = f"{name}-{slot_s}-X{'' if n == 0 else n}"
            if not self.journal.has_client_order_id(c):
                return c
        raise RefusedError("no free exit clientOrderId")

    def _request_exit(self, st: _Sym, reason: str, now_ms: int) -> None:
        if st.state in (State.PENDING_NEW, State.WORKING, State.PARTIAL):
            st.want_exit = reason
            if st.state in (State.WORKING, State.PARTIAL):
                self._cancel_working(st, reason, reason, now_ms)
            return
        if st.state is State.CANCEL_PENDING:
            st.want_exit = reason
            return
        if st.state is not State.OPEN:
            return
        spec = st.spec
        vol = st.filled - st.closed
        st.exit_reason, st.want_exit = reason, None
        st.exit_coid = self._next_exit_coid(st, now_ms)
        st.exit_order_id, st.exit_attempts = None, st.exit_attempts + 1
        self._cancel_timer(st, "exit")
        netted = self.broker.account_type == "NETTED"
        self.journal.record_order(st.exit_coid, ts_ms=now_ms, symbol_id=st.symbol_id,
                                  side=Side.SELL if st.side is Side.BUY else Side.BUY, volume=vol, kind="EXIT",
                                  order_type="MARKET" if netted else "CLOSE", signal_id=st.signal_id,
                                  label=self.settings.label, position_id=st.position_id, status="SENT")
        log.info("exit order sym=%s vol=%s reason=%s (%s)", st.symbol_id, vol, reason, st.exit_coid)
        self._set(st, State.EXIT_PENDING, f"close {vol} ({reason}, {'netted' if netted else 'hedged'})", now_ms)
        self._arm(st, "reply", REPLY_TIMEOUT_S, self._reply_timeout, State.EXIT_PENDING)
        if netted:
            opp = Side.SELL if st.side is Side.BUY else Side.BUY
            self._send(st, State.EXIT_PENDING, lambda: self.broker.place_entry(
                spec, opp, vol, ExecMode.TAKER, None, None, None, st.exit_coid, self.settings.label,
                f"exit:{st.signal_id}"))
        else:
            self._send(st, State.EXIT_PENDING, lambda: self.broker.close(st.position_id, vol))

    def _is_exit_report(self, st: _Sym, r: ExecReport) -> bool:
        if st.state is State.EXIT_PENDING or (st.state is State.RECONCILING and
                                              st.prev_state is State.EXIT_PENDING):
            return True
        return False

    def _on_exit_report(self, st: _Sym, r: ExecReport, now: int) -> None:
        et = r.execution_type
        if st.state is State.OPEN:                   # server-side stop / external close
            st.exit_reason = "stop"
            st.exit_attempts = 0
            self._cancel_timer(st, "exit")
            self._set(st, State.EXIT_PENDING, "closed by server (stop)", now)
        if st.exit_coid:
            self.journal.update_order(st.exit_coid, status=et)
        if r.order_id and st.exit_order_id is None:
            st.exit_order_id = r.order_id
        if et == "ORDER_ACCEPTED":
            self._cancel_timer(st, "reply")
            if st.state is State.RECONCILING:
                st.prev_state = None
                self._set(st, State.EXIT_PENDING, "accepted", now)
        elif et in ("ORDER_PARTIAL_FILL", "ORDER_FILLED"):
            self._cancel_timer(st, "reply")
            if st.state is State.RECONCILING:
                st.prev_state = None
                self._set(st, State.EXIT_PENDING, "fill while reconciling", now)
            vol = r.filled_volume
            st.closed += vol
            st.exit_value += (r.price or 0.0) * vol
            st.exit_ms = now
            if r.gross_profit is not None:
                st.exit_gross = (st.exit_gross or 0) + r.gross_profit
            st.exit_comm += r.commission or 0
            st.exit_swap += r.swap or 0
            st.exit_md = r.money_digits or st.exit_md
            log.info("fill exit sym=%s %s px=%s vol=%s (%s)", st.symbol_id, et, r.price, vol, st.exit_coid)
            self.journal.record_fill(ts_ms=now, symbol_id=st.symbol_id, client_order_id=st.exit_coid,
                                     order_id=r.order_id, position_id=r.position_id, deal=et,
                                     side=r.side, price=r.price, volume=vol, commission=r.commission,
                                     gross=r.gross_profit, swap=r.swap, money_digits=r.money_digits)
            if et == "ORDER_FILLED" or st.closed >= st.filled:
                self._finalize(st, now)
        elif et in ("ORDER_REJECTED", "ORDER_CANCELLED", "ORDER_EXPIRED"):
            self._cancel_timer(st, "reply")
            self.journal.record_risk_event("order_reject", f"exit {et} {r.error_code or ''}", st.symbol_id, now)
            if st.closed >= st.filled > 0:
                self._finalize(st, now)
            else:
                self._exit_failed(st, et.lower(), now)

    def _exit_failed(self, st: _Sym, why: str, now: int) -> None:
        """Close did not go through: stay OPEN and retry (5 s, then 30 s after 5 attempts)."""
        self._cancel_timer(st, "reply")
        st.prev_state = None
        self._set(st, State.OPEN, f"exit failed ({why}), attempt {st.exit_attempts}", now)
        if st.exit_attempts >= 5:
            self.journal.record_risk_event("exit_failing", f"{st.exit_attempts} attempts", st.symbol_id, now)
        st.exit_at_ms = min(st.exit_at_ms, now)
        self._arm_exit_timer(st, 5 if st.exit_attempts < 5 else 30)

    def _finalize(self, st: _Sym, now: int) -> None:
        """Position is flat: write the trade, go CLOSED then IDLE, run a pending flip entry."""
        for k in list(st.timers):
            self._cancel_timer(st, k)
        sign = st.side.value
        entry = st.entry_value / st.filled if st.filled else None
        exit_p = st.exit_value / st.closed if st.closed else None
        q = self._quotes.get(st.symbol_id)
        exit_mid = _mid(q)
        pnl_price = sign * (exit_p - entry) / entry * 1e4 if entry and exit_p else None
        pnl_mid = sign * (exit_mid - st.signal_mid) / st.signal_mid * 1e4 if exit_mid and st.signal_mid else None
        markout = sign * (exit_mid - st.fill_mid) / st.fill_mid * 1e4 if exit_mid and st.fill_mid else None
        md = st.exit_md if st.exit_md is not None else getattr(self.broker, "money_digits", 2)
        scale = 10 ** md
        net = ((st.exit_gross or 0) + st.entry_comm + st.exit_comm + st.exit_swap) / scale \
            if st.exit_gross is not None else None
        self.journal.record_trade(
            symbol_id=st.symbol_id, side=st.side, volume=st.filled, signal_id=st.signal_id,
            client_order_id=st.coid, position_id=st.position_id, entry_ms=st.entry_ms, exit_ms=now,
            entry_price=entry, exit_price=exit_p, reason=st.exit_reason or "time", pnl_bps_mid=pnl_mid,
            pnl_bps_price=pnl_price, pnl_net=net,
            gross=None if st.exit_gross is None else st.exit_gross / scale,
            commission=(st.entry_comm + st.exit_comm) / scale, swap=st.exit_swap / scale, money_digits=md,
            adverse_bps=st.adverse_bps, markout_bps=markout)
        log.info("trade closed sym=%s %s reason=%s pnl_bps_price=%s pnl_net=%s", st.symbol_id, st.side.name,
                 st.exit_reason or "time", None if pnl_price is None else round(pnl_price, 2), net)
        self._set(st, State.CLOSED, f"closed {st.exit_reason}", now)
        flip = st.pending_flip
        st.pending_flip = None
        st.filled = st.closed = 0
        st.entry_value = 0.0
        st.position_id = st.order_id = st.coid = st.exit_coid = None
        self._set(st, State.IDLE, "", now)
        if flip is not None:
            self._enter(st, flip, st.flip_now_ms or now)

    # ----- reconcile ------------------------------------------------------------------------------
    def _reconcile(self, st: _Sym) -> None:
        d = defer.maybeDeferred(self.broker.reconcile)
        d.addCallbacks(lambda res: self._reconciled(st, *res), lambda f: self._reconcile_failed(st, f))

    def _reconcile_failed(self, st: _Sym, failure) -> None:
        self.journal.record_risk_event("reconcile_failed", str(failure.value), st.symbol_id)
        if st.state is State.RECONCILING:
            self._arm(st, "reply", REPLY_TIMEOUT_S, lambda s: self._reconcile(s))

    def _reconciled(self, st: _Sym, positions: list, orders: list) -> None:
        if st.state is not State.RECONCILING:
            return
        now = self.clock.now_ms()
        label = self.settings.label
        prev, st.prev_state = st.prev_state, None
        mine = [p for p in positions if p.label == label and p.symbol_id == st.symbol_id]
        if prev is State.EXIT_PENDING:
            p = next((p for p in mine if st.position_id in (None, p.position_id)), None)
            if p is not None:
                st.filled = st.closed + p.volume
                self._exit_failed(st, "not closed per reconcile", now)
            else:
                self.journal.record_risk_event("exit_unconfirmed", "position gone, fill report lost",
                                               st.symbol_id, now)
                if st.closed == 0:
                    st.closed, st.exit_value = st.filled, 0.0   # exit price unknown
                st.exit_ms = now
                self._finalize(st, now)
            return
        o = next((o for o in orders if o.client_order_id == st.coid), None)
        if o is not None:
            st.order_id = o.order_id
            if prev is State.CANCEL_PENDING:
                self._set(st, State.CANCEL_PENDING, "reconciled: order still resting, re-cancel", now)
                self._arm(st, "reply", REPLY_TIMEOUT_S, self._reply_timeout, State.CANCEL_PENDING)
                self._send(st, State.CANCEL_PENDING, lambda: self.broker.cancel(o.order_id))
            else:
                self._set(st, State.WORKING, "reconciled: found by clientOrderId", now)
                if st.exec_mode is ExecMode.MAKER and st.expiry_ms:
                    self._arm(st, "expiry", (st.expiry_ms - now) / 1000 + REPLY_TIMEOUT_S, self._expiry_timer)
                if st.want_exit:
                    self._request_exit(st, st.want_exit, now)
            return
        p = next((p for p in mine if st.position_id in (None, p.position_id)), None)
        if p is not None:
            st.position_id = p.position_id
            st.entry_value, st.filled = p.price * p.volume, p.volume
            st.fill_mid = _mid(self._quotes.get(st.symbol_id))
            self._go_open(st, now)
        elif st.filled > 0:
            self._go_open(st, now)
        else:
            self.journal.record_risk_event("order_lost", st.coid or "", st.symbol_id, now)
            self._entry_dead(st, "reconciled: order not found, not resending", now)

    def _on_error(self, r: ExecReport, now: int) -> None:
        """OrderErrorEvent has orderId/positionId only; attribute it, else reconcile the candidates."""
        st = None
        for s in self._syms.values():
            if (r.order_id is not None and r.order_id in (s.order_id, s.exit_order_id)) or \
                    (r.position_id is not None and r.position_id == s.position_id):
                st = s
                break
        cands = [st] if st else [s for s in self._syms.values() if
                                 (s.state is State.PENDING_NEW and s.order_id is None) or
                                 (s.state is State.EXIT_PENDING and s.exit_order_id is None) or
                                 s.state is State.CANCEL_PENDING]
        self.journal.record_risk_event("order_reject", f"order_error {r.error_code}", cands[0].symbol_id
                                       if len(cands) == 1 else None, now)
        if not cands:
            return
        if len(cands) > 1:
            for s in cands:
                s.prev_state = s.state
                self._set(s, State.RECONCILING, "ambiguous order error", now)
                self._reconcile(s)
            return
        s = cands[0]
        self._cancel_timer(s, "reply")
        if s.state is State.PENDING_NEW:
            self._entry_dead(s, f"order error {r.error_code}", now)
        elif s.state is State.EXIT_PENDING:
            self._exit_failed(s, f"order error {r.error_code}", now)
        elif s.state is State.CANCEL_PENDING:
            self._cancel_rejected(s, now)
        elif s.state in (State.WORKING, State.PARTIAL):
            self._note(s, now, f"order error {r.error_code}")

    # ----- startup --------------------------------------------------------------------------------
    def _startup_apply(self, res, specs: dict) -> None:
        positions, orders = res
        now = self.clock.now_ms()
        label = self.settings.label
        for o in orders:
            if o.label == label:
                self.journal.record_risk_event("startup_cancel_order", f"{o.client_order_id} {o.order_id}",
                                               o.symbol_id, now)
                self._send_raw(lambda o=o: self.broker.cancel(o.order_id), o.symbol_id)
        for p in positions:
            if p.label != label:
                continue
            st = self._sym(p.symbol_id, specs.get(p.symbol_id))
            if st.state is not State.IDLE:
                self.journal.record_risk_event("startup_extra_position", f"close {p.position_id}",
                                               p.symbol_id, now)
                self._send_raw(lambda p=p: self.broker.close(p.position_id, p.volume), p.symbol_id)
                continue
            opened = p.open_ms or now
            st.side, st.filled, st.requested, st.closed = p.side, p.volume, p.volume, 0
            st.entry_value, st.entry_comm = p.price * p.volume, 0
            st.position_id, st.entry_ms = p.position_id, opened
            st.coid = f"adopted-{p.position_id}"
            st.signal_id, st.signal_mid, st.fill_mid = "adopted", None, None
            st.exit_at_ms = (opened // 1000 // SLOT_S * SLOT_S + SLOT_S + self.settings.grace_s) * 1000
            st.exit_attempts = 0
            self._set(st, State.OPEN, f"adopted position {p.position_id}", now)
            if now >= st.exit_at_ms:
                if st.spec is None and self.broker.account_type == "NETTED":
                    self.journal.record_risk_event("startup_no_spec", "cannot close netted without spec",
                                                   p.symbol_id, now)
                    self._arm_exit_timer(st, 5)
                else:
                    self._request_exit(st, "time", now)
            else:
                self._arm_exit_timer(st, (st.exit_at_ms + EXIT_BACKSTOP_S * 1000 - now) / 1000)

    def _send_raw(self, call: Callable[[], defer.Deferred], symbol_id: int) -> None:
        try:
            d = call()
        except Exception as e:                        # noqa: BLE001
            self.journal.record_risk_event("send_failed", f"{type(e).__name__}: {e}", symbol_id)
            return
        if isinstance(d, defer.Deferred):
            d.addErrback(lambda f: self.journal.record_risk_event("send_failed", str(f.value), symbol_id))
