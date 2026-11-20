"""Shared contracts between the autotrade modules (see DESIGN.md section 3).

Prices are Open API integers in 1/100000 of a price unit unless noted. Bar and FeatureRow
carry floats in display units, as in the research pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class Side(Enum):
    """Trade direction."""
    BUY = 1
    SELL = -1


class IntentKind(Enum):
    """What the strategy wants done for one symbol at one bar."""
    NONE = "none"
    ENTER = "enter"
    EXIT = "exit"
    FLIP = "flip"
    HOLD = "hold"


class ExecMode(Enum):
    """Order style for entries."""
    TAKER = "taker"
    MAKER = "maker"


class RunMode(Enum):
    """DRY_RUN fills from live quotes in the sim broker; DEMO sends orders to the demo account."""
    DRY_RUN = "dry_run"
    DEMO = "demo"


@dataclass(frozen=True)
class SymbolSpec:
    """Broker symbol details resolved from SymbolsList / SymbolById."""
    name: str
    symbol_id: int
    digits: int
    pip_position: int
    lot_size: int
    min_volume: int
    step_volume: int
    max_volume: int
    sl_distance: int
    distance_set_in: str  # "POINTS" or "PERCENTAGE"
    schedule: list[tuple[int, int]]  # (start, end) intervals in seconds from week start
    schedule_tz: str
    holidays: list


@dataclass(frozen=True)
class Quote:
    """Top-of-book spot quote (prices in 1/100000)."""
    symbol_id: int
    bid: int
    ask: int
    ts_ms: int


@dataclass(frozen=True)
class DepthRow:
    """One depth event: best bid/ask stamped with the latest spot timestamp."""
    symbol_id: int
    stamp_ms: int
    best_bid: Optional[int]
    best_ask: Optional[int]
    source: str  # "live" | "tick" | "store"


@dataclass(frozen=True)
class Bucket:
    """10 s bucket: last book state plus event count."""
    symbol_id: int
    start_ms: int
    best_bid: Optional[int]
    best_ask: Optional[int]
    events: int
    source: str  # "live" | "tick" | "store"


@dataclass(frozen=True)
class Bar:
    """5-minute bar, floats in display units (same columns as research metrics_5m)."""
    symbol_id: int
    slot_end: datetime  # UTC
    has_data: bool
    events: int
    mid_open: float
    mid_high: float
    mid_low: float
    mid_close: float
    spread_mean: float
    spread_median: float
    rel_spread_mean: float
    realised_vol_pct: float
    vap_240m_poc: float
    vap_240m_va_low: float
    vap_240m_va_high: float
    vap_240m_volume_events: float
    approx_share: float  # share of the VAP window built from non-live (tick/store) data
    # share of the 240 min VAP window after the start of the data read (export_flat vap_*_coverage);
    # < 1 only for warm-up bars whose window reaches back before the fetched history
    vap_coverage: float = 1.0


@dataclass(frozen=True)
class FeatureRow:
    """Features for one symbol and slot, as computed by the FeatureEngine."""
    symbol_id: int
    slot_end: datetime  # UTC
    poc_dist_240: float
    range_pos_12: float
    events_z: float
    sig_poc: float
    z_poc: float
    sig_range: float
    z_range: float
    realised_vol_pct: float
    spread_median_48: float
    approx_share: float
    events_z_live_only: bool


@dataclass(frozen=True)
class Intent:
    """Strategy output for one symbol and bar."""
    kind: IntentKind
    symbol_id: int
    side: Optional[Side]
    size_mult: float
    limit_price: Optional[int]
    expiry_ms: Optional[int]
    stop_rel: Optional[int]
    reason: str
    signal_id: str


@dataclass(frozen=True)
class ExecReport:
    """Normalised execution event from the broker (real or simulated)."""
    execution_type: str
    order_id: Optional[int]
    position_id: Optional[int]
    client_order_id: Optional[str]
    label: Optional[str]
    symbol_id: Optional[int]
    side: Optional[Side]
    filled_volume: int
    price: Optional[float]
    status: Optional[str]
    error_code: Optional[str]
    gross_profit: Optional[int]
    commission: Optional[int]
    swap: Optional[int]
    money_digits: Optional[int]
    ts_ms: int


@dataclass(frozen=True)
class PositionView:
    """What the strategy sees of the bot's own open position on one symbol.

    Produced by execution.OrderManager.position_view(); consumed by
    strategy.decide(). `exit_at_ms` is the scheduled time exit (next slot_end
    + grace); `entry_price` is in display units.
    """
    symbol_id: int
    side: "Side"
    volume: int                 # Open API volume units (cents of base units)
    entry_price: float
    opened_ms: int
    exit_at_ms: int
    position_id: Optional[int]  # None in dry-run (SimBroker)


@dataclass(frozen=True)
class RiskContext:
    """Everything risk.RiskManager.check() needs, assembled by the runner.

    Times are epoch ms (UTC). Spreads are in Open API price integers.
    """
    now_ms: int
    quote: Optional["Quote"]
    spread_median_48: Optional[float]  # median of the last 48 bars' spread_median, price ints
    last_data_ms: Optional[int]        # last spot/depth event for this symbol
    session_open: bool
    minutes_to_close: Optional[float]  # None if no close is scheduled soon / unknown
    bars_since_open: int
    open_positions: int                # bot's own positions across all symbols
    trades_today: int
    orders_last_min: int
    pnl_today_bps: float
    pnl_today_pct: float               # % of start-of-day balance
    consec_losses: int
    rejects_window: int                # rejects/order errors in the breaker window
    kill_file: bool
    pause_file: bool
    seconds_since_reconnect: Optional[float]
    # Length (s) of a connection outage not yet acted on: grows while down, kept after the
    # reconnect until the runner has flattened (or had nothing to flatten), 0.0 otherwise.
    # None = unknown: risk falls back to the last_data_ms proxy.
    disconnect_s: Optional[float] = None
