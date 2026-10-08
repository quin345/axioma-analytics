"""Application configuration for the production data endpoints.

The app reads **one** source: the production Fabric **KQL** database
(`KQL_ENDPOINT_PROD`). Every table it uses lives there -

* ``dom_stream_raw`` / ``dom_book_flat`` - the raw and flattened DOM feed
  (ingested, not queried directly by the dashboard),
* ``agg_dom`` - the per-tick aggregate order-book metrics the analytics read,
* ``symbols_icmarkets`` - the icmarkets instrument dimension.

Query results are cached in Redis (`REDIS_HOST`) on the same Entra identity, so
the dashboard reads KQL once per window rather than on every request. The cache
window is fixed at `CACHE_LOOKBACK_HOURS` (4 by default); the UI may narrow it
but never widen it. A background cycle (`CACHE_REFRESH_MINUTES`, 30 by default)
re-reads that whole window on a timer, so what the dashboard serves is never
more than half an hour behind the feed.

There is no environment selector, no catalog discovery and no synthetic
fallback - the configuration is just the endpoints, the Entra ID identity and
the object names.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

#: Affirmative spellings accepted for boolean flags.
_TRUTHY = ("1", "true", "yes", "on")


def _env(*names: str, default: str | None = None) -> str | None:
    """First non-blank environment variable from `names`, unquoted."""
    for n in names:
        v = os.getenv(n)
        if v and v.strip():
            return v.strip().strip('"').strip("'")
    return default


def _env_int(*names: str, default: int) -> int:
    """First parseable integer from `names`, else `default`."""
    raw = _env(*names)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _env_bool(*names: str, default: bool) -> bool:
    """First affirmative boolean from `names`, else `default`."""
    raw = _env(*names)
    if raw is None:
        return default
    return raw.lower() in _TRUTHY


@dataclass(frozen=True)
class Settings:
    tenant_id: str | None = field(default_factory=lambda: _env("FABRIC_TENANT_ID", "AZURE_TENANT_ID"))
    client_id: str | None = field(default_factory=lambda: _env("FABRIC_CLIENT_ID", "AZURE_CLIENT_ID"))
    client_secret: str | None = field(default_factory=lambda: _env("FABRIC_CLIENT_SECRET", "AZURE_CLIENT_SECRET"))
    #: Azure managed identity: system-assigned when blank, user-assigned when a
    #: client id is given. Preferred over `az login` because it needs no
    #: interactive session and is the identity the Redis cache trusts.
    managed_identity: bool = field(
        default_factory=lambda: _env_bool("FABRIC_MANAGED_IDENTITY", default=False)
    )
    managed_identity_client_id: str | None = field(
        default_factory=lambda: _env("FABRIC_MANAGED_IDENTITY_CLIENT_ID")
    )

    # --- Production KQL (Eventhouse) endpoint: the only data source ---------
    kql_host: str = field(
        default_factory=lambda: _env("KQL_ENDPOINT_PROD", "KQL_ENDPOINT", default="") or ""
    )
    kql_database: str = field(
        default_factory=lambda: _env("KQL_DATABASE", default="ctrader_dom") or "ctrader_dom"
    )
    #: The per-tick aggregate DOM metrics the analytics read.
    kql_table: str = field(default_factory=lambda: _env("KQL_TABLE", default="agg_dom") or "agg_dom")
    #: The icmarkets instrument dimension, in the same KQL database.
    symbol_table: str = field(
        default_factory=lambda: _env("SYMBOL_TABLE", default="symbols_icmarkets") or "symbols_icmarkets"
    )
    #: Buffer applied to the KQL time filter so a tick written just before the
    #: window opens is still returned.
    kql_lag_minutes: int = field(default_factory=lambda: _env_int("KQL_LAG_MINUTES", default=2))

    # --- Redis cache --------------------------------------------------------
    #: Redis Enterprise (Entra-auth only - no key). The dashboard's KQL results
    #: live here between requests.
    redis_host: str = field(
        default_factory=lambda: _env("REDIS_HOST", default="axiomacache.australiacentral.redis.azure.net")
        or "axiomacache.australiacentral.redis.azure.net"
    )
    redis_port: int = field(default_factory=lambda: _env_int("REDIS_PORT", default=10000))
    redis_ssl: bool = field(default_factory=lambda: _env_bool("REDIS_SSL", default=True))
    redis_db: int = field(default_factory=lambda: _env_int("REDIS_DB", default=0))
    redis_timeout: int = field(default_factory=lambda: _env_int("REDIS_TIMEOUT", default=15))
    cache_key_prefix: str = field(
        default_factory=lambda: _env("REDIS_KEY_PREFIX", default="axioma") or "axioma"
    )
    #: Lifetime of a cached KQL result. 45 minutes covers one ingestion
    #: interval, so the cache never outlives the data it mirrors.
    cache_ttl_seconds: int = field(default_factory=lambda: _env_int("REDIS_TTL_SECONDS", default=2700))
    #: The fixed window the cache holds and the UI may only narrow.
    cache_lookback_hours: int = field(default_factory=lambda: _env_int("CACHE_LOOKBACK_HOURS", default=4))
    #: How often the background cycle re-reads that window from KQL and replaces
    #: the cached copies. 30 minutes is the maximum age of what the dashboard
    #: serves, and `cache_ttl_seconds` runs 1.5 cycles ahead of it so a single
    #: missed cycle still leaves the cache populated. 0 switches the cycle off
    #: and returns to lazy, on-demand caching.
    cache_refresh_minutes: int = field(
        default_factory=lambda: _env_int("CACHE_REFRESH_MINUTES", default=30)
    )

    app_name: str = "Axioma Analytics"
    max_ticks: int = field(default_factory=lambda: _env_int("MAX_TICKS", default=200000))
    #: Ticker pinned as the dashboard default (matched by symbolName, then id).
    default_symbol: str = field(
        default_factory=lambda: _env("DEFAULT_SYMBOL", default="XAUUSD") or "XAUUSD"
    )

    # --- Availability -------------------------------------------------------
    #: Serve the branded "temporarily unavailable" page instead of the
    #: dashboard, with a 503. For a planned maintenance window: set
    #: MAINTENANCE_MODE=1, restart, unset it afterwards. The API stays up so
    #: the page's own static assets and health checks keep working.
    maintenance: bool = field(
        default_factory=lambda: _env_bool("MAINTENANCE_MODE", default=False)
    )

    @property
    def has_credentials(self) -> bool:
        return bool(self.tenant_id and self.client_id and self.client_secret)

    @property
    def use_managed_identity(self) -> bool:
        """Managed identity wins over any credential in the environment.

        It is the only mode that works unattended: one identity authenticates
        both the KQL endpoint and the Redis cache, so the service needs neither
        a stored secret nor an `az login` session.
        """
        return self.managed_identity

    @property
    def kql_configured(self) -> bool:
        return bool(self.kql_host)

    @property
    def redis_configured(self) -> bool:
        return bool(self.redis_host and self.redis_port > 0)

    @property
    def is_configured(self) -> bool:
        """The KQL endpoint is the only data source; without it there is nothing."""
        return bool(self.kql_host)

    @property
    def cache_lookback_minutes(self) -> int:
        """The fixed cache window, in minutes - the widest the UI may ask for."""
        return max(1, self.cache_lookback_hours * 60)

    @property
    def cache_refresh_seconds(self) -> int:
        """The refresh interval in seconds; 0 means the cycle is off."""
        return max(0, self.cache_refresh_minutes) * 60


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()