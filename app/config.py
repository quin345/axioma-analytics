"""Application configuration, loaded from .env (never committed)."""
from __future__ import annotations

import os
import threading
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


def _clean(value: str | None) -> str | None:
    return value.strip().strip('"').strip("'") if value and value.strip() else None


# --------------------------------------------------------------------------
# Environment selection
#
# One process can talk to several Fabric SQL analytics endpoints (dev / test /
# prod). Each is declared as SQL_ENDPOINT_<NAME>=<host> and the active one is
# chosen with SQL_ENV=<NAME>. The choice can also change at runtime via
# set_environment(), which is what the dashboard selector and the
# /api/environments endpoint call.
# --------------------------------------------------------------------------

_ENDPOINT_PREFIX = "SQL_ENDPOINT_"
_ENV_SELECTOR_VARS = ("SQL_ENV", "SQL_ENVIRONMENT")
_KNOWN_ENVIRONMENTS = ("dev", "test", "prod")

_active_env: str | None = None
_env_lock = threading.Lock()


def available_environments() -> dict[str, str]:
    """{name: host} for every configured endpoint, in a stable order.

    Names come from ``SQL_ENDPOINT_<NAME>``. When none are defined, a legacy
    single ``SQL_ANALYTICS_ENDPOINT`` is exposed as the ``default`` environment
    so existing deployments keep working unchanged.
    """
    found: dict[str, str] = {}
    for key, value in os.environ.items():
        if not key.upper().startswith(_ENDPOINT_PREFIX):
            continue
        name = key[len(_ENDPOINT_PREFIX):].strip().lower()
        host = _clean(value)
        if name and host:
            found[name] = host

    if not found:
        legacy = _clean(_env("SQL_ANALYTICS_ENDPOINT", "FABRIC_HOST"))
        if legacy:
            found["default"] = legacy

    # Present dev/test/prod first (in that order) so the UI dropdown is stable.
    ordered: dict[str, str] = {}
    for name in _KNOWN_ENVIRONMENTS:
        if name in found:
            ordered[name] = found[name]
    for name in sorted(k for k in found if k not in ordered):
        ordered[name] = found[name]
    return ordered


def active_environment() -> str:
    """Name of the selected environment (empty when none is configured)."""
    if _active_env:
        return _active_env
    envs = available_environments()
    chosen = (_clean(_env(*_ENV_SELECTOR_VARS)) or "").lower()
    if chosen in envs:
        return chosen
    return next(iter(envs), "")


def environment_host(name: str | None = None) -> str | None:
    """Host for one environment (defaults to the active one)."""
    envs = available_environments()
    if name is None:
        name = active_environment()
    return envs.get((name or "").lower())


def set_environment(name: str | None) -> str:
    """Select the active environment and rebuild the cached settings.

    ``None`` reverts to the ``SQL_ENV`` default. Raises ``ValueError`` for an
    unknown name so the API can return an actionable error.
    """
    global _active_env
    envs = available_environments()
    wanted = (name or "").strip().lower()
    if wanted and envs and wanted not in envs:
        raise ValueError(
            f"Unknown environment '{wanted}'. "
            f"Available: {', '.join(envs) or 'none configured'}."
        )
    with _env_lock:
        _active_env = wanted or None
        get_settings.cache_clear()
    return active_environment()


def _resolve_host() -> str:
    """Host for the active environment (falls back to the first configured)."""
    envs = available_environments()
    if envs:
        return envs.get(active_environment()) or next(iter(envs.values()))
    return _clean(_env("SQL_ANALYTICS_ENDPOINT", "FABRIC_HOST")) or ""


def mask_host(host: str | None) -> str:
    """Opaque but human-checkable form of an endpoint host.

    Storage internals are never sent to the browser in full; this keeps enough
    of the unique segment to tell environments apart without revealing it.
    """
    if not host:
        return ""
    first = host.split(",", 1)[0].split(".", 1)[0]
    if len(first) <= 12:
        return first
    return f"{first[:6]}\u2026{first[-6:]}"


@dataclass(frozen=True)
class Settings:
    # --- Data source endpoint (TDS) ---
    # Resolved from the active environment (SQL_ENDPOINT_<NAME> / SQL_ENV).
    host: str = field(default_factory=_resolve_host)
    environment: str = field(default_factory=lambda: active_environment() or "default")
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

    # The single book-snapshot table the app reads from. Its physical name is
    # never surfaced to clients - it is resolved internally only.
    data_table: str = field(
        default_factory=lambda: _env("DATA_TABLE", default="gold.agg_dom_book_snapshot")
        or "gold.agg_dom_book_snapshot"
    )

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
        """Data source endpoint via Entra ID service principal.

        Note: this endpoint type does NOT accept a `Database=` key - objects are
        addressed as fully-qualified [schema].[table].
        """
        if not self.host:
            raise RuntimeError(
                "No SQL analytics endpoint is configured. Set SQL_ENDPOINT_<NAME> "
                "(and SQL_ENV to pick one), or the legacy SQL_ANALYTICS_ENDPOINT."
            )
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