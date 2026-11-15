"""Fill-logic tests for pipeline.strategies on hand-built bars."""
import pathlib
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pipeline import strategies as S  # noqa: E402


def bars(closes, highs=None, lows=None, spread=0.0, poc=100.0, va=(99.0, 101.0), sig=None):
    n = len(closes)
    t = pd.date_range("2026-11-05T00:05:00Z", periods=n, freq="5min")
    df = pd.DataFrame({
        "slot_end": t, "ctid_trader_account": "a", "symbol_id": 1, "standardized_name": "X",
        "has_data": True, "mid_close": closes, "mid_high": highs or closes, "mid_low": lows or closes,
        "spread_mean": spread, "vap_60m_poc": poc, "vap_60m_va_low": va[0], "vap_60m_va_high": va[1],
    })
    if sig is not None:
        df["sig"] = sig
    return df


class PocTest(unittest.TestCase):
    P = S.PocParams(window=60, entry_k=0.5, stop_k=0.5, hold=6, mode="maker")  # width 2 -> buy 99, sell 101

    def test_maker_buy_fills_next_bar_and_hits_poc(self):
        df = bars([100, 99.5, 100.2, 100.2], highs=[100, 100, 100.5, 100.2], lows=[100, 98.9, 99.8, 100.2])
        t = S.simulate_poc(df, self.P)
        self.assertEqual(len(t), 1)
        r = t.iloc[0]
        self.assertEqual((r.side, r.entry, r.reason), (1, 99.0, "target"))
        self.assertEqual(r.entry_time, df.slot_end[1])  # decided at bar 0, filled in bar 1
        self.assertAlmostEqual(r.pnl_bps, (100 - 99) / 99 * 1e4)

    def test_spread_blocks_marginal_fill(self):
        # low touches 99.0 exactly, but the ask (low + half spread) does not reach the limit
        df = bars([100, 99.5, 99.5], lows=[100, 99.0, 99.5], spread=0.2)
        self.assertTrue(S.simulate_poc(df, self.P).empty)

    def test_stop_assumed_before_target_in_same_bar(self):
        # filled at 99, next bar spans both the stop (98) and the target (100)
        df = bars([100, 99.5, 99.0, 99], highs=[100, 100, 100.5, 99], lows=[100, 98.9, 97.5, 99])
        r = S.simulate_poc(df, self.P).iloc[0]
        self.assertEqual(r.reason, "stop")
        self.assertLess(r.pnl_bps, 0)

    def test_both_quotes_touched_skips_bar(self):
        df = bars([100, 100, 100], highs=[100, 101.5, 100], lows=[100, 98.5, 100])
        self.assertTrue(S.simulate_poc(df, self.P).empty)

    def test_taker_pays_spread(self):
        p = S.PocParams(window=60, entry_k=0.5, stop_k=0, hold=1, mode="taker")
        df = bars([98.5, 98.5, 98.5], spread=0.2)
        r = S.simulate_poc(df, p).iloc[0]
        self.assertEqual(r.reason, "timeout")
        self.assertAlmostEqual(r.entry, 98.6)
        self.assertAlmostEqual(r.exit, 98.4)


class SignalTest(unittest.TestCase):
    def test_signal_trade_uses_future_close_and_costs(self):
        df = bars([100, 101, 102, 103], spread=0.2, sig=[2.0, 0, 0, 0])
        t = S.simulate_signal(df, "sig", threshold=1.0, hold=2)
        r = t.iloc[0]
        self.assertEqual((r.side, r.entry, r.exit), (1, 100.1, 101.9))
        gross = S.simulate_signal(df, "sig", 1.0, 2, cost=False).iloc[0]
        self.assertGreater(gross.pnl_bps, r.pnl_bps)

    def test_summarize_empty(self):
        self.assertEqual(S.summarize(pd.DataFrame())["trades"], 0)
        s = S.summarize(pd.DataFrame({"pnl_bps": [1.0, -1.0, 3.0], "reason": "timeout",
                                      "entry_time": pd.Timestamp("2026-11-05", tz="UTC"),
                                      "exit_time": pd.Timestamp("2026-11-06", tz="UTC")}))
        self.assertAlmostEqual(s["avg_bps"], 1.0)
        self.assertTrue(np.isclose(s["win_rate"], 2 / 3))


if __name__ == "__main__":
    unittest.main()
