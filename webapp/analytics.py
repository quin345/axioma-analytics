"""Calculations on series rows. Pure Python: no Flask, no Kusto, so they are easy to test."""
import math
from statistics import median

SECONDS_PER_YEAR = 365 * 24 * 3600
ACF_BAND_Z = 1.96
# Gap bins in milliseconds, lower bound inclusive. The last bin is open-ended.
GAP_EDGES_MS = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000]


def _valid_mids(rows):
    """(bucket, mid, absolute spread) for every bucket where both sides of the book exist."""
    out = []
    for r in rows:
        if r["best_bid"] is not None and r["best_ask"] is not None:
            out.append((r["bucket"], (r["best_bid"] + r["best_ask"]) / 2, r["best_ask"] - r["best_bid"]))
    return out


def _log_returns(mids):
    return [math.log(b[1] / a[1]) for a, b in zip(mids, mids[1:])]


def _stdev(xs):
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def kpis(rows, bin_seconds):
    ticks = sum(r["events"] for r in rows)
    mids = _valid_mids(rows)
    if not mids:
        return {"ticks": ticks, "last_mid": None, "change_abs": None, "change_pct": None,
                "high": None, "low": None, "avg_spread": None, "realised_vol_pct": None}

    prices = [m[1] for m in mids]
    first, last = prices[0], prices[-1]
    sd = _stdev(_log_returns(mids))
    return {
        "ticks": ticks,
        "last_mid": last,
        "change_abs": last - first,
        "change_pct": (last - first) / first * 100 if first else None,
        "high": max(prices),
        "low": min(prices),
        "avg_spread": sum(m[2] for m in mids) / len(mids),
        "realised_vol_pct": sd * math.sqrt(SECONDS_PER_YEAR / bin_seconds) * 100 if sd is not None else None,
    }


def spread(rows):
    points = [{"bucket": b, "spread": s, "rel_spread": s / m} for b, m, s in _valid_mids(rows)]
    if not points:
        return {"series": [], "mean": None, "median": None, "min": None, "max": None}
    spreads = [p["spread"] for p in points]
    return {
        "series": points,
        "mean": sum(spreads) / len(spreads),
        "median": median(spreads),
        "min": min(spreads),
        "max": max(spreads),
    }


def rolling_volatility(rows, bin_seconds, window):
    """Annualised volatility (percent) over the last `window` returns, one value per bucket."""
    mids = _valid_mids(rows)
    rets = _log_returns(mids)
    scale = math.sqrt(SECONDS_PER_YEAR / bin_seconds) * 100
    out = []
    for i in range(window, len(rets) + 1):
        sd = _stdev(rets[i - window:i])
        # rets[k] is the return into mids[k + 1], so this window ends at mids[i].
        out.append({"bucket": mids[i][0], "vol_pct": sd * scale})
    return out


def autocorrelation(rows, max_lag):
    """Autocorrelation of log returns. `band` is the approximate 95% significance bound."""
    rets = _log_returns(_valid_mids(rows))
    n = len(rets)
    if n < 2:
        return {"n": n, "band": None, "lags": []}

    m = sum(rets) / n
    d = [x - m for x in rets]
    denom = sum(x * x for x in d)
    lags = []
    for k in range(1, min(max_lag, n - 1) + 1):
        num = sum(d[t] * d[t + k] for t in range(n - k))
        lags.append({"lag": k, "acf": num / denom if denom else None})
    return {"n": n, "band": ACF_BAND_Z / math.sqrt(n), "lags": lags}


def tick_gaps(rows):
    """
    rows: [{epoch_ms, events}] sorted by epoch_ms, one row per millisecond.
    Gaps are measured between distinct timestamps. `shared_timestamp_share` is the share
    of events that reuse the previous event's timestamp.
    """
    stamps = [r["epoch_ms"] for r in rows]
    total = sum(r["events"] for r in rows)
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]

    bins = []
    bounds = list(zip(GAP_EDGES_MS, GAP_EDGES_MS[1:] + [None]))
    for lo, hi in bounds:
        count = sum(1 for g in gaps if g >= lo and (hi is None or g < hi))
        bins.append({"from_ms": lo, "to_ms": hi, "count": count})

    return {
        "events": total,
        "distinct_timestamps": len(rows),
        "shared_timestamp_share": (total - len(rows)) / total if total else None,
        "median_gap_ms": median(gaps) if gaps else None,
        "bins": bins,
    }
