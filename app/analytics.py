"""Tick-level analytics.

Everything operates on the canonical frame from `app.schema`:

    ts (datetime, UTC) | symbol | bid | ask | last | volume | mid

All public functions return JSON-serialisable dicts/lists so FastAPI can hand
them straight to the browser.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

TIMEFRAMES: dict[str, str] = {
    "1s": "1s", "5s": "5s", "10s": "10s", "30s": "30s",
    "1m": "1min", "2m": "2min", "5m": "5min", "15m": "15min",
    "30m": "30min", "1h": "1h", "2h": "2h", "4h": "4h", "1d": "1D",
}


def _f(x: Any, default: float | None = None) -> float | None:
    """JSON-safe float: NaN/Inf become None."""
    if x is None:
        return default
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return None if (math.isnan(v) or math.isinf(v)) else v


def clean(obj: Any) -> Any:
    """Recursively replace NaN/Inf with None so the payload is valid JSON.

    `json.dumps` happily emits bare `NaN`, which `JSON.parse` rejects in the
    browser - so sanitise at the boundary rather than trusting each leaf.
    """
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, float):
        return _f(obj)
    if isinstance(obj, (np.floating, np.integer)):
        return _f(obj)
    if obj is pd.NaT:
        return None
    return obj


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure the canonical frame is sorted, deduped and enriched for charting."""
    if df is None or df.empty:
        return pd.DataFrame(
            columns=["ts", "symbol", "bid", "ask", "last", "volume", "mid", "spread", "spread_bps"]
        )

    out = df.copy()
    out["ts"] = pd.to_datetime(out["ts"], errors="coerce", utc=True)

    # Derive mid when absent: prefer the quote midpoint, fall back to last.
    if "mid" not in out.columns:
        out["mid"] = np.nan
    if "bid" not in out.columns:
        out["bid"] = np.nan
    if "ask" not in out.columns:
        out["ask"] = np.nan
    if "last" not in out.columns:
        out["last"] = np.nan
    for col in ("mid", "bid", "ask", "last"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "symbol" not in out.columns:
        out["symbol"] = pd.Series(pd.NA, index=out.index, dtype="string")

    quoted = (out["bid"] + out["ask"]) / 2.0
    out["mid"] = out["mid"].where(out["mid"].notna(), quoted)
    out["mid"] = out["mid"].where(out["mid"].notna(), out["last"])

    out = out.dropna(subset=["ts", "mid"]).sort_values("ts")
    out = out[~out.duplicated(subset="ts", keep="last")].reset_index(drop=True)
    if "volume" not in out.columns:
        out["volume"] = 0.0
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0.0)
    out["spread"] = (out["ask"] - out["bid"]).where(out["ask"].notna() & out["bid"].notna())
    # Relative spread in basis points is comparable across symbols.
    out["spread_bps"] = (out["spread"] / out["mid"].replace(0, np.nan) * 10_000).astype(float)
    return out


def summary(df: pd.DataFrame) -> dict:
    """Headline statistics for the selected symbol/window."""
    if df.empty:
        return {"ticks": 0}

    mid = df["mid"]
    first, last = float(mid.iloc[0]), float(mid.iloc[-1])
    duration_s = float((df["ts"].iloc[-1] - df["ts"].iloc[0]).total_seconds())
    returns = mid.pct_change().dropna()
    vol_bps = float(np.sqrt(returns.pow(2).sum() / duration_s) * 10_000) if duration_s > 0 and len(returns) else None
    spread = df["spread_bps"].dropna()
    volume = df["volume"].fillna(0.0)
    iarr = df["interarrival_ms"].dropna() if "interarrival_ms" in df.columns else pd.Series(dtype=float)
    has_volume = bool((volume > 0).any())

    return {
        "ticks": int(len(df)),
        "symbol": _first(df["symbol"]),
        "start": df["ts"].iloc[0].isoformat(),
        "end": df["ts"].iloc[-1].isoformat(),
        "duration_seconds": duration_s,
        "session_span": _session_span(df),
        "open": _f(first),
        "close": _f(last),
        "high": _f(mid.max()),
        "low": _f(mid.min()),
        "change": _f(last - first),
        "change_pct": _f((last - first) / first * 100 if first else None),
        "vwap": _f((mid * volume).sum() / volume.sum()) if has_volume and volume.sum() > 0 else _f(mid.mean()),
        "mean_mid": _f(mid.mean()),
        "std_mid": _f(mid.std()),
        "realized_vol_bps": _f(vol_bps),
        "annualised_vol_pct": _f(vol_bps * math.sqrt(252 * 24 * 3600) / 10_000 * 100 if vol_bps else None),
        "avg_spread": _f(spread.mean()) if len(spread) else None,
        "median_spread": _f(spread.median()) if len(spread) else None,
        "p95_spread": _f(spread.quantile(0.95)) if len(spread) else None,
        "max_spread": _f(spread.max()) if len(spread) else None,
        "min_spread": _f(spread.min()) if len(spread) else None,
        "total_volume": _f(volume.sum()) if has_volume else None,
        "avg_tick_size": _f(abs(df["price_change"].dropna()).mean()) if "price_change" in df.columns else None,
        "ticks_per_minute": _f(len(df) / (duration_s / 60), 0.0) if duration_s > 0 else None,
        "avg_interarrival_ms": _f(iarr.mean()) if len(iarr) else None,
        "median_interarrival_ms": _f(iarr.median()) if len(iarr) else None,
        "max_interarrival_ms": _f(iarr.max()) if len(iarr) else None,
        "buy_ticks": int((df["tick_dir"] > 0).sum()) if "tick_dir" in df.columns else None,
        "sell_ticks": int((df["tick_dir"] < 0).sum()) if "tick_dir" in df.columns else None,
        "unchanged_ticks": int((df["tick_dir"] == 0).sum()) if "tick_dir" in df.columns else None,
        "large_trades": int((df["volume"] > _large_trade_threshold(df)).sum()) if has_volume else 0,
    }


def _first(series: pd.Series) -> str | None:
    s = series.dropna()
    return None if s.empty else str(s.iloc[0])


def ohlcv(df: pd.DataFrame, timeframe: str = "1m", *, max_bars: int = 1500) -> dict:
    """Resample ticks into OHLCV bars plus derived bar metrics."""
    if df.empty:
        return {"timeframe": timeframe, "bars": []}

    rule = TIMEFRAMES.get(timeframe, timeframe)
    d = df.set_index("ts").sort_index()
    # Pre-compute price*volume so a true VWAP can be derived per bar.
    d["_pv"] = d["mid"] * d["volume"]
    agg = {"mid": ["first", "max", "min", "last", "mean", "count"],
           "volume": ["sum"], "_pv": ["sum"],
           "spread_bps": ["mean"]}

    bars = d.resample(rule).agg(agg)
    # Resample creates empty bins; keep only bins that actually contain ticks.
    bars = bars[bars[("mid", "count")] > 0].dropna(subset=[("mid", "count")])
    if bars.empty:
        return {"timeframe": timeframe, "bars": []}

    bars.columns = ["_".join(c for c in col if c) for col in bars.columns]
    bars = bars.tail(max_bars)

    out: list[dict] = []
    prev_close = None
    for ts, row in bars.iterrows():
        o, h, l, c = (row.get("mid_first"), row.get("mid_max"), row.get("mid_min"), row.get("mid_last"))
        volume = _f(row.get("volume_sum"), 0.0) or 0.0
        pv = _f(row.get("_pv_sum"), 0.0) or 0.0
        vwap = (pv / volume) if volume > 0 else None
        rec = {
            "t": ts.isoformat(),
            "o": _f(o), "h": _f(h), "l": _f(l), "c": _f(c),
            "v": volume,
            "n": int(row.get("mid_count") or 0),
            "spread": _f(row.get("spread_bps_mean")),
            "vwap": _f(vwap),
        }
        if prev_close:
            rec["chg"] = _f((c - prev_close) / prev_close * 100)
        prev_close = c if c is not None else prev_close
        out.append(rec)

    return {"timeframe": timeframe, "bars": out}


def microstructure(df: pd.DataFrame, *, max_points: int = 1200) -> dict:
    """Spread, order-flow, trade-sign and arrival-intensity analytics."""
    if df.empty:
        return {"spread": [], "order_flow": [], "interarrival": [], "trade_sign": {}, "large_trades": []}

    d = df.copy()

    # --- Order flow imbalance: normalised aggressive volume (OFI-style) ---
    signed = d.get("signed_volume")
    if signed is not None and float(np.abs(signed).sum()) > 0:
        window = max(1, len(d) // max_points)
        ofi = signed.rolling(window, min_periods=1).sum()
        total = signed.abs().rolling(window, min_periods=1).sum().replace(0, np.nan)
        # clip guards against float overshoot (e.g. -1.0000000000000007).
        ofi_norm = (ofi / total).fillna(0.0).clip(-1.0, 1.0)
        ofi_out = [
            {"t": ts.isoformat(), "ofi": _f(o), "imbalance": _f(v)}
            for ts, o, v in zip(d["ts"], ofi, ofi_norm)
        ][-max_points:]
    else:
        # No volume -> fall back to tick-direction imbalance (buy vs sell ticks).
        window = max(1, len(d) // max_points)
        signed_ticks = pd.Series(d["tick_dir"].fillna(0.0) if "tick_dir" in d.columns else 0.0)
        ofi_norm = signed_ticks.rolling(window, min_periods=1).mean()
        ofi_out = [
            {"t": ts.isoformat(), "ofi": _f(v), "imbalance": _f(v)}
            for ts, v in zip(d["ts"], ofi_norm)
        ][-max_points:]

    # --- Spread evolution ---
    sp = d[["ts", "spread_bps"]].dropna()
    spread_out = [
        {"t": ts.isoformat(), "spread_bps": _f(v)} for ts, v in zip(sp["ts"], sp["spread_bps"])
    ][-max_points:]

    # --- Inter-arrival times (liquidity / activity bursts) ---
    ia = d[["ts", "interarrival_ms"]].dropna() if "interarrival_ms" in d.columns else pd.DataFrame()
    ia_out = [
        {"t": ts.isoformat(), "ms": _f(v)} for ts, v in zip(ia["ts"], ia["interarrival_ms"])
    ][-max_points:] if not ia.empty else []

    # --- Trade sign distribution (Lee-Ready style tick test) ---
    td = d["tick_dir"].fillna(0.0) if "tick_dir" in d.columns else pd.Series(0.0, index=d.index)
    buys, sells = int((td > 0).sum()), int((td < 0).sum())
    unchanged = int((td == 0).sum())
    total_signed = buys + sells

    # --- Large trades and their immediate price impact ---
    threshold = _large_trade_threshold(d)
    large: list[dict] = []
    if math.isfinite(threshold):
        big = d[d["volume"] > threshold]
        for _, row in big.tail(200).iterrows():
            idx = row.name
            impact = None
            if idx + 1 < len(d):
                nxt = d.iloc[idx + 1]
                if row["mid"]:
                    impact = (nxt["mid"] - row["mid"]) / row["mid"] * 10_000
            large.append({
                "t": row["ts"].isoformat(),
                "volume": _f(row["volume"]),
                "price": _f(row["mid"]),
                "impact_bps_1tick": _f(impact),
                "direction": "buy" if (row.get("tick_dir") or 0) > 0 else ("sell" if (row.get("tick_dir") or 0) < 0 else "flat"),
            })

    return {
        "spread": spread_out,
        "order_flow": ofi_out,
        "interarrival": ia_out,
        "large_trades": large,
        "large_trade_threshold": _f(threshold if math.isfinite(threshold) else None),
        "trade_sign": {
            "buy_ticks": buys,
            "sell_ticks": sells,
            "unchanged_ticks": unchanged,
            "buy_ratio": _f(buys / total_signed) if total_signed else None,
            "sell_ratio": _f(sells / total_signed) if total_signed else None,
            "imbalance": _f((buys - sells) / total_signed) if total_signed else None,
        },
    }
def _large_trade_threshold(df: pd.DataFrame) -> float:
    """Tail cut for 'large trade' detection - the 95th percentile of volume."""
    v = df["volume"].fillna(0.0)
    positive = v[v > 0]
    return float(positive.quantile(0.95)) if len(positive) > 20 else math.inf


def volume_profile(df: pd.DataFrame, *, bins: int = 60) -> dict:
    """Volume-at-price: where activity clusters = support / resistance zones."""
    if df.empty:
        return {"bins": [], "poc": None, "value_area": None}

    d = df.dropna(subset=["mid"])
    if d.empty:
        return {"bins": [], "poc": None, "value_area": None}

    lo, hi = float(d["mid"].min()), float(d["mid"].max())
    if hi <= lo:
        return {"bins": [{"price": lo, "volume": float(d["volume"].sum()), "ticks": int(len(d))}],
                "poc": lo, "value_area": [lo, hi]}

    edges = np.linspace(lo, hi, int(bins) + 1)
    labels = [(edges[i] + edges[i + 1]) / 2 for i in range(len(edges) - 1)]
    idx = np.clip(np.digitize(d["mid"], edges) - 1, 0, len(labels) - 1)

    vol = pd.Series(d["volume"].to_numpy(), index=idx).groupby(level=0).sum().reindex(range(len(labels))).fillna(0.0)
    cnt = pd.Series(1, index=idx).groupby(level=0).sum().reindex(range(len(labels))).fillna(0)

    profile = [
        {"price": _f(labels[i]), "volume": _f(vol.iloc[i], 0.0), "ticks": int(cnt.iloc[i])}
        for i in range(len(labels))
    ]

    # Point of control = busiest price; value area = 70% of total volume.
    poc_i = int(np.argmax(vol.to_numpy()))
    total = float(vol.sum())
    va_lo = va_hi = None
    if total > 0:
        lo_i = hi_i = poc_i
        acc = float(vol.iloc[poc_i])
        while acc < 0.70 * total and (lo_i > 0 or hi_i < len(vol) - 1):
            down = float(vol.iloc[lo_i - 1]) if lo_i > 0 else -1.0
            up = float(vol.iloc[hi_i + 1]) if hi_i < len(vol) - 1 else -1.0
            if up >= down:
                hi_i += 1; acc += up
            else:
                lo_i -= 1; acc += down
        va_lo, va_hi = _f(labels[lo_i]), _f(labels[hi_i])

    return {
        "bins": profile,
        "poc": _f(labels[poc_i]),
        "value_area": [va_lo, va_hi] if va_lo is not None else None,
        "high_volume_nodes": _high_volume_nodes(profile),
    }


def _high_volume_nodes(profile: list[dict], top: int = 5) -> list[dict]:
    """Local maxima in the profile - candidate support/resistance levels."""
    vols = [p["volume"] for p in profile]
    if not vols:
        return []
    cutoff = float(np.percentile(vols, 80))
    nodes = [
        profile[i]
        for i in range(1, len(profile) - 1)
        if vols[i] >= cutoff and vols[i] >= vols[i - 1] and vols[i] >= vols[i + 1]
    ]
    nodes.sort(key=lambda n: n["volume"], reverse=True)
    return nodes[:top]


def rolling_metrics(df: pd.DataFrame, *, window: int = 50, max_points: int = 1200) -> dict:
    """Rolling realised volatility, spread and cumulative return."""
    if df.empty or len(df) < 5:
        return {"volatility": [], "spread": [], "cumulative_return": [], "window": window}

    w = max(2, int(window))
    d = df.copy()
    mid = d["mid"]
    # Per-tick log returns, annualised within the rolling window.
    logret = np.log(mid / mid.shift(1))
    ticks_per_year = _ticks_per_year(d)
    roll_vol = logret.rolling(w).std() * math.sqrt(ticks_per_year) * 100

    cum = (mid / mid.iloc[0] - 1) * 100
    roll_spread = d["spread_bps"].rolling(w).mean() if "spread_bps" in d.columns else pd.Series(dtype=float)

    start = max(0, len(d) - max_points)
    return {
        "window": w,
        "volatility": [
            {"t": ts.isoformat(), "v": _f(v)} for ts, v in zip(d["ts"], roll_vol) if not pd.isna(v)
        ][-max_points:],
        "spread": [
            {"t": ts.isoformat(), "v": _f(v)} for ts, v in zip(d["ts"], roll_spread) if not pd.isna(v)
        ][-max_points:],
        "cumulative_return": [
            {"t": ts.isoformat(), "v": _f(v)} for ts, v in zip(d["ts"], cum[start:])
        ],
    }


def _ticks_per_year(df: pd.DataFrame) -> float:
    """Scale factor that turns per-tick stdev into an annualised percentage."""
    if len(df) < 2:
        return math.sqrt(252 * 24 * 3600)
    dur_s = float((df["ts"].iloc[-1] - df["ts"].iloc[0]).total_seconds())
    if dur_s <= 0:
        return math.sqrt(252 * 24 * 3600)
    ticks_per_second = len(df) / dur_s
    return max(1.0, ticks_per_second * 252 * 24 * 3600)
def _session_span(df: pd.DataFrame) -> list[dict]:
    """Break the data into contiguous trading sessions (gap > 30min = new session)."""
    if df.empty:
        return []
    ts = df["ts"]
    gaps = ts.diff().dt.total_seconds().fillna(0.0)
    group = (gaps > 1800).cumsum()
    out = []
    for _, chunk in df.groupby(group):
        out.append({
            "start": chunk["ts"].iloc[0].isoformat(),
            "end": chunk["ts"].iloc[-1].isoformat(),
            "ticks": int(len(chunk)),
            "minutes": _f((chunk["ts"].iloc[-1] - chunk["ts"].iloc[0]).total_seconds() / 60),
        })
    return out


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Add derived microstructure columns used across the analytics."""
    if df.empty:
        return df
    d = df.copy()
    mid = d["mid"]
    d["mid_prev"] = mid.shift(1)
    d["tick_dir"] = np.sign(mid.diff().fillna(0.0))
    d["price_change"] = mid.diff()
    # Simple quoted-size proxy: wider spread => thinner top of book.
    d["depth_proxy"] = 1.0 / d["spread"].replace(0, np.nan)
    d["interarrival_ms"] = d["ts"].diff().dt.total_seconds() * 1000.0

    if "volume" in d.columns and (d["volume"] > 0).any():
        d["signed_volume"] = d["volume"] * d["tick_dir"]
    else:
        d["signed_volume"] = 0.0
    return d
def return_distribution(df: pd.DataFrame, *, bins: int = 60) -> dict:
    """Histogram + higher moments of per-tick returns, with fat-tail check."""
    if df.empty or len(df) < 5:
        return {"histogram": [], "stats": {}}

    rets = df["mid"].pct_change().dropna() * 10_000  # basis points
    if rets.empty or float(rets.std()) == 0:
        return {"histogram": [], "stats": {}}

    counts, edges = np.histogram(rets.to_numpy(), bins=int(bins))
    centre = [(edges[i] + edges[i + 1]) / 2 for i in range(len(edges) - 1)]
    total = int(counts.sum()) or 1

    mu, sd = float(rets.mean()), float(rets.std())
    skew = float(((rets - mu) ** 3).mean() / sd**3) if sd > 0 else None
    kurt = float(((rets - mu) ** 4).mean() / sd**4) if sd > 0 else None

    stats = {
        "mean_bps": _f(mu),
        "std_bps": _f(sd),
        "min_bps": _f(rets.min()),
        "max_bps": _f(rets.max()),
        "median_bps": _f(rets.median()),
        "skew": _f(skew),
        "excess_kurtosis": _f(kurt - 3) if kurt is not None else None,
        "p01_bps": _f(rets.quantile(0.01)),
        "p99_bps": _f(rets.quantile(0.99)),
        # Jarque-Bera normality statistic (no p-value without scipy).
        "jarque_bera": _f(
            (len(rets) / 6) * (skew**2 + ((kurt - 3) ** 2) / 4) if skew is not None and kurt is not None else None
        ),
        "bullish_ratio": _f((rets > 0).sum() / total),
    }
    return {
        "histogram": [{"bps": _f(c), "count": int(n), "pct": _f(n / total * 100)} for c, n in zip(centre, counts)],
        "stats": stats,
    }


def drawdown(df: pd.DataFrame) -> dict:
    """Peak-to-trough drawdown path and the worst episodes."""
    if df.empty:
        return {"series": [], "max_drawdown_pct": None, "episodes": []}
    mid = df["mid"]
    peak = mid.cummax()
    dd = (mid / peak - 1) * 100
    episodes: list[dict] = []
    in_dd = dd < 0
    start = None
    for i, flag in enumerate(in_dd):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            episodes.append(_dd_episode(df, dd, start, i - 1))
            start = None
    if start is not None:
        episodes.append(_dd_episode(df, dd, start, len(dd) - 1))
    episodes.sort(key=lambda e: e["depth_pct"])
    step = max(1, len(df) // 1200)
    return {
        "series": [
            {"t": ts.isoformat(), "dd": _f(v)} for ts, v in zip(df["ts"], dd)
        ][::step],
        "max_drawdown_pct": _f(dd.min()),
        "episodes": episodes[:5],
    }


def behaviour(df: pd.DataFrame, *, lags: int = 10) -> dict:
    """Autocorrelation, efficiency ratio and Hurst exponent.

    These distinguish trending from mean-reverting microstructure.
    """
    if df.empty or len(df) < 30:
        return {"autocorrelation": [], "efficiency_ratio": None, "hurst": None, "variance_ratio": []}

    r = df["mid"].pct_change().dropna()
    if len(r) < 30 or float(r.std()) == 0:
        return {"autocorrelation": [], "efficiency_ratio": None, "hurst": None, "variance_ratio": []}

    rv = r.to_numpy()
    acf = [1.0] + [float(np.corrcoef(rv[:-k], rv[k:])[0, 1]) for k in range(1, min(lags, len(rv) // 3) + 1)]
    acf = [None if math.isnan(a) or math.isinf(a) else a for a in acf]

    net = float(df["mid"].iloc[-1] - df["mid"].iloc[0])
    path = float(np.abs(np.diff(df["mid"].to_numpy())).sum())
    er = abs(net) / path if path > 0 else None

    return {
        "autocorrelation": [{"lag": i, "acf": _f(a)} for i, a in enumerate(acf)],
        "efficiency_ratio": _f(er),
        "hurst": _hurst(rv),
        "variance_ratio": _variance_ratio(rv),
    }


def _hurst(x: np.ndarray) -> float | None:
    """Hurst via rescaled range of cumulative sums (<0.5 mean-reverting, >0.5 trending)."""
    n = len(x)
    if n < 40:
        return None
    cum = np.cumsum(x - x.mean())
    r = float(cum.max() - cum.min())
    s = float(x.std())
    return _f(r / (s * math.sqrt(n))) if s > 0 else None


def _variance_ratio(x: np.ndarray, max_lag: int = 20) -> list[dict]:
    """Lo-MacKinlay style variance ratios for multi-step returns."""
    var1 = float(x.var())
    if var1 <= 0 or len(x) < 60:
        return []
    out = []
    for lag in range(2, min(max_lag, len(x) // 5) + 1):
        agg = x[: len(x) // lag * lag].reshape(-1, lag).sum(axis=1)
        out.append({"lag": lag, "vr": _f(float(agg.var()) / (lag * var1))})
    return out


def hourly_profile(df: pd.DataFrame) -> dict:
    """Activity and directional bias by hour of day (UTC)."""
    if df.empty:
        return {"hourly": []}
    d = df.copy()
    d["hour"] = d["ts"].dt.hour
    out = []
    for hour, chunk in d.groupby("hour"):
        rets = chunk["mid"].pct_change().dropna()
        out.append({
            "hour": int(hour),
            "ticks": int(len(chunk)),
            "volume": _f(chunk["volume"].sum()),
            "mean_return_bps": _f(rets.mean() * 10_000 if len(rets) else None),
            "volatility_bps": _f(rets.std() * 10_000 if len(rets) > 1 else None),
            "avg_spread_bps": _f(chunk["spread_bps"].mean()),
        })
    return {"hourly": sorted(out, key=lambda r: r["hour"])}


def tick_table(df: pd.DataFrame, *, limit: int = 200) -> list[dict]:
    """Most recent raw ticks for the data table view."""
    if df.empty:
        return []
    cols = [c for c in ["ts", "bid", "ask", "mid", "volume", "spread_bps", "tick_dir"] if c in df.columns]
    tail = df[cols].tail(limit).copy()
    tail["ts"] = tail["ts"].dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return tail.astype(object).where(pd.notna(tail), None).to_dict("records")


def book_depth(df: pd.DataFrame, *, bins: int = 25) -> dict:
    """Depth-at-price and resting-size profile for state-snapshot sources.

    Present only when the reader produced depth columns (silver levels or the
    raw L2 replay).
    """
    if df is None or df.empty or "bid_depth" not in df.columns:
        return {"available": False}

    bid = pd.to_numeric(df.get("bid_depth"), errors="coerce")
    ask = pd.to_numeric(df.get("ask_depth"), errors="coerce")
    if bid.notna().sum() == 0 and ask.notna().sum() == 0:
        return {"available": False}

    total = bid.fillna(0.0) + ask.fillna(0.0)
    # Depth imbalance: share of resting size sitting on the bid.
    share = (bid.fillna(0.0) / total.replace(0, np.nan))
    ratio = (bid.fillna(0.0) - ask.fillna(0.0)) / total.replace(0, np.nan)

    hist = []
    if float(total.max() or 0) > 0:
        counts, edges = np.histogram(total.to_numpy(), bins=int(bins))
        for i, c in enumerate(counts):
            hist.append({"depth": float(edges[i]), "count": int(c)})

    return {
        "available": True,
        "avg_bid_depth": _f(bid.mean()),
        "avg_ask_depth": _f(ask.mean()),
        "max_bid_depth": _f(bid.max()),
        "max_ask_depth": _f(ask.max()),
        "avg_imbalance_share": _f(share.mean()),
        "depth_histogram": hist,
        "imbalance_series": [
            {"t": ts.isoformat(), "v": _f(v)} for ts, v in zip(df["ts"], ratio)
        ][-1200:],
        "avg_levels_bid": _f(df["levels_bid"].mean()) if "levels_bid" in df.columns else None,
        "avg_levels_ask": _f(df["levels_ask"].mean()) if "levels_ask" in df.columns else None,
    }


def build_report(df: pd.DataFrame, *, timeframe: str = "1m", window: int = 50, bins: int = 60) -> dict:
    """One-stop bundle of every analytics block for the dashboard."""
    d = enrich(prepare(df))
    return clean({
        "summary": summary(d),
        "ohlcv": ohlcv(d, timeframe),
        "microstructure": microstructure(d),
        "volume_profile": volume_profile(d, bins=bins),
        "rolling": rolling_metrics(d, window=window),
        "distribution": return_distribution(d, bins=min(bins, 40)),
        "drawdown": drawdown(d),
        "behaviour": behaviour(d),
        "hourly": hourly_profile(d),
        "depth": book_depth(d),
        "ticks": tick_table(d, limit=250),
        "timeframes": list(TIMEFRAMES.keys()),
    })


def _dd_episode(df: pd.DataFrame, dd: pd.Series, lo: int, hi: int) -> dict:
    worst = dd.iloc[lo:hi + 1]
    trough = int(worst.idxmin())
    return {
        "peak_ts": df["ts"].iloc[lo].isoformat(),
        "trough_ts": df["ts"].iloc[trough].isoformat(),
        "recovered_ts": df["ts"].iloc[hi].isoformat() if hi + 1 < len(df) else None,
        "depth_pct": _f(worst.min()),
        "duration_ticks": int(hi - lo + 1),
    }