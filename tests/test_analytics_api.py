"""Tests for `/api/analytics` as the dashboard calls it: a point in time plus a
window, over the cached window.

No data source required: the tick read is faked with a frame of known rows, so
what is under test is the endpoint's contract - `as_of` reaches the read, it is
echoed back in `meta` so a report can be tied to the moment it describes, and a
point with nothing behind it is reported rather than silently answered with the
newest rows instead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import config, main
from app.errors import DataSourceError
from app.frames import from_ticks
from app.main import app

WINDOW_START = "2026-10-08T00:00:00"
WINDOW_END = "2026-10-08T03:59:00"      # 240 rows, one per minute


def _rows(periods: int = 240) -> pd.DataFrame:
    """One row per minute, oldest first - the shape the cached window holds."""
    ts = pd.date_range(WINDOW_START, periods=periods, freq="1min", tz="UTC")
    n = len(ts)
    mid = 1.0850 + np.linspace(0, 1e-3, n)
    return pd.DataFrame({
        "timestamp": ts,
        "symbolId": "1",
        "total_bid": np.full(n, 100.0),
        "total_ask": np.full(n, 120.0),
        "best_bid": mid - 1e-5,
        "best_ask": mid + 1e-5,
        "imbalance": np.zeros(n),
        "imbalance_ratio": np.zeros(n),
        "vwap_bid": mid - 1e-5,
        "vwap_ask": mid + 1e-5,
        "vwap_spread": np.full(n, 2e-5),
        "rel_spread": np.full(n, 1e-5),
        "rel_vwap_spread": np.full(n, 1e-5),
    })


@pytest.fixture
def client(monkeypatch):
    """A client whose tick read is a fixed window, recording the request.

    `main.service` is patched rather than the cache: the endpoint is the thing
    under test, and the clamp inside `load_ticks_cached` is covered in
    `test_service.py`.
    """
    monkeypatch.setenv("MAINTENANCE_MODE", "false")
    config.get_settings.cache_clear()
    seen: list[dict] = []

    def fake_load(symbol=None, limit=None, window_minutes=None, end_time=None):
        seen.append({"window_minutes": window_minutes, "end_time": end_time})
        frame = from_ticks(_rows())
        if end_time is not None:
            end = pd.Timestamp(end_time)
            frame = frame.loc[frame["ts"] <= end]
            if frame.empty:
                raise DataSourceError(
                    f"No usable aggregate rows for {symbol or 'any instrument'} "
                    f"ending at {end_time} within the last {window_minutes} minute(s)."
                )
        return frame

    monkeypatch.setattr(main.service, "load_ticks_cached", fake_load)
    yield TestClient(app), seen
    config.get_settings.cache_clear()


# ----------------------------------------------------------------------
# What the UI builds its controls from
# ----------------------------------------------------------------------

def test_health_publishes_the_point_in_time_controls(client):
    """The timeline and the window choices come from the API, so a shorter
    cache window or a different grid reaches the UI without a code change."""
    r = client[0].get("/api/health")
    body = r.json()

    assert r.status_code == 200
    # Four hours of timeline plus the widest (240 min) window = 8 hours.
    assert body["lookback_minutes"] == 480
    assert (body["timeline_minutes"], body["timeline_step_minutes"]) == (240, 5)
    assert (body["window_min_minutes"], body["window_max_minutes"]) == (5, 240)
    assert body["timeline_minutes"] + body["window_max_minutes"] == body["lookback_minutes"]


# ----------------------------------------------------------------------
# The point in time
# ----------------------------------------------------------------------

def test_the_chosen_point_reaches_the_read(client):
    test_client, seen = client

    r = test_client.get("/api/analytics", params={
        "symbol": "1", "as_of": "2026-10-08T02:59:00Z", "window_minutes": 60,
    })

    assert r.status_code == 200
    assert seen[-1]["end_time"] == "2026-10-08T02:59:00Z"
    assert seen[-1]["window_minutes"] == 60
    assert r.json()["meta"]["window_minutes"] == 60
    assert r.json()["meta"]["as_of"] == "2026-10-08T02:59:00Z"
    # The report really ends at the chosen point, not at the newest row.
    assert r.json()["meta"]["latest_tick"].startswith("2026-10-08T02:59:00")


def test_the_deprecated_lookback_alias_still_selects_a_window(client):
    """A bookmarked `lookback_minutes` URL keeps its meaning after the rename."""
    test_client, seen = client

    r = test_client.get("/api/analytics", params={
        "symbol": "1", "lookback_minutes": 120,
    })

    assert r.status_code == 200
    assert seen[-1]["window_minutes"] == 120
    assert r.json()["meta"]["window_minutes"] == 120


def test_window_minutes_wins_over_the_alias(client):
    test_client, seen = client

    test_client.get("/api/analytics", params={
        "symbol": "1", "window_minutes": 30, "lookback_minutes": 120,
    })

    assert seen[-1]["window_minutes"] == 30


def test_omitting_the_point_analyses_the_newest_rows(client):
    test_client, seen = client

    r = test_client.get("/api/analytics", params={"symbol": "1", "window_minutes": 60})

    assert r.status_code == 200
    assert seen[-1]["end_time"] is None
    assert r.json()["meta"]["as_of"] is None
    assert r.json()["meta"]["latest_tick"].startswith(WINDOW_END)


def test_a_point_with_nothing_behind_it_is_reported(client):
    """A 502 naming the point, not a report built from rows the user did not
    ask for."""
    test_client, _ = client

    r = test_client.get("/api/analytics", params={
        "symbol": "1", "as_of": "2026-10-07T23:00:00Z", "window_minutes": 60,
    })

    assert r.status_code == 502
    assert "ending at 2026-10-07T23:00:00Z" in r.json()["detail"]
