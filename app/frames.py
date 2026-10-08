"""Reader for the per-tick order-book metrics.

``ctrader_dom.agg_dom`` (KQL) holds one row per symbol and tick with best
bid/ask, resting sizes and the pipeline's own imbalance and spread measures.
Those metrics are *derived* from a full order-book reconstruction rather than
sampled as point-in-time snapshots:

    dom_stream_raw  ->  dom_book_flat   ->  agg_dom
    (raw DOM events)    (reconstructed      (derived per-tick metrics)
                         full order book)

`from_ticks` projects ``agg_dom`` onto the canonical tick frame the analytics
engine consumes:

    ts | symbol | bid | ask | last | volume | mid
"""
from __future__ import annotations

import numpy as np
import pandas as pd

#: Columns of the canonical tick frame produced by `from_ticks`.
TICK_COLUMNS = [
    "ts", "symbol", "bid", "ask", "last", "volume",
    "bid_depth", "ask_depth", "signed_volume",
    "imbalance", "imbalance_ratio",
    "vwap_bid", "vwap_ask", "vwap_spread", "rel_spread", "rel_vwap_spread",
]

# Resting sizes arrive in units of 100 lots; canonical volume is in lots.
VOLUME_SCALE = 100.0


def from_ticks(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalise raw tick-metric rows into the canonical tick frame.

    The KQL column names are identical to the old SQL table's, so only the
    transport changed; this projection is shape-only.

    Rows missing a best bid or ask are one-sided books: they have no mid price,
    so they are dropped rather than faked. A crossed book (bid >= ask) is
    likewise discarded as a desynchronised tick - *unless* most of the frame
    is crossed, which means the feed itself is mirrored rather than the rows
    being out of sync: the pipeline then has best_bid/best_ask the wrong way
    round (its own vwap quotes stay correctly oriented), and dropping would
    empty the whole window. Mirrored frames are repaired by swapping the quotes
    back; the mid price is identical either way, only the spread sign changes,
    which is exactly what a mirrored feed gets wrong.
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
    # Top-of-book resting size is the only "volume" a tick metric guarantees.
    # The feed reports it in units of 100 lots, so scale to lots before
    # anything downstream (KPI totals, volume profile, imbalance) reads it.
    out["bid_depth"] = bid_depth
    out["ask_depth"] = ask_depth
    out["volume"] = (bid_depth.fillna(0.0) + ask_depth.fillna(0.0)) / VOLUME_SCALE
    out["last"] = (out["bid"] + out["ask"]) / 2.0

    for name in ("imbalance", "imbalance_ratio", "vwap_bid", "vwap_ask",
                 "vwap_spread", "rel_spread", "rel_vwap_spread"):
        src = raw.get(name)
        out[name] = pd.to_numeric(src, errors="coerce") if src is not None else np.nan

    out = out.dropna(subset=["ts", "symbol", "bid", "ask"])
    out = out[(out["bid"] > 0) & (out["ask"] > 0)].copy()

    # Majority-crossed frame => the quotes arrived mirrored. Swap them back
    # (and the pipeline's own relative spread with them) instead of throwing
    # the window away; a genuine one-off crossed tick is still a minority
    # here and is dropped below as before.
    crossed = out["bid"] > out["ask"]
    if len(out) and bool(crossed.mean() > 0.5):
        mirrored = crossed.to_numpy()
        original_bid = out.loc[mirrored, "bid"].to_numpy(copy=True)
        out.loc[mirrored, "bid"] = out.loc[mirrored, "ask"].to_numpy()
        out.loc[mirrored, "ask"] = original_bid
        out.loc[mirrored, "rel_spread"] = -out.loc[mirrored, "rel_spread"]

    out = out[out["ask"] > out["bid"]]
    out = out.sort_values("ts").reset_index(drop=True)
    if out.empty:
        return pd.DataFrame(columns=TICK_COLUMNS)

    # Directional signal: prefer the pipeline's own imbalance, else derive it
    # from the resting sizes.
    derived = out["bid_depth"].fillna(0.0) - out["ask_depth"].fillna(0.0)
    out["signed_volume"] = np.where(out["imbalance"].notna(), out["imbalance"], derived)
    return out[TICK_COLUMNS]