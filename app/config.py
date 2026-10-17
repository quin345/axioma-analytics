"""Application configuration for the production data endpoints.

The app reads **one** source: the production Fabric **KQL** database
(`KQL_ENDPOINT_PROD`). Every table it uses lives there -

* ``dom_stream_raw`` / ``dom_book_flat`` - the raw and flattened DOM feed
  (ingested, not queried directly by the dashboard),
* ``agg_dom`` - the per-tick aggregate order-book metrics the analytics read,
* ``metrics_5m`` - the precomputed window metrics: one row per
  ``(symbolId, as_of)`` stop with every metric already aggregated over the four
  fixed 1 h / 2 h / 3 h / 4 h trailing windows (`docs/metrics_5m.md`). This is
  what the headline metrics are served from - pushed to Redis by the refresh
  cycle and read back as a lookup, so a dashboard request never re-scans
  `agg_dom` nor re-derives anything in pandas.
* ``symbols_icmarkets`` - the icmarkets instrument dimension.

Query results are cached in Redis (`REDIS_HOST`) on the same Entra identity, so
the dashboard reads KQL once per window rather than on every request. The cache
window is fixed at `CACHE_LOOKBACK_HOURS` (8 by default); the UI may narrow it
but never widen it. A background cycle (`CACHE_REFRESH_MINUTES`, 5 by default)
slides that window on the clock - just past the five-minute marks (:00:15,
:05:15, ...) - purging the earliest slice and reading only the newest, so what
the dashboard serves is never more than five minutes behind the feed and a
symbol's cache
entry never holds more than eight hours of rows.

Eight hours is the sum the aggregate-window controls need: the timeline the user
picks a point from spans `TIMELINE_MINUTES` (240, four hours) and the widest
window at that point is 240 minutes more, so the earliest point with the widest
window reaches exactly eight hours back. Anything shorter and the oldest
selectable combination would fall outside the cache. The window itself is one of
a small fixed set (`WINDOW_CHOICES_MINUTES`, 60/120/180/240) that mirrors the
four trailing windows the precomputed `metrics_5m` table always ships.

There is no environment selector, no catalog discovery and no synthetic
fallback - the configuration is just the endpoints, the Entra ID identity and
the object names.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

#: Affirmative spellings accepted for boolean flags.
_TRUTHY = ("1", "true", "yes", "on")

#: The zone the dashboard renders wall-clock times in. New York by default -
#: the primary audience trades the US session - and any IANA name may be set
#: with DISPLAY_TIMEZONE.
_DEFAULT_DISPLAY_TIMEZONE = "America/New_York"


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


def _env_int_list(*names: str, default: tuple[int, ...]) -> tuple[int, ...]:
    """A comma/space separated integer list from `names`, else `default`.

    Duplicate, out-of-order and non-numeric entries are tolerated: the values
    are de-duped and sorted ascending, so a stray config value cannot leave the
    UI with two identical options or with a wider window offered after a
    narrower one.
    """
    raw = _env(*names)
    if not raw:
        return tuple(default)
    seen: set[int] = set()
    for part in raw.replace(" ", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            n = int(part)
        except ValueError:
            continue
        if n > 0:
            seen.add(n)
    return tuple(sorted(seen)) if seen else tuple(default)


def _display_timezone(raw: str | None) -> str:
    """A usable IANA zone name; New York when the value is missing or unknown."""
    name = (raw or "").strip() or _DEFAULT_DISPLAY_TIMEZONE
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # A typo, or a zone this tzdata build does not carry, must not crash
        # the app or silently bucket hours by the wrong clock: fall back to NY.
        return _DEFAULT_DISPLAY_TIMEZONE
    return name


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
    #: The precomputed window metrics: one row per `(symbolId, as_of)` stop with
    #: every metric already aggregated over the four fixed trailing windows
    #: (1 h / 2 h / 3 h / 4 h). Built by `Metrics5m()` in the metrics repo on the
    #: same cadence as the cache - see `docs/metrics_5m.md`, which is the
    #: source-of-truth for its columns. This is what `/api/metrics` reads, and
    #: what the refresh cycle pushes to Redis: the point of the table is that a
    #: dashboard request becomes a lookup of pre-aggregated columns instead of a
    #: re-scan of `agg_dom` and a re-derive in pandas.
    metrics_table: str = field(
        default_factory=lambda: _env("METRICS_TABLE", default="metrics_5m") or "metrics_5m"
    )
    #: `metrics_5m` (default) reads the precomputed table; `ticks` falls back to
    #: recomputing the metrics from `agg_dom`. See `metrics_source`.
    _metrics_source: str | None = field(
        default_factory=lambda: _env("METRICS_SOURCE", default="metrics_5m")
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
    #: Lifetime of a cached KQL result. 450 s (7.5 min) covers many refresh
    #: cycles, so one missed cycle still leaves the cache populated.
    cache_ttl_seconds: int = field(default_factory=lambda: _env_int("REDIS_TTL_SECONDS", default=450))
    #: The fixed window the cache holds and the UI may only narrow. Eight hours
    #: = a four-hour point-in-time timeline plus the widest (240 minute)
    #: aggregate window from its earliest point, so every combination the
    #: controls offer is served from the cache.
    cache_lookback_hours: int = field(default_factory=lambda: _env_int("CACHE_LOOKBACK_HOURS", default=8))
    #: How often the background cycle slides that window from KQL: every
    #: minute on the clock marks plus a short offset (:00:15, :01:15, ...),
    #: the earliest interval is purged and only the newest interval is read.
    #: One minute is the maximum age of what the dashboard serves, and
    #: `cache_ttl_seconds` runs several cycles ahead of it so a single missed
    #: cycle still leaves the cache populated. 0 switches the cycle off and
    #: returns to lazy, on-demand caching.
    cache_refresh_minutes: int = field(
        default_factory=lambda: _env_int("CACHE_REFRESH_MINUTES", default=1)
    )
    #: Delay past each clock mark before the cycle fires, so the
    #: upstream feed has settled and the read does not race immature data.
    #: 15 s puts the runs at :00:15, :01:15, ... (e.g. 4:45:15).
    cache_refresh_offset_seconds: float = field(
        default_factory=lambda: _env_int(
            "CACHE_REFRESH_OFFSET_SECONDS", default=15
        )
    )

    # --- Aggregate window ---------------------------------------------------
    #: The timeline the user picks a point from: this many minutes long, in
    #: steps of `timeline_step_minutes`. Four hours in five-minute steps puts
    #: every point on the clock grid while keeping the picker to 49 positions.
    timeline_minutes: int = field(default_factory=lambda: _env_int("TIMELINE_MINUTES", default=240))
    timeline_step_minutes: int = field(
        default_factory=lambda: _env_int("TIMELINE_STEP_MINUTES", default=5)
    )
    #: The fixed aggregate-window choices offered at a point, in minutes. These
    #: mirror the four trailing windows `metrics_5m` precomputes for every
    #: `(symbolId, as_of)` stop - 1 h / 2 h / 3 h / 4 h - so the UI offers only
    #: what the precomputed table always ships. The widest (240) plus the
    #: timeline is what fixes `cache_lookback_hours`: 240 + 240 = 480.
    #: `window_min_minutes` / `window_max_minutes` are the legacy continuous
    #: bounds, kept only so an older cached `/api/health` payload still renders
    #: a sensible control; the UI prefers `window_choices` when present.
    window_choices_minutes: tuple[int, ...] = field(
        default_factory=lambda: _env_int_list(
            "WINDOW_CHOICES_MINUTES", default=(60, 120, 180, 240)
        )
    )
    window_min_minutes: int = field(
        default_factory=lambda: _env_int("WINDOW_MIN_MINUTES", default=60)
    )
    window_max_minutes: int = field(
        default_factory=lambda: _env_int("WINDOW_MAX_MINUTES", default=240)
    )

    app_name: str = "Axioma Analytics"
    max_ticks: int = field(default_factory=lambda: _env_int("MAX_TICKS", default=200000))
    #: Ticker pinned as the dashboard default (matched by symbolName, then id).
    default_symbol: str = field(
        default_factory=lambda: _env("DEFAULT_SYMBOL", default="XAUUSD") or "XAUUSD"
    )

    #: IANA timezone the dashboard renders wall-clock times in; the "activity by
    #: hour" profile is bucketed by it, so hours read as the local session rather
    #: than UTC. New York by default, overridable with DISPLAY_TIMEZONE; an
    #: unknown zone falls back to New York.
    display_timezone: str = field(
        default_factory=lambda: _display_timezone(_env("DISPLAY_TIMEZONE"))
    )

    # --- Availability -------------------------------------------------------
    #: Serve the branded "temporarily unavailable" page instead of the
    #: dashboard, with a 503. For a planned maintenance window: set
    #: MAINTENANCE_MODE=1, restart, unset it afterwards. The API stays up so
    #: the page's own static assets and health checks keep working.
    maintenance: bool = field(
        default_factory=lambda: _env_bool("MAINTENANCE_MODE", default=False)
    )

    # --- Dashboard login (the website gate) ---------------------------------
    #: The account that signs in at /login. User-facing, and entirely separate
    #: from the Entra identity above, which only authenticates the data plane.
    login_username: str = field(
        default_factory=lambda: _env("LOGIN_USERNAME", default="admin") or "admin"
    )
    #: The gate exists only while this is set: a blank password leaves the site
    #: open, which is how a credential-less local run and the test suite
    #: behave. `run.py` prints the state so an open door in production is
    #: visible at startup rather than discovered later.
    login_password: str = field(
        default_factory=lambda: _env("LOGIN_PASSWORD") or ""
    )
    #: How long a signed-in session lives. Sessions are held in the app
    #: process (single worker), so a restart signs everyone out; the cookie
    #: and its server-side token expire together.
    login_session_hours: int = field(
        default_factory=lambda: _env_int("LOGIN_SESSION_HOURS", default=12)
    )
    #: True behind TLS (nginx terminates it in production) so the session
    #: cookie is never sent over plain HTTP. Off for http://127.0.0.1.
    login_cookie_secure: bool = field(
        default_factory=lambda: _env_bool("LOGIN_COOKIE_SECURE", default=False)
    )

    @property
    def has_credentials(self) -> bool:
        return bool(self.tenant_id and self.client_id and self.client_secret)

    @property
    def login_enabled(self) -> bool:
        """True while a dashboard password is configured - the gate is the
        password alone, so blank/unset leaves the site open."""
        return bool(self.login_password)

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
    def max_window_minutes(self) -> int:
        """The widest aggregate window the UI may offer, in minutes.

        `window_max_minutes` is what the control is built from, but never
        beyond what the cache can serve: a window wider than the cache would
        silently return less than the user asked for.
        """
        lo = max(1, self.window_min_minutes)
        return max(lo, min(self.window_max_minutes, self.cache_lookback_minutes))

    @property
    def window_choices(self) -> tuple[int, ...]:
        """The fixed aggregate-window choices the UI offers, in minutes.

        `window_choices_minutes` (the four `metrics_5m` windows: 1 h / 2 h /
        3 h / 4 h) capped by the cache window, so a shorter cache drops a
        choice it cannot serve rather than offering history it does not hold.
        Never empty: if every choice is wider than the cache, the cache window
        itself is offered so the control always has a usable value.
        """
        cap = self.cache_lookback_minutes
        choices = tuple(m for m in self.window_choices_minutes if m <= cap)
        return choices or (cap,)

    @property
    def metrics_source(self) -> str:
        """Where `/api/metrics` reads from: the precomputed table or the ticks.

        A rollback switch, not a data-source selector: the tick path is kept
        because `metrics_5m` is rebuilt by a separate scheduled job and a
        stalled or half-appended table would otherwise leave the dashboard with
        no headline metrics at all. Set `METRICS_SOURCE=ticks` to fall back to
        the pandas recompute without a deploy.
        """
        return (self._metrics_source or "").strip().lower() or "metrics_5m"

    @property
    def use_metrics_5m(self) -> bool:
        """True when the precomputed table backs the metrics read path."""
        return self.metrics_source == "metrics_5m"

    @property
    def cache_refresh_seconds(self) -> int:
        """The refresh interval in seconds; 0 means the cycle is off."""
        return max(0, self.cache_refresh_minutes) * 60


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()