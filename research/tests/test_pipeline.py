"""Run from the repo root:  venv/bin/python -m unittest discover -s research/tests -t research"""
import os
import pathlib
import sqlite3
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pipeline import backtest, clean, data, evaluate, features, signals  # noqa: E402
import run_pipeline  # noqa: E402

COLS = ["slot_start", "slot_end", "broker", "ctid_trader_account", "symbol_id", "symbol_name",
        "standardized_name", "asset_class", "has_data", "mid_close", "log_return", "spread_mean",
        "rel_spread_mean", "realised_vol_pct", "flow_imbalance", "book_imbalance", "computed_at"]
VAP = [f"vap_{w}m_{k}" for w in (60, 120, 180, 240) for k in ("poc", "va_low", "va_high")]


def make_db(path, n=200, gap_at=50):
    rng = np.random.default_rng(0)
    rows = []
    t0 = pd.Timestamp("2026-11-05T00:00:00Z")
    for sym, name in ((1, "EURUSD"), (2, "XAUUSD")):
        price = 100.0
        for i in range(n):
            if i == gap_at:
                continue  # missing slot
            price *= np.exp(rng.normal(0, 1e-3))
            end = t0 + pd.Timedelta(minutes=5 * (i + 1))
            r = [(end - pd.Timedelta(minutes=5)).isoformat(), end.strftime("%Y-%m-%dT%H:%M:%SZ"), "b", "acc", sym,
                 name, name, "FX", int(i % 17 != 0), price, 0.0, 0.01, 1e-4, 0.1,
                 rng.normal(), rng.normal(), "2026-11-15T00:00:00Z"]
            rows.append(r + [price, price * 0.99, price * 1.01] * 4)
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE metrics_5m ({', '.join(COLS + VAP)})")
    conn.executemany(f"INSERT INTO metrics_5m VALUES ({','.join('?' * (len(COLS) + len(VAP)))})", rows)
    conn.commit()
    conn.close()


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.db")
        make_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_loader_is_read_only(self):
        with self.assertRaises(sqlite3.OperationalError):
            data.connect(self.db).execute("DELETE FROM metrics_5m")

    def test_symbol_filter(self):
        df = data.load_metrics_5m(self.db, symbols=["XAUUSD"])
        self.assertEqual(set(df["symbol_id"]), {2})

    def test_clean_regular_grid(self):
        bars = clean.clean_metrics(data.load_metrics_5m(self.db))
        for _, g in bars.groupby(["ctid_trader_account", "symbol_id"]):
            self.assertTrue((g["slot_end"].diff().dropna() == pd.Timedelta(minutes=5)).all())
            self.assertEqual(len(g), 200)
        self.assertTrue(bars.loc[~bars["has_data"], "mid_close"].isna().all())

    def test_forward_return_no_lookahead(self):
        f = features.build_features(clean.clean_metrics(data.load_metrics_5m(self.db)))
        g = f[f["symbol_id"] == 1].reset_index(drop=True)
        i = 10
        expected = np.log(g.loc[i + 3, "mid_close"]) - np.log(g.loc[i, "mid_close"])
        if not np.isnan(expected):
            self.assertAlmostEqual(g.loc[i, "fwd_ret_3"], expected)
        # changing future prices must not change features at i
        before = g.loc[i, ["ret_1", "flow_imbalance_z", "poc_dist_60"]]
        bars = clean.clean_metrics(data.load_metrics_5m(self.db))
        bars.loc[bars["slot_end"] > g.loc[i, "slot_end"], ["mid_close", "flow_imbalance"]] *= 3
        after = features.build_features(bars)
        after = after[after["symbol_id"] == 1].reset_index(drop=True).loc[i, before.index]
        pd.testing.assert_series_equal(before, after, check_names=False)

    def test_perfect_signal_ic(self):
        f = features.build_features(clean.clean_metrics(data.load_metrics_5m(self.db)))
        f["sig_oracle"] = f["fwd_ret_1"]
        ic = evaluate.ic_table(f, ["sig_oracle"], ["fwd_ret_1"])
        self.assertAlmostEqual(ic.loc[ic["instrument"] == "ALL", "ic"].iloc[0], 1.0)

    def test_backtest_costs_reduce_pnl(self):
        f = signals.add_signals(features.build_features(clean.clean_metrics(data.load_metrics_5m(self.db))))
        gross, _ = backtest.backtest(f, "sig_short_reversal", cost=False)
        net, _ = backtest.backtest(f, "sig_short_reversal", cost=True)
        self.assertTrue((net["total_log_ret"] < gross["total_log_ret"]).all())

    def test_run_writes_outputs(self):
        out = pathlib.Path(self.tmp.name) / "out"
        run_pipeline.run(self.db, out, log=lambda *_: None)
        for name in ("features.parquet", "ic.csv", "quantile_returns.csv", "backtest_summary.csv"):
            self.assertTrue((out / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
