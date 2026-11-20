"""Shared fixtures for execution/simbroker tests."""
from twisted.internet import defer

from autotrade.clock import TestClock
from autotrade.config import Settings
from autotrade.execution import OrderManager
from autotrade.journal import Journal
from autotrade.types import (ExecMode, ExecReport, Intent, IntentKind, Quote, RunMode, Side, SymbolSpec)

SLOT = 1_800_000_000          # multiple of 300
LABEL = "axq-poc4h-v1"
SPEC = SymbolSpec("XAUUSD", 41, 2, 1, 10000, 100, 100, 100000, 10, "POINTS", [], "UTC", [])
SPEC2 = SymbolSpec("BTCUSD", 42, 2, 1, 100, 1, 1, 10000, 10, "POINTS", [], "UTC", [])


class StubBroker:
    name = "demo"
    money_digits = 2

    def __init__(self, account_type="HEDGED"):
        self.account_type = account_type
        self.calls = []
        self.recon = ([], [])
        self.fail = None

    def _ret(self):
        return defer.fail(self.fail) if self.fail else defer.succeed(None)

    def place_entry(self, spec, side, volume, mode, limit_price, expiry_ms, stop_rel, coid, label, comment):
        self.calls.append(("place", dict(spec=spec, side=side, volume=volume, mode=mode, limit=limit_price,
                                          expiry=expiry_ms, stop=stop_rel, coid=coid, label=label,
                                          comment=comment)))
        return self._ret()

    def cancel(self, order_id):
        self.calls.append(("cancel", order_id))
        return self._ret()

    def close(self, position_id, volume):
        self.calls.append(("close", position_id, volume))
        return self._ret()

    def reconcile(self):
        self.calls.append(("reconcile",))
        return defer.succeed(self.recon)

    def of(self, kind):
        return [c for c in self.calls if c[0] == kind]


def rep(et, **kw):
    d = dict(execution_type=et, order_id=None, position_id=None, client_order_id=None, label=LABEL,
             symbol_id=41, side=None, filled_volume=0, price=None, status=None, error_code=None,
             gross_profit=None, commission=None, swap=None, money_digits=None, ts_ms=0)
    d.update(kw)
    return ExecReport(**d)


def intent(kind=IntentKind.ENTER, side=Side.BUY, sym=41, mult=1.0, limit=None, expiry=None, stop=50000,
           reason="signal", sid="sig1"):
    return Intent(kind, sym, side, mult, limit, expiry, stop, reason, sid)


def quote(bid=200000000, ask=200010000, ts=0, sym=41):
    return Quote(sym, bid, ask, ts)


def make(tmp_path, broker=None, mode=RunMode.DEMO, exec_mode=ExecMode.TAKER, **settings):
    clock = TestClock((SLOT + 2) * 1000)
    j = Journal(tmp_path / "t.db", tmp_path / "t.jsonl")
    j.start_run(mode, Settings(**settings))
    broker = broker or StubBroker()
    om = OrderManager(broker, j, clock, Settings(**settings), mode, exec_mode)
    return om, broker, j, clock
