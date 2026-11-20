"""autotrade/daily.py (`autotrade trades-report`) on synthetic journals with hand-checkable numbers."""
import json
import os
import stat
from datetime import date

import pytest

from autotrade import __main__ as cli
from autotrade import daily
from autotrade.config import Settings
from autotrade.journal import Journal
from autotrade.types import RunMode, Side

D0 = daily.day_bounds(date(2026, 11, 18))[0]       # 2026-11-18 00:00:00 UTC
DAY = daily.DAY_MS
H = 3_600_000
NOW = D0 + 5 * DAY + 20 * 60_000                    # 2026-11-23 00:20 UTC: the cron time


def settings_for(tmp_path, name="ax5m", tf="5m"):
    return Settings(instance_name=name, instance_timeframe=tf, state_dir=str(tmp_path / name / "state"),
                    log_dir=str(tmp_path / name / "logs"))


class Jr:
    """A journal whose clock the test sets (run start/end times)."""

    def __init__(self, tmp_path, name="ax5m", tf="5m"):
        self.settings = settings_for(tmp_path, name, tf)
        self.t = D0
        self.j = Journal(tmp_path / f"{name}.db", tmp_path / f"{name}.jsonl", time_fn=lambda: self.t / 1000)
        self.path = tmp_path / f"{name}.db"
        self.n = 0

    def run(self, mode, start, end=None):
        self.t = start
        rid = self.j.start_run(mode, self.settings)
        if end is not None:
            self.t = end
            self.j.end_run()
        return rid

    def trade(self, entry, exit_, net=1.0, bp=5.0, sym=41, side=Side.BUY, vol=200, reason="time", z=1.2,
              sim=None, md=2, mid=None, spread=0.05, **kw):
        self.n += 1
        sid = f"{sym}-{self.n}"
        if z is not None:
            self.j.record_signal(sid, ts_ms=entry - 2000, symbol_id=sym, slot_end_ms=entry - 2000, z_poc=z, z_range=0.0,
                                 events_z=0.0, spread=spread, approx_share=0.0, decision="enter", reason="enter",
                                 taker_pnl_bps=sim)
        name = "XAUUSD" if sym == 41 else "BTCUSD"
        self.j.record_trade(symbol_id=sym, side=side, volume=vol, signal_id=sid if z is not None else "none",
                            client_order_id=f"{self.settings.instance_name}-{name}-{1795000000 + self.n}-E",
                            position_id=self.n, entry_ms=entry, exit_ms=exit_, entry_price=100.0, exit_price=100.1,
                            reason=reason, pnl_bps_mid=bp if mid is None else mid, pnl_bps_price=bp, pnl_net=net,
                            gross=net + 0.1, commission=-0.1, swap=0.0, money_digits=md, adverse_bps=0.5,
                            markout_bps=1.5, **kw)


@pytest.fixture
def jr(tmp_path):
    x = Jr(tmp_path)
    yield x
    x.j.close()


def day(x, d=date(2026, 11, 18), now=NOW, kill=None):
    conn = daily.open_ro(x.path)
    try:
        return daily.compute_day(conn, x.settings, d, now, kill)
    finally:
        conn.close()


def test_trades_belong_to_the_utc_day_of_their_exit(jr):
    jr.run(RunMode.DEMO, D0 - H, D0 + 2 * DAY)
    jr.trade(D0 + DAY - 600_000, D0 + DAY - 1, net=2.0)          # exits 23:59:59.999 on the 18th
    jr.trade(D0 + DAY - 300_000, D0 + DAY, net=-1.0)             # exits 00:00:00.000 on the 19th: opened the day before
    r18, r19 = day(jr), day(jr, date(2026, 11, 19))
    assert (r18["summary"]["trades"], r18["summary"]["net"]) == (1, 2.0)
    assert (r19["summary"]["trades"], r19["summary"]["net"]) == (1, -1.0)
    assert r19["trades"][0]["hold_s"] == 300.0 and r19["trades"][0]["exit"] == "2026-11-19T00:00:00Z"
    assert r18["meta"]["day_start"] == "2026-11-18T00:00:00Z" and r18["meta"]["day_end"] == "2026-11-19T00:00:00Z"
    assert r18["cumulative"]["trades"] == 1 and r19["cumulative"]["trades"] == 2     # up to each day's end
    assert r19["cumulative"]["net"] == 1.0


def test_dry_run_trades_are_excluded_and_counted(jr):
    jr.run(RunMode.DRY_RUN, D0, D0 + H)
    jr.trade(D0 + 10, D0 + 20, net=50.0)
    jr.run(RunMode.DEMO, D0 + 2 * H, D0 + 3 * H)
    jr.trade(D0 + 2 * H + 10, D0 + 2 * H + 20, net=-3.0)
    r = day(jr)
    s = r["summary"]
    assert (s["trades"], s["net"], s["excluded_dry_run_trades"]) == (1, -3.0, 1)
    assert r["cumulative"]["trades"] == 1 and r["cumulative"]["net"] == -3.0
    assert r["meta"]["runs"] == {"count": 2, "modes": ["DEMO", "DRY_RUN"], "restarts": 1, "started_in_day": 2}
    assert "dry_run trades excluded: 1" in daily.render(r)


def test_empty_day_report_and_days_before_the_first_run(jr):
    jr.run(RunMode.DEMO, D0 + H, D0 + 3 * DAY)                   # runs through the 18th, 19th, 20th, 21st
    r = day(jr, date(2026, 11, 19))
    s = r["summary"]
    assert s["trades"] == 0 and s["win_rate"] is None and s["net"] == 0.0 and s["profit_factor"] is None
    assert r["trades"] == [] and r["breakdown"]["by_symbol"] == {} and r["meta"]["runs"]["count"] == 1
    assert "trades 0" in daily.render(r)
    conn = daily.open_ro(jr.path)
    now = NOW
    assert daily.resolve_days(conn, "2026-11-17", False, False, False, now) == []    # before the first run
    assert daily.resolve_days(conn, "2026-11-18", False, False, False, now) == [date(2026, 11, 18)]
    assert daily.resolve_days(conn, None, True, False, False, now) == [date(2026, 11, 22)]   # yesterday: a heartbeat
    assert daily.resolve_days(conn, None, False, False, True, now) == [date(2026, 11, 23)]
    with pytest.raises(ValueError):
        daily.resolve_days(conn, "2026-12-01", False, False, False, now)
    conn.close()


def test_backfill_enumerates_days_with_runs_or_trades_up_to_yesterday(jr):
    jr.run(RunMode.DEMO, D0 + H, D0 + DAY + H)                   # 18th and 19th
    jr.run(RunMode.DEMO, D0 + 3 * DAY, D0 + 3 * DAY + H)         # the 21st (the 20th has no run)
    jr.run(RunMode.DEMO, D0 + 5 * DAY - H)                       # never ended: last activity bounds it
    jr.trade(D0 + 4 * DAY, D0 + 4 * DAY + 5)                     # a trade on the 22nd (exit day)
    conn = daily.open_ro(jr.path)
    got = daily.enumerate_days(conn, NOW)
    conn.close()
    assert got == [date(2026, 11, d) for d in (18, 19, 21, 22)]  # today (23rd) excluded, the 20th had nothing


def test_money_digits_are_reported_not_rescaled(jr):
    jr.run(RunMode.DEMO, D0, D0 + H)
    jr.trade(D0 + 10, D0 + 20, net=1.2345, md=4)
    jr.trade(D0 + 30, D0 + 40, net=-0.5, md=2)
    s = day(jr)["summary"]
    assert s["net"] == pytest.approx(0.7345) and s["money_digits"] == [2, 4]        # stored amounts are already scaled
    assert s["gross"] == pytest.approx(1.3345 - 0.4) and s["commission"] == pytest.approx(-0.2)


def test_summary_statistics(jr):
    jr.run(RunMode.DEMO, D0, D0 + DAY)
    nets, bps = [4.0, -2.0, 3.0, -6.0], [10.0, -5.0, 8.0, -20.0]
    for k, (n, b) in enumerate(zip(nets, bps)):
        jr.trade(D0 + k * 1000, D0 + k * 1000 + 100_000, net=n, bp=b, reason="stop" if n < 0 and k == 3 else "time",
                 mid=b + 1)
    s = day(jr)["summary"]
    assert (s["trades"], s["wins"], s["losses"], s["flat"]) == (4, 2, 2, 0) and s["win_rate"] == 0.5
    assert s["net"] == -1.0 and s["avg_win"] == 3.5 and s["avg_loss"] == -4.0
    assert s["profit_factor"] == pytest.approx(7 / 8)
    assert s["avg_bp_price"] == pytest.approx(-1.75) and s["median_bp_price"] == pytest.approx(1.5)
    assert s["avg_bp_mid"] == pytest.approx(-0.75)
    assert s["max_drawdown"] == 6.0                               # cum 4, 2, 5, -1: peak 5 -> -1
    assert s["best_trade"]["net"] == 4.0 and s["worst_trade"]["net"] == -6.0
    assert s["avg_hold_s"] == 100.0 and s["max_hold_s"] == 100.0 and s["volume"] == 800
    assert s["stop_share"] == 0.25
    assert s["t_stat_bp"] == pytest.approx(-1.75 / (__import__("statistics").stdev(bps) / 2), abs=1e-5)


def test_t_stat_needs_three_trades(jr):
    jr.run(RunMode.DEMO, D0, D0 + DAY)
    jr.trade(D0 + 1, D0 + 2, bp=5.0)
    jr.trade(D0 + 3, D0 + 4, bp=-1.0)
    assert day(jr)["summary"]["t_stat_bp"] is None
    assert "t n/a" in daily.render(day(jr))


def test_breakdowns_execution_quality_and_funnel(jr):
    jr.run(RunMode.DEMO, D0, D0 + DAY)
    jr.trade(D0 + 1 * H, D0 + 1 * H + 300_000, net=1.0, bp=4.0, sym=41, z=1.2, sim=3.0)
    jr.trade(D0 + 2 * H, D0 + 2 * H + 300_000, net=-1.0, bp=-6.0, sym=10026, side=Side.SELL, vol=2, z=1.7, sim=-2.0)
    jr.trade(D0 + 2 * H + 10, D0 + 2 * H + 400_000, net=0.5, bp=1.0, sym=10026, vol=1, z=2.5, reason="stop", sim=2.0)
    jr.trade(D0 + 3 * H, D0 + 3 * H + 300_000, net=0.2, bp=1.0, z=None)                 # no signal link
    b = day(jr)["breakdown"]
    assert set(b["by_symbol"]) == {"XAUUSD", "BTCUSD"} and b["by_symbol"]["BTCUSD"]["trades"] == 2
    assert b["by_side"]["SELL"]["trades"] == 1 and b["by_side"]["BUY"]["trades"] == 3
    assert b["by_exit_reason"]["stop"]["trades"] == 1 and b["by_exit_reason"]["time"]["trades"] == 3
    assert list(b["by_hour_utc"]) == ["01", "02", "03"] and b["by_hour_utc"]["02"]["trades"] == 2
    assert {k: v["trades"] for k, v in b["by_abs_z"].items()} == {"1-1.5": 1, "1.5-2": 1, ">2": 1, "unlinked": 1}
    assert b["by_size"]["full"]["trades"] == 3 and b["by_size"]["half"]["trades"] == 1      # BTC full = 2, half = 1
    e = day(jr)["execution_quality"]
    assert e["slippage_bp"]["n"] == 3 and e["slippage_bp"]["mean"] == pytest.approx((1 - 4 - 1) / 3)
    assert e["avg_adverse_bp"] == 0.5 and e["avg_markout_bp"] == 1.5
    assert e["avg_spread_entry_bp"] == pytest.approx(0.05 / 100 * 1e4)                        # price units to bp
    assert e["orders"] == 0 and e["rejects_or_errors"] == 0


def test_orders_rejects_funnel_and_risk_events(jr):
    jr.run(RunMode.DEMO, D0, D0 + DAY)
    t = D0 + H
    jr.j.record_order("a-1", ts_ms=t, symbol_id=41, side="BUY", volume=1, kind="ENTRY", order_type="MARKET",
                      status="ORDER_FILLED")
    jr.j.record_order("a-2", ts_ms=t + 1, symbol_id=41, side="BUY", volume=1, kind="ENTRY", order_type="MARKET",
                      status="ORDER_REJECTED", error_code="TRADING_BAD_VOLUME")
    for k, (z, dec, why) in enumerate([(2.0, "enter", "enter"), (1.5, "hold", "rollover"), (1.4, "none", "approx"),
                                       (1.3, "none", "kql_stale"), (1.2, "none", "max_positions"),
                                       (1.1, "none", "no_signal"), (0.2, "none", "no_signal")]):
        jr.j.record_signal(f"s{k}", ts_ms=t + k, symbol_id=41, slot_end_ms=t, z_poc=z, decision=dec, reason=why)
    jr.j.record_signal("late", ts_ms=D0 + DAY + 5, symbol_id=41, slot_end_ms=0, z_poc=3.0, decision="enter", reason="enter")
    jr.j.record_risk_event("order_reject", "x token=SECRET123 at /root/axioma-analytics/.env " + "y" * 400, 41, ts_ms=t)
    for k in range(7):
        jr.j.record_risk_event("data_gate", f"gap {k}", ts_ms=t + k)
    r = day(jr)
    e = r["execution_quality"]
    assert (e["orders"], e["rejects_or_errors"], e["error_codes"]) == (2, 1, {"TRADING_BAD_VOLUME": 1})
    assert e["order_status"] == {"ORDER_FILLED": 1, "ORDER_REJECTED": 1}
    f = r["funnel"]
    assert (f["signal_rows"], f["above_entry_z"], f["entered"], f["rollover"], f["gated"], f["none"]) == (7, 6, 1, 1, 3, 1)
    assert f["gated_reasons"] == {"approx": 1, "kql_stale": 1, "max_positions": 1}
    ev = r["risk_events"]
    assert ev["data_gate"]["count"] == 7 and len(ev["data_gate"]["last"]) == daily.MAX_EVENT_DETAILS
    d = ev["order_reject"]["last"][0]["detail"]
    assert len(d) <= daily.DETAIL_CHARS and "SECRET123" not in d and "/root" not in d and "token=***" in d


def test_both_instances_have_their_own_streams(tmp_path):
    a, b = Jr(tmp_path, "ax5m", "5m"), Jr(tmp_path, "ax1m", "1m")
    for x, n in ((a, 1), (b, 3)):
        x.run(RunMode.DEMO, D0, D0 + DAY)
        for k in range(n):
            x.trade(D0 + k * 1000, D0 + k * 1000 + 500, net=-1.0, bp=-2.0)
    ra, rb = day(a), day(b)
    assert (ra["meta"]["instance"], ra["meta"]["timeframe"], ra["meta"]["strategy"]) == ("ax5m", "5m", "poc_reversion_4h")
    assert (rb["meta"]["instance"], rb["meta"]["timeframe"], rb["meta"]["strategy"]) == ("ax1m", "1m", "poc_reversion_1h")
    assert "axq-poc4h-v1" in ra["meta"]["labels"] and rb["meta"]["labels"] == ["ax1m"]
    assert (ra["summary"]["trades"], rb["summary"]["trades"]) == (1, 3)
    assert ra["research_expectation"]["taker_net_bp_per_trade"] == -0.31
    assert rb["research_expectation"]["taker_net_bp_per_trade"] == -0.84
    assert rb["cumulative"]["vs_research_bp"] == pytest.approx(-2.0 + 0.84)
    a.j.close()
    b.j.close()


def test_kill_switch_numbers_and_na_for_old_days(jr):
    jr.run(RunMode.DEMO, D0, D0 + 3 * DAY)
    jr.trade(D0 + 1, D0 + 2, net=-10.0)
    conn = daily.open_ro(jr.path)
    kill = daily._kill_numbers(conn, jr.settings, D0 + H)
    conn.close()
    assert kill["available"] and kill["max_loss"] == 500 and kill["unrealised"] is None
    assert kill["unrealised_note"] and kill["realised"] is None                     # no baseline file in this test
    assert kill["today"] == -10.0 and kill["distance_to_daily_limit"] == 190.0
    assert day(jr, kill=kill)["kill_switch"]["daily_max_loss"] == 200
    assert day(jr)["kill_switch"]["available"] is False                              # not attached: n/a
    assert "kill switch: n/a" in daily.render(day(jr))


def test_partial_today(jr):
    jr.run(RunMode.DEMO, D0, None)
    jr.trade(D0 + 1, D0 + 2, net=1.0)
    jr.trade(D0 + 5 * H, D0 + 5 * H + 1, net=9.0)                                    # "in the future"
    r = day(jr, now=D0 + H)
    assert r["meta"]["partial"] is True and r["summary"]["trades"] == 1 and r["meta"]["as_of"] == "2026-11-18T01:00:00Z"
    assert "PARTIAL" in daily.render(r)


def test_cap_of_500_trade_rows(jr):
    jr.run(RunMode.DEMO, D0, D0 + DAY)
    for k in range(daily.MAX_TRADE_ROWS + 7):
        jr.trade(D0 + k * 100, D0 + k * 100 + 50, net=1.0)
    r = day(jr)
    assert r["summary"]["trades"] == 507 and r["trades_total"] == 507
    assert len(r["trades"]) == daily.MAX_TRADE_ROWS and r["trades_truncated"] is True
    assert "(500 of 507)" in daily.render(r)


def _files(d):
    return sorted(p.name for p in d.iterdir())


def test_write_index_latest_permissions_and_idempotence(jr, tmp_path):
    jr.run(RunMode.DEMO, D0, D0 + 2 * DAY)
    jr.trade(D0 + 10, D0 + 20, net=2.0, bp=3.0)
    jr.trade(D0 + DAY + 10, D0 + DAY + 20, net=-1.0, bp=-1.0)
    out = tmp_path / "reports" / "ax5m"
    old = os.umask(0o077)                                                            # a strict umask must not matter
    try:
        conn = daily.open_ro(jr.path)
        daily.run(conn, jr.settings, out, [date(2026, 11, 18), date(2026, 11, 19)], NOW)
        conn.close()
    finally:
        os.umask(old)
    assert _files(out) == ["2026-11-18.json", "2026-11-19.json", "index.json", "latest.json"]
    assert stat.S_IMODE(out.stat().st_mode) == 0o755 and stat.S_IMODE(out.parent.stat().st_mode) == 0o755
    for p in out.iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o644
    idx = json.loads((out / "index.json").read_text())
    assert idx["schema_version"] == 1 and idx["instance"] == "ax5m" and idx["latest"] == "2026-11-19"
    assert [d["date"] for d in idx["days"]] == ["2026-11-18", "2026-11-19"]
    assert [d["cum_net"] for d in idx["days"]] == [2.0, 1.0] and idx["days"][0]["trades"] == 1
    assert idx["research_taker_net_bp"] == -0.31 and idx["kill_switch"]["available"] is True
    assert json.loads((out / "latest.json").read_text())["meta"]["date"] == "2026-11-19"
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    conn = daily.open_ro(jr.path)
    daily.run(conn, jr.settings, out, [date(2026, 11, 18)], NOW)                     # rewriting a day: same bytes
    conn.close()
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before


def test_partial_day_is_in_the_index_but_not_latest(jr, tmp_path):
    jr.run(RunMode.DEMO, D0, None)
    jr.trade(D0 + 10, D0 + 20)
    jr.trade(D0 + DAY + 10, D0 + DAY + 20)
    out = tmp_path / "r"
    conn = daily.open_ro(jr.path)
    daily.run(conn, jr.settings, out, [date(2026, 11, 18)], D0 + DAY + H)
    daily.run(conn, jr.settings, out, [date(2026, 11, 19)], D0 + DAY + H)            # today: partial
    conn.close()
    idx = json.loads((out / "index.json").read_text())
    assert [(d["date"], d["partial"]) for d in idx["days"]] == [("2026-11-18", False), ("2026-11-19", True)]
    assert idx["latest"] == "2026-11-18"
    assert json.loads((out / "latest.json").read_text())["meta"]["date"] == "2026-11-18"


def test_write_is_atomic(jr, tmp_path, monkeypatch):
    jr.run(RunMode.DEMO, D0, D0 + DAY)
    jr.trade(D0 + 10, D0 + 20)
    out = tmp_path / "r"
    conn = daily.open_ro(jr.path)
    daily.run(conn, jr.settings, out, [date(2026, 11, 18)], NOW)
    good = (out / "2026-11-18.json").read_bytes()
    jr.trade(D0 + 30, D0 + 40, net=7.0)

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(daily.os, "replace", boom)
    with pytest.raises(OSError):
        daily.run(conn, jr.settings, out, [date(2026, 11, 18)], NOW)
    conn.close()
    assert (out / "2026-11-18.json").read_bytes() == good                             # the old file is intact
    assert _files(out) == ["2026-11-18.json", "index.json", "latest.json"]            # and no temp file is left behind


def test_no_absolute_paths_or_secrets_in_the_files(jr, tmp_path):
    jr.run(RunMode.DEMO, D0, D0 + DAY)
    jr.trade(D0 + 10, D0 + 20)
    jr.j.record_risk_event("send_failed", f"cannot write {tmp_path}/x password=hunter2", ts_ms=D0 + 30)
    out = tmp_path / "reports" / "ax5m"
    conn = daily.open_ro(jr.path)
    daily.run(conn, jr.settings, out, [date(2026, 11, 18)], NOW)
    conn.close()
    for p in out.iterdir():
        text = p.read_text()
        assert str(tmp_path) not in text and "hunter2" not in text and "/root" not in text, p.name
    assert json.loads((out / "2026-11-18.json").read_text())["meta"]["journal"] == "autotrade.db"


def test_cli_writes_prints_and_validates(tmp_path, make_env, make_toml, capsys):
    x = Jr(tmp_path)
    x.run(RunMode.DEMO, D0, D0 + 3 * DAY)
    x.trade(D0 + 10, D0 + 20, net=2.0)
    x.j.close()
    sd = tmp_path / "state"
    sd.mkdir()
    os.replace(x.path, sd / "autotrade.db")
    env = make_env()
    toml = make_toml(f'state_dir = "{sd}"\nlog_dir = "{tmp_path / "logs"}"\n')
    out = tmp_path / "out"
    base = ["--env", str(env), "--config", str(toml), "trades-report", "--out-dir", str(out)]
    with pytest.raises(SystemExit) as e:
        cli.main(base)                                                                # one of the day options is required
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        cli.main(base + ["--date", "2026-13-40"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        cli.main(base + ["--date", "2026-11-18", "--backfill"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        cli.main(base + ["--date", "2026-11-18", "--stdout"])
    assert e.value.code == 0 and not out.exists()                                      # --stdout writes nothing
    text = capsys.readouterr().out
    assert "ax5m (5m, poc_reversion_4h)  2026-11-18 UTC" in text and "trades 1" in text
    with pytest.raises(SystemExit) as e:
        cli.main(base + ["--date", "2026-11-18"])
    assert e.value.code == 0
    assert _files(out) == ["2026-11-18.json", "index.json", "latest.json"]
    secret_free = "".join(p.read_text() for p in out.iterdir())
    assert "SECRET" not in secret_free
    with pytest.raises(SystemExit) as e:
        cli.main(base + ["--date", "2020-01-01"])                                       # before the first run: nothing
    assert e.value.code == 0 and "nothing to report" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        cli.main(base + ["--backfill"])                                                 # NOW is real time: all past days
    assert e.value.code == 0
    assert "2026-11-18.json" in _files(out)


def test_missing_journal_is_an_error(tmp_path, make_env, make_toml, capsys):
    toml = make_toml(f'state_dir = "{tmp_path / "none"}"\n')
    with pytest.raises(SystemExit) as e:
        cli.main(["--env", str(make_env()), "--config", str(toml), "trades-report", "--last-day"])
    assert e.value.code == 1 and "no journal" in capsys.readouterr().out
