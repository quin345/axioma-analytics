"""report.py on a synthetic journal with hand-checkable numbers."""
import json
import random
import sqlite3

import numpy as np
import pytest

from autotrade import report
from autotrade.config import Settings
from autotrade.journal import Journal
from autotrade.types import RunMode, Side

T0 = 1_800_000_000_000
SLOT = 300_000
N = 144                       # 12 h = three 4 h blocks


def bar_row(sym, k, mid):
    return (sym, T0 + k * SLOT, 1, 100, mid, mid, mid, mid, 0.1, 0.1, 0.0, 10.0, 100.0, 99.0, 101.0, 1000.0, 0.0)


@pytest.fixture
def jr(tmp_path):
    j = Journal(tmp_path / "t.db", tmp_path / "t.jsonl")
    j.start_run(RunMode.DRY_RUN, Settings())
    rng = random.Random(7)
    s = [rng.uniform(-1, 1) for _ in range(N)]
    mids = [100 - 2 * x for x in s]            # poc 100, va width 2 -> sig_poc == s exactly
    j.db.executemany("INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     [bar_row(1, k, m) for k, m in enumerate(mids)])
    j.db.commit()
    j.mids = mids
    yield j
    j.close()


def add_signal(j, k, z, **kw):
    f = dict(ts_ms=T0 + k * SLOT + 2000, symbol_id=1, slot_end_ms=T0 + k * SLOT, z_poc=z, z_range=0.0,
             events_z=0.0, spread=0.1, approx_share=0.0, decision="enter", reason="enter")
    f.update(kw)
    j.record_signal(f"1-{k}", **f)


def test_ic_taker_maker_sections(jr):
    j, mids = jr, jr.mids
    # taker shadow: 4 signals (+ one |z| < 1 row that must be ignored)
    add_signal(j, 10, 2.0, taker_pnl_bps=1.0, maker_outcome="fill", maker_fill_price=100.0,
               taker_exit=100.5, cf_return_bps=-2.0)
    add_signal(j, 20, -2.0, taker_pnl_bps=-0.5, maker_outcome="no_fill", cf_return_bps=3.0)
    add_signal(j, 30, 3.0, taker_pnl_bps=0.5, maker_outcome="fill", maker_fill_price=101.0,
               taker_exit=100.0, cf_return_bps=-1.0)
    add_signal(j, 40, -1.5, taker_pnl_bps=-1.0, maker_outcome="no_fill", cf_return_bps=1.0)
    add_signal(j, 50, 0.4, taker_pnl_bps=9.0, maker_outcome="fill", maker_fill_price=1.0, taker_exit=1.0,
               cf_return_bps=9.0)
    r = report.compute_report(j.db)
    assert r["entry_z"] == 1.0 and r["bars"] == N
    assert r["signals"]["rows"] == 5 and r["signals"]["signals"] == 4

    # mean reversion by construction: next return is anti-correlated with the previous level
    ic = r["ic"]
    assert ic["blocks"] == 3 and ic["ic"] > 0.4 and ic["t"] > 1

    tk = r["taker"]
    assert tk["net"]["n"] == 4 and tk["net"]["mean"] == pytest.approx(0.0)       # (1 - .5 + .5 - 1) / 4
    exp_gross = np.mean([np.sign(z) * (mids[k + 1] - mids[k]) / mids[k] * 1e4
                         for k, z in ((10, 2.0), (20, -2.0), (30, 3.0), (40, -1.5))])
    assert tk["gross"]["mean"] == pytest.approx(exp_gross)

    mk = r["maker"]
    assert (mk["signals"], mk["fills"], mk["fill_rate"]) == (4, 2, 0.5)
    pnl = [(+1) * (100.5 - 100.0) / 100.0 * 1e4, (+1) * (100.0 - 101.0) / 101.0 * 1e4]
    assert mk["pnl_per_fill"]["mean"] == pytest.approx(np.mean(pnl))
    assert mk["cf_filled"]["mean"] == pytest.approx(-1.5) and mk["cf_unfilled"]["mean"] == pytest.approx(2.0)
    assert mk["adverse_bps"] == pytest.approx(-3.5)


def test_trades_risk_and_render(jr):
    j = jr
    j.record_trade(symbol_id=1, side=Side.BUY, volume=100, signal_id="a", client_order_id="c1", entry_ms=1,
                   exit_ms=2, entry_price=100.0, exit_price=100.1, reason="time", pnl_bps_mid=9.0,
                   pnl_bps_price=10.0, pnl_net=0.5, money_digits=2)
    j.record_trade(symbol_id=1, side=Side.SELL, volume=100, signal_id="b", client_order_id="c2", entry_ms=3,
                   exit_ms=4, entry_price=100.0, exit_price=100.3, reason="stop", pnl_bps_mid=-29.0,
                   pnl_bps_price=-30.0, pnl_net=-1.5, money_digits=2)
    j.record_risk_event("order_reject", "x", 1)
    j.record_risk_event("order_reject", "y", 1)
    j.record_risk_event("kill", "KILL file")
    r = report.compute_report(j.db)
    t = r["trades"]["DRY_RUN"]
    assert t["trades"] == 2 and t["pnl_bps"]["mean"] == pytest.approx(-10.0)
    assert t["pnl_net_sum"] == pytest.approx(-1.0) and t["stop_share"] == 0.5
    assert t["reasons"] == {"stop": 1, "time": 1}
    assert r["risk_events"] == {"order_reject": 2, "kill": 1}
    text = report.render(r)
    for needle in ("forward IC", "taker shadow", "maker sim", "DRY_RUN: trades", "order_reject", "+0.39", "-0.31"):
        assert needle in text


def test_entry_z_from_run_config_and_empty_journal(tmp_path):
    j = Journal(tmp_path / "e.db", tmp_path / "e.jsonl")
    j.start_run(RunMode.DEMO, config_json=json.dumps({"entry_z": 1.5}))
    assert report._entry_z(j.db) == 1.5
    r = report.compute_report(j.db)               # nothing journalled: must not raise
    assert r["bars"] == 0 and r["signals"]["rows"] == 0 and r["ic"]["blocks"] == 0
    assert "none" in report.render(r)
    j.close()
