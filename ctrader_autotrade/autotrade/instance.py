"""Per-instance state directory ownership (DESIGN.md section 3, "Instances").

Each instance (config [instance] name, e.g. "ax5m" / "ax1m") has its own state dir: journal
autotrade.db, KILL / PAUSE / PARITY_OK, kill_baseline.json / kill_status.json, token.json. The state
dir carries a marker, state/INSTANCE.json ({"name", "timeframe"}): a run with another instance name or
timeframe is refused, so a config pointing the 1-minute bot at the 5-minute bot's state (or the other
way round) cannot mix journals, risk counters or control files. `autotrade run` also holds an exclusive
lock (state/instance.lock) for its lifetime: one process per instance.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

from .config import ConfigError

MARKER = "INSTANCE.json"
LOCK = "instance.lock"
_held: dict = {}       # state dir -> open lock file (kept for the process lifetime)


def _marker(settings) -> dict:
    return {"name": settings.instance_name, "timeframe": settings.instance_timeframe}


def check(settings) -> None:
    """Refuse a state dir that belongs to another instance (missing marker: not claimed yet)."""
    p = Path(settings.state_path()) / MARKER
    if not p.is_file():
        return
    try:
        have = json.loads(p.read_text())
    except (OSError, ValueError) as exc:
        raise ConfigError(f"unreadable {p}: {exc}") from None
    want = _marker(settings)
    if {k: have.get(k) for k in want} != want:
        raise ConfigError(f"state dir {settings.state_path()} belongs to instance {have.get('name')!r} "
                          f"({have.get('timeframe')}), not {want['name']!r} ({want['timeframe']}): "
                          f"check state_dir / [instance] in the config")


def claim(settings) -> None:
    """check(), write the marker if missing, and take the per-instance process lock."""
    sd = Path(settings.state_path())
    sd.mkdir(parents=True, exist_ok=True)
    Path(settings.log_path()).mkdir(parents=True, exist_ok=True)
    check(settings)
    p = sd / MARKER
    if not p.is_file():
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(_marker(settings), sort_keys=True) + "\n")
        os.replace(tmp, p)
    key = str(sd.resolve())
    if key in _held:
        return
    fh = open(sd / LOCK, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise ConfigError(f"instance {settings.instance_name!r} is already running (lock {sd / LOCK})") from None
    fh.seek(0)
    fh.truncate()
    fh.write(f"{os.getpid()}\n")
    fh.flush()
    _held[key] = fh


def release(settings) -> None:
    fh = _held.pop(str(Path(settings.state_path()).resolve()), None)
    if fh is not None:
        fh.close()
