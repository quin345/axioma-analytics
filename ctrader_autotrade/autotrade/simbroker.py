"""Dry-run broker: fills from live quotes, no order ever leaves the process.

Emits synthetic ExecReports through `listener` (normally OrderManager.on_report) so the same state
machine runs in dry-run. Driven by `on_quote(Quote)`. Fill model:

- market: buy at ask, sell at bid of the latest quote;
- maker limit buy at L fills at L on the first later quote with ask <= L (cTrader semantics);
  `fill_notes[client_order_id]["trade_through"]` says whether it was also strictly ask < L (the
  stricter variant a real queue position would need). Sell mirrors with bid >= L / bid > L;
- GTD: a resting order expires on the first quote at or after expiry_ms (or tick(now_ms));
- the server-side stop is simulated on quotes (buy stop hit when bid <= stop, sell when ask >= stop;
  fills at the quote, so gaps cost extra);
- gross profit is approximated as price_diff * volume / 100 in a USD-quoted account (volume is in
  1/100 of a base unit), scaled by 10**money_digits. Commission and swap are zero.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable, Optional

from twisted.internet import defer

from .clock import Clock
from .execution import PRICE_SCALE, BrokerOrder, BrokerPosition, price_to_float, round_price
from .types import ExecMode, ExecReport, Quote, Side, SymbolSpec


@dataclass
class _Order:
    order_id: int
    spec: SymbolSpec
    side: Side
    volume: int
    mode: ExecMode
    limit: Optional[int]
    expiry_ms: Optional[int]
    stop_rel: Optional[int]
    coid: str
    label: str
    remaining: int = 0
    first_fill_done: bool = False


@dataclass
class _Pos:
    position_id: int
    spec: SymbolSpec
    side: Side
    volume: int
    price: int
    label: str
    open_ms: int
    stop: Optional[int] = None


class SimBroker:
    name = "sim"

    def __init__(self, clock: Clock, account_type: str = "HEDGED", money_digits: int = 2,
                 listener: Optional[Callable[[ExecReport], None]] = None,
                 partial_fraction: Optional[float] = None):
        self.clock = clock
        self.account_type = account_type
        self.money_digits = money_digits
        self.listener = listener
        self.partial_fraction = partial_fraction      # fill a limit order in two chunks (testing)
        self.quotes: dict[int, Quote] = {}
        self.orders: dict[int, _Order] = {}
        self.positions: dict[int, _Pos] = {}
        self.fill_notes: dict[str, dict] = {}
        self._ids = itertools.count(1)

    # ----- Broker interface -----------------------------------------------------------------------
    def place_entry(self, spec, side, volume, mode, limit_price, expiry_ms, stop_rel,
                    client_order_id, label, comment) -> defer.Deferred:
        o = _Order(next(self._ids), spec, side, volume, mode,
                   None if limit_price is None else round_price(spec, limit_price), expiry_ms, stop_rel,
                   client_order_id, label, remaining=volume)
        events = [self._report("ORDER_ACCEPTED", o, status="ORDER_STATUS_ACCEPTED")]
        q = self.quotes.get(spec.symbol_id)
        if mode is ExecMode.TAKER:
            if q is None:
                events = [self._report("ORDER_REJECTED", o, status="ORDER_STATUS_REJECTED",
                                       error="NO_QUOTE")]
            else:
                events += self._fill(o, q.ask if side is Side.BUY else q.bid, o.volume, final=True)
        else:
            self.orders[o.order_id] = o
            if q is not None:
                events += self._try_limit(o, q)
        self._emit(events)
        return defer.succeed(None)

    def cancel(self, order_id: int) -> defer.Deferred:
        o = self.orders.pop(order_id, None)
        if o is None:
            self._emit([ExecReport("ORDER_CANCEL_REJECTED", order_id, None, None, None, None, None, 0, None,
                                   None, "ORDER_NOT_FOUND", None, None, None, None, self.clock.now_ms())])
        else:
            self._emit([self._report("ORDER_CANCELLED", o, status="ORDER_STATUS_CANCELLED")])
        return defer.succeed(None)

    def close(self, position_id: int, volume: int) -> defer.Deferred:
        p = self.positions.get(position_id)
        if p is None:
            self._emit([ExecReport("ORDER_REJECTED", None, position_id, None, None, None, None, 0, None, None,
                                   "POSITION_NOT_FOUND", None, None, None, None, self.clock.now_ms())])
            return defer.succeed(None)
        q = self.quotes.get(p.spec.symbol_id)
        if q is None:
            self._emit([ExecReport("ORDER_REJECTED", None, position_id, None, p.label, p.spec.symbol_id, None,
                                   0, None, None, "NO_QUOTE", None, None, None, None, self.clock.now_ms())])
            return defer.succeed(None)
        price = q.bid if p.side is Side.BUY else q.ask
        self._emit(self._close_fill(p, min(volume, p.volume), price, next(self._ids), None))
        return defer.succeed(None)

    def reconcile(self) -> defer.Deferred:
        pos = [BrokerPosition(p.position_id, p.spec.symbol_id, p.side, p.volume, p.price / PRICE_SCALE,
                              p.label, p.open_ms) for p in self.positions.values()]
        ords = [BrokerOrder(o.order_id, o.spec.symbol_id, o.side, o.remaining, o.coid, o.label, "LIMIT",
                            o.expiry_ms, None if o.limit is None else o.limit / PRICE_SCALE)
                for o in self.orders.values()]
        return defer.succeed((pos, ords))

    # ----- market data ----------------------------------------------------------------------------
    def on_quote(self, q: Quote) -> None:
        self.quotes[q.symbol_id] = q
        events: list[ExecReport] = []
        for o in [o for o in self.orders.values() if o.spec.symbol_id == q.symbol_id]:
            if o.order_id not in self.orders:
                continue
            if o.expiry_ms is not None and q.ts_ms >= o.expiry_ms:
                del self.orders[o.order_id]
                events.append(self._report("ORDER_EXPIRED", o, status="ORDER_STATUS_EXPIRED", ts=q.ts_ms))
            else:
                events += self._try_limit(o, q)
        for p in [p for p in self.positions.values() if p.spec.symbol_id == q.symbol_id]:
            if p.stop is None or p.position_id not in self.positions:
                continue
            if (p.side is Side.BUY and q.bid <= p.stop) or (p.side is Side.SELL and q.ask >= p.stop):
                price = q.bid if p.side is Side.BUY else q.ask
                events += self._close_fill(p, p.volume, price, next(self._ids), None, ts=q.ts_ms)
        self._emit(events)

    def tick(self, now_ms: int) -> None:
        """Expire GTD orders by wall clock when no quote arrives."""
        events = []
        for o in [o for o in self.orders.values() if o.expiry_ms is not None and now_ms >= o.expiry_ms]:
            del self.orders[o.order_id]
            events.append(self._report("ORDER_EXPIRED", o, status="ORDER_STATUS_EXPIRED", ts=now_ms))
        self._emit(events)

    # ----- internals ------------------------------------------------------------------------------
    def _emit(self, events: list) -> None:
        if self.listener:
            for e in events:
                self.listener(e)

    def _report(self, et: str, o: _Order, status: Optional[str] = None, error: Optional[str] = None,
                price: Optional[float] = None, vol: int = 0, ts: Optional[int] = None,
                position_id: Optional[int] = None, gross: Optional[int] = None) -> ExecReport:
        return ExecReport(et, o.order_id, position_id, o.coid, o.label, o.spec.symbol_id, o.side, vol, price,
                          status, error, gross, 0 if gross is not None or vol else None,
                          0 if gross is not None else None, self.money_digits, ts or self.clock.now_ms())

    def _try_limit(self, o: _Order, q: Quote) -> list[ExecReport]:
        if o.side is Side.BUY:
            hit, through = q.ask <= o.limit, q.ask < o.limit
        else:
            hit, through = q.bid >= o.limit, q.bid > o.limit
        if not hit:
            return []
        note = self.fill_notes.setdefault(o.coid, {"trade_through": through, "fill_ts_ms": q.ts_ms})
        note["trade_through"] = note["trade_through"] or through
        chunk = o.remaining
        if self.partial_fraction and not o.first_fill_done and o.remaining > o.spec.step_volume:
            step = max(o.spec.step_volume, 1)
            chunk = max(step, int(o.remaining * self.partial_fraction) // step * step)
            chunk = min(chunk, o.remaining - step) if o.remaining > step else o.remaining
        o.first_fill_done = True
        return self._fill(o, o.limit, chunk, final=chunk >= o.remaining, ts=q.ts_ms)

    def _fill(self, o: _Order, price: int, vol: int, final: bool, ts: Optional[int] = None) -> list[ExecReport]:
        o.remaining -= vol
        if final or o.remaining <= 0:
            self.orders.pop(o.order_id, None)
        spec, pf = o.spec, price_to_float(o.spec, price)
        net = self.account_type == "NETTED"
        existing = next((p for p in self.positions.values() if p.spec.symbol_id == spec.symbol_id), None) \
            if net else None
        if existing is not None and existing.side is not o.side:     # netted: opposite order closes
            return self._close_fill(existing, min(vol, existing.volume), price, o.order_id, o, ts=ts)
        if existing is not None:
            existing.price = (existing.price * existing.volume + price * vol) // (existing.volume + vol)
            existing.volume += vol
            pid = existing.position_id
        else:
            pid = next(self._ids)
            stop = None
            if o.stop_rel:
                stop = price - o.stop_rel if o.side is Side.BUY else price + o.stop_rel
            self.positions[pid] = _Pos(pid, spec, o.side, vol, price, o.label, ts or self.clock.now_ms(), stop)
        return [self._report("ORDER_FILLED" if (final or o.remaining <= 0) else "ORDER_PARTIAL_FILL", o,
                             status="ORDER_STATUS_FILLED" if (final or o.remaining <= 0) else
                             "ORDER_STATUS_ACCEPTED", price=pf, vol=vol, ts=ts, position_id=pid)]

    def _close_fill(self, p: _Pos, vol: int, price: int, order_id: int, o: Optional[_Order],
                    ts: Optional[int] = None) -> list[ExecReport]:
        sign = 1 if p.side is Side.BUY else -1
        gross = int(round(sign * (price - p.price) / PRICE_SCALE * vol / 100 * 10 ** self.money_digits))
        p.volume -= vol
        if p.volume <= 0:
            del self.positions[p.position_id]
        closing_side = Side.SELL if p.side is Side.BUY else Side.BUY
        return [ExecReport("ORDER_FILLED", order_id, p.position_id, o.coid if o else None, p.label,
                           p.spec.symbol_id, closing_side, vol, price_to_float(p.spec, price),
                           "ORDER_STATUS_FILLED", None, gross, 0, 0, self.money_digits,
                           ts or self.clock.now_ms())]
