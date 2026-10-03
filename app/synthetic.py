"""Synthetic tick generator.

Used as a fallback so the dashboard is demonstrable when the warehouse is
unreachable (no credentials, endpoint down, or no tick table yet). The output is
deliberately realistic - variable arrival times, a stochastic spread that widens
with volatility, occasional volume spikes, and intraday activity clustering.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "XAUUSD", "BTCUSD"]

# Approximate realistic reference prices, used to pick a sensible tick size.
_BASE_PRICE = {
    "EURUSD": 1.0850, "GBPUSD": 1.2650, "USDJPY": 151.20, "AUDUSD": 0.6520,
    "XAUUSD": 2335.0, "BTCUSD": 64250.0, "ETHUSD": 3120.0, "NAS100": 18400.0,
}

_PIP = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001, "USDJPY": 0.01,
    "XAUUSD": 0.01, "BTCUSD": 0.1, "ETHUSD": 0.01, "NAS100": 0.1,
}


def available_symbols() -> list[str]:
    return list(DEFAULT_SYMBOLS)


def generate(
    symbol: str = "EURUSD",
    *,
    ticks: int = 20_000,
    seed: int | None = 7,
    volatility_bps: float = 0.35,
    base_spread_bps: float = 0.8,
) -> pd.DataFrame:
    """Generate one session of plausible tick data for `symbol`."""
    rng = np.random.default_rng(seed if seed is not None else abs(hash(symbol)) % (2**32))
    symbol = symbol.upper()
    n = max(50, int(ticks))

    base = _BASE_PRICE.get(symbol, 100.0)
    pip = _PIP.get(symbol, max(round(base * 1e-5, 5), 1e-5))

    # --- Time: business-hours intraday clustering over one trading day ---
    day = pd.Timestamp("2026-09-28", tz="UTC")
    open_s = 8 * 3600
    close_s = 17 * 3600
    # Activity is U-shaped: heavy at the open and close, quieter midday.
    u = rng.beta(1.6, 1.6, n)
    secs = open_s + u * (close_s - open_s)
    secs.sort()
    # Most markets quote fast; a minority of slow ticks add realistic clustering.
    secs = np.sort(secs + rng.exponential(scale=0.4, size=n) * (close_s - open_s) / n * 20)
    ts = day + pd.to_timedelta(np.clip(secs, open_s, close_s), unit="s")
    ts = pd.Series(ts).drop_duplicates().reset_index(drop=True)
    n = len(ts)

    # --- Price: mean-reverting random walk with volatility clustering ---
    shock = rng.standard_normal(n)
    # GARCH-like leverage: big moves tend to follow big moves.
    vol_state = np.zeros(n)
    vol_state[0] = volatility_bps
    for i in range(1, n):
        vol_state[i] = 0.92 * vol_state[i - 1] + 0.08 * volatility_bps + 0.35 * abs(shock[i - 1]) * volatility_bps
    vol_state = np.clip(vol_state, volatility_bps * 0.3, volatility_bps * 12)

    steps = rng.standard_normal(n) * vol_state / 10_000
    # Gentle pull back toward the session open price keeps the walk plausible.
    level = base + np.cumsum(steps * base)
    level = base + (level - base) * 0.985
    # Quantise to the instrument's tick size.
    mid = np.round(level / pip) * pip

    # --- Spread: widens with volatility, tighter when volume is high ---
    spread_bps = np.abs(rng.gamma(shape=4.0, scale=base_spread_bps / 4.0, size=n))
    spread_bps = np.clip(spread_bps * (0.7 + 0.6 * vol_state / volatility_bps), 0.05, None)
    spread = mid * spread_bps / 10_000

    # --- Volume: log-normal with a heavy tail ---
    volume = rng.lognormal(mean=1.4, sigma=1.25, size=n)
    # Occasional block trades.
    block = rng.random(n) < 0.004
    volume[block] *= rng.uniform(25, 120, size=block.sum())

    half = spread / 2.0
    df = pd.DataFrame({
        "ts": ts,
        "symbol": symbol,
        "bid": mid - half,
        "ask": mid + half,
        "last": mid + rng.normal(0, half * 0.4),
        "volume": np.round(volume, 4),
    })
    return df


def multi(symbols: list[str] | None = None, *, ticks: int = 6_000, seed: int = 11) -> pd.DataFrame:
    """Generate a mixed frame across several symbols."""
    syms = symbols or DEFAULT_SYMBOLS
    frames = [generate(s, ticks=ticks, seed=(seed + i * 17)) for i, s in enumerate(syms)]
    return pd.concat(frames, ignore_index=True).sort_values("ts").reset_index(drop=True)