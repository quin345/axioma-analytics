"""Canonical tick schema + tolerant column mapping.

Real warehouses name tick columns inconsistently (bid/BidPrice/bid_price...).
This module normalises whatever the catalog reports into one stable frame:

    ts | symbol | bid | ask | last | volume
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

CANONICAL = ["ts", "symbol", "bid", "ask", "last", "volume"]

# Ordered most-specific-first; first match wins.
ALIASES: dict[str, tuple[str, ...]] = {
    "ts": (
        "tick_timestamp", "tick_ts", "tick_time", "bar_time", "bar_ts",
        "event_time", "event_timestamp", "eventtime", "timestamp_utc", "ts_utc",
        "datetime_utc", "trade_time", "tradetime", "utc_time", "timestamp",
        "datetime", "time", "ts", "t", "date", "created_at",
    ),
    "symbol": (
        "instrument_symbol", "instrument", "symbol", "ticker", "pair", "sym_name",
        "sym", "inst",
    ),
    "bid": ("bid_price", "bidprice", "bid", "b"),
    "ask": ("askprice", "ask_price", "offer_price", "offerprice", "ask", "a"),
    "last": ("lastprice", "last_price", "tradeprice", "trade_price", "last", "price", "mid", "close"),
    "volume": ("tradesize", "trade_size", "lastsize", "last_size", "quantity", "qty", "volume", "vol", "size", "amount"),
}


def _norm(name: str) -> str:
    return str(name).strip().lower().replace(" ", "_")


@dataclass
class ColumnMap:
    """Maps canonical tick fields onto physical column names."""

    ts: str | None = None
    symbol: str | None = None
    bid: str | None = None
    ask: str | None = None
    last: str | None = None
    volume: str | None = None
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def required(self) -> list[str]:
        need = ["ts"]
        if self.bid and self.ask:
            need += ["bid", "ask"]
        elif self.last:
            need += ["last"]
        else:
            need += ["bid", "ask"]
        return need

    @property
    def valid(self) -> bool:
        return bool(self.ts) and bool((self.bid and self.ask) or self.last)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in CANONICAL}


def build_column_map(columns: list[str]) -> ColumnMap:
    """Best-effort match of warehouse columns to the canonical tick schema."""
    lowered = {_norm(c): c for c in columns if c}
    cm = ColumnMap()
    for field_name, aliases in ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                setattr(cm, field_name, lowered[alias])
                break
    matched = set(cm.to_dict().values())
    cm.extra = {k: v for k, v in lowered.items() if v not in matched}
    return cm


def normalise(df: pd.DataFrame, cm: ColumnMap, *, has_symbol: bool = True) -> pd.DataFrame:
    """Project a raw query result onto the canonical tick frame, coercing types."""
    out = pd.DataFrame()
    out["ts"] = pd.to_datetime(df[cm.ts], errors="coerce", utc=True)

    if has_symbol and cm.symbol and cm.symbol in df.columns:
        out["symbol"] = df[cm.symbol].astype("string").str.strip().str.upper()
    else:
        out["symbol"] = pd.Series(pd.NA, index=df.index, dtype="string")

    for col in ("bid", "ask", "last", "volume"):
        src = getattr(cm, col)
        out[col] = pd.to_numeric(df[src], errors="coerce") if src and src in df.columns else float("nan")

    out = out.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)

    # A tick needs at least one usable price. Prefer mid derived from bid/ask.
    mid = (out["bid"] + out["ask"]) / 2.0
    out["mid"] = mid.where(out["bid"].notna() & out["ask"].notna())
    if out["mid"].isna().all() and out["last"].notna().any():
        out["mid"] = out["last"]
    out = out.dropna(subset=["mid"])
    return out