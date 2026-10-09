"""Tests for the branded temporary-unavailable page and how it is served.

No data source required: the page is a static asset and the routes that serve
it never touch the warehouse.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
PAGE = STATIC / "maintenance.html"


@pytest.fixture
def client(monkeypatch):
    """Test client with maintenance mode forced off (the real .env may set it)."""
    monkeypatch.setenv("MAINTENANCE_MODE", "false")
    config.get_settings.cache_clear()
    yield TestClient(app)
    config.get_settings.cache_clear()


@pytest.fixture
def maintenance(monkeypatch):
    """Turn MAINTENANCE_MODE on for one test, then put the settings back."""
    monkeypatch.setenv("MAINTENANCE_MODE", "true")
    config.get_settings.cache_clear()
    yield
    monkeypatch.delenv("MAINTENANCE_MODE", raising=False)
    config.get_settings.cache_clear()


# ----------------------------------------------------------------------
# The page itself
# ----------------------------------------------------------------------

def test_page_exists_next_to_the_dashboard():
    assert PAGE.is_file()
    assert (STATIC / "index.html").is_file()


def test_page_carries_the_brand():
    html = PAGE.read_text(encoding="utf-8")
    assert "AXIOMA" in html
    assert "Tick microstructure" in html
    # The wordmark and the favicon must be the same mark, so both are checked
    # against the geometry in app/static/favicon.svg.
    mark = 'd="M20 3 L33 20 L20 37 L7 20 Z"'
    assert html.count(mark) == 2          # header brand + centred artwork
    assert '#4f9dff' in html and '#22d3a7' in html   # the brand gradient
    assert 'href="/static/favicon.svg"' in html


def test_page_states_the_condition():
    html = PAGE.read_text(encoding="utf-8")
    assert "Temporarily unavailable" in html
    assert "role=\"alert\"" in html
    assert "Try again now" in html


def test_page_is_self_contained():
    """It must render while the app is unhealthy, so it loads no shared assets.

    Only the favicons and the logo are external - both are plain files served
    by the static mount, not code that can fail to parse.
    """
    html = PAGE.read_text(encoding="utf-8")
    # Match the reference itself, not the prose comment explaining it.
    assert 'href="/static/styles.css"' not in html
    assert 'src="/static/app.js"' not in html
    assert "<style>" in html


# ----------------------------------------------------------------------
# How it is served
# ----------------------------------------------------------------------

def test_page_is_always_reachable_for_preview(client):
    r = client.get("/unavailable")
    assert r.status_code == 200
    assert "Temporarily unavailable" in r.text
    assert r.headers["cache-control"] == "no-cache, must-revalidate"


def test_root_serves_the_dashboard_by_default(client):
    """`/` is the dashboard; the outage page is maintenance-only."""
    r = client.get("/")
    assert r.status_code == 200
    assert "Temporarily unavailable" not in r.text
    assert "Under development" not in r.text
    assert 'id="banner"' in r.text


def test_root_serves_the_page_with_503_in_maintenance(client, maintenance):
    """A temporary outage is a 503, so proxies and checks do not read it as OK."""
    r = client.get("/")
    assert r.status_code == 503
    assert "Temporarily unavailable" in r.text
    assert "AXIOMA" in r.text


def test_health_stays_up_during_maintenance(client, maintenance):
    """The API is not part of the maintenance window: the page depends on it."""
    assert client.get("/api/health").status_code == 200
