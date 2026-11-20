"""P&L kill switch: baseline file, limits (realised, server and fallback unrealised), profit target,
daily_max_loss, KILL at start, the kill-reset CLI and the report section. Temp dirs only."""
import json
import os
import sqlite3
import stat

import pytest

from autotrade import killswitch, report
from autotrade.__main__ import main
from autotrade.config import ConfigError, Settings, load
from autotrade.runner import EXIT_OK
from autotrade.types import PositionView, RunMode, Side
from ctrader_open_api.messages import OpenApiMessages_pb2 as oa
from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model
from test_e2e import B0, BASE, Harness

ACCT = 12345


@pytest.fixture
def h(tmp_path, make_env, make_toml):
    return lambda **kw: Harness(tmp_path, make_env, make_toml, **kw)


def kill_cfg(**kw):
    return {"kill": {"enabled": True, "max_loss": 500, "profit_target": 0, "daily_max_loss": 0, **kw}}


def seed_trade(x, pnl, exit_ms=BASE + 500, mode="DRY_RUN", account=ACCT):
    """A closed trade of an earlier run (own run row: account and mode are what the kill switch filters on)."""
    con = sqlite3.connect(x.db_path)
    rid = con.execute("INSERT INTO runs (mode, account_id, started_ms) VALUES (?,?,?)",
                      (mode, account, exit_ms - 10)).lastrowid
    con.execute("INSERT INTO trades (run_id, symbol_id, side, volume, exit_ms, pnl_net) VALUES (?,?,?,?,?,?)",
                (rid, 1, "BUY", 100, exit_ms, pnl))
    con.commit()
    con.close()


def events(x, kind):
    return [json.loads(r[0]) if r[0].startswith("{") else r[0]
            for r in x.q("SELECT detail FROM risk_events WHERE kind=?", kind)]


# ---- baseline ----

def test_baseline_created_once_private_and_per_account(tmp_path):
    sd = tmp_path / "s"
    b = killswitch.current_baseline(sd, 111, 5_000, None)
    assert b["source"] == "new" and b["since_ms"] == 5_000 and b["account_id"] == 111
    f = sd / "kill_baseline.json"
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert json.loads(f.read_text()) == {"account_id": 111, "created": "1970-01-01T00:00:05Z", "since_ms": 5000}
    again = killswitch.current_baseline(sd, 111, 99_000, None)
    assert again["source"] == "file" and again["since_ms"] == 5_000
    other = killswitch.current_baseline(sd, 222, 99_000, None)          # account changed: new baseline
    assert other["source"] == "new" and other["since_ms"] == 99_000 and other["account_id"] == 222
    assert killswitch.read_baseline(sd)["account_id"] == 222


def test_since_override_beats_the_file_and_is_not_written(tmp_path):
    sd = tmp_path / "s"
    killswitch.write_baseline(sd, 111, 5_000)
    b = killswitch.current_baseline(sd, 111, 99_000, "2026-11-20T05:00:00Z")
    assert b["source"] == "config" and b["since_ms"] == 1_795_150_800_000
    assert killswitch.read_baseline(sd)["since_ms"] == 5_000
    with pytest.raises(ConfigError, match="kill.since"):
        killswitch.parse_since("yesterday")


@pytest.mark.parametrize("kw", [{"profit_action": "stop"}, {"max_loss": 0}, {"max_loss": -5},
                                {"daily_max_loss": -1}, {"check_interval_s": 0.5}, {"since": "soon"},
                                {"enabled": "yes"}, {"max_loss": "lots"}])
def test_bad_kill_config(make_env, make_toml, kw):
    with pytest.raises(ConfigError, match="kill"):
        load(make_env(), make_toml(), overrides={"kill": kw})


def test_kill_table_is_flattened_with_prefix(make_env, make_toml):
    s = load(make_env(), make_toml('[kill]\nenabled = true\nmax_loss = 7\nprofit_action = "pause"\n'))
    assert (s.kill_enabled, s.kill_max_loss, s.kill_profit_action) == (True, 7, "pause")
    assert load(make_env(), make_toml()).kill_enabled is False


def test_kill_reset_cli_rearms_without_deleting_control_files(tmp_path, make_env, capsys):
    sd = tmp_path / "state"
    sd.mkdir()
    (sd / "KILL").write_text("x")
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'state_dir = "{sd}"\n[kill]\nenabled = true\n')
    killswitch.write_baseline(sd, ACCT, 1_000)
    with pytest.raises(SystemExit) as e:
        main(["--env", str(make_env()), "--config", str(cfg), "kill-reset"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    b = killswitch.read_baseline(sd)
    assert b["account_id"] == ACCT and b["since_ms"] > 1_700_000_000_000
    assert (sd / "KILL").exists() and f"rm {sd / 'KILL'}" in out and "PAUSE" not in out
    assert stat.S_IMODE(os.stat(sd / "kill_baseline.json").st_mode) == 0o600
    assert "SECRET" not in out


# ---- limits through the whole bot (DRY_RUN) ----

def test_baseline_created_at_first_run_and_realised_before_it_ignored(h):
    x = h(preload=False, overrides=kill_cfg(max_loss=500))
    seed_trade(x, -900, exit_ms=BASE - 1)            # before the baseline: not counted
    x.start()
    x.feed(10)
    b = killswitch.read_baseline(x.state)
    assert BASE <= b["since_ms"] < BASE + 500 and b["account_id"] == ACCT
    assert not x.bot.done.called and not (x.state / "KILL").exists()
    st = killswitch.read_status(x.state)
    assert st["realised"] == 0 and st["cumulative"] == 0 and st["unrealised_source"] == "none"
    x.bot.shutdown("test")


def test_realised_breach_kills_flat_and_exits_zero(h):
    x = h(preload=False, overrides=kill_cfg(max_loss=500))
    seed_trade(x, -600)
    x.start()
    assert x.bot.done.called and x.bot.exit_code == EXIT_OK
    assert (x.state / "KILL").exists()
    d = events(x, "kill_max_loss")
    assert len(d) == 1 and d[0]["realised"] == -600 and d[0]["cumulative"] == -600
    assert d[0]["max_loss"] == 500 and d[0]["unrealised"] == 0 and d[0]["daily"] == -600
    assert x.fake.stopped


def test_trades_of_other_account_or_mode_do_not_count(h):
    x = h(preload=False, overrides=kill_cfg(max_loss=500))
    seed_trade(x, -900, account=999)
    seed_trade(x, -900, mode="DEMO")
    x.start()
    x.feed(10)
    assert not x.bot.done.called
    assert killswitch.read_status(x.state)["realised"] == 0
    x.bot.shutdown("test")


def test_unrealised_fallback_pushes_over_the_limit(h):
    # realised alone (-10) is inside the 30 limit; the open shorts, marked at the touch after the
    # price ticks up (~ -2 on gold, ~ -40 on bitcoin), push the total over it
    x = h(overrides=kill_cfg(max_loss=30))
    seed_trade(x, -10)
    x.start()
    x.feed_until(630)                                      # shorts open at ~605, marked at the touch every 5 s
    assert x.bot.done.called and x.bot.exit_code == EXIT_OK and (x.state / "KILL").exists()
    d = events(x, "kill_max_loss")[0]
    assert d["realised"] == -10 and d["unrealised"] < -20 and d["unrealised_source"] == "touch"
    assert d["cumulative"] == pytest.approx(d["realised"] + d["unrealised"], abs=0.02)
    assert [t[0] for t in x.q("SELECT reason FROM trades WHERE run_id=(SELECT MAX(run_id) FROM runs)")] == \
        ["kill", "kill"]


def test_profit_target_kill(h):
    x = h(preload=False, overrides=kill_cfg(profit_target=1000))
    seed_trade(x, 1200)
    x.start()
    assert x.bot.done.called and x.bot.exit_code == EXIT_OK and (x.state / "KILL").exists()
    assert events(x, "kill_profit_target")[0]["cumulative"] == 1200
    assert not events(x, "kill_max_loss")


def test_profit_target_pause_keeps_running_and_blocks_entries(h):
    x = h(overrides=kill_cfg(profit_target=1000, profit_action="pause"))
    seed_trade(x, 1200)
    x.start()
    x.feed_until(610)
    assert not x.bot.done.called and (x.state / "PAUSE").exists() and not (x.state / "KILL").exists()
    assert x.bot.om.open_count() == 0
    assert x.q("SELECT decision, reason FROM signals WHERE slot_end_ms=? AND symbol_id=1", B0 + 600_000)[0] == \
        ("none", "pause_file")
    assert len(events(x, "kill_profit_target")) == 1       # logged once, not every tick
    x.bot.shutdown("test")


def test_daily_max_loss_blocks_entries_and_lifts_at_the_next_reset(h):
    # reset 08:15 UTC = B0 + 900 s: slot 2 (602 s) is inside the loss day, slot 3 (902 s) after the reset
    x = h(overrides={**kill_cfg(daily_max_loss=100), "risk": {"daily_reset": "08:15"}})
    seed_trade(x, -150)
    x.start()
    x.feed_until(610)
    assert not x.bot.done.called and not (x.state / "KILL").exists()
    d = events(x, "daily_max_loss")
    assert len(d) == 1 and d[0]["daily"] == -150
    assert x.bot.om.open_count() == 0
    assert x.q("SELECT decision, reason FROM signals WHERE slot_end_ms=? AND symbol_id=1", B0 + 600_000)[0] == \
        ("none", "daily_max_loss")
    assert x.bot.risk.daily_latched(x.clock.now_ms())
    assert x.bot.risk.state()["daily_latched_until_ms"] == B0 + 900_000
    x.feed_until(910)                                      # past the reset and slot 3
    assert not x.bot.risk.daily_latched(x.clock.now_ms())
    assert x.q("SELECT reason FROM signals WHERE slot_end_ms=? AND symbol_id=1", B0 + 900_000)[0][0] \
        != "daily_max_loss"
    assert len(events(x, "daily_max_loss")) == 1 and not (x.state / "KILL").exists()
    assert killswitch.read_status(x.state)["realised_today"] == 0
    x.bot.shutdown("test")


def test_daily_max_loss_flattens_open_positions(h):
    x = h(overrides=kill_cfg(daily_max_loss=30))
    x.start()
    x.feed_until(630)                                      # the shorts (open at ~605) lose more than 30 unrealised
    assert events(x, "daily_max_loss")[0]["unrealised"] < -30 and not x.bot.done.called
    assert [t[0] for t in x.q("SELECT reason FROM trades")] == ["risk", "risk"]
    assert x.bot.om.open_count() == 0
    x.bot.shutdown("test")


def test_disabled_does_nothing(h):
    x = h(preload=False)
    seed_trade(x, -10_000)
    x.start()
    x.feed(20)
    assert not x.bot.done.called and not (x.state / "kill_baseline.json").exists()
    x.bot.shutdown("test")


# ---- KILL file present at start ----

def test_kill_file_at_start_exits_immediately_without_subscribing(h):
    x = h(preload=False)
    (x.state / "KILL").write_text("")
    x.start()
    assert x.bot.done.called and x.bot.exit_code == EXIT_OK and x.fake.stopped
    assert not x.fake.sent_of(oa.ProtoOASubscribeSpotsReq)
    assert ("kill",) in x.q("SELECT kind FROM risk_events")


def test_kill_file_at_start_flattens_adopted_demo_position(h):
    x = h(preload=False, mode=RunMode.DEMO)
    rec = oa.ProtoOAReconcileRes()
    p = rec.position.add(positionId=9, price=2000.0)
    p.tradeData.symbolId, p.tradeData.volume, p.tradeData.tradeSide = 1, 200, model.BUY
    p.tradeData.label, p.tradeData.openTimestamp = "axq-poc4h-v1", BASE
    x.fake.respond(oa.ProtoOAReconcileReq, rec)
    x.fake.respond(oa.ProtoOAClosePositionReq, oa.ProtoOAExecutionEvent())
    (x.state / "KILL").write_text("")
    x.start()
    assert [m.positionId for m in x.fake.sent_of(oa.ProtoOAClosePositionReq)] == [9]
    assert x.bot.exit_code == EXIT_OK and x.bot._stopping and ("kill",) in x.q("SELECT kind FROM risk_events")
    assert not x.fake.sent_of(oa.ProtoOASubscribeSpotsReq)


# ---- DEMO: the server's unrealised P&L ----

def pnl_res(rows, md=2):
    r = oa.ProtoOAGetPositionUnrealizedPnLRes(ctidTraderAccountId=ACCT, moneyDigits=md)
    for pid, gross, net in rows:
        r.positionUnrealizedPnL.add(positionId=pid, grossUnrealizedPnL=gross, netUnrealizedPnL=net)
    return r


def view(pid=777, entry=2000.0, side=Side.BUY, vol=200):
    return PositionView(1, side, vol, entry, BASE, BASE + 300_000, pid)


def test_server_unrealised_is_used_in_demo_and_foreign_positions_ignored(h):
    x = h(preload=False, mode=RunMode.DEMO, overrides=kill_cfg(max_loss=40))
    x.fake.respond(oa.ProtoOAGetPositionUnrealizedPnLReq,
                   pnl_res([(777, -4400, -4500), (888, -999_999, -999_999)]))
    x.start()
    out = []
    x.bot._unrealised([view()]).addCallback(out.append)
    assert out == [(-45.0, "server")]
    sent = x.fake.sent_of(oa.ProtoOAGetPositionUnrealizedPnLReq)
    assert len(sent) == 1 and sent[0].ctidTraderAccountId == ACCT
    x.bot.shutdown("test")


def test_server_unrealised_breach_kills(h):
    x = h(preload=False, mode=RunMode.DEMO, overrides=kill_cfg(max_loss=40))
    x.fake.respond(oa.ProtoOAGetPositionUnrealizedPnLReq, pnl_res([(777, -4400, -4500)]))
    x.start()
    x.bot.om.position_view = lambda sid: view() if sid == 1 else None
    x.feed(10)                                              # next kill tick sees -45 <= -40
    assert x.bot.done.called and x.bot.exit_code == EXIT_OK and (x.state / "KILL").exists()
    d = events(x, "kill_max_loss")[0]
    assert d["unrealised"] == -45 and d["unrealised_source"] == "server" and d["cumulative"] == -45


def test_server_error_falls_back_to_the_touch_mark(h):
    x = h(mode=RunMode.DEMO, overrides=kill_cfg(max_loss=5000))
    x.fake.respond(oa.ProtoOAGetPositionUnrealizedPnLReq, oa.ProtoOAErrorRes(errorCode="X", description="no"))
    x.start()
    x.feed(10)                                              # quotes flowing (gold bid ~ 2000.0)
    q = x.bot.last_quote[1]
    out = []
    x.bot._unrealised([view(entry=q.bid / 100_000 + 1.0)]).addCallback(out.append)   # long, 1.0 above the bid
    assert out[0][1] == "touch" and out[0][0] == pytest.approx(-2.0)                  # -1.0 * 200 / 100
    x.bot.shutdown("test")


def test_fallback_math_and_unknown_quote_currency(h):
    from autotrade.types import Quote
    q = Quote(1, 199_000_000, 199_020_000, 0)               # bid 1990.00 ask 1990.20
    assert killswitch.unrealised_fallback(view(entry=1991.0), q) == pytest.approx(-2.0)              # long at bid
    assert killswitch.unrealised_fallback(view(entry=1991.0, side=Side.SELL), q) == pytest.approx(1.6)  # short at ask
    assert killswitch.unrealised_fallback(view(), None) is None


def test_quote_asset_differs_from_deposit_asset_means_realised_only(h, caplog):
    import dataclasses
    x = h(preload=False, overrides=kill_cfg(max_loss=500))
    x.start()
    x.bot.specs[1] = dataclasses.replace(x.bot.specs[1], quote_asset_id=99)       # deposit asset is 15
    out = []
    with caplog.at_level("WARNING"):
        x.bot._unrealised([view()]).addCallback(out.append)
        x.bot._unrealised([view()]).addCallback(out.append)
    assert out == [(None, "unknown")] * 2
    assert len([r for r in caplog.records if "differs from the deposit asset" in r.getMessage()]) == 1
    x.bot.shutdown("test")


# ---- report ----

def test_report_kill_section(h):
    x = h(preload=False, overrides=kill_cfg(max_loss=500, profit_target=1000, daily_max_loss=200))
    seed_trade(x, -150)
    x.start()
    x.feed(10)
    x.bot.shutdown("test")
    con = sqlite3.connect(x.db_path)
    r = report.compute_report(con, settings=x.settings, now_ms=x.clock.now_ms())
    con.close()
    k = r["kill"]
    assert BASE <= k["since_ms"] < BASE + 500 and k["realised"] == -150 and k["max_loss"] == 500
    assert k["next_reset_ms"] > k["day_start_ms"] and k["baseline_source"] == "state/kill_baseline.json"
    text = report.render(r)
    assert "Kill switch" in text and "baseline since" in text and "-150.00" in text
    assert "limit <= -200" in text and "stop at <= -500" in text
    assert "Kill switch" not in report.render(report.compute_report(sqlite3.connect(x.db_path)))
