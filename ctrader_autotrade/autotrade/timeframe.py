"""Bar timeframe of a bot instance: every slot-, window- and bar-count the bot uses, in one object.

The bot mirrors research.pipeline.timeframe (TF_5M / TF_1M) and webapp export_flat.TIMEFRAMES so
live == research by construction; tests/test_timeframe.py pins the equality. Per-bar counts that the
research keeps fixed in bars (range_pos_12, the 12-bar vol median of the stop) stay fixed in bars.

    5m: 5-minute slots, POC window 240 min, z-window 48 bars (min_periods 12), IC block 4 h
    1m: 1-minute slots, POC window  60 min, z-window 60 bars (min_periods 15), IC block 1 h

The strategy signal is `poc_reversion_<poc window>` (= -poc_dist_<poc window>), journalled range
signal `range_reversal_<12 bars>`; names come from research hypotheses_for(tf).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ._research import export_flat as _ef
from ._research import timeframe as _rtf

BUCKET_S = _ef.BIN_SECONDS        # 10 s buckets


@dataclass(frozen=True)
class BotTimeframe:
    key: str                 # "5m" | "1m" (research / export_flat key)
    history_bars: int        # bars rebuilt at start-up and held by the FeatureEngine (>= 2 z-windows)

    # ---- mirrored from research (single source of truth) ----
    @property
    def research(self):
        return _rtf.get(self.key)

    @property
    def export(self):
        """webapp export_flat.Timeframe: slot width and VAP lookbacks of metrics_<key>."""
        return _ef.TIMEFRAMES[self.key]

    @property
    def slot_minutes(self) -> int:
        return self.research.slot_minutes

    @property
    def vap_minutes(self) -> int:
        """The VAP window behind z_poc: research poc_long (240 at 5m, 60 at 1m)."""
        return self.research.poc_long

    @property
    def zwindow(self) -> int:
        return self.research.zwindow

    @property
    def min_periods(self) -> int:
        """rolling-z min_periods (research _rolling_z: window // 4); also the bars needed after an open."""
        return self.zwindow // 4

    @property
    def block_hours(self) -> int:
        return self.research.block_hours

    # ---- derived ----
    @property
    def slot_ms(self) -> int:
        return self.slot_minutes * 60_000

    @property
    def slot_s(self) -> int:
        return self.slot_minutes * 60

    @property
    def vap_ms(self) -> int:
        return self.vap_minutes * 60_000

    @property
    def bars_10s_per_slot(self) -> int:
        return self.slot_s // BUCKET_S

    @property
    def poc_col(self) -> str:
        return f"poc_dist_{self.vap_minutes}"

    @property
    def poc_key(self) -> str:
        """Hypothesis key of the traded signal: poc_reversion_4h (5m) / poc_reversion_1h (1m)."""
        return f"poc_reversion_{_rtf.tag(self.vap_minutes)}"

    @property
    def range_key(self) -> str:
        """Journalled confirmation signal: range_reversal_1h (5m) / range_reversal_12m (1m)."""
        return f"range_reversal_{_rtf.tag(self.research.minutes(12))}"

    @property
    def repair_slots(self) -> int:
        """kql: bars closed on stale/failed data are rebuilt this far back (one z-window)."""
        return self.zwindow

    @property
    def label(self) -> str:
        return f"{self.slot_minutes}-minute"

    def history_minutes(self, history_slots: int) -> int:
        """Depth history so each of `history_slots` bars has a full VAP window behind it."""
        return history_slots * self.slot_minutes + self.vap_minutes

    def bars_per_hour(self) -> float:
        return 60 / self.slot_minutes

    def sqrt_bars_10s(self) -> float:
        return math.sqrt(self.bars_10s_per_slot)


TF_5M = BotTimeframe("5m", history_bars=150)
TF_1M = BotTimeframe("1m", history_bars=120)
TIMEFRAMES = {t.key: t for t in (TF_5M, TF_1M)}


def get(key: str) -> BotTimeframe:
    try:
        return TIMEFRAMES[key]
    except KeyError:
        raise ValueError(f"unknown timeframe {key!r}; choose from {sorted(TIMEFRAMES)}") from None


def of(settings) -> BotTimeframe:
    """The timeframe of a Settings object (5m when it has none, e.g. a test namespace)."""
    return get(getattr(settings, "instance_timeframe", None) or "5m")
