"""FeatureEngine: runs the research feature code on a rolling window of bars.

Each appended Bar becomes a metrics_5m-shaped row; the last ~150 bars per symbol go through the
research functions themselves: research/pipeline/clean.py:clean_metrics (5-minute grid with NaN
rows for gaps / weekends / gold's break), features.py:build_features (poc_dist_240, range_pos_12
with min_periods 6, events_z; _rolling_z window 48, min_periods 12), hypotheses.py signals
(sig poc = -poc_dist_240 = hyp poc_reversion_4h, sig range = -range_pos_12 = hyp
range_reversal_1h) and screen.py:218-222 (z = _rolling_z of the signal per instrument).
The 60/120 min VAP columns build_features loops over are supplied as NaN (only 240 is used by
the signals; NaN does not propagate to any feature the bot uses).

Timeframe (autotrade/timeframe.py): the description above is the 5-minute default. With tf=TF_1M
the grid is 1 minute, the POC column poc_dist_60 (hyp poc_reversion_1h), the range signal
range_reversal_12m, the z-window 60 bars (min_periods 15) and the NaN VAP columns are 15/30 min: the
research functions get tf=research TF_1M, so live == research for each timeframe.

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
from .timeframe import TF_5M, BotTimeframe
from .types import Bar, FeatureRow

HISTORY_BARS = TF_5M.history_bars
SPREAD_MEDIAN_BARS = TF_5M.zwindow     # 48 at 5m; tf.zwindow in general
PRICE_INT = 100000
_ACCOUNT = "bot"
NAN = float("nan")


def _signals(tf: BotTimeframe) -> list:
    """The research Hypothesis objects of the traded and the journalled signal on tf."""
    keys = (tf.poc_key, tf.range_key)
    hs = [h for h in _hyp.hypotheses_for(tf.research) if h.key in keys]
    if len(hs) != 2:
        raise RuntimeError(f"research hypotheses for {tf.key} lack {keys}")
    return hs


class _Symbol:
    def __init__(self, maxlen: int):
        self.bars: deque = deque(maxlen=maxlen)   # (Bar, live)


class FeatureEngine:
    def __init__(self, history: Optional[int] = None, tf: BotTimeframe = TF_5M):
        self.tf = tf
        self.history = tf.history_bars if history is None else history
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
        """Median spread_median over the last z-window of bars (48 at 5m), price ints."""
        st = self._s.get(symbol_id)
        return self._spread_median(st, self.tf.zwindow) if st else NAN

    @staticmethod
    def _spread_median(st: _Symbol, n: int = SPREAD_MEDIAN_BARS) -> float:
        vals = [b.spread_median for b, _ in list(st.bars)[-n:]
                if b.has_data and not math.isnan(b.spread_median)]
        return float(np.median(vals)) * PRICE_INT if vals else NAN

    @staticmethod
    def frame(items, tf: BotTimeframe = TF_5M) -> pd.DataFrame:
        """metrics_5m/_1m-shaped frame for (Bar, live) items. Only the POC window the signal uses is
        filled; the research's shorter VAP windows are NaN (no feature the bot uses reads them)."""
        slot = pd.Timedelta(minutes=tf.slot_minutes)
        rows = []
        for b, live in items:
            events = float(b.events) if (b.has_data and live) else NAN
            if not b.has_data:
                events = float(b.events)  # clean_metrics blanks it anyway
            full = b.vap_coverage >= 1.0
            row = {
                "ctid_trader_account": _ACCOUNT, "symbol_id": b.symbol_id,
                "slot_start": pd.Timestamp(b.slot_end) - slot, "slot_end": pd.Timestamp(b.slot_end),
                "has_data": 1 if b.has_data else 0, "events": events,
                "mid_open": b.mid_open, "mid_high": b.mid_high, "mid_low": b.mid_low,
                "mid_close": b.mid_close, "spread_mean": b.spread_mean,
                "spread_median": b.spread_median, "rel_spread_mean": b.rel_spread_mean,
                "realised_vol_pct": b.realised_vol_pct,
            }
            for w in tf.research.poc_windows:
                for k in ("poc", "va_low", "va_high"):
                    row[f"vap_{w}m_{k}"] = NAN
            # a window reaching back before the fetched history is truncated: NaN, never a
            # distorted poc_dist (export_flat flags the same case with coverage < 1)
            p = f"vap_{tf.vap_minutes}m_"
            row[p + "poc"] = b.vap_240m_poc if full else NAN
            row[p + "va_low"] = b.vap_240m_va_low if full else NAN
            row[p + "va_high"] = b.vap_240m_va_high if full else NAN
            row["flow_imbalance"] = NAN
            row["book_imbalance"] = NAN
            rows.append(row)
        return pd.DataFrame(rows)

    @staticmethod
    def research_features(df: pd.DataFrame, tf: BotTimeframe = TF_5M) -> pd.DataFrame:
        """clean -> build_features -> signals -> rolling z, exactly as the research batch path
        (screen.trade_test: z over tf.zwindow bars)."""
        from ._research import clean as _clean
        from ._research import screen as _screen
        rtf = tf.research
        f = _feat.build_features(_clean.clean_metrics(df, tf=rtf), tf=rtf)
        f = _screen.add_hypothesis_signals(f, _signals(tf), tf=rtf)
        zw = tf.zwindow
        g = f.groupby(["ctid_trader_account", "symbol_id"])
        f["z_poc"] = g[f"hyp_{tf.poc_key}"].transform(lambda s: _feat._rolling_z(s, zw))
        f["z_range"] = g[f"hyp_{tf.range_key}"].transform(lambda s: _feat._rolling_z(s, zw))
        return f

    def _compute(self, symbol_id: int, st: _Symbol) -> FeatureRow:
        tf = self.tf
        items = list(st.bars)
        f = self.research_features(self.frame(items, tf), tf)
        last = f.iloc[-1]
        bar = items[-1][0]
        cutoff = pd.Timestamp(bar.slot_end) - tf.zwindow * pd.Timedelta(minutes=tf.slot_minutes)
        live_only = all(live for b, live in items
                        if pd.Timestamp(b.slot_end) > cutoff and b.has_data)
        return FeatureRow(
            symbol_id=symbol_id, slot_end=bar.slot_end,
            poc_dist_240=float(last[tf.poc_col]), range_pos_12=float(last["range_pos_12"]),
            events_z=float(last["events_z"]),
            sig_poc=float(last[f"hyp_{tf.poc_key}"]), z_poc=float(last["z_poc"]),
            sig_range=float(last[f"hyp_{tf.range_key}"]), z_range=float(last["z_range"]),
            realised_vol_pct=bar.realised_vol_pct, spread_median_48=self._spread_median(st, tf.zwindow),
            approx_share=bar.approx_share, events_z_live_only=bool(live_only))
