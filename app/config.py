"""Application configuration for the production gold endpoint.

The app reads exactly one source: the production Fabric SQL analytics endpoint,
`[ctrader_lakehouse].[gold]`. There is no environment selector, no catalog
discovery and no synthetic fallback - the configuration is just the endpoint,
the Entra ID service principal and the gold object names.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _env(*names: str, default: str | None = None) -> str | None:
    """First non-blank environment variable from `names`, unquoted."""
    for n in names:
        v = os.getenv(n)
        if v and v.strip():
            return v.strip().strip('"').strip("'")
    return default


@dataclass(frozen=True)
class Settings:
    # --- Production SQL analytics endpoint (TDS) ---
    host: str = field(
        default_factory=lambda: _env("SQL_ENDPOINT_PROD", "SQL_ANALYTICS_ENDPOINT", default="") or ""
    )
    tenant_id: str | None = field(default_factory=lambda: _env("FABRIC_TENANT_ID", "AZURE_TENANT_ID"))
    client_id: str | None = field(default_factory=lambda: _env("FABRIC_CLIENT_ID", "AZURE_CLIENT_ID"))
    client_secret: str | None = field(default_factory=lambda: _env("FABRIC_CLIENT_SECRET", "AZURE_CLIENT_SECRET"))
    odbc_driver: str = field(
        default_factory=lambda: _env("FABRIC_ODBC_DRIVER", default="ODBC Driver 18 for SQL Server") or ""
    )
    connect_timeout: int = field(default_factory=lambda: int(_env("FABRIC_CONNECT_TIMEOUT", default="15") or 15))

    # --- Gold objects ---
    # Physical names are resolved here and never sent to the browser.
    gold_database: str = field(
        default_factory=lambda: _env("GOLD_DATABASE", default="ctrader_lakehouse") or "ctrader_lakehouse"
    )
    gold_schema: str = field(default_factory=lambda: _env("GOLD_SCHEMA", default="gold") or "gold")
    snapshot_table: str = field(
        default_factory=lambda: _env("SNAPSHOT_TABLE", default="agg_dom_book_snapshot") or "agg_dom_book_snapshot"
    )
    #: Per-broker symbol dimensions in the gold schema, in precedence order.
    symbol_tables: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            t.strip() for t in
            (_env("SYMBOL_TABLES", default="symbols_pepperstone,symbols_icmarkets") or "").split(",")
            if t.strip()
        )
    )

    app_name: str = "Axioma Analytics"
    max_ticks: int = field(default_factory=lambda: int(_env("MAX_TICKS", default="200000") or 200000))

    @property
    def server(self) -> str:
        return self.host.split(",")[0].strip()

    @property
    def port(self) -> int:
        parts = [p.strip() for p in self.host.split(",")]
        return int(parts[1]) if len(parts) > 1 else 1433

    @property
    def has_credentials(self) -> bool:
        return bool(self.tenant_id and self.client_id and self.client_secret)

    @property
    def is_configured(self) -> bool:
        return bool(self.host)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()