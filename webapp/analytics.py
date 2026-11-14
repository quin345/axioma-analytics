"""Calculations on series rows. Pure Python: no Flask, no Kusto, so they are easy to test."""
import math
from statistics import median

SECONDS_PER_YEAR = 365 * 24 * 3600
ACF_BAND_Z = 1.96
# Gap bins in milliseconds, lower bound inclusive. The last bin is open-ended.
GAP_EDGES_MS = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000]

# --- Volume-at-price ---------------------------------------------------------
# Share of total volume the value area must cover, expanding outwards from the
# point of control. Same 70% rule the tick frame and metrics_5m used.
VALUE_AREA_SHARE = 0.70
# How many high-volume nodes a profile reports.
HIGH_VOLUME_NODES = 5
# Default price-bin count for one ladder. Fixed (not range-scaled) so a 1 h and
# a 4 h profile of the same symbol are binned on the same grid and stay
# comparable stop to stop.
VAP_BINS = 60


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


# --- Trade-flow proxies ------------------------------------------------------
#
# The feed is depth-of-market only: no trade prints, no aggressor side, and the
# ladder's slot sizes are fixed, so the book's own `imbalance_ratio` sits at ~0.
# The cards that need trades are filled with the closest proxy the book
# supports, all built on the **tick rule** (Lee & Ready): an update that lifts
# the mid is buyer-initiated, one that drops it is seller-initiated, and an
# unchanged mid inherits the last non-zero direction. Each payload names its
# proxy (`proxy`) so the UI can say so.

# Event-study horizons for the burst-impact curve, in 10-second buckets:
# 10s, 30s, 1m, 3m, 5m after the burst.
IMPACT_HORIZONS = (1, 3, 6, 18, 30)
# A bucket is a "burst" (the large-trade stand-in) when its event count is at
# or above this quantile of the window's buckets.
BURST_QUANTILE = 0.95


def _tick_signs(mids):
    """Tick-rule direction (+1 / -1 / 0) for each mid against the one before.

    Zero ticks carry the previous non-zero sign; the first observation (and
    any zero tick before the first move) is 0, i.e. unsigned.
    """
    signs = []
    last = 0
    for prev, cur in zip([None] + mids[:-1], mids):
        if prev is not None and cur != prev:
            last = 1 if cur > prev else -1
        signs.append(last)
    return signs


def order_flow(rows):
    """Signed-flow proxy per bucket: event count signed by the tick rule.

    `rows` are series buckets (oldest first). Returns the per-bucket signed
    flow, its running sum, the book's mean `imbalance_ratio` for reference, and
    the window's buy/sell split with a normalised imbalance in [-1, +1].
    """
    mids = _valid_mids(rows)
    by_bucket = {r["bucket"]: r for r in rows}
    signs = _tick_signs([m for _, m, _ in mids])

    series = []
    buy = sell = cumulative = 0
    for (bucket, _, _), sign in zip(mids, signs):
        events = by_bucket[bucket]["events"] or 0
        signed = sign * events
        cumulative += signed
        if sign > 0:
            buy += events
        elif sign < 0:
            sell += events
        series.append({"bucket": bucket, "signed": signed, "cumulative": cumulative})

    total = buy + sell
    book = [r.get("imbalance_ratio") for r in rows if r.get("imbalance_ratio") is not None]
    return {
        "proxy": "tick-rule signed depth events",
        "series": series,
        "buy_events": buy,
        "sell_events": sell,
        "imbalance": (buy - sell) / total if total else None,
        "book_imbalance": sum(book) / len(book) if book else None,
    }


def _quantile(values, q):
    """Nearest-rank quantile; keeps numpy out for one number."""
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(math.ceil(q * len(ordered))) - 1))
    return ordered[idx]


def burst_impact(rows, horizons=IMPACT_HORIZONS, quantile=BURST_QUANTILE,
                 bin_seconds=10, recent=8):
    """Large-trade-impact proxy: the mid's response after activity bursts.

    A burst is a bucket whose event count is in the window's top `quantile`;
    its direction is the tick-rule sign of the mid move *into* that bucket.
    For each horizon h the impact is the signed mid change from just before the
    burst to h buckets after it, in basis points of the pre-burst mid -
    positive means the price kept going the burst's way (permanent impact),
    negative means it reverted.
    """
    mids = _valid_mids(rows)
    empty = {"proxy": "top-activity buckets, tick-rule signed", "threshold": None,
             "bursts": 0, "curve": [], "events": []}
    if len(mids) < 3:
        return empty

    by_bucket = {r["bucket"]: r["events"] or 0 for r in rows}
    counts = [by_bucket[b] for b, _, _ in mids]
    threshold = _quantile(counts, quantile)
    if threshold <= 0:
        return empty

    prices = [m for _, m, _ in mids]
    samples = {h: [] for h in horizons}
    headline = horizons[min(2, len(horizons) - 1)]
    events = []
    for i in range(1, len(mids)):
        if counts[i] < threshold:
            continue
        move = prices[i] - prices[i - 1]
        base = prices[i - 1]
        if move == 0 or not base:
            continue                      # no direction to attribute
        sign = 1 if move > 0 else -1
        impact = None
        for h in horizons:
            j = i + h
            if j < len(prices):
                bps = sign * (prices[j] - base) / base * 10_000
                samples[h].append(bps)
                if h == headline:
                    impact = bps
        events.append({"bucket": mids[i][0], "events": counts[i], "direction": sign,
                       "move_bps": abs(move) / base * 10_000, "impact_bps": impact})

    curve = [{"horizon_s": h * bin_seconds,
              "mean_bps": sum(v) / len(v) if v else None,
              "n": len(v)} for h, v in samples.items()]
    return {**empty, "threshold": threshold, "bursts": len(events), "curve": curve,
            "headline_horizon_s": headline * bin_seconds,
            "events": events[-recent:][::-1]}


def raw_ticks(rows, price_scale):
    """The newest raw book updates with a tick-rule side, newest first.

    `rows` arrive newest first, so each row's direction is measured against the
    row after it (the next older update). Prices are scaled to display units.
    """
    out = []
    for i, row in enumerate(rows):
        mid = _mid(row)
        older = _mid(rows[i + 1]) if i + 1 < len(rows) else None
        side = None
        if mid is not None and older is not None and mid != older:
            side = "buy" if mid > older else "sell"
        bid, ask = row.get("best_bid"), row.get("best_ask")
        out.append({
            "timestamp": row.get("timestamp"),
            "seq": row.get("eventSeq"),
            "bid": None if bid is None else bid / price_scale,
            "ask": None if ask is None else ask / price_scale,
            "spread": None if bid is None or ask is None else (ask - bid) / price_scale,
            "bid_size": row.get("total_bid"),
            "ask_size": row.get("total_ask"),
            "side": side,
        })
    return out


# --- Volume-at-price ---------------------------------------------------------


def _mid(row):
    """The mid price of a tick row, or None when one side of the book is missing."""
    bid, ask = row.get("best_bid"), row.get("best_ask")
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2


def _level_weight(row):
    """The volume a tick contributes to its price bin.

    `volume` when the feed carries one (traded size), otherwise the event count
    - the same fallback `metrics_5m` used when the DOM feed had no trades. Never
    negative, so a bad column cannot invert a ladder.
    """
    volume = row.get("volume")
    if volume is None:
        return max(0.0, float(row.get("events") or 0))
    try:
        value = float(volume)
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0 else 0.0


def value_area(profile, poc_index):
    """The 70% value area around the point of control, as (low, high) prices.

    Greedy expansion from the POC outwards: at each step the busier of the two
    neighbouring bins is absorbed, until `VALUE_AREA_SHARE` of the volume is
    covered or the ladder runs out. `profile` must be in **ascending** price
    order, so adjacency means anything.
    """
    if not profile or not 0 <= poc_index < len(profile):
        return None
    vols = [float(p["volume"]) for p in profile]
    total = float(sum(vols))
    if total <= 0:
        return None

    lo_i = hi_i = poc_index
    acc = vols[poc_index]
    while acc < VALUE_AREA_SHARE * total and (lo_i > 0 or hi_i < len(vols) - 1):
        down = vols[lo_i - 1] if lo_i > 0 else -1.0
        up = vols[hi_i + 1] if hi_i < len(vols) - 1 else -1.0
        if up >= down:
            hi_i += 1
            acc += up
        else:
            lo_i -= 1
            acc += down
    return profile[lo_i]["price"], profile[hi_i]["price"]


def _high_volume_nodes(profile, top=HIGH_VOLUME_NODES):
    """Local maxima in the profile - candidate support/resistance levels."""
    vols = [p["volume"] for p in profile]
    if not vols:
        return []
    ordered = sorted(vols)
    # 80th percentile by nearest-rank; avoids pulling in numpy for one number.
    idx = min(len(ordered) - 1, int(round(0.80 * (len(ordered) - 1))))
    cutoff = ordered[idx]
    nodes = [
        profile[i]
        for i in range(1, len(profile) - 1)
        if vols[i] >= cutoff and vols[i] >= vols[i - 1] and vols[i] >= vols[i + 1]
    ]
    nodes.sort(key=lambda n: n["volume"], reverse=True)
    return nodes[:top]


def volume_at_price(rows, price_scale, bins=VAP_BINS):
    """Volume-at-price ladder for one window of tick rows.

    Bins the mid price into `bins` equal buckets between the window's own low
    and high, sums each bucket's weight, and reports the ladder, its point of
    control and its value area. Because the grid is derived from the rows given,
    a shorter window is binned over its own (narrower) range - which is what
    makes each lookback carry a different POC and a different ladder.

    `price_scale` divides the raw integer prices back to display units, so the
    returned prices match `/api/series` and the UI's `PRICE_DECIMALS`.
    """
    points = []
    for row in rows:
        mid = _mid(row)
        if mid is None:
            continue
        points.append((mid / price_scale, _level_weight(row)))

    if not points:
        return {"bins": [], "poc": None, "value_area": None,
                "high_volume_nodes": [], "total_volume": 0.0}

    prices = [p for p, _ in points]
    lo, hi = min(prices), max(prices)
    width = hi - lo

    # A window where every mid is identical has no range to bin: one level.
    if width <= 0:
        total = sum(w for _, w in points)
        level = {"price": lo, "volume": total, "ticks": len(points)}
        return {"bins": [level], "poc": lo, "value_area": [lo, lo],
                "high_volume_nodes": [], "total_volume": total}

    n_bins = max(1, int(bins))
    step = width / n_bins
    vol = [0.0] * n_bins
    cnt = [0] * n_bins
    for price, weight in points:
        idx = int((price - lo) / step)
        if idx >= n_bins:          # the top edge belongs to the last bin
            idx = n_bins - 1
        vol[idx] += weight
        cnt[idx] += 1

    # Bin label is the bucket midpoint, ascending.
    profile = [
        {"price": lo + (i + 0.5) * step, "volume": vol[i], "ticks": cnt[i]}
        for i in range(n_bins)
    ]

    total = sum(vol)
    poc_index = max(range(n_bins), key=lambda i: vol[i])
    area = value_area(profile, poc_index) if total > 0 else None

    return {
        "bins": profile,
        "poc": profile[poc_index]["price"],
        "value_area": [area[0], area[1]] if area else None,
        "high_volume_nodes": _high_volume_nodes(profile),
        "total_volume": total,
    }


def profiles_by_window(rows, price_scale, windows, bins=VAP_BINS):
    """One volume-at-price profile per lookback window.

    `rows` is a single tick frame (oldest first) spanning at least the widest
    window, so one Kusto scan serves every window. Each window is sliced as the
    trailing `window` minutes of the frame and binned on its own range, so the
    ladders - and their points of control - genuinely differ per lookback
    instead of sharing one profile.

    Returns `{window_minutes: profile}`.
    """
    out = {}
    for window in windows:
        window = int(window)
        sliced = _trailing_minutes(rows, window)
        out[window] = volume_at_price(sliced, price_scale, bins)
    return out


def _trailing_minutes(rows, minutes):
    """The tail of `rows` whose stamp is within `minutes` of the newest one.

    Stamps are ISO-8601 strings (as `kusto_client` hands them back) or epoch
    milliseconds, so both are accepted. Rows without a usable stamp are kept:
    dropping them would silently bin a shorter window than asked for.
    """
    stamps = [s for s in (_stamp(row) for row in rows) if s is not None]
    if not stamps:
        return list(rows)

    cutoff = max(stamps) - minutes * 60_000
    return [row for row in rows if (_stamp(row) or cutoff) >= cutoff]


def _stamp(row):
    """A row's time as epoch milliseconds, or None when it carries no usable stamp."""
    value = row.get("epoch_ms")
    if isinstance(value, (int, float)):
        return float(value)

    raw = row.get("bucket") or row.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return _fromisoformat(raw).timestamp() * 1000
    except ValueError:
        return None


def _fromisoformat(raw):
    from datetime import datetime

    return datetime.fromisoformat(raw.replace("Z", "+00:00"))
