"""Tests for the one-shot refresh script (no data source required).

The script is the cron/systemd alternative to the in-process cycle, so its
contract matters to operators: a summary on stdout, a non-zero exit when the
endpoint is unreachable (so a timer surfaces it), and zero for a normal run
even if a single symbol came back empty.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app import service
from app.config import Settings

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "refresh_cache.py"

_SUMMARY = {
    "started_at": "2026-10-08T01:30:00+00:00",
    "finished_at": "2026-10-08T01:30:02+00:00",
    "seconds": 2.0,
    "symbols": 1,
    "rows": 240,
    "window_minutes": 240,
    "interval_minutes": 30,
    "errors": [],
}


@pytest.fixture
def script():
    """The script, loaded from its path so `scripts/` needs no package."""
    spec = importlib.util.spec_from_file_location("refresh_cache_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr("app.config.get_settings",
                        lambda: Settings(kql_host="https://kql.example.invalid"))
    monkeypatch.setattr(service, "refresh_cache", lambda: dict(_SUMMARY))
    monkeypatch.setattr(sys, "argv", ["refresh_cache.py"])
    yield


def test_a_configured_run_prints_a_summary_and_succeeds(script, configured, capsys):
    assert script.main() == 0

    out = capsys.readouterr().out
    assert "Refreshed 1 symbol(s) over 4 h, 240 row(s)" in out
    assert "next cycle in 30 min" in out


def test_the_json_flag_prints_the_summary(script, configured, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["refresh_cache.py", "--json"])

    assert script.main() == 0

    assert json.loads(capsys.readouterr().out)["symbols"] == 1


def test_everything_failing_is_a_non_zero_exit(script, configured, capsys, monkeypatch):
    """A timer has to be able to see a broken endpoint."""
    def failed():
        summary = dict(_SUMMARY)
        summary.update(symbols=0, rows=0, errors=["41: KQL query failed: Forbidden"])
        return summary

    monkeypatch.setattr(service, "refresh_cache", failed)

    assert script.main() == 1
    assert "! 41: KQL query failed: Forbidden" in capsys.readouterr().err


def test_an_unconfigured_host_refuses_to_run(script, monkeypatch, capsys):
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(kql_host=""))
    monkeypatch.setattr(sys, "argv", ["refresh_cache.py"])

    assert script.main() == 1
    assert "KQL_ENDPOINT_PROD not set" in capsys.readouterr().err
