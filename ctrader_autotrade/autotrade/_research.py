"""Import shim for the research / webapp code the bot reuses (parity by construction).

webapp (/root/axioma-analytics/webapp) is a flat set of top-level modules (`analytics`,
`config`, `db`, `queries`, `export_flat`). It is put on sys.path only while importing, then the
collision-prone names are removed from sys.modules and sys.path is restored, so the bot's own
imports (and anything else named `config`/`db`) are unaffected. The imported module objects stay
referenced here. Side effects checked: importing `export_flat` only defines functions/constants;
`config.load_config()` is called only inside `export_flat.main()` (export_flat.py:331), never at
import time.

research/ has no __init__.py (namespace package); it is imported as `research.pipeline.*` with the
repo root on sys.path. /root/axioma-analytics/research itself is never put on sys.path (its
config.py would collide with webapp/config).
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WEBAPP = REPO / "webapp"

_WEBAPP_MODULES = ("analytics", "config", "db", "queries", "export_flat")


def _import_webapp():
    saved_mods = {n: sys.modules.pop(n) for n in _WEBAPP_MODULES if n in sys.modules}
    saved_path = list(sys.path)
    sys.path.insert(0, str(WEBAPP))
    try:
        analytics = importlib.import_module("analytics")
        export_flat = importlib.import_module("export_flat")
    finally:
        sys.path[:] = saved_path
        for n in _WEBAPP_MODULES:
            sys.modules.pop(n, None)
        sys.modules.update(saved_mods)
    return analytics, export_flat


def _import_research():
    added = str(REPO) not in sys.path
    if added:
        sys.path.append(str(REPO))  # appended: never shadows the bot's own modules
    try:
        clean = importlib.import_module("research.pipeline.clean")
        features = importlib.import_module("research.pipeline.features")
        hypotheses = importlib.import_module("research.pipeline.hypotheses")
        screen = importlib.import_module("research.pipeline.screen")
        timeframe = importlib.import_module("research.pipeline.timeframe")
    finally:
        if added:
            sys.path.remove(str(REPO))
    return clean, features, hypotheses, screen, timeframe


analytics, export_flat = _import_webapp()
clean, features, hypotheses, screen, timeframe = _import_research()
