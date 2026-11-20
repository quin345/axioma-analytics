"""`autotrade` command line: probe | run | flatten | parity | report | trades-report | kill-reset | kql-check (README.md has the rollout).

Only `probe`, `run` and `flatten` connect to cTrader (demo host only). `report` and `parity` read
the journal db read-only; `kql-check` only reads KQL (and the journal, read-only). Nothing here prints
a credential.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

DAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
PROBE_TIMEOUT_S = 60
SPOT_WAIT_S = 15
FLATTEN_WAIT_S = 30


# ---- logging ----

class JournalLogHandler(logging.Handler):
    """Forward log records into the journal's JSON-lines file (logs/autotrade.jsonl)."""

    def __init__(self, journal):
        super().__init__(logging.INFO)
        self.journal = journal

    def emit(self, record):
        try:
            self.journal.log("log", level=record.levelname, logger=record.name, msg=record.getMessage())
        except Exception:
            pass  # journal closed during shutdown


def setup_logging(journal=None, verbose: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    out = logging.StreamHandler(sys.stdout)
    out.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
    root.addHandler(out)
    if journal is not None:
        root.addHandler(JournalLogHandler(journal))
    logging.getLogger("twisted").setLevel(logging.WARNING)


def _load(args):
    from . import config
    return config.load(args.env, args.config)


def _db_path(settings) -> Path:
    return settings.state_path() / "autotrade.db"


def _open_ro(settings) -> Optional[sqlite3.Connection]:
    p = _db_path(settings)
    if not p.exists():
        print(f"no journal at {p} yet (run the bot first)")
        return None
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)


# ---- probe ----

def _fmt_sec(sec: int) -> str:
    return f"{DAYS[(sec // 86400) % 7]} {sec % 86400 // 3600:02d}:{sec % 3600 // 60:02d}"


def schedule_summary(spec, limit: int = 6) -> str:
    if not spec.schedule:
        return "always open (no schedule)"
    iv = sorted(spec.schedule)
    shown = ", ".join(f"{_fmt_sec(s)}-{_fmt_sec(e)}" for s, e in iv[:limit])
    more = f", ... ({len(iv)} intervals)" if len(iv) > limit else ""
    return f"{shown}{more}; {len(spec.holidays)} holidays"


def spec_lines(spec, broker_name: str) -> list[str]:
    return [
        f"{spec.name} (broker name {broker_name}, id {spec.symbol_id})",
        f"    digits={spec.digits} pipPosition={spec.pip_position}",
        f"    lotSize={spec.lot_size} minVolume={spec.min_volume} stepVolume={spec.step_volume} "
        f"maxVolume={spec.max_volume}",
        f"    slDistance={spec.sl_distance} distanceSetIn={spec.distance_set_in}",
        f"    schedule tz={spec.schedule_tz}: {schedule_summary(spec)}",
    ]


def cmd_probe(args) -> int:
    from twisted.internet import reactor
    from twisted.internet.defer import Deferred, inlineCallbacks
    from ctrader_open_api.messages import OpenApiMessages_pb2 as oa
    from .clock import Clock
    from .session import Session
    from .transport import OpenApiTransport

    settings = _load(args)
    clock = Clock(reactor)
    session = Session(settings, OpenApiTransport(settings.host, settings.port, clock), clock)
    result = {"code": 1}
    quotes: dict = {}
    waiting: dict = {"all": None}

    def finish(code):
        if result.get("done"):
            return
        result["done"], result["code"] = True, code
        session.stop()
        reactor.callLater(0.2, lambda: reactor.running and reactor.stop())

    def on_spot(sid, bid, ask, ts, raw):
        q = quotes.setdefault(sid, {})
        if bid is not None:
            q["bid"] = bid
        if ask is not None:
            q["ask"] = ask
        if "bid" in q and "ask" in q and waiting["all"] is not None and \
                all("bid" in quotes.get(i, {}) and "ask" in quotes.get(i, {}) for i in session.specs):
            d, waiting["all"] = waiting["all"], None
            d.callback(None)

    @inlineCallbacks
    def on_ready(specs, info):
        try:
            print(f"instance:       {settings.instance_name} ({settings.instance_timeframe} bars, order label "
                  f"{settings.order_label!r}, owns {sorted(settings.own_labels)}"
                  f"{' + unlabelled' if settings.instance_claim_unlabelled else ''})")
            print(f"account id:     {session.account_id}")
            print("isLive:         False (verified: the account is listed and not live)")
            print(f"broker:         {info.get('broker_name')}")
            at = info.get("account_type")
            print(f"account type:   {at}  " + (
                "-> HEDGING: every order opens its own position; instances on the same symbol stay independent"
                if at == "HEDGED" else "-> NETTING: positions of a symbol net together; a second instance on the "
                "same symbols is refused (give it its own demo account via --env)"))
            lev = info.get("leverage_in_cents")
            print(f"leverage:       {'n/a' if lev is None else f'1:{lev / 100:g}'} (max "
                  f"{info.get('max_leverage') or 'n/a'})")
            print(f"money digits:   {info.get('money_digits')}")
            md = info.get("money_digits") or 2
            print(f"balance:        {info['balance'] / 10 ** md:.{md}f} (deposit asset id "
                  f"{info.get('deposit_asset_id')})")
            if session.unresolved:
                print(f"UNRESOLVED symbols: {', '.join(session.unresolved)}")
            print(f"host:           {settings.host}:{settings.port}")
            for sid, spec in sorted(specs.items()):
                for line in spec_lines(spec, session.broker_names.get(sid, "?")):
                    print(line)
            yield _probe_ownership(session, settings, specs)
            ids = sorted(specs)
            if ids:
                wait_d = waiting["all"] = Deferred()
                yield session.request(oa.ProtoOASubscribeSpotsReq(symbolId=ids))
                timer = clock.call_later(SPOT_WAIT_S, lambda: waiting["all"] and waiting["all"].callback(None))
                yield wait_d
                if timer.active():
                    timer.cancel()
                for sid in ids:
                    q, spec = quotes.get(sid, {}), specs[sid]
                    if "bid" in q and "ask" in q:
                        mid = (q["bid"] + q["ask"]) / 2
                        sp = q["ask"] - q["bid"]
                        print(f"spot {spec.name}: bid={q['bid'] / 1e5} ask={q['ask'] / 1e5} "
                              f"spread={sp / 1e5:.5f} ({sp / mid * 1e4:.2f} bp)")
                        _probe_margin(spec, mid, settings, info)
                    else:
                        print(f"spot {spec.name}: none within {SPOT_WAIT_S} s (market closed?)")
                yield session.request(oa.ProtoOAUnsubscribeSpotsReq(symbolId=ids))
            finish(0)
        except Exception as exc:
            print(f"probe failed: {type(exc).__name__}: {exc}")
            finish(1)

    session.on_spot = on_spot
    session.on_ready = on_ready
    session.on_refused = lambda reason: (print(f"REFUSED: {reason}"), finish(2))
    session.on_safe = lambda reason: (print(f"SAFE mode: {reason}"), finish(3))
    reactor.callLater(PROBE_TIMEOUT_S, lambda: (print("probe timed out"), finish(1)))
    session.start()
    reactor.run()
    return result["code"]


def _probe_margin(spec, mid_int: float, settings, info) -> None:
    """Notional of one order of this instance and of two instances at once vs balance (and leverage)."""
    from .execution import normalise_volume
    vol = normalise_volume(spec, 1.0, settings.base_size_x_min)
    units = vol / 100                                   # Open API volume is in 1/100 of a unit
    notional = units * mid_int / 1e5                    # quote currency
    md = info.get("money_digits") or 2
    bal = info["balance"] / 10 ** md
    lev = (info.get("leverage_in_cents") or 0) / 100
    margin = f", margin ~{2 * notional / lev:,.2f} at 1:{lev:g}" if lev else ""
    print(f"    exposure {spec.name}: order {vol} ({units:g} units, notional ~{notional:,.2f} quote ccy); two "
          f"instances ~{2 * notional:,.2f} = {2 * notional / bal:.4%} of balance{margin}; maxVolume "
          f"{spec.max_volume}, maxExposure {spec.max_exposure or 'n/a'}")


def _probe_ownership(session, settings, specs):
    """Read-only: open positions/orders by owner (ProtoOAReconcileReq) and the labels of the account's orders
    in the last 24 h (ProtoOAOrderListReq; closing orders show whether a close inherits the position label)."""
    from twisted.internet.defer import inlineCallbacks
    from ctrader_open_api.messages import OpenApiMessages_pb2 as oa

    def owner(label):
        if settings.owns_label(label):
            return f"THIS instance ({label or 'no label'})"
        return f"other: {label}" if label else "other: no label (manual?)"

    @inlineCallbacks
    def run():
        try:
            res = yield session.request(oa.ProtoOAReconcileReq(), timeout=10)
            print(f"open positions: {len(res.position)}, pending orders: {len(res.order)}")
            for p in res.position:
                td = p.tradeData
                name = specs[td.symbolId].name if td.symbolId in specs else td.symbolId
                print(f"    position {p.positionId} {name} vol={td.volume} label={td.label!r} "
                      f"comment={td.comment!r} -> {owner(td.label)}")
            for o in res.order:
                print(f"    order {o.orderId} sym={o.tradeData.symbolId} label={o.tradeData.label!r} "
                      f"clientOrderId={o.clientOrderId!r} -> {owner(o.tradeData.label)}")
            now = int(time.time() * 1000)
            hist = yield session.request(oa.ProtoOAOrderListReq(fromTimestamp=now - 86_400_000, toTimestamp=now),
                                         timeout=15)
            counts: dict = {}
            for o in hist.order:
                k = (o.tradeData.label or "<no label>", "closing" if o.closingOrder else "opening")
                counts[k] = counts.get(k, 0) + 1
            print("orders in the last 24 h by label (closing = ClosePositionReq / stop / stop-out):")
            for (lab, kind), n in sorted(counts.items()):
                print(f"    {lab:<20} {kind:<8} {n}")
        except Exception as exc:
            print(f"ownership check failed: {type(exc).__name__}: {exc}")
    return run()


# ---- run ----

def cmd_run(args) -> int:
    from twisted.internet import reactor
    from .clock import Clock
    from .runner import Bot
    from .transport import OpenApiTransport
    from .types import ExecMode, RunMode

    from . import instance
    settings = _load(args)
    instance.claim(settings)               # state dir belongs to this instance; one process per instance
    mode = RunMode.DEMO if args.mode == "demo" else RunMode.DRY_RUN
    exec_mode = ExecMode(args.exec or settings.exec)
    clock = Clock(reactor)
    bot = Bot(settings, mode, exec_mode, clock, OpenApiTransport(settings.host, settings.port, clock),
              hours=args.hours)
    setup_logging(bot.journal, args.verbose)
    logging.getLogger(__name__).info(
        "starting: instance=%s timeframe=%s label=%s owns=%s state=%s mode=%s exec=%s symbols=%s hours=%s",
        settings.instance_name, settings.instance_timeframe, settings.order_label, sorted(settings.own_labels),
        settings.state_path(), mode.value, exec_mode.value, settings.symbols, args.hours)
    bot.done.addCallback(lambda code: reactor.running and reactor.stop())
    reactor.addSystemEventTrigger("before", "shutdown", lambda: bot.shutdown("signal"))
    bot.start()
    reactor.run()
    return bot.exit_code


# ---- flatten ----

def cmd_flatten(args) -> int:
    from twisted.internet import reactor
    from twisted.internet.defer import inlineCallbacks
    from .clock import Clock
    from .execution import DemoBroker, OrderManager
    from .journal import Journal
    from .session import Session
    from .transport import OpenApiTransport
    from .types import ExecMode, RunMode

    from . import instance
    settings = _load(args)
    instance.check(settings)
    setup_logging(None, args.verbose)
    print(f"instance {settings.instance_name}: flattening only positions/orders labelled "
          f"{sorted(settings.own_labels)}{' or unlabelled' if settings.instance_claim_unlabelled else ''}")
    journal = Journal.from_settings(settings)
    clock = Clock(reactor)
    session = Session(settings, OpenApiTransport(settings.host, settings.port, clock), clock)
    result = {"code": 1}

    def finish(code):
        if result.get("done"):
            return
        result["done"], result["code"] = True, code
        journal.end_run()
        journal.close()
        session.stop()
        reactor.callLater(0.2, lambda: reactor.running and reactor.stop())

    @inlineCallbacks
    def on_ready(specs, info):
        try:
            journal.start_run(RunMode.DEMO, settings, account_id=session.account_id, is_live=False,
                              broker=f"demo/{info.get('broker_name')} flatten")
            om = OrderManager(DemoBroker(session), journal, clock, settings, RunMode.DEMO, ExecMode.TAKER)
            session.on_execution = om.on_execution_event
            session.on_order_error = om.on_order_error
            yield om.startup_reconcile(specs)  # cancels own resting orders, adopts own positions
            om.flatten_all("manual")
            waited = 0.0
            while om.open_count() and waited < FLATTEN_WAIT_S:
                yield _sleep(clock, 0.5)
                waited += 0.5
            left = om.open_count()
            print("flat: all own positions closed and own orders cancelled" if not left else
                  f"NOT FLAT after {FLATTEN_WAIT_S} s: {left} symbol(s) still open, check the account")
            finish(0 if not left else 1)
        except Exception as exc:
            print(f"flatten failed: {type(exc).__name__}: {exc}")
            finish(1)

    session.on_ready = on_ready
    session.on_refused = lambda reason: (print(f"REFUSED: {reason}"), finish(2))
    session.on_safe = lambda reason: (print(f"SAFE mode: {reason}"), finish(3))
    reactor.callLater(PROBE_TIMEOUT_S + FLATTEN_WAIT_S, lambda: (print("flatten timed out"), finish(1)))
    session.start()
    reactor.run()
    return result["code"]


def _sleep(clock, seconds):
    from twisted.internet.defer import Deferred
    d = Deferred()
    clock.call_later(seconds, d.callback, None)
    return d


# ---- report / parity ----

def cmd_report(args) -> int:
    from . import report
    settings = _load(args)
    conn = _open_ro(settings)
    if conn is None:
        return 1
    since = None
    if args.since_hours:
        import time
        since = int((time.time() - args.since_hours * 3600) * 1000)
    print(report.render(report.compute_report(conn, since_ms=since, settings=settings)))
    return 0


def cmd_trades_report(args) -> int:
    """Daily executed-trades report(s) of this instance (autotrade/daily.py): files under --out-dir, or text
    on stdout. Reads the journal read-only; writes only the report files."""
    from . import daily
    settings = _load(args)
    if not _db_path(settings).exists():
        print(f"no journal at {_db_path(settings)} yet (run the bot first)")
        return 1
    conn = daily.open_ro(_db_path(settings))
    out_dir = Path(args.out_dir) if args.out_dir else daily.DEFAULT_OUT_ROOT / settings.instance_name
    now = int(time.time() * 1000)
    try:
        days = daily.resolve_days(conn, args.date, args.last_day, args.backfill, args.today, now)
    except ValueError as exc:
        print(f"trades-report: {exc}", file=sys.stderr)
        return 1
    if not days:
        print(f"trades-report {settings.instance_name}: nothing to report (no run or trade covers the requested day)")
        return 0
    reports = daily.run(conn, settings, out_dir, days, now, write=not args.stdout)
    conn.close()
    if args.stdout:
        print("\n\n".join(daily.render(r) for r in reports))
    else:
        print(f"trades-report {settings.instance_name}: wrote {len(reports)} day(s) "
              f"{reports[0]['meta']['date']}..{reports[-1]['meta']['date']}, "
              f"{sum(r['summary']['trades'] for r in reports)} trade(s), to {out_dir}")
    return 0


def cmd_kill_reset(args) -> int:
    """Re-arm the P&L kill switch: a new baseline at now. KILL / PAUSE are left for the operator."""
    from . import killswitch
    settings = _load(args)
    sd = settings.state_path()
    now = int(time.time() * 1000)
    b = killswitch.write_baseline(sd, settings.secrets.account_id, now)
    print(f"kill switch re-armed: baseline now {b['created']} (state/{killswitch.BASELINE_FILE})")
    if settings.kill_since:
        print(f"WARNING: kill.since = {settings.kill_since!r} in config.toml overrides the baseline file")
    if not settings.kill_enabled:
        print("note: kill.enabled is false in config.toml")
    for name in ("KILL", "PAUSE"):
        if (sd / name).exists():
            print(f"state/{name} is still present (not removed); remove it with: rm {sd / name}")
    return 0


def cmd_parity(args) -> int:
    from . import parity
    settings = _load(args)
    conn = _open_ro(settings)
    if conn is None:
        return 1
    from . import timeframe
    print(parity.render(parity.parity_report(conn, timeframe.of(settings))))
    return 0


def cmd_kql_check(args) -> int:
    """Read-only smoke check of the [data] source = "kql" path: the bot's bucket query for the last N
    minutes of its own streams, with counts, watermark lag and gaps; --verify recomputes the buckets in
    Python from the raw rows (kql_source.reference_rows) and compares. No cTrader connection."""
    from . import kql_source as kq
    settings = _load(args)
    if args.symbol_ids:
        ids = [int(x) for x in args.symbol_ids.split(",") if x.strip()]
    else:
        conn = _open_ro(settings)
        if conn is None:
            print("pass --symbol-ids (no journal to take them from)")
            return 1
        since = int(time.time() * 1000) - 2 * 86_400_000
        ids = [r[0] for r in conn.execute("SELECT DISTINCT symbol_id FROM bars WHERE slot_end_ms>=? ORDER BY 1",
                                          (since,))]
        conn.close()
        if not ids:
            print("no recent bars in the journal: pass --symbol-ids")
            return 1
    try:
        cfg = kq.load_kql_config(settings.data_env_path())
        reader = kq.KustoReader(cfg, settings.data_timeout_s)
    except Exception as exc:
        print(f"KQL not usable: {type(exc).__name__}: {exc}")
        return 1
    from . import timeframe
    tf = timeframe.of(settings)
    acct = str(settings.secrets.account_id)
    print(f"instance {settings.instance_name}: {tf.key} bars, VAP window {tf.vap_minutes} min, gates: freshness "
          f"{settings.data_freshness_s:g}s, gap > {settings.data_gap_max_s:g}s, first query slot_end + "
          f"{settings.data_grace_s:g}s, retry every {settings.data_retry_s:g}s x{settings.data_retries}, deadline "
          f"slot_end + {settings.data_deadline_s:g}s")
    now = int(time.time() * 1000)
    to_ms = now // 10_000 * 10_000
    # the slot-close query the bot runs every slot: [previous fetch end - one slot, t) ~ two slots
    t_slot = now // tf.slot_ms * tf.slot_ms
    inc = kq.KqlRequest(cfg.table, acct, tuple(ids), t_slot - 2 * tf.slot_ms, t_slot)
    timings = []
    for _ in range(3):
        t0 = time.time()
        try:
            reader.rows(inc)
        except Exception as exc:
            print(f"KQL slot-close query failed: {type(exc).__name__}: {str(exc)[:300]}")
            return 1
        timings.append(time.time() - t0)
    print(f"slot-close query ({2 * tf.slot_minutes} min of buckets): " +
          ", ".join(f"{x:.2f}s" for x in timings))
    req = kq.KqlRequest(cfg.table, acct, tuple(ids), to_ms - int(args.minutes * 60_000), to_ms)
    t0 = time.time()
    try:
        rows = reader.rows(req)
    except Exception as exc:
        print(f"KQL query failed: {type(exc).__name__}: {str(exc)[:300]}")
        return 1
    res = kq.parse_result(req, rows)
    print(f"KQL {cfg.database}.{cfg.table}: account {acct}, symbolIds {ids}, last {args.minutes:g} min "
          f"(query {time.time() - t0:.2f}s)")
    code = 0
    for sid in ids:
        bks = res.buckets[sid]
        wm = res.watermark[sid]
        gaps = kq.window_gaps([b.start_ms for b in bks], req.from_ms, to_ms, settings.data_gap_max_s * 1000)
        lag = "n/a" if wm is None else f"{(now - wm) / 1000:.1f}s"
        print(f"  symbolId {sid}: {len(bks)} buckets, {sum(b.events for b in bks)} events, watermark lag {lag}, "
              f"gaps > {settings.data_gap_max_s:g}s: {len(gaps)} (session schedule not applied here)")
        if not bks:
            code = 1
    if args.verify:
        raw_q = (f"{cfg.table}\n| where ctidTraderAccountId == '{int(acct)}' and symbolId in "
                 f"({', '.join(repr(str(i)) for i in ids)}) and timestamp >= {kq._kdt(req.from_ms)}\n| project symbol_id = toint(symbolId), ts_ms = "
                 f"datetime_diff('millisecond', timestamp, datetime(1970-01-01)), seq = eventSeq, best_bid, best_ask")
        raw = reader.run(raw_q)
        ref = kq.parse_result(req, kq.reference_rows(req, raw))
        for sid in ids:
            a = [(b.start_ms, b.best_bid, b.best_ask, b.events) for b in res.buckets[sid]]
            b = [(x.start_ms, x.best_bid, x.best_ask, x.events) for x in ref.buckets[sid]]
            diff = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
            # the raw rows are read after the bucket query, so they may hold newer rows: the query's watermark
            # must cover every row before `to` and not exceed what the later raw read saw
            wm, ref_wm = res.watermark[sid], ref.watermark[sid]
            before = max((r["ts_ms"] for r in raw if r["symbol_id"] == sid and r["ts_ms"] < to_ms), default=None)
            wm_ok = wm is None or ref_wm is None or ((before is None or before <= wm) and wm <= ref_wm)
            print(f"  verify symbolId {sid}: {len(a)} KQL buckets vs {len(b)} recomputed from {sum(1 for r in raw if r['symbol_id'] == sid)} "
                  f"raw rows: {diff} differ; watermark {'ok' if wm_ok else 'MISMATCH'}")
            if diff or not wm_ok:
                code = 1
    return code


def _iso_date(text: str) -> str:
    from datetime import date
    try:
        date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {text!r}") from None
    return text


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="autotrade", description=__doc__.splitlines()[0])
    p.add_argument("--env", help="path to .env (default: ctrader_autotrade/.env)")
    p.add_argument("--config", help="path to config.toml (default: ctrader_autotrade/config.toml)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe", help="connect, print account/symbol facts and current spreads, disconnect")
    r = sub.add_parser("run", help="run the bot")
    r.add_argument("--mode", choices=("dry-run", "demo"), required=True)
    r.add_argument("--exec", choices=("taker", "maker"), help="entry style (default: config.toml)")
    r.add_argument("--hours", type=float, help="stop after N hours (bounded test runs)")
    sub.add_parser("flatten", help="close own positions and cancel own orders on the demo account")
    sub.add_parser("kill-reset", help="re-arm the P&L kill switch: new baseline at now (KILL/PAUSE not removed)")
    sub.add_parser("parity", help="P4 drift: research features over the bars table vs journalled features")
    rp = sub.add_parser("report", help="forward-test report from the journal")
    rp.add_argument("--since-hours", type=float, help="only the last N hours")
    tr = sub.add_parser("trades-report", help="daily executed-trades report of this instance (JSON files per UTC day)")
    when = tr.add_mutually_exclusive_group(required=True)
    when.add_argument("--date", metavar="YYYY-MM-DD", type=_iso_date, help="one UTC day (a trade belongs to its exit day)")
    when.add_argument("--last-day", action="store_true", help="yesterday UTC (what cron runs)")
    when.add_argument("--backfill", action="store_true", help="every UTC day with a run or trades, up to yesterday")
    when.add_argument("--today", action="store_true", help="the current UTC day so far (marked partial)")
    tr.add_argument("--out-dir", help="default: reports/<instance name>/ next to config.toml's directory")
    tr.add_argument("--stdout", action="store_true", help="print the text rendering instead of writing files")
    kc = sub.add_parser("kql-check", help="read-only: the bot's KQL bucket query for its streams, counts and lag")
    kc.add_argument("--minutes", type=float, default=30)
    kc.add_argument("--symbol-ids", help="comma-separated cTrader symbolIds (default: from the journal's bars)")
    kc.add_argument("--verify", action="store_true", help="recompute the buckets from raw rows and compare")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    from . import config
    try:
        code = {"probe": cmd_probe, "run": cmd_run, "flatten": cmd_flatten, "report": cmd_report,
                "parity": cmd_parity, "trades-report": cmd_trades_report, "kill-reset": cmd_kill_reset, "kql-check": cmd_kql_check}[args.cmd](args)
    except config.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
