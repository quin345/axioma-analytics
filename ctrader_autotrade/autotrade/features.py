"""FeatureEngine: runs the research feature code on a rolling window of bars.

Each appended Bar becomes a metrics_5m-shaped row; the last ~150 bars per symbol go through the
research functions themselves: research/pipeline/clean.py:clean_metrics (5-minute grid with NaN
rows for gaps / weekends / gold's break), features.py:build_features (poc_dist_240, range_pos_12
with min_periods 6, events_z; _rolling_z window 48, min_periods 12), hypotheses.py signals
(sig poc = -poc_dist_240 = hyp poc_reversion_4h, sig range = -range_pos_12 = hyp
range_reversal_1h) and screen.py:218-222 (z = _rolling_z of the signal per instrument).
The 60/120 min VAP columns build_features loops over are supplied as NaN (only 240 is used by
the signals; NaN does not propagate to any feature the bot uses).

events_z uses live bars only: a bar counted non-live (`live=False`, default
`approx_share >= 1`) contributes NaN events. `events_z_live_only` is True when every bar in the
events_z window (the last 48 slots) is live (missing slots are not approximations).
"""
from __future__ import annotations

import math
from collections import deque
from datetime import timedelta
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from ._research import features as _feat
from ._research import hypotheses as _hyp
from .types import Bar, FeatureRow

HISTORY_BARS = 150
SPREAD_MEDIAN_BARS = 48
PRICE_INT = 100000
_ACCOUNT = "bot"
_SLOT = pd.Timedelta(minutes=5)
_KEYS = ("poc_reversion_4h", "range_reversal_1h")
_SIGNALS = [h for h in _hyp.HYPOTHESES if h.key in _KEYS]
NAN = float("nan")


class _Symbol:
    def __init__(self, maxlen: int):
        self.bars: deque = deque(maxlen=maxlen)   # (Bar, live)


class FeatureEngine:
    def __init__(self, history: int = HISTORY_BARS):
        self.history = history
        self._s: dict[int, _Symbol] = {}

    @staticmethod
    def _is_live(bar: Bar, live: Optional[bool]) -> bool:
        return bool(live) if live is not None else not bar.approx_share >= 1.0

    def _store(self, bar: Bar, live: Optional[bool]) -> _Symbol:
        st = self._s.setdefault(bar.symbol_id, _Symbol(self.history))
        item = (bar, self._is_live(bar, live))
        if st.bars and bar.slot_end == st.bars[-1][0].slot_end:
            st.bars[-1] = item
        elif st.bars and bar.slot_end < st.bars[-1][0].slot_end:
            raise ValueError(f"bar {bar.slot_end} older than last {st.bars[-1][0].slot_end}")
        else:
            st.bars.append(item)
        return st

    def load(self, bars: Iterable[Bar], live: Optional[bool] = None) -> None:
        """Warm-up: store bars (oldest first) without computing features."""
        for b in bars:
            self._store(b, live)

    def drop_from(self, symbol_id: int, slot_end) -> int:
        """Forget the held bars with slot_end >= slot_end (to rebuild them); returns how many."""
        st = self._s.get(symbol_id)
        n = 0
        while st and st.bars and st.bars[-1][0].slot_end >= slot_end:
            st.bars.pop()
            n += 1
        return n

    def bars(self, symbol_id: int) -> list[Bar]:
        st = self._s.get(symbol_id)
        return [b for b, _ in st.bars] if st else []

    def append(self, bar: Bar, live: Optional[bool] = None) -> FeatureRow:
        st = self._store(bar, live)
        return self._compute(bar.symbol_id, st)

    def spread_median_48(self, symbol_id: int) -> float:
        st = self._s.get(symbol_id)
        return self._spread_median(st) if st else NAN

    @staticmethod
    def _spread_median(st: _Symbol) -> float:
        vals = [b.spread_median for b, _ in list(st.bars)[-SPREAD_MEDIAN_BARS:]
                if b.has_data and not math.isnan(b.spread_median)]
        return float(np.median(vals)) * PRICE_INT if vals else NAN

    @staticmethod
    def frame(items) -> pd.DataFrame:
        """metrics_5m-shaped frame for (Bar, live) items."""
        rows = []
        for b, live in items:
            events = float(b.events) if (b.has_data and live) else NAN
            if not b.has_data:
                events = float(b.events)  # clean_metrics blanks it anyway
            full = b.vap_coverage >= 1.0
            rows.append({
                "ctid_trader_account": _ACCOUNT, "symbol_id": b.symbol_id,
                "slot_start": pd.Timestamp(b.slot_end) - _SLOT, "slot_end": pd.Timestamp(b.slot_end),
                "has_data": 1 if b.has_data else 0, "events": events,
                "mid_open": b.mid_open, "mid_high": b.mid_high, "mid_low": b.mid_low,
                "mid_close": b.mid_close, "spread_mean": b.spread_mean,
                "spread_median": b.spread_median, "rel_spread_mean": b.rel_spread_mean,
                "realised_vol_pct": b.realised_vol_pct,
                "vap_60m_poc": NAN, "vap_60m_va_low": NAN, "vap_60m_va_high": NAN,
                "vap_120m_poc": NAN, "vap_120m_va_low": NAN, "vap_120m_va_high": NAN,
                # a window reaching back before the fetched history is truncated: NaN, never a
                # distorted poc_dist_240 (export_flat flags the same case with coverage < 1)
                "vap_240m_poc": b.vap_240m_poc if full else NAN,
                "vap_240m_va_low": b.vap_240m_va_low if full else NAN,
                "vap_240m_va_high": b.vap_240m_va_high if full else NAN,
                "flow_imbalance": NAN, "book_imbalance": NAN,
            })
        return pd.DataFrame(rows)

    @staticmethod
    def research_features(df: pd.DataFrame) -> pd.DataFrame:
        """clean -> build_features -> signals -> rolling z, exactly as the research batch path."""
        from ._research import clean as _clean
        f = _feat.build_features(_clean.clean_metrics(df))
        f = _hyp_signals(f)
        f["z_poc"] = f.groupby(["ctid_trader_account", "symbol_id"])["hyp_poc_reversion_4h"].transform(_feat._rolling_z)
        f["z_range"] = f.groupby(["ctid_trader_account", "symbol_id"])["hyp_range_reversal_1h"].transform(_feat._rolling_z)
        return f

    def _compute(self, symbol_id: int, st: _Symbol) -> FeatureRow:
        items = list(st.bars)
        f = self.research_features(self.frame(items))
        last = f.iloc[-1]
        bar = items[-1][0]
        cutoff = pd.Timestamp(bar.slot_end) - _feat.ZWINDOW * _SLOT
        live_only = all(live for b, live in items
                        if pd.Timestamp(b.slot_end) > cutoff and b.has_data)
        return FeatureRow(
            symbol_id=symbol_id, slot_end=bar.slot_end,
            poc_dist_240=float(last["poc_dist_240"]), range_pos_12=float(last["range_pos_12"]),
            events_z=float(last["events_z"]),
            sig_poc=float(last["hyp_poc_reversion_4h"]), z_poc=float(last["z_poc"]),
            sig_range=float(last["hyp_range_reversal_1h"]), z_range=float(last["z_range"]),
            realised_vol_pct=bar.realised_vol_pct, spread_median_48=self._spread_median(st),
            approx_share=bar.approx_share, events_z_live_only=bool(live_only))


def _hyp_signals(f: pd.DataFrame) -> pd.DataFrame:
    from ._research import screen as _screen
    return _screen.add_hypothesis_signals(f, _SIGNALS)
