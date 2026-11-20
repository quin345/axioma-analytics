import pytest

from autotrade import config
from autotrade.clock import TestClock
from autotrade.session import ApiError, Session
from autotrade.types import Bucket, DepthRow
from autotrade.warmup import (RateLimiter, decode_ticks, fetch_ticks, merge_ticks, plan_warmup,
                              warmup)
from ctrader_open_api.messages import OpenApiMessages_pb2 as oa
from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model
from fake_transport import FakeTransport, error_res, install_happy


def tick_res(rows, has_more):
    """rows: absolute [(ts, price)] newest first; encoded as first-absolute then deltas."""
    res = oa.ProtoOAGetTickDataRes(hasMore=has_more)
    pts, pp = None, None
    for ts, px in rows:
        if pts is None:
            res.tickData.add(timestamp=ts, tick=px)
        else:
            res.tickData.add(timestamp=ts - pts, tick=px - pp)
        pts, pp = ts, px
    return res


@pytest.fixture
def sess(tmp_path, make_env, make_toml):
    st = config.load(make_env(), make_toml(), overrides={"state_dir": str(tmp_path / "state")})
    clock = TestClock(10_000_000)
    fake = FakeTransport()
    install_happy(fake)
    s = Session(st, fake, clock)
    fake.connect()
    return s, fake, clock


def run(d):
    out = []
    d.addBoth(out.append)
    return out


def test_decode_deltas():
    res = tick_res([(5000, 200_000), (4000, 199_000), (4500, 199_500)], False)
    assert [(t.timestamp, t.tick) for t in res.tickData] == [(5000, 200_000), (-1000, -1000), (500, 500)]
    assert decode_ticks(res.tickData) == [(4000, 199_000), (4500, 199_500), (5000, 200_000)]


def test_fetch_ticks_pages_backwards_over_three_pages(sess):
    s, fake, clock = sess
    bid_pages = [tick_res([(9000, 90), (8000, 80)], True), tick_res([(7000, 70), (6000, 60)], True),
                 tick_res([(5000, 50), (4000, 40)], False)]
    ask_pages = [tick_res([(9000, 91), (8500, 85)], True), tick_res([(8500, 85), (7000, 71)], True),
                 tick_res([(3000, 31)], False)]
    pages = {model.BID: iter(bid_pages), model.ASK: iter(ask_pages)}
    fake.respond(oa.ProtoOAGetTickDataReq, lambda m: next(pages[m.type]))
    out = run(fetch_ticks(s, 1, 1000, 10_000))
    clock.advance(5)
    bids, asks = out[0]
    assert bids == [(4000, 40), (5000, 50), (6000, 60), (7000, 70), (8000, 80), (9000, 90)]
    assert asks == [(3000, 31), (7000, 71), (8500, 85), (9000, 91)]  # duplicate 8500 dropped
    reqs = fake.sent_of(oa.ProtoOAGetTickDataReq)
    assert len(reqs) == 6
    assert [r.type for r in reqs] == [model.BID] * 3 + [model.ASK] * 3
    # paging moves toTimestamp backwards to the earliest tick of the previous page
    assert [(r.fromTimestamp, r.toTimestamp) for r in reqs[:3]] == [(1000, 10_000), (1000, 8000), (1000, 6000)]
    assert all(r.symbolId == 1 and r.ctidTraderAccountId == 12345 for r in reqs)


def test_fetch_ticks_error_response_fails(sess):
    s, fake, clock = sess
    fake.respond(oa.ProtoOAGetTickDataReq, error_res("INCORRECT_BOUNDARIES"))
    out = run(fetch_ticks(s, 1, 1, 2))
    assert out[0].check(ApiError)


def test_merge_ticks_carries_other_side():
    rows = merge_ticks([(1000, 10), (3000, 12)], [(2000, 21), (3000, 22)], 7)
    assert rows == [DepthRow(7, 1000, 10, None, "tick"), DepthRow(7, 2000, 10, 21, "tick"),
                    DepthRow(7, 3000, 12, 21, "tick"), DepthRow(7, 3000, 12, 22, "tick")]
    assert merge_ticks([], [], 7) == []


def b(sid, start, src="live"):
    return Bucket(sid, start, 1, 2, 3, src)


def test_plan_warmup_gaps():
    now = 100_000_000
    since = now - 3600_000
    stored = [b(1, since + 600_000), b(1, since + 610_000), b(1, since + 1_200_000),
              b(1, now - 20_000)]
    plan = plan_warmup([1, 2], lambda sid, s: stored if sid == 1 else [], now, 1)
    assert plan[1][1] == [(since, since + 600_000), (since + 620_000, since + 1_200_000),
                          (since + 1_210_000, now - 20_000)]
    assert plan[2] == ([], [(since, now)])


def test_warmup_combines_store_and_ticks(sess):
    s, fake, clock = sess
    now = 10_000_000 + 3600_000
    since = now - 3600_000
    stored = [b(1, since + 1_000_000), b(1, since + 1_010_000)]
    fake.respond(oa.ProtoOAGetTickDataReq,
                 lambda m: tick_res([(since + 100, 5)] if m.type == model.BID else [(since + 200, 6)], False))
    out = run(warmup(s, [1], lambda sid, t: stored, now, hours=1))
    clock.advance(30)
    res = out[0][1]
    assert [r.source for r in res[:2]] == ["tick", "tick"]
    assert {r.source for r in res if isinstance(r, Bucket)} == {"store"}
    assert [type(r) for r in res if isinstance(r, DepthRow)] == [DepthRow] * 2  # only the first gap's bid and ask fall inside their gap
    keys = [r.start_ms if isinstance(r, Bucket) else r.stamp_ms for r in res]
    assert keys == sorted(keys)


def test_warmup_default_hours_from_settings(sess):
    s, fake, clock = sess
    got = []
    warmup(s, [1], lambda sid, since: got.append(since) or [b(1, 10_000_000 - 1)],
           now_ms=100_000_000 + 1)
    assert got == [100_000_001 - 9 * 3600_000]


def test_rate_limiter_at_most_5_per_second():
    clock = TestClock(0)
    lim = RateLimiter(clock, 5)
    stamps = []
    for _ in range(23):
        lim.acquire().addCallback(lambda _: stamps.append(clock.now_ms()))
    for _ in range(100):
        clock.advance(0.1)
    assert len(stamps) == 23
    for i, t in enumerate(stamps):
        assert sum(1 for u in stamps if t - 1000 < u <= t) <= 5, (i, stamps)


def test_warmup_requests_rate_limited(sess):
    s, fake, clock = sess
    sent_at = []

    def resp(m):
        sent_at.append(clock.now_ms())
        return tick_res([(1000, 1)], False)
    fake.respond(oa.ProtoOAGetTickDataReq, resp)
    out = run(warmup(s, [1, 2, 3, 4], lambda sid, t: [], 10_000_000 + 3600_000, hours=1))
    for _ in range(100):
        clock.advance(0.1)
    assert out and len(sent_at) == 8
    for t in sent_at:
        assert sum(1 for u in sent_at if t - 1000 < u <= t) <= 5
    assert max(sent_at) - min(sent_at) >= 1000  # the burst was actually spread out
