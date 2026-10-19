"""Tests for the Postgres metadata read (no database required).

The dimension read is one filtered scan of the metadata view, left-joined to
the broker's own category catalogue (``symbols_category`` -> ``asset_classes``)
so every row carries its own broker's asset-class label. These tests pin the
projection, the join keys and the fallback shape without a server.
"""
from __future__ import annotations

import contextlib

import pandas as pd
import pytest

from app import meta
from app.config import Settings
from app.errors import DataSourceError


def _settings(**overrides) -> Settings:
    """A settings object pointed at a host that is never contacted."""
    base = {"pg_host": "meta.example.invalid", "pg_database": "metadata",
            "redis_host": "cache.example.invalid"}
    base.update(overrides)
    return Settings(**base)


class _Recorder:
    """A stand-in for ``pd.read_sql`` that captures the SQL and returns a frame."""

    def __init__(self, frame: pd.DataFrame):
        self.frame = frame
        self.sql = ""
        self.params = None

    def __call__(self, sql, conn, params=None):
        self.sql = sql
        self.params = params
        return self.frame

    @property
    def flat_sql(self) -> str:
        return " ".join(self.sql.split())


def _capture(monkeypatch, frame: pd.DataFrame) -> _Recorder:
    """Route ``symbol_catalogue``'s read through a recorder, no server."""
    rec = _Recorder(frame)

    @contextlib.contextmanager
    def connect(settings=None):
        yield object()

    monkeypatch.setattr(meta, "_connect", connect)
    monkeypatch.setattr(meta.pd, "read_sql", rec)
    return rec


def _catalogue_rows() -> pd.DataFrame:
    """The columns the join projects, with acgmarkets' gold row."""
    return pd.DataFrame([
        {"symbolId": 136, "symbolName": "XAUUSD.pro", "symbolCategoryId": 10,
         "description": "Gold vs US Dollar", "ctrader_account_id": 49208255,
         "broker_name": "acgmarkets", "asset_class_id": 10,
         "asset_class_name": "Metals (Pro)"},
        {"symbolId": 140, "symbolName": "USOIL.pro", "symbolCategoryId": 11,
         "description": "WTI Crude Oil", "ctrader_account_id": 49208255,
         "broker_name": "acgmarkets", "asset_class_id": 11,
         "asset_class_name": "Oil (Pro)"},
    ])


# ----------------------------------------------------------------------
# The projection carries the broker's own class catalogue
# ----------------------------------------------------------------------

def test_catalogue_projects_the_brokers_class_columns(monkeypatch):
    rec = _capture(monkeypatch, _catalogue_rows())

    frame = meta.symbol_catalogue(_settings())

    assert list(frame.columns) == meta.SYMBOL_COLUMNS + meta.SCOPE_COLUMNS + [
        "asset_class_id", "asset_class_name"
    ]
    assert frame.loc[0, "asset_class_name"] == "Metals (Pro)"
    assert int(frame.loc[0, "asset_class_id"]) == 10


def test_catalogue_joins_the_category_and_class_tables(monkeypatch):
    rec = _capture(monkeypatch, _catalogue_rows())

    meta.symbol_catalogue(_settings())

    assert "symbols_category" in rec.flat_sql
    assert "asset_classes" in rec.flat_sql
    # The join must key on the account as well as the id: the same
    # symbolCategoryId means something different in each account's catalogue.
    assert 'symbols."ctrader_account_id" = sc."ctidTraderAccountId"' in rec.flat_sql
    assert 'symbols."symbolCategoryId" = sc."symbolCategoryId"' in rec.flat_sql
    assert 'sc."ctidTraderAccountId" = ac."ctidTraderAccountId"' in rec.flat_sql
    assert 'sc."assetClassId" = ac."assetClassId"' in rec.flat_sql


def test_catalogue_scopes_on_the_metadata_view(monkeypatch):
    """The configured table (the view, by default) stays the FROM target."""
    rec = _capture(monkeypatch, _catalogue_rows())

    meta.symbol_catalogue(_settings())

    assert 'FROM "symbols_metadata" AS symbols' in rec.flat_sql


def test_catalogue_passes_the_scope_filters_as_parameters(monkeypatch):
    rec = _capture(monkeypatch, _catalogue_rows())

    meta.symbol_catalogue(_settings(ctrader_account_id="49208255",
                                    broker_name="ACGMarkets"))

    assert 'symbols."ctrader_account_id" = %s' in rec.flat_sql
    assert 'LOWER(symbols."broker_name") = LOWER(%s)' in rec.flat_sql
    assert list(rec.params) == ["49208255", "ACGMarkets"]


def test_an_empty_catalogue_keeps_the_class_columns(monkeypatch):
    """Callers read ``asset_class_name`` off the rows; an empty read must not
    change the shape."""
    _capture(monkeypatch, pd.DataFrame())
    frame = meta.symbol_catalogue(_settings())
    assert list(frame.columns) == meta.SYMBOL_COLUMNS + meta.SCOPE_COLUMNS + [
        "asset_class_id", "asset_class_name"
    ]
    assert frame.empty


def test_catalogue_drops_unusable_symbol_ids(monkeypatch):
    """A row whose id will not coerce is dropped instead of shipped."""
    rows = pd.DataFrame([
        {"symbolId": 136, "symbolName": "XAUUSD.pro", "symbolCategoryId": 10,
         "description": "Gold vs US Dollar", "ctrader_account_id": 49208255,
         "broker_name": "acgmarkets", "asset_class_id": 10,
         "asset_class_name": "Metals (Pro)"},
        {"symbolId": "not-a-number", "symbolName": "Broken",
         "symbolCategoryId": 10, "description": "",
         "ctrader_account_id": 49208255, "broker_name": "acgmarkets",
         "asset_class_id": 10, "asset_class_name": "Metals (Pro)"},
    ])
    _capture(monkeypatch, rows)

    frame = meta.symbol_catalogue(_settings())

    assert list(frame["symbolId"]) == [136]
    assert frame["symbolId"].dtype.kind == "i"


def test_a_broken_read_is_reported_as_a_data_source_error(monkeypatch):
    @contextlib.contextmanager
    def connect(settings=None):
        raise DataSourceError("nope")

    monkeypatch.setattr(meta, "_connect", connect)

    with pytest.raises(DataSourceError):
        meta.symbol_catalogue(_settings())
