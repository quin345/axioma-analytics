"""Tests for the "under development" page that replaces the main page.

While the app is being built, `/` serves a self-contained development notice
(unavailable.html, gear artwork) instead of the dashboard. The dashboard
itself is retained at `/dashboard`, and the branded outage page
(maintenance.html) is retained for future maintenance windows - a planned
build and a real outage are different messages. No data source required:
these routes serve files and never touch the warehouse.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
PAGE = STATIC / "unavailable.html"


@pytest.fixture
def client(monkeypatch):
    """Test client with maintenance mode forced off (the real .env may set it)."""
    monkeypatch.setenv("MAINTENANCE_MODE", "false")
    config.get_settings.cache_clear()
    yield TestClient(app)
    config.get_settings.cache_clear()


# ----------------------------------------------------------------------
# The page itself
# ----------------------------------------------------------------------

def test_page_exists_next_to_the_retained_pages():
    assert PAGE.is_file()
    assert (STATIC / "index.html").is_file()          # the dashboard, retained
    assert (STATIC / "maintenance.html").is_file()    # the outage page, retained


def test_page_carries_the_brand():
    html = PAGE.read_text(encoding="utf-8")
    assert "AXIOMA" in html
    assert "Tick microstructure" in html
    mark = 'd="M20 3 L33 20 L20 37 L7 20 Z"'
    assert html.count(mark) == 1          # the header brand; the card is gears
    assert 'href="/static/favicon.svg"' in html


def test_page_states_the_condition():
    html = PAGE.read_text(encoding="utf-8")
    assert "Under development" in html
    assert "under development" in html
    assert 'role="status"' in html


def test_page_shows_the_gear_artwork():
    """The notice is illustrated with gears, not the dashboard's mark."""
    html = PAGE.read_text(encoding="utf-8")
    assert 'aria-label="Gears turning"' in html
    # Two gears: each is a body, a hub and a ring of teeth.
    assert html.count('class="gear gear-') == 2
    assert html.count("<rect") >= 14
    assert "@keyframes spin" in html


def test_page_is_self_contained():
    """It is the main page now, so it must render without the dashboard's assets.

    Only the favicons are external - plain files served by the static mount,
    not code that can fail to parse.
    """
    html = PAGE.read_text(encoding="utf-8")
    assert 'href="/static/styles.css"' not in html
    assert 'src="/static/app.js"' not in html
    assert "<style>" in html


# ----------------------------------------------------------------------
# How it is served
# ----------------------------------------------------------------------

def test_root_serves_the_development_notice(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Under development" in r.text
    assert "building something new" in r.text
    assert r.headers["cache-control"] == "no-cache, must-revalidate"


def test_the_dashboard_is_retained_at_its_own_path(client):
    """`/` is the notice; the dashboard itself still answers at /dashboard."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert 'src="/static/app.js"' in r.text
    assert 'id="banner"' in r.text
    assert "Under development" not in r.text


def test_the_outage_page_is_retained_for_future_use(client):
    r = client.get("/unavailable")
    assert r.status_code == 200
    assert "Temporarily unavailable" in r.text