"""Tests for the KQL transport helpers (no data source required).

The client is faked rather than mocked at the library boundary, so the assertions
cover what the app actually sends: the database, the CSL text and the declarative
query parameters. That is where the migration from T-SQL to KQL can regress
silently - the returned columns are identical by design.
"""
from __future__ import annotations

import pandas as pd
import pytest
from azure.kusto.data.exceptions import KustoError

from app import config, kql
from app.config import Settings
from app.db import DataSourceError


# --------------------------------------------------------------------------
# A stand-in for the Kusto client: replays canned result tables, records calls
# --------------------------------------------------------------------------

class _Column:
    def __init__(self, name: str) -> None:
        self.column_name = name


class _Row:
    def __init__(self, values: dict) -> None:
        self._values = values

    def to_dict(self) -> dict:
        return dict(self._values)


class _Table:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.columns = [_Column(k) for k in (rows[0] if rows else {})]

    def __iter__(self):
        return iter(_Row(r) for r in self._rows)


class _Result:
    def __init__(self, rows: list[dict]) -> None:
        self.primary_results = [_Table(rows)]


class _FakeClient:
    def __init__(self, results: list[list[dict]] | None = None, error: Exception | None = None) -> None:
        self.results = list(results or [])
        self.error = error
        self.calls: list[tuple[str, str, object]] = []

    def execute(self, database: str, csl: str, props=None):
        self.calls.append((database, csl, props))
        if self.error is not None:
            raise self.error
        return _Result(self.results.pop(0))


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Credential selection from a known-empty environment, then restore.

    The suite runs against a populated ``.env``, so without this the auth-mode
    assertions would silently test whichever mode the developer's file selects.
    """
    for key in ("FABRIC_MANAGED_IDENTITY", "FABRIC_MANAGED_IDENTITY_CLIENT_ID",
                "FABRIC_CLIENT_ID", "FABRIC_CLIENT_SECRET", "FABRIC_TENANT_ID"):
        monkeypatch.delenv(key, raising=False)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        kql_host="https://trd-abc.z9.kusto.fabric.microsoft.com",
        kql_database="ctrader_dom",
        kql_table="agg_dom",
    )


def _param(props, name: str):
    """Declarative parameter value, or None when it was never set."""
    return props.get_parameter(name, None) if props is not None else None


# --------------------------------------------------------------------------
# Object names
# --------------------------------------------------------------------------

def test_table_ref_leaves_a_plain_name_alone():
    assert kql.table_ref("agg_dom") == "agg_dom"


def test_table_ref_bracket_quotes_an_awkward_name():
    assert kql.table_ref("agg-dom") == "['agg-dom']"
    assert kql.table_ref("agg dom") == "['agg dom']"
    # A quote cannot escape the bracket, it is dropped rather than interpolated.
    assert kql.table_ref("agg'dom") == "['aggdom']"


# --------------------------------------------------------------------------
# query()
# --------------------------------------------------------------------------

def test_query_returns_the_primary_result_as_a_dataframe(settings):
    client = _FakeClient([[{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]])
    df = kql.query(client, "print a = 1", settings=settings)
    assert list(df.columns) == ["a", "b"]
    assert df["a"].tolist() == [1, 2]


def test_query_sends_parameters_declaratively_not_interpolated(settings):
    client = _FakeClient([[{"n": 1}]])
    kql.query(client, "print n = 1", {"sym": "7701", "start": "2026-10-08T09:30:00+00:00"},
              settings=settings)
    database, csl, props = client.calls[0]
    assert database == "ctrader_dom"
    # The query text keeps its declaration and never carries the values.
    assert "7701" not in csl
    assert _param(props, "sym") == "7701"
    assert _param(props, "start") == "2026-10-08T09:30:00+00:00"


def test_query_skips_none_parameters(settings):
    client = _FakeClient([[{"n": 1}]])
    kql.query(client, "print n = 1", {"sym": "7701", "end": None}, settings=settings)
    props = client.calls[0][2]
    assert _param(props, "sym") == "7701"
    assert not props.has_parameter("end")


def test_query_wraps_a_kusto_failure_in_a_data_source_error(settings):
    client = _FakeClient(error=KustoError("Forbidden (403-Forbidden)"))
    with pytest.raises(DataSourceError, match="KQL query failed"):
        kql.query(client, "print n = 1", settings=settings)


# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------

def test_server_time_reads_the_endpoint_clock(settings):
    client = _FakeClient([[{"server_time": "2026-10-08T10:00:00Z"}]])
    assert kql.server_time(client, settings) == "2026-10-08T10:00:00Z"


def test_server_time_is_blank_when_the_probe_returns_no_rows(settings):
    client = _FakeClient([[]])
    assert kql.server_time(client, settings) == ""


def test_tick_stats_returns_count_and_newest_timestamp(settings):
    latest = pd.Timestamp("2026-10-08T10:00:00Z")
    client = _FakeClient([[{"n": 12, "latest": latest}]])
    count, seen = kql.tick_stats(client, settings)
    assert (count, seen) == (12, str(latest))
    assert "agg_dom" in client.calls[0][1]
    assert "summarize" in client.calls[0][1]


def test_tick_stats_reports_an_empty_table_as_no_latest(settings):
    client = _FakeClient([[{"n": 0, "latest": None}]])
    assert kql.tick_stats(client, settings) == (0, None)


def test_symbol_tick_counts_keys_on_the_stringified_symbol_id(settings):
    client = _FakeClient([[{"symbolId": "7701", "ticks": 5},
                           {"symbolId": 42, "ticks": 3}]])
    counts = kql.symbol_tick_counts(client, settings)
    assert counts == {"7701": 5, "42": 3}
    # The grouping key is the column itself - `symbolId` is a long in KQL.
    assert "by symbolId" in client.calls[0][1]
    assert "tostring" not in client.calls[0][1]


# --------------------------------------------------------------------------
# fetch_ticks()
# --------------------------------------------------------------------------

def test_fetch_ticks_caps_oldest_rows_first(settings):
    client = _FakeClient([[{"timestamp": pd.Timestamp("2026-10-08T09:00:00Z"),
                            "symbolId": "7701"}]])
    kql.fetch_ticks(client, symbol="7701", limit=10, settings=settings)
    csl = client.calls[0][1]
    assert "top 10 by timestamp asc" in csl
    assert client.calls[0][0] == "ctrader_dom"


def test_fetch_ticks_compares_the_symbol_id_as_a_long(settings):
    """`symbolId` is a long, so the parameter is cast, not the column."""
    client = _FakeClient([[{"timestamp": pd.Timestamp("2026-10-08T10:00:00Z")}]])
    kql.fetch_ticks(client, symbol="7701", settings=settings)
    csl = client.calls[0][1]
    assert "symbolId == tolong(sym)" in csl
    assert "tostring(symbolId)" not in csl


def test_fetch_ticks_anchors_the_lookback_on_the_newest_row(settings):
    anchor = pd.Timestamp("2026-10-08T10:00:00Z")
    client = _FakeClient([[{"mx": anchor}], [{"timestamp": anchor, "symbolId": "7701"}]])
    kql.fetch_ticks(client, symbol="7701", lookback_minutes=30, settings=settings)

    assert len(client.calls) == 2
    assert "summarize mx = max(timestamp)" in client.calls[0][1]
    # 10:00 minus 30 minutes, normalised to an ISO-8601 UTC instant Kusto casts.
    assert _param(client.calls[1][2], "start") == "2026-10-08T09:30:00+00:00"
    assert _param(client.calls[1][2], "sym") == "7701"


def test_fetch_ticks_without_a_lookback_runs_one_query(settings):
    client = _FakeClient([[{"timestamp": pd.Timestamp("2026-10-08T10:00:00Z")}]])
    kql.fetch_ticks(client, symbol="7701", settings=settings)
    assert len(client.calls) == 1
    assert not client.calls[0][2].has_parameter("start")


def test_fetch_ticks_explicit_range_wins_over_the_lookback(settings):
    client = _FakeClient([[{"timestamp": pd.Timestamp("2026-01-01T00:00:00Z")}]])
    kql.fetch_ticks(client, symbol="7701", start="2026-01-01T00:00:00+00:00",
                    end="2026-01-02T00:00:00+00:00", lookback_minutes=30, settings=settings)
    assert len(client.calls) == 1  # no anchor query
    props = client.calls[0][2]
    assert _param(props, "start") == "2026-01-01T00:00:00+00:00"
    assert _param(props, "end") == "2026-01-02T00:00:00+00:00"


class _AnchorFailingClient(_FakeClient):
    """Fails the anchor query, then serves the fetch."""

    def execute(self, database: str, csl: str, props=None):
        if not self.calls:
            self.calls.append((database, csl, props))
            raise KustoError("Forbidden (403-Forbidden)")
        return super().execute(database, csl, props)


def test_fetch_ticks_falls_back_when_the_anchor_query_fails(settings):
    """An anchor that cannot be read must not turn the fetch into an error."""
    client = _AnchorFailingClient([[{"timestamp": pd.Timestamp("2026-10-08T10:00:00Z")}]])
    df = kql.fetch_ticks(client, symbol="7701", lookback_minutes=30, settings=settings)
    assert len(client.calls) == 2
    assert not client.calls[1][2].has_parameter("start")
    assert not df.empty


def test_fetch_ticks_without_a_symbol_does_not_filter(settings):
    client = _FakeClient([[{"timestamp": pd.Timestamp("2026-10-08T10:00:00Z")}]])
    kql.fetch_ticks(client, settings=settings)
    assert _param(client.calls[0][2], "sym") == ""


# --------------------------------------------------------------------------
# Credentials and connection
# --------------------------------------------------------------------------

def test_managed_identity_wins_even_with_a_secret_present(monkeypatch):
    seen: list[dict] = []

    class _Recorder:
        def __init__(self, **kwargs) -> None:
            seen.append(kwargs)

    monkeypatch.setattr(kql, "ManagedIdentityCredential", _Recorder)
    kql._credential(Settings(managed_identity=True, client_id="cid",
                             client_secret="shh", tenant_id="tid"))
    assert seen == [{}]


def test_managed_identity_client_id_is_forwarded(monkeypatch):
    seen: list[dict] = []

    class _Recorder:
        def __init__(self, **kwargs) -> None:
            seen.append(kwargs)

    monkeypatch.setattr(kql, "ManagedIdentityCredential", _Recorder)
    kql._credential(Settings(managed_identity=True, managed_identity_client_id="mi-1"))
    assert seen == [{"client_id": "mi-1"}]


def test_service_principal_is_used_when_no_managed_identity():
    cred = kql._credential(Settings(client_id="cid", client_secret="shh", tenant_id="tid"))
    assert type(cred).__name__ == "ClientSecretCredential"


def test_azure_cli_is_the_last_fallback():
    cred = kql._credential(Settings())
    assert type(cred).__name__ == "AzureCliCredential"


def test_connect_refuses_an_unconfigured_endpoint():
    with pytest.raises(DataSourceError, match="KQL endpoint is not configured"):
        with kql.connect(Settings(kql_host="")):
            pass
