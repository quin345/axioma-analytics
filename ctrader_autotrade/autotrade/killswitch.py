"""P&L kill switch: pure helpers (DESIGN.md section 5). The runner owns the timer and the actions.

Cumulative P&L = realised (journal trades of this account and run mode, net of commission and swap,
account currency) closed since the baseline + unrealised of the bot's own open positions.
Daily P&L = the same, but realised since the last daily reset (resetday) only; the unrealised part
is the whole current unrealised, even for a position opened before the reset.

Baseline: `state/kill_baseline.json` {"since_ms", "account_id", "created"} (mode 0600), created the
first time the bot runs with kill.enabled on an account (a different account id starts a new one);
`autotrade kill-reset` rewrites it. `kill.since` in config.toml overrides the file.

`state/kill_status.json` is the bot's latest numbers, read by `autotrade report`.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

BASELINE_FILE = "kill_baseline.json"
STATUS_FILE = "kill_status.json"
KILL_KINDS = ("kill_max_loss", "kill_profit_target", "daily_max_loss")


def parse_since(text: str) -> int:
    """ISO instant ('2026-11-20T05:00:00Z', or an offset; naive = UTC) -> epoch ms."""
    from .config import ConfigError
    try:
        dt = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        raise ConfigError(f"kill.since is not an ISO timestamp, e.g. 2026-11-20T05:00:00Z: {text!r}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_private(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, sort_keys=True)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def read_baseline(state_dir) -> Optional[dict]:
    try:
        d = json.loads((Path(state_dir) / BASELINE_FILE).read_text())
        return {"since_ms": int(d["since_ms"]), "account_id": int(d["account_id"]),
                "created": str(d.get("created", ""))}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_baseline(state_dir, account_id: int, since_ms: int) -> dict:
    d = {"since_ms": int(since_ms), "account_id": int(account_id), "created": iso(since_ms)}
    _write_private(Path(state_dir) / BASELINE_FILE, d)
    return d


def current_baseline(state_dir, account_id: int, now_ms: int, since: Optional[str] = None) -> dict:
    """The baseline in force: the config `since`, else the file for this account, else a new file at
    now_ms (first run, or the account id changed). Returns {"since_ms", "account_id", "created", "source"}."""
    if since:
        ms = parse_since(since)
        return {"since_ms": ms, "account_id": int(account_id), "created": iso(ms), "source": "config"}
    b = read_baseline(state_dir)
    if b is not None and b["account_id"] == int(account_id):
        return {**b, "source": "file"}
    return {**write_baseline(state_dir, account_id, now_ms), "source": "new"}


def write_status(state_dir, status: dict) -> None:
    _write_private(Path(state_dir) / STATUS_FILE, status)


def read_status(state_dir) -> Optional[dict]:
    try:
        return json.loads((Path(state_dir) / STATUS_FILE).read_text())
    except (OSError, ValueError):
        return None


# ---- unrealised pnl ----

def unrealised_fallback(view, quote) -> Optional[float]:
    """One own position marked at the touch it would exit at (bid for a long, ask for a short), in
    account currency, assuming the quote currency is the deposit currency (SimBroker does the same).
    Volume is cents of base units: pnl = side * (exit - entry) * volume / 100. None without a quote."""
    if quote is None:
        return None
    exit_px = (quote.bid if view.side.value > 0 else quote.ask) / 100_000
    return view.side.value * (exit_px - view.entry_price) * view.volume / 100


def unrealised_from_server(res: Any, own_position_ids: set) -> dict[int, float]:
    """{position_id: net unrealised pnl in account currency} for our own positions, from a
    ProtoOAGetPositionUnrealizedPnLRes (money scaled by the response's moneyDigits)."""
    scale = 10 ** int(res.moneyDigits)
    return {p.positionId: p.netUnrealizedPnL / scale for p in res.positionUnrealizedPnL
            if p.positionId in own_position_ids}


def evaluate(s, cumulative: float, daily: float) -> list[str]:
    """Every breached limit, most severe first (kill_max_loss, kill_profit_target, daily_max_loss)."""
    out = []
    if cumulative <= -s.kill_max_loss:
        out.append("kill_max_loss")
    if s.kill_profit_target > 0 and cumulative >= s.kill_profit_target:
        out.append("kill_profit_target")
    if s.kill_daily_max_loss > 0 and daily <= -s.kill_daily_max_loss:
        out.append("daily_max_loss")
    return out
