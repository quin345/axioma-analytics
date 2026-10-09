"""Tests for the "under development" page that replaces the main page.

While the app is being built, `/` serves a self-contained development notice
(unavailable.html, gear artwork) instead of the dashboard. The dashboard is
only on `dev` - no route and no static file on this branch - and the branded
outage page (maintenance.html) is served from `/` with a 503 while
MAINTENANCE_MODE is on. Neither state page is reachable as a raw file: only
`/` picks the one that matches the server's real state. No data source
required: these routes serve files and never touch the warehouse.
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


def test_page_offers_clickable_service_cards():
    """The notice doubles as the landing page: cards explain the service.

    Each card is a real link into the explainer, so a visitor arriving
    during the build still gets the information about the analytics
    service without needing the dashboard or the API docs.
    """
    html = PAGE.read_text(encoding="utf-8")
    assert 'class="cards"' in html
    assert html.count('class="info"') == 2
    assert 'href="/welcome#measures"' in html
    assert 'href="/welcome#h-data"' in html


def test_page_promotes_the_service():
    """The cards carry the product pitch, not just navigation."""
    html = PAGE.read_text(encoding="utf-8")
    assert "Microstructure analytics" in html
    assert "Production data, kept fresh" in html


def test_page_hides_the_docs_and_the_dashboard():
    """On main the page offers only service information - no links to the
    API docs or the dashboard (those stay on the dev branch)."""
    html = PAGE.read_text(encoding="utf-8")
    assert 'href="/docs"' not in html
    assert 'href="/dashboard"' not in html
    assert "API documentation" not in html
    assert "Open the dashboard" not in html
    assert "Open JSON API" not in html


def test_page_has_no_check_again_option():
    """The manual re-check was replaced by clickable routes off the page."""
    html = PAGE.read_text(encoding="utf-8")
    assert "Check again" not in html
    assert 'id="retry"' not in html
    assert 'id="checked"' not in html
    assert "<script>" not in html


# ----------------------------------------------------------------------
# How it is served
# ----------------------------------------------------------------------

def test_root_serves_the_development_notice(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Under development" in r.text
    assert "building something new" in r.text
    assert r.headers["cache-control"] == "no-cache, must-revalidate"


def test_the_dashboard_is_only_on_dev(client):
    """No route and no static file for the dashboard on this branch."""
    assert client.get("/dashboard").status_code == 404
    for asset in ("/static/index.html", "/static/app.js", "/static/styles.css"):
        assert client.get(asset).status_code == 404


def test_the_state_pages_are_not_reachable_as_files(client):
    """Both state pages answer from `/` only, never as raw static files.

    Opened directly either one claims a condition that may not hold (build in
    progress, or an outage while the site is healthy), so the mount refuses
    them and `/` stays the single place that picks the right page.
    """
    assert client.get("/unavailable").status_code == 404
    assert client.get("/static/unavailable.html").status_code == 404
    assert client.get("/static/maintenance.html").status_code == 404


def test_the_shared_static_assets_still_serve(client):
    """The block must not take the assets the served pages need with it."""
    assert client.get("/static/favicon.svg").status_code == 200
    assert client.get("/static/landing.html").status_code == 200