"""Warm-up bars are replayed in time order, so each bar's 240 min VAP window is complete.

Regression: the runner used to load every warm-up bucket into the (250 min retention) Bucketiser
and only then build bars, so older buckets were pruned before their slots were built: 9 h of
buckets gave 50 bars and the early ones had truncated windows.
"""
import math
import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from autotrade._research import export_flat as ef
from autotrade.bars import BarBuilder, Bucketiser, replay_bars
from autotrade.features import FeatureEngine
from autotrade.runner import Bot
from autotrade.types import Bucket

SLOT = 300_000
BASE = 1_800_000_000_000          # a 5 minute boundary
HOURS = 9


def synth(hours=HOURS, seed=3, sym=7):
    rnd = random.Random(seed)
    mid = 3_600_000_000
    out = []
    for i in range(hours * 360):
        mid += rnd.randint(-300, 300)
        src = "tick" if i % 7 < 2 else "store"
        out.append(Bucket(sym, BASE + i * 10_000, mid - 10, mid + 10 + rnd.randint(0, 4),
                          rnd.randint(1, 30), src))
    return out


def _end(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


_CACHE = {}


def _build(buckets=None):
    """Replay of synth(), built once (the module's slowest step)."""
    if "x" not in _CACHE:
        buckets = buckets or synth()
        last_end = (buckets[-1].start_ms + 10_000) // SLOT * SLOT
        _CACHE["x"] = (replay_bars(7, buckets, BASE + SLOT, last_end, BASE), last_end)
    return _CACHE["x"]


def test_replay_builds_a_bar_per_slot_not_just_the_retention_window():
    bks = synth()
    bars, last_end = _build()
    assert len(bars) >= 100                      # was 50 with load-then-build
    assert len(bars) == (last_end - BASE) // SLOT
    # the old path, for contrast: pruned to the last 250 min before any slot is built
    bz = Bucketiser(); bz.load(bks)
    assert len(bz.buckets_since(7, 0)) < len(bks) / 2


def test_full_window_bars_match_export_flat_and_truncated_ones_are_flagged():
    bks = synth()
    bars, _ = _build()
    raw = [{"bucket": b.start_ms, "epoch_ms": float(b.start_ms), "best_bid": b.best_bid,
            "best_ask": b.best_ask, "events": b.events} for b in bks]
    full = [b for b in bars if b.vap_coverage >= 1.0]
    short = [b for b in bars if b.vap_coverage < 1.0]
    assert len(short) == 47                      # slots 1..47: window starts before the first bucket
    assert full[0].slot_end == _end(BASE + 48 * SLOT)   # the earliest bar with a full 240 min window
    for i, b in enumerate(bars):
        if i % 4 and b not in full[:2] + short[-2:]:
            continue                             # every 4th slot plus the coverage boundary
        end = b.slot_end
        vstart = end - timedelta(minutes=240)
        exp = ef.vap_metrics(raw, end, 100000, _end(BASE))     # same data_start the bot passes
        assert math.isclose(b.vap_coverage, exp["vap_240m_coverage"])
        # the VAP over whatever window exists is identical with or without the retention limit
        for k in ("poc", "va_low", "va_high", "volume_events"):
            assert getattr(b, f"vap_240m_{k}") == exp[f"vap_240m_{k}"], (end, k)
        window = [x for x in bks if vstart.timestamp() * 1000 <= x.start_ms < end.timestamp() * 1000]
        assert b.approx_share == sum(x.source != "live" for x in window) / len(window)
    # direct computation over the earliest full window
    e0 = full[0].slot_end
    d = ef.vap_metrics(raw, e0, 100000, e0 - timedelta(minutes=240))
    assert (full[0].vap_240m_poc, full[0].vap_240m_va_low, full[0].vap_240m_va_high,
            full[0].vap_240m_volume_events) == (d["vap_240m_poc"], d["vap_240m_va_low"],
                                               d["vap_240m_va_high"], d["vap_240m_volume_events"])


def test_old_load_then_build_truncates_windows():
    """Documents the bug: the same slot built from the pruned bucketiser differs from the replay."""
    bks = synth()
    bars, last_end = _build()
    bz = Bucketiser(); bz.load(bks)
    old = BarBuilder(bz).close_slot(7, _end(BASE + 60 * SLOT))
    new = next(b for b in bars if b.slot_end == _end(BASE + 60 * SLOT))
    assert old.vap_240m_volume_events != new.vap_240m_volume_events


def test_truncated_bars_enter_the_features_with_nan_vap():
    bars, _ = _build()
    fe = FeatureEngine()
    df = fe.frame([(b, False) for b in bars])
    n_short = sum(b.vap_coverage < 1.0 for b in bars)
    assert df["vap_240m_poc"].isna().sum() == n_short
    assert not df["vap_240m_poc"].iloc[n_short:].isna().any()
    # z_poc is NaN until 12 valid poc_dist bars exist, finite after
    f = fe.research_features(df)
    assert math.isnan(f["z_poc"].iloc[n_short - 1])
    assert math.isfinite(f["z_poc"].iloc[-1])


def test_warmup_fetch_covers_history_slots_plus_the_vap_window():
    def hours(setting, slots):
        return Bot.warmup_hours(SimpleNamespace(settings=SimpleNamespace(warmup_hours=setting),
                                                history_slots=slots))
    assert hours(9, 150) == 16.5                 # 150 bars * 5 min + 240 min
    assert hours(9, 30) == 9                     # the configured floor wins for short histories
    assert hours(9, 48) >= 8.5                   # 48 z bars + 240 min = 8 h
