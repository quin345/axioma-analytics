"""Live depth book and the collector's row-stamping rule.

OrderBook is a port of /root/data-mining-project/collector/order_book.py:13-37 that takes
already-decoded quote lists instead of a protobuf event, so the transport layer feeds it:
`new_quotes` = iterable of (id, side, price, size) with side "bid" | "ask" (price/size as the
Open API integers), `deleted_ids` = iterable of quote ids. Best bid/ask follow
collector/aggregate.py:15-16 (top of the best-first snapshot; None when a side is empty).

Stamper applies collector/main.py:24-51: a spot event only records the latest exchange
timestamp for the symbol; a depth event always updates the book, then yields one row stamped
with that spot timestamp, or nothing when no spot has been seen yet.
"""
from __future__ import annotations

from typing import Iterable, Optional, Tuple

from .types import DepthRow

Quote = Tuple[int, str, int, int]  # (id, side, price, size)


class OrderBook:
    def __init__(self, symbol_id: int = 0):
        self.symbol_id = symbol_id
        self._quotes: dict[int, tuple[str, int, int]] = {}  # id -> (side, price, size)
        self._count = 0

    def apply(self, new_quotes: Iterable[Quote], deleted_ids: Iterable[int] = ()) -> int:
        """Apply one depth event (deletions first, as the collector does). Returns its local seq."""
        self._count += 1
        for qid in deleted_ids:
            self._quotes.pop(qid, None)
        for qid, side, price, size in new_quotes:
            if side not in ("bid", "ask"):
                raise ValueError(f"unknown quote side {side!r}")
            self._quotes[qid] = (side, price, size)
        return self._count

    def snapshot(self):
        """(bids, asks) as lists of (price, size), best price first."""
        bids = sorted(((p, s) for side, p, s in self._quotes.values() if side == "bid"), reverse=True)
        asks = sorted((p, s) for side, p, s in self._quotes.values() if side == "ask")
        return bids, asks

    def best(self) -> tuple[Optional[int], Optional[int]]:
        bids = [p for side, p, _ in self._quotes.values() if side == "bid"]
        asks = [p for side, p, _ in self._quotes.values() if side == "ask"]
        return (max(bids) if bids else None, min(asks) if asks else None)

    def clear(self) -> None:
        """Drop all quotes (e.g. after a resubscribe, when a fresh snapshot follows)."""
        self._quotes.clear()


class Stamper:
    """Per-symbol books plus the latest spot timestamp; turns depth events into DepthRows."""

    def __init__(self, source: str = "live"):
        self.source = source
        self._books: dict[int, OrderBook] = {}
        self._last_spot_ms: dict[int, int] = {}

    def on_spot(self, symbol_id: int, ts_ms: int) -> None:
        self._last_spot_ms[symbol_id] = ts_ms

    def on_depth(self, symbol_id: int, new_quotes: Iterable[Quote],
                 deleted_ids: Iterable[int] = ()) -> Optional[DepthRow]:
        book = self._books.get(symbol_id)
        if book is None:
            book = self._books[symbol_id] = OrderBook(symbol_id)
        book.apply(new_quotes, deleted_ids)
        ts = self._last_spot_ms.get(symbol_id)
        if ts is None:
            return None
        bid, ask = book.best()
        return DepthRow(symbol_id, ts, bid, ask, self.source)

    def book(self, symbol_id: int) -> Optional[OrderBook]:
        return self._books.get(symbol_id)
