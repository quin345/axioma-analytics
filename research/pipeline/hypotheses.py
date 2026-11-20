"""The pre-registered hypothesis set for the screen (`screen.py`, `run_screen.py`).

Each hypothesis fixes, before any data is seen:

* its **signal**: a point-in-time function of the feature frame;
* its **direction**: the signal is oriented so that a *positive* value predicts a
  *higher* target. A strong result in the opposite direction is evidence against
  the hypothesis, not a licence to flip it;
* its **target**: `ret` (forward log return, `fwd_ret_h`) or `vol` (mean realised
  volatility over the next h bars, `fwd_vol_h`);
* an optional **control**: a column whose effect is partialled out before the
  signal is scored. Every volatility hypothesis controls for current realised
  volatility, so "volatility clusters" cannot pass for a new finding;
* `baseline=True` marks a known effect kept as a yardstick. Baselines are scored
  but never eligible for the top-N.

What the feed can and cannot support (checked in KQL on 2026-11-20): the
liquidity providers quote fixed, symmetric ladder sizes, so total-depth
imbalance is non-zero in under 1.5% of updates and the VWAP skew is about
+/-0.01 bp. Book-pressure hypotheses are therefore left out: they would be
measuring rounding noise. There is no traded volume either; `flow_imbalance` is
the tick-rule proxy on mid changes. What carries information is the mid path,
the spread, the update rate and the update timing.
"""
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Hypothesis:
    key: str
    target: str                      # "ret" or "vol"
    statement: str                   # the claim, in one sentence
    mechanism: str                   # why it could be true
    signal: Callable[[pd.DataFrame], pd.Series]
    control: Optional[str] = None    # feature partialled out of signal and target
    baseline: bool = False           # a known effect, never eligible for the top-N


def _where(mask, value):
    return value.where(mask.fillna(False), 0.0)


HYPOTHESES = [
    # --- Direction: does the signal predict the sign of the next move? ---------
    Hypothesis(
        "reversal_5m", "ret",
        "A 5-minute move partly reverses over the next bars.",
        "LP quotes overshoot on bursts of updates, then re-centre.",
        lambda f: -f["ret_1"]),
    Hypothesis(
        "momentum_15m", "ret",
        "The last 15 minutes' direction continues.",
        "Slow diffusion of information through the quote stream.",
        lambda f: f["ret_3"]),
    Hypothesis(
        "momentum_1h", "ret",
        "The last hour's direction continues.",
        "Intraday trends (macro flow, session drift) persist past an hour.",
        lambda f: f["ret_12"]),
    Hypothesis(
        "tick_flow", "ret",
        "Net up-ticks in a slot predict further rises.",
        "Persistent one-sided quote revisions reflect order flow the feed does not show.",
        lambda f: f["flow_imbalance"]),
    Hypothesis(
        "range_reversal_1h", "ret",
        "Price near the top of its 1-hour range falls back, near the bottom rises.",
        "Range-bound intraday trading; extremes attract liquidity on the other side.",
        lambda f: -f["range_pos_12"]),
    Hypothesis(
        "poc_reversion_1h", "ret",
        "Price reverts toward the 1-hour point of control.",
        "The most-quoted level acts as a magnet (fair-value anchor).",
        lambda f: -f["poc_dist_60"]),
    Hypothesis(
        "poc_reversion_4h", "ret",
        "Price reverts toward the 4-hour point of control.",
        "As above, on a slower anchor.",
        lambda f: -f["poc_dist_240"]),
    Hypothesis(
        "wide_spread_reversal", "ret",
        "Moves made while the spread is unusually wide reverse.",
        "Moves on thin liquidity are noise, not information.",
        lambda f: _where(f["rel_spread_z"] > 1.0, -f["ret_1"])),
    Hypothesis(
        "micro_trend_continuation", "ret",
        "When 10-second returns inside a slot trend (positive autocorrelation), "
        "the slot's move continues.",
        "Trending micro-structure marks an informed move rather than noise.",
        lambda f: f["ret_1"] * f["acf1"]),
    Hypothesis(
        "cross_asset_lead", "ret",
        "The other instrument's last 5-minute move leads this one's next move.",
        "A common risk factor reaches the two quote streams at different speeds.",
        lambda f: f["cross_ret_1"]),

    # --- Volatility: does the signal predict how much price will move? --------
    Hypothesis(
        "vol_clustering", "vol",
        "High volatility now means high volatility next.",
        "Volatility clustering (known; the yardstick the others must beat).",
        lambda f: f["realised_vol_pct"], baseline=True),
    Hypothesis(
        "spread_predicts_vol", "vol",
        "A widening spread predicts higher volatility, beyond current volatility.",
        "LPs widen when they expect to be picked off: they price risk first.",
        lambda f: f["rel_spread_z"], control="realised_vol_pct"),
    Hypothesis(
        "activity_predicts_vol", "vol",
        "A surge in quote updates predicts higher volatility, beyond current volatility.",
        "Update intensity rises as information arrives, ahead of the price move.",
        lambda f: f["events_z"], control="realised_vol_pct"),
    Hypothesis(
        "tick_speed_predicts_vol", "vol",
        "Faster ticks (shorter gaps) predict higher volatility, beyond current volatility.",
        "As above, measured on timing rather than counts.",
        lambda f: -f["tick_gap_z"], control="realised_vol_pct"),
]

BY_KEY = {h.key: h for h in HYPOTHESES}


def evaluate_signal(h, features):
    """The hypothesis signal on the feature frame, with infinities as NaN."""
    return h.signal(features).replace([np.inf, -np.inf], np.nan)
