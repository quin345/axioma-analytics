"""kql_source: query shape, Bucketiser-equivalent semantics, export_flat parity, data gates, config."""
import csv
import gzip
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from twisted.internet import defer, task

from autotrade import kql_source as kq
from autotrade._research import export_flat as ef
from autotrade.bars import Bucketiser, replay_bars, slot_bar
from autotrade.types import Bucket, DepthRow

FX = Path(__file__).parent / "fixtures"
T0 = 1_800_000_000_000          # a 5 minute boundary (ms)
SLOT = 300_000


def req(ids=(41, 10026), lo=T0 - 3600_000, hi=T0, table="agg_dom", acct="48664595"):
    return kq.KqlRequest(table, acct, tuple(ids), lo, hi)


def bucketiser_of(events):
    """The bot's own live path: rows in arrival (seq) order through the Bucketiser."""
    bz = Bucketiser(retain_ms=10 ** 15)
    for e in sorted(events, key=lambda e: e["seq"]):
        bz.on_row(DepthRow(e["symbol_id"], e["ts_ms"], e["best_bid"], e["best_ask"], "live"))
    return bz


def kql_buckets(events, sid, lo, hi):
    r = req((sid,), lo, hi)
    return kq.parse_result(r, kq.reference_rows(r, events)).buckets[sid]


def same(a: list, b: list):
    assert [(x.start_ms, x.best_bid, x.best_ask, x.events) for x in a] == \
           [(x.start_ms, x.best_bid, x.best_ask, x.events) for x in b]


# ---- query ----

def test_query_filters_the_stream_and_breaks_ties_by_event_seq():
    q = req().query()
    assert q.startswith("// AUTOTRADE_BUCKETS")
    assert "ctidTraderAccountId == '48664595' and symbolId in ('41', '10026')" in q
    assert "bin(timestamp, 10s)" in q and "events = count()" in q
    assert "arg_max(_k, best_bid, best_ask)" in q and "coalesce(eventSeq, 0)" in q
    assert "datetime(2027-01-15T08:00:00.000Z)" in q            # T0, ms precision
    assert "timestamp >= _from and timestamp < _to" in q        # half-open bucket range
    assert "summarize wm = max(timestamp)" in q                 # watermark: no upper bound
    for bad in (req(table="agg_dom; drop"), req(acct="1 or 1==1"), req(ids=())):
        with pytest.raises(kq.KqlError):
            bad.query()


def test_tie_key_is_fixed_width_so_string_order_is_time_then_seq():
    def key(ms, seq):
        return f"{ms + kq.SEQ_PAD}|{seq + kq.SEQ_PAD}"
    pairs = [(T0, 5), (T0, 40), (T0 + 1, 0), (T0 + 1, 3), (T0 + 1, 1_795_150_801_234_567)]
    keys = [key(*p) for p in pairs]
    assert len({len(k) for k in keys}) == 1
    assert sorted(keys) == [key(*p) for p in sorted(pairs)]


# ---- semantics: KQL query (reference) == Bucketiser ----

@pytest.mark.skipif(not (FX / "xauusd_events.csv.gz").exists(), reason="fixture absent")
def test_reference_equals_bucketiser_on_recorded_events():
    """9 h of real agg_dom rows (file order = eventSeq), including the tied last timestamps that make
    plain arg_max(timestamp) ambiguous."""
    events = []
    with gzip.open(FX / "xauusd_events.csv.gz", "rt") as f:
        for i, r in enumerate(csv.DictReader(f)):
            events.append({"symbol_id": 136, "ts_ms": int(r["timestamp_ms"]), "seq": i,
                           "best_bid": int(float(r["best_bid"])) if r["best_bid"] else None,
                           "best_ask": int(float(r["best_ask"])) if r["best_ask"] else None})
    lo, hi = events[0]["ts_ms"] // 10_000 * 10_000, events[-1]["ts_ms"] + 1
    same(kql_buckets(events, 136, lo, hi), bucketiser_of(events).buckets_since(136, 0))


def synth_events(seed=1, n=4000, sid=7, start=T0 - 6 * 3600_000):
    """Out-of-order stamps, tied stamps with different books, empty sides, a quiet stretch."""
    rnd = random.Random(seed)
    out, ts, mid = [], start, 3_600_000_000
    for i in range(n):
        ts += rnd.choice((0, 0, 1, 7, 250, 900, 3000, 12_000)) if i % 500 else 400_000
        stamp = ts - rnd.choice((0, 0, 0, 5)) if i % 9 == 0 else ts
        mid += rnd.randint(-300, 300)
        bid = None if i % 97 == 0 else mid - 10
        ask = None if i % 89 == 0 else mid + 10 + rnd.randint(0, 4)
        out.append({"symbol_id": sid, "ts_ms": stamp, "seq": i, "best_bid": bid, "best_ask": ask})
    return out


def test_reference_equals_bucketiser_on_synthetic_ties_and_disorder():
    ev = synth_events()
    lo, hi = ev[0]["ts_ms"] // 10_000 * 10_000 - 10_000, max(e["ts_ms"] for e in ev) + 1
    got = kql_buckets(ev, 7, lo, hi)
    same(got, bucketiser_of(ev).buckets_since(7, 0))
    ties = {}
    for e in ev:
        ties.setdefault((e["ts_ms"] // 10_000, e["ts_ms"]), set()).add((e["best_bid"], e["best_ask"]))
    assert sum(len(v) > 1 for v in ties.values()) > 50            # the data really has ambiguous ties
    # a seq that does not start at 0 (collector restart counters / wall-clock init) changes nothing
    shifted = [dict(e, seq=e["seq"] + 1_795_150_801_234_567) for e in ev]
    same(kql_buckets(shifted, 7, lo, hi), got)


def test_range_is_half_open_and_watermark_unbounded():
    ev = [{"symbol_id": 7, "ts_ms": t, "seq": i, "best_bid": 1, "best_ask": 2}
          for i, t in enumerate((T0 - 10_001, T0 - 10_000, T0 - 1, T0, T0 + 4000))]
    r = req((7,), T0 - 10_000, T0)
    res = kq.parse_result(r, kq.reference_rows(r, ev))
    assert [(b.start_ms, b.events) for b in res.buckets[7]] == [(T0 - 10_000, 2)]
    assert res.watermark[7] == T0 + 4000


def test_parse_result_types_sources_and_unknown_streams():
    r = req((41,))
    rows = [{"row_kind": "b", "symbolId": "41", "ms": T0 - 10_000, "best_bid": 376361000.0, "best_ask": None, "events": 3},
            {"row_kind": "b", "symbolId": "41", "ms": T0 - 20_000, "best_bid": float("nan"), "best_ask": 2.0, "events": 1},
            {"row_kind": "b", "symbolId": "99", "ms": T0, "best_bid": 1.0, "best_ask": 2.0, "events": 1},
            {"row_kind": "w", "symbolId": "41", "ms": T0 + 5, "best_bid": None, "best_ask": None, "events": None}]
    res = kq.parse_result(r, rows)
    assert res.buckets[41] == [Bucket(41, T0 - 20_000, None, 2, 1, "kql"),
                               Bucket(41, T0 - 10_000, 376361000, None, 3, "kql")]
    assert isinstance(res.buckets[41][1].best_bid, int)
    assert res.watermark == {41: T0 + 5}
    assert kq.parse_result(r, []).watermark == {41: None}


# ---- parity with export_flat (what research metrics_5m is built from) ----

def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def test_bars_from_kql_buckets_match_export_flat():
    """KQL-shaped buckets -> the bot's bars == export_flat.build_rows over the webapp series_range rows
    (shaped as KustoReader returns them: ISO strings, extra columns), to 1e-9."""
    rnd = random.Random(5)
    ev, mid, ts, seq = [], 3_600_000_000, T0 - 6 * 3600_000, 0
    while ts < T0:
        ts += rnd.choice((300, 1200, 2500, 4000))
        if T0 - 5 * 3600_000 < ts < T0 - 5 * 3600_000 + 600_000:
            continue                                          # a 10 minute hole
        mid += rnd.randint(-400, 400)
        seq += 1
        ev.append({"symbol_id": 7, "ts_ms": ts, "seq": seq, "best_bid": mid - 15, "best_ask": mid + 15 + rnd.randint(0, 6)})
    lo = ev[0]["ts_ms"] // 10_000 * 10_000
    r = req((7,), lo, T0)
    rows = kq.reference_rows(r, ev)
    kql = kq.parse_result(r, rows).buckets[7]
    # webapp series_range rows: bucket/timestamp ISO strings, plus columns the bars ignore
    last_ts = {}
    for e in ev:
        if e["ts_ms"] < T0:
            last_ts[e["ts_ms"] // 10_000 * 10_000] = max(last_ts.get(e["ts_ms"] // 10_000 * 10_000, 0), e["ts_ms"])
    series = [{"bucket": _iso(x["ms"]), "timestamp": _iso(last_ts[x["ms"]]), "best_bid": x["best_bid"],
               "best_ask": x["best_ask"], "rel_spread": 1e-5, "rel_vwap_spread": 2e-5, "imbalance_ratio": 0.1,
               "events": x["events"]} for x in rows if x["row_kind"] == "b"]
    ends = [datetime.fromtimestamp(ms / 1000, tz=timezone.utc) for ms in range(T0 - 3600_000, T0 + 1, SLOT)]
    data_start = datetime.fromtimestamp(lo / 1000, tz=timezone.utc)
    exp = ef.build_rows({}, series, [], ends, 100000, data_start)
    got = replay_bars(7, kql, int(ends[0].timestamp() * 1000), T0, lo)
    assert len(got) == len(exp) == 13
    fields = ["mid_open", "mid_high", "mid_low", "mid_close", "spread_mean", "spread_median",
              "rel_spread_mean", "realised_vol_pct", "vap_240m_poc", "vap_240m_va_low", "vap_240m_va_high",
              "vap_240m_volume_events"]
    for b, e in zip(got, exp):
        assert b.events == e["events"] and b.has_data == bool(e["has_data"]) and b.approx_share == 0.0
        assert b.vap_coverage == e["vap_240m_coverage"]
        for k in fields:
            x, y = getattr(b, k), e[k]
            y = float("nan") if y is None else float(y)
            assert math.isnan(x) == math.isnan(y), (b.slot_end, k)
            if not math.isnan(x):
                assert abs(x - y) <= 1e-9 * max(1.0, abs(y)), (b.slot_end, k, x, y)
    # KQL buckets count as exact: approx_share 0, unlike tick/store buckets
    mixed = kql[:-30] + [Bucket(7, b.start_ms, b.best_bid, b.best_ask, b.events, "tick") for b in kql[-30:]]
    end = datetime.fromtimestamp(T0 / 1000, tz=timezone.utc)
    assert slot_bar(7, end, kql).approx_share == 0.0
    assert 0 < slot_bar(7, end, mixed).approx_share < 1


# ---- gates ----

def test_stream_state():
    f = 15_000
    assert kq.stream_state(None, T0, f) == "stale"
    assert kq.stream_state(T0, T0, f) == "complete"
    assert kq.stream_state(T0 + 900, T0, f) == "complete"
    assert kq.stream_state(T0 - 1, T0, f) == "fresh"
    assert kq.stream_state(T0 - 15_000, T0, f) == "fresh"
    assert kq.stream_state(T0 - 15_001, T0, f) == "stale"
    # quiet market: KQL has caught up with the latest spot the bot has seen
    assert kq.stream_state(T0 - 40_000, T0, f, last_spot_ms=T0 - 40_000) == "complete"
    assert kq.stream_state(T0 - 40_000, T0, f, last_spot_ms=T0 - 20_000) == "stale"
    assert kq.stream_state(T0 - 5_000, T0, f, last_spot_ms=T0 + 1000) == "fresh"


def test_window_gaps_counts_only_open_session_time():
    start, end = T0 - 240 * 60_000, T0
    full = list(range(start, end, 10_000))
    assert kq.window_gaps(full, start, end, 60_000) == []
    hole = [s for s in full if not T0 - 3600_000 <= s < T0 - 3600_000 + 70_000]   # 70 s without a bucket
    assert kq.window_gaps(hole, start, end, 60_000) == [(T0 - 3600_000, T0 - 3600_000 + 70_000, 70_000)]
    assert kq.window_gaps(hole, start, end, 70_000) == []                         # not longer than the max
    # head (stream started late) and tail (stopped) are gaps too
    assert kq.window_gaps(full[100:], start, end, 60_000)[0][:2] == (start, full[100])
    assert kq.window_gaps(full[:-10], start, end, 60_000)[0][:2] == (full[-10], end)
    # a closed session (e.g. gold's daily break) is not a gap, a half-open one counts its open part
    br0, br1 = T0 - 3 * 3600_000, T0 - 2 * 3600_000
    brk = [s for s in full if not br0 <= s < br1]
    closed = lambda ms: not br0 <= ms < br1
    assert kq.window_gaps(brk, start, end, 60_000, is_open=closed) == []
    late = [s for s in full if not br0 <= s < br1 + 90_000]                      # reopened, 90 s silent
    (g,) = kq.window_gaps(late, start, end, 60_000, is_open=closed)
    assert g[:2] == (br0, br1 + 90_000) and 60_000 < g[2] <= 90_000     # open time: a lower bound past the max


# ---- config and source ----

def test_kql_config_reads_only_its_keys_and_hides_the_secret(tmp_path):
    env = tmp_path / ".env"
    env.write_text('KUSTO_CLUSTER_URI="https://x.kusto"\nKUSTO_DATABASE=dom_ctrader\nSECRET_KEY=zzz\n'
                   'AZURE_TENANT_ID=t\nAZURE_CLIENT_ID=c\nAZURE_CLIENT_SECRET=SUPER-SECRET\n')
    cfg = kq.load_kql_config(env, environ={})
    assert (cfg.cluster_uri, cfg.database, cfg.table) == ("https://x.kusto", "dom_ctrader", "agg_dom")
    assert cfg.creds == ("t", "c", "SUPER-SECRET") and "SUPER-SECRET" not in repr(cfg)
    assert kq.load_kql_config(env, environ={"KUSTO_TABLE": "agg2"}).table == "agg2"   # process env wins
    with pytest.raises(kq.KqlError, match="plain table"):
        kq.load_kql_config(env, environ={"KUSTO_TABLE": "a|b"})
    with pytest.raises(kq.KqlError, match="KUSTO_CLUSTER_URI"):
        kq.load_kql_config(tmp_path / "missing", environ={})
    env.write_text("KUSTO_CLUSTER_URI=u\nKUSTO_DATABASE=d\n")
    assert kq.load_kql_config(env, environ={}).creds is None        # DefaultAzureCredential then


class Reader:
    def __init__(self, events=(), fail=None):
        self.events, self.fail, self.requests = list(events), fail, []

    def rows(self, r):
        self.requests.append(r)
        if self.fail:
            raise self.fail
        return kq.reference_rows(r, self.events)


def sync(fn, *a):
    return defer.maybeDeferred(fn, *a)


def test_source_fetch_aligns_start_and_reports_errors():
    ev = [{"symbol_id": 41, "ts_ms": T0 - 12_345, "seq": 1, "best_bid": 10, "best_ask": 12}]
    rd = Reader(ev)
    src = kq.KqlSource("48664595", reader=rd, defer_fn=sync)
    out = []
    src.fetch([41], T0 - 12_345, T0).addCallback(out.append)
    assert rd.requests[0].from_ms == T0 - 20_000 and rd.requests[0].to_ms == T0     # whole first bucket
    assert out[0].buckets[41] == [Bucket(41, T0 - 20_000, 10, 12, 1, "kql")] and src.calls == 1
    bad = kq.KqlSource("48664595", reader=Reader(fail=RuntimeError("boom")), defer_fn=sync)
    errs = []
    bad.fetch([41], T0 - 60_000, T0).addErrback(lambda f: errs.append(f.value))
    assert isinstance(errs[0], RuntimeError)
    # a config problem surfaces as a failed fetch, not an exception at construction
    cfg_bad = kq.KqlSource("48664595", config_fn=lambda: kq.load_kql_config(None, environ={}), defer_fn=sync)
    errs.clear()
    cfg_bad.fetch([41], T0 - 60_000, T0).addErrback(lambda f: errs.append(f.value))
    assert isinstance(errs[0], kq.KqlError)


def test_source_fetch_times_out():
    clock = task.Clock()
    src = kq.KqlSource("48664595", reader=Reader(), reactor=clock, defer_fn=lambda fn, *a: defer.Deferred())
    errs = []
    src.fetch([41], T0 - 60_000, T0, timeout_s=4).addErrback(lambda f: errs.append(f.value))
    clock.advance(3.9)
    assert not errs
    clock.advance(0.2)
    assert isinstance(errs[0], defer.TimeoutError)
