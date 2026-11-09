import os
import re
from dataclasses import dataclass

NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Where the local record-keeping database lives, relative to this file's parent.
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "axioma.db")


@dataclass(frozen=True)
class Config:
    cluster_uri: str
    database: str
    table: str
    price_scale: float
    frontend_dist: str   # folder with the built React app (pnpm build output)
    db_path: str         # local SQLite file holding the derived VAP records


def _require(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_config():
    table = _require("KUSTO_TABLE")
    if not NAME_PATTERN.match(table):
        raise RuntimeError("KUSTO_TABLE must be a plain table name, e.g. order_book_aggregate")

    db_path = os.environ.get("DB_PATH", "").strip() or DEFAULT_DB_PATH

    return Config(
        cluster_uri=_require("KUSTO_CLUSTER_URI"),
        database=_require("KUSTO_DATABASE"),
        table=table,
        price_scale=float(_require("PRICE_SCALE")),
        frontend_dist=_require("FRONTEND_DIST"),
        db_path=db_path,
    )
