"""Application configuration, loaded from .env (never committed)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _env(*names: str, default: str | None = None) -> str | None:
    for n in names:
        v = os.getenv(n)
        if v:
            return v.strip().strip('"').strip("'")
    return default


@dataclass(frozen=True)
class Settings:
    # --- Fabric SQL analytics endpoint (TDS) ---
    host: str = field(default_factory=lambda: _env("SQL_ANALYTICS_ENDPOINT", "FABRIC_HOST") or "")
    tenant_id: str | None = field(default_factory=lambda: _env("FABRIC_TENANT_ID", "AZURE_TENANT_ID"))
    client_id: str | None = field(default_factory=lambda: _env("FABRIC_CLIENT_ID", "AZURE_CLIENT_ID"))
    client_secret: str | None = field(default_factory=lambda: _env("FABRIC_CLIENT_SECRET", "AZURE_CLIENT_SECRET"))
    odbc_driver: str = field(
        default_factory=lambda: _env("FABRIC_ODBC_DRIVER", default="ODBC Driver 18 for SQL Server") or ""
    )
    connect_timeout: int = field(default_factory=lambda: int(_env("FABRIC_CONNECT_TIMEOUT", default="15") or 15))

    # Optional explicit table override. When unset, the app auto-discovers the
    # tick table by scoring every schema.table on its columns.
    tick_schema: str | None = field(default_factory=lambda: _env("FABRIC_TICK_SCHEMA"))
    tick_table: str | None = field(default_factory=lambda: _env("FABRIC_TICK_TABLE"))

    # Serve synthetic tick data when the warehouse is unreachable, so the UI is
    # always demonstrable. Set FABRIC_ALLOW_SYNTHETIC=false to fail hard instead.
    allow_synthetic: bool = field(
        default_factory=lambda: (_env("FABRIC_ALLOW_SYNTHETIC", default="true") or "true").lower() == "true"
    )

    # How many price levels per side count toward top-of-book depth when
    # reconstructing the order book from cTrader DOM events.
    l2_depth_levels: int = field(
        default_factory=lambda: int(_env("FABRIC_L2_DEPTH_LEVELS", default="5") or 5)
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

    def connection_string(self) -> str:
        """Fabric SQL analytics endpoint via Entra ID service principal.

        Note: Fabric SQL analytics endpoints do NOT accept a `Database=` key -
        objects are addressed as fully-qualified [schema].[dbo].[table].
        """
        if not self.host:
            raise RuntimeError("SQL_ANALYTICS_ENDPOINT is not set")
        parts = [
            f"DRIVER={{{self.odbc_driver}}}",
            f"SERVER={self.server},{self.port}",
            f"Encrypt=yes",
            f"TrustServerCertificate=yes",
            f"Connection Timeout={self.connect_timeout}",
        ]
        if self.has_credentials:
            parts += [
                "Authentication=ActiveDirectoryServicePrincipal",
                f"UID={self.client_id}",
                f"PWD={self.client_secret}",
            ]
        else:
            # Fall back to an integrated/default login so the failure surfaces
            # from the driver with a real error instead of a silent no-op.
            parts.append("Trusted_Connection=yes")
        return ";".join(parts)

    def redact(self) -> dict:
        d = dict(self.__dict__)
        if d.get("client_secret"):
            d["client_secret"] = "***"
        return d


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()