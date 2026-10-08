"""Tests for the selector catalogue and the endpoints that back it.

No data source required: `instruments` is seeded directly so these cover the
two rules the pickers depend on - unclassified instruments are excluded, and
the asset-class picker keeps every class while a filter is active.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import service
from app.main import app


def _inst(sid, name, asset_class, ticks):
    return service.Instrument(symbol_id=sid, name=name, description="",
                              asset_class=asset_class, ticks=ticks)


@pytest.fixture
def catalogue(monkeypatch):
    """A seeded catalogue mixing classified and unclassifiable instruments."""
    rows = {
        "1": _inst("1", "EURUSD", "fx_major", 500),
        "2": _inst("2", "XAUUSD", "metal", 400),
        "3": _inst("3", "BTCUSD", "crypto", 300),
        # No row in symbols_icmarkets: traded, but nothing to classify it by.
        "99": _inst("99", "99", "", 900),
        # Present in the dimension but never traded.
        "4": _inst("4", "GBPUSD", "fx_major", 0),
    }
    monkeypatch.setattr(service, "_instruments", rows, raising=False)
    # This module is data-source-free by design, so pin the health probe:
    # without it /api/health would borrow the coverage counters from a live
    # endpoint and the reconciliation assertions would depend on connectivity.
    monkeypatch.setattr(service, "status",
                        lambda **_: service.Status(connected=True), raising=False)
    return rows


@pytest.fixture
def client(catalogue):
    return TestClient(app)


# ----------------------------------------------------------------------
# Unclassified instruments are excluded from the selectors
# ----------------------------------------------------------------------

def test_unclassified_instruments_are_not_offered(catalogue):
    listed = service.symbol_list(only_traded=True)
    assert "99" not in [s["symbol"] for s in listed]


def test_selector_total_matches_classified_traded_instruments(client):
    """`total` counts what the user can pick: classified and traded only."""
    body = client.get("/api/symbols").json()
    assert body["total"] == 3  # EURUSD, XAUUSD, BTCUSD
    assert all(s["asset_class"] for s in body["symbols"])


def test_symbol_payload_has_no_unclassified_entries(client):
    body = client.get("/api/symbols").json()
    assert not [s for s in body["symbols"] if not s["asset_class"]]
    assert "Unclassified" not in [s["asset_class_label"] for s in body["symbols"]]


def test_idle_instruments_still_need_a_class(client):
    """include_idle adds untraded instruments, but never unclassifiable ones."""
    body = client.get("/api/symbols?include_idle=true").json()
    assert body["total"] == 4  # + GBPUSD, which has ticks==0 but a class
    assert "99" not in [s["symbol"] for s in body["symbols"]]


# ----------------------------------------------------------------------
# The asset-class picker must not collapse when a class is selected
# ----------------------------------------------------------------------

def test_class_summary_is_the_full_rollup_when_unfiltered(client):
    body = client.get("/api/symbols").json()
    assert {c["key"] for c in body["summary"]} == {"fx_major", "metal", "crypto"}


def test_class_picker_still_lists_every_class_while_filtered(client):
    """Regression: the picker used to be rebuilt from the filtered groups, so
    selecting one asset class left it as the only option - unrecoverable
    without first choosing 'All asset classes'."""
    unfiltered = client.get("/api/symbols").json()["summary"]
    expected = {c["key"] for c in unfiltered}
    for key in expected:
        body = client.get(f"/api/symbols?asset_class={key}").json()
        assert {c["key"] for c in body["summary"]} == expected, \
            f"selecting {key} shrank the asset-class options"


def test_filtered_groups_are_still_narrowed(client):
    """The picker stays complete while the symbol list does narrow."""
    body = client.get("/api/symbols?asset_class=crypto").json()
    assert body["total"] == 1
    assert [s["symbol"] for s in body["symbols"]] == ["3"]
    assert len(body["groups"]) == 1


def test_unknown_asset_class_is_rejected(client):
    assert client.get("/api/symbols?asset_class=nope").status_code == 400


# ----------------------------------------------------------------------
# Coverage reporting stays truthful
# ----------------------------------------------------------------------

def test_traded_count_includes_unclassifiable_instruments(catalogue):
    """`traded_count` reports the whole feed, not just what the pickers show."""
    assert service.traded_count() == 4
    assert service.unclassified_count() == 1


def test_health_counters_reconcile(client):
    h = client.get("/api/health").json()
    assert h["classified_count"] + h["unclassified_count"] == h["symbol_count"]
    # The unclassifiable instruments are hidden, but still reported.
    assert h["unclassified_count"] == 1


def test_health_publishes_the_refresh_cycle(client, catalogue, monkeypatch):
    """The cycle's state is read live, not taken from the cached probe."""
    monkeypatch.setattr(service, "_last_refresh", {
        "finished_at": "2026-10-08T01:30:00+00:00", "symbols": 3,
    })

    h = client.get("/api/health").json()

    assert h["cache_refresh_minutes"] == 30
    assert h["cache_refreshed_at"] == "2026-10-08T01:30:00+00:00"
    assert h["cached_symbols"] == 3


def test_health_is_silent_about_the_cycle_before_it_runs(client, catalogue, monkeypatch):
    monkeypatch.setattr(service, "_last_refresh", None)

    h = client.get("/api/health").json()

    assert h["cache_refreshed_at"] is None
    assert h["cached_symbols"] is None


def test_coverage_note_is_no_longer_surfaced(client):
    """The hidden-instrument warning was removed from the health hints."""
    hints = client.get("/api/health").json()["hints"]
    assert not any("symbols_icmarkets" in hint for hint in hints)