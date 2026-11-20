"""Timeframe presets: the bar size of a research table and every window derived from it.

Two presets share the same code path:

* `5m`: metrics_5m, 5-minute bars, 4-hour aggregation windows (the original screen);
* `1m`: metrics_1m, 1-minute bars, 1-hour aggregation windows.

Per-bar counts (horizons, short returns, the 12-bar range, the embargo) stay the
same *in bars*. Every window that was 4 hours becomes 1 hour on the 1-minute
preset: the z-score window, the IC block, the POC window and the economics
signal window. The POC windows keep the 1/4, 1/2, 1 ratio of the longest one.
"""
from dataclasses import dataclass
from typing import Tuple

import pandas as pd


@dataclass(frozen=True)
class Timeframe:
    key: str                          # "5m" / "1m"
    slot_minutes: int                 # bar size
    table: str                        # source table in axioma.db
    poc_windows: Tuple[int, ...]      # VAP windows (minutes) turned into poc_dist_{w}; short -> long
    zwindow: int                      # rolling z-score window, bars (min_periods = zwindow // 4)
    block_hours: int                  # IC block length, hours
    horizons: Tuple[int, ...] = (1, 3, 6, 12)   # forward horizons, bars

    @property
    def slot(self):
        return pd.Timedelta(minutes=self.slot_minutes)

    @property
    def block(self):
        return pd.Timedelta(hours=self.block_hours)

    @property
    def embargo_bars(self):
        return max(self.horizons)

    @property
    def bars_per_year(self):
        return 252 * 24 * (60 // self.slot_minutes)   # 24h markets

    @property
    def poc_short(self):
        return self.poc_windows[0]

    @property
    def poc_long(self):
        return self.poc_windows[-1]

    @property
    def vap_columns(self):
        return [f"vap_{w}m_{k}" for w in self.poc_windows for k in ("poc", "va_low", "va_high")]

    def minutes(self, bars):
        return self.slot_minutes * int(bars)

    @property
    def slot_label(self):
        return f"{self.slot_minutes}-minute"

    @property
    def window_label(self):
        return span(self.block_hours * 60)

    def to_dict(self):
        return {"key": self.key, "slot_minutes": self.slot_minutes, "table": self.table,
                "poc_windows": list(self.poc_windows), "zwindow_bars": self.zwindow,
                "block_hours": self.block_hours, "horizons_bars": list(self.horizons)}


def tag(minutes):
    """Short name suffix: 5 -> '5m', 60 -> '1h', 240 -> '4h'."""
    return f"{minutes // 60}h" if minutes % 60 == 0 else f"{minutes}m"


def span(minutes):
    """'5-minute', '1-hour', '4-hour'."""
    return f"{minutes // 60}-hour" if minutes % 60 == 0 else f"{minutes}-minute"


TF_5M = Timeframe("5m", 5, "metrics_5m", (60, 120, 240), zwindow=48, block_hours=4)
TF_1M = Timeframe("1m", 1, "metrics_1m", (15, 30, 60), zwindow=60, block_hours=1)
TIMEFRAMES = {t.key: t for t in (TF_5M, TF_1M)}


def get(key):
    try:
        return TIMEFRAMES[key]
    except KeyError:
        raise ValueError(f"unknown timeframe {key!r}; choose from {sorted(TIMEFRAMES)}") from None
