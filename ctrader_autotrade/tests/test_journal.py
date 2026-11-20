import json
from datetime import datetime, timezone

from autotrade.config import Settings, Secrets
from autotrade.journal import Journal, settings_to_json
from autotrade.types import Bar, Bucket, RunMode, Side

DAY = 86_400_000
T0 = 1_800_000_000_000 - 1_800_000_000_000 % DAY      # a UTC midnight


def mk(tmp_path):
    return Journal(tmp_path / "state" / "a.db", tmp_path / "logs" / "a.jsonl", max_bytes=2000, backups=2)


def trade(j, exit_ms, pnl):
    j.record_trade(symbol_id=1, side=Side.BUY, volume=100, entry_ms=exit_ms - 1, exit_ms=exit_ms,
                   pnl_bps_price=pnl, reason="time")


def test_buckets_roundtrip(tmp_path):
    j = mk(tmp_path)
    bs = [Bucket(1, 10_000 * i, 100 + i, 101 + i, i, "live") for i in range(5)] + [Bucket(2, 0, None, None, 0, "tick")]
    j.save_buckets(bs)
    j.save_buckets([Bucket(1, 10_000, 5, 6, 9, "live")])            # replace on PK
    got = j.load_buckets(1, 10_000)
    assert [b.start_ms for b in got] == [10_000, 20_000, 30_000, 40_000]
    assert got[0] == Bucket(1, 10_000, 5, 6, 9, "live")
    assert j.load_buckets(2, 0) == [Bucket(2, 0, None, None, 0, "tick")]


def test_today_aggregates(tmp_path):
    j = mk(tmp_path)
    trade(j, T0 - 1000, -5.0)                 # yesterday
    trade(j, T0 + 1000, 3.0)
    trade(j, T0 + 2000, -1.0)
    trade(j, T0 + 3000, -2.0)
    now = T0 + 10_000
    assert j.trades_today(now) == 3
    assert j.pnl_today_bps(now) == 0.0
    assert j.consec_losses() == 2
    trade(j, T0 + 4000, 1.0)
    assert j.consec_losses() == 0


def test_orders_and_idempotency(tmp_path):
    j = mk(tmp_path)
    j.start_run(RunMode.DRY_RUN, Settings(), account_id=5, is_live=False, broker="sim")
    j.record_order("A-1-E", ts_ms=T0 + 1000, symbol_id=1, side=Side.BUY, volume=100)
    j.record_order("A-2-E", ts_ms=T0 + 50_000, symbol_id=1, side=Side.SELL, volume=100)
    assert j.has_client_order_id("A-1-E") and not j.has_client_order_id("nope")
    assert j.orders_last_min(T0 + 70_000) == 1
    assert j.orders_last_min(T0 + 55_000) == 2
    j.update_order("A-1-E", status="ORDER_FILLED", order_id=9)
    assert j.db.execute("SELECT status, side FROM orders WHERE client_order_id='A-1-E'").fetchone() == \
        ("ORDER_FILLED", "BUY")


def test_signals_bars_risk_and_run(tmp_path):
    j = mk(tmp_path)
    rid = j.start_run(RunMode.DRY_RUN, Settings(), account_id=5, is_live=False, broker="sim", sha="abc")
    j.record_signal("s1", ts_ms=1, symbol_id=1, z_poc=1.5, decision="enter", maker_outcome="no_fill")
    j.update_signal("s1", taker_pnl_bps=-0.3, cf_return_bps=0.2)
    assert j.db.execute("SELECT z_poc, taker_pnl_bps, cf_return_bps, run_id FROM signals").fetchone() == \
        (1.5, -0.3, 0.2, rid)
    j.record_bar(Bar(1, datetime(2026, 11, 20, tzinfo=timezone.utc), True, 5, 1, 2, 0.5, 1.5, .1, .1, .01, .02,
                     1.2, 1.0, 1.4, 100.0, 0.0))
    assert j.db.execute("SELECT mid_close, slot_end_ms FROM bars").fetchone()[0] == 1.5
    j.record_risk_event("order_reject", {"code": "X"}, 1)
    j.record_fill(ts_ms=1, symbol_id=1, price=1.0, volume=1)
    j.end_run()
    row = j.db.execute("SELECT mode, git_sha, account_id, is_live, broker, ended_ms FROM runs").fetchone()
    assert row[:5] == ("DRY_RUN", "abc", 5, 0, "sim") and row[5] is not None
    assert j.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_no_secrets_written(tmp_path):
    s = Settings(secrets=Secrets("CID-SECRET", "CSEC-SECRET", "TOK-SECRET", 12345, "REF-SECRET"))
    j = mk(tmp_path)
    j.start_run(RunMode.DRY_RUN, s, broker="sim")
    j.log("probe", access_token="TOK-SECRET", nested={"clientSecret": "CSEC-SECRET"})
    j.close()
    blob = (tmp_path / "logs" / "a.jsonl").read_text() + settings_to_json(s)
    for needle in ("CID-SECRET", "CSEC-SECRET", "TOK-SECRET", "REF-SECRET"):
        assert needle not in blob
    assert all(json.loads(line) for line in (tmp_path / "logs" / "a.jsonl").read_text().splitlines())


def test_jsonl_rotates(tmp_path):
    j = mk(tmp_path)
    for i in range(200):
        j.log("x", i=i, pad="y" * 50)
    j.close()
    assert (tmp_path / "logs" / "a.jsonl.1").exists()
