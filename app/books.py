"""Readers for the cTrader medallion tables in `ctrader_lakehouse`.

Three shapes are supported, all normalised to the canonical tick frame so the
analytics engine stays unchanged:

`agg`    - gold.agg_dom_book_snapshot: one pre-aggregated row per symbol/time
           with best_bid/best_ask, total sizes, imbalance and VWAP spreads.
`levels` - dbo.silver_dom_book_snapshot: one row per resting price level
           (symbolId, quoteId, timestamp, side, price, size), collapsed here into
           per-timestamp book snapshots.
`l2`     - raw newQuotes/deletedQuotes event deltas (see app/l2.py).
`flat`   - conventional ts/bid/ask/last/volume tick columns.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .schema import CANONICAL

# --------------------------------------------------------------------------
# gold.agg_dom_book_snapshot
# --------------------------------------------------------------------------

AGG_ALIASES: dict[str, tuple[str, ...]] = {
    "ts": ("timestamp", "ts", "eventtime", "event_time"),
    "symbol": ("symbolid", "symbol_id", "symbol"),
    "best_bid": ("best_bid", "bid", "bestbid"),
    "best_ask": ("best_ask", "ask", "bestask"),
    "total_bid": ("total_bid", "bid_size", "size_bid"),
    "total_ask": ("total_ask", "ask_size", "size_ask"),
    "imbalance": ("imbalance",),
    "imbalance_ratio": ("imbalance_ratio",),
    "vwap_bid": ("vwap_bid",),
    "vwap_ask": ("vwap_ask",),
    "vwap_spread": ("vwap_spread",),
    "rel_spread": ("rel_spread", "spread_rel"),
    "rel_vwap_spread": ("rel_vwap_spread",),
}


_LEVEL_COLUMNS = CANONICAL + ["bid_depth", "ask_depth", "levels_bid", "levels_ask",
                              "bid_curve", "ask_curve"]


def _map(columns: list[str], aliases: dict[str, tuple[str, ...]]) -> dict[str, str]:
    lowered = {str(c).strip().lower(): c for c in columns if c}
    out: dict[str, str] = {}
    for field_name, names in aliases.items():
        for alias in names:
            if alias in lowered:
                out[field_name] = lowered[alias]
                break
    return out


def map_agg_columns(columns: list[str]) -> dict[str, str]:
    return _map(columns, AGG_ALIASES)


def is_agg_table(columns: list[str]) -> bool:
    m = map_agg_columns(columns)
    return bool(m.get("ts") and m.get("symbol") and m.get("best_bid") and m.get("best_ask"))


def from_agg(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalise the aggregated book snapshot into canonical ticks.

    Rows missing a best bid or ask are one-sided books and cannot produce a mid
    price, so they are dropped rather than faked.
    """
    m = map_agg_columns(raw.columns)
    out = pd.DataFrame()
    out["ts"] = pd.to_datetime(raw[m["ts"]], errors="coerce", utc=True)
    out["symbol"] = raw[m["symbol"]].astype("string").str.strip()
    out["bid"] = pd.to_numeric(raw[m["best_bid"]], errors="coerce")
    out["ask"] = pd.to_numeric(raw[m["best_ask"]], errors="coerce")

    tb = pd.to_numeric(raw[m["total_bid"]], errors="coerce") if m.get("total_bid") else np.nan
    ta = pd.to_numeric(raw[m["total_ask"]], errors="coerce") if m.get("total_ask") else np.nan
    # Top-of-book size is the only "volume" a snapshot guarantees.
    out["volume"] = tb.fillna(0.0) + ta.fillna(0.0)
    out["bid_depth"] = tb
    out["ask_depth"] = ta
    out["last"] = (out["bid"] + out["ask"]) / 2.0

    for field_name in ("imbalance", "imbalance_ratio", "vwap_bid", "vwap_ask",
                       "vwap_spread", "rel_spread", "rel_vwap_spread"):
        src = m.get(field_name)
        out[field_name] = pd.to_numeric(raw[src], errors="coerce") if src else np.nan

    out = out.dropna(subset=["ts", "bid", "ask"]).sort_values("ts").reset_index(drop=True)
    out = out[out["bid"] > 0].reset_index(drop=True)
    if out.empty:
        return pd.DataFrame(columns=CANONICAL + ["bid_depth", "ask_depth", "imbalance",
                                                 "imbalance_ratio", "vwap_bid", "vwap_ask",
                                                 "vwap_spread", "rel_spread",
                                                 "rel_vwap_spread", "signed_volume"])
    # Directional signal: prefer the pipeline's own imbalance, else derive it.
    derived = out["bid_depth"].fillna(0.0) - out["ask_depth"].fillna(0.0)
    out["signed_volume"] = np.where(out["imbalance"].notna(), out["imbalance"], derived)
    return out
# --------------------------------------------------------------------------
# dbo.silver_dom_book_snapshot  (one row per resting price level)
# --------------------------------------------------------------------------

LEVEL_ALIASES: dict[str, tuple[str, ...]] = {
    "ts": ("timestamp", "ts", "eventtime", "event_time"),
    "symbol": ("symbolid", "symbol_id", "symbol"),
    "side": ("side", "quote_side", "direction"),
    "price": ("price", "quote_price", "level_price"),
    "size": ("size", "quote_size", "level_size", "volume"),
    "quote_id": ("quoteid", "quote_id"),
}


def map_level_columns(columns: list[str]) -> dict[str, str]:
    return _map(columns, LEVEL_ALIASES)


def is_levels_table(columns: list[str]) -> bool:
    m = map_level_columns(columns)
    return bool(m.get("ts") and m.get("symbol") and m.get("side")
                and m.get("price") and m.get("size"))


def from_levels(raw: pd.DataFrame, *, depth_levels: int = 10) -> pd.DataFrame:
    """Collapse per-level rows into one book snapshot per (symbol, timestamp).

    Produces canonical ticks plus depth-curve columns, so the UI can show how
    much size rests at each distance from the touch.
    """
    m = map_level_columns(raw.columns)
    d = pd.DataFrame()
    d["ts"] = pd.to_datetime(raw[m["ts"]], errors="coerce", utc=True)
    d["symbol"] = raw[m["symbol"]].astype("string").str.strip()
    d["side"] = raw[m["side"]].astype("string").str.strip().str.lower()
    d["price"] = pd.to_numeric(raw[m["price"]], errors="coerce")
    d["size"] = pd.to_numeric(raw[m["size"]], errors="coerce")
    d = d.dropna(subset=["ts", "symbol", "price", "size"])
    d = d[d["price"] > 0].copy()
    if d.empty:
        return pd.DataFrame(columns=_LEVEL_COLUMNS)

    is_bid = d["side"].eq("bid")
    is_ask = d["side"].eq("ask")
    d = d[is_bid | is_ask]

    n = max(1, int(depth_levels))
    rows: list[dict] = []
    for (symbol, ts), chunk in d.groupby(["symbol", "ts"], sort=True):
        bids = chunk[is_bid.loc[chunk.index]]
        asks = chunk[is_ask.loc[chunk.index]]
        if bids.empty or asks.empty:
            continue                      # one-sided book: no mid, so no tick

        best_bid = float(bids["price"].max())
        best_ask = float(asks["price"].min())
        if best_bid >= best_ask:
            continue                      # crossed snapshot: desynced, skip it

        top_bids = bids.nlargest(n, "price")
        top_asks = asks.nsmallest(n, "price")
        rows.append({
            "ts": ts,
            "symbol": symbol,
            "bid": best_bid,
            "ask": best_ask,
            "last": (best_bid + best_ask) / 2.0,
            "volume": float(top_bids["size"].sum() + top_asks["size"].sum()),
            "bid_depth": float(top_bids["size"].sum()),
            "ask_depth": float(top_asks["size"].sum()),
            "levels_bid": int(len(bids)),
            "levels_ask": int(len(asks)),
            # Cumulative size at each level distance from the touch.
            "bid_curve": top_bids.sort_values("price", ascending=False)["size"].cumsum().tolist(),
            "ask_curve": top_asks.sort_values("price")["size"].cumsum().tolist(),
        })

    if not rows:
        return pd.DataFrame(columns=_LEVEL_COLUMNS)
    out = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
    out["signed_volume"] = out["bid_depth"] - out["ask_depth"]
    return out


def classify(columns: list[str]) -> str:
    """Reader kind for a set of columns: agg | levels | l2 | flat | ''."""
    from .l2 import is_l2_table

    if is_agg_table(columns):
        return "agg"
    if is_levels_table(columns):
        return "levels"
    if is_l2_table(columns):
        return "l2"
    return "flat"
    return out