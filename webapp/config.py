import os
import re
from dataclasses import dataclass

NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Where the local record-keeping database lives, relative to this file's parent.
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "axioma.db")
# Values that turn a flag on. Anything else (or unset) is off.
TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    cluster_uri: str
    database: str
    table: str
    price_scale: float
    frontend_dist: str   # folder with the built React app (pnpm build output)
    db_path: str         # local SQLite file holding the derived VAP records
    maintenance: bool    # serve the holding page at / instead of the dashboard
    vap_keep_runs: int | None       # cap stops per (symbol, window); None = keep all
    vap_level_hours: float | None   # hours of vap_level bins to keep; None = all


def _require(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _flag(name):
    """A boolean env var. Set MAINTENANCE_MODE=1 to switch the site into 503."""
    return os.environ.get(name, "").strip().lower() in TRUE_VALUES


def _opt_int(name, default=None):
    """An optional whole-number env var. Blank or unset falls back to `default`.

    A non-positive value means "off" (None), so `VAP_KEEP_RUNS=0` and an unset
    `VAP_KEEP_RUNS` both mean "do not cap", which is what we want for the
    durable `vap_run` record.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    return value if value > 0 else None


def _opt_float(name, default=None):
    """Like `_opt_int` for a fractional env var (hours)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = float(raw)
    return value if value > 0 else None


def load_config():
    table = _require("KUSTO_TABLE")
    if not NAME_PATTERN.match(table):
        raise RuntimeError("KUSTO_TABLE must be a plain table name, e.g. order_book_aggregate")

    db_path = os.environ.get("DB_PATH", "").strip() or DEFAULT_DB_PATH

    # Retention. `vap_run` is the durable analytical record and is kept by
    # default (VAP_KEEP_RUNS unset -> no cap). `vap_level` is the chart detail
    # and is trimmed to the last VAP_LEVEL_HOURS hours by default (48).
    keep_runs = _opt_int("VAP_KEEP_RUNS") or _opt_int("VAP_KEEP")

    return Config(
        cluster_uri=_require("KUSTO_CLUSTER_URI"),
        database=_require("KUSTO_DATABASE"),
        table=table,
        price_scale=float(_require("PRICE_SCALE")),
        frontend_dist=_require("FRONTEND_DIST"),
        db_path=db_path,
        maintenance=_flag("MAINTENANCE_MODE"),
        vap_keep_runs=keep_runs,
        vap_level_hours=_opt_float("VAP_LEVEL_HOURS", 48.0),
    )
