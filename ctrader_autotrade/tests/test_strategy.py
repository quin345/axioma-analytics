import math
from datetime import datetime, timezone

import pytest

from autotrade import strategy
from autotrade.config import Settings
from autotrade.strategy import decide, stop_distance, taker_shadow
from autotrade.types import (FeatureRow, IntentKind, PositionView, Quote, Side, SymbolSpec)

SLOT_END_MS = 1_800_000_000_000
NOW = SLOT_END_MS + 2000
NAN = float("nan")


def spec(**kw):
    d = dict(name="XAUUSD", symbol_id=41, digits=2, pip_position=1, lot_size=100, min_volume=1,
             step_volume=1, max_volume=1000, sl_distance=0, distance_set_in="POINTS",
             schedule=[], schedule_tz="UTC", holidays=[])
    d.update(kw)
    return SymbolSpec(**d)


def feat(z=1.5, zr=1.0, ev=0.0, **kw):
    d = dict(symbol_id=41, slot_end=datetime.fromtimestamp(SLOT_END_MS / 1000, timezone.utc),
             poc_dist_240=0.0, range_pos_12=0.0, events_z=ev, sig_poc=0.0, z_poc=z, sig_range=0.0,
             z_range=zr, realised_vol_pct=10.0, spread_median_48=10.0, approx_share=0.0,
             events_z_live_only=True)
    d.update(kw)
    return FeatureRow(**d)


def quote(bid=200_000_000, ask=200_002_000):  # 2000.00 / 2000.02
    return Quote(41, bid, ask, NOW)


def pos(side=Side.BUY):
    return PositionView(41, side, 2, 2000.0, SLOT_END_MS - 300_000, SLOT_END_MS + 2000, None)


def run(f=None, q="q", p=None, st=None, approx=True, vol=None, **skw):
    s = st or Settings(**skw)
    q = quote() if q == "q" else q
    return decide(f, q, p, spec(), s, NOW, SLOT_END_MS, approx, vol)


@pytest.mark.parametrize("z,kind,side", [
    (1.0, IntentKind.NONE, None),
    (-1.0, IntentKind.NONE, None),
    (1.0001, IntentKind.ENTER, Side.BUY),
    (-1.0001, IntentKind.ENTER, Side.SELL),
    (0.0, IntentKind.NONE, None),
    (NAN, IntentKind.NONE, None),
])
def test_threshold_and_nan(z, kind, side):
    i = run(feat(z=z))
    assert (i.kind, i.side) == (kind, side)


def test_feature_none_and_signal_id():
    i = run(None)
    assert i.kind is IntentKind.NONE and i.reason == "no_feature"
    assert i.signal_id == f"41-{SLOT_END_MS // 1000}"
    assert i.symbol_id == 41


@pytest.mark.parametrize("mode,z,zr,expected", [
    ("off", 1.5, -3.0, IntentKind.ENTER),
    ("off", 1.5, NAN, IntentKind.ENTER),
    ("sign", 1.5, 0.2, IntentKind.ENTER),
    ("sign", 1.5, -0.2, IntentKind.NONE),
    ("sign", -1.5, -0.2, IntentKind.ENTER),
    ("sign", -1.5, 0.2, IntentKind.NONE),
    ("sign", 1.5, 0.0, IntentKind.NONE),
    ("sign", 1.5, NAN, IntentKind.NONE),
])
def test_confirm_range(mode, z, zr, expected):
    i = run(feat(z=z, zr=zr), confirm_range=mode)
    assert i.kind is expected
    if expected is IntentKind.NONE:
        assert i.reason == "range_confirm"


@pytest.mark.parametrize("ev,mult", [(2.0, 1.0), (2.01, 0.5), (-1.0, 1.0), (NAN, 1.0), (5.0, 0.5)])
def test_shrink(ev, mult):
    assert run(feat(ev=ev)).size_mult == mult


def test_approx_blocks_entry_and_exits_position():
    i = run(feat(), approx=False)
    assert i.kind is IntentKind.NONE and i.reason == "approx"
    assert run(feat(z=0.1), approx=False).reason == "no_signal"
    j = run(feat(), p=pos(), approx=False)
    assert j.kind is IntentKind.EXIT and j.reason == "approx"


def test_no_quote():
    assert run(feat(), q=None).reason == "no_quote"
    assert run(feat(), q=None).kind is IntentKind.NONE
    i = run(feat(z=-2), q=None, p=pos(Side.BUY))
    assert i.kind is IntentKind.EXIT and i.reason == "no_quote"
    assert run(feat(), q=None, p=pos(Side.BUY)).kind is IntentKind.HOLD  # hold needs no quote


@pytest.mark.parametrize("held,z,kind,side", [
    (Side.BUY, 1.5, IntentKind.HOLD, Side.BUY),
    (Side.SELL, -1.5, IntentKind.HOLD, Side.SELL),
    (Side.BUY, -1.5, IntentKind.FLIP, Side.SELL),
    (Side.SELL, 1.5, IntentKind.FLIP, Side.BUY),
    (Side.BUY, 0.5, IntentKind.EXIT, None),
    (Side.BUY, NAN, IntentKind.EXIT, None),
])
def test_position_rules(held, z, kind, side):
    i = run(feat(z=z), p=pos(held))
    assert (i.kind, i.side) == (kind, side)


def test_feature_none_with_position_exits():
    i = run(None, p=pos())
    assert i.kind is IntentKind.EXIT and i.reason == "no_feature"


def test_hold_carries_no_time_and_no_rollover():
    i = run(feat(), p=pos(), grace_s=2)
    assert i.kind is IntentKind.HOLD and i.expiry_ms is None  # OrderManager owns exit_at
    assert strategy.next_exit_at_ms(SLOT_END_MS, 5) == SLOT_END_MS + 305_000
    j = run(feat(), p=pos(), rollover_same_side=False)
    assert j.kind is IntentKind.EXIT and j.reason == "no_rollover"


def test_taker_vs_maker_prices():
    t = run(feat(z=2))
    assert t.limit_price is None and t.expiry_ms is None
    m = run(feat(z=2), exec="maker", limit_ttl_s=120)
    assert (m.limit_price, m.expiry_ms) == (200_000_000, NOW + 120_000)
    s = run(feat(z=-2), exec="maker")
    assert s.limit_price == 200_002_000
    f = run(feat(z=-2), p=pos(Side.BUY), exec="maker", limit_ttl_s=60)
    assert f.kind is IntentKind.FLIP and f.limit_price == 200_002_000
    assert f.expiry_ms == NOW + 60_000
    assert run(feat(z=0.1), p=pos(), exec="maker").limit_price is None  # exit


def test_stop_in_intent_is_int():
    i = run(feat(z=2))
    assert isinstance(i.stop_rel, int) and i.stop_rel > 0


def vol_const(v, n=12):
    return [v] * n


def test_sigma_bar_formula():
    sb = strategy.sigma_bar(vol_const(20.0))
    assert sb == pytest.approx(0.20 / math.sqrt(3_153_600) * math.sqrt(30))
    assert strategy.sigma_bar(vol_const(20.0, 5)) is None
    assert strategy.sigma_bar([NAN] * 12) is None
    assert strategy.sigma_bar(None) is None
    # median of last 12 only
    assert strategy.sigma_bar([1000.0] * 20 + vol_const(20.0)) == pytest.approx(sb)


def test_stop_vol_term_floor_cap_fallback():
    s = Settings()
    q = quote()
    mid = (q.bid + q.ask) / 2
    sb = strategy.sigma_bar(vol_const(20.0))
    assert stop_distance(q, spec(), s, vol_const(20.0)) == round(4 * sb * mid)
    # fallback 60 bp
    assert stop_distance(q, spec(), s, None) == round(0.006 * mid)
    assert stop_distance(q, spec(), s, [1.0, 2.0]) == round(0.006 * mid)
    # tiny vol: spread floor 3 x spread wins
    wide = quote(200_000_000, 200_100_000)
    assert stop_distance(wide, spec(), s, vol_const(0.001)) == 300_000
    # broker minimum wins (POINTS, digits=2 -> 1000 per point)
    assert stop_distance(q, spec(sl_distance=5000), s, vol_const(0.001)) == 5_000_000
    # percentage minimum
    pct = stop_distance(q, spec(sl_distance=100_000, distance_set_in="PERCENTAGE"), s,
                        vol_const(0.001))
    assert pct == round(mid * 0.01)
    # cap at 200 bp of the vol term
    assert stop_distance(q, spec(), s, vol_const(5000.0)) == round(0.02 * mid)
    # min 1
    assert stop_distance(Quote(1, 10, 10, 0), spec(), s, vol_const(0.0000001)) >= 1


def test_taker_shadow():
    q0 = Quote(41, 1000, 1002, 0)
    q1 = Quote(41, 1010, 1012, 1)
    assert taker_shadow(q0, q1, Side.BUY) == pytest.approx((1010 - 1002) / 1002 * 1e4)
    assert taker_shadow(q0, q1, Side.SELL) == pytest.approx(-(1012 - 1000) / 1000 * 1e4)
    assert math.isnan(taker_shadow(None, q1, Side.BUY))
    assert math.isnan(taker_shadow(Quote(41, 0, 0, 0), q1, Side.BUY))
