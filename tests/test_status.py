"""Tests for the two-source status probe (no data source required).

Rows come from KQL and their names from SQL, so health is only green when both
answer. These tests pin that contract: a dashboard reporting `connected` while
the selectors still fail is worse than reporting which side is down.
"""
from __future__ import annotations

from contextlib import contextmanager

import pandas as pd
import pytest

from app import service
from app.config import Settings
from app.db import DataSourceError


@contextmanager
def _ok_conn():
    yield object()


@contextmanager
def _failing_conn(message: str):
    raise DataSourceError(message)
    yield  # pragma: no cover - the context never opens


@pytest.fixture
def settings() -> Settings:
    return Settings(host="sql.example.invalid", kql_host="https://kql.example.invalid",
                    kql_database="ctrader_dom", kql_table="agg_dom")


@pytest.fixture(autouse=True)
def _fresh_status(monkeypatch, settings):
    """Detach the probe from the module cache and from the real environment."""
    monkeypatch.setattr(service, "_status", service.Status())
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    yield


def _patch_kql(monkeypatch, *, rows: int = 100, latest: str | None = "2026-10-08T01:00:00Z",
               error: str | None = None) -> None:
    if error:
        monkeypatch.setattr(service.kql, "connect", lambda settings=None: _failing_conn(error))
    else:
        monkeypatch.setattr(service.kql, "connect", lambda settings=None: _ok_conn())
        monkeypatch.setattr(service.kql, "server_time",
                            lambda client, settings=None: "2026-10-08T01:00:00Z")
        monkeypatch.setattr(service.kql, "snapshot_stats",
                            lambda client, settings=None: (rows, latest))


def _patch_sql(monkeypatch, *, rows: int = 348, error: str | None = None) -> list[str]:
    """Patch the dimension read; return the list the probe's SQL is recorded in."""
    seen: list[str] = []
    if error:
        monkeypatch.setattr(service, "connect", lambda settings=None: _failing_conn(error))
    else:
        monkeypatch.setattr(service, "connect", lambda settings=None: _ok_conn())

        def _query(conn, sql, params=None):
            seen.append(sql)
            return pd.DataFrame({"n": [rows]})

        monkeypatch.setattr(service, "query", _query)
    return seen


def test_both_sources_answering_reports_connected(monkeypatch):
    _patch_kql(monkeypatch)
    seen = _patch_sql(monkeypatch)

    st = service.status(refresh=True)

    assert st.connected is True
    assert st.error is None
    assert st.hints == []
    assert st.row_count == 100
    assert st.dimension_rows == 348
    # The probe must read the configured dimension, not a hard-coded table.
    assert "symbols_icmarkets" in seen[0]


def test_a_failing_kql_endpoint_disconnects_the_dashboard(monkeypatch):
    _patch_kql(monkeypatch, error="KQL query failed: Forbidden (403-Forbidden)")
    _patch_sql(monkeypatch)

    st = service.status(refresh=True)

    assert st.connected is False
    assert "KQL" in st.hints[0]
    assert "403" in st.error
    # The dimension still answered, so its count is reported rather than lost.
    assert st.dimension_rows == 348
    assert st.row_count is None


def test_a_failing_dimension_disconnects_the_dashboard(monkeypatch):
    _patch_kql(monkeypatch)
    _patch_sql(monkeypatch, error="Query failed: underlying location does not exist")

    st = service.status(refresh=True)

    assert st.connected is False
    assert st.dimension_rows is None
    assert len(st.hints) == 1
    assert "dimension" in st.hints[0]
    # Rows were readable, so the row evidence survives the dimension failure.
    assert st.row_count == 100
    assert "underlying location" in st.error


def test_both_failures_are_reported_side_by_side(monkeypatch):
    _patch_kql(monkeypatch, error="KQL query failed: Forbidden (403-Forbidden)")
    _patch_sql(monkeypatch, error="Query failed: underlying location does not exist")

    st = service.status(refresh=True)

    assert st.connected is False
    assert len(st.hints) == 2
    assert "KQL" in st.hints[0] and "dimension" in st.hints[1]
    # The first failure is the one kept as the headline error.
    assert "KQL query failed" in st.error


def test_an_empty_kql_table_hints_at_the_ingest_pipeline(monkeypatch):
    _patch_kql(monkeypatch, rows=0, latest=None)
    _patch_sql(monkeypatch)

    st = service.status(refresh=True)

    assert st.connected is True
    assert st.row_count == 0
    assert "empty" in st.hints[0]


def test_the_probe_is_cached_until_refreshed(monkeypatch):
    _patch_kql(monkeypatch)
    seen = _patch_sql(monkeypatch)

    service.status(refresh=True)
    service.status()      # cached
    assert len(seen) == 1

    service.status(refresh=True)
    assert len(seen) == 2
