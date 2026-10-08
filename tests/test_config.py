"""Tests for the production configuration (no data source required).

The app has one data source - the Fabric KQL endpoint - and one cache. There is
no environment selector, no catalog discovery and no SQL endpoint, so most of
these assertions are about the *defaults*: the KQL database and table, the
instrument dimension, the fixed cache window and the TTL that keeps the cache
from outliving the data it mirrors.
"""
from __future__ import annotations

import pytest

from app import config
from app.config import Settings

#: Every variable `Settings` reads. Cleared so assertions are about the
#: defaults rather than about whatever this machine's ``.env`` happens to set.
_ENV_KEYS = (
    "KQL_ENDPOINT_PROD", "KQL_ENDPOINT", "KQL_DATABASE", "KQL_TABLE",
    "KQL_LAG_MINUTES", "SYMBOL_TABLE", "DEFAULT_SYMBOL", "MAX_TICKS",
    "REDIS_HOST", "REDIS_PORT", "REDIS_SSL", "REDIS_DB", "REDIS_TIMEOUT",
    "REDIS_KEY_PREFIX", "REDIS_TTL_SECONDS", "CACHE_LOOKBACK_HOURS",
    "FABRIC_TENANT_ID", "FABRIC_CLIENT_ID", "FABRIC_CLIENT_SECRET",
    "FABRIC_MANAGED_IDENTITY", "FABRIC_MANAGED_IDENTITY_CLIENT_ID",
    "MAINTENANCE_MODE",
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Build Settings from a known-empty environment, then restore."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


# --- The KQL data source ------------------------------------------------

def test_defaults_point_at_the_kql_database():
    s = Settings()
    assert s.kql_database == "ctrader_dom"
    assert s.kql_table == "agg_dom"


def test_defaults_point_at_the_kql_dimension():
    """The instrument dimension lives in the same KQL database, not in SQL."""
    assert Settings().symbol_table == "symbols_icmarkets"


def test_default_symbol_is_gold():
    assert Settings().default_symbol == "XAUUSD"


def test_kql_endpoint_is_read(monkeypatch):
    monkeypatch.setenv("KQL_ENDPOINT_PROD", "https://kusto.host")
    s = Settings()
    assert s.kql_host == "https://kusto.host"
    assert s.kql_configured
    assert s.is_configured


def test_kql_endpoint_falls_back_to_the_legacy_name(monkeypatch):
    monkeypatch.setenv("KQL_ENDPOINT", "https://legacy-kusto.host")
    assert Settings().kql_host == "https://legacy-kusto.host"


def test_prod_endpoint_wins_over_the_legacy_name(monkeypatch):
    monkeypatch.setenv("KQL_ENDPOINT", "https://legacy-kusto.host")
    monkeypatch.setenv("KQL_ENDPOINT_PROD", "https://kusto.host")
    assert Settings().kql_host == "https://kusto.host"


def test_kql_object_names_are_overridable(monkeypatch):
    monkeypatch.setenv("KQL_DATABASE", "other_kql")
    monkeypatch.setenv("KQL_TABLE", "agg_other")
    s = Settings()
    assert (s.kql_database, s.kql_table) == ("other_kql", "agg_other")


def test_symbol_table_is_overridable(monkeypatch):
    monkeypatch.setenv("SYMBOL_TABLE", "symbols_other")
    assert Settings().symbol_table == "symbols_other"


def test_the_lag_buffer_defaults_to_two_minutes():
    """A tick written just before the window opens must still be returned."""
    assert Settings().kql_lag_minutes == 2


def test_missing_endpoint_is_reported_not_raised():
    s = Settings()
    assert s.kql_host == ""
    assert not s.kql_configured
    assert not s.is_configured


def test_blank_endpoint_falls_through(monkeypatch):
    monkeypatch.setenv("KQL_ENDPOINT_PROD", "   ")
    monkeypatch.setenv("KQL_ENDPOINT", "https://legacy-kusto.host")
    assert Settings().kql_host == "https://legacy-kusto.host"


# --- The Redis cache ----------------------------------------------------
# The cache is configured independently of the data source: an app with no
# reachable cache still works, it just re-reads KQL on every request.

def test_cache_endpoint_has_defaults():
    s = Settings()
    assert s.redis_host
    assert s.redis_port == 10000
    assert s.redis_ssl is True
    assert s.redis_configured


def test_cache_defaults_are_entra_authenticated():
    """Redis Enterprise here is Entra-only, so there is no password to read."""
    assert not hasattr(Settings(), "redis_password")
    assert Settings().redis_timeout == 15


def test_cache_window_is_fixed_at_four_hours():
    s = Settings()
    assert s.cache_lookback_hours == 4
    assert s.cache_lookback_minutes == 240


def test_cache_window_is_overridable(monkeypatch):
    monkeypatch.setenv("CACHE_LOOKBACK_HOURS", "8")
    s = Settings()
    assert (s.cache_lookback_hours, s.cache_lookback_minutes) == (8, 480)


def test_cache_window_is_never_degenerate(monkeypatch):
    monkeypatch.setenv("CACHE_LOOKBACK_HOURS", "0")
    assert Settings().cache_lookback_minutes == 1


def test_cache_ttl_covers_one_ingestion_interval():
    """45 minutes: long enough to be useful, short enough not to outlive data."""
    assert Settings().cache_ttl_seconds == 2700


def test_cache_key_prefix_defaults(monkeypatch):
    assert Settings().cache_key_prefix == "axioma"
    monkeypatch.setenv("REDIS_KEY_PREFIX", "other")
    assert Settings().cache_key_prefix == "other"


def test_cache_settings_are_overridable(monkeypatch):
    monkeypatch.setenv("REDIS_HOST", "cache.example.net")
    monkeypatch.setenv("REDIS_PORT", "6380")
    monkeypatch.setenv("REDIS_SSL", "false")
    monkeypatch.setenv("REDIS_DB", "2")
    s = Settings()
    assert (s.redis_host, s.redis_port, s.redis_ssl, s.redis_db) == \
        ("cache.example.net", 6380, False, 2)


def test_a_host_without_a_port_is_not_configured(monkeypatch):
    monkeypatch.setenv("REDIS_HOST", "")
    monkeypatch.setenv("REDIS_PORT", "0")
    assert not Settings().redis_configured


def test_a_kql_endpoint_without_a_cache_is_still_usable():
    """The cache is an optimisation, never a precondition."""
    s = Settings(kql_host="https://kusto.host", redis_host="", redis_port=0)
    assert s.is_configured
    assert not s.redis_configured



# --- Entra ID identity --------------------------------------------------

def test_credentials_drive_has_credentials(monkeypatch):
    monkeypatch.setenv("FABRIC_TENANT_ID", "t")
    monkeypatch.setenv("FABRIC_CLIENT_ID", "c")
    monkeypatch.setenv("FABRIC_CLIENT_SECRET", "")
    assert not Settings().has_credentials
    monkeypatch.setenv("FABRIC_CLIENT_SECRET", "s")
    assert Settings().has_credentials


# The production host authenticates with the VM's system-assigned managed
# identity, so the flag has to be read from the environment and has to win
# over any credential that happens to be lying around. One identity
# authenticates both KQL and Redis.

@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test_managed_identity_flag_is_truthy(monkeypatch, raw):
    monkeypatch.setenv("FABRIC_MANAGED_IDENTITY", raw)
    assert Settings().managed_identity
    assert Settings().use_managed_identity


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "   "])
def test_managed_identity_defaults_off(monkeypatch, raw):
    monkeypatch.setenv("FABRIC_MANAGED_IDENTITY", raw)
    assert not Settings().use_managed_identity


def test_managed_identity_absent_is_off(monkeypatch):
    monkeypatch.delenv("FABRIC_MANAGED_IDENTITY", raising=False)
    assert not Settings().use_managed_identity


def test_managed_identity_overrides_credentials(monkeypatch):
    """MSI is unattended; a stale FABRIC_CLIENT_SECRET must not disable it."""
    monkeypatch.setenv("FABRIC_MANAGED_IDENTITY", "true")
    monkeypatch.setenv("FABRIC_TENANT_ID", "t")
    monkeypatch.setenv("FABRIC_CLIENT_ID", "c")
    monkeypatch.setenv("FABRIC_CLIENT_SECRET", "s")
    assert Settings().has_credentials
    assert Settings().use_managed_identity


def test_user_assigned_identity_carries_a_client_id(monkeypatch):
    monkeypatch.setenv("FABRIC_MANAGED_IDENTITY", "true")
    monkeypatch.setenv("FABRIC_MANAGED_IDENTITY_CLIENT_ID", "abc-123")
    assert Settings().managed_identity_client_id == "abc-123"


# --- Misc ---------------------------------------------------------------

def test_max_ticks_is_overridable(monkeypatch):
    monkeypatch.setenv("MAX_TICKS", "1000")
    assert Settings().max_ticks == 1000


def test_get_settings_is_cached():
    config.get_settings.cache_clear()
    assert config.get_settings() is config.get_settings()


# The flag decides whether / serves the branded holding page, so a stray value
# in the environment must never turn it on by accident: only the affirmative
# spellings count.

def test_maintenance_is_off_by_default():
    assert Settings().maintenance is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test_maintenance_affirmative_values_turn_it_on(monkeypatch, raw):
    monkeypatch.setenv("MAINTENANCE_MODE", raw)
    assert Settings().maintenance is True


@pytest.mark.parametrize("raw", ["", "   ", "false", "0", "no", "off"])
def test_maintenance_other_values_leave_it_off(monkeypatch, raw):
    monkeypatch.setenv("MAINTENANCE_MODE", raw)
    assert Settings().maintenance is False

