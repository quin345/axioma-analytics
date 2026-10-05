"""Tests for the gold asset-class classification (no data source required).

Cases mirror the real production `gold.asset_classes_icmarkets` names and the
instruments actually present in `gold.symbols_icmarkets`.
"""
from __future__ import annotations

import pytest

from app.assets import (ASSET_CLASSES, BROKER_CLASSES, CLASS_LABELS,
                        CLASS_PARENT, classify_asset_class, classify_by_name,
                        family_of, sort_key)


# ----------------------------------------------------------------------
# Broker chain (the authoritative source)
# ----------------------------------------------------------------------

@pytest.mark.parametrize("name,key", [
    ("Forex", "fx"),
    ("Metals", "metal"),
    ("Indices", "index"),
    ("Oil", "energy"),
    ("Cryptocurrencies", "crypto"),
    ("Commodities", "commodity"),
    ("Futures", "future"),
    ("Bonds", "bond"),
    ("Futures Commodities", "future"),
])
def test_every_broker_class_maps_to_a_known_key(name, key):
    assert BROKER_CLASSES[name.lower()] == key
    # Every broker class must resolve to a displayable class: either a leaf
    # that is itself a key, or a parent of at least one sub-classed key.
    leaves = [k for k in CLASS_LABELS if CLASS_PARENT.get(k, k) == key]
    assert key in CLASS_LABELS or leaves, f"'{key}' has no leaf and is not a key"


@pytest.mark.parametrize("broker,symbol,expected", [
    ("Forex", "EURUSD", "fx_major"),
    ("Forex", "USDPLN", "fx_exotic"),
    ("Forex", "EURGBP", "fx_cross"),
    ("Metals", "XAUUSD", "metal"),
    ("Metals", "XPTUSD", "metal"),
    ("Indices", "US500", "index"),
    ("Oil", "XBRUSD", "energy"),
    ("Cryptocurrencies", "BTCUSD", "crypto"),
    ("Bonds", "US10Y", "bond"),
    ("Futures", "EURBBL_H6", "future"),
    ("Commodities", "Wheat_K6", "commodity"),
])
def test_classification_from_the_broker_chain(broker, symbol, expected):
    assert classify_asset_class(symbol, broker) == expected


def test_broker_name_wins_over_the_fallback():
    # The chain says Metals even though the name looks like a crypto pair.
    assert classify_asset_class("XAUUSD", "Metals") == "metal"


def test_chain_name_is_case_insensitive():
    assert classify_asset_class("BTCUSD", "cryptocurrencies") == "crypto"
    assert classify_asset_class("BTCUSD", "  CRYPTOCURRENCIES ") == "crypto"


# ----------------------------------------------------------------------
# FX sub-classification
# ----------------------------------------------------------------------

@pytest.mark.parametrize("pair", ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
                                  "USDCHF", "USDCAD", "NZDUSD"])
def test_the_seven_majors(pair):
    assert classify_asset_class(pair, "Forex") == "fx_major"


@pytest.mark.parametrize("pair", ["EURGBP", "EURJPY", "GBPAUD", "AUDCHF",
                                  "NZDCAD", "EURCHF", "CHFJPY"])
def test_major_leg_pairs_are_crosses(pair):
    assert classify_asset_class(pair, "Forex") == "fx_cross"


@pytest.mark.parametrize("pair", ["USDHUF", "EURPLN", "USDTRY", "USDSGD",
                                  "GBPSEK", "EURZAR", "USDCNH"])
def test_emerging_leg_pairs_are_exotics(pair):
    assert classify_asset_class(pair, "Forex") == "fx_exotic"


# ----------------------------------------------------------------------
# Fallback (no chain row)
# ----------------------------------------------------------------------

@pytest.mark.parametrize("name,category,expected", [
    ("EURUSD", 7, "fx"),        # symbolCategoryId -> assetClassId mapping
    ("XAUUSD", 8, "metal"),
    ("US500", 9, "index"),
    ("XTIUSD", 10, "energy"),
    ("Wheat_K6", 11, "commodity"),
    ("BTCUSD", 12, "crypto"),
    ("GCM25", 13, "future"),
    ("US10Y", 14, "bond"),
])
def test_fallback_uses_the_category_id(name, category, expected):
    assert classify_by_name(name, category) == expected


@pytest.mark.parametrize("name,expected", [
    ("XAGUSD", "metal"),
    ("SpotBrent", "energy"),
    ("VIX", "index"),
    ("Cotton_Z6", "commodity"),
    ("UKGILT", "bond"),
    ("GCM25", "future"),
    ("BTCUSD", "crypto"),
    ("USDPLN", "fx"),
])
def test_fallback_uses_the_ticker_when_the_category_is_unknown(name, expected):
    assert classify_by_name(name, None) == expected


def test_fallback_prefers_the_category_over_the_ticker():
    # A category row is more authoritative than a name pattern.
    assert classify_by_name("XAUUSD", 7) == "fx"


# ----------------------------------------------------------------------
# Robustness
# ----------------------------------------------------------------------

def test_unknown_class_and_unknown_name_yields_nothing():
    assert classify_asset_class("Mystery", "Something Else") == ""


def test_null_inputs_do_not_raise():
    assert classify_asset_class(None, None, None, None) == ""
    assert classify_asset_class("", None, None, "") == ""
    assert classify_by_name(None) == ""


def test_blank_category_is_tolerated():
    assert classify_asset_class("XAUUSD", "Metals", None, None) == "metal"
    assert classify_by_name("XAUUSD", "not-a-number") == "metal"
    assert classify_by_name("XAUUSD", float("nan")) == "metal"


def test_every_class_key_has_a_label_and_parents():
    for key, label in ASSET_CLASSES:
        assert CLASS_LABELS[key] == label
        assert label
        assert family_of(key)


def test_display_order_is_liquidity_first():
    order = [k for k, _ in ASSET_CLASSES]
    assert sorted(order, key=sort_key) == order
    assert order.index("fx_major") < order.index("crypto")
    assert order.index("crypto") < order.index("future")


def test_unknown_class_sorts_last():
    assert sort_key("nope") > sort_key("future")