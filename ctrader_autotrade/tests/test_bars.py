import math
import random
from datetime import datetime, timedelta, timezone

from autotrade._research import analytics, export_flat as ef
from autotrade.bars import BarBuilder, Bucketiser
from autotrade.types import Bucket, DepthRow

T0 = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def ms(dt):
    return int(dt.timestamp() * 1000)


def synth(n_min=260, seed=1, sym=7, gaps=()):
    rnd = random.Random(seed)
    mid = 3_600_000_000  # 36000.00 -> price int
    out = []
    start = T0 - timedelta(minutes=n_min)
    for i in range(n_min * 6):
        t = ms(start) + i * 10_000
        mid += rnd.randint(-300, 300)
        if any(a <= t < b for a, b in gaps):
            continue
        out.append(Bucket(sym, t, mid - 10, mid + 10 + rnd.randint(0, 4), rnd.randint(1, 30), "live"))
    return out


def test_p1_slot_and_vap_match_export_flat():
    bks = synth()
    bi = Bucketiser(); bi.load(bks)
    bar = BarBuilder(bi).close_slot(7, T0)
    raw = [{"bucket": b.start_ms, "epoch_ms": float(b.start_ms), "best_bid": b.best_bid,
            "best_ask": b.best_ask, "events": b.events} for b in bks]
    start = T0 - timedelta(minutes=5)
    slot = ef._scaled(ef._between(raw, start, T0), 100000)
    m = ef.slot_metrics(slot, [])
    v = ef.vap_metrics(raw, T0, 100000, T0 - timedelta(minutes=240))
    # independent re-derivation from analytics
    mids = [(b.best_bid + b.best_ask) / 2 / 100000 for b in bks if ms(start) <= b.start_ms < ms(T0)]
    assert bar.mid_open == mids[0] and bar.mid_close == mids[-1]
    assert bar.mid_high == max(mids) and bar.mid_low == min(mids)
    assert bar.events == sum(b.events for b in bks if ms(start) <= b.start_ms < ms(T0))
    for got, want in [(bar.mid_close, m["mid_close"]), (bar.realised_vol_pct, m["realised_vol_pct"]),
                      (bar.spread_median, m["spread_median"]), (bar.rel_spread_mean, m["rel_spread_mean"]),
                      (bar.vap_240m_poc, v["vap_240m_poc"]), (bar.vap_240m_va_low, v["vap_240m_va_low"]),
                      (bar.vap_240m_va_high, v["vap_240m_va_high"]),
                      (bar.vap_240m_volume_events, v["vap_240m_volume_events"])]:
        assert abs(got - want) <= 1e-12
    win = [b for b in bks if ms(T0 - timedelta(minutes=240)) <= b.start_ms < ms(T0)]
    prof = analytics.volume_at_price(
        [{"best_bid": b.best_bid, "best_ask": b.best_ask, "events": b.events} for b in win], 100000)
    assert abs(bar.vap_240m_poc - prof["poc"]) <= 1e-12
    assert bar.approx_share == 0.0


def test_half_open_slot_boundaries():
    bi = Bucketiser()
    mk = lambda t, p, e=1: Bucket(7, t, p, p + 2, e, "live")
    s = ms(T0)
    bi.load([mk(s - 300_000, 1_000_000, 4),      # first bucket of the slot: included
             mk(s - 10_000, 1_100_000, 6),        # last bucket of the slot: included
             mk(s, 9_000_000, 100),               # belongs to the next slot
             mk(s - 310_000, 5_000_000, 50)])     # previous slot
    bar = BarBuilder(bi).close_slot(7, T0)
    assert bar.events == 10
    assert bar.mid_open == 10.00001 and bar.mid_close == 11.00001


def test_empty_slot_has_no_data():
    bi = Bucketiser(); bi.load(synth(gaps=[(ms(T0) - 300_000, ms(T0))]))
    bar = BarBuilder(bi).close_slot(7, T0)
    assert bar.has_data is False and bar.events == 0
    assert math.isnan(bar.mid_close) and math.isnan(bar.realised_vol_pct)
    assert not math.isnan(bar.vap_240m_poc)  # the 240 min window still has data


def test_bucketiser_last_state_and_event_count():
    bi = Bucketiser()
    base = ms(T0)
    for k, (bid, ask) in enumerate([(100, 102), (101, 103), (None, 104)]):
        bi.on_row(DepthRow(7, base + k * 1000, bid, ask, "live"))
    bi.on_row(DepthRow(7, base + 10_000, 200, 202, "live"))
    b0, b1 = bi.buckets_since(7, base)
    assert (b0.start_ms, b0.best_bid, b0.best_ask, b0.events) == (base, None, 104, 3)
    assert (b1.start_ms, b1.events) == (base + 10_000, 1)
    assert bi.buckets_since(7, base + 10_000) == [b1]


def test_bucketiser_retention_and_approx_share():
    bi = Bucketiser()
    bks = synth(n_min=300)
    # first half of the window from tick data
    half = ms(T0) - 120 * 60_000
    bks = [Bucket(b.symbol_id, b.start_ms, b.best_bid, b.best_ask, b.events,
                  "tick" if b.start_ms < half else "live") for b in bks]
    bi.load(bks)
    assert bi.buckets_since(7, 0)[0].start_ms >= ms(T0) - 300 * 60_000  # pruned to < retention
    bar = BarBuilder(bi).close_slot(7, T0)
    assert abs(bar.approx_share - 0.5) < 1e-9
