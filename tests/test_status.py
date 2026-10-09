"""Tests for the health probe (no data source required).

Rows *and* names come from the one KQL database now, so there is a single
source whose failure makes the dashboard unhealthy. Redis is reported next to
it but never gates it: the cache is what keeps a request from re-scanning
``agg_dom``, so losing it costs a query rather than an outage. These tests pin
both halves of that contract.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from app import service
from app.config import Settings
from app.errors import DataSourceError


@contextmanager
def _failing_conn(message: str):
    raise DataSourceError(message)
    yield  # pragma: no cover - the context never opens


@pytest.fixture
def settings() -> Settings:
    return Settings(kql_host="https://kql.example.invalid", kql_database="ctrader_dom",
                    kql_table="agg_dom", symbol_table="symbols_icmarkets",
                    redis_host="cache.example.invalid", cache_lookback_hours=4)


@pytest.fixture(autouse=True)
def _fresh_status(monkeypatch, settings):
    """Detach the probe from the module cache and from the real environment."""
    monkeypatch.setattr(service, "_status", service.Status())
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    yield


def _patch_kql(monkeypatch, *, rows: int = 100,
               latest: str | None = "2026-10-08T01:00:00Z", symbols: int = 348,
               error: str | None = None) -> list[str]:
    """Patch the KQL probe; return the list the connection lifecycle is logged in.

    `rows=0` / `symbols=0` replay an empty-but-readable table, which is the
    ingest-pipeline case rather than the endpoint-down case.
    """
    seen: list[str] = []

    @contextmanager
    def _connect(settings=None):
        seen.append("connect")
        yield object()

    if error:
        monkeypatch.setattr(service.kql, "connect",
                            lambda settings=None: _failing_conn(error))
    else:
        monkeypatch.setattr(service.kql, "connect", _connect)
        monkeypatch.setattr(service.kql, "server_time",
                            lambda client, settings=None: "2026-10-08T01:00:00Z")
        monkeypatch.setattr(service.kql, "tick_stats",
                            lambda client, settings=None: (rows, latest))
        monkeypatch.setattr(service.kql, "symbol_count",
                            lambda client, settings=None: symbols)
    return seen


def _patch_cache(monkeypatch, *, answering: bool = True) -> None:
    monkeypatch.setattr(service.cache, "ping", lambda settings=None: answering)


def test_a_healthy_probe_reports_connected(monkeypatch):
    seen = _patch_kql(monkeypatch)
    _patch_cache(monkeypatch)

    st = service.status(refresh=True)

    assert st.connected is True
    assert st.error is None
    assert st.hints == []
    assert st.row_count == 100
    assert st.dimension_rows == 348
    assert st.server_time is not None
    assert seen == ["connect"]


def test_the_probe_reads_the_configured_dimension(monkeypatch):
    """The dimension count comes from the configured table, not a hard-coded one."""
    seen: list[str] = []
    settings = Settings(symbol_table="symbols_icmarkets", kql_host="https://kql.example.invalid")

    @contextmanager
    def _connect(s=None):
        yield object()

    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(service.kql, "connect", _connect)
    monkeypatch.setattr(service.kql, "server_time", lambda c, s=None: "now")
    monkeypatch.setattr(service.kql, "tick_stats", lambda c, s=None: (1, None))
    monkeypatch.setattr(service.kql, "symbol_count",
                        lambda c, s=None: seen.append(s.symbol_table) or 5)
    _patch_cache(monkeypatch)

    assert service.status(refresh=True).dimension_rows == 5
    assert seen == ["symbols_icmarkets"]


def test_the_fixed_window_is_echoed_on_the_probe(monkeypatch):
    """The UI bounds its duration control to this, so it must be reported."""
    _patch_kql(monkeypatch)
    _patch_cache(monkeypatch)

    assert service.status(refresh=True).lookback_minutes == 240


def test_the_point_in_time_controls_are_echoed_on_the_probe(monkeypatch):
    """The timeline and the window choices are built from the API, so the
    probe has to publish them - hard-coding them client-side would let the UI
    offer history the cache cannot serve."""
    _patch_kql(monkeypatch)
    _patch_cache(monkeypatch)

    st = service.status(refresh=True)

    assert (st.timeline_minutes, st.timeline_step_minutes) == (240, 5)
    assert (st.window_min_minutes, st.window_max_minutes) == (5, 240)


def test_the_window_ceiling_follows_a_shorter_cache(monkeypatch):
    """`window_max_minutes` is capped by the cache, so a shorter cache never
    advertises a window it cannot fill."""
    monkeypatch.setattr(service, "get_settings",
                        lambda: Settings(kql_host="https://kql.example.invalid",
                                         cache_lookback_hours=1))
    _patch_kql(monkeypatch)
    _patch_cache(monkeypatch)

    st = service.status(refresh=True)

    assert st.window_minutes == 60
    assert st.window_max_minutes == 60


def test_a_failing_kql_endpoint_disconnects_the_dashboard(monkeypatch):
    _patch_kql(monkeypatch, error="KQL query failed: Forbidden (403-Forbidden)")
    _patch_cache(monkeypatch)

    st = service.status(refresh=True)

    assert st.connected is False
    assert "KQL" in st.hints[0]
    assert "403" in st.error
    assert st.row_count is None
    assert st.dimension_rows is None


def test_an_empty_metrics_table_hints_at_the_ingest_pipeline(monkeypatch):
    _patch_kql(monkeypatch, rows=0, latest=None)
    _patch_cache(monkeypatch)

    st = service.status(refresh=True)

    assert st.connected is True
    assert st.row_count == 0
    assert "empty" in st.hints[0]


def test_an_empty_dimension_hints_at_the_metadata_notebook(monkeypatch):
    _patch_kql(monkeypatch, symbols=0)
    _patch_cache(monkeypatch)

    st = service.status(refresh=True)

    assert st.connected is True
    assert st.dimension_rows == 0
    assert "dimension" in st.hints[0]


# --- The cache is reported but never gates health -----------------------

def test_a_reachable_cache_is_reported_and_silent(monkeypatch):
    _patch_kql(monkeypatch)
    _patch_cache(monkeypatch, answering=True)

    st = service.status(refresh=True)

    assert st.cache_connected is True
    assert st.cache_error is None


def test_a_cache_outage_is_a_warning_not_an_outage(monkeypatch):
    """Reads fall through to KQL, so the dashboard stays connected."""
    _patch_kql(monkeypatch)
    _patch_cache(monkeypatch, answering=False)

    st = service.status(refresh=True)

    assert st.connected is True
    assert st.cache_connected is False
    assert "Redis" in st.cache_error
    assert st.hints == [st.cache_error]


def test_a_cache_outage_never_masks_a_kql_failure(monkeypatch):
    _patch_kql(monkeypatch, error="KQL query failed: Forbidden (403-Forbidden)")
    _patch_cache(monkeypatch, answering=False)

    st = service.status(refresh=True)

    assert st.connected is False
    assert st.cache_connected is False
    assert len(st.hints) == 2
    assert "KQL" in st.hints[0]
    assert st.hints[1] == st.cache_error


def test_last_refresh_reports_the_cycle(monkeypatch):
    """Health publishes when the window was last re-read, so a stalled cycle shows."""
    stamp = {"finished_at": "2026-10-08T01:30:00+00:00",
             "started_at": "2026-10-08T00:30:00+00:00", "symbols": 2}
    monkeypatch.setattr(service, "_last_refresh", stamp)

    assert service.last_refresh() == stamp


def test_there_is_no_refresh_state_before_the_first_cycle(monkeypatch):
    monkeypatch.setattr(service, "_last_refresh", None)

    assert service.last_refresh() is None


def test_the_probe_is_cached_until_refreshed(monkeypatch):
    seen = _patch_kql(monkeypatch)
    _patch_cache(monkeypatch)

    service.status(refresh=True)
    service.status()      # cached
    assert len(seen) == 1

    service.status(refresh=True)
    assert len(seen) == 2
