"""Tests for the dashboard login gate (no data source required).

The gate is opt-in: ``LOGIN_PASSWORD`` turns it on and a blank password
leaves the site open, so a credential-less run and the rest of the suite are
unaffected. ``tests/conftest.py`` forces it off for every test; the ``gated``
fixture here turns it back on. Everything below either serves files or checks
the session - the one test that reaches a data endpoint stubs the handler's
own call - so none of it touches KQL or Redis.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth, config, main, service
from app.config import Settings
from app.main import app

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
PAGE = STATIC / "login.html"

USER = "gatekeeper"
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def gated(_login_disabled, monkeypatch):
    """Turn the gate on for one test, then put the environment back.

    Requests ``_login_disabled`` so the enable always lands *after* the
    suite-wide disable, whatever pytest's ordering would otherwise do.
    """
    monkeypatch.setenv("LOGIN_USERNAME", USER)
    monkeypatch.setenv("LOGIN_PASSWORD", PASSWORD)
    config.get_settings.cache_clear()
    yield
    monkeypatch.delenv("LOGIN_USERNAME", raising=False)
    config.get_settings.cache_clear()


@pytest.fixture
def maintenance(monkeypatch):
    """Turn maintenance mode on for one test, then put the settings back."""
    monkeypatch.setenv("MAINTENANCE_MODE", "true")
    config.get_settings.cache_clear()
    yield
    monkeypatch.delenv("MAINTENANCE_MODE", raising=False)
    config.get_settings.cache_clear()


@pytest.fixture
def client():
    return TestClient(app)


def _sign_in(client, username=USER, password=PASSWORD):
    """Sign in through the endpoint, keeping the cookie on the client."""
    r = client.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r


# ----------------------------------------------------------------------
# The page itself
# ----------------------------------------------------------------------

def test_page_exists_next_to_the_other_pages():
    assert PAGE.is_file()
    assert (STATIC / "index.html").is_file()
    assert (STATIC / "landing.html").is_file()


def test_page_carries_the_brand():
    html = PAGE.read_text(encoding="utf-8")
    assert "AXIOMA" in html
    assert "Tick microstructure" in html
    # The wordmark and the favicon must be the same mark, so the geometry is
    # checked against app/static/favicon.svg - exactly once, the header brand.
    mark = 'd="M20 3 L33 20 L20 37 L7 20 Z"'
    assert html.count(mark) == 1
    assert "#4f9dff" in html and "#22d3a7" in html   # the brand gradient
    assert 'href="/static/favicon.svg"' in html


def test_page_is_self_contained():
    """Like the explainer and the outage page: it must render even if the
    dashboard's own assets are unavailable. Only the favicons are external -
    plain files served by the static mount, not code that can fail to parse.
    """
    html = PAGE.read_text(encoding="utf-8")
    assert 'href="/static/styles.css"' not in html
    assert 'src="/static/app.js"' not in html
    assert "<style>" in html


def test_page_carries_the_form_and_posts_it():
    """The whole point of the page: the credentials go to /api/login."""
    html = PAGE.read_text(encoding="utf-8")
    assert 'id="loginForm"' in html
    assert 'id="user"' in html
    assert 'id="pass"' in html
    assert 'type="password"' in html
    assert 'autocomplete="current-password"' in html
    assert 'fetch("/api/login"' in html
    assert 'method: "POST"' in html
    assert 'id="loginErr"' in html       # where a wrong password is reported


# ----------------------------------------------------------------------
# The gate off: no LOGIN_PASSWORD
# ----------------------------------------------------------------------

def test_without_a_password_the_dashboard_stays_open(client):
    """The gate is opt-in, and this is the state a fresh checkout runs in."""
    r = client.get("/")
    assert r.status_code == 200
    assert 'src="/static/app.js"' in r.text


def test_without_a_password_the_data_api_stays_open(client, monkeypatch):
    monkeypatch.setattr(main.metrics, "report",
                        lambda *a, **k: {"summary": {}})
    r = client.get("/api/metrics")
    assert r.status_code == 200


def test_without_a_password_signing_in_is_refused(client):
    """There is nothing to sign in to - and no credential that would work."""
    r = client.post("/api/login", json={"username": USER, "password": PASSWORD})
    assert r.status_code == 403
    assert "disabled" in r.json()["detail"].lower()


def test_the_gate_is_driven_by_the_password_alone():
    assert Settings(login_password="s3cret").login_enabled is True
    assert Settings(login_password="").login_enabled is False


# ----------------------------------------------------------------------
# The gate on: pages bounce, the API answers 401
# ----------------------------------------------------------------------

def test_the_login_page_is_served_at_its_own_path(client, gated):
    r = client.get("/login")
    assert r.status_code == 200
    assert 'id="loginForm"' in r.text
    assert r.headers["cache-control"] == "no-cache, must-revalidate"


def test_the_dashboard_redirects_to_the_login_page(client, gated):
    """A bookmarked dashboard lands on the form, not on an empty page."""
    for path in ("/", "/dashboard"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 307, path
        assert r.headers["location"] == "/login"


def test_data_endpoints_answer_401_without_a_session(client, gated):
    """The API answers rather than redirects: a redirect would hand the
    dashboard's own fetch call an HTML body."""
    for path in ("/api/analytics", "/api/symbols", "/api/metrics"):
        r = client.get(path)
        assert r.status_code == 401, path
        assert r.json()["detail"] == "Not authenticated."


def test_a_forged_cookie_is_not_a_session(client, gated):
    client.cookies.set(auth.COOKIE_NAME, "not-a-real-token")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/login"


# ----------------------------------------------------------------------
# Signing in
# ----------------------------------------------------------------------

def test_a_wrong_password_is_rejected(client, gated):
    r = client.post("/api/login", json={"username": USER, "password": "wrong"})
    assert r.status_code == 401
    # The same answer whatever was wrong, and no half-open door behind it.
    assert r.json()["detail"] == "Invalid username or password."
    assert "set-cookie" not in {k.lower() for k in r.headers}


def test_a_wrong_username_is_answered_like_a_wrong_password(client, gated):
    """No user enumeration: name and password fail the same way."""
    r = client.post("/api/login", json={"username": "someone", "password": PASSWORD})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid username or password."


def test_blank_credentials_are_simply_wrong(client, gated):
    r = client.post("/api/login", json={"username": "", "password": ""})
    assert r.status_code == 401


def test_the_right_password_signs_in_with_an_httponly_cookie(client, gated):
    r = _sign_in(client)
    assert r.json() == {"ok": True}
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{auth.COOKIE_NAME}=")
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    assert auth.COOKIE_NAME in r.cookies


def test_a_signed_in_session_reaches_the_dashboard(client, gated):
    _sign_in(client)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert 'src="/static/app.js"' in r.text
    # And the alias too, so a bookmarked /dashboard keeps working.
    assert client.get("/dashboard").status_code == 200


def test_a_signed_in_session_reaches_the_data_api(client, gated, monkeypatch):
    """The gate is the only thing a 401 was standing between: past it, the
    endpoint runs as before (here with its one data call stubbed)."""
    monkeypatch.setattr(main.metrics, "report",
                        lambda *a, **k: {"summary": {}})
    assert client.get("/api/metrics").status_code == 401   # before: the gate
    _sign_in(client)
    assert client.get("/api/metrics").status_code == 200   # after: the handler


def test_an_authenticated_visitor_is_sent_off_the_login_page(client, gated):
    """The form only ever shows to someone who needs it."""
    _sign_in(client)
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/"


# ----------------------------------------------------------------------
# Signing out
# ----------------------------------------------------------------------

def test_signing_out_ends_the_session(client, gated):
    _sign_in(client)
    r = client.post("/api/logout")
    assert r.status_code == 200
    assert client.get("/", follow_redirects=False).status_code == 307


def test_signing_out_twice_is_not_an_error(client, gated):
    _sign_in(client)
    assert client.post("/api/logout").status_code == 200
    assert client.post("/api/logout").status_code == 200


# ----------------------------------------------------------------------
# What stays public
# ----------------------------------------------------------------------

def test_health_stays_public_and_reports_the_gate(client, gated, monkeypatch):
    """The probe carries no rows and is what uptime checks poll, so it is
    left outside the gate; it publishes `login_enabled`, which is what the
    dashboard shows the Sign-out control from."""
    monkeypatch.setattr(main.service, "status",
                        lambda refresh=False: service.Status())
    monkeypatch.setattr(main.service, "last_refresh", lambda: None)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["login_enabled"] is True


def test_health_reports_the_gate_off(client, monkeypatch):
    monkeypatch.setattr(main.service, "status",
                        lambda refresh=False: service.Status())
    monkeypatch.setattr(main.service, "last_refresh", lambda: None)
    assert client.get("/api/health").json()["login_enabled"] is False


def test_the_explainer_the_outage_page_and_the_docs_stay_public(client, gated):
    """Static pages and the API schema never touch the warehouse, and the
    outage page must be reachable while the dashboard behind it is closed."""
    assert client.get("/welcome").status_code == 200
    assert client.get("/unavailable").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_the_static_assets_the_login_page_needs_stay_public(client, gated):
    assert client.get("/static/favicon.svg").status_code == 200
    assert client.get("/static/login.html").status_code == 200


def test_the_outage_page_holds_the_root_even_while_gated(client, gated, maintenance):
    """A planned outage is told to everyone the same way; the gate is for
    the dashboard, not for the notice that it is down."""
    r = client.get("/")
    assert r.status_code == 503
    assert "Temporarily unavailable" in r.text


# ----------------------------------------------------------------------
# Sessions themselves, and the controls around them
# ----------------------------------------------------------------------

def test_an_expired_session_is_rejected_and_forgotten(gated):
    token = auth.start_session()
    assert auth.session_valid(token) is True
    auth._sessions[token] = time.time() - 1        # wind the clock back
    assert auth.session_valid(token) is False
    assert token not in auth._sessions             # pruned, not just ignored


def test_ending_a_session_makes_it_unusable(gated):
    token = auth.start_session()
    auth.end_session(token)
    assert auth.session_valid(token) is False


def test_no_session_is_never_a_valid_one():
    assert auth.session_valid(None) is False
    assert auth.session_valid("") is False


def test_the_sign_out_control_is_offered_only_when_the_gate_is_on():
    """The button lives in the dashboard header, starts hidden, and follows
    the `login_enabled` flag /api/health publishes."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'id="logout"' in html
    assert 'class="ghost sm hidden"' in html
    assert "h.login_enabled" in js                 # the toggle in loadHealth
    assert 'fetch("/api/logout"' in js             # what the button does
    assert 'location.href = "/login"' in js        # and where it lands


def test_the_dashboard_script_bounces_a_401_to_the_login_page():
    """A session that expires mid-use should cost one redirect, not a page
    of failed panels."""
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "r.status === 401" in js
    assert 'location.href = "/login"' in js


