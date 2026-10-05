"""Tests for the production gold configuration (no data source required)."""
from __future__ import annotations

import pytest

from app import config
from app.config import Settings


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Build Settings from a known-empty environment, then restore."""
    for key in ("SQL_ENDPOINT_PROD", "SQL_ANALYTICS_ENDPOINT", "GOLD_DATABASE",
                "GOLD_SCHEMA", "SNAPSHOT_TABLE", "SYMBOL_TABLE"):
        monkeypatch.delenv(key, raising=False)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_defaults_point_at_the_gold_schema():
    s = Settings()
    assert s.gold_database == "ctrader_lakehouse"
    assert s.gold_schema == "gold"
    assert s.snapshot_table == "agg_dom_book_snapshot"
    assert s.symbol_table == "symbols_icmarkets"


def test_prod_endpoint_is_read(monkeypatch):
    monkeypatch.setenv("SQL_ENDPOINT_PROD", "prod.host")
    s = Settings()
    assert s.host == "prod.host"
    assert s.server == "prod.host"
    assert s.port == 1433
    assert s.is_configured


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
    monkeypatch.setenv("SNAPSHOT_TABLE", "book")
    s = Settings()
    assert (s.gold_database, s.gold_schema, s.snapshot_table) == ("other_db", "silver", "book")


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


def test_get_settings_is_cached():
    config.get_settings.cache_clear()
    assert config.get_settings() is config.get_settings()