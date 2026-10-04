"""Environment selection for the SQL analytics endpoints (no data source)."""
from __future__ import annotations

import os

import pytest

from app import config

_ENV_KEYS = ("SQL_ENV", "SQL_ENVIRONMENT", "SQL_ANALYTICS_ENDPOINT", "FABRIC_HOST")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Strip any real endpoints (e.g. from a loaded .env) around each test."""
    for key in list(os.environ):
        if key.upper().startswith(config._ENDPOINT_PREFIX) or key.upper() in _ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    config.set_environment(None)
    config.get_settings.cache_clear()
    yield
    config.set_environment(None)
    config.get_settings.cache_clear()


def test_legacy_endpoint_becomes_default(monkeypatch):
    monkeypatch.setenv("SQL_ANALYTICS_ENDPOINT", "legacy.host")
    assert config.available_environments() == {"default": "legacy.host"}
    assert config.active_environment() == "default"
    assert config.get_settings().host == "legacy.host"
    assert config.get_settings().environment == "default"


def test_named_environments_are_ordered(monkeypatch):
    monkeypatch.setenv("SQL_ENDPOINT_PROD", "prod.host")
    monkeypatch.setenv("SQL_ENDPOINT_DEV", "dev.host")
    monkeypatch.setenv("SQL_ENDPOINT_TEST", "test.host")
    assert list(config.available_environments()) == ["dev", "test", "prod"]


def test_sql_env_selects_active_endpoint(monkeypatch):
    monkeypatch.setenv("SQL_ENDPOINT_DEV", "dev.host")
    monkeypatch.setenv("SQL_ENDPOINT_TEST", "test.host")
    monkeypatch.setenv("SQL_ENV", "test")
    assert config.active_environment() == "test"
    assert config.get_settings().host == "test.host"
    assert config.get_settings().environment == "test"


def test_blank_endpoints_are_ignored(monkeypatch):
    monkeypatch.setenv("SQL_ENDPOINT_DEV", "")
    monkeypatch.setenv("SQL_ENDPOINT_TEST", "test.host")
    assert config.available_environments() == {"test": "test.host"}


def test_runtime_switch_rebuilds_settings(monkeypatch):
    monkeypatch.setenv("SQL_ENDPOINT_DEV", "dev.host")
    monkeypatch.setenv("SQL_ENDPOINT_PROD", "prod.host")
    assert config.set_environment("dev") == "dev"
    assert config.get_settings().host == "dev.host"
    assert config.set_environment("prod") == "prod"
    assert config.get_settings().host == "prod.host"


def test_unknown_environment_rejected(monkeypatch):
    monkeypatch.setenv("SQL_ENDPOINT_DEV", "dev.host")
    with pytest.raises(ValueError):
        config.set_environment("nope")


def test_unknown_env_falls_back_to_first_configured(monkeypatch):
    monkeypatch.setenv("SQL_ENDPOINT_DEV", "dev.host")
    monkeypatch.setenv("SQL_ENDPOINT_TEST", "test.host")
    monkeypatch.setenv("SQL_ENV", "staging")  # not configured
    assert config.active_environment() == "dev"
    assert config.get_settings().host == "dev.host"


def test_mask_host_hides_middle():
    assert config.mask_host(None) == ""
    assert config.mask_host("") == ""
    assert config.mask_host("short.host") == "short"
    assert config.mask_host("abcdefghijklmnop.datawarehouse.fabric.microsoft.com") == "abcdef\u2026klmnop"
