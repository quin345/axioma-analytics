"""Tests for `/api/metrics`: the precomputed grid, served as a lookup.

No data source required: the metrics read is faked with a grid of known stops,
so what is under test is the endpoint's contract - the parameters reach the
lookup, the payload carries all four windows so switching is free, a window the
table does not ship is reported as snapped rather than silently widened, and an
empty table answers 502 with a message that names it.
"""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import config, kql, main, metrics
from app.config import Settings
from app.errors import DataSourceError
from app.main import app


def _grid(periods: int = 5) -> pd.DataFrame:
    """A `metrics_5m` grid: `periods` 5-minute stops, every window present."""
    rows = []
    for i, at in enumerate(pd.date_range("2026-10-10T09:00", periods=periods,
                                         freq="5min", tz="UTC")):
        row = {"symbolId": "1", "as_of": at.to_pydatetime()}
        for suffix, _ in kql.WINDOW_SUFFIXES:
            row[f"ticks_{suffix}"] = 100 + i
            row[f"open_{suffix}"] = 1.0850
            row[f"close_{suffix}"] = 1.0855
            row[f"high_{suffix}"] = 1.0860
            row[f"low_{suffix}"] = 1.0845
            row[f"vwap_{suffix}"] = 1.0852
            row[f"change_pct_{suffix}"] = 0.046
            row[f"total_volume_{suffix}"] = 1234.5
            row[f"realized_vol_bps_{suffix}"] = 12.5
            row[f"avg_spread_bps_{suffix}"] = 1.5
            row[f"ofi_imbalance_{suffix}"] = 0.25
            row[f"vap_{suffix}"] = [
                {"price": 1.0850 + n * 0.001, "volume": 10.0 - n, "ticks": 3}
                for n in range(5)
            ]
            row[f"poc_{suffix}"] = 1.0850
        rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture
def client(monkeypatch):
    """A client whose metrics read is a fixed grid, recording the request.

    `main.metrics` is patched rather than the cache: the endpoint is the thing
    under test, and the cache-through read is covered in `test_metrics.py`.
    """
    monkeypatch.setenv("MAINTENANCE_MODE", "false")
    monkeypatch.setenv("METRICS_SOURCE", "metrics_5m")
    config.get_settings.cache_clear()
    seen: list[dict] = []
    # Captured before the patch: `main.metrics` *is* this module, so patching
    # `report` on it would otherwise leave the fake calling itself.
    real_report = metrics.report

    def fake_report(symbol=None, *, window_minutes=None, as_of=None, settings=None):
        seen.append({"symbol": symbol, "window_minutes": window_minutes,
                     "as_of": as_of})
        return real_report(symbol, window_minutes=window_minutes, as_of=as_of,
                           settings=Settings(kql_host="https://kql.example.invalid",
                                             metrics_table="metrics_5m"))

    monkeypatch.setattr(main.metrics, "report", fake_report)
    monkeypatch.setattr(main.metrics, "load",
                        lambda symbol=None, *, settings=None: _grid())
    yield TestClient(app), seen
    config.get_settings.cache_clear()


def test_the_parameters_reach_the_lookup(client):
    test_client, seen = client

    r = test_client.get("/api/metrics", params={
        "symbol": "1", "window_minutes": 240, "as_of": "2026-10-10T09:10:00Z"})

    assert r.status_code == 200
    assert seen[-1] == {"symbol": "1", "window_minutes": 240,
                        "as_of": "2026-10-10T09:10:00Z"}


def test_every_window_comes_back_so_switching_is_free(client):
    """Four column sets on one row: the UI must not need a request to change
    window, exactly as it does not need one to change point in time."""
    test_client, _ = client

    body = test_client.get("/api/metrics", params={"symbol": "1"}).json()

    assert sorted(body["windows"]) == ["120", "180", "240", "60"]
    assert body["window_choices_minutes"] == [60, 120, 180, 240]
    assert body["summary"] == body["windows"]["60"]
    assert all(body["windows"][w]["ticks"] for w in body["windows"])


def test_the_payload_carries_the_ladder_without_a_second_call(client):
    """The nested `vap_*` is already in the blob, so the volume-profile panel
    needs no KQL call and no re-binning."""
    test_client, _ = client

    body = test_client.get("/api/metrics",
                           params={"symbol": "1", "window_minutes": 120}).json()

    profile = body["volume_profiles"]["120"]
    assert profile["bins"]
    assert profile["poc"] is not None
    assert profile["value_area"] is not None


def test_a_window_the_table_does_not_ship_is_reported_as_snapped(client):
    """90 minutes is answered with the 60-minute window, and the payload says so:
    the control must never show a span the user was not given."""
    test_client, _ = client

    body = test_client.get("/api/metrics",
                           params={"symbol": "1", "window_minutes": 90}).json()

    assert body["requested_window_minutes"] == 90
    assert body["selected_window_minutes"] == 60


def test_the_report_is_anchored_on_a_stop_and_names_its_source(client):
    test_client, _ = client

    body = test_client.get("/api/metrics", params={"symbol": "1"}).json()

    assert body["meta"]["source"] == "metrics_5m"
    assert body["meta"]["as_of"] == "2026-10-10T09:20:00+00:00"
    assert len(body["stops"]) == 5


def test_an_empty_table_is_a_502_naming_the_table(client, monkeypatch):
    """The metrics job having not run is not a feed outage, so the message says
    which one it is rather than returning an empty panel."""
    monkeypatch.setattr(main.metrics, "load",
                        lambda symbol=None, *, settings=None: pd.DataFrame())

    r = client[0].get("/api/metrics", params={"symbol": "1"})

    assert r.status_code == 502
    assert "metrics_5m" in r.json()["detail"]


def test_a_failure_reading_the_grid_is_a_502_not_a_500(client, monkeypatch):
    def boom(symbol=None, *, window_minutes=None, as_of=None, settings=None):
        raise DataSourceError("KQL query failed: Forbidden (403-Forbidden)")

    monkeypatch.setattr(main.metrics, "report", boom)

    r = client[0].get("/api/metrics", params={"symbol": "1"})

    assert r.status_code == 502
    assert "403-Forbidden" in r.json()["detail"]


def test_health_reports_the_metrics_table_the_headline_metrics_come_from(client):
    """A stalled metrics job has to be visible in health rather than showing up
    as a panel of nulls."""
    body = client[0].get("/api/health").json()

    assert body["metrics_5m"] is True
    assert body["metrics_table"] == "metrics_5m"

