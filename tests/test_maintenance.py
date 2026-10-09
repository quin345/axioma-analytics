"""Tests for the "under development" page that replaces the main page.

While the app is being built, `/` serves a self-contained development notice
(unavailable.html, gear artwork) instead of the dashboard. The dashboard
itself is retained at `/dashboard`, and the branded outage page
(maintenance.html) is retained for future maintenance windows - a planned
build and a real outage are different messages. No data source required:
these routes serve files and never touch the warehouse.
"""
from __future__ import annotations

import re
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

    Each card is a real link, so a visitor arriving during the build can
    still reach the explainer, the data provenance and the API docs.
    """
    html = PAGE.read_text(encoding="utf-8")
    assert 'class="cards"' in html
    assert html.count('class="info"') == 3
    assert 'href="/welcome#measures"' in html
    assert 'href="/welcome#h-data"' in html
    assert 'href="/docs"' in html


def test_page_promotes_the_service():
    """The cards carry the product pitch, not just navigation."""
    html = PAGE.read_text(encoding="utf-8")
    assert "Microstructure analytics" in html
    assert "Production data, kept fresh" in html
    assert "Open JSON API" in html
    assert "/api/analytics" in html


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


# ----------------------------------------------------------------------
# The point-in-time controls
# ----------------------------------------------------------------------

def test_the_dashboard_replaces_the_duration_picker(client):
    """A four-hour point-in-time field plus an aggregate window, in place of
    the old duration picker."""
    r = client.get("/dashboard")

    assert r.status_code == 200
    # The point in time: a time field on the 5-minute grid, with its window.
    assert 'id="asOf"' in r.text and 'type="time"' in r.text
    assert 'step="300"' in r.text
    assert 'id="asOfOut"' in r.text
    assert 'id="asOfRange"' in r.text
    # The aggregate window, filled from the API.
    assert 'id="windowMinutes"' in r.text
    assert 'id="duration"' not in r.text
    assert 'id="lookback"' not in r.text
    assert 'type="range"' not in r.text


def test_the_header_says_last_update_not_latest_tick(client):
    """The badge beside "Connected" is about the feed, not the loaded window,
    so it is scoped to the header: the freshness strip below keeps its own
    "Latest tick" label, which describes the window on screen."""
    page = client.get("/dashboard").text
    header = page.partition('<div id="banner"')[0]

    assert 'id="lastTick"' in header and "Last update" in header
    assert "Latest tick" not in header
    # The alignment is stated in the tooltip, so it is not a surprise.
    assert "30-minute refresh step" in header
    # The cadence strip leads with the 30-minute grid, not 45.
    assert "Data refreshes every 30 minutes" in page
    assert "Data refreshes every 45 minutes" not in page


def test_every_control_the_script_reads_exists_in_the_page():
    """index.html and app.js are two separately cached files; a newer script
    against an older page leaves it reading controls that are not there. Both
    sides of every binding are pinned here."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    referenced = {m.group(1) for m in re.finditer(r'(?:\$|val|on)\("([^"]+)"', js)}
    assert referenced, "no element references found in app.js"
    missing = [i for i in referenced if f'id="{i}"' not in html]
    assert missing == [], f"app.js reads controls index.html does not define: {missing}"


# ----------------------------------------------------------------------
# The dashboard script
# ----------------------------------------------------------------------

#: JS globals and language keywords a page script may call without defining
#: them itself. Everything else app.js invokes must be declared in app.js.
_JS_GLOBALS = {
    "AbortController", "Array", "BigInt", "Boolean", "Chart", "Date", "Error",
    "Image", "Infinity", "Intl", "JSON", "Map", "Math", "NaN", "Number", "Object",
    "Promise", "Proxy", "RegExp", "ResizeObserver", "IntersectionObserver", "Set",
    "String", "Symbol", "URL", "URLSearchParams", "WeakMap", "WeakSet", "alert",
    "atob", "btoa", "cancelAnimationFrame", "clearInterval", "clearTimeout",
    "console", "crypto", "decodeURIComponent", "document", "encodeURIComponent",
    "fetch", "getComputedStyle", "history", "isFinite", "isNaN", "location",
    "matchMedia", "navigator", "parseFloat", "parseInt", "performance", "prompt",
    "queueMicrotask", "requestAnimationFrame", "setInterval", "setTimeout",
    "structuredClone", "window",
}
_JS_KEYWORDS = {
    "async", "await", "case", "catch", "class", "const", "delete", "do", "else",
    "for", "function", "if", "in", "instanceof", "let", "new", "of", "return",
    "switch", "throw", "try", "typeof", "var", "void", "while", "with", "yield",
}


def _js_code(js: str) -> str:
    """Comments, string literals and regex literals blanked out; code kept.

    What remains is scan-able as plain source. Without it, words inside strings
    (a CSS ``rgba(...)`` colour, say) read as call sites. Template
    interpolations are *kept* - that is real code - but wrapped in parentheses
    so neighbouring pieces (``${m}${esc(x)}``) cannot glue into one bogus
    identifier.
    """
    out: list[str] = []
    i, n = 0, len(js)
    while i < n:
        c = js[i]
        if js.startswith("//", i):
            j = js.find("\n", i)
            i = n if j < 0 else j
        elif js.startswith("/*", i):
            j = js.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "\"'":
            q, i = c, i + 1
            while i < n and js[i] != q:
                i += 2 if js[i] == "\\" else 1
            i += 1
            out.append(q + q)
        elif c == "`":
            i += 1
            while i < n:
                if js.startswith("${", i):
                    j, depth = i + 2, 1
                    while j < n and depth:
                        depth += (js[j] == "{") - (js[j] == "}")
                        j += 1
                    out.append("(" + js[i + 2:j - 1] + ")")
                    i = j
                elif js[i] == "\\":
                    i += 2
                elif js[i] == "`":
                    i += 1
                    break
                else:
                    i += 1
            out.append("``")
        elif c == "/":
            # A slash after a value is division; after punctuation or at a
            # statement start it opens a regex literal, whose body may contain
            # quotes that would otherwise derail the string scan above.
            prev = "".join(out).rstrip()[-1:] if out else ""
            if prev in "(,=:[!&|?{};":
                j, esc, in_class = i + 1, False, False
                while j < n:
                    ch = js[j]
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == "[":
                        in_class = True
                    elif ch == "]":
                        in_class = False
                    elif ch == "/" and not in_class:
                        break
                    elif ch == "\n":
                        break
                    j += 1
                i = j + 1
                out.append("//")
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def test_every_helper_the_script_calls_is_defined():
    """A rename that leaves a call site behind breaks the dashboard at load,
    not at import: `init()` catches the TypeError, so the page comes up with
    empty selects and a generic banner instead of a stack trace - invisible to
    `node --check` and to every pytest that only reads the served HTML.

    (Half of the lookback-to-window rename did exactly this: `fillTimeframes`
    was renamed away, but `init()` still awaited it.)
    """
    code = _js_code((STATIC / "app.js").read_text(encoding="utf-8"))

    defined = set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", code))
    defined |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", code))
    called = {m.group(1) for m in re.finditer(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(", code)}

    missing = sorted(called - defined - _JS_GLOBALS - _JS_KEYWORDS)
    assert missing == [], f"app.js calls helpers it never defines: {missing}"


def test_the_script_never_declares_a_helper_twice():
    """Two declarations of the same name silently shadow each other: the later
    one wins for the whole script and the earlier becomes dead code nobody
    notices. The same rename shipped two `fillWindows`, one of them a stray
    copy that hid the loss of the timeframe picker.
    """
    code = _js_code((STATIC / "app.js").read_text(encoding="utf-8"))
    names = re.findall(
        r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", code, re.M
    )

    dupes = sorted({n for n in names if names.count(n) > 1})
    assert dupes == [], f"app.js declares these functions more than once: {dupes}"
