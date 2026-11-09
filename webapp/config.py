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
    vap_grid_delay: float           # seconds to wait into the slot before reading
    sql_server: str      # Fabric SQL database host; blank = export disabled
    sql_database: str    # the database (catalog) to write into
    sql_schema: str      # schema for the mirrored table
    sql_table: str       # the mirrored table name


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

    # How far into the 5-minute slot the recorder waits before it reads. The job
    # fires on the mark, so the newest tick in the feed is still the previous
    # 10s bucket; waiting lets the slot's own ticks land first, so the recorded
    # `as_of` reflects the stop that just closed rather than the one before it.
    # `_opt_float` maps 0/unset to None, so 0 (and an unset value) means "read
    # immediately" and only a positive value sleeps.
    grid_delay = _opt_float("VAP_GRID_DELAY", 1.0)

    # The Fabric SQL database mirror of `vap_run`. All of it is optional: with
    # SQL_SERVER blank the daily export is simply off, so the dashboard and the
    # 5-minute recorder keep running on a box with no SQL target configured.
    sql_server = os.environ.get("SQL_SERVER", "").strip()
    sql_database = os.environ.get("SQL_DATABASE", "").strip()
    sql_schema = os.environ.get("SQL_SCHEMA", "").strip() or "dbo"
    sql_table = os.environ.get("SQL_TABLE", "").strip() or "vap_run"
    if sql_server:
        if not sql_database:
            raise RuntimeError("SQL_DATABASE is required when SQL_SERVER is set")
        # Schema and table are interpolated into DDL/DML, so they are held to
        # the same plain-identifier rule as KUSTO_TABLE rather than quoted.
        for label, value in (("SQL_SCHEMA", sql_schema), ("SQL_TABLE", sql_table)):
            if not NAME_PATTERN.match(value):
                raise RuntimeError(f"{label} must be a plain identifier, e.g. {value!r}")

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
        vap_grid_delay=grid_delay or 0.0,
        sql_server=sql_server,
        sql_database=sql_database,
        sql_schema=sql_schema,
        sql_table=sql_table,
    )
