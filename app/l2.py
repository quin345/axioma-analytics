"""cTrader L2 depth-of-market (DOM) reconstruction.

The warehouse stores *incremental book events*, not flat quotes:

    symbolId | newQuotes | deletedQuotes | digits | timestamp

where `newQuotes` is a JSON array of `{"id","size","bid"|"ask"}` and
`deletedQuotes` is a JSON array of quote ids. Prices are scaled integers that
must be divided by `10 ** digits`.

This module replays those deltas to rebuild the book per symbol and emits a
canonical tick stream (best bid / best ask / mid / spread / depth), which the
rest of the analytics pipeline consumes unchanged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .schema import CANONICAL

L2_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("symbolid", "symbol_id", "symbol"),
    "new_quotes": ("newquotes", "new_quotes"),
    "deleted_quotes": ("deletedquotes", "deleted_quotes"),
    "digits": ("digits", "decimalplaces", "decimal_places", "digits_no"),
    "ts": ("timestamp", "ts", "eventtime", "event_time", "_ts"),
}


def _norm(name: str) -> str:
    return str(name).strip().lower().replace(" ", "_")


def map_l2_columns(columns: list[str]) -> dict[str, str]:
    """Map warehouse columns onto the L2 event fields."""
    lowered = {_norm(c): c for c in columns if c}
    out: dict[str, str] = {}
    for field_name, aliases in L2_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                out[field_name] = lowered[alias]
                break
    return out


def is_l2_table(columns: list[str]) -> bool:
    """True when the table looks like a cTrader DOM event stream."""
    m = map_l2_columns(columns)
    return bool(m.get("ts") and m.get("new_quotes") and m.get("symbol"))


@dataclass
class L2Options:
    """Reconstruction tuning."""
    top_levels: int = 5        # levels per side included in the depth totals
    min_book_size: float = 0.0
    # The cTrader feed stops overnight and restarts with fresh quote ids. Replaying
    # across that boundary leaves stale levels behind and produces crossed books,
    # so the book is discarded whenever a symbol goes quiet for this long.
    session_gap_seconds: float = 1800.0


def _parse_quotes(raw: object) -> list[dict]:
    """Parse one `newQuotes` cell into a list of quote dicts."""
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return []
    if isinstance(raw, list):
        return raw
    text = str(raw).strip()
    if not text or text in ("[]", "null", "None"):
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else [parsed]


def _parse_ids(raw: object) -> list[str]:
    """Parse one `deletedQuotes` cell into a list of quote ids."""
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    text = str(raw).strip()
    if not text or text in ("[]", "null", "None"):
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    return [str(x) for x in parsed] if isinstance(parsed, list) else []


# Replay counters from the most recent reconstruct() call, for diagnostics.
_LAST_STATS: dict = {}


def reconstruct(raw: pd.DataFrame, options: L2Options | None = None) -> pd.DataFrame:
    """Replay DOM events into a canonical tick frame.

    Emits: ts | symbol | bid | ask | last | volume | mid, plus the book
    diagnostics (book_size, bid_depth, ask_depth, signed_volume).
    """
    opts = options or L2Options()
    m = map_l2_columns(raw.columns)
    if not (m.get("ts") and m.get("new_quotes")):
        raise ValueError("DataFrame does not carry the L2 event columns")

    df = pd.DataFrame(index=raw.index)
    df["ts"] = pd.to_datetime(raw[m["ts"]], errors="coerce", utc=True)
    df["symbol"] = (raw[m["symbol"]].astype("string") if m.get("symbol")
                    else pd.Series("?", index=raw.index))
    df["digits"] = (pd.to_numeric(raw[m["digits"]], errors="coerce").fillna(2.0)
                    if m.get("digits") else 2.0)
    df = df.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)

    new_col = raw[m["new_quotes"]].reset_index(drop=True)
    del_col = (raw[m["deleted_quotes"]].reset_index(drop=True)
               if m.get("deleted_quotes") else None)

    extra_cols = CANONICAL + ["book_events", "book_size", "bid_depth", "ask_depth"]
    ticks: list[dict] = []
    # One book per symbol; events are replayed in global timestamp order.
    books: dict[str, dict[str, tuple[str, float, float]]] = {}
    last_seen: dict[str, pd.Timestamp] = {}
    stats = {"events": 0, "crossed": 0, "one_sided": 0, "resets": 0}

    for i, row in enumerate(df.itertuples(index=False)):
        symbol = str(row.symbol)
        scale = 10.0 ** float(row.digits)
        stats["events"] += 1

        prev = last_seen.get(symbol)
        if prev is not None and (row.ts - prev).total_seconds() > opts.session_gap_seconds:
            # New session for this symbol - stale levels from the old one are invalid.
            books.pop(symbol, None)
            stats["resets"] += 1
        last_seen[symbol] = row.ts

        book = books.setdefault(symbol, {})

        if del_col is not None:
            for qid in _parse_ids(del_col.iloc[i]):
                book.pop(qid, None)

        quotes = _parse_quotes(new_col.iloc[i])
        for q in quotes:
            if not isinstance(q, dict):
                continue
            qid = str(q.get("id", ""))
            if not qid:
                continue
            side = "bid" if q.get("bid") is not None else "ask"
            try:
                price = float(q.get(side)) / scale
                size = float(q.get("size") or 0.0)
            except (TypeError, ValueError):
                continue
            if size <= opts.min_book_size:
                book.pop(qid, None)        # a zero-size update removes the quote
            else:
                book[qid] = (side, price, size)

        if not book:
            continue
        bids = [(p, s) for _, (sd, p, s) in book.items() if sd == "bid"]
        asks = [(p, s) for _, (sd, p, s) in book.items() if sd == "ask"]
        if not bids or not asks:
            stats["one_sided"] += 1
            continue

        best_bid, best_bid_size = max(bids, key=lambda x: x[0])
        best_ask, best_ask_size = min(asks, key=lambda x: x[0])
        if best_bid <= 0 or best_ask <= 0:
            continue
        if best_bid >= best_ask:
            # A genuinely crossed book means our replay lost sync; never emit it.
            stats["crossed"] += 1
            continue

        # Depth over the top N levels = resting size at the touch of the book.
        n = max(1, opts.top_levels)
        bid_depth = sum(s for _, s in sorted(bids, reverse=True)[:n])
        ask_depth = sum(s for _, s in sorted(asks)[:n])

        ticks.append({
            "ts": row.ts,
            "symbol": symbol,
            "bid": best_bid,
            "ask": best_ask,
            "last": (best_bid + best_ask) / 2.0,
            "volume": float(best_bid_size + best_ask_size),
            "book_events": len(quotes),
            "book_size": len(book),
            "bid_depth": float(bid_depth),
            "ask_depth": float(ask_depth),
        })

    if not ticks:
        return pd.DataFrame(columns=extra_cols + ["mid", "spread", "signed_volume"])

    global _LAST_STATS
    _LAST_STATS = dict(stats)
    stats["ticks"] = len(ticks)
    _LAST_STATS["ticks"] = len(ticks)

    out = pd.DataFrame(ticks)
    out["mid"] = (out["bid"] + out["ask"]) / 2.0
    out["spread"] = out["ask"] - out["bid"]
    # Book imbalance: the L2-native directional signal (positive = bid heavy).
    out["signed_volume"] = out["bid_depth"] - out["ask_depth"]
    return out.reset_index(drop=True)


def last_stats() -> dict:
    """Replay counters from the most recent `reconstruct` call (diagnostics)."""
    return dict(_LAST_STATS)
    return [str(x) for x in parsed] if isinstance(parsed, list) else []