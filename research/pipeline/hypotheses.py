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

Timeframes. The same fourteen ideas run on two bar sizes (`pipeline/timeframe.py`):
the original 5-minute screen (`hypotheses_for(TF_5M)`, names and wording frozen) and
a 1-minute screen (`hypotheses_for(TF_1M)`).

Pre-registration of the 1-minute set. Written before any
`metrics_1m` row was read by the screen; it must not be edited after the first run.

* Same ideas, same formulas, same directions, same targets and controls as the
  5-minute set. Nothing was added, dropped or re-signed.
* Per-bar counts are unchanged *in bars*: horizons of 1, 3, 6 and 12 bars (so 1, 3,
  6 and 12 minutes), `ret_1`, `ret_3`, `ret_12`, a 12-bar range, a 3-bar flow
  average, an embargo of 12 bars.
* Every window that was 4 hours becomes 1 hour: the rolling z-score window
  (60 bars, min_periods 15), the IC block (1 hour; `MIN_BLOCK_OBS` stays 12), the
  signal z-window of the taker test (60 bars) and the long POC window (60 min). The
  POC windows keep the 1/4, 1/2, 1 ratio: 15, 30 and 60 minutes. The two POC
  hypotheses therefore use the 15-minute and the 1-hour point of control
  (`poc_reversion_15m`, `poc_reversion_1h`).
* Rationale for the rescaling: the claims are about microstructure that plays out
  over a few bars (overshoot then re-centre, slow information diffusion, thin-liquidity
  noise), so the bar count is the natural unit, while the normalisation and anchor
  windows should span a comparable number of bars (60 one-minute bars against 48
  five-minute bars), which is the old 4-hour window scaled by the 1/4 bar-size ratio.
* Thresholds, FDR (0.10), top N (3), split (0.6), the duplicate cut-off (0.7) and
  the Bonferroni rule are unchanged.
* Expectation, stated in advance: at one-minute bars the spread is a larger share
  of the move, so return hypotheses are less likely to cover costs; volatility
  hypotheses are the more likely survivors. The screen decides, not this note.
"""
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd

from .timeframe import TF_5M, span, tag


@dataclass(frozen=True)
class Hypothesis:
    key: str
    target: str                      # "ret" or "vol"
    statement: str                   # the claim, in one sentence
    mechanism: str                   # why it could be true
    signal: Callable[[pd.DataFrame], pd.Series]
    control: Optional[str] = None    # feature partialled out of signal and target
    baseline: bool = False           # a known effect, never eligible for the top-N


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


def _last(minutes):
    return "The last hour's" if minutes == 60 else f"The last {minutes} minutes'"


def hypotheses_for(tf):
    """The fourteen hypotheses on timeframe `tf`; names and wording derive from its windows."""
    slot, short, long_ = tf.slot_minutes, tf.minutes(3), tf.minutes(12)
    poc_s, poc_l = tf.poc_short, tf.poc_long
    col_s, col_l = f"poc_dist_{poc_s}", f"poc_dist_{poc_l}"
    trend_why = ("Intraday trends (macro flow, session drift) persist past an hour." if long_ == 60
                 else "Short bursts of directional flow keep going for a few more bars.")
    return [
        # --- Direction: does the signal predict the sign of the next move? -------
        Hypothesis(
            f"reversal_{tag(slot)}", "ret",
            f"A {span(slot)} move partly reverses over the next bars.",
            "LP quotes overshoot on bursts of updates, then re-centre.",
            lambda f: -f["ret_1"]),
        Hypothesis(
            f"momentum_{tag(short)}", "ret",
            f"{_last(short)} direction continues.",
            "Slow diffusion of information through the quote stream.",
            lambda f: f["ret_3"]),
        Hypothesis(
            f"momentum_{tag(long_)}", "ret",
            f"{_last(long_)} direction continues.",
            trend_why,
            lambda f: f["ret_12"]),
        Hypothesis(
            "tick_flow", "ret",
            "Net up-ticks in a slot predict further rises.",
            "Persistent one-sided quote revisions reflect order flow the feed does not show.",
            lambda f: f["flow_imbalance"]),
        Hypothesis(
            f"range_reversal_{tag(long_)}", "ret",
            f"Price near the top of its {span(long_)} range falls back, near the bottom rises.",
            "Range-bound intraday trading; extremes attract liquidity on the other side.",
            lambda f: -f["range_pos_12"]),
        Hypothesis(
            f"poc_reversion_{tag(poc_s)}", "ret",
            f"Price reverts toward the {span(poc_s)} point of control.",
            "The most-quoted level acts as a magnet (fair-value anchor).",
            lambda f: -f[col_s]),
        Hypothesis(
            f"poc_reversion_{tag(poc_l)}", "ret",
            f"Price reverts toward the {span(poc_l)} point of control.",
            "As above, on a slower anchor.",
            lambda f: -f[col_l]),
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
            f"The other instrument's last {span(slot)} move leads this one's next move.",
            "A common risk factor reaches the two quote streams at different speeds.",
            lambda f: f["cross_ret_1"]),

        # --- Volatility: does the signal predict how much price will move? ------
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


HYPOTHESES = hypotheses_for(TF_5M)   # the original 5-minute set
BY_KEY = {h.key: h for h in HYPOTHESES}


def evaluate_signal(h, features):
    """The hypothesis signal on the feature frame, with infinities as NaN."""
    return h.signal(features).replace([np.inf, -np.inf], np.nan)
