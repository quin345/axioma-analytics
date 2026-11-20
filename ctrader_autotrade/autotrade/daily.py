"""Daily executed-trades analytics: one report per UTC day and instance (README "Daily trades report").

`autotrade trades-report` reads ONE instance's journal read-only and writes `<out-dir>/YYYY-MM-DD.json`
(schema_version 1), `index.json` (instance meta + the day list with headline numbers + the kill switch
as of generation) and `latest.json` (a copy of the newest completed day). The web app (webapp/trade_reports.py)
only reads these files.

Definitions:
- Days are plain UTC days (integer ms arithmetic, no time zones). A trade belongs to the day of its exit_ms.
- Only trades of runs with mode DEMO are real executed trades. dry_run trades (the simulator) are excluded
  from every number and only counted in `summary.excluded_dry_run_trades`.
- Money columns of `trades` (pnl_net, gross, commission, swap) are ALREADY divided by 10**money_digits when
  the bot writes the trade (execution._finalize), so they are account currency as stored; money_digits is
  reported for information and never applied again.
- win = net P&L > 0 (pnl_net, else price-based bp, else mid-based bp), loss = < 0, flat = 0.
- bp = pnl_bps_price (fill to fill), with pnl_bps_mid (signal mid to exit mid) as the fallback.
- A report is written for a day when the instance ran that day (a run overlapped it) or had trades; an
  explicit --date / --last-day also writes a zero-trade report for any day from the first run on, so the
  history a cron job builds has no holes.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import statistics
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from . import report as _report
from . import timeframe as _tf

SCHEMA_VERSION = 1
DAY_MS = 86_400_000
MAX_TRADE_ROWS = 500
MAX_EVENT_DETAILS = 5
DETAIL_CHARS = 200
DEFAULT_OUT_ROOT = Path(__file__).resolve().parent.parent / "reports"
# Research taker net bp/trade per timeframe: report.REFS (DESIGN.md section 1, docs/findings-1m-screen.md).
RESEARCH_REFS = _report.REFS
# Fallback names when a journal has no client_order_id to read the symbol from (account 48664595).
KNOWN_SYMBOLS = {41: "XAUUSD", 10026: "BTCUSD"}
Z_BUCKETS = (("<1", 0.0, 1.0), ("1-1.5", 1.0, 1.5), ("1.5-2", 1.5, 2.0), (">2", 2.0, math.inf))
_DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
_SECRET_TEXT = re.compile(r"(?i)\b(access_?token|refresh_?token|client_?secret|client_?id|token|secret|password)"
                          r"\s*[=:]\s*\S+")
_PATH_TEXT = re.compile(r"/(?:root|etc|home|var|usr|opt|tmp|run)/\S*")
_COID_SYMBOL = re.compile(r"(?:^|-)([A-Za-z0-9._]+)-\d{9,}-[A-Za-z]+$")


# ---- small helpers ----

def _clean(x, nd: int = 6):
    """Finite float rounded, else None (JSON has no NaN)."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, nd)


def _iso(ms: Optional[int]) -> Optional[str]:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_bounds(day: date) -> tuple[int, int]:
    """[start, end) of a UTC calendar day in epoch ms."""
    s = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)
    return s, s + DAY_MS


def _hour(ms: int) -> int:
    return ms // 3_600_000 % 24


def day_of(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date()


def _scrub_text(text) -> str:
    s = _SECRET_TEXT.sub(lambda m: m.group(1) + "=***", str(text or ""))
    s = _PATH_TEXT.sub("<path>", s)
    return s[:DETAIL_CHARS]


def open_ro(db_path) -> sqlite3.Connection:
    """Read-only connection; row_factory = Row."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---- runs ----

def _runs(conn, now_ms: int) -> list[dict]:
    """Every run with an effective end: ended_ms, else its last journalled activity (a crashed run has no
    ended_ms), never before its start."""
    out = []
    for r in conn.execute("SELECT run_id, mode, started_ms, ended_ms FROM runs ORDER BY run_id"):
        end = r["ended_ms"]
        if end is None:
            last = [r["started_ms"]]
            for sql in ("SELECT MAX(ts_ms) FROM signals WHERE run_id=?", "SELECT MAX(exit_ms) FROM trades WHERE run_id=?",
                        "SELECT MAX(ts_ms) FROM risk_events WHERE run_id=?"):
                v = conn.execute(sql, (r["run_id"],)).fetchone()[0]
                if v:
                    last.append(v)
            end = min(max(last), now_ms)
        out.append({"run_id": r["run_id"], "mode": str(r["mode"] or "").upper(), "start": r["started_ms"] or 0,
                    "end": max(end or 0, r["started_ms"] or 0)})
    return out


def _first_activity_ms(conn, runs) -> Optional[int]:
    cands = [r["start"] for r in runs]
    v = conn.execute("SELECT MIN(MIN(entry_ms), MIN(exit_ms)) FROM trades").fetchone()[0]
    if v:
        cands.append(v)
    return min(cands) if cands else None


def enumerate_days(conn, now_ms: int) -> list[date]:
    """Every UTC day up to yesterday on which a run was active or a trade closed (--backfill)."""
    today = day_of(now_ms)
    days = set()
    for r in _runs(conn, now_ms):
        d = day_of(r["start"])
        while d <= day_of(r["end"]):
            days.add(d)
            d += timedelta(days=1)
    for (ms,) in conn.execute("SELECT exit_ms FROM trades WHERE exit_ms IS NOT NULL"):
        days.add(day_of(ms))
    return sorted(d for d in days if d < today)


# ---- symbol names ----

def _symbol_names(conn) -> dict[int, str]:
    names: dict[int, str] = {}
    for table in ("trades", "orders"):
        for r in conn.execute(f"SELECT symbol_id, client_order_id FROM {table} WHERE client_order_id IS NOT NULL"):
            m = _COID_SYMBOL.search(r["client_order_id"] or "")
            if m and r["symbol_id"] not in names:
                names[r["symbol_id"]] = m.group(1)
    for sid, nm in KNOWN_SYMBOLS.items():
        names.setdefault(sid, nm)
    return names


# ---- per-trade rows ----

def _trade_rows(conn, d0: int, d1: int, names: dict, full_volume: dict) -> list[dict]:
    q = ("SELECT t.*, s.z_poc AS z_poc, s.events_z AS events_z, s.spread AS sig_spread, "
         "s.taker_pnl_bps AS sim_bp FROM trades t "
         "JOIN runs r ON r.run_id=t.run_id AND UPPER(r.mode)='DEMO' "
         "LEFT JOIN signals s ON s.signal_id=t.signal_id "
         "WHERE t.exit_ms>=? AND t.exit_ms<? ORDER BY t.exit_ms, t.id")
    rows = []
    for r in conn.execute(q, (d0, d1)):
        bp_price, bp_mid = r["pnl_bps_price"], r["pnl_bps_mid"]
        bp = bp_price if bp_price is not None else bp_mid
        net = r["pnl_net"]
        outcome = net if net is not None else bp
        sid = r["symbol_id"]
        full = full_volume.get(sid)
        spread_bp = None
        if r["sig_spread"] is not None and r["entry_price"]:
            spread_bp = r["sig_spread"] / r["entry_price"] * 1e4
        rows.append({
            "id": r["id"], "symbol_id": sid, "symbol": names.get(sid, f"#{sid}"), "side": r["side"],
            "volume": r["volume"] or 0, "entry_ms": r["entry_ms"], "exit_ms": r["exit_ms"],
            "entry_price": r["entry_price"], "exit_price": r["exit_price"], "reason": r["reason"] or "other",
            "bp": bp, "bp_price": bp_price, "bp_mid": bp_mid, "net": net, "gross": r["gross"],
            "commission": r["commission"], "swap": r["swap"], "money_digits": r["money_digits"],
            "outcome": outcome, "adverse": r["adverse_bps"], "markout": r["markout_bps"],
            "z": None if r["z_poc"] is None else abs(r["z_poc"]), "linked": r["z_poc"] is not None,
            "sim_bp": r["sim_bp"], "spread_bp": spread_bp,
            "hold_s": None if r["entry_ms"] is None or r["exit_ms"] is None else (r["exit_ms"] - r["entry_ms"]) / 1000,
            "size": "half" if full and (r["volume"] or 0) < full else "full"})
    return rows


def _full_volumes(conn) -> dict[int, int]:
    """Largest volume ever traded per symbol by DEMO runs = the full size; anything smaller is a half size."""
    q = ("SELECT t.symbol_id, MAX(t.volume) FROM trades t JOIN runs r ON r.run_id=t.run_id "
         "AND UPPER(r.mode)='DEMO' GROUP BY t.symbol_id")
    return {sid: v for sid, v in conn.execute(q)}


# ---- statistics ----

def _mean(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def _median(v):
    v = [x for x in v if x is not None]
    return statistics.median(v) if v else None


def _t_stat(v):
    v = [x for x in v if x is not None]
    if len(v) < 3:
        return None
    sd = statistics.stdev(v)
    return None if sd == 0 else statistics.fmean(v) / (sd / math.sqrt(len(v)))


def _mini(rows: list[dict]) -> dict:
    """Compact stats of a group of trades (breakdown tables)."""
    n = len(rows)
    wins = sum(1 for r in rows if r["outcome"] is not None and r["outcome"] > 0)
    nets = [r["net"] for r in rows if r["net"] is not None]
    return {"trades": n, "wins": wins, "win_rate": _clean(wins / n) if n else None,
            "net": _clean(sum(nets)), "avg_bp": _clean(_mean([r["bp"] for r in rows])),
            "volume": sum(r["volume"] for r in rows)}


def _group(rows, keyfn, order=None) -> dict:
    g: dict = {}
    for r in rows:
        g.setdefault(keyfn(r), []).append(r)
    keys = sorted(g) if order is None else [k for k in order if k in g] + sorted(k for k in g if k not in order)
    return {str(k): _mini(g[k]) for k in keys}


def _max_drawdown(rows) -> float:
    cum = peak = dd = 0.0
    for r in rows:
        cum += r["net"] or 0.0
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def _z_bucket(z):
    if z is None:
        return "unlinked"
    for name, lo, hi in Z_BUCKETS:
        if lo <= z < hi:
            return name
    return ">2"


def _summary(rows: list[dict], dry_run_excluded: int) -> dict:
    n = len(rows)
    out = {"trades": n, "excluded_dry_run_trades": dry_run_excluded}
    outc = [r["outcome"] for r in rows]
    wins = [r for r in rows if r["outcome"] is not None and r["outcome"] > 0]
    losses = [r for r in rows if r["outcome"] is not None and r["outcome"] < 0]
    win_net = [r["net"] for r in wins if r["net"] is not None]
    loss_net = [r["net"] for r in losses if r["net"] is not None]
    nets = [r["net"] for r in rows if r["net"] is not None]
    holds = [r["hold_s"] for r in rows if r["hold_s"] is not None]
    ranked = [r for r in rows if r["net"] is not None]
    best = max(ranked, key=lambda r: r["net"], default=None)
    worst = min(ranked, key=lambda r: r["net"], default=None)

    def pick(r):
        return None if r is None else {"net": _clean(r["net"]), "bp": _clean(r["bp"]), "symbol": r["symbol"],
                                       "side": r["side"], "exit": _iso(r["exit_ms"])}
    out.update({
        "wins": len(wins), "losses": len(losses), "flat": n - len(wins) - len(losses) - sum(o is None for o in outc),
        "win_rate": _clean(len(wins) / n) if n else None,
        "net": _clean(sum(nets)) if n else 0.0,
        "gross": _clean(sum(r["gross"] for r in rows if r["gross"] is not None)) if n else 0.0,
        "commission": _clean(sum(r["commission"] for r in rows if r["commission"] is not None)) if n else 0.0,
        "swap": _clean(sum(r["swap"] for r in rows if r["swap"] is not None)) if n else 0.0,
        "net_missing": sum(1 for r in rows if r["net"] is None),
        "avg_bp_price": _clean(_mean([r["bp_price"] for r in rows])),
        "median_bp_price": _clean(_median([r["bp_price"] for r in rows])),
        "avg_bp_mid": _clean(_mean([r["bp_mid"] for r in rows])),
        "median_bp_mid": _clean(_median([r["bp_mid"] for r in rows])),
        "t_stat_bp": _clean(_t_stat([r["bp"] for r in rows])),     # null below 3 trades (or zero variance)
        "avg_win": _clean(_mean(win_net)), "avg_loss": _clean(_mean(loss_net)),
        "profit_factor": _clean(sum(win_net) / -sum(loss_net)) if loss_net and sum(loss_net) < 0 else None,
        "best_trade": pick(best), "worst_trade": pick(worst),
        "max_drawdown": _clean(_max_drawdown(rows)),
        "avg_hold_s": _clean(_mean(holds), 1), "max_hold_s": _clean(max(holds), 1) if holds else None,
        "volume": sum(r["volume"] for r in rows),
        "stop_share": _clean(sum(1 for r in rows if r["reason"] == "stop") / n) if n else None,
        "money_digits": sorted({r["money_digits"] for r in rows if r["money_digits"] is not None})})
    return out


def _execution_quality(conn, rows, d0, d1) -> dict:
    slip = [r["bp"] - r["sim_bp"] for r in rows if r["bp"] is not None and r["sim_bp"] is not None]
    orders = {}
    errors = {}
    n_orders = rejects = 0
    q = ("SELECT o.status, o.error_code FROM orders o JOIN runs r ON r.run_id=o.run_id AND UPPER(r.mode)='DEMO' "
         "WHERE o.ts_ms>=? AND o.ts_ms<?")
    for r in conn.execute(q, (d0, d1)):
        n_orders += 1
        st = str(r["status"] or "none")
        orders[st] = orders.get(st, 0) + 1
        bad = r["error_code"] is not None or "REJECT" in st.upper() or "ERROR" in st.upper()
        if bad:
            rejects += 1
            k = str(r["error_code"] or st)
            errors[k] = errors.get(k, 0) + 1
    return {
        "slippage_bp": {"n": len(slip), "mean": _clean(_mean(slip)), "median": _clean(_median(slip)),
                        "note": "realised bp minus the signal's simulated taker bp (touch at t to touch at t+1); "
                                "stop and early exits differ from the simulation by construction"},
        "avg_adverse_bp": _clean(_mean([r["adverse"] for r in rows])),
        "avg_markout_bp": _clean(_mean([r["markout"] for r in rows])),
        "avg_spread_entry_bp": _clean(_mean([r["spread_bp"] for r in rows])),
        "orders": n_orders, "order_status": orders, "rejects_or_errors": rejects, "error_codes": errors}


def _funnel(conn, d0, d1, entry_z: float) -> dict:
    """The day's signal rows (one per bar and symbol) from DEMO runs, by what became of them."""
    q = ("SELECT s.z_poc, s.decision, s.reason FROM signals s JOIN runs r ON r.run_id=s.run_id "
         "AND UPPER(r.mode)='DEMO' WHERE s.ts_ms>=? AND s.ts_ms<?")
    rows = conn.execute(q, (d0, d1)).fetchall()
    sig = [r for r in rows if r["z_poc"] is not None and abs(r["z_poc"]) > entry_z]
    entered = sum(1 for r in sig if r["decision"] == "enter")
    rollover = sum(1 for r in sig if r["reason"] == "rollover")
    none = sum(1 for r in sig if r["reason"] == "no_signal")
    gated: dict[str, int] = {}
    for r in sig:
        if r["decision"] != "enter" and r["reason"] not in ("rollover", "no_signal"):
            k = str(r["reason"] or r["decision"] or "none")
            gated[k] = gated.get(k, 0) + 1
    return {"entry_z": entry_z, "signal_rows": len(rows), "above_entry_z": len(sig), "entered": entered,
            "rollover": rollover, "gated": sum(gated.values()), "gated_reasons": dict(sorted(gated.items())),
            "none": none}


def _risk_events(conn, d0, d1) -> dict:
    q = ("SELECT e.kind, e.ts_ms, e.detail FROM risk_events e JOIN runs r ON r.run_id=e.run_id "
         "AND UPPER(r.mode)='DEMO' WHERE e.ts_ms>=? AND e.ts_ms<? ORDER BY e.ts_ms, e.id")
    out: dict[str, dict] = {}
    for r in conn.execute(q, (d0, d1)):
        e = out.setdefault(r["kind"], {"count": 0, "last": []})
        e["count"] += 1
        e["last"].append({"ts": _iso(r["ts_ms"]), "detail": _scrub_text(r["detail"])})
        e["last"] = e["last"][-MAX_EVENT_DETAILS:]
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["count"]))


def _cumulative(conn, d1: int) -> dict:
    q = ("SELECT COUNT(*), SUM(CASE WHEN COALESCE(t.pnl_net,t.pnl_bps_price,t.pnl_bps_mid)>0 THEN 1 ELSE 0 END), "
         "COALESCE(SUM(t.pnl_net),0), AVG(COALESCE(t.pnl_bps_price,t.pnl_bps_mid)), MIN(t.exit_ms) "
         "FROM trades t JOIN runs r ON r.run_id=t.run_id AND UPPER(r.mode)='DEMO' WHERE t.exit_ms<?")
    n, wins, net, avg_bp, first = conn.execute(q, (d1,)).fetchone()
    return {"since": _iso(first), "trades": n, "wins": wins or 0, "win_rate": _clean(wins / n) if n else None,
            "net": _clean(net), "avg_bp": _clean(avg_bp)}


def _kill_numbers(conn, settings, now_ms: int) -> dict:
    """The kill-switch section of `autotrade report`, flattened. n/a (None) where unknown."""
    try:
        k = _report._kill_section(conn, settings, now_ms)
    except Exception as exc:                      # a state file we cannot read must not sink the report
        return {"available": False, "reason": f"{type(exc).__name__}"}
    if not k:
        return {"available": False, "reason": "no settings"}
    unreal = k["unrealised"]
    cum = None if k["realised"] is None else k["realised"] + (unreal or 0.0)
    today = None if k["realised_today"] is None else k["realised_today"] + (unreal or 0.0)
    return {
        "available": True, "as_of": _iso(now_ms), "enabled": k["enabled"], "mode": k["mode"],
        "baseline_since": _iso(k["since_ms"]), "baseline_source": k["baseline_source"],
        "realised": _clean(k["realised"]), "unrealised": _clean(unreal),
        "unrealised_note": None if unreal is not None else "bot not running or no recent kill_status",
        "cumulative": _clean(cum), "max_loss": k["max_loss"], "profit_target": k["profit_target"],
        "profit_action": k["profit_action"],
        "distance_to_max_loss": None if cum is None else _clean(cum + k["max_loss"]),
        "today": _clean(today), "daily_max_loss": k["daily_max_loss"],
        "distance_to_daily_limit": None if today is None or not k["daily_max_loss"]
        else _clean(today + k["daily_max_loss"]),
        "day_started": _iso(k["day_start_ms"]), "next_reset": _iso(k["next_reset_ms"]),
        "kill_file": k["kill_file"], "pause_file": k["pause_file"]}


_KILL_NA = {"available": False, "reason": "as-of-now numbers are attached to the newest reports only"}


# ---- one day ----

def _entry_z(conn, settings, d1: int) -> float:
    row = conn.execute("SELECT config_json FROM runs WHERE started_ms<? ORDER BY run_id DESC LIMIT 1", (d1,)).fetchone()
    try:
        return float(json.loads(row[0])["entry_z"]) if row else float(settings.entry_z)
    except (ValueError, KeyError, TypeError):
        return float(settings.entry_z)


def compute_day(conn, settings, day: date, now_ms: Optional[int] = None, kill: Optional[dict] = None,
                names: Optional[dict] = None) -> dict:
    """The full report dict of one UTC day. `day` == today (or later than now) makes it partial."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    d0, d1 = day_bounds(day)
    partial = d1 > now_ms
    upto = min(d1, now_ms + 1) if partial else d1     # a partial day ends at generation
    tf = _tf.of(settings)
    names = names if names is not None else _symbol_names(conn)
    rows = _trade_rows(conn, d0, upto, names, _full_volumes(conn))
    dry = conn.execute("SELECT COUNT(*) FROM trades t JOIN runs r ON r.run_id=t.run_id AND UPPER(r.mode)<>'DEMO' "
                       "WHERE t.exit_ms>=? AND t.exit_ms<?", (d0, upto)).fetchone()[0]
    runs = [r for r in _runs(conn, now_ms) if r["start"] < d1 and r["end"] >= d0]
    ref = RESEARCH_REFS.get(tf.key, {})
    summary = _summary(rows, dry)
    entry_z = _entry_z(conn, settings, d1)
    cum = _cumulative(conn, upto)
    res_net = ref.get("taker_net_bps")
    shown = rows[:MAX_TRADE_ROWS]
    trades = [{"entry": _iso(r["entry_ms"]), "exit": _iso(r["exit_ms"]), "symbol": r["symbol"], "side": r["side"],
               "volume": r["volume"], "entry_price": _clean(r["entry_price"], 8), "exit_price": _clean(r["exit_price"], 8),
               "reason": r["reason"], "bp": _clean(r["bp"], 3), "net": _clean(r["net"]), "z_poc": _clean(r["z"], 3),
               "hold_s": _clean(r["hold_s"], 1), "size": r["size"]} for r in shown]
    return {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "instance": settings.instance_name, "timeframe": tf.key, "strategy": tf.poc_key,
            "labels": sorted(settings.own_labels), "date": day.isoformat(), "generated_at": _iso(now_ms),
            "day_start": _iso(d0), "day_end": _iso(d1), "partial": partial,
            "as_of": _iso(upto - 1) if partial else None,
            "runs": {"count": len(runs), "modes": sorted({r["mode"] for r in runs}),
                     "restarts": max(0, len(runs) - 1), "started_in_day": sum(1 for r in runs if r["start"] >= d0)},
            "journal": "autotrade.db",
            "note": "DEMO-run trades only; trades belong to the UTC day of their exit"
                    + ("; partial day, numbers so far" if partial else "")},
        "summary": summary,
        "breakdown": {
            "by_symbol": _group(rows, lambda r: r["symbol"]),
            "by_side": _group(rows, lambda r: r["side"] or "?"),
            "by_exit_reason": _group(rows, lambda r: r["reason"]),
            "by_hour_utc": _group(rows, lambda r: f"{_hour(r['exit_ms']):02d}"),
            "by_abs_z": _group(rows, lambda r: _z_bucket(r["z"]), order=[b[0] for b in Z_BUCKETS] + ["unlinked"]),
            "by_size": _group(rows, lambda r: r["size"], order=["full", "half"])},
        "execution_quality": _execution_quality(conn, rows, d0, upto),
        "funnel": _funnel(conn, d0, upto, entry_z),
        "cumulative": dict(cum, research_taker_net_bp=res_net,
                           research_source="DESIGN.md section 1 / docs/findings-1m-screen.md (report.REFS)",
                           vs_research_bp=None if cum["avg_bp"] is None or res_net is None
                           else _clean(cum["avg_bp"] - res_net)),
        "research_expectation": {"taker_net_bp_per_trade": res_net, "taker_gross_bp_per_trade": ref.get("taker_gross_bps"),
                                 "source": "DESIGN.md section 1 / docs/findings-1m-screen.md (report.REFS)"},
        "kill_switch": kill if kill is not None else _KILL_NA,
        "trades": trades,
        "trades_total": len(rows),
        "trades_truncated": len(rows) > MAX_TRADE_ROWS,
        "risk_events": _risk_events(conn, d0, upto)}


# ---- files ----

def _atomic_write(path: Path, data: dict) -> None:
    """Temp file in the same directory, chmod 0644, rename over the target (readers never see half a file)."""
    text = json.dumps(data, separators=(",", ":"), allow_nan=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ensure_dir(out_dir: Path) -> None:
    """Create the directory (and the reports/ parent if new) readable by the web user, whatever the umask."""
    made = [p for p in (out_dir.parent, out_dir) if not p.exists()]
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in made:
        os.chmod(p, 0o755)


def _headline(d: dict) -> dict:
    s, c, m = d["summary"], d["cumulative"], d["meta"]
    return {"date": m["date"], "partial": m["partial"], "trades": s["trades"], "wins": s["wins"], "losses": s["losses"],
            "win_rate": s["win_rate"], "net": s["net"], "avg_bp": s["avg_bp_price"],
            "cum_net": c["net"], "cum_trades": c["trades"], "cum_win_rate": c["win_rate"], "cum_avg_bp": c["avg_bp"],
            "runs": m["runs"]["count"], "restarts": m["runs"]["restarts"]}


def write_day(out_dir: Path, report: dict) -> Path:
    ensure_dir(out_dir)
    path = out_dir / f"{report['meta']['date']}.json"
    _atomic_write(path, report)
    return path


def write_index(out_dir: Path, settings, now_ms: int, kill: Optional[dict]) -> dict:
    """Rebuild index.json from the day files present; latest.json = the newest completed day."""
    ensure_dir(out_dir)
    days, newest = [], None
    for p in sorted(out_dir.iterdir()):
        if not _DAY_FILE.match(p.name):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("schema_version") != SCHEMA_VERSION:
                continue
            days.append(_headline(d))
            if not d["meta"]["partial"]:
                newest = d
        except (OSError, ValueError, KeyError):
            continue
    tf = _tf.of(settings)
    ref = RESEARCH_REFS.get(tf.key, {})
    index = {"schema_version": SCHEMA_VERSION, "instance": settings.instance_name, "timeframe": tf.key,
             "strategy": tf.poc_key, "labels": sorted(settings.own_labels), "generated_at": _iso(now_ms),
             "research_taker_net_bp": ref.get("taker_net_bps"),
             "first_day": days[0]["date"] if days else None, "latest": newest["meta"]["date"] if newest else None,
             "kill_switch": kill if kill is not None else _KILL_NA, "days": days}
    _atomic_write(out_dir / "index.json", index)
    if newest is not None:
        _atomic_write(out_dir / "latest.json", newest)
    return index


def resolve_days(conn, args_date: Optional[str], last_day: bool, backfill: bool, today: bool,
                 now_ms: int) -> list[date]:
    """The days a CLI invocation asks for (see eligibility in the module docstring)."""
    runs = _runs(conn, now_ms)
    first = _first_activity_ms(conn, runs)
    if first is None:
        return []
    t = day_of(now_ms)
    if backfill:
        return enumerate_days(conn, now_ms)
    if today:
        day = t
    elif last_day:
        day = t - timedelta(days=1)
    else:
        day = date.fromisoformat(args_date)
        if day > t:
            raise ValueError(f"--date {day} is in the future")
    return [day] if day_bounds(day)[1] > first else []


def run(conn, settings, out_dir: Path, days: list[date], now_ms: int, write: bool = True) -> list[dict]:
    """Compute (and write) the given days, then refresh index.json / latest.json. Returns the day reports."""
    names = _symbol_names(conn)
    kill = _kill_numbers(conn, settings, now_ms)
    horizon = now_ms - 2 * DAY_MS          # the kill numbers are "as of now": only attach them to recent days
    out = []
    for d in days:
        rep = compute_day(conn, settings, d, now_ms, kill if day_bounds(d)[1] > horizon else None, names)
        if write:
            write_day(out_dir, rep)
        out.append(rep)
    if write and days:
        write_index(out_dir, settings, now_ms, kill)
    return out


# ---- text rendering ----

def _n(x, fmt="{:+.2f}") -> str:
    return "n/a" if x is None else fmt.format(x)


def _table(title: str, d: dict, L: list) -> None:
    if not d:
        return
    L.append("")
    L.append(title)
    L.append(f"  {'':<14}{'trades':>7}{'win%':>7}{'net':>10}{'avg bp':>9}{'volume':>9}")
    for k, v in d.items():
        L.append(f"  {k:<14}{v['trades']:>7}{_n(None if v['win_rate'] is None else v['win_rate'] * 100, '{:.0f}'):>7}"
                 f"{_n(v['net']):>10}{_n(v['avg_bp']):>9}{v['volume']:>9}")


def render(r: dict) -> str:
    """Compact plain-text view of one day report."""
    L = []
    add = L.append
    m, s = r["meta"], r["summary"]
    add(f"{m['instance']} ({m['timeframe']}, {m['strategy']})  {m['date']} UTC" + ("  PARTIAL" if m["partial"] else ""))
    add("=" * 78)
    add(f"runs {m['runs']['count']} ({'/'.join(m['runs']['modes']) or '-'}), restarts {m['runs']['restarts']}; "
        f"dry_run trades excluded: {s['excluded_dry_run_trades']}")
    add(f"trades {s['trades']}  wins {s['wins']}  losses {s['losses']}  flat {s['flat']}  win rate "
        f"{_n(None if s['win_rate'] is None else s['win_rate'] * 100, '{:.0f}')}%")
    add(f"net {_n(s['net'])} (gross {_n(s['gross'])}, commission {_n(s['commission'])}, swap {_n(s['swap'])})  "
        f"profit factor {_n(s['profit_factor'], '{:.2f}')}  max drawdown {_n(s['max_drawdown'], '{:.2f}')}")
    add(f"bp/trade price {_n(s['avg_bp_price'])} (median {_n(s['median_bp_price'])}), mid {_n(s['avg_bp_mid'])} "
        f"(median {_n(s['median_bp_mid'])}), t {_n(s['t_stat_bp'])}")
    add(f"avg win {_n(s['avg_win'])}  avg loss {_n(s['avg_loss'])}  hold avg {_n(s['avg_hold_s'], '{:.0f}')} s / "
        f"max {_n(s['max_hold_s'], '{:.0f}')} s  volume {s['volume']}  stop share "
        f"{_n(None if s['stop_share'] is None else s['stop_share'] * 100, '{:.0f}')}%")
    for k in ("best_trade", "worst_trade"):
        t = s[k]
        if t:
            add(f"{k.replace('_', ' ')}: {t['symbol']} {t['side']} {_n(t['net'])} ({_n(t['bp'])} bp) at {t['exit']}")
    b = r["breakdown"]
    for title, key in (("by symbol", "by_symbol"), ("by side", "by_side"), ("by exit reason", "by_exit_reason"),
                       ("by exit hour (UTC)", "by_hour_utc"), ("by entry |z_poc|", "by_abs_z"), ("by size", "by_size")):
        _table(title, b[key], L)
    e = r["execution_quality"]
    add("")
    add("execution quality")
    add(f"  slippage vs simulated taker (bp): n {e['slippage_bp']['n']} mean {_n(e['slippage_bp']['mean'])} "
        f"median {_n(e['slippage_bp']['median'])}")
    add(f"  adverse {_n(e['avg_adverse_bp'])} bp, markout {_n(e['avg_markout_bp'])} bp, entry spread "
        f"{_n(e['avg_spread_entry_bp'], '{:.2f}')} bp")
    add(f"  orders {e['orders']}, rejects/errors {e['rejects_or_errors']} {e['error_codes'] or ''}")
    f = r["funnel"]
    add("")
    add(f"funnel: {f['signal_rows']} signal rows, {f['above_entry_z']} above |z| {f['entry_z']:g}, entered "
        f"{f['entered']}, rollover {f['rollover']}, gated {f['gated']} {f['gated_reasons'] or ''}, none {f['none']}")
    c = r["cumulative"]
    add(f"cumulative since {c['since'] or 'n/a'}: {c['trades']} trades, win rate "
        f"{_n(None if c['win_rate'] is None else c['win_rate'] * 100, '{:.0f}')}%, net {_n(c['net'])}, "
        f"{_n(c['avg_bp'])} bp/trade vs research {_n(c['research_taker_net_bp'])}")
    k = r["kill_switch"]
    if k.get("available"):
        add(f"kill switch (as of {k['as_of']}): cumulative {_n(k['cumulative'])} vs -{k['max_loss']:g} "
            f"(distance {_n(k['distance_to_max_loss'])}); today {_n(k['today'])} vs daily -{k['daily_max_loss']:g}")
    else:
        add(f"kill switch: n/a ({k.get('reason')})")
    if r["risk_events"]:
        add("risk events: " + ", ".join(f"{kk}={v['count']}" for kk, v in r["risk_events"].items()))
    if r["trades"]:
        add("")
        add(f"trades ({len(r['trades'])} of {r['trades_total']})")
        add(f"  {'exit (UTC)':<22}{'symbol':<8}{'side':<5}{'vol':>6}{'bp':>8}{'net':>8}  {'reason':<9}{'z':>5}{'hold':>6}")
        for t in r["trades"]:
            add(f"  {t['exit'] or '?':<22}{t['symbol']:<8}{t['side'] or '?':<5}{t['volume']:>6}{_n(t['bp']):>8}"
                f"{_n(t['net']):>8}  {t['reason']:<9}{_n(t['z_poc'], '{:.2f}'):>5}{_n(t['hold_s'], '{:.0f}'):>6}")
    return "\n".join(L)
