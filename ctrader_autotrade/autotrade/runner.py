"""The runnable bot: wires session, warm-up, bars/features, strategy, risk and execution.

Lifecycle (DESIGN.md sections 3-5):

1. `Bot.start()` starts the session. Every (re)connect handshake ends in `_on_ready`.
2. First `_on_ready`: journal run record, broker (SimBroker in DRY_RUN, DemoBroker in DEMO and only
   when the session is a verified demo), startup reconcile (DEMO), warm-up (stored buckets plus tick
   gap fill), bars and features rebuilt for the last HISTORY_SLOTS slots, then subscribe.
   Later `_on_ready` calls (reconnects) only gap-fill ticks and, when the order manager is idle,
   reconcile again.
3. Live: spot/depth feed the order manager, the sim broker, the stamper and the bucketiser.
4. Slot timer at every 5 minute boundary + grace_s: close the bar, append features, decide, check
   risk, hand the intent to the order manager and journal one signal row (with the taker and
   maker-sim shadows, in both modes). A 5 s risk tick handles KILL, session close and long
   disconnects between slots.
5. Shutdown (SIGTERM/SIGINT, KILL file, SAFE, refusal, --hours): stop entries, cancel own resting
   orders, flatten, flush, record the end of the run, fire `Bot.done` with the exit code
   (0 ok, 2 refused, 3 SAFE).
"""
from __future__ import annotations

import dataclasses
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from twisted.internet import defer
from twisted.internet.defer import inlineCallbacks

from ctrader_open_api.messages import OpenApiMessages_pb2 as oa

from .bars import VAP_MINUTES, BarBuilder, Bucketiser, replay_bars
from .book import Stamper
from .execution import DemoBroker, OrderManager
from .features import FeatureEngine
from .journal import Journal
from . import killswitch
from .resetday import day_start_ms, next_reset_ms
from .risk import RiskManager, SessionCalendar
from .session import Session
from .simbroker import SimBroker
from .strategy import decide, signal_id, signal_side, taker_shadow
from .types import (Bucket, DepthRow, ExecMode, Intent, IntentKind, Quote, RiskContext, RunMode,
                    Side)
from .warmup import warmup

log = logging.getLogger(__name__)
NAN = float("nan")

SLOT_MS = 300_000
PRICE_SCALE = 100_000
HISTORY_SLOTS = 150            # bars rebuilt at start-up (FeatureEngine window)
RISK_TICK_S = 5
SHUTDOWN_WAIT_S = 25           # systemd TimeoutStopSec is 45
PERSIST_BACK_MS = 3 * SLOT_MS  # buckets re-saved at each slot close
KEEP_BUCKETS_MS = 2 * 86_400_000
EXIT_OK, EXIT_REFUSED, EXIT_SAFE = 0, 2, 3
PARITY_MARKER = "PARITY_OK"    # state/PARITY_OK: the approximate-bar gate (DESIGN 4) was reviewed


@dataclass
class _Shadow:
    """Signal shadows for one symbol, resolved one bar later."""
    signal_id: str
    side: Side
    q0: Quote
    limit: int
    deadline_ms: int
    maker: Optional[str] = None      # None while open, then "fill" | "no_fill"
    fill_price: Optional[float] = None


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _finite(x) -> bool:
    return x is not None and not math.isnan(x) and not math.isinf(x)


class Bot:
    def __init__(self, settings, mode: RunMode, exec_mode: ExecMode, clock, transport,
                 journal: Optional[Journal] = None, hours: Optional[float] = None,
                 history_slots: int = HISTORY_SLOTS, engine: Optional[FeatureEngine] = None):
        self.settings = dataclasses.replace(settings, exec=exec_mode.value)
        self.mode, self.exec_mode = mode, exec_mode
        self.clock = clock
        self.hours = hours
        self.history_slots = history_slots
        self.journal = journal or Journal.from_settings(self.settings)
        self.journal.use_daily_reset(self.settings)
        self.session = Session(self.settings, transport, clock)
        self.risk = RiskManager(self.settings)
        self.engine = engine or FeatureEngine()
        self.stamper = Stamper()
        self.bucketiser = Bucketiser()
        self.builder = BarBuilder(self.bucketiser)
        self.om: Optional[OrderManager] = None
        self.broker = None
        self.specs: dict = {}
        self.calendars: dict[int, SessionCalendar] = {}
        self.last_quote: dict[int, Quote] = {}
        self.last_data_ms: dict[int, int] = {}
        self._bid: dict[int, int] = {}
        self._ask: dict[int, int] = {}
        self._vol: dict[int, list] = {}
        self._warm_buckets: dict[int, list] = {}   # first warm-up only: all buckets, for the replay
        self._warm_start_ms = 0
        self._shadows: dict[int, _Shadow] = {}
        self.exit_code = EXIT_OK
        self.done: defer.Deferred = defer.Deferred()
        self.initialised = False     # first on_ready finished
        self.trading_ready = False   # connected, reconciled, subscribed
        self._run_started = False
        self._stopping = False
        self._finished = False
        self._waiters: list = []
        self._calls: dict = {}
        self._last_slot_ms = 0
        self._down_since: Optional[int] = None
        self._pending_outage_s: Optional[float] = None
        self._ready_ms: Optional[int] = None
        self._balance: Optional[float] = None
        self._balance_pnl_ref = 0.0
        self._kill_busy = False          # a kill-switch check (maybe a server request) is in flight
        self._kill_profit_done = False   # profit_target already acted on (pause) while it stays reached
        self._kill_daily_day: Optional[int] = None   # day start for which daily_max_loss already fired
        self._unreal_warned = False      # quote asset != deposit asset warning, once
        self._unreal_err = False         # server unrealised request failed, warning once per streak

        s = self.session
        s.on_ready = self._on_ready
        s.on_spot = self._on_spot
        s.on_depth = self._on_depth
        s.on_execution = self._on_execution
        s.on_order_error = self._on_order_error
        s.on_disconnected = self._on_disconnected
        s.on_refused = self._on_refused
        s.on_safe = self._on_safe

    # ---- lifecycle ----

    def start(self) -> defer.Deferred:
        """Start connecting; the returned Deferred fires with the exit code when the bot has stopped."""
        if self.hours:
            self._calls["hours"] = self.clock.call_later(self.hours * 3600, self.shutdown, "hours")
        self.session.start()
        return self.done

    def shutdown(self, reason: str, code: Optional[int] = None, flatten: Optional[bool] = None,
                 orders: bool = True) -> defer.Deferred:
        """Stop new entries, cancel resting orders, flatten, flush. Idempotent; the Deferred fires
        with the exit code when the bot is finished."""
        d: defer.Deferred = defer.Deferred()
        if self._finished:
            d.callback(self.exit_code)
            return d
        self._waiters.append(d)
        if self._stopping:
            return d
        self._stopping = True
        self.trading_ready = False
        if code is not None:
            self.exit_code = code
        log.info("shutdown: %s", reason)
        self._ensure_run(None)
        self.journal.log("shutdown", reason=reason, exit_code=self.exit_code)
        for k in ("slot", "risk", "hours", "kill"):
            c = self._calls.pop(k, None)
            if c is not None and c.active():
                c.cancel()
        do_flatten = False
        if self.om is not None and orders:
            do_flatten = (self.settings.close_on_shutdown or self.mode is RunMode.DRY_RUN
                          if flatten is None else flatten)
            try:
                if do_flatten:
                    self.om.flatten_all(reason if reason in ("kill",) else "shutdown")
                else:
                    self.om.cancel_working("shutdown")
            except Exception:
                log.exception("flatten at shutdown failed")
        self._wait_settled(self.clock.now_ms() + SHUTDOWN_WAIT_S * 1000, do_flatten)
        return d

    def _settled(self, flatten: bool) -> bool:
        if self.om is None:
            return True
        if flatten:
            return self.om.open_count() == 0
        from .execution import State
        return not any(self.om.state_of(sid) in (State.PENDING_NEW, State.WORKING, State.CANCEL_PENDING)
                       for sid in self.specs)

    def _wait_settled(self, deadline_ms: int, flatten: bool) -> None:
        if self._settled(flatten) or self.clock.now_ms() >= deadline_ms:
            if not self._settled(flatten):
                self.journal.record_risk_event("shutdown_unsettled", "positions/orders not confirmed closed")
            return self._finish()
        self._calls["settle"] = self.clock.call_later(0.5, self._wait_settled, deadline_ms, flatten)

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        try:
            self._persist_buckets(back_ms=None)
            self.journal.end_run()
            self.journal.close()
        except Exception:
            log.exception("journal flush failed")
        self.session.stop()
        for d in self._waiters:
            if not d.called:
                d.callback(self.exit_code)
        self._waiters.clear()
        if not self.done.called:
            self.done.callback(self.exit_code)

    # ---- session callbacks ----

    def _ensure_run(self, is_live: Optional[bool]) -> None:
        if self._run_started:
            return
        self._run_started = True
        info = self.session.trader_info
        broker = f"{self.broker.name if self.broker else 'none'}/{info.get('broker_name') or '?'}"
        self.journal.start_run(self.mode, self.settings, account_id=self.session.account_id,
                               is_live=is_live, broker=broker)

    def _on_refused(self, reason: str) -> None:
        self._ensure_run(True if "LIVE" in reason else None)
        self.journal.record_risk_event("refused", reason)
        log.error("refused: %s", reason)
        self.shutdown("refused", code=EXIT_REFUSED, flatten=False, orders=False)

    def _on_safe(self, reason: str) -> None:
        self._ensure_run(None)
        self.journal.record_risk_event("safe", reason)
        # trading messages are refused in SAFE mode: nothing can be cancelled or closed from here
        self.shutdown("safe", code=EXIT_SAFE, flatten=False, orders=False)

    def _on_disconnected(self, reason: str) -> None:
        self.trading_ready = False
        if self._down_since is None:
            self._down_since = self.clock.now_ms()
        for sid in self.specs:          # the resubscribe brings a fresh snapshot: drop stale quote ids
            book = self.stamper.book(sid)
            if book is not None:
                book.clear()
        self._bid.clear()
        self._ask.clear()
        if self._run_started and not self._stopping:
            self.journal.record_risk_event("disconnected", reason)

    @inlineCallbacks
    def _on_ready(self, specs, trader_info):
        if self._stopping:
            return
        now = self.clock.now_ms()
        self.specs = dict(specs)
        self._ready_ms = now
        if self._down_since is not None:
            self._pending_outage_s = (now - self._down_since) / 1000
            self.journal.record_risk_event("reconnected", f"outage {self._pending_outage_s:.1f}s")
            self._down_since = None
        first = not self.initialised
        if self.mode is RunMode.DEMO and not self.session.verified_demo:
            self._ensure_run(None)
            self.journal.record_risk_event("refused", "DEMO mode needs a verified demo account")
            self.shutdown("refused", code=EXIT_REFUSED, flatten=False, orders=False)
            return
        if first:
            self._build_broker(trader_info)
            self._ensure_run(False)
        for sid, spec in self.specs.items():
            self.calendars[sid] = SessionCalendar(spec)
        self._set_balance(trader_info)
        if self.mode is RunMode.DEMO and self.om.open_count() == 0:
            try:
                yield self.om.startup_reconcile(self.specs)
            except Exception as exc:
                log.error("startup reconcile failed: %s", exc)
                self.journal.record_risk_event("reconcile_failed", str(exc))
        if self._check_kill():           # KILL present at start (or on a reconnect): flatten, exit 0
            return
        try:
            yield self._warmup(now, first)
        except Exception as exc:
            log.error("warm-up failed: %s", exc)
            self.journal.record_risk_event("warmup_failed", f"{type(exc).__name__}: {exc}")
        if first:
            self._rebuild_bars(now)
            self.initialised = True
            self._arm_slot()
            self._arm_risk()
            if self.settings.kill_enabled:
                self._calls["kill"] = self.clock.call_later(0, self._kill_tick)
            self.clock.call_later(0, self._first_subscribe)
        self.trading_ready = True

    def _first_subscribe(self) -> None:
        d = self.session.subscribe()
        d.addErrback(lambda f: (log.error("subscribe failed: %s", f.getErrorMessage()),
                                self.journal.record_risk_event("subscribe_failed", f.getErrorMessage())))

    def _build_broker(self, trader_info: dict) -> None:
        if self.mode is RunMode.DEMO:
            self.broker = DemoBroker(self.session)
        else:
            self.broker = SimBroker(self.clock, account_type=trader_info.get("account_type") or "HEDGED",
                                    money_digits=trader_info.get("money_digits") or 2)
        self.om = OrderManager(self.broker, self.journal, self.clock, self.settings, self.mode,
                               self.exec_mode)
        if self.mode is RunMode.DRY_RUN:
            self.broker.listener = self.om.on_report

    def _set_balance(self, trader_info: dict) -> None:
        bal, md = trader_info.get("balance"), trader_info.get("money_digits") or 2
        self._balance = None if bal is None else bal / 10 ** md
        self._balance_pnl_ref = self.journal.pnl_net_total()

    def _on_execution(self, raw) -> None:
        if self.mode is RunMode.DEMO and self.om is not None:
            self.om.on_execution_event(raw)

    def _on_order_error(self, raw) -> None:
        if self.mode is RunMode.DEMO and self.om is not None:
            self.om.on_order_error(raw)

    # ---- warm-up ----

    @inlineCallbacks
    def _warmup(self, now: int, first: bool):
        self._persist_buckets(back_ms=None)
        hours = self.warmup_hours()
        self._warm_start_ms = now - int(hours * 3600_000)
        out = yield warmup(self.session, sorted(self.specs), self.journal.load_buckets, now, hours)
        for sid, items in out.items():
            tmp = Bucketiser(retain_ms=10 ** 15)
            tmp.load([it for it in items if isinstance(it, Bucket)])   # one sort, not one per bucket
            for it in items:
                if not isinstance(it, Bucket):
                    tmp.on_row(it)
            have = {b.start_ms for b in self.bucketiser.buckets_since(sid, 0)}
            new = [b for b in tmp.buckets_since(sid, 0)
                   if first or (b.source == "tick" and b.start_ms not in have)]
            if first:
                self._warm_buckets[sid] = new   # bars are replayed from the full set, see _rebuild_bars
            self.bucketiser.load(new)           # keeps only the retention window for live operation
            log.info("warm-up %s: %d buckets loaded", self.specs[sid].name, len(new))

    def warmup_hours(self) -> float:
        """Fetch depth: enough that each of the history_slots bars (so each z-window bar) has a full
        240 min VAP window behind it."""
        return max(self.settings.warmup_hours, (self.history_slots * SLOT_MS / 60_000 + VAP_MINUTES) / 60)

    def _rebuild_bars(self, now: int) -> None:
        """Bars (and their features) for the last history_slots closed slots; warm-up bars are not live.

        Built by replaying the warm-up buckets in time order (bars.replay_bars), so every bar's
        240 min VAP window is complete. Bars whose window reaches back before the fetched history
        carry vap_coverage < 1 and enter the features with a NaN VAP.
        """
        last_end = now // SLOT_MS * SLOT_MS
        for sid, spec in self.specs.items():
            buckets = self._warm_buckets.pop(sid, [])
            if not buckets:
                continue
            buckets = sorted(buckets, key=lambda b: b.start_ms)
            start_end = max(last_end - (self.history_slots - 1) * SLOT_MS,
                            buckets[0].start_ms // SLOT_MS * SLOT_MS + SLOT_MS)
            have = self.journal.bar_slots(sid, start_end)
            bars = replay_bars(sid, buckets, start_end, last_end, self._warm_start_ms)
            self.engine.load(bars, live=False)
            for b in bars:
                if int(b.slot_end.timestamp() * 1000) not in have:
                    self.journal.record_bar(b)
                if b.has_data and _finite(b.realised_vol_pct):
                    self._vol.setdefault(sid, []).append(b.realised_vol_pct)
            self._vol[sid] = self._vol.get(sid, [])[-12:]
            full = sum(1 for b in bars if b.vap_coverage >= 1.0)
            log.info("warm-up done %s: %d bars (%d with full VAP window), %.1f h covered, approx share "
                     "at last bar %.2f", spec.name, len(bars), full,
                     (last_end - start_end) / 3600_000 + 5 / 60 if bars else 0.0,
                     bars[-1].approx_share if bars else 0.0)

    def _persist_buckets(self, back_ms: Optional[int] = PERSIST_BACK_MS) -> None:
        for sid in self.specs:
            last = self.bucketiser.last_start_ms(sid)
            if last is None:
                continue
            since = 0 if back_ms is None else last - back_ms
            self.journal.save_buckets(self.bucketiser.buckets_since(sid, since))

    # ---- live data ----

    def _on_spot(self, symbol_id, bid, ask, ts_ms, raw) -> None:
        now = self.clock.now_ms()
        if ts_ms is not None:
            self.stamper.on_spot(symbol_id, ts_ms)
        if bid is not None:
            self._bid[symbol_id] = bid
        if ask is not None:
            self._ask[symbol_id] = ask
        b, a = self._bid.get(symbol_id), self._ask.get(symbol_id)
        self.last_data_ms[symbol_id] = now
        if b is None or a is None or b <= 0 or a <= 0:
            return
        q = Quote(symbol_id, b, a, ts_ms if ts_ms is not None else now)
        self.last_quote[symbol_id] = q
        if self.om is not None:
            self.om.on_quote(q)
        if self.mode is RunMode.DRY_RUN and self.broker is not None:
            self.broker.on_quote(q)
        self._shadow_quote(q)

    def _on_depth(self, symbol_id, new_quotes, deleted_ids, raw) -> None:
        self.last_data_ms[symbol_id] = self.clock.now_ms()
        row = self.stamper.on_depth(symbol_id, new_quotes, deleted_ids)
        if row is not None:
            self.bucketiser.on_row(row)

    # ---- slot timer ----

    def _arm_slot(self) -> None:
        now = self.clock.now_ms()
        grace = self.settings.grace_s * 1000
        b = max((now - grace) // SLOT_MS * SLOT_MS + SLOT_MS, self._last_slot_ms + SLOT_MS)
        self._calls["slot"] = self.clock.call_later(max(b + grace - now, 0) / 1000, self._on_slot, b)

    def _on_slot(self, slot_end_ms: int) -> None:
        self._last_slot_ms = slot_end_ms
        self._calls.pop("slot", None)
        if self._stopping:
            return
        self._arm_slot()
        try:
            self._process_slot(slot_end_ms)
        except Exception:
            log.exception("slot %d failed", slot_end_ms)
            self.journal.record_risk_event("slot_error", str(slot_end_ms))

    def _process_slot(self, slot_end_ms: int) -> None:
        now = self.clock.now_ms()
        self._persist_buckets()
        self.journal.prune_buckets(slot_end_ms - KEEP_BUCKETS_MS)
        if self._check_kill():
            return
        for sid in sorted(self.specs):
            try:
                self._process_symbol(self.specs[sid], slot_end_ms, now)
            except Exception:
                log.exception("symbol %s slot %d failed", self.specs[sid].name, slot_end_ms)
                self.journal.record_risk_event("symbol_error", str(slot_end_ms), sid)
        self._pending_outage_s = None

    def _approx_ok(self, row) -> bool:
        if row.approx_share > 0 and not (self.settings.state_path() / PARITY_MARKER).exists():
            return self.mode is RunMode.DRY_RUN   # dry-run still journals; DEMO waits for the gate
        return True

    def _process_symbol(self, spec, slot_end_ms: int, now: int) -> None:
        sid = spec.symbol_id
        slot_dt = _utc(slot_end_ms)
        bar = self.builder.close_slot(sid, slot_dt)
        live = all(b.source == "live" for b in self.bucketiser.buckets_between(
            sid, slot_end_ms - SLOT_MS, slot_end_ms))
        self.journal.record_bar(bar)
        row = self.engine.append(bar, live=live)
        if bar.has_data and _finite(bar.realised_vol_pct):
            self._vol.setdefault(sid, []).append(bar.realised_vol_pct)
            self._vol[sid] = self._vol[sid][-12:]
        q = self.last_quote.get(sid)
        self._resolve_shadow(sid, now)
        sig = signal_id(sid, slot_end_ms)

        position = self.om.position_view(sid) if self.om else None
        decision, reason = "none", ""
        if not self.trading_ready:
            decision, reason = "none", "offline"
        elif self.om is None:
            reason = "no_broker"
        else:
            ctx = self._risk_ctx(spec, now, q)
            flat, why = self.risk.must_flatten(ctx, position)
            busy = position is None and self.om.state_of(sid).value != "IDLE"
            if flat and position is not None:
                intent = self._exit_intent(sid, sig, why)
                decision, reason = self._handle(intent, spec, q, now)
            elif busy:
                reason = f"busy:{self.om.state_of(sid).value}"
            elif position is not None and position.exit_at_ms > now:
                decision, reason = "hold", "not_due"
            else:
                intent = decide(row, q, position, spec, self.settings, now, slot_end_ms,
                                self._approx_ok(row), self._vol.get(sid))
                ok, rreason, mult = self.risk.check(intent, ctx)
                if intent.kind in (IntentKind.ENTER, IntentKind.FLIP):
                    if not ok:
                        self.journal.record_risk_event("entry_blocked", rreason, sid, now)
                        log.info("risk block %s: entry blocked (%s)", spec.name, rreason)
                        if intent.kind is IntentKind.FLIP:
                            intent = dataclasses.replace(intent, kind=IntentKind.EXIT, side=None,
                                                         reason=f"flip_blocked:{rreason}")
                        else:
                            intent = dataclasses.replace(intent, kind=IntentKind.NONE, reason=rreason)
                    else:
                        intent = dataclasses.replace(intent, size_mult=mult)
                decision, reason = self._handle(intent, spec, q, now)

        shadow_side, _ = signal_side(row, self.settings)
        taker_entry = None
        if shadow_side is not None and q is not None:
            taker_entry = (q.ask if shadow_side is Side.BUY else q.bid) / PRICE_SCALE
            lim = q.bid if shadow_side is Side.BUY else q.ask
            self._shadows[sid] = _Shadow(sig, shadow_side, q, lim,
                                         now + self.settings.limit_ttl_s * 1000)
        self.journal.record_signal(
            sig, ts_ms=now, symbol_id=sid, slot_end_ms=slot_end_ms, z_poc=row.z_poc, z_range=row.z_range,
            events_z=row.events_z, spread=None if q is None else (q.ask - q.bid) / PRICE_SCALE,
            approx_share=row.approx_share, decision=decision, reason=reason, taker_entry=taker_entry)
        spread_bp = NAN if q is None else (q.ask - q.bid) / ((q.ask + q.bid) / 2) * 1e4
        log.info("slot %s %s mid=%.5g z_poc=%.2f z_range=%.2f events_z=%.2f spread_bp=%.2f approx=%.2f "
                 "-> %s (%s)", slot_dt.strftime("%H:%M"), spec.name, bar.mid_close, row.z_poc, row.z_range,
                 row.events_z, spread_bp, row.approx_share, decision, reason or "-")

    def _handle(self, intent: Intent, spec, q, now: int) -> tuple[str, str]:
        self.om.handle(intent, spec, q, now)
        return intent.kind.value, intent.reason

    @staticmethod
    def _exit_intent(sid: int, sig: str, why: str) -> Intent:
        reason = {"kill": "kill", "session_close": "session", "session_closed": "session",
                  "disconnect": "disconnect"}.get(why, "risk")
        return Intent(IntentKind.EXIT, sid, None, 1.0, None, None, None, reason, sig)

    # ---- shadows ----

    def _shadow_quote(self, q: Quote) -> None:
        """Maker-sim: a resting buy at L fills when a later ask <= L (sell: bid >= L) within the ttl."""
        sh = self._shadows.get(q.symbol_id)
        if sh is None or sh.maker is not None:
            return
        if q.ts_ms > sh.deadline_ms:
            sh.maker = "no_fill"
        elif (sh.side is Side.BUY and q.ask <= sh.limit) or (sh.side is Side.SELL and q.bid >= sh.limit):
            sh.maker, sh.fill_price = "fill", sh.limit / PRICE_SCALE

    def _resolve_shadow(self, sid: int, now: int) -> None:
        sh = self._shadows.pop(sid, None)
        q1 = self.last_quote.get(sid)
        if sh is None or q1 is None:
            return
        mid0, mid1 = (sh.q0.bid + sh.q0.ask) / 2, (q1.bid + q1.ask) / 2
        cf = sh.side.value * (mid1 - mid0) / mid0 * 1e4
        exit_px = (q1.bid if sh.side is Side.BUY else q1.ask) / PRICE_SCALE
        self.journal.update_signal(
            sh.signal_id, taker_exit=exit_px, taker_pnl_bps=taker_shadow(sh.q0, q1, sh.side),
            maker_outcome=sh.maker or "no_fill", maker_fill_price=sh.fill_price, cf_return_bps=cf)

    # ---- risk ----

    def _disconnect_s(self, now: int) -> float:
        if self._down_since is not None:
            return (now - self._down_since) / 1000
        return self._pending_outage_s or 0.0

    def _risk_ctx(self, spec, now: int, q: Optional[Quote]) -> RiskContext:
        sid, j, st = spec.symbol_id, self.journal, self.settings
        cal = self.calendars[sid]
        pnl_net = j.pnl_net_today(now)
        pct = 0.0
        if self._balance:
            sod = self._balance + (j.pnl_net_total() - self._balance_pnl_ref) - pnl_net
            pct = pnl_net / sod * 100 if sod > 0 else 0.0
        mtc = cal.minutes_to_close(now)
        sd = self.settings.state_path()
        med = self.engine.spread_median_48(sid)
        return RiskContext(
            now_ms=now, quote=q, spread_median_48=None if not _finite(med) else med,
            last_data_ms=self.last_data_ms.get(sid), session_open=cal.is_open(now),
            minutes_to_close=mtc, bars_since_open=cal.bars_since_open(now),
            open_positions=self.om.open_count() if self.om else 0, trades_today=j.trades_today(now),
            orders_last_min=j.orders_last_min(now), pnl_today_bps=j.pnl_today_bps(now),
            pnl_today_pct=pct, consec_losses=j.consec_losses(),
            rejects_window=j.rejects_since(now - st.reject_breaker_window_min * 60_000),
            kill_file=(sd / "KILL").exists(), pause_file=(sd / "PAUSE").exists(),
            seconds_since_reconnect=None if self._ready_ms is None else (now - self._ready_ms) / 1000,
            disconnect_s=self._disconnect_s(now))

    def _check_kill(self) -> bool:
        if (self.settings.state_path() / "KILL").exists():
            if not self._stopping:
                log.error("KILL file present: flattening and stopping")
                self.journal.record_risk_event("kill", "KILL file")
                self.shutdown("kill", code=EXIT_OK, flatten=True)
            return True
        return False

    def _arm_risk(self) -> None:
        self._calls["risk"] = self.clock.call_later(RISK_TICK_S, self._risk_tick)

    def _risk_tick(self) -> None:
        self._calls.pop("risk", None)
        if self._stopping:
            return
        self._arm_risk()
        try:
            if self._check_kill():
                return
            if not (self.trading_ready and self.om):
                return
            now = self.clock.now_ms()
            for sid, spec in sorted(self.specs.items()):
                position = self.om.position_view(sid)
                if position is None:
                    continue
                ctx = self._risk_ctx(spec, now, self.last_quote.get(sid))
                flat, why = self.risk.must_flatten(ctx, position)
                if flat and self.om.state_of(sid).value == "OPEN":
                    self.journal.record_risk_event("flatten", why, sid, now)
                    log.info("risk %s: flatten (%s)", spec.name, why)
                    self.om.handle(self._exit_intent(sid, signal_id(sid, now // 1000), why), spec,
                                   self.last_quote.get(sid), now)
            self._pending_outage_s = None
        except Exception:
            log.exception("risk tick failed")

    # ---- P&L kill switch (DESIGN.md section 5) ----

    def _kill_tick(self) -> None:
        self._calls.pop("kill", None)
        if self._stopping:
            return
        self._calls["kill"] = self.clock.call_later(self.settings.kill_check_interval_s, self._kill_tick)
        if self._kill_busy:              # the previous check still waits for the server: do not pile up
            return
        self._kill_busy = True
        d = self._kill_check()
        d.addErrback(lambda f: log.error("kill-switch check failed: %s", f.getErrorMessage()))
        d.addBoth(lambda _: setattr(self, "_kill_busy", False))

    def _own_views(self) -> list:
        return [v for v in (self.om.position_view(sid) for sid in sorted(self.specs)) if v is not None]

    def _fallback_ok(self, views) -> bool:
        """The fallback assumes quote currency == deposit currency; warn once and refuse when known otherwise."""
        dep = self.session.trader_info.get("deposit_asset_id")
        bad = sorted({self.specs[v.symbol_id].name for v in views
                      if self.specs[v.symbol_id].quote_asset_id not in (None, dep)})
        if bad and not self._unreal_warned:
            self._unreal_warned = True
            log.warning("kill switch: quote asset of %s differs from the deposit asset: no client-side "
                        "unrealised P&L (realised-only unless the server value is available)", bad)
        return not bad

    @inlineCallbacks
    def _unrealised(self, views):
        """(unrealised net pnl of the bot's own open positions in account currency or None if unknown, source).
        DEMO: the server's netUnrealizedPnL per position (read-only request), per-position fallback to the
        touch mark; DRY_RUN: the touch mark."""
        if not views:
            return 0.0, "none"
        server: dict = {}
        if self.mode is RunMode.DEMO and self.session.ready:
            ids = {v.position_id for v in views if v.position_id is not None}
            try:
                res = yield self.session.request(oa.ProtoOAGetPositionUnrealizedPnLReq(), timeout=5)
                server = killswitch.unrealised_from_server(res, ids)
                self._unreal_err = False
            except Exception as exc:
                if not self._unreal_err:
                    self._unreal_err = True
                    log.warning("kill switch: server unrealised P&L unavailable (%s): using the touch mark",
                                exc)
        total, used = 0.0, set()
        for v in views:
            if v.position_id is not None and v.position_id in server:
                total += server[v.position_id]
                used.add("server")
                continue
            if not self._fallback_ok([v]):
                return None, "unknown"
            part = killswitch.unrealised_fallback(v, self.last_quote.get(v.symbol_id))
            if part is None:
                return None, "unknown"
            total += part
            used.add("touch")
        return total, "+".join(sorted(used))

    @inlineCallbacks
    def _kill_check(self):
        st = self.settings
        if not (st.kill_enabled and self.om is not None and self.initialised):
            return
        now = self.clock.now_ms()
        acct = self.session.account_id
        base = killswitch.current_baseline(st.state_path(), acct, now, st.kill_since)
        views = self._own_views()
        unreal, src = yield self._unrealised(views)
        if self._stopping:
            return
        j = self.journal
        d0, d1 = j.day_bounds(now)
        realised = j.pnl_net_window(base["since_ms"], None, acct, self.mode)
        realised_day = j.pnl_net_window(d0, d1, acct, self.mode)
        u = unreal or 0.0
        cum, daily = realised + u, realised_day + u
        status = {"ts_ms": now, "account_id": acct, "mode": self.mode.value, "since_ms": base["since_ms"],
                  "realised": realised, "unrealised": unreal, "unrealised_source": src,
                  "cumulative": cum, "realised_today": realised_day, "daily": daily,
                  "day_start_ms": d0, "next_reset_ms": d1}
        try:
            killswitch.write_status(st.state_path(), status)
        except OSError as exc:
            log.warning("kill_status.json not written: %s", exc)
        kinds = killswitch.evaluate(st, cum, daily)
        if "kill_profit_target" not in kinds:
            self._kill_profit_done = False
        detail = json.dumps({"cumulative": round(cum, 2), "realised": round(realised, 2),
                             "unrealised": None if unreal is None else round(unreal, 2),
                             "unrealised_source": src, "daily": round(daily, 2),
                             "max_loss": st.kill_max_loss, "profit_target": st.kill_profit_target,
                             "daily_max_loss": st.kill_daily_max_loss, "since_ms": base["since_ms"]},
                            sort_keys=True)
        for kind in kinds:
            if kind == "kill_max_loss" or (kind == "kill_profit_target" and st.kill_profit_action == "kill"):
                self._kill_fire(kind, detail)
                return
            if kind == "kill_profit_target":          # profit_action = pause: no new entries, exits continue
                if not self._kill_profit_done:
                    self._kill_profit_done = True
                    log.warning("KILL SWITCH %s: pausing new entries: %s", kind, detail)
                    self.journal.record_risk_event(kind, detail)
                    self._touch_control("PAUSE", f"{kind} {detail}")
            elif self._kill_daily_day != d0:          # daily_max_loss
                self._kill_daily_day = d0
                log.warning("KILL SWITCH %s: flat, no entries until %s: %s", kind,
                            killswitch.iso(next_reset_ms(now, st)), detail)
                self.journal.record_risk_event(kind, detail)
                self.risk.latch_daily("daily_max_loss", now)
                self.om.flatten_all("risk", now)

    def _touch_control(self, name: str, text: str) -> None:
        try:
            p = self.settings.state_path() / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text + "\n")
        except OSError as exc:
            log.error("could not write state/%s: %s", name, exc)

    def _kill_fire(self, kind: str, detail: str) -> None:
        log.warning("KILL SWITCH %s: flattening and stopping: %s", kind, detail)
        self.journal.record_risk_event(kind, detail)
        self._touch_control("KILL", f"{kind} {detail}")
        self.shutdown("kill", code=EXIT_OK, flatten=True)
