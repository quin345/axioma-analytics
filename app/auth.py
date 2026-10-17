"""User-facing login for the dashboard: sessions and the credential check.

This is the *website* gate - one account stands between a visitor and the
dashboard - and is entirely separate from the Entra identity that
authenticates the data plane (see ``app/kql.py``). The gate exists only while
``LOGIN_PASSWORD`` is configured (``Settings.login_enabled``); with no
password the site stays open, which keeps local runs and the test suite
working without a credential in the environment.

Sessions are random bearer tokens held in this process - the deployment is a
single uvicorn worker - so a token cannot be forged, nothing is persisted, and
a restart signs everyone out. The cookie carrying one is ``HttpOnly`` and
``SameSite=Lax``, so page scripts never see it and a cross-site form post
cannot ride it.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from .config import Settings, get_settings

#: The session cookie the gate reads. HttpOnly, so it is invisible to the
#: dashboard's own script; the browser just carries it back on every request.
COOKIE_NAME = "axioma_session"

#: A failed sign-in is delayed by this much so a wrong guess costs real time
#: rather than only a rate the attacker controls. Deliberately small: this is
#: a single shared account, not a user directory, and the honest user only
#: ever pays it after their own typo.
_FAIL_DELAY_SECONDS = 0.25

#: Live sessions: token -> unix expiry. Pruned of expired entries on every
#: mint and check, so the store cannot grow past the session lifetime.
_sessions: dict[str, float] = {}

#: The endpoints and pages that stay reachable without a session: the login
#: flow itself; ``/api/health``, which carries no rows and is what uptime
#: checks poll; the explainer and outage pages; the API docs (schema only -
#: every data endpoint still answers 401); and the static assets the login
#: page renders from.
_PUBLIC = frozenset({
    "/login", "/api/login", "/api/logout", "/api/health",
    "/welcome", "/unavailable", "/docs", "/openapi.json", "/redoc",
})

_lock = threading.Lock()


def _digest(value: str) -> bytes:
    """SHA-256 of the value, so both sides of a compare are fixed-length."""
    return hashlib.sha256((value or "").encode("utf-8")).digest()


def verify(username: str, password: str, settings: Settings | None = None) -> bool:
    """True for the one configured account.

    Both halves are compared as digests through ``hmac.compare_digest``, and
    both are always evaluated, so neither the username nor the password leaks
    its correctness through timing. A wrong pair is answered the same way
    whether the name or the password was the wrong half, so the endpoint
    cannot be used to enumerate accounts.
    """
    s = settings or get_settings()
    if not s.login_enabled:
        return False
    user_ok = hmac.compare_digest(_digest(username), _digest(s.login_username))
    pass_ok = hmac.compare_digest(_digest(password), _digest(s.login_password))
    ok = user_ok & pass_ok          # both compares always run
    if not ok:
        time.sleep(_FAIL_DELAY_SECONDS)
    return ok


def start_session(settings: Settings | None = None) -> str:
    """Mint a fresh session token, pruning any that have expired."""
    s = settings or get_settings()
    token = secrets.token_urlsafe(32)
    now = time.time()
    ttl = max(1, s.login_session_hours) * 3600.0
    with _lock:
        for stale in [t for t, exp in _sessions.items() if exp <= now]:
            del _sessions[stale]
        _sessions[token] = now + ttl
    return token


def session_valid(token: str | None) -> bool:
    """True while the token names a live session; expired ones are dropped."""
    if not token:
        return False
    now = time.time()
    with _lock:
        expires = _sessions.get(token)
        if expires is None:
            return False
        if expires <= now:
            del _sessions[token]
            return False
        return True


def end_session(token: str | None) -> None:
    """Forget a session. Idempotent: an absent token is already ended."""
    if not token:
        return
    with _lock:
        _sessions.pop(token, None)


def is_public(path: str, settings: Settings | None = None) -> bool:
    """True for the paths the gate never blocks.

    The one conditional case is ``/`` during maintenance mode: the outage
    page must stay reachable for uptime checks and visitors even while the
    dashboard behind it is closed.
    """
    s = settings or get_settings()
    if path in _PUBLIC or path.startswith("/static/"):
        return True
    return bool(s.maintenance) and path == "/"