"""Shared fixtures.

The suite runs against the developer's populated ``.env``, which carries
``LOGIN_PASSWORD``. The login gate must not turn every pre-existing route
test into a redirect, so it is forced off here for every test; the tests
that are *about* the gate turn it back on with a fixture that requests this
one, so the enable always lands after the disable. Live session tokens are
dropped too, so a session minted in one test can never authenticate another.
"""
from __future__ import annotations

import pytest

from app import auth, config


@pytest.fixture(autouse=True)
def _login_disabled(monkeypatch):
    """Keep every test off the gate unless it opts in explicitly.

    The cache-clear is bound while ``get_settings`` is still the real
    function: some tests (test_refresh_script.py) replace the module
    attribute with a stub, and that replacement outlives this teardown -
    calling ``config.get_settings.cache_clear()`` by name here would hit the
    stub and raise. The saved bound method clears the same underlying cache.
    """
    clear = config.get_settings.cache_clear
    monkeypatch.setenv("LOGIN_PASSWORD", "")
    clear()
    auth._sessions.clear()
    yield
    auth._sessions.clear()
    clear()
