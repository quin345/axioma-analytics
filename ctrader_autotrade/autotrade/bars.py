"""10 s buckets and 5-minute bars, built with the webapp/export_flat functions themselves.

Bucketiser reproduces queries.series_range (webapp/queries.py:154-166): per 10 s bin the last
book state (arg_max over timestamp) and events = count(). BarBuilder reproduces
export_flat.build_rows (export_flat.py:192-205) for one slot: the half-open slot [t-5m, t)
keyed on t feeds `export_flat.slot_metrics` (bucket-last mids, kpis/spread via analytics) and
the 240 min window [t-240m, t) feeds `export_flat.vap_metrics` -> analytics.volume_at_price /
value_area (webapp/analytics.py:300-425). Prices are scaled /100000 to display units exactly
as export_flat._scaled does.
"""
from __future__ import annotations

import math
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from ._research import export_flat as _ef
from .types import Bar, Bucket, DepthRow

BUCKET_MS = _ef.BIN_SECONDS * 1000
SLOT_MS = _ef.SLOT_MINUTES * 60 * 1000
VAP_MINUTES = 240
PRICE_SCALE = 100000
# 240 min of VAP + the slot being closed + one slot of slack for the open bucket / late closing.
RETAIN_MS = (VAP_MINUTES + 2 * _ef.SLOT_MINUTES) * 60 * 1000
NAN = float("nan")
# Buckets built from every depth event: the bot's own feed or the KQL agg_dom table. "tick" (spot
# changes) and "store" (reloaded own buckets) are approximate and count toward Bar.approx_share.
EXACT_SOURCES = frozenset({"live", "kql"})


class Bucketiser:
    """Aggregates DepthRows into 10 s Buckets per symbol."""

    def __init__(self, retain_ms: int = RETAIN_MS):
        self.retain_ms = retain_ms
        self._b: dict[int, "OrderedDict[int, Bucket]"] = {}
        self._last_stamp: dict[tuple[int, int], int] = {}  # (symbol, bucket start) -> stamp of held book

    def on_row(self, row: DepthRow) -> None:
        start = row.stamp_ms - row.stamp_ms % BUCKET_MS
        d = self._b.setdefault(row.symbol_id, OrderedDict())
        cur = d.get(start)
        key = (row.symbol_id, start)
        if cur is None:
            d[start] = Bucket(row.symbol_id, start, row.best_bid, row.best_ask, 1, row.source)
            self._last_stamp[key] = row.stamp_ms
            if len(d) > 1 and next(reversed(d)) != start:
                d2 = OrderedDict(sorted(d.items()))  # late bucket: keep ordered
                d.clear(); d.update(d2)
            self._prune(row.symbol_id)
        elif row.stamp_ms >= self._last_stamp[key]:  # arg_max(timestamp): latest stamp wins, ties to the later row
            d[start] = Bucket(row.symbol_id, start, row.best_bid, row.best_ask, cur.events + 1, row.source)
            self._last_stamp[key] = row.stamp_ms
        else:
            d[start] = Bucket(row.symbol_id, start, cur.best_bid, cur.best_ask, cur.events + 1, cur.source)

    def add_ordered(self, b: Bucket) -> None:
        """Warm-up replay: append a finished bucket that is not older than the held ones; prunes."""
        d = self._b.setdefault(b.symbol_id, OrderedDict())
        d[b.start_ms] = b
        self._last_stamp[(b.symbol_id, b.start_ms)] = b.start_ms + BUCKET_MS - 1
        self._prune(b.symbol_id)

    def _prune(self, symbol_id: int) -> None:
        d = self._b[symbol_id]
        cutoff = next(reversed(d)) - self.retain_ms
        while d and next(iter(d)) < cutoff:
            old, _ = d.popitem(last=False)
            self._last_stamp.pop((symbol_id, old), None)

    def load(self, buckets: Iterable[Bucket]) -> None:
        """Warm-up: insert finished buckets (stored or tick-built); replaces same-start buckets."""
        touched = set()
        for b in buckets:
            d = self._b.setdefault(b.symbol_id, OrderedDict())
            d[b.start_ms] = b
            self._last_stamp[(b.symbol_id, b.start_ms)] = b.start_ms + BUCKET_MS - 1
            touched.add(b.symbol_id)
        for s in touched:
            d = self._b[s]
            items = sorted(d.items())
            d.clear(); d.update(items)
            self._prune(s)

    def buckets_since(self, symbol_id: int, start_ms: int) -> list[Bucket]:
        return [b for b in self._b.get(symbol_id, {}).values() if b.start_ms >= start_ms]

    def buckets_between(self, symbol_id: int, start_ms: int, end_ms: int) -> list[Bucket]:
        """Buckets with start in [start_ms, end_ms)."""
        return [b for b in self._b.get(symbol_id, {}).values() if start_ms <= b.start_ms < end_ms]

    def last_start_ms(self, symbol_id: int) -> Optional[int]:
        d = self._b.get(symbol_id)
        return next(reversed(d)) if d else None


def _rows(buckets: Iterable[Bucket]) -> list[dict]:
    """Series rows in the shape export_flat expects (raw prices, epoch_ms = bucket start)."""
    return [{"bucket": b.start_ms, "epoch_ms": float(b.start_ms), "best_bid": b.best_bid,
             "best_ask": b.best_ask, "events": b.events} for b in buckets]


def _f(x) -> float:
    return NAN if x is None else float(x)


def slot_bar(symbol_id: int, slot_end: datetime, buckets: list[Bucket],
             data_start: Optional[datetime] = None) -> Bar:
    """Bar for the slot ending at `slot_end` from buckets covering [slot_end-240m, slot_end).

    `data_start` is the earliest moment the data was read from (export_flat's `data_start`): the
    window share after it is the bar's vap_coverage. None = the window is complete (live bars).
    """
    end_ms = slot_end.timestamp() * 1000
    start = slot_end - timedelta(minutes=_ef.SLOT_MINUTES)
    raw = _rows(buckets)
    slot = _ef._scaled(_ef._between(raw, start, slot_end), PRICE_SCALE)
    m = _ef.slot_metrics(slot, [])
    vstart = slot_end - timedelta(minutes=VAP_MINUTES)
    vap_rows = _ef._between(raw, vstart, slot_end)
    v = _ef.vap_metrics(vap_rows, slot_end, PRICE_SCALE, vstart if data_start is None else data_start)
    window = [b for b in buckets if end_ms - VAP_MINUTES * 60_000 <= b.start_ms < end_ms]
    approx = (sum(1 for b in window if b.source not in EXACT_SOURCES) / len(window)) if window else 0.0
    return Bar(
        symbol_id=symbol_id, slot_end=slot_end, has_data=bool(m["has_data"]), events=int(m["events"]),
        mid_open=_f(m["mid_open"]), mid_high=_f(m["mid_high"]), mid_low=_f(m["mid_low"]),
        mid_close=_f(m["mid_close"]), spread_mean=_f(m["spread_mean"]),
        spread_median=_f(m["spread_median"]), rel_spread_mean=_f(m["rel_spread_mean"]),
        realised_vol_pct=_f(m["realised_vol_pct"]),
        vap_240m_poc=_f(v["vap_240m_poc"]), vap_240m_va_low=_f(v["vap_240m_va_low"]),
        vap_240m_va_high=_f(v["vap_240m_va_high"]),
        vap_240m_volume_events=_f(v["vap_240m_volume_events"]), approx_share=approx,
        vap_coverage=float(v["vap_240m_coverage"]))


class BarBuilder:
    def __init__(self, bucketiser: Bucketiser, data_start: Optional[datetime] = None):
        self.bucketiser = bucketiser
        self.data_start = data_start  # warm-up replays only: start of the fetched history

    def close_slot(self, symbol_id: int, slot_end: datetime) -> Bar:
        if slot_end.tzinfo is None:
            slot_end = slot_end.replace(tzinfo=timezone.utc)
        end_ms = int(slot_end.timestamp() * 1000)
        buckets = self.bucketiser.buckets_between(
            symbol_id, end_ms - VAP_MINUTES * 60_000, end_ms)
        return slot_bar(symbol_id, slot_end, buckets, self.data_start)


def replay_bars(symbol_id: int, buckets: Iterable[Bucket], first_end_ms: int, last_end_ms: int,
                data_start_ms: Optional[int] = None) -> list[Bar]:
    """Warm-up bars for slot ends first_end_ms..last_end_ms, built as live operation would.

    Buckets are replayed in time order and each 5 min slot is closed once replay time reaches its
    slot_end, so every bar sees its whole [t-240m, t) window; the Bucketiser prunes only buckets
    older than the retention (240 min + two slots) behind the replay position. Buckets before
    `first_end_ms` still enter the replay: they are the history of the first windows.
    """
    bz = Bucketiser()
    builder = BarBuilder(bz, None if data_start_ms is None else _utc_ms(data_start_ms))
    bars: list[Bar] = []
    nxt = first_end_ms
    for b in sorted(buckets, key=lambda x: x.start_ms):
        while nxt <= last_end_ms and nxt <= b.start_ms:
            bars.append(builder.close_slot(symbol_id, _utc_ms(nxt)))
            nxt += SLOT_MS
        bz.add_ordered(b)
    while nxt <= last_end_ms:
        bars.append(builder.close_slot(symbol_id, _utc_ms(nxt)))
        nxt += SLOT_MS
    return bars


def _utc_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
