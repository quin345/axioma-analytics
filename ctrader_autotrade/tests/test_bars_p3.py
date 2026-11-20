"""P3: raw per-event rows (XAUUSD, 2026-11-18 12:00-21:00 UTC, read from the Kusto feed) replayed
through Stamper -> Bucketiser -> BarBuilder must reproduce the recorded metrics_5m rows."""
import csv
import gzip
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autotrade.bars import BarBuilder, Bucketiser
from autotrade.book import Stamper

FX = Path(__file__).parent / "fixtures"
SYM = 136
FIELDS = ["mid_open", "mid_high", "mid_low", "mid_close", "spread_mean", "spread_median",
          "rel_spread_mean", "realised_vol_pct", "vap_240m_poc", "vap_240m_va_low",
          "vap_240m_va_high", "vap_240m_volume_events"]


def fl(x):
    return float("nan") if x in ("", None) else float(x)


@pytest.mark.skipif(not (FX / "xauusd_events.csv.gz").exists(), reason="fixture absent")
def test_p3_event_replay_reproduces_metrics_5m():
    stamper, bi = Stamper(), Bucketiser()
    builder = BarBuilder(bi)
    with open(FX / "xauusd_metrics_5m.csv") as f:
        rows = list(csv.DictReader(f))
    ends = [datetime.fromisoformat(r["slot_end"].replace("Z", "+00:00")).astimezone(timezone.utc) for r in rows]
    bars = {}
    pending = list(ends)

    def close_due(upto_ms):
        while pending and pending[0].timestamp() * 1000 <= upto_ms:  # replay in time order, as live
            e = pending.pop(0)
            bars[e] = builder.close_slot(SYM, e)

    with gzip.open(FX / "xauusd_events.csv.gz", "rt") as f:
        for r in csv.DictReader(f):
            ts = int(r["timestamp_ms"])
            close_due(ts)
            stamper.on_spot(SYM, ts)
            q = []
            if r["best_bid"]:
                q.append((1, "bid", int(float(r["best_bid"])), 1))
            if r["best_ask"]:
                q.append((2, "ask", int(float(r["best_ask"])), 1))
            deleted = [i for i, k in ((1, "best_bid"), (2, "best_ask")) if not r[k]]
            row = stamper.on_depth(SYM, q, deleted)
            if row:
                bi.on_row(row)
    close_due(10 ** 15)
    # Kusto's arg_max(timestamp, ...) picks an arbitrary row when several rows share the bucket's
    # last timestamp with different books (4% of buckets here); the replay orders ties by
    # eventSeq. Slot metrics of slots containing no such bucket must match exactly; the rest loosely (VAP always loosely except its event total).
    last = {}
    with gzip.open(FX / "xauusd_events.csv.gz", "rt") as f:
        for r in csv.DictReader(f):
            ts = int(r["timestamp_ms"])
            last.setdefault(ts - ts % 10_000, {}).setdefault(ts, set()).add((r["best_bid"], r["best_ask"]))
    amb = [b for b, d in last.items() if len(d[max(d)]) > 1]
    worst = {k: 0.0 for k in FIELDS}
    clean_slots = 0
    for r in rows:
        bar = bars[datetime.fromisoformat(r["slot_end"].replace("Z", "+00:00")).astimezone(timezone.utc)]
        assert bar.events == int(r["events"]), r["slot_end"]
        assert bar.has_data == bool(int(r["has_data"]))
        assert bar.approx_share == 0.0
        e = int(bar.slot_end.timestamp() * 1000)
        ambiguous = any(e - 300_000 <= b < e for b in amb)
        for k in FIELDS:
            a, b = getattr(bar, k), fl(r[k])
            assert math.isnan(a) == math.isnan(b), (r["slot_end"], k)
            if not math.isnan(a):
                d = abs(a - b) / max(1.0, abs(b))
                worst[k] = max(worst[k], d)
                if not ambiguous and not k.startswith("vap_"):
                    assert d < 1e-9, (r["slot_end"], k, a, b)  # slots with no tie: exact
                elif k == "vap_240m_volume_events":
                    assert d < 1e-3, (r["slot_end"], k, a, b)  # event total; late-arriving rows move it slightly
                elif k.startswith("vap_"):
                    assert d < 5e-3, (r["slot_end"], k, a, b)  # profile over 240 min of tie-prone buckets
                else:
                    assert d < (0.15 if k == "realised_vol_pct" else 1e-2), (r["slot_end"], k, a, b)
        clean_slots += not ambiguous
    print("P3 slots", len(rows), "unambiguous", clean_slots, "ambiguous buckets", len(amb), "max rel diff", worst)
    assert clean_slots >= 5
