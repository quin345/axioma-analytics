"""The bot's timeframe mirror (autotrade/timeframe.py) == research.pipeline.timeframe and webapp
export_flat.TIMEFRAMES; the 5-minute values are pinned to what the bot used before instances existed."""
import math

import pytest

from autotrade import strategy, timeframe
from autotrade._research import export_flat as ef
from autotrade._research import hypotheses as hyp
from autotrade._research import timeframe as rtf
from autotrade.config import Settings
from autotrade.timeframe import TF_1M, TF_5M


@pytest.mark.parametrize("key", ["5m", "1m"])
def test_mirror_equals_research_and_export(key):
    tf, r, e = timeframe.get(key), rtf.get(key), ef.TIMEFRAMES[key]
    assert tf.research is r and tf.export is e
    assert tf.slot_minutes == r.slot_minutes == e.slot_minutes
    assert tf.vap_minutes == r.poc_long and tf.vap_minutes in e.vap_windows
    assert tf.zwindow == r.zwindow and tf.min_periods == r.zwindow // 4 and tf.block_hours == r.block_hours
    keys = {h.key: h for h in hyp.hypotheses_for(r)}
    assert tf.poc_key in keys and tf.range_key in keys
    import pandas as pd
    f = pd.DataFrame({tf.poc_col: [0.3, -1.2], "range_pos_12": [0.1, -0.4]})
    assert list(keys[tf.poc_key].signal(f)) == [-0.3, 1.2]            # -poc_dist_<POC window>
    assert list(keys[tf.range_key].signal(f)) == [-0.1, 0.4]          # -range_pos_12
    assert tf.history_bars >= 2 * tf.zwindow


def test_five_minute_values_are_unchanged():
    t = TF_5M
    assert (t.slot_ms, t.slot_s, t.vap_minutes, t.zwindow, t.min_periods, t.bars_10s_per_slot) == \
        (300_000, 300, 240, 48, 12, 30)
    assert (t.poc_key, t.range_key, t.poc_col, t.history_bars, t.repair_slots) == \
        ("poc_reversion_4h", "range_reversal_1h", "poc_dist_240", 150, 48)
    assert t.history_minutes(150) / 60 == 16.5
    assert timeframe.of(Settings()) is TF_5M and timeframe.of(object()) is TF_5M


def test_one_minute_values():
    t = TF_1M
    assert (t.slot_ms, t.vap_minutes, t.zwindow, t.min_periods, t.bars_10s_per_slot, t.block_hours) == \
        (60_000, 60, 60, 15, 6, 1)
    assert (t.poc_key, t.range_key, t.poc_col) == ("poc_reversion_1h", "range_reversal_12m", "poc_dist_60")
    assert t.history_minutes(t.history_bars) == 180            # 120 bars + 60 min VAP = 3 h warm-up


def test_sigma_bar_and_exit_time_scale_with_the_slot():
    vols = [20.0] * 12
    s5, s1 = strategy.sigma_bar(vols), strategy.sigma_bar(vols, TF_1M)
    assert s5 == strategy.sigma_bar(vols, TF_5M)
    assert s1 == pytest.approx(s5 * math.sqrt(6 / 30), rel=1e-12)
    assert strategy.next_exit_at_ms(1_800_000_000_000, 2) == 1_800_000_302_000
    assert strategy.next_exit_at_ms(1_800_000_000_000, 2, TF_1M) == 1_800_000_062_000
