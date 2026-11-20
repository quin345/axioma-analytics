"""Run from the repo root:  venv/bin/python -m unittest discover -s research/tests -t research"""
import csv
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

STUDIES = pathlib.Path(__file__).resolve().parents[1] / "studies"
sys.path.insert(0, str(STUDIES))

import build_report  # noqa: E402

PICKS = [
    {"hypothesis": "alpha_vol", "target": "vol", "horizon_bars": 1, "horizon_minutes": 5, "ic": 0.2, "t": 10.0,
     "p": 1e-20, "q": 1e-19, "blocks": 60, "holdout_ic": 0.21, "holdout_t": 8.0, "holdout_p": 1e-15,
     "holdout_blocks": 40, "holdout_ic_by_instrument": {"AAA": 0.15, "BBB": 0.25}, "verdict": "confirmed",
     "statement": "Activity predicts volatility.", "mechanism": "Info arrives.", "control": "vol",
     "vol_ratio_top_vs_bottom": 1.3, "ic_ctrl_vol_and_hour": 0.25, "t_ctrl_vol_and_hour": 7.5},
    {"hypothesis": "beta_ret", "target": "ret", "horizon_bars": 1, "horizon_minutes": 5, "ic": 0.15, "t": 8.0,
     "p": 1e-10, "q": 1e-9, "blocks": 60, "holdout_ic": 0.1, "holdout_t": 2.0, "holdout_p": 0.03,
     "holdout_blocks": 40, "holdout_ic_by_instrument": {"AAA": 0.1, "BBB": 0.1},
     "verdict": "right sign, not significant", "statement": "Price reverts.", "mechanism": "Anchor.",
     "control": None, "trades": 700, "gross_avg_bps": 0.4, "net_avg_bps": -0.3, "cost_per_trade_bps": 0.7,
     "ic_ctrl_last_return": 0.1, "t_ctrl_last_return": 6.0, "ic_skip_one_bar": 0.12, "t_skip_one_bar": 5.0},
    {"hypothesis": "gamma_ret", "target": "ret", "horizon_bars": 3, "horizon_minutes": 15, "ic": 0.1, "t": 5.0,
     "p": 1e-6, "q": 1e-5, "blocks": 60, "holdout_ic": -0.01, "holdout_t": -0.5, "holdout_p": 0.7,
     "holdout_blocks": 40, "holdout_ic_by_instrument": {"AAA": -0.01, "BBB": 0.0}, "verdict": "failed",
     "statement": "Range reverses.", "mechanism": "Liquidity.", "control": None, "trades": 10,
     "gross_avg_bps": 1.5, "net_avg_bps": 0.8, "cost_per_trade_bps": 0.7,
     "ic_ctrl_last_return": 0.1, "t_ctrl_last_return": 3.0, "ic_skip_one_bar": 0.1, "t_skip_one_bar": 3.0},
]
COLS = ["hypothesis", "target", "horizon_bars", "instrument", "blocks", "ic", "t", "p", "q", "sign_consistent",
        "passed_discovery", "instruments_voting", "rank", "redundant_with", "picked"]


TF_1M_META = {"key": "1m", "slot_minutes": 1, "table": "metrics_1m", "poc_windows": [15, 30, 60],
              "zwindow_bars": 60, "block_hours": 1, "horizons_bars": [1, 3, 6, 12], "hypotheses": 14, "baselines": 1}


def write_screen(d, tf=None):
    """A screen output folder. tf=None writes the legacy 5-minute layout (no `timeframe` key)."""
    d.mkdir(parents=True)
    picks = PICKS
    if tf:
        picks = [{**p, "horizon_minutes": p["horizon_bars"]} for p in PICKS]
    top = {"split": {"cut": "2026-01-02 10:00:00+00:00", "fraction": 0.6, "embargo_bars": 12,
                     "block_hours": tf["block_hours"] if tf else 4,
                     "fdr": 0.1, "top": 3},
           "coverage": [{"instrument": "AAA", "bars_with_data": 100, "first": "2026-01-01 00:00:00+00:00",
                         "last": "2026-01-05 00:00:00+00:00", "days_with_data": 4}],
           "picks": picks}
    if tf:
        top["timeframe"] = tf
    (d / "top_hypotheses.json").write_text(json.dumps(top))
    with (d / "ranking.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, COLS)
        w.writeheader()
        for i, p in enumerate(PICKS, 1):
            w.writerow({"hypothesis": p["hypothesis"], "target": p["target"], "horizon_bars": p["horizon_bars"],
                        "instrument": "ALL", "blocks": 60, "ic": p["ic"], "t": p["t"], "p": p["p"], "q": p["q"],
                        "sign_consistent": True, "passed_discovery": True, "instruments_voting": 2, "rank": i,
                        "redundant_with": "", "picked": True})
    for n in ("discovery_scores.csv", "report.md"):
        (d / n).write_text("x")


def build(tmp, **kw):
    return build_report.build(out=tmp / "studies", site=tmp / "site", screen_dir=tmp / "screen",
                              screen_1m_dir=tmp / "screen_1m", **kw)


class ReportBuildTest(unittest.TestCase):
    def test_5m_item_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            write_screen(tmp / "screen")
            build(tmp)
            page = (tmp / "site" / "screen-5m" / "index.html").read_text()
            for name in ("alpha_vol", "beta_ret", "gamma_ret", "confirmed", "right sign, not significant",
                         "failed", "Full ranking", 'href="data/screen/top_hypotheses.json"',
                         'href="data/screen/ranking.csv"', "Covers the spread in this test.",
                         "Predicts direction, but the edge is smaller than the spread",
                         'href="../report.css"', "Top 3 hypotheses", "Fourteen pre-registered ideas",
                         "5-minute bars", "4-hour windows", "AAA", "All research"):
                self.assertIn(name, page)
            self.assertNotIn("What changed vs the 5-minute screen", page)
            self.assertNotIn("http://", page)
            self.assertTrue((tmp / "site" / "screen-5m" / "data" / "screen" / "ranking.csv").exists())

    def test_1m_item_page_is_derived_from_the_timeframe(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            write_screen(tmp / "screen_1m", TF_1M_META)
            build(tmp)
            page = (tmp / "site" / "screen-1m" / "index.html").read_text()
            for name in ("What changed vs the 5-minute screen", "1-minute bars", "metrics_1m", "15 min / 30 min / 60 min",
                         "60 bars = 1 h", "48 bars = 4 h", "Not independent of the 5-minute screen",
                         "1-hour windows", "--timeframe 1m", "1-minute taker trade", "12 bars (12 min)", "Top 3 hypotheses"):
                self.assertIn(name, page)
            self.assertNotIn("5-minute taker trade", page)
            self.assertNotIn("0.61", page)                 # first-run 5-minute caveats do not carry over
            self.assertNotIn("rho 0.95", page)
            self.assertFalse((tmp / "site" / "screen-5m").exists())

    def test_index_lists_both_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            write_screen(tmp / "screen")
            write_screen(tmp / "screen_1m", TF_1M_META)
            path = build(tmp)
            self.assertEqual(path, tmp / "site" / "index.html")
            index = path.read_text()
            for name in ('href="screen-5m/index.html"', 'href="screen-1m/index.html"',
                         "Hypothesis screen, 5-minute bars", "Hypothesis screen, 1-minute bars",
                         "14 pre-registered hypotheses on 1-minute bars with 1-hour aggregation windows",
                         "14 pre-registered hypotheses on 5-minute bars with 4-hour aggregation windows",
                         "1 of 3 top picks confirmed on holdout", "2026-01-01 to 2026-01-05", 'href="report.css"'):
                self.assertIn(name, index)
            self.assertTrue((tmp / "site" / "screen-5m" / "index.html").exists())
            self.assertTrue((tmp / "site" / "screen-1m" / "index.html").exists())
            self.assertTrue((tmp / "site" / "report.css").exists())

    def test_building_one_item_keeps_the_other_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            write_screen(tmp / "screen")
            write_screen(tmp / "screen_1m", TF_1M_META)
            build(tmp)
            keep = tmp / "site" / "screen-5m"
            (keep / "marker.txt").write_text("mine")
            before = (keep / "index.html").read_text()
            shutil.rmtree(tmp / "screen")                 # 5m outputs go missing; 1m is rebuilt
            build(tmp)
            self.assertEqual((keep / "marker.txt").read_text(), "mine")
            self.assertEqual((keep / "index.html").read_text(), before)
            self.assertIn('href="screen-5m/index.html"', (tmp / "site" / "index.html").read_text())
            self.assertIn("1-minute", (tmp / "site" / "screen-1m" / "index.html").read_text())

    def test_old_root_layout_is_replaced_by_the_5m_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            (tmp / "site" / "charts").mkdir(parents=True)
            (tmp / "site" / "charts" / "old.png").write_text("x")
            (tmp / "site" / "index.html").write_text("old single page")
            write_screen(tmp / "screen")
            build(tmp)
            self.assertFalse((tmp / "site" / "charts").exists())
            self.assertIn("screen-5m/index.html", (tmp / "site" / "index.html").read_text())

    def test_nothing_to_build_leaves_site_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            (tmp / "site").mkdir()
            (tmp / "site" / "keep.txt").write_text("old")
            with self.assertRaises(FileNotFoundError):
                build(tmp)
            self.assertEqual((tmp / "site" / "keep.txt").read_text(), "old")


if __name__ == "__main__":
    unittest.main()
