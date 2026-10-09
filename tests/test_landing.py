"""Tests for the front-facing explainer page and how it is served.

No data source required: the page is a static asset and the route that serves
it never touches the warehouse. It is the page behind www.axiomanalytics.info,
so it must stand on its own and always point visitors into the dashboard.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
PAGE = STATIC / "landing.html"
DASHBOARD = "https://app.axiomanalytics.info/"


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
    assert html.count(mark) == 1          # header brand
    assert '#4f9dff' in html and '#22d3a7' in html   # the brand gradient
    assert 'href="/static/favicon.svg"' in html


def test_page_gives_access_to_the_main_page():
    """The whole point of the front page: a way into the dashboard."""
    html = PAGE.read_text(encoding="utf-8")
    assert html.count(DASHBOARD) >= 2      # header, hero and footer links
    assert "Open the dashboard" in html


def test_page_explains_what_the_app_measures():
    html = PAGE.read_text(encoding="utf-8")
    for topic in (
        "What it measures",
        "order-flow imbalance",
        "volume-at-price",
        "drawdown",
        "autocorrelation",
        "resampled bars",
        "asset class",
    ):
        assert topic in html, f"the explainer never mentions {topic!r}"


def test_page_is_self_contained():
    """It must render even if the dashboard's own assets are unavailable.

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

def test_welcome_route_serves_the_page(client):
    r = client.get("/welcome")
    assert r.status_code == 200
    assert "Tick microstructure" in r.text
    assert r.headers["cache-control"] == "no-cache, must-revalidate"


def test_welcome_stays_up_during_maintenance(client, maintenance):
    """The explainer is not part of the maintenance window: it introduces the
    app even while the dashboard itself is showing the holding page."""
    r = client.get("/welcome")
    assert r.status_code == 200
    assert "What it measures" in r.text


def test_root_serves_the_dashboard_not_the_explainer(client):
    """The explainer lives at its own path; `/` is the dashboard."""
    r = client.get("/")
    assert r.status_code == 200
    assert "What it measures" not in r.text
    assert 'src="/static/app.js"' in r.text
    assert 'id="banner"' in r.text
    assert "Under development" not in r.text
