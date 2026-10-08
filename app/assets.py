"""Asset-class taxonomy for the instrument dimension.

The pipeline publishes the class on each symbol row:

    symbols_icmarkets   (symbolId, symbolName, symbolCategoryId, description)

`classify_asset_class` reads that category first, so the dashboard groups
instruments exactly the way the pipeline does. The broker names nine classes
(Forex, Metals, Indices, Oil, Cryptocurrencies, Commodities, Futures, Bonds,
Futures Commodities); :data:`ASSET_CLASSES` maps them to stable client keys and
adds a *sub-class* for FX, which the pipeline lumps together but which is worth
splitting on a dashboard (majors, crosses, exotics).

For a symbol whose category is missing - an archive row, a new listing, a
dimension table that has not been reloaded - :func:`classify_by_name` falls
back to the same signals cTrader exposes (`symbolCategoryId`, the ticker and
the description), so no instrument is ever left uncategorised.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Taxonomy
# --------------------------------------------------------------------------

#: Broker asset-class name -> stable client key.
BROKER_CLASSES: dict[str, str] = {
    "forex": "fx",
    "metals": "metal",
    "indices": "index",
    "oil": "energy",
    "cryptocurrencies": "crypto",
    "commodities": "commodity",
    "futures": "future",
    "bonds": "bond",
    "futures commodities": "future",
}

#: Client key -> display label, ordered most-liquid-first.
ASSET_CLASSES: tuple[tuple[str, str], ...] = (
    ("fx_major", "FX - Majors"),
    ("fx_cross", "FX - Crosses"),
    ("fx_exotic", "FX - Exotics"),
    ("metal", "Metals"),
    ("energy", "Energy"),
    ("commodity", "Commodities & Agriculture"),
    ("index", "Equity Indices"),
    ("bond", "Bonds"),
    ("crypto", "Crypto"),
    ("future", "Futures & Forwards"),
)

#: The parent class of each sub-classed key, so grouping can roll up.
CLASS_PARENT: dict[str, str] = {
    "fx_major": "fx", "fx_cross": "fx", "fx_exotic": "fx",
    "metal": "metal", "energy": "energy", "commodity": "commodity",
    "index": "index", "bond": "bond", "crypto": "crypto", "future": "future",
}

CLASS_LABELS: dict[str, str] = dict(ASSET_CLASSES)

#: Broad families, used to group the selector without losing the detail.
CLASS_FAMILY: dict[str, str] = {
    "fx_major": "fx", "fx_cross": "fx", "fx_exotic": "fx",
    "metal": "commodities", "energy": "commodities", "commodity": "commodities",
    "index": "indices", "bond": "rates", "crypto": "crypto", "future": "derivatives",
}

FAMILY_LABELS: dict[str, str] = {
    "fx": "FX", "commodities": "Commodities", "indices": "Indices",
    "rates": "Rates", "crypto": "Crypto", "derivatives": "Derivatives",
}
# --------------------------------------------------------------------------
# FX sub-classification
# --------------------------------------------------------------------------

#: The seven most-traded pairs. Anything else quoted as two ISO currencies is a
#: cross, and an emerging-market leg makes it an exotic.
_FX_MAJORS = {
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD", "NZDUSD",
}
# Every currency cTrader quotes, split by liquidity. A pair with a leg in
# _EXOTIC_LEGS is an exotic; a pair made only of _MAJOR_LEGS is a cross.
_MAJOR_LEGS = frozenset("""
USD EUR GBP JPY CHF AUD NZD CAD
""".split())
_EXOTIC_LEGS = frozenset("""
NOK SEK DKK PLN HUF CZK TRY ZAR MXN BRL CNH CNY SGD HKD THB ILS RON RUB
INR KRW TWD IDR PHP MYR CLP COP ARS
""".split())


def _fx_subclass(symbol_name: object) -> str:
    """Split an FX instrument into majors / crosses / exotics."""
    name = str(symbol_name or "").strip().upper()
    if name in _FX_MAJORS:
        return "fx_major"
    if len(name) == 6 and name.isalpha():
        if name[:3] in _EXOTIC_LEGS or name[3:] in _EXOTIC_LEGS:
            return "fx_exotic"
        return "fx_cross"
    # An FX instrument that is not a plain pair (a forward, an index) stays
    # with the cross bucket rather than being dropped.
    return "fx_cross"


# --------------------------------------------------------------------------
# Fallback signals
# --------------------------------------------------------------------------

#: cTrader symbolCategoryId -> asset class, used when the dimension row has no
#: usable category. These ids are the broker's own ``symbolCategoryId`` values.
_FALLBACK_CATEGORY: dict[int, str] = {
    7: "fx", 8: "metal", 9: "index", 10: "energy", 11: "commodity",
    12: "crypto", 13: "future", 14: "bond", 15: "future",
}

_METAL_TOKENS = (
    "XAU", "XAG", "XPT", "XPD", "GOLD", "SILVER", "PLATINUM", "PALLADIUM",
    "COPPER", "ALUMINIUM", "ALUMINUM", "ZINC", "NICKEL", "IRON",
)
_ENERGY_TOKENS = ("XTI", "XBR", "OIL", "BRENT", "CRUDE", "WTI", "NATGAS", "GASOLINE")
_SOFT_TOKENS = (
    "WHEAT", "CORN", "SOYBEAN", "SUGAR", "COFFEE", "COCOA", "COTTON",
    "CATTLE", "ORANGEJUICE", "LUMBER", "RICE", "OATS", "HOGS",
)
_INDEX_TOKENS = (
    "US500", "US2000", "US30", "US100", "US400", "USTEC", "NAS100", "AUS200",
    "GER40", "DE40", "FRA40", "EUSTX50", "UK100", "JPN225", "JP225", "HSTECH",
    "SPA35", "CHINAH", "HK50", "CN50", "CHINA50", "SCI25", "SWI20", "SG30",
    "VIX", "MID", "ES30", "KR200",
)
_BOND_TOKENS = ("UKGILT", "USTN", "TBOND", "T-NOTE", "BUND", "BOBL", "BTP", "GILT", "BOND")
#: Quote currencies a crypto pair is printed against.
_CRYPTO_QUOTES = ("USDC", "USD", "EUR", "GBP", "AUD", "BRL", "TRY", "JPY")
#: cTrader crypto tickers are a closed list, so a symbol built as
#: <BASE><QUOTE> against one of these quotes is a coin, not a currency pair.
_CRYPTO_BASES = frozenset("""
BTC ETH LTC BCH XRP SOL ADA DOT DOGE AVAX LINK UNI ATOM XTZ TRX ETC XLM
ALGO AAVE NEAR FIL SAND MANA GRT LDO INJ IMX LRC COMP MASK CRV RENDER ARB
APT SUI TIA THETA FTM HBAR ICP VET ONDO PEPE SHIB WIF BONK FLOKI ASTER AERO
ENA SEI PENGU JUP WLD TON TRUMP VIRTUAL PYTH SYRUP FET DEXE GLM KSM CAKE
1INCH STORJ OCEAN CTSI YFI ANKR CELO USTC RUNE QNT NKN OMG ZRX SKL BAND
KAVA IOST AXS GALA CHZ ENS DENT BAL SXP REEF POLS TCT GMT DYDX BLZ FLUX
HIGH JOE ACH API3 ARPA MBOX MLN PROM WOO QKC SFP TROY VIB ALICE TLM PSG
JUV ATM ACM BAR LAZIO ASR ALPINE AUTO UTK XNO ZEC DASH ONE HOT
""".split())

# cTrader futures names: root + month letter + 2-digit year (GCM25, Cotton_Z6).
_FUTURES_NAME = re.compile(r"^[A-Z]{1,3}[FGHJKMNQUVXZ]\d{2}(_CFD)?$")
def _category_id(value: object) -> int | None:
    """Coerce a `symbolCategoryId` cell to int, tolerating None/blank/str."""
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def classify_by_name(symbol_name: object, category_id: object = None,
                     description: object = None) -> str:
    """Fallback classification from the raw cTrader columns.

    Used where the dimension row carries no usable category. The same signals
    are on the row (``symbolCategoryId``, the ticker and the description), so an
    uncategorised instrument is still placed sensibly rather than disappearing
    from the selector.
    """
    name = str(symbol_name or "").strip().upper()
    desc = str(description or "").strip().upper()
    if not name:
        return ""
    cat = _category_id(category_id)

    if cat in _FALLBACK_CATEGORY:
        return _FALLBACK_CATEGORY[cat]
    if any(tok in name for tok in _METAL_TOKENS):
        return "metal"
    if any(tok in name for tok in _ENERGY_TOKENS):
        return "energy"
    if any(tok in name for tok in _INDEX_TOKENS) or "INDEX" in desc:
        return "index"
    if any(tok in name for tok in _SOFT_TOKENS):
        return "commodity"
    if any(tok in name for tok in _BOND_TOKENS):
        return "bond"
    if _FUTURES_NAME.match(name) or _FUTURES_SUFFIX.match(name) or "FORWARD" in desc:
        return "future"
    if name in _CRYPTO_BASES or any(
        name.endswith(q) and name[: -len(q)] in _CRYPTO_BASES for q in _CRYPTO_QUOTES
    ):
        return "crypto"
    if " VS " in f" {desc} " or (len(name) == 6 and name.isalpha()):
        return "fx"
    return ""


def classify_asset_class(symbol_name: object, asset_class_name: object = None,
                         category_id: object = None, description: object = None) -> str:
    """Resolve one instrument to a client asset-class key.

    Prefers a broker class name when one is supplied; otherwise falls back to
    the raw cTrader columns. FX is sub-divided into majors/crosses/exotics, which
    the pipeline does not do but a dashboard benefits from. Never raises - an
    unrecognised instrument yields ``""`` and the caller groups it separately.
    """
    base = BROKER_CLASSES.get(str(asset_class_name or "").strip().lower(), "")
    if not base:
        base = classify_by_name(symbol_name, category_id, description)
    if base == "fx":
        return _fx_subclass(symbol_name)
    return base


def family_of(asset_class: str) -> str:
    """Broad family key for an asset class."""
    return CLASS_FAMILY.get(asset_class, "")


def family_label(family: str) -> str:
    """Human label for a broad family."""
    return FAMILY_LABELS.get(family, family.title())


def sort_key(asset_class: str) -> tuple[int, str]:
    """Position of a class in the canonical (liquidity-first) display order."""
    order = {key: i for i, (key, _) in enumerate(ASSET_CLASSES)}
    return order.get(asset_class, len(order)), asset_class
_FUTURES_SUFFIX = re.compile(r"^[A-Z]+_[FGHJKMNQUVXZ]\d{2}$")


