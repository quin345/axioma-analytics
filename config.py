import os
import re
from dataclasses import dataclass

NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class Config:
    cluster_uri: str
    database: str
    table: str
    price_scale: float


def _require(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_config():
    table = _require("KUSTO_TABLE")
    # The table name goes into KQL text, so only allow plain identifiers.
    if not NAME_PATTERN.match(table):
        raise RuntimeError("KUSTO_TABLE must be a plain table name, e.g. order_book_aggregate")

    return Config(
        cluster_uri=_require("KUSTO_CLUSTER_URI"),
        database=_require("KUSTO_DATABASE"),
        table=table,
        price_scale=float(_require("PRICE_SCALE")),
    )