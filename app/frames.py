"""Reader for the gold aggregate book snapshots.

`gold.agg_dom_book_snapshot` holds one row per symbol and timestamp with best
bid/ask, resting sizes and the pipeline's own imbalance and spread measures.
`from_snapshot` projects it onto the canonical tick frame the analytics engine
consumes:

    ts | symbol | bid | ask | last | volume | mid
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .db import TICK_COLUMNS


def from_snapshot(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalise raw gold snapshots into the canonical tick frame.

    Rows missing a best bid or ask are one-sided books: they have no mid price,
    so they are dropped rather than faked. A crossed book (bid >= ask) is
    likewise discarded as a desynchronised snapshot.
    """
    if raw is None or raw.empty:
        return pd.DataFrame(columns=TICK_COLUMNS)

    out = pd.DataFrame()
    out["ts"] = pd.to_datetime(raw["timestamp"], errors="coerce", utc=True)
    out["symbol"] = raw["symbolId"].astype("string").str.strip()
    out["bid"] = pd.to_numeric(raw["best_bid"], errors="coerce")
    out["ask"] = pd.to_numeric(raw["best_ask"], errors="coerce")

    bid_depth = pd.to_numeric(raw.get("total_bid"), errors="coerce")
    ask_depth = pd.to_numeric(raw.get("total_ask"), errors="coerce")
    # Top-of-book resting size is the only "volume" a snapshot guarantees.
    out["bid_depth"] = bid_depth
    out["ask_depth"] = ask_depth
    out["volume"] = bid_depth.fillna(0.0) + ask_depth.fillna(0.0)
    out["last"] = (out["bid"] + out["ask"]) / 2.0

    for name in ("imbalance", "imbalance_ratio", "vwap_bid", "vwap_ask",
                 "vwap_spread", "rel_spread", "rel_vwap_spread"):
        src = raw.get(name)
        out[name] = pd.to_numeric(src, errors="coerce") if src is not None else np.nan

    out = out.dropna(subset=["ts", "symbol", "bid", "ask"])
    out = out[(out["bid"] > 0) & (out["ask"] > 0) & (out["ask"] > out["bid"])]
    out = out.sort_values("ts").reset_index(drop=True)
    if out.empty:
        return pd.DataFrame(columns=TICK_COLUMNS)

    # Directional signal: prefer the pipeline's own imbalance, else derive it
    # from the resting sizes.
    derived = out["bid_depth"].fillna(0.0) - out["ask_depth"].fillna(0.0)
    out["signed_volume"] = np.where(out["imbalance"].notna(), out["imbalance"], derived)
    return out[TICK_COLUMNS]