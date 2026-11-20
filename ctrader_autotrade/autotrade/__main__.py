"""`autotrade` command line: probe | run | flatten | parity | report | kill-reset | kql-check (README.md has the rollout).

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
            print(f"account id:     {session.account_id}")
            print("isLive:         False (verified: the account is listed and not live)")
            print(f"broker:         {info.get('broker_name')}")
            print(f"account type:   {info.get('account_type')}")
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


# ---- run ----

def cmd_run(args) -> int:
    from twisted.internet import reactor
    from .clock import Clock
    from .runner import Bot
    from .transport import OpenApiTransport
    from .types import ExecMode, RunMode

    settings = _load(args)
    mode = RunMode.DEMO if args.mode == "demo" else RunMode.DRY_RUN
    exec_mode = ExecMode(args.exec or settings.exec)
    clock = Clock(reactor)
    bot = Bot(settings, mode, exec_mode, clock, OpenApiTransport(settings.host, settings.port, clock),
              hours=args.hours)
    setup_logging(bot.journal, args.verbose)
    logging.getLogger(__name__).info("starting: mode=%s exec=%s symbols=%s hours=%s", mode.value,
                                     exec_mode.value, settings.symbols, args.hours)
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

    settings = _load(args)
    setup_logging(None, args.verbose)
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
    print(parity.render(parity.parity_report(conn)))
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
    acct = str(settings.secrets.account_id)
    now = int(time.time() * 1000)
    to_ms = now // 10_000 * 10_000
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
            wm_ok = res.watermark[sid] is None or ref.watermark[sid] is None or ref.watermark[sid] <= res.watermark[sid]
            print(f"  verify symbolId {sid}: {len(a)} KQL buckets vs {len(b)} recomputed from {sum(1 for r in raw if r['symbol_id'] == sid)} "
                  f"raw rows: {diff} differ; watermark {'ok' if wm_ok else 'MISMATCH'}")
            if diff or not wm_ok:
                code = 1
    return code


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
                "parity": cmd_parity, "kill-reset": cmd_kill_reset, "kql-check": cmd_kql_check}[args.cmd](args)
    except config.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
