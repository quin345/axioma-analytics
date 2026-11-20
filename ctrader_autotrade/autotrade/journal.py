"""Journal: SQLite (WAL) plus a size-rotated JSON-lines log (DESIGN.md sections 3 and 5).

Everything the forward test needs is written here: runs, 10 s buckets (the warm-up store),
bars, signals, orders, fills, trades and risk events. Nothing secret is ever written: Settings
are serialised without `secrets`, and the JSON-lines writer scrubs known credential keys.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import logging.handlers
import re
import sqlite3
import subprocess
import time
from datetime import timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .types import Bar, Bucket

DAY_MS = 86_400_000
_SECRET_KEY = re.compile(r"^(client_?id|client_?secret|access_?token|refresh_?token|token|secret|password)$",
                         re.IGNORECASE)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT, git_sha TEXT, config_json TEXT, account_id INTEGER, is_live INTEGER,
    broker TEXT, started_ms INTEGER, ended_ms INTEGER);
CREATE TABLE IF NOT EXISTS buckets (
    symbol_id INTEGER NOT NULL, start_ms INTEGER NOT NULL,
    best_bid INTEGER, best_ask INTEGER, events INTEGER, source TEXT,
    PRIMARY KEY (symbol_id, start_ms));
CREATE TABLE IF NOT EXISTS bars (
    symbol_id INTEGER NOT NULL, slot_end_ms INTEGER NOT NULL, has_data INTEGER, events INTEGER,
    mid_open REAL, mid_high REAL, mid_low REAL, mid_close REAL, spread_mean REAL, spread_median REAL,
    rel_spread_mean REAL, realised_vol_pct REAL, vap_poc REAL, vap_va_low REAL, vap_va_high REAL,
    vap_volume_events REAL, approx_share REAL,
    PRIMARY KEY (symbol_id, slot_end_ms));
CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY, run_id INTEGER, ts_ms INTEGER, symbol_id INTEGER, slot_end_ms INTEGER,
    z_poc REAL, z_range REAL, events_z REAL, spread REAL, approx_share REAL, decision TEXT, reason TEXT,
    taker_entry REAL, taker_exit REAL, taker_pnl_bps REAL,
    maker_outcome TEXT, maker_fill_price REAL, cf_return_bps REAL);
CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY, run_id INTEGER, ts_ms INTEGER, symbol_id INTEGER, side TEXT,
    volume INTEGER, kind TEXT, order_type TEXT, limit_price INTEGER, expiry_ms INTEGER, stop_rel INTEGER,
    signal_id TEXT, label TEXT, order_id INTEGER, position_id INTEGER, status TEXT, error_code TEXT);
CREATE INDEX IF NOT EXISTS orders_ts ON orders (ts_ms);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, ts_ms INTEGER, symbol_id INTEGER,
    client_order_id TEXT, order_id INTEGER, position_id INTEGER, deal TEXT, side TEXT, price REAL,
    volume INTEGER, commission INTEGER, gross INTEGER, swap INTEGER, money_digits INTEGER);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, symbol_id INTEGER, side TEXT, volume INTEGER,
    signal_id TEXT, client_order_id TEXT, position_id INTEGER, entry_ms INTEGER, exit_ms INTEGER,
    entry_price REAL, exit_price REAL, reason TEXT, pnl_bps_mid REAL, pnl_bps_price REAL,
    pnl_net REAL, gross REAL, commission REAL, swap REAL, money_digits INTEGER,
    adverse_bps REAL, markout_bps REAL);
CREATE INDEX IF NOT EXISTS trades_exit ON trades (exit_ms);
CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, ts_ms INTEGER, symbol_id INTEGER,
    kind TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, ts_ms INTEGER, symbol_id INTEGER,
    from_state TEXT, to_state TEXT, client_order_id TEXT, detail TEXT);
"""

SIGNAL_COLS = ("ts_ms", "symbol_id", "slot_end_ms", "z_poc", "z_range", "events_z", "spread", "approx_share",
               "decision", "reason", "taker_entry", "taker_exit", "taker_pnl_bps", "maker_outcome",
               "maker_fill_price", "cf_return_bps")
ORDER_COLS = ("ts_ms", "symbol_id", "side", "volume", "kind", "order_type", "limit_price", "expiry_ms",
              "stop_rel", "signal_id", "label", "order_id", "position_id", "status", "error_code")
TRADE_COLS = ("symbol_id", "side", "volume", "signal_id", "client_order_id", "position_id", "entry_ms",
              "exit_ms", "entry_price", "exit_price", "reason", "pnl_bps_mid", "pnl_bps_price", "pnl_net",
              "gross", "commission", "swap", "money_digits", "adverse_bps", "markout_bps")
FILL_COLS = ("ts_ms", "symbol_id", "client_order_id", "order_id", "position_id", "deal", "side", "price",
             "volume", "commission", "gross", "swap", "money_digits")


def utc_day_bounds(now_ms: int) -> tuple[int, int]:
    """The plain UTC day: the default until a daily reset is configured (resetday.day_start_ms)."""
    d0 = now_ms - now_ms % DAY_MS
    return d0, d0 + DAY_MS


def pnl_net_window(conn, since_ms: int, until_ms: Optional[int] = None, account_id: Optional[int] = None,
                   mode=None) -> float:
    """Realised net pnl (commission and swap included, account currency) of trades closed in
    [since_ms, until_ms), only those of runs on this account and in this run mode (kill switch, report)."""
    sql = ("SELECT COALESCE(SUM(COALESCE(t.pnl_net,0)),0) FROM trades t JOIN runs r ON r.run_id=t.run_id "
           "WHERE t.exit_ms>=? AND t.exit_ms<?")
    args: list = [since_ms, until_ms if until_ms is not None else 2 ** 62]
    if account_id is not None:
        sql += " AND r.account_id=?"
        args.append(account_id)
    if mode is not None:
        sql += " AND r.mode=?"
        args.append(_plain(mode))
    return float(conn.execute(sql, args).fetchone()[0])


def settings_to_json(settings: Any) -> str:
    """Settings as JSON without secrets (the `secrets` field is dropped)."""
    if dataclasses.is_dataclass(settings):
        d = {f.name: getattr(settings, f.name) for f in dataclasses.fields(settings) if f.name != "secrets"}
    else:
        d = {k: v for k, v in dict(settings).items() if k != "secrets"}
    return json.dumps(_scrub(d), sort_keys=True, default=str)


def _scrub(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: ("***" if _SECRET_KEY.match(str(k)) else _scrub(v)) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_scrub(v) for v in o]
    return o


def _plain(v: Any) -> Any:
    """Enums to their name for storage."""
    return v.name if hasattr(v, "name") and hasattr(v, "value") else v


def git_sha() -> Optional[str]:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             timeout=5, cwd=Path(__file__).resolve().parent)
        return out.stdout.strip() or None
    except Exception:
        return None


class Journal:
    """SQLite store plus JSON-lines log. Single-threaded use (the Twisted reactor thread)."""

    def __init__(self, db_path, jsonl_path, max_bytes: int = 5_000_000, backups: int = 5,
                 time_fn=time.time):
        self._time_fn = time_fn
        self.day_bounds = utc_day_bounds   # (now_ms) -> (day start, next reset); the Bot installs the configured one
        self.run_id: Optional[int] = None
        db_path = str(db_path)
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        Path(jsonl_path).parent.mkdir(parents=True, exist_ok=True)
        self._log = logging.getLogger(f"autotrade.journal.{id(self)}")
        self._log.setLevel(logging.INFO)
        self._log.propagate = False
        self._handler = logging.handlers.RotatingFileHandler(str(jsonl_path), maxBytes=max_bytes,
                                                             backupCount=backups, encoding="utf-8")
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        self._log.addHandler(self._handler)

    @classmethod
    def from_settings(cls, settings, **kw) -> "Journal":
        j = cls(settings.state_path() / "autotrade.db", settings.log_path() / "autotrade.jsonl", **kw)
        j.use_daily_reset(settings)
        return j

    def use_daily_reset(self, settings) -> None:
        """Make every "today" query run from the configured daily reset (resetday) to the next."""
        from .resetday import day_start_ms, next_reset_ms
        self.day_bounds = lambda now_ms: (day_start_ms(now_ms, settings), next_reset_ms(now_ms, settings))

    def close(self) -> None:
        self._log.removeHandler(self._handler)
        self._handler.close()
        self.db.close()

    # --- helpers ---
    def _now(self) -> int:
        return int(self._time_fn() * 1000)

    def log(self, event: str, **fields) -> None:
        rec = {"ts_ms": fields.pop("ts_ms", None) or self._now(), "run_id": self.run_id, "event": event}
        rec.update({k: _plain(v) for k, v in fields.items()})
        self._log.info(json.dumps(_scrub(rec), default=str, sort_keys=True))

    def _insert(self, table: str, cols: tuple, data: dict, replace: bool = False, extra: Optional[dict] = None):
        unknown = set(data) - set(cols)
        if unknown:
            raise ValueError(f"unknown {table} columns: {sorted(unknown)}")
        row = {**(extra or {}), **{k: _plain(v) for k, v in data.items()}}
        names = list(row)
        sql = (f"INSERT OR {'REPLACE' if replace else 'ABORT'} INTO {table} ({','.join(names)}) "
               f"VALUES ({','.join('?' * len(names))})")
        cur = self.db.execute(sql, [row[n] for n in names])
        self.db.commit()
        return cur

    def _update(self, table: str, cols: tuple, key: str, key_val, data: dict) -> None:
        unknown = set(data) - set(cols)
        if unknown:
            raise ValueError(f"unknown {table} columns: {sorted(unknown)}")
        if not data:
            return
        sets = ",".join(f"{k}=?" for k in data)
        self.db.execute(f"UPDATE {table} SET {sets} WHERE {key}=?", [*map(_plain, data.values()), key_val])
        self.db.commit()

    # --- runs ---
    def start_run(self, mode, settings=None, account_id: Optional[int] = None, is_live: Optional[bool] = None,
                  broker: str = "", config_json: Optional[str] = None, sha: Optional[str] = None) -> int:
        cfg = config_json if config_json is not None else (settings_to_json(settings) if settings else "{}")
        cur = self.db.execute(
            "INSERT INTO runs (mode, git_sha, config_json, account_id, is_live, broker, started_ms) "
            "VALUES (?,?,?,?,?,?,?)",
            (_plain(mode), sha or git_sha(), cfg, account_id,
             None if is_live is None else int(is_live), broker, self._now()))
        self.db.commit()
        self.run_id = cur.lastrowid
        self.log("run_start", mode=mode, broker=broker, is_live=is_live)
        return self.run_id

    def end_run(self) -> None:
        if self.run_id is not None:
            self.db.execute("UPDATE runs SET ended_ms=? WHERE run_id=?", (self._now(), self.run_id))
            self.db.commit()
            self.log("run_end")

    # --- buckets (warm-up store) ---
    def save_buckets(self, buckets: Iterable[Bucket]) -> None:
        rows = [(b.symbol_id, b.start_ms, b.best_bid, b.best_ask, b.events, b.source) for b in buckets]
        if rows:
            self.db.executemany("INSERT OR REPLACE INTO buckets VALUES (?,?,?,?,?,?)", rows)
            self.db.commit()

    def load_buckets(self, symbol_id: int, since_ms: int) -> list[Bucket]:
        cur = self.db.execute(
            "SELECT symbol_id,start_ms,best_bid,best_ask,events,source FROM buckets "
            "WHERE symbol_id=? AND start_ms>=? ORDER BY start_ms", (symbol_id, since_ms))
        return [Bucket(*r) for r in cur.fetchall()]

    def prune_buckets(self, before_ms: int) -> None:
        self.db.execute("DELETE FROM buckets WHERE start_ms<?", (before_ms,))
        self.db.commit()

    def bar_slots(self, symbol_id: int, since_ms: int) -> set[int]:
        """slot_end_ms of the stored bars of one symbol since since_ms."""
        cur = self.db.execute("SELECT slot_end_ms FROM bars WHERE symbol_id=? AND slot_end_ms>=?",
                              (symbol_id, since_ms))
        return {r[0] for r in cur.fetchall()}

    # --- bars / signals ---
    def record_bar(self, bar: Bar) -> None:
        end = bar.slot_end if bar.slot_end.tzinfo else bar.slot_end.replace(tzinfo=timezone.utc)
        self.db.execute(
            "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (bar.symbol_id, int(end.timestamp() * 1000), int(bar.has_data), bar.events, bar.mid_open,
             bar.mid_high, bar.mid_low, bar.mid_close, bar.spread_mean, bar.spread_median,
             bar.rel_spread_mean, bar.realised_vol_pct, bar.vap_240m_poc, bar.vap_240m_va_low,
             bar.vap_240m_va_high, bar.vap_240m_volume_events, bar.approx_share))
        self.db.commit()

    def record_signal(self, signal_id: str, **fields) -> None:
        """One row per bar and symbol: z values, decision and shadow results (update_signal fills the rest)."""
        self._insert("signals", SIGNAL_COLS, fields, replace=True,
                     extra={"signal_id": signal_id, "run_id": self.run_id})
        self.log("signal", signal_id=signal_id, **fields)

    def update_signal(self, signal_id: str, **fields) -> None:
        self._update("signals", SIGNAL_COLS, "signal_id", signal_id, fields)
        self.log("signal_update", signal_id=signal_id, **fields)

    # --- orders / fills / trades ---
    def record_order(self, client_order_id: str, **fields) -> None:
        self._insert("orders", ORDER_COLS, fields, extra={"client_order_id": client_order_id,
                                                         "run_id": self.run_id})
        self.log("order", client_order_id=client_order_id, **fields)

    def update_order(self, client_order_id: str, **fields) -> None:
        self._update("orders", ORDER_COLS, "client_order_id", client_order_id, fields)
        self.log("order_update", client_order_id=client_order_id, **fields)

    def has_client_order_id(self, client_order_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM orders WHERE client_order_id=?",
                               (client_order_id,)).fetchone() is not None

    def record_fill(self, **fields) -> None:
        self._insert("fills", FILL_COLS, fields, extra={"run_id": self.run_id})
        self.log("fill", **fields)

    def record_trade(self, **fields) -> None:
        self._insert("trades", TRADE_COLS, fields, extra={"run_id": self.run_id})
        self.log("trade", **fields)

    def record_risk_event(self, kind: str, detail: Any = "", symbol_id: Optional[int] = None,
                          ts_ms: Optional[int] = None) -> None:
        ts = ts_ms or self._now()
        text = detail if isinstance(detail, str) else json.dumps(_scrub(detail), default=str, sort_keys=True)
        self.db.execute("INSERT INTO risk_events (run_id, ts_ms, symbol_id, kind, detail) VALUES (?,?,?,?,?)",
                        (self.run_id, ts, symbol_id, kind, text))
        self.db.commit()
        self.log("risk_event", ts_ms=ts, kind=kind, symbol_id=symbol_id, detail=text)

    def record_transition(self, symbol_id: int, from_state: str, to_state: str,
                          client_order_id: Optional[str] = None, detail: str = "",
                          ts_ms: Optional[int] = None) -> None:
        ts = ts_ms or self._now()
        self.db.execute("INSERT INTO transitions (run_id, ts_ms, symbol_id, from_state, to_state, "
                        "client_order_id, detail) VALUES (?,?,?,?,?,?,?)",
                        (self.run_id, ts, symbol_id, from_state, to_state, client_order_id, detail))
        self.db.commit()
        self.log("transition", ts_ms=ts, symbol_id=symbol_id, from_state=from_state, to_state=to_state,
                 client_order_id=client_order_id, detail=detail)

    # --- queries for risk (the trading day: see resetday) ---
    def trades_today(self, now_ms: int) -> int:
        d0, d1 = self.day_bounds(now_ms)
        return self.db.execute("SELECT COUNT(*) FROM trades WHERE exit_ms>=? AND exit_ms<?",
                               (d0, d1)).fetchone()[0]

    def pnl_today_bps(self, now_ms: int) -> float:
        """Sum of per-trade price-based bp (mid-based when the price is unknown)."""
        d0, d1 = self.day_bounds(now_ms)
        return float(self.db.execute(
            "SELECT COALESCE(SUM(COALESCE(pnl_bps_price, pnl_bps_mid)),0) FROM trades "
            "WHERE exit_ms>=? AND exit_ms<?", (d0, d1)).fetchone()[0])

    def consec_losses(self) -> int:
        n = 0
        for (p,) in self.db.execute(
                "SELECT COALESCE(pnl_bps_price, pnl_bps_mid) FROM trades ORDER BY exit_ms DESC, id DESC"):
            if p is not None and p < 0:
                n += 1
            else:
                break
        return n

    def orders_last_min(self, now_ms: int) -> int:
        return self.db.execute("SELECT COUNT(*) FROM orders WHERE ts_ms>? AND ts_ms<=?",
                               (now_ms - 60_000, now_ms)).fetchone()[0]

    def rejects_since(self, since_ms: int) -> int:
        """Order rejects / order errors recorded since since_ms (the reject breaker window)."""
        return self.db.execute("SELECT COUNT(*) FROM risk_events WHERE kind='order_reject' AND ts_ms>=?",
                               (since_ms,)).fetchone()[0]

    def pnl_net_today(self, now_ms: int) -> float:
        """Realised account-currency pnl of trades closed in the current trading day."""
        d0, d1 = self.day_bounds(now_ms)
        return float(self.db.execute(
            "SELECT COALESCE(SUM(COALESCE(pnl_net,0)),0) FROM trades WHERE exit_ms>=? AND exit_ms<?",
            (d0, d1)).fetchone()[0])

    def pnl_net_total(self) -> float:
        return float(self.db.execute("SELECT COALESCE(SUM(COALESCE(pnl_net,0)),0) FROM trades").fetchone()[0])

    def pnl_net_window(self, since_ms: int, until_ms: Optional[int] = None, account_id: Optional[int] = None,
                       mode=None) -> float:
        """See pnl_net_window() below."""
        return pnl_net_window(self.db, since_ms, until_ms, account_id, mode)
