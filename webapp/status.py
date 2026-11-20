"""System status: is the data pipeline healthy right now?

Collects, read-only, the state of
  * the cTrader collector units  (ctrader-collector@<broker>-<account>-<SYMBOL>.service),
  * the autotrader unit(s)       (ctrader-autotrade*.service, discovered by pattern),
  * the KQL database and the depth events flowing into it,
  * the cron jobs that export bars into the local record store,
  * the other systemd units the app depends on (axioma, nginx, cron),
and serves them as one JSON payload (`GET /api/status`, page at `/status`).

Design rules
  * Everything that touches the system goes through a `Sources` object
    (command runner, file reader, clock, Kusto reader, read-only SQLite), so the
    tests run with fakes and no systemd/Kusto.
  * Subprocesses are argument lists (never a shell), each with a timeout, and
    only ever `systemctl list-units|list-unit-files|show`. Unit names must pass
    `valid_unit` (regex + prefix whitelist) before they reach a command line.
  * Read-only: nothing here starts, stops or writes anything.
  * Secret-free: env files are never opened (their *names* are listed only when
    the directory is listable), unit command lines are never returned, cron
    commands are reduced to "script + options", errors are reported by class
    name only, and `scrub()` removes any known secret value as a last line.
  * Each section fails on its own into `unknown`; `collect()` never raises.

The status rules are the constants below; README.md "System status" documents them.
"""
from __future__ import annotations

import os
import re
import shlex
import sqlite3
import subprocess
import threading
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import quote

# --- Status values ---------------------------------------------------------------
OK, WARN, DOWN, UNKNOWN, NA = "ok", "warn", "down", "unknown", "n/a"
# How bad each status is when rolling up. `unknown` counts as `warn`; `n/a`
# (not configured / nothing to check) does not count against anything.
SEVERITY = {OK: 0, NA: 0, WARN: 1, UNKNOWN: 1, DOWN: 2}

# --- Rules -------------------------------------------------------------------------
SUBPROCESS_TIMEOUT_S = 3.0      # every systemctl call
KQL_TIMEOUT_S = 10.0            # the KQL round trip
KQL_SLOW_S = 5.0                # KQL ok below this latency, warn above, down on error
CACHE_TTL_S = 10.0              # one collection per this many seconds (per worker)

UNIT_SETTLE_S = 60              # active but started less than this ago -> warn
UNIT_FLAP_WINDOW_S = 600        # NRestarts > 0 and the current start is younger -> warn (flapping)

STREAM_LIVE_S = 60              # last depth event younger than this -> ok
STREAM_IDLE_S = 30 * 60         # up to this -> warn "idle / market closed?"; older -> down
STREAM_RECENT = "15m"           # "rows in 15 min"
STREAM_LOOKBACK = "24h"         # streams seen in this window are expected to keep flowing

AUTOTRADE_STALE_SLOTS = 3       # active bot, last decision older than 3 slots (+60 s) -> warn
AUTOTRADE_DEFAULT_SLOT_S = 300
POSITION_STATES = ("OPEN", "EXIT_PENDING")


@dataclass(frozen=True)
class CronJob:
    table: str          # the record-store table the job writes
    timeframe: str      # export_flat.py --timeframe (5m is the script's default)
    interval_s: int     # how often cron runs it
    warn_s: int         # table freshness (age of max(computed_at)) above this -> warn
    down_s: int         # ... above this -> down


CRON_JOBS = (
    CronJob("metrics_5m", "5m", 300, 600, 1200),
    CronJob("metrics_1m", "1m", 60, 180, 600),
)
CRON_SCRIPT = "export_flat.py"
CRON_RELEVANT = re.compile(r"axioma|ctrader|export_flat", re.I)

# Instruments with a session: depth goes quiet at the daily break and at the
# weekend. Inside these UTC windows a silent stream is `warn` (expected), not
# `down`. Wide enough to cover both DST settings of the 21:56-23:00 UTC break
# (22:00-23:00 in winter, 21:00-22:00 in summer). Crypto trades 24/7: no window.
SESSION_SYMBOL_PREFIXES = ("XAU", "XAG", "GOLD", "SILVER")
DAILY_BREAK_UTC = ((20, 55), (23, 10))          # [start, end) every weekday
WEEKEND_UTC = ((4, 20, 55), (6, 23, 10))         # Fri 20:55 -> Sun 23:10 (Mon=0)

# --- Unit whitelist ---------------------------------------------------------------
UNIT_NAME_RE = re.compile(r"^[A-Za-z0-9@._:-]+$")
UNIT_PREFIXES = ("ctrader-collector@", "ctrader-autotrade", "axioma", "nginx", "cron")
COLLECTOR_PREFIX = "ctrader-collector@"
AUTOTRADE_PREFIX = "ctrader-autotrade"
APP_UNITS = ("axioma.service", "nginx.service", "cron.service")
SHOW_PROPERTIES = ("Id", "LoadState", "ActiveState", "SubState", "UnitFileState",
                   "ActiveEnterTimestamp", "InactiveEnterTimestamp", "MainPID", "NRestarts",
                   "Result", "ExecMainStatus", "ExecMainCode", "WorkingDirectory", "ExecStart")
SYSTEMCTL = "/usr/bin/systemctl"


@dataclass
class Paths:
    cron_d: str = "/etc/cron.d"
    crontab: str = "/etc/crontab"
    cron_spool: str = "/var/spool/cron/crontabs"
    wants_dirs: tuple = ("/etc/systemd/system/multi-user.target.wants",)
    collector_env_dir: str = "/etc/ctrader-collector"


@dataclass
class Sources:
    """Everything the collection reads, injectable for tests.

    run(argv) -> stdout (raises on failure/timeout); read_text(path, max_bytes)
    -> the file's last max_bytes as text; listdir(path); mtime(path) -> epoch;
    exists(path); now() -> epoch seconds; kusto(query) -> rows (None = no KQL
    client); sqlite(path, sql, params) -> rows from a read-only connection;
    symbol_names() -> {(broker, account, symbol_id): name}; broker(name) ->
    the catalogue spelling of a broker.
    """
    run: Callable
    read_text: Callable
    listdir: Callable
    mtime: Callable
    exists: Callable
    now: Callable
    kusto: Optional[Callable]
    sqlite: Callable
    symbol_names: Callable
    kql_table: str
    kql_database: str = ""
    app_db: str = ""
    broker: Callable = lambda name: name
    paths: Paths = field(default_factory=Paths)


# --- Default (real) sources -------------------------------------------------------

class CommandError(RuntimeError):
    pass


_CMD_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "SYSTEMD_PAGER": "", "SYSTEMD_COLORS": "0"}


def run_command(argv, timeout=SUBPROCESS_TIMEOUT_S):
    """Run a whitelisted command as an argument list, never through a shell."""
    if not isinstance(argv, (list, tuple)) or not argv or argv[0] != SYSTEMCTL:
        raise CommandError("command not allowed")
    proc = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout,
                          check=False, env=_CMD_ENV, stdin=subprocess.DEVNULL, shell=False)
    if proc.returncode != 0:
        raise CommandError(f"systemctl exited {proc.returncode}")
    return proc.stdout


def read_text(path, max_bytes=256 * 1024):
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        return handle.read().decode("utf-8", errors="replace")


def sqlite_query(path, sql, params=()):
    """Rows from a read-only connection (mode=ro: never creates or writes the file)."""
    uri = "file:" + quote(os.path.abspath(path)) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def call_with_timeout(fn, timeout, *args):
    """fn(*args) on a daemon thread; TimeoutError if it takes longer than `timeout`."""
    box = {}

    def target():
        try:
            box["value"] = fn(*args)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True, name="status-kql")
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"no answer in {timeout:.0f} s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def default_sources(kusto, kql_table, kql_database, app_db, symbol_names, broker=lambda n: n):
    return Sources(run=run_command, read_text=read_text, listdir=os.listdir,
                   mtime=os.path.getmtime, exists=os.path.exists, now=time.time,
                   kusto=kusto, sqlite=sqlite_query, symbol_names=symbol_names,
                   kql_table=kql_table, kql_database=kql_database, app_db=app_db, broker=broker)


# --- Small helpers ----------------------------------------------------------------

def worst(statuses):
    """The worst status of an iterable: down > warn > unknown > ok. `n/a` is
    ignored unless everything is n/a (then n/a); empty is ok."""
    statuses = [s if s in SEVERITY else UNKNOWN for s in statuses]
    real = [s for s in statuses if s != NA]
    if not real:
        return NA if statuses else OK
    return max(real, key=_RANK.__getitem__)


_RANK = {OK: 0, UNKNOWN: 1, WARN: 2, DOWN: 3}


def rollup(statuses):
    """The overall rule: the worst status, with `unknown` counted as warn and
    `n/a` as ok."""
    result = worst(statuses)
    return {UNKNOWN: WARN, NA: OK}.get(result, result)


def human_age(seconds):
    if seconds is None:
        return "n/a"
    seconds = int(max(0, seconds))
    if seconds < 120:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 120:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {minutes}m"
    return f"{hours // 24}d {hours % 24}h"


def iso(epoch):
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


def parse_time(value):
    """Epoch seconds from an ISO string, a systemd `@unix` stamp or a datetime; else None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return stamp.timestamp()
    text = str(value).strip()
    if not text or text == "n/a":
        return None
    if text.startswith("@"):
        try:
            return float(text[1:])
        except ValueError:
            return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            stamp = datetime.strptime(text, "%a %Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def item(name, status, detail, **raw):
    out = {"name": name, "status": status, "detail": detail}
    out.update(raw)
    return out


def failed(name, exc):
    """The `unknown` item a failing check turns into: the error class only."""
    return item(name, UNKNOWN, f"check failed: {type(exc).__name__}")


def valid_unit(name):
    return (isinstance(name, str) and len(name) <= 200 and bool(UNIT_NAME_RE.match(name))
            and name.startswith(UNIT_PREFIXES) and name.endswith(".service")
            and name != COLLECTOR_PREFIX + ".service")


# --- systemd ----------------------------------------------------------------------

def parse_show(text):
    """`systemctl show` output -> {unit name: {property: value}} (blank-line separated blocks)."""
    units, block = {}, {}
    for line in (text or "").splitlines() + [""]:
        if not line.strip():
            if block.get("Id"):
                units[block["Id"]] = block
            block = {}
            continue
        key, sep, value = line.partition("=")
        if sep:
            block[key.strip()] = value.strip()
    return units


def _int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def classify_unit(props, now):
    """(status, detail) for one unit's `systemctl show` properties."""
    load = props.get("LoadState", "")
    active = props.get("ActiveState", "")
    sub = props.get("SubState", "")
    enabled = props.get("UnitFileState", "") or "n/a"
    restarts = _int(props.get("NRestarts"))
    since = parse_time(props.get("ActiveEnterTimestamp"))
    uptime = None if since is None else max(0.0, now - since)
    if load == "not-found":
        return DOWN, "not installed"
    if active == "active":
        if sub != "running":
            return WARN, f"active ({sub}), not running"
        if uptime is not None and uptime < UNIT_SETTLE_S:
            return WARN, f"started {human_age(uptime)} ago" + (f", {restarts} restarts" if restarts else "")
        if restarts and uptime is not None and uptime < UNIT_FLAP_WINDOW_S:
            return WARN, f"restarted {restarts}x, up {human_age(uptime)} (flapping?)"
        return OK, f"running for {human_age(uptime)}" + (f", {restarts} restarts" if restarts else "")
    if active in ("activating", "reloading", "deactivating"):
        return WARN, f"{active} ({sub})" + (f", {restarts} restarts" if restarts else "")
    if active == "failed":
        return DOWN, f"failed ({props.get('Result', '?')}, status {props.get('ExecMainStatus', '?')}), {enabled}"
    if active == "inactive":
        return DOWN, f"inactive, {enabled}, last result {props.get('Result', '?')}"
    return UNKNOWN, f"state {active or '?'}"


def unit_item(name, props, now, configured=None):
    """One unit with its raw systemd fields. `configured`: has an env file
    (None = not a collector, or the env directory is not listable)."""
    status, detail = classify_unit(props, now)
    active = props.get("ActiveState", "")
    enabled = props.get("UnitFileState", "")
    since = parse_time(props.get("ActiveEnterTimestamp"))
    if active in ("inactive",) and enabled not in ("enabled", "enabled-runtime", "static", "indirect", "alias"):
        # Installed but disabled and stopped: nothing is expected to run.
        if configured:
            status, detail = WARN, "configured (env file) but not enabled or running"
        elif props.get("LoadState") != "not-found":
            status, detail = NA, f"{enabled or 'not enabled'} and not running"
    return item(
        name, status, detail,
        ActiveState=active or None, SubState=props.get("SubState") or None,
        LoadState=props.get("LoadState") or None, UnitFileState=enabled or None,
        since=iso(since) if active == "active" else None,
        uptime_s=int(now - since) if since is not None and active == "active" else None,
        NRestarts=_int(props.get("NRestarts")), MainPID=_int(props.get("MainPID")),
        Result=props.get("Result") or None,
        ExecMainStatus=_int(props.get("ExecMainStatus"), None),
        configured=configured,
    )


def _first_unit_token(line):
    for token in line.split():
        if token.endswith(".service") and UNIT_NAME_RE.match(token):
            return token
    return None


def discover(sources):
    """Every ctrader-* unit worth showing, plus the collector env-file names.

    Sources: loaded units (`list-units --all`), installed unit files
    (`list-unit-files`, templates skipped), enabled instances (the wants
    directories) and the env-file names in /etc/ctrader-collector (names only,
    and only if the directory is listable). Returns (sorted names, env instance
    names or None if not listable, notes).
    """
    names, notes = set(), []
    pattern = "ctrader-*"
    for argv in (
        [SYSTEMCTL, "list-units", "--all", "--plain", "--no-legend", "--no-pager", "--full",
         "--type=service", pattern],
        [SYSTEMCTL, "list-unit-files", "--plain", "--no-legend", "--no-pager", "--type=service", pattern],
    ):
        try:
            for line in sources.run(argv).splitlines():
                token = _first_unit_token(line)
                if token:
                    names.add(token)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{argv[1]}: {type(exc).__name__}")
    for wants in sources.paths.wants_dirs:
        try:
            names.update(n for n in sources.listdir(wants) if n.startswith("ctrader-"))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{wants}: {type(exc).__name__}")
    env_instances = None
    try:
        env_instances = sorted(n[:-4] for n in sources.listdir(sources.paths.collector_env_dir)
                               if n.endswith(".env"))
        names.update(f"{COLLECTOR_PREFIX}{inst}.service" for inst in env_instances)
    except PermissionError:
        notes.append(f"{sources.paths.collector_env_dir}: not readable")
    except FileNotFoundError:
        env_instances = []
    except Exception as exc:  # noqa: BLE001
        notes.append(f"{sources.paths.collector_env_dir}: {type(exc).__name__}")
    return sorted(n for n in names if valid_unit(n)), env_instances, notes


def show_units(sources, names):
    """One `systemctl show` call for every unit; {name: props}."""
    names = [n for n in names if valid_unit(n)]
    if not names:
        return {}
    argv = [SYSTEMCTL, "show", "--no-pager", "--timestamp=unix",
            "--property=" + ",".join(SHOW_PROPERTIES), "--"] + names
    return parse_show(sources.run(argv))


# --- KQL --------------------------------------------------------------------------

def kql_query(table):
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", table or ""):
        raise ValueError("bad table name")
    return (f"{table} | where timestamp > ago({STREAM_LOOKBACK}) "
            f"| summarize rows_15m=countif(timestamp > ago({STREAM_RECENT})), "
            f"last_event=max(timestamp), last_ingested=max(ingestion_time()) "
            f"by broker, ctidTraderAccountId, symbolId")


def expected_quiet(symbol, now):
    """True inside the daily break / weekend of a session instrument (gold, silver)."""
    if not symbol or not str(symbol).upper().startswith(SESSION_SYMBOL_PREFIXES):
        return False
    t = datetime.fromtimestamp(now, tz=timezone.utc)
    minute = t.hour * 60 + t.minute
    (sh, sm), (eh, em) = DAILY_BREAK_UTC
    if sh * 60 + sm <= minute < eh * 60 + em:
        return True
    (wd0, h0, m0), (wd1, h1, m1) = WEEKEND_UTC
    week_minute = t.weekday() * 1440 + minute
    return wd0 * 1440 + h0 * 60 + m0 <= week_minute < wd1 * 1440 + h1 * 60 + m1


def classify_stream(age_s, unit_active=None, quiet=False):
    """(status, detail) for one depth stream.

    age_s: seconds since its last event (None = no row in the 24 h lookback).
    unit_active: its collector unit is active (None = no collector unit known).
    quiet: inside the instrument's expected closed window.
    """
    if age_s is None:
        if quiet:
            return WARN, f"no rows in {STREAM_LOOKBACK}, market closed (expected)"
        if unit_active:
            return DOWN, f"collector active but no rows in {STREAM_LOOKBACK}"
        return DOWN, f"no rows in {STREAM_LOOKBACK}"
    if age_s < STREAM_LIVE_S:
        return OK, f"live, last event {human_age(age_s)} ago"
    if age_s <= STREAM_IDLE_S:
        return WARN, f"idle {human_age(age_s)} / market closed?"
    if quiet:
        return WARN, f"quiet {human_age(age_s)}, market closed (expected)"
    return DOWN, f"no rows for {human_age(age_s)}"


def kql_section(sources, names, now):
    """(kql dict, {triple: stream item}). One Kusto round trip."""
    base = {"name": "kql", "database": sources.kql_database or None, "table": sources.kql_table}
    if sources.kusto is None:
        return dict(base, status=UNKNOWN, detail="no KQL client configured", streams=[]), {}
    started = sources.now()
    try:
        rows = call_with_timeout(sources.kusto, KQL_TIMEOUT_S, kql_query(sources.kql_table))
    except Exception as exc:  # noqa: BLE001 - class name only, never the message
        return dict(base, status=DOWN, detail=f"query failed: {type(exc).__name__}",
                    error=type(exc).__name__, latency_s=round(sources.now() - started, 3),
                    streams=[]), {}
    latency = sources.now() - started
    status = OK if latency < KQL_SLOW_S else WARN
    detail = f"query ok in {latency:.2f} s" + ("" if status == OK else " (slow)")
    streams = {}
    for row in rows:
        triple = (sources.broker(str(row.get("broker"))), str(row.get("ctidTraderAccountId")),
                  str(row.get("symbolId")))
        last = parse_time(row.get("last_event"))
        ingested = parse_time(row.get("last_ingested"))
        age = None if last is None else max(0.0, now - last)
        name = names.get(triple)
        st, det = classify_stream(age, None, expected_quiet(name, now))
        streams[triple] = item(
            f"{triple[0]}/{triple[1]}/{triple[2]}" + (f" {name}" if name else ""), st, det,
            broker=triple[0], account=triple[1], symbolId=triple[2], symbol=name,
            rows_15m=_int(row.get("rows_15m")), last_event=iso(last),
            last_event_age_s=None if age is None else round(age, 1),
            ingestion_lag_s=None if last is None or ingested is None else round(ingested - last, 3),
            collector=None,
        )
    kql = dict(base, status=status, detail=detail + f", {len(streams)} streams in {STREAM_LOOKBACK}",
               latency_s=round(latency, 3), streams=list(streams.values()))
    return kql, streams


# --- Collectors -------------------------------------------------------------------

def parse_instance(instance):
    """'icmarketssc-48664595-XAUUSD' -> ('icmarketssc', '48664595', 'XAUUSD'), else None."""
    parts = instance.rsplit("-", 2)
    if len(parts) != 3 or not parts[1].isdigit():
        return None
    return parts[0], parts[1], parts[2]


def _symbol_id_for(names, broker, account, symbol):
    for (b, a, sid), name in names.items():
        if b == broker and a == account and name and name.upper() == symbol.upper():
            return sid
    for (b, a, sid), name in names.items():
        if b == broker and a == account and name and name.upper().split(".")[0] == symbol.upper():
            return sid
    return None


def collectors_section(sources, units, streams, names, kql_ok, env_instances, now):
    out = []
    collector_units = sorted(n for n in units if n.startswith(COLLECTOR_PREFIX))
    for unit in collector_units:
        instance = unit[len(COLLECTOR_PREFIX):-len(".service")]
        configured = (instance in env_instances) if env_instances is not None else None
        u = unit_item(unit, units[unit], now, configured)
        parsed = parse_instance(instance)
        fields = dict(instance=instance, configured=configured,
                      unit_status=u["status"], unit_detail=u["detail"],
                      ActiveState=u["ActiveState"], SubState=u["SubState"],
                      UnitFileState=u["UnitFileState"], since=u["since"], uptime_s=u["uptime_s"],
                      NRestarts=u["NRestarts"], MainPID=u["MainPID"])
        if parsed is None:
            out.append(item(instance, worst([u["status"], UNKNOWN]),
                            u["detail"] + "; instance name not <broker>-<account>-<SYMBOL>", **fields))
            continue
        broker, account, symbol = sources.broker(parsed[0]), parsed[1], parsed[2]
        sid = _symbol_id_for(names, broker, account, symbol)
        fields.update(broker=broker, account=account, symbol=symbol, symbolId=sid)
        active = u["ActiveState"] == "active"
        if u["status"] == NA:
            # Deliberately disabled and stopped: its stream is not expected to flow.
            stream = streams.get((broker, account, sid)) if sid else None
            if stream is not None:
                stream.update(collector=instance, status=NA, detail="collector disabled")
            out.append(item(instance, NA, u["detail"], data_status=NA, **fields))
            continue
        if not kql_ok:
            data_status, data_detail = UNKNOWN, "KQL unavailable"
        elif sid is None:
            data_status, data_detail = UNKNOWN, "symbol not in the catalogue"
        else:
            stream = streams.get((broker, account, sid))
            quiet = expected_quiet(symbol, now)
            if stream is None:
                data_status, data_detail = classify_stream(None, active, quiet)
            else:
                stream["collector"] = instance
                data_status, data_detail = classify_stream(stream["last_event_age_s"], active, quiet)
                stream["status"], stream["detail"] = data_status, data_detail
                fields.update(rows_15m=stream["rows_15m"], last_event=stream["last_event"],
                              last_event_age_s=stream["last_event_age_s"],
                              ingestion_lag_s=stream["ingestion_lag_s"])
        fields["data_status"] = data_status
        out.append(item(instance, worst([u["status"], data_status]),
                        f"unit: {u['detail']}; data: {data_detail}", **fields))
    if env_instances:
        known = {u[len(COLLECTOR_PREFIX):-len(".service")] for u in collector_units}
        for instance in env_instances:
            if instance not in known:
                out.append(item(instance, WARN, "env file present but no unit loaded or enabled",
                                instance=instance, configured=True))
    return out


# --- Autotraders ------------------------------------------------------------------

def _exec_args(exec_start):
    """The argv of a `systemctl show` ExecStart value (used locally, never returned)."""
    match = re.search(r"argv\[\]=(.*?)\s;\s", exec_start or "")
    if not match:
        return []
    try:
        return shlex.split(match.group(1))
    except ValueError:
        return match.group(1).split()


def _option(args, name):
    for i, arg in enumerate(args):
        if arg == name and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def _short_gate(kind, detail):
    """A secret-free summary of a data_gate / kql_error risk event."""
    text = str(detail or "")
    if kind == "data_gate":
        match = re.match(r"^\s*([a-z_]+)\s*->\s*([a-z_]+)", text)
        return f"{match.group(1)} -> {match.group(2)}" if match else "gate changed"
    if kind == "kql_error":
        match = re.match(r"^(slot \S+: \d+ attempts)", text)
        return match.group(1) if match else "kql_error"
    return kind


def autotrader_item(sources, unit, props, now):
    u = unit_item(unit, props, now)
    fields = {k: u[k] for k in ("ActiveState", "SubState", "UnitFileState", "since", "uptime_s",
                                "NRestarts", "MainPID", "Result", "ExecMainStatus")}
    fields.update(unit_status=u["status"], unit_detail=u["detail"])
    statuses, notes = [u["status"]], []
    root = props.get("WorkingDirectory") or ""
    args = _exec_args(props.get("ExecStart"))
    fields["mode"] = _option(args, "--mode")
    if not root.startswith("/"):
        fields.update(state="not readable", journal="not readable")
        return item(unit, worst(statuses), u["detail"] + "; no working directory", **fields)
    config_path = _option(args, "--config") or "config.toml"
    config_path = config_path if config_path.startswith("/") else os.path.join(root, config_path)
    state_dir, log_dir = os.path.join(root, "state"), os.path.join(root, "logs")
    configured_slot = None
    fields["config"] = os.path.basename(config_path)
    try:
        conf = tomllib.loads(sources.read_text(config_path))
        state_dir = os.path.join(root, str(conf.get("state_dir", "state")))
        log_dir = os.path.join(root, str(conf.get("log_dir", "logs")))
        instance = conf.get("instance") if isinstance(conf.get("instance"), dict) else {}
        fields["instance"] = instance.get("name")
        fields["timeframe"] = instance.get("timeframe")
        match = re.fullmatch(r"(\d+)m", str(instance.get("timeframe") or ""))
        configured_slot = int(match.group(1)) * 60 if match else None
    except Exception as exc:  # noqa: BLE001 - defaults stay
        fields["config"] = "not readable"
        notes.append(f"config {type(exc).__name__}")

    # KILL / PAUSE switches.
    try:
        if not os.path.isabs(state_dir):
            raise ValueError
        kill = sources.exists(os.path.join(state_dir, "KILL"))
        pause = sources.exists(os.path.join(state_dir, "PAUSE"))
        fields.update(kill_file=kill, pause_file=pause)
        if kill:
            statuses.append(DOWN)
            notes.append("KILL file present")
        if pause:
            statuses.append(WARN)
            notes.append("PAUSE file present")
    except Exception:  # noqa: BLE001
        fields.update(kill_file="not readable", pause_file="not readable")

    # The journal (read-only SQLite).
    db_path = os.path.join(state_dir, "autotrade.db")
    try:
        run = sources.sqlite(db_path, "SELECT run_id, mode, is_live, started_ms, ended_ms "
                                      "FROM runs ORDER BY run_id DESC LIMIT 1")
        if run:
            run_id, mode, is_live, started_ms, ended_ms = run[0]
            fields.update(run_id=run_id, run_mode=(mode or "").lower() or None,
                          is_live=None if is_live is None else bool(is_live),
                          run_started=iso(started_ms / 1000) if started_ms else None,
                          run_ended=iso(ended_ms / 1000) if ended_ms else None)
            fields["mode"] = fields["mode"] or ((mode or "").lower().replace("_", "-") or None)
        last = sources.sqlite(db_path, "SELECT ts_ms, slot_end_ms, decision FROM signals "
                                       "ORDER BY slot_end_ms DESC, ts_ms DESC LIMIT 1")
        slots = [r[0] for r in sources.sqlite(db_path, "SELECT DISTINCT slot_end_ms FROM signals "
                                                       "ORDER BY slot_end_ms DESC LIMIT 6")]
        gaps = [a - b for a, b in zip(slots, slots[1:]) if a and b and a > b]
        slot_s = configured_slot or ((min(gaps) / 1000) if gaps else AUTOTRADE_DEFAULT_SLOT_S)
        fields["slot_s"] = int(slot_s)
        if last:
            ts_ms, slot_end_ms, decision = last[0]
            age = now - ts_ms / 1000
            fields.update(last_decision=iso(ts_ms / 1000), last_decision_age_s=round(age, 1),
                          last_slot_end=iso(slot_end_ms / 1000) if slot_end_ms else None,
                          last_decision_value=decision)
            if u["ActiveState"] == "active" and age > AUTOTRADE_STALE_SLOTS * slot_s + 60:
                statuses.append(WARN)
                notes.append(f"no decision for {human_age(age)}")
        else:
            fields.update(last_decision=None)
        gate = sources.sqlite(db_path, "SELECT ts_ms, kind, symbol_id, detail FROM risk_events "
                                       "WHERE kind IN ('data_gate', 'kql_error') ORDER BY id DESC LIMIT 1")
        if gate:
            ts_ms, kind, symbol_id, detail = gate[0]
            summary = _short_gate(kind, detail)
            fields["last_data_event"] = {"kind": kind, "at": iso(ts_ms / 1000),
                                         "age_s": round(now - ts_ms / 1000, 1),
                                         "symbolId": symbol_id, "summary": summary}
            gate_now = summary.split("-> ")[-1] if kind == "data_gate" else "kql_error"
            if gate_now != "ok" and now - ts_ms / 1000 < 2 * slot_s:
                statuses.append(WARN)
                notes.append(f"data gate {gate_now}")
        else:
            fields["last_data_event"] = None
        positions = sources.sqlite(db_path, "SELECT to_state FROM transitions WHERE id IN "
                                            "(SELECT max(id) FROM transitions GROUP BY symbol_id)")
        fields["open_positions"] = sum(1 for (state,) in positions if state in POSITION_STATES)
    except Exception as exc:  # noqa: BLE001
        reason = "not readable" if isinstance(exc, (sqlite3.Error, OSError)) else type(exc).__name__
        for key in ("last_decision", "open_positions", "last_data_event"):
            fields.setdefault(key, "not readable")
        fields["journal"] = reason
        notes.append(f"journal {reason}")
    try:
        fields["log_age_s"] = round(now - sources.mtime(os.path.join(log_dir, "autotrade.jsonl")), 1)
    except Exception:  # noqa: BLE001
        fields["log_age_s"] = "not readable"
    detail = u["detail"] + "".join(f"; {n}" for n in notes)
    return item(unit, worst(statuses), detail, **fields)


# --- Cron -------------------------------------------------------------------------

_ASSIGN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_SECRETISH = re.compile(r"(?i)pass|pwd|secret|token|apikey|api_key|key=|auth|credential|bearer")
_REDIRECT = re.compile(r"^\d*(>>|>|<|&>>|&>|>&)(.*)$")
_SKIP_COMMANDS = {"cd", "set", ".", "source", "export", "umask", "test", "[", "true", ":", "unset"}


def parse_cron(text, source, system=True):
    """Relevant (axioma/ctrader) entries of a cron file: [{source, schedule, user, command, env}]."""
    env, entries = {}, []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        assign = _ASSIGN.match(line)
        if assign:
            env[assign.group(1)] = assign.group(2).strip().strip('"\'')
            continue
        fields = 1 if line.startswith("@") else 5
        fields += 1 if system else 0
        parts = line.split(None, fields)
        if len(parts) <= fields:
            continue
        schedule = parts[0] if line.startswith("@") else " ".join(parts[:5])
        user = parts[fields - 1] if system else None
        command = parts[fields]
        if not CRON_RELEVANT.search(command):
            continue
        entries.append({"source": source, "schedule": schedule, "user": user,
                        "command": command, "env": dict(env)})
    return entries


def _expand(text, env):
    return re.sub(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", lambda m: env.get(m.group(1), m.group(0)), text)


def _drop_redirections(words):
    out, skip = [], False
    for word in words:
        if skip:
            skip = False
            continue
        match = _REDIRECT.match(word)
        if match:
            target = match.group(2)
            if not target:
                skip = True        # operator and target are separate words
            continue
        out.append(word)
    return out


def _unwrap(words):
    """Strip env assignments and wrapper commands; returns (words, runs_as)."""
    runs_as = None
    while words:
        head = os.path.basename(words[0])
        if _ASSIGN.match(words[0]) and "/" not in words[0].split("=")[0]:
            words = words[1:]
            continue
        if head == "flock":
            words = words[1:]
            while words and words[0].startswith("-"):
                opt = words.pop(0)
                if opt in ("-w", "--wait", "--timeout", "-E", "--conflict-exit-code") and words:
                    words.pop(0)
            words = words[1:]           # the lock file
            if words and words[0] in ("-c", "--command") and len(words) > 1:
                words = shlex.split(words[1]) if words[1] else []
            continue
        if head in ("runuser", "su", "sudo"):
            words = words[1:]
            while words and words[0].startswith("-") and words[0] != "--":
                opt = words.pop(0)
                if opt in ("-u", "--user", "-g", "--group", "-s", "--shell") and words:
                    value = words.pop(0)
                    if opt in ("-u", "--user"):
                        runs_as = value
                elif opt.startswith("--user="):
                    runs_as = opt.split("=", 1)[1]
                elif opt in ("-c", "--command") and words:
                    words = shlex.split(words[0])
                    break
            if words and words[0] == "--":
                words = words[1:]
            continue
        if head in ("timeout", "nice", "ionice", "env", "nohup", "setsid", "chronic", "exec", "stdbuf"):
            words = words[1:]
            while words and (words[0].startswith("-") or _ASSIGN.match(words[0])):
                opt = words.pop(0)
                if opt in ("-n", "-c", "-s", "-k", "--signal", "--kill-after", "-u") and words:
                    words.pop(0)
            if head == "timeout" and words:
                words = words[1:]      # the duration
            continue
        break
    return words, runs_as


def _short_word(word, previous):
    if ".env" in word or "dotenv" in word.lower():
        return "<redacted>"
    if _SECRETISH.search(previous or "") and previous.startswith("-"):
        return "<redacted>"
    if _SECRETISH.search(word) and ("=" in word or not word.startswith("-")):
        return "<redacted>"
    if word.startswith(("/", "~", "$")) or "/root" in word or "/home/" in word:
        return os.path.basename(word.rstrip("/")) or "<path>"
    if len(word) >= 24 and re.fullmatch(r"[A-Za-z0-9+/=_-]+", word) and re.search(r"\d", word) \
            and re.search(r"[A-Za-z]", word):
        return "<redacted>"
    return word


def sanitize_command(command, env=None):
    """'cd $APP && set -a && . .env && flock ... runuser -u axioma -- $APP/venv/bin/python
    export_flat.py --recent 3 >> log 2>&1' -> ('export_flat.py --recent 3', 'axioma')."""
    runs_as, kept = None, []
    for segment in re.split(r"\s*(?:&&|\|\||;|\|)\s*", command or ""):
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        words = _drop_redirections(words)
        if not words or os.path.basename(words[0]) in _SKIP_COMMANDS:
            continue
        words, user = _unwrap(words)
        runs_as = user or runs_as
        words = _drop_redirections(words)
        if not words or os.path.basename(words[0]) in _SKIP_COMMANDS:
            continue
        if re.fullmatch(r"python[0-9.]*|bash|sh|dash", os.path.basename(words[0])) and len(words) > 1:
            words = words[1:]
            while words and words[0] in ("-u", "-B", "-E", "-I", "-s", "-e"):
                words = words[1:]
        out, previous = [], ""
        for word in words:
            out.append(_short_word(word, previous))
            previous = word
        if out:
            kept.append(" ".join(out))
    return " | ".join(kept)[:160], runs_as


def _log_path(command, env):
    match = re.search(r">>?\s*(\S+\.log)\b", command or "")
    return _expand(match.group(1), env) if match else None


def job_for(command):
    """The CronJob a command runs, or None."""
    if CRON_SCRIPT not in (command or ""):
        return None
    match = re.search(r"--timeframe[ =](\S+)", command)
    timeframe = match.group(1) if match else "5m"
    return next((job for job in CRON_JOBS if job.timeframe == timeframe), None)


def classify_freshness(age_s, job):
    if age_s is None:
        return DOWN, "table empty or missing"
    if age_s > job.down_s:
        return DOWN, f"{job.table} last written {human_age(age_s)} ago (stale)"
    if age_s > job.warn_s:
        return WARN, f"{job.table} last written {human_age(age_s)} ago (late)"
    return OK, f"{job.table} written {human_age(age_s)} ago"


def _log_info(sources, path, now):
    if not path:
        return {"log": None}
    info = {"log": os.path.basename(path)}
    try:
        mtime = sources.mtime(path)
        tail = sources.read_text(path, 4096)
    except Exception:  # noqa: BLE001
        info.update(last_log="not readable", last_wrote="not readable")
        return info
    lines = [line for line in tail.splitlines() if line.strip()]
    info["last_log"] = iso(mtime)
    info["last_log_age_s"] = round(now - mtime, 1)
    last = lines[-1] if lines else ""
    match = re.match(r"^wrote (\w+) rows=(\d+)", last)
    # The log has no timestamps: a run that ends on "wrote ..." is the file's last
    # write, so its mtime is the time of the last successful write.
    info["last_wrote"] = iso(mtime) if match else None
    info["last_wrote_rows"] = int(match.group(2)) if match else None
    # ERR lines of the latest run (since the previous "wrote" line); count only.
    errors = 0
    for line in reversed(lines[:-1] if match else lines):
        if line.startswith("wrote "):
            break
        if line.startswith("ERR"):
            errors += 1
    info["last_run_errors"] = errors
    return info


def table_age(sources, table, now):
    rows = sources.sqlite(sources.app_db, f"SELECT max(computed_at) FROM {table}")
    stamp = parse_time(rows[0][0]) if rows else None
    return (None, None) if stamp is None else (iso(stamp), round(now - stamp, 1))


def cron_section(sources, units, now):
    props = units.get("cron.service")
    service = unit_item("cron.service", props, now) if props else item("cron.service", UNKNOWN, "unit not shown")
    cron_active = service["status"] in (OK, WARN) and service.get("ActiveState") == "active"
    entries, sources_read = [], []
    files = [(sources.paths.crontab, True)]
    try:
        files += [(os.path.join(sources.paths.cron_d, n), True) for n in sorted(sources.listdir(sources.paths.cron_d))
                  if not n.startswith(".") and re.match(r"^[A-Za-z0-9_-]+$", n)]
    except Exception as exc:  # noqa: BLE001
        sources_read.append({"path": sources.paths.cron_d, "readable": False, "error": type(exc).__name__})
    try:
        files += [(os.path.join(sources.paths.cron_spool, n), False)
                  for n in sorted(sources.listdir(sources.paths.cron_spool))]
    except Exception:  # noqa: BLE001
        sources_read.append({"path": sources.paths.cron_spool, "readable": False})
    for path, system in files:
        try:
            text = sources.read_text(path, 64 * 1024)
        except Exception:  # noqa: BLE001
            sources_read.append({"path": path, "readable": False})
            continue
        found = parse_cron(text, path, system)
        sources_read.append({"path": path, "readable": True, "relevant_lines": len(found)})
        entries.extend(found)

    jobs, matched = [], set()
    for entry in entries:
        job = job_for(entry["command"])
        command, runs_as = sanitize_command(entry["command"], entry["env"])
        base = dict(schedule=entry["schedule"], user=entry["user"], runs_as=runs_as,
                    command=command, source=entry["source"])
        if job is None or job.table in matched:
            jobs.append(item(command or "cron job", NA, "no freshness check for this job", **base))
            continue
        matched.add(job.table)
        base.update(table=job.table, expected_interval_s=job.interval_s,
                    warn_after_s=job.warn_s, down_after_s=job.down_s)
        base.update(_log_info(sources, _log_path(entry["command"], entry["env"]), now))
        try:
            written, age = table_age(sources, job.table, now)
            base.update(table_written=written, table_age_s=age)
            status, detail = classify_freshness(age, job)
        except Exception as exc:  # noqa: BLE001
            status, detail = UNKNOWN, f"{job.table}: {type(exc).__name__}"
            base.update(table_written="not readable", table_age_s=None)
        if not cron_active:
            status, detail = DOWN, "cron.service not active; " + detail
        jobs.append(item(job.table, status, detail, **base))
    for job in CRON_JOBS:
        if job.table not in matched:
            jobs.append(item(job.table, NA, "not scheduled in cron", table=job.table,
                             expected_interval_s=job.interval_s))
    status = worst([service["status"]] + [j["status"] for j in jobs])
    detail = f"cron.service {service['detail']}; {sum(1 for j in jobs if j.get('schedule'))} relevant job(s)"
    return {"name": "cron", "status": status, "detail": detail, "service": service,
            "jobs": jobs, "sources": sources_read}


# --- App --------------------------------------------------------------------------

def app_section(sources, units, now):
    out = []
    for unit in APP_UNITS:
        props = units.get(unit)
        out.append(unit_item(unit, props, now) if props else item(unit, UNKNOWN, "unit not shown"))
    try:
        rows = sources.sqlite(sources.app_db, "SELECT count(*) FROM sqlite_master")
        out.append(item("record store", OK, f"readable ({rows[0][0]} schema objects)",
                        path=os.path.basename(sources.app_db)))
    except Exception as exc:  # noqa: BLE001
        out.append(item("record store", DOWN, f"not readable: {type(exc).__name__}",
                        path=os.path.basename(sources.app_db)))
    return out


# --- Collection -------------------------------------------------------------------

def collect(sources):
    """The whole status payload. Never raises: a failing part becomes `unknown`."""
    now = sources.now()
    notes = []
    try:
        names, env_instances, discover_notes = discover(sources)
        notes += discover_notes
    except Exception as exc:  # noqa: BLE001
        names, env_instances = [], None
        notes.append(f"discovery: {type(exc).__name__}")
    units, units_error = {}, None
    try:
        units = show_units(sources, list(names) + list(APP_UNITS))
    except Exception as exc:  # noqa: BLE001
        units_error = exc
    try:
        symbol_names = sources.symbol_names() or {}
    except Exception as exc:  # noqa: BLE001
        symbol_names = {}
        notes.append(f"symbol catalogue: {type(exc).__name__}")

    sections = {}

    def guarded(key, build, as_list):
        try:
            sections[key] = build()
        except Exception as exc:  # noqa: BLE001
            unknown = failed(key, exc)
            sections[key] = [unknown] if as_list else dict(unknown)

    def need_units():
        if units_error is not None:
            raise units_error

    def units_list():
        need_units()
        out = []
        for name in names:
            if name in units:
                instance = name[len(COLLECTOR_PREFIX):-len(".service")] if name.startswith(COLLECTOR_PREFIX) else None
                configured = (instance in env_instances) if instance and env_instances is not None else None
                out.append(unit_item(name, units[name], now, configured))
        return out

    guarded("units", units_list, True)
    streams, kql_ok = {}, False
    try:
        kql, streams = kql_section(sources, symbol_names, now)
        kql_ok = kql["status"] in (OK, WARN)
        sections["kql"] = kql
    except Exception as exc:  # noqa: BLE001
        sections["kql"] = dict(failed("kql", exc), streams=[])

    def collectors():
        need_units()
        return collectors_section(sources, units, streams, symbol_names, kql_ok, env_instances, now)

    def autotraders():
        need_units()
        return [autotrader_item(sources, n, units[n], now)
                for n in sorted(units) if n.startswith(AUTOTRADE_PREFIX)]

    def cron():
        need_units()
        return cron_section(sources, units, now)

    def app():
        need_units()
        return app_section(sources, units, now)

    guarded("collectors", collectors, True)
    guarded("autotraders", autotraders, True)
    guarded("cron", cron, False)
    guarded("app", app, True)
    # Streams in KQL that no collector unit claims keep their own status.
    if "streams" in sections["kql"]:
        sections["kql"]["streams"] = sorted(sections["kql"]["streams"], key=lambda s: s["name"])

    statuses = []
    for key, value in sections.items():
        if isinstance(value, list):
            statuses += [v["status"] for v in value]
        else:
            statuses.append(value["status"])
            statuses += [s["status"] for s in value.get("streams", [])]
            statuses += [j["status"] for j in value.get("jobs", [])]
    return {"checked_at": iso(now), "overall": rollup(statuses), "sections": sections,
            "notes": notes}


def scrub(payload, secrets):
    """Replace every known secret value anywhere in the payload (defence in depth)."""
    secrets = [s for s in secrets if s and len(s) >= 6]
    if not secrets:
        return payload

    def clean(value):
        if isinstance(value, str):
            for secret in secrets:
                if secret in value:
                    value = value.replace(secret, "[redacted]")
            return value
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        return value

    return clean(payload)


def failure_payload(now, exc):
    return {"checked_at": iso(now), "overall": WARN, "notes": [f"collection failed: {type(exc).__name__}"],
            "sections": {key: [failed(key, exc)] for key in ("units", "collectors", "autotraders", "app")}
            | {"kql": dict(failed("kql", exc), streams=[]), "cron": dict(failed("cron", exc), jobs=[])}}


class StatusCache:
    """One collection per `ttl` seconds; concurrent callers wait for it and share it."""

    def __init__(self, ttl=CACHE_TTL_S, clock=time.monotonic):
        self.ttl, self.clock = ttl, clock
        self._lock = threading.Lock()
        self._value, self._at = None, None

    def get(self, build):
        with self._lock:
            now = self.clock()
            if self._value is not None and now - self._at < self.ttl:
                return self._value
            self._value = build()
            self._at = self.clock()
            return self._value
