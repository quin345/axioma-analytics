"""Pure strategy decision (DESIGN.md section 2). No I/O and no clock reads: time is passed in.

Price integers are Open API units (1/100000 of a price unit). Bar-level volatility is the
research `realised_vol_pct` (annualised percent of 10 s mid returns).
"""
from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence

from .types import (ExecMode, FeatureRow, Intent, IntentKind, PositionView, Quote, Side,
                    SymbolSpec)

PRICE_SCALE = 100_000
SLOT_MS = 5 * 60 * 1000
SECONDS_PER_YEAR = 31_536_000
BARS_10S_PER_SLOT = 30
VOL_BARS = 12
MIN_VOL_BARS = 6                # fewer valid vol samples than this -> fallback stop
FALLBACK_STOP_BP = 60.0         # used when vol history or its inputs are missing
MAX_STOP_BP = 200.0             # cap so a vol spike cannot push the stop absurdly far
SPREAD_STOP_MULT = 3.0


def signal_id(symbol_id: int, slot_end_ms: int) -> str:
    return f"{symbol_id}-{slot_end_ms // 1000}"


def next_exit_at_ms(slot_end_ms: int, grace_s: int) -> int:
    """Scheduled time exit for a position held through the bar that closes at slot_end_ms."""
    return slot_end_ms + SLOT_MS + grace_s * 1000


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _finite(x: Optional[float]) -> bool:
    return x is not None and not math.isnan(x) and not math.isinf(x)


def signal_side(feature: Optional[FeatureRow], settings) -> tuple[Optional[Side], str]:
    """(side, reason). side None means no signal; reason says why."""
    if feature is None:
        return None, "no_feature"
    z = feature.z_poc
    if not _finite(z):
        return None, "nan_z"
    if not abs(z) > settings.entry_z:
        return None, "no_signal"
    s = _sign(z)
    if settings.confirm_range == "sign":
        zr = feature.z_range
        if not _finite(zr) or _sign(zr) != s:
            return None, "range_confirm"
    return (Side.BUY if s > 0 else Side.SELL), "signal"


def size_mult(feature: Optional[FeatureRow], settings) -> float:
    if feature is None or not _finite(feature.events_z):
        return 1.0
    return 0.5 if feature.events_z > settings.vol_shrink_z else 1.0


def sigma_bar(recent_vol: Optional[Sequence[float]]) -> Optional[float]:
    """Per-5-min return sigma from the median of the last 12 realised_vol_pct values.

    De-annualise the 10 s vol (divide by sqrt(periods per year)) and scale to 5 min
    (x sqrt(30)). None if fewer than MIN_VOL_BARS finite positive samples.
    """
    vals = [v for v in (recent_vol or [])[-VOL_BARS:] if _finite(v) and v > 0]
    if len(vals) < MIN_VOL_BARS:
        return None
    med = statistics.median(vals) / 100.0
    return med / math.sqrt(SECONDS_PER_YEAR / 10) * math.sqrt(BARS_10S_PER_SLOT)


def _broker_min_distance(spec: SymbolSpec, mid: float) -> float:
    """Broker min SL distance in price-int units.

    POINTS: one point = 10**-digits price units. PERCENTAGE: assumed to be in 1/100000 of a
    percent of price (unconfirmed; the value is tiny, so a wrong guess only loosens the floor).
    """
    d = spec.sl_distance or 0
    if str(spec.distance_set_in).upper().endswith("PERCENTAGE"):
        return mid * d / 1e5 / 100.0
    return d * 10 ** (5 - spec.digits)


def stop_distance(quote: Quote, spec: SymbolSpec, settings,
                  recent_vol: Optional[Sequence[float]] = None) -> int:
    """Protective stop distance in price ints: max(stop_k*sigma_bar*mid, 3*spread, broker min).

    Fallback when there is no usable vol history: 60 bp of mid in place of the vol term.
    The vol term is capped at 200 bp of mid. The result is never below 1.
    """
    mid = (quote.bid + quote.ask) / 2.0
    sb = sigma_bar(recent_vol)
    if sb is None:
        vol_term = FALLBACK_STOP_BP / 1e4 * mid
    else:
        vol_term = min(settings.stop_k * sb * mid, MAX_STOP_BP / 1e4 * mid)
    spread = max(quote.ask - quote.bid, 0)
    dist = max(vol_term, SPREAD_STOP_MULT * spread, _broker_min_distance(spec, mid))
    return max(int(round(dist)), 1)


def taker_shadow(quote_at_t: Quote, quote_at_t1: Quote, side: Side) -> float:
    """Taker pnl in bp: enter at the touch at t, exit at the touch at t+1 (nan if unusable)."""
    if quote_at_t is None or quote_at_t1 is None:
        return float("nan")
    if side is Side.BUY:
        entry, exit_ = quote_at_t.ask, quote_at_t1.bid
        sgn = 1
    else:
        entry, exit_ = quote_at_t.bid, quote_at_t1.ask
        sgn = -1
    if entry <= 0 or exit_ <= 0:
        return float("nan")
    return sgn * (exit_ - entry) / entry * 1e4


def decide(feature: Optional[FeatureRow], quote: Optional[Quote], position: Optional[PositionView],
           spec: SymbolSpec, settings, now_ms: int, slot_end_ms: int, approx_ok: bool = True,
           recent_vol: Optional[Sequence[float]] = None) -> Intent:
    """One decision per symbol per bar (called at slot_end + grace)."""
    sid = spec.symbol_id
    sig = signal_id(sid, slot_end_ms)
    side, why = signal_side(feature, settings)
    mult = size_mult(feature, settings)

    def mk(kind, s=None, reason="", **kw):
        return Intent(kind, sid, s, kw.get("size_mult", 1.0), kw.get("limit_price"),
                      kw.get("expiry_ms"), kw.get("stop_rel"), reason, sig)

    def entry(kind: IntentKind) -> Intent:
        if quote is None:
            return mk(IntentKind.EXIT if kind is IntentKind.FLIP else IntentKind.NONE,
                      reason="no_quote")
        limit = expiry = None
        if ExecMode(settings.exec) is ExecMode.MAKER:
            limit = quote.bid if side is Side.BUY else quote.ask
            expiry = now_ms + settings.limit_ttl_s * 1000
        return mk(kind, side, "flip" if kind is IntentKind.FLIP else "enter",
                  size_mult=mult, limit_price=limit, expiry_ms=expiry,
                  stop_rel=stop_distance(quote, spec, settings, recent_vol))

    if side is not None and not approx_ok:
        side, why = None, "approx"

    if position is None:
        return entry(IntentKind.ENTER) if side is not None else mk(IntentKind.NONE, reason=why)

    if side is None:  # no (trusted) signal: time exit
        return mk(IntentKind.EXIT, reason=why)
    if side is position.side:
        if settings.rollover_same_side:
            # HOLD carries no time: OrderManager owns exit_at and extends it itself
            return mk(IntentKind.HOLD, side, "rollover")
        return mk(IntentKind.EXIT, reason="no_rollover")
    return entry(IntentKind.FLIP)
