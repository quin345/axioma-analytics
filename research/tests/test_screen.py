"""Run from the repo root:  venv/bin/python -m unittest discover -s research/tests -t research"""
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pipeline import clean, features, screen  # noqa: E402
from pipeline.hypotheses import BY_KEY, HYPOTHESES, hypotheses_for  # noqa: E402
from pipeline.timeframe import TF_1M, TF_5M  # noqa: E402
import run_screen  # noqa: E402

GOLDEN = pathlib.Path(__file__).resolve().parent / "golden"


def vap_columns(tf):
    """The VAP columns of the table: 5m has 60/120/180/240 minute windows, 1m has 15/30/60."""
    windows = (60, 120, 180, 240) if tf.key == "5m" else (15, 30, 60)
    return [f"vap_{w}m_{k}" for w in windows for k in ("poc", "va_low", "va_high")]


def synthetic_bars(n=1800, phi=-0.35, seed=1, tf=TF_5M):
    """Two instruments on a `tf` grid whose returns mean-revert (AR(1), phi < 0)."""
    rng = np.random.default_rng(seed)
    t0 = pd.Timestamp("2026-11-05T00:00:00Z")
    rows = []
    for sym, name in ((1, "AAA"), (2, "BBB")):
        price, prev = 100.0, 0.0
        for i in range(n):
            r = phi * prev + rng.normal(0, 1e-3)
            prev = r
            price *= np.exp(r)
            end = t0 + pd.Timedelta(minutes=tf.slot_minutes * (i + 1))
            rows.append({
                "slot_start": (end - tf.slot).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "slot_end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "broker": "b", "ctid_trader_account": "acc", "symbol_id": sym,
                "symbol_name": name, "standardized_name": name, "asset_class": "FX",
                "has_data": 1, "events": int(rng.integers(50, 150)),
                "mid_open": price, "mid_high": price * 1.0005, "mid_low": price * 0.9995, "mid_close": price,
                "log_return": r, "spread_mean": price * 1e-6, "rel_spread_mean": 1e-6,
                "realised_vol_pct": abs(rng.normal(10, 2)), "acf_lag1": rng.normal(0, 0.2),
                "flow_imbalance": rng.normal(), "book_imbalance": 0.0,
                "tick_median_gap_ms": abs(rng.normal(500, 100)), "computed_at": "2026-11-20T00:00:00Z",
                **{c: price for c in vap_columns(tf)},
            })
    return pd.DataFrame(rows)


def write_db(path, df, table="metrics_5m"):
    conn = sqlite3.connect(path)
    df.to_sql(table, conn, index=False)
    conn.close()


class StatisticsTest(unittest.TestCase):
    def test_bh_qvalues_match_the_textbook_procedure(self):
        q = screen.bh_qvalues([0.01, 0.04, 0.03, 0.20, np.nan])
        # sorted p: .01 .03 .04 .20 (m=4) -> .04 .06 .0533 .20, then running min from the top
        np.testing.assert_allclose(q[:4].to_numpy(), [0.04, 0.0533333, 0.0533333, 0.20], rtol=1e-5)
        self.assertTrue(np.isnan(q.iloc[4]))

    def test_partial_ic_removes_a_shared_driver(self):
        """x only correlates with y through z: the raw IC is large, the partial IC near zero."""
        rng = np.random.default_rng(3)
        n = 2000
        z = rng.normal(size=n)
        g = pd.DataFrame({"slot_end": pd.date_range("2026-11-05", periods=n, freq="5min", tz="UTC"),
                          "z": z, "x": z + rng.normal(scale=0.5, size=n), "y": z + rng.normal(scale=0.5, size=n)})
        raw = np.mean(screen.block_ics(g, "x", "y"))
        partial = np.mean(screen.block_ics(g, "x", "y", "z"))
        self.assertGreater(raw, 0.5)
        self.assertLess(abs(partial), 0.1)

    def test_one_sided_p_follows_the_sign(self):
        self.assertLess(screen.summarise_ics([0.1, 0.12, 0.09, 0.11])["p"], 0.01)
        self.assertGreater(screen.summarise_ics([-0.1, -0.12, -0.09, -0.11])["p"], 0.99)


class FeatureTest(unittest.TestCase):
    def test_new_features_do_not_look_ahead(self):
        bars = clean.clean_metrics(synthetic_bars(n=400))
        base = features.build_features(bars)
        changed = bars.copy()
        future = changed["slot_end"] > changed["slot_end"].iloc[250]
        for c in ("mid_close", "mid_high", "mid_low", "events", "tick_median_gap_ms", "acf_lag1"):
            changed.loc[future, c] = changed.loc[future, c] * 1.5
        after = features.build_features(changed)
        past = base["slot_end"] <= base["slot_end"].iloc[250]
        for c in ("ret_3", "ret_12", "range_pos_12", "events_z", "tick_gap_z", "acf1", "cross_ret_1"):
            pd.testing.assert_series_equal(base.loc[past, c], after.loc[past, c], check_names=False)

    def test_cross_return_is_the_other_instrument(self):
        f = features.build_features(clean.clean_metrics(synthetic_bars(n=50)))
        a = f[f["symbol_id"] == 1].set_index("slot_end")
        b = f[f["symbol_id"] == 2].set_index("slot_end")
        pd.testing.assert_series_equal(a["cross_ret_1"].dropna(), b["ret_1"].reindex(a["cross_ret_1"].dropna().index),
                                       check_names=False)

    def test_forward_vol_is_the_next_bars_mean(self):
        f = features.build_features(clean.clean_metrics(synthetic_bars(n=60)))
        g = f[f["symbol_id"] == 1].reset_index(drop=True)
        self.assertAlmostEqual(g.loc[10, "fwd_vol_3"], g.loc[11:13, "realised_vol_pct"].mean())


class ScreenTest(unittest.TestCase):
    def test_split_has_an_embargo_and_one_cut(self):
        f = features.build_features(clean.clean_metrics(synthetic_bars(n=600)))
        disc, hold, cut = screen.split(f, 0.6)
        self.assertLessEqual(disc["slot_end"].max(), cut - 12 * screen.SLOT)
        f1 = features.build_features(clean.clean_metrics(synthetic_bars(n=600, tf=TF_1M), TF_1M), tf=TF_1M)
        disc, hold, cut = screen.split(f1, 0.6, tf=TF_1M)
        self.assertLessEqual(disc["slot_end"].max(), cut - 12 * pd.Timedelta(minutes=1))
        self.assertGreater(disc["slot_end"].max(), cut - pd.Timedelta(minutes=13))
        self.assertGreater(hold["slot_end"].min(), cut)

    def test_screen_finds_and_confirms_a_planted_reversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.db")
            write_db(db, synthetic_bars())
            result = run_screen.run(db, tmp, log=lambda *a: None)
            picks = {p["hypothesis"]: p for p in result["picks"]}
            self.assertIn("reversal_5m", picks)
            self.assertEqual(picks["reversal_5m"]["verdict"], "confirmed")
            self.assertTrue(picks["reversal_5m"]["passed_discovery"])
            ranking = pd.read_csv(os.path.join(tmp, "ranking.csv"))
            mom = ranking[ranking["hypothesis"] == "momentum_15m"].iloc[0]
            self.assertFalse(mom["passed_discovery"])  # the opposite claim must not pass
            self.assertTrue((pathlib.Path(tmp) / "report.md").read_text().startswith("# Hypothesis screen"))
            json.loads((pathlib.Path(tmp) / "top_hypotheses.json").read_text())

    def test_a_duplicate_signal_is_skipped_for_the_next_distinct_one(self):
        keys = ["activity_predicts_vol", "tick_speed_predicts_vol", "poc_reversion_4h", "range_reversal_1h"]
        ts = [9.0, 8.0, 7.0, 6.0]
        table = pd.DataFrame([
            {"hypothesis": k, "target": BY_KEY[k].target, "horizon_bars": 1, "instrument": inst,
             "blocks": 10, "ic": 0.2, "t": t, "p": 1e-6}
            for k, t in zip(keys, ts) for inst in ("A", "ALL")])
        rng = np.random.default_rng(5)
        a, b, c = rng.normal(size=(3, 500))
        disc = pd.DataFrame({"hyp_activity_predicts_vol": a, "hyp_tick_speed_predicts_vol": a + 0.05 * rng.normal(size=500),
                             "hyp_poc_reversion_4h": b, "hyp_range_reversal_1h": c})
        ranked, picks = screen.select(table, top=3, discovery=disc)
        self.assertEqual(list(picks["hypothesis"]), ["activity_predicts_vol", "poc_reversion_4h", "range_reversal_1h"])
        dup = ranked.set_index("hypothesis").loc["tick_speed_predicts_vol", "redundant_with"]
        self.assertTrue(dup.startswith("activity_predicts_vol"))

    def test_baselines_are_never_picked(self):
        table = pd.DataFrame([
            {"hypothesis": h.key, "target": h.target, "horizon_bars": 1, "instrument": inst,
             "blocks": 10, "ic": 0.5 if h.baseline else 0.01, "t": 9.0 if h.baseline else 0.1,
             "p": 1e-9 if h.baseline else 0.4}
            for h in HYPOTHESES for inst in ("A", "ALL")])
        ranked, picks = screen.select(table, top=3)
        self.assertNotIn("vol_clustering", set(ranked["hypothesis"]))
        self.assertTrue(BY_KEY["vol_clustering"].baseline)


class FiveMinuteRegressionTest(unittest.TestCase):
    """The 5-minute screen must reproduce the outputs it produced before timeframes existed."""

    def test_features_unchanged(self):
        golden = json.loads((GOLDEN / "features_5m_sums.json").read_text())
        f = features.build_features(clean.clean_metrics(synthetic_bars(n=400)))
        self.assertEqual(len(f), golden.pop("_rows"))
        for c, total in golden.items():
            self.assertAlmostEqual(float(np.nansum(f[c].to_numpy())), total, delta=1e-9 * max(1.0, abs(total)), msg=c)

    def test_screen_outputs_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.db")
            write_db(db, synthetic_bars())
            run_screen.run(db, tmp, log=lambda *a: None)
            tmp = pathlib.Path(tmp)
            pd.testing.assert_frame_equal(pd.read_csv(tmp / "ranking.csv"),
                                          pd.read_csv(GOLDEN / "screen_5m_ranking.csv"), rtol=1e-9)
            got = json.loads((tmp / "top_hypotheses.json").read_text())
            want = json.loads((GOLDEN / "screen_5m_top_hypotheses.json").read_text())
            self.assertEqual(got.pop("timeframe")["key"], "5m")   # the only addition: metadata
            self.assertEqual(got, want)
            self.assertEqual((tmp / "report.md").read_text(), (GOLDEN / "screen_5m_report.md").read_text())

    def test_5m_hypothesis_names_are_frozen(self):
        self.assertEqual([h.key for h in HYPOTHESES], [
            "reversal_5m", "momentum_15m", "momentum_1h", "tick_flow", "range_reversal_1h", "poc_reversion_1h",
            "poc_reversion_4h", "wide_spread_reversal", "micro_trend_continuation", "cross_asset_lead",
            "vol_clustering", "spread_predicts_vol", "activity_predicts_vol", "tick_speed_predicts_vol"])


class OneMinuteTest(unittest.TestCase):
    def test_hypothesis_names_follow_the_timeframe(self):
        hs = hypotheses_for(TF_1M)
        self.assertEqual([h.key for h in hs][:7], [
            "reversal_1m", "momentum_3m", "momentum_12m", "tick_flow", "range_reversal_12m",
            "poc_reversion_15m", "poc_reversion_1h"])
        self.assertEqual([h.key for h in hs][7:], [h.key for h in HYPOTHESES][7:])
        self.assertEqual(len(hs), 14)
        self.assertEqual(sum(h.baseline for h in hs), 1)
        by = {h.key: h for h in hs}
        self.assertIn("1-minute move", by["reversal_1m"].statement)
        self.assertIn("15-minute point of control", by["poc_reversion_15m"].statement)
        self.assertIn("1-hour point of control", by["poc_reversion_1h"].statement)
        self.assertIn("12-minute range", by["range_reversal_12m"].statement)

    def test_feature_windows_are_rescaled(self):
        bars = synthetic_bars(n=200, tf=TF_1M)
        bars["vap_15m_poc"], bars["vap_15m_va_low"], bars["vap_15m_va_high"] = (
            bars["mid_close"] * 0.999, bars["mid_close"] * 0.99, bars["mid_close"] * 1.01)
        f = features.build_features(clean.clean_metrics(bars, TF_1M), tf=TF_1M)
        for c in ("poc_dist_15", "poc_dist_30", "poc_dist_60"):
            self.assertIn(c, f)
        self.assertNotIn("poc_dist_240", f)
        g = f[f["symbol_id"] == 1].reset_index(drop=True)
        self.assertTrue((g["slot_end"].diff().dropna() == pd.Timedelta(minutes=1)).all())
        # z-window: 60 bars, min_periods 15 -> first value at index 14, computed from 15 observations
        self.assertTrue(g["flow_imbalance_z"].iloc[:14].isna().all())
        self.assertTrue(np.isfinite(g.loc[14, "flow_imbalance_z"]))
        x = g["flow_imbalance"]
        i = 100
        w = x.iloc[i - 59:i + 1]
        self.assertAlmostEqual(g.loc[i, "flow_imbalance_z"], (x[i] - w.mean()) / w.std())
        # bar counts stay in bars: ret_3 is 3 minutes, the forward horizons are 1/3/6/12 bars
        lp = np.log(g["mid_close"])
        self.assertAlmostEqual(g.loc[i, "ret_3"], lp[i] - lp[i - 3])
        self.assertAlmostEqual(g.loc[i, "fwd_ret_12"], lp[i + 12] - lp[i])
        self.assertAlmostEqual(g.loc[i, "poc_dist_15"],
                               (g.loc[i, "mid_close"] - g.loc[i, "vap_15m_poc"])
                               / (g.loc[i, "vap_15m_va_high"] - g.loc[i, "vap_15m_va_low"]))

    def test_1m_does_not_look_ahead(self):
        bars = clean.clean_metrics(synthetic_bars(n=300, tf=TF_1M), TF_1M)
        base = features.build_features(bars, tf=TF_1M)
        changed = bars.copy()
        future = changed["slot_end"] > changed["slot_end"].iloc[200]
        for c in ("mid_close", "mid_high", "mid_low", "events"):
            changed.loc[future, c] = changed.loc[future, c] * 1.5
        after = features.build_features(changed, tf=TF_1M)
        past = base["slot_end"] <= base["slot_end"].iloc[200]
        for c in ("ret_3", "range_pos_12", "events_z", "rel_spread_z", "cross_ret_1"):
            pd.testing.assert_series_equal(base.loc[past, c], after.loc[past, c], check_names=False)

    def test_screen_runs_end_to_end_on_1m_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.db")
            write_db(db, synthetic_bars(n=6000, tf=TF_1M), "metrics_1m")
            out = os.path.join(tmp, "o")
            result = run_screen.run(db, out, log=lambda *a: None, tf=TF_1M)
            self.assertEqual(result["timeframe"]["key"], "1m")
            self.assertEqual(result["timeframe"]["poc_windows"], [15, 30, 60])
            self.assertEqual(result["split"]["block_hours"], 1)
            self.assertEqual(result["split"]["embargo_bars"], 12)
            picks = {p["hypothesis"]: p for p in result["picks"]}
            self.assertIn("reversal_1m", picks)
            self.assertEqual(picks["reversal_1m"]["horizon_minutes"], picks["reversal_1m"]["horizon_bars"])
            self.assertEqual(picks["reversal_1m"]["verdict"], "confirmed")
            ranking = pd.read_csv(os.path.join(out, "ranking.csv"))
            self.assertIn("momentum_3m", set(ranking["hypothesis"]))
            self.assertNotIn("reversal_5m", set(ranking["hypothesis"]))
            text = (pathlib.Path(out) / "report.md").read_text()
            self.assertTrue(text.startswith("# Hypothesis screen (1m bars)"))
            self.assertNotIn(" 5 min", text)

    def test_cli_defaults_pick_the_timeframe_output_dir(self):
        self.assertEqual(run_screen.default_out(TF_1M).name, "screen_1m")
        self.assertEqual(run_screen.default_out(TF_5M).name, "screen")

    def test_backtest_annualisation_comes_from_the_timeframe(self):
        self.assertEqual(TF_5M.bars_per_year, 252 * 24 * 12)
        self.assertEqual(TF_1M.bars_per_year, 252 * 24 * 60)


if __name__ == "__main__":
    unittest.main()
