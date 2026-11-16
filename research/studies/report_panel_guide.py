"""'Panel guide' section of the /research/ report: what each terminal panel measures and how to read it.

Content mirrors the terminal panels in terminal/src/panels/ and the calculations in webapp/analytics.py.
"""
import html

# (anchor, title, group, shows, method, use, caveat)
PANELS = [
    ("pg-price", "Price + volume", "Price",
     "Mid price over time. Line view by default; candles (OHLC of bucket mids) and an optional volume-profile overlay (POC and value area).",
     "Mid = (best bid + best ask) / 2 per 10-second bucket. The overlay is the recorded volume-at-price profile for a 1-4 hour lookback.",
     "Trend and range reading; candle structure; seeing where activity clustered relative to price.",
     "Candles and highs/lows are built from mids, not executed trades."),
    ("pg-vap", "Volume at price", "Price",
     "Horizontal ladder of price bins; bar length is volume; POC highlighted.",
     "Mid of each row, weighted by traded volume if present, otherwise by book-event count. The window's low-high range is split into 60 equal bins. POC = busiest bin. Value area = 70% of volume grown outwards from the POC. High-volume nodes = local maxima at or above the 80th percentile.",
     "Fair-value zones and support/resistance; thin areas where price can move fast; comparing POC across lookbacks.",
     "A quiet window gets a narrow grid, which exaggerates small moves. Event-count weights measure book activity, not size."),
    ("pg-spread", "Bid-ask spread", "Liquidity",
     "Quoted spread over time in basis points of mid, with the series average in the header.",
     "Per valid bucket: spread = ask - bid; relative spread = spread / mid, shown in bps.",
     "Execution cost; spread widening around news or quiet hours; comparing instruments on a relative basis.",
     "Absolute spreads depend on price, so use bps when comparing instruments."),
    ("pg-depth", "Book depth", "Liquidity",
     "Mirrored cumulative depth from the newest two-sided snapshot. Bids to the left, asks to the right. Header shows total bid and ask size.",
     "The feed's own bid and ask ladder, summed level by level from the touch outwards.",
     "Where resting liquidity sits and how far price must move to consume it; a rough bid/ask balance at one moment.",
     "A single snapshot, not history. Resting size is a quote, not a commitment, and can be pulled before it trades."),
    ("pg-ofi", "Order-flow imbalance", "Flow (proxy)",
     "Per-minute signed flow bars plus a running total line. Header shows the imbalance; footnote gives buy and sell counts.",
     "Each 10-second bucket gets a tick-rule sign (mid up = buy, mid down = sell, unchanged keeps the last sign). Event counts are multiplied by the sign and summed per minute. Imbalance = (buy - sell) / (buy + sell), between -1 and +1.",
     "Whether activity leans up or down over time; divergence, such as price rising while cumulative flow falls.",
     "Proxy. It measures how often the mid moved and in which direction, weighted by book updates. It does not measure executed buying or selling. The book's own imbalance ratio is near zero in this feed."),
    ("pg-impact", "Large-trade impact", "Flow (proxy)",
     "Average signed mid response at +10s, +30s, +1m, +3m and +5m after an activity burst. Headline uses +1m. A table lists recent bursts.",
     "A burst is a 10-second bucket in the window's top 5% by event count. Its direction is the tick-rule sign of the mid move into it. Impact at each horizon = sign x (mid at horizon - pre-burst mid) / pre-burst mid, in bps. The curve is the mean across bursts.",
     "Testing whether bursts persist (positive) or revert (negative); judging whether a short-term move is likely to hold.",
     "No trade sizes exist, so a 'large trade' is only a burst of book updates. Check the burst count before trusting the curve."),
    ("pg-vol", "Rolling volatility", "Volatility",
     "Annualised volatility recomputed every bucket over a trailing 30-minute window. Headline shows the latest value and the full-window figure.",
     "Standard deviation of the last 180 ten-second log returns, scaled by sqrt(seconds per year / 10) x 100.",
     "Spotting volatility regime changes; sizing positions and stops to current volatility.",
     "The line needs more than 30 minutes of quotes. The calendar-year convention (365 days) is not a market-hours figure."),
    ("pg-acf", "Price-behaviour autocorrelation", "Volatility",
     "Autocorrelation of 10-second mid log returns at lags 1 to 30, with a 95% white-noise band. Bars outside the band are significant. Headline is lag 1.",
     "Sample autocorrelation of demeaned returns. Band = plus or minus 1.96 / sqrt(n).",
     "Choosing between mean reversion and momentum at a given horizon; separating real signal from bid-ask bounce.",
     "Negative lag 1 is often bid-ask bounce, not information. With few returns the band is wide, so check n."),
    ("pg-gaps", "Tick inter-arrival", "Feed timing",
     "Histogram of gaps between distinct event timestamps (ms). The median's bin is highlighted. Footnote gives event count, distinct timestamps and the share of events sharing a timestamp.",
     "Log-spaced bins at 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000 and 5000 ms, plus an open-ended final bin. Shared-timestamp share = (events - distinct timestamps) / events.",
     "How fast and how bursty the feed is; latency expectations for anything consuming it.",
     "A high shared-timestamp share suggests batched venue updates."),
    ("pg-raw", "Raw tick data", "Feed timing",
     "Newest book updates, newest first. Columns: time (UTC), sequence, bid, ask, spread, bid and ask size, side.",
     "Each row is one book update. Side uses the tick rule on the mid: BUY if the mid rose, SELL if it fell, dash if unchanged.",
     "Auditing the data behind other panels; checking quote changes at a specific time.",
     "BUY and SELL come from mid direction, not from trades."),
    ("pg-hour", "Activity by hour", "Activity",
     "24 bars, one per UTC hour, showing average book events per hour over the last 7 days. Current hour highlighted; header shows the peak.",
     "Events per hour summed across the 7-day window, divided by 7.",
     "Finding the most active hours; planning when to expect higher spreads and volatility.",
     "UTC hours, not local market hours."),
    ("pg-day", "Activity by day", "Activity",
     "Monthly calendar shaded by book events per UTC day over the last six weeks. Clicking a day shows its count; the header shows the month total.",
     "Events per UTC day, shaded relative to the busiest day in the data.",
     "Spotting weekly patterns and unusual days; checking whether a quiet period elsewhere is a one-off.",
     "Shading is relative to the busiest day, so compare months with care."),
    ("pg-coverage", "Instrument coverage", "Universe",
     "Asset classes in the collected universe and how many instruments fall in each.",
     "Counts come from the broker and symbol mapping snapshotted from the Fabric ctrader_metadata source, joined to the instruments the feed has.",
     "Confirming which markets are covered and that a new instrument is mapped and receiving data.",
     "Reflects the mapping snapshot, so it can lag the live feed."),
]

GROUP_ORDER = ["Price", "Liquidity", "Flow (proxy)", "Volatility", "Feed timing", "Activity", "Universe"]


def _card(anchor, title, shows, method, use, caveat):
    e = html.escape
    return (
        f"<article class='pg-card' id='{anchor}'>"
        f"<h4>{e(title)}</h4>"
        f"<dl>"
        f"<dt>What it shows</dt><dd>{e(shows)}</dd>"
        f"<dt>Method</dt><dd>{e(method)}</dd>"
        f"<dt>Use it for</dt><dd>{e(use)}</dd>"
        f"<dt>Caution</dt><dd>{e(caveat)}</dd>"
        f"</dl></article>"
    )


def panel_guide():
    groups = []
    for g in GROUP_ORDER:
        cards = "".join(_card(a, t, s, m, u, c) for a, t, grp, s, m, u, c in PANELS if grp == g)
        if cards:
            groups.append(f"<h3 id='pg-{g.split()[0].lower()}'>{html.escape(g)}</h3><div class='pg-grid'>{cards}</div>")
    return f"""
<section class="block" id="panel-guide">
  <div class="eyebrow">08 · Reference</div><h2>Panel guide</h2>
  <p>What each panel in the AXIOMA terminal measures, how it is calculated, and when to use it.
  The feed carries <b>order book updates only</b>: no trade prints, no trade sizes and no aggressor side.
  Panels marked <b>proxy</b> infer flow from book activity using the tick rule (Lee and Ready: a mid move up is
  treated as a buy, down as a sell). Read their numbers as indicators, not executed trades.</p>
  <aside class="callout"><b>Common definitions.</b><p>Bucket = 10 seconds. Mid = (best bid + best ask) / 2.
  "Volume" falls back to book-event count where no traded size exists.</p></aside>
  {''.join(groups)}
</section>"""


TOC_ENTRY = ("panel-guide", "Panel guide", False)
