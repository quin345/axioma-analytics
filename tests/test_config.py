"""Tests for the production configuration (no data source required)."""
from __future__ import annotations

import pytest

from app import config
from app.config import Settings
from app.db import connection_string


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Build Settings from a known-empty environment, then restore."""
    for key in ("SQL_ENDPOINT_PROD", "SQL_ANALYTICS_ENDPOINT", "GOLD_DATABASE",
                "GOLD_SCHEMA", "SYMBOL_TABLE", "DEFAULT_SYMBOL",
                "KQL_ENDPOINT_PROD", "KQL_ENDPOINT", "KQL_DATABASE", "KQL_TABLE",
                "FABRIC_MANAGED_IDENTITY", "FABRIC_MANAGED_IDENTITY_CLIENT_ID",
                "MAINTENANCE_MODE"):
        monkeypatch.delenv(key, raising=False)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_defaults_point_at_the_gold_schema():
    s = Settings()
    assert s.gold_database == "ctrader_lakehouse"
    assert s.gold_schema == "gold"
    assert s.symbol_table == "symbols_icmarkets"


def test_defaults_point_at_the_kql_database():
    """The aggregate rows moved off SQL onto the KQL endpoint."""
    s = Settings()
    assert s.kql_database == "ctrader_dom"
    assert s.kql_table == "agg_dom"


def test_default_symbol_is_gold():
    assert Settings().default_symbol == "XAUUSD"


def test_prod_endpoint_is_read(monkeypatch):
    monkeypatch.setenv("SQL_ENDPOINT_PROD", "prod.host")
    monkeypatch.setenv("KQL_ENDPOINT_PROD", "https://kusto.host")
    s = Settings()
    assert s.host == "prod.host"
    assert s.server == "prod.host"
    assert s.port == 1433
    assert s.kql_host == "https://kusto.host"
    assert s.is_configured


def test_kql_endpoint_falls_back_to_the_legacy_name(monkeypatch):
    monkeypatch.setenv("KQL_ENDPOINT", "https://legacy-kusto.host")
    assert Settings().kql_host == "https://legacy-kusto.host"


def test_both_endpoints_are_required(monkeypatch):
    """Rows come from KQL and the dimension from SQL; neither alone is enough."""
    monkeypatch.setenv("SQL_ENDPOINT_PROD", "prod.host")
    assert not Settings().is_configured
    monkeypatch.setenv("KQL_ENDPOINT_PROD", "https://kusto.host")
    assert Settings().is_configured


def test_dev_and_test_endpoints_are_ignored(monkeypatch):
    monkeypatch.setenv("SQL_ENDPOINT_DEV", "dev.host")
    monkeypatch.setenv("SQL_ENDPOINT_TEST", "test.host")
    monkeypatch.setenv("SQL_ENDPOINT_PROD", "prod.host")
    assert Settings().host == "prod.host"


def test_legacy_endpoint_is_the_fallback(monkeypatch):
    monkeypatch.setenv("SQL_ANALYTICS_ENDPOINT", "legacy.host")
    assert Settings().host == "legacy.host"


def test_missing_endpoint_is_reported_not_raised(monkeypatch):
    s = Settings()
    assert s.host == ""
    assert not s.is_configured


def test_blank_endpoint_falls_through(monkeypatch):
    monkeypatch.setenv("SQL_ENDPOINT_PROD", "   ")
    monkeypatch.setenv("SQL_ANALYTICS_ENDPOINT", "legacy.host")
    assert Settings().host == "legacy.host"


def test_host_may_carry_an_explicit_port(monkeypatch):
    monkeypatch.setenv("SQL_ENDPOINT_PROD", "prod.host,1444")
    s = Settings()
    assert s.server == "prod.host"
    assert s.port == 1444


def test_gold_object_names_are_overridable(monkeypatch):
    monkeypatch.setenv("GOLD_DATABASE", "other_db")
    monkeypatch.setenv("GOLD_SCHEMA", "silver")
    s = Settings()
    assert (s.gold_database, s.gold_schema) == ("other_db", "silver")


def test_kql_object_names_are_overridable(monkeypatch):
    monkeypatch.setenv("KQL_DATABASE", "other_kql")
    monkeypatch.setenv("KQL_TABLE", "agg_other")
    s = Settings()
    assert (s.kql_database, s.kql_table) == ("other_kql", "agg_other")


def test_symbol_table_is_overridable(monkeypatch):
    monkeypatch.setenv("SYMBOL_TABLE", "symbols_other")
    assert Settings().symbol_table == "symbols_other"


def test_credentials_drive_has_credentials(monkeypatch):
    monkeypatch.setenv("FABRIC_TENANT_ID", "t")
    monkeypatch.setenv("FABRIC_CLIENT_ID", "c")
    monkeypatch.setenv("FABRIC_CLIENT_SECRET", "")
    assert not Settings().has_credentials
    monkeypatch.setenv("FABRIC_CLIENT_SECRET", "s")
    assert Settings().has_credentials


# --- Managed identity ---------------------------------------------------
# The production host authenticates with the VM's system-assigned managed
# identity, so the flag has to be read from the environment and has to win
# over any credential that happens to be lying around.

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


# --- Connection string selection ----------------------------------------
# The ODBC driver only accepts the literal `ActiveDirectoryMSI`; the longer
# `ActiveDirectoryManagedIdentity` spelling fails with "Invalid value
# specified for connection string attribute 'Authentication'".

def _string(**env: str) -> str:
    """Build a connection string from explicit fields only.

    `Settings` reads defaults from the environment, and the repo's `.env` turns
    managed identity on for the production host. Passing both fields explicitly
    keeps these assertions about the *selection* logic rather than about
    whatever this machine happens to be configured with.
    """
    defaults = {"managed_identity": False, "managed_identity_client_id": None}
    defaults.update(env)
    return connection_string(Settings(host="prod.host", **defaults))


def test_managed_identity_uses_the_msi_spelling():
    cs = _string(managed_identity=True)
    assert "Authentication=ActiveDirectoryMSI" in cs
    assert "ManagedIdentity" not in cs


def test_managed_identity_system_assigned_sends_no_client_id():
    """Blank client id means the system-assigned identity; omit the keyword."""
    cs = _string(managed_identity=True, managed_identity_client_id=None)
    assert "ClientId=" not in cs


def test_user_assigned_identity_sends_the_client_id():
    cs = _string(managed_identity=True, managed_identity_client_id="abc-123")
    assert "ClientId=abc-123" in cs


def test_managed_identity_is_not_confused_with_service_principal():
    cs = _string(managed_identity=True,
                 tenant_id="t", client_id="c", client_secret="s")
    assert "ActiveDirectoryServicePrincipal" not in cs
    assert "PWD=" not in cs


def test_service_principal_used_when_no_managed_identity():
    cs = _string(tenant_id="t", client_id="c", client_secret="s")
    assert "Authentication=ActiveDirectoryServicePrincipal" in cs
    assert "UID=c" in cs


def test_plain_connection_has_no_authentication_when_unset():
    """No credentials and no identity: the driver stays unauthenticated and the
    `az login` token path in connect() takes over."""
    cs = _string()
    assert "Authentication=" not in cs


def test_connection_string_always_pins_tls_and_the_server():
    cs = _string(managed_identity=True)
    assert "SERVER=prod.host,1433" in cs
    assert "Encrypt=yes" in cs
    assert "TrustServerCertificate=yes" in cs


def test_get_settings_is_cached():
    config.get_settings.cache_clear()
    assert config.get_settings() is config.get_settings()


# --- Maintenance mode -----------------------------------------------------
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