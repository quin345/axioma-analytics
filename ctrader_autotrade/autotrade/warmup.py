"""Warm-up: reload stored buckets, gap-fill from ProtoOAGetTickDataReq (DESIGN.md section 4).

Tick decoding: ProtoOATickData rows are delta encoded; the first row of a response is absolute
(timestamp ms, price in 1/100000) and every later row is a delta from the row before it, for both
fields. Rows are accumulated, then sorted by time. Tick data counts spot changes, not depth
events, so rows carry source="tick".
"""
from __future__ import annotations

import dataclasses
import logging
from collections import deque
from typing import Callable, Optional

from twisted.internet import defer
from twisted.internet.defer import inlineCallbacks, returnValue

from ctrader_open_api.messages import OpenApiMessages_pb2 as oa
from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model

from .types import Bucket, DepthRow

log = logging.getLogger(__name__)

BUCKET_MS = 10_000
GAP_MIN_MS = 60_000      # stored buckets further apart than this leave a gap to fill
HIST_PER_SECOND = 5
MAX_PAGES = 200


class RateLimiter:
    """At most `per_second` acquisitions in any 1 s window, using the injected clock."""

    def __init__(self, clock, per_second: int = HIST_PER_SECOND):
        self._clock = clock
        self._n = per_second
        self._times: deque[int] = deque()

    def acquire(self) -> "defer.Deferred":
        d: defer.Deferred = defer.Deferred()
        self._try(d)
        return d

    def _try(self, d):
        now = self._clock.now_ms()
        while self._times and self._times[0] <= now - 1000:
            self._times.popleft()
        if len(self._times) < self._n:
            self._times.append(now)
            d.callback(None)
        else:
            wait = (self._times[0] + 1000 - now) / 1000
            self._clock.call_later(max(wait, 0.001), self._try, d)


def _limiter(session) -> RateLimiter:
    if session.hist_limiter is None:
        session.hist_limiter = RateLimiter(session._clock)
    return session.hist_limiter


def decode_ticks(rows) -> list[tuple[int, int]]:
    """Delta-decode one response's tickData into [(ts_ms, price)] sorted by time."""
    out, ts, px = [], 0, 0
    for i, row in enumerate(rows):
        if i == 0:
            ts, px = row.timestamp, row.tick
        else:
            ts += row.timestamp
            px += row.tick
        out.append((ts, px))
    out.sort()
    return out


@inlineCallbacks
def _fetch_side(session, symbol_id: int, quote_type: int, from_ms: int, to_ms: int):
    got: set[tuple[int, int]] = set()
    cur_to = to_ms
    for _ in range(MAX_PAGES):
        yield _limiter(session).acquire()
        req = oa.ProtoOAGetTickDataReq(symbolId=symbol_id, type=quote_type,
                                       fromTimestamp=from_ms, toTimestamp=cur_to)
        res = yield session.request(req, timeout=15)
        page = decode_ticks(res.tickData)
        got.update(page)
        if not res.hasMore or not page:
            break
        earliest = page[0][0]
        if earliest >= cur_to:  # no progress (a whole page in one millisecond)
            log.warning("tick paging stalled at %d for symbol %d", earliest, symbol_id)
            break
        cur_to = earliest  # inclusive bound; exact duplicates are dropped by the set
    else:
        log.warning("tick paging hit %d pages for symbol %d", MAX_PAGES, symbol_id)
    returnValue(sorted(got))


@inlineCallbacks
def fetch_ticks(session, symbol_id: int, from_ms: int, to_ms: int):
    """BID and ASK ticks in [from_ms, to_ms], paged backwards while hasMore.

    Fires with (bids, asks), each an ascending list of (ts_ms, price).
    """
    bids = yield _fetch_side(session, symbol_id, model.BID, from_ms, to_ms)
    asks = yield _fetch_side(session, symbol_id, model.ASK, from_ms, to_ms)
    returnValue((bids, asks))


def merge_ticks(bids, asks, symbol_id: int) -> list[DepthRow]:
    """One DepthRow per tick change, time ordered, carrying the other side's last price."""
    events = [(ts, 0, px) for ts, px in bids] + [(ts, 1, px) for ts, px in asks]
    events.sort(key=lambda e: (e[0], e[1]))
    bid: Optional[int] = None
    ask: Optional[int] = None
    rows = []
    for ts, side, px in events:
        if side == 0:
            bid = px
        else:
            ask = px
        rows.append(DepthRow(symbol_id, ts, bid, ask, "tick"))
    return rows


def plan_warmup(symbol_ids, load_buckets: Callable[[int, int], list], now_ms: int, hours: float):
    """Per symbol: (stored buckets since the window start, gaps [(from_ms, to_ms)] to fill).

    A gap is a stretch longer than GAP_MIN_MS without a stored bucket, including the head of the
    window and the tail up to now_ms.
    """
    since = now_ms - int(hours * 3600_000)
    plan = {}
    for sid in symbol_ids:
        stored = sorted(load_buckets(sid, since), key=lambda b: b.start_ms)
        gaps = []
        cursor = since
        for b in stored:
            if b.start_ms - cursor > GAP_MIN_MS:
                gaps.append((cursor, b.start_ms))
            cursor = max(cursor, b.start_ms + BUCKET_MS)
        if now_ms - cursor > GAP_MIN_MS:
            gaps.append((cursor, now_ms))
        plan[sid] = (stored, gaps)
    return plan


@inlineCallbacks
def warmup(session, symbol_ids, load_buckets, now_ms: int, hours: Optional[float] = None):
    """Stored buckets (source "store") plus tick-filled gaps (source "tick"), time ordered per symbol."""
    hours = session.settings.warmup_hours if hours is None else hours
    plan = plan_warmup(symbol_ids, load_buckets, now_ms, hours)
    out: dict[int, list] = {}
    for sid, (stored, gaps) in plan.items():
        items: list = [b if b.source == "store" else dataclasses.replace(b, source="store")
                       for b in stored]
        for g_from, g_to in gaps:
            bids, asks = yield fetch_ticks(session, sid, g_from, g_to)
            items.extend(r for r in merge_ticks(bids, asks, sid) if g_from <= r.stamp_ms < g_to)
        items.sort(key=lambda x: x.start_ms if isinstance(x, Bucket) else x.stamp_ms)
        out[sid] = items
    returnValue(out)
