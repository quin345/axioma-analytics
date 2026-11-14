def _where(symbol_id, account=None):
    """The instrument predicate every per-symbol query starts with.

    A `symbolId` alone is shared across brokers - 114 is BTCUSD on one account
    and EURCHF on another - so when the caller knows the trader account it is
    added to the filter. `ctidTraderAccountId` is globally unique in cTrader,
    which makes (account, symbolId) a complete key without the broker spelling
    (the feed and the catalogue disagree on that; see app.BROKER_ALIASES).

    Both values are validated as digits by the route before they get here.
    """
    clause = f"symbolId == '{symbol_id}'"
    if account:
        clause += f" and ctidTraderAccountId == '{account}'"
    return clause


# How far back an instrument may have last ticked and still be listed. A week,
# so a market that is shut for the weekend (gold, FX) keeps its place in the
# selector and its last session stays on screen rather than vanishing.
SYMBOL_LOOKBACK = "7d"
# How far back the window anchor looks for an instrument's newest tick.
ANCHOR_LOOKBACK = "30d"


def _window(table, symbol_id, account, minutes):
    """The rows of one instrument's trailing `minutes`, ending at its newest tick.

    Anchoring on the instrument's own last update rather than on `now()` keeps
    a closed market analysable: over a weekend a 4 h window still covers the
    last 4 h of trading instead of 4 h of silence. During a session the two
    are the same thing to within a tick.
    """
    where = _where(symbol_id, account)
    return f"""let _end = toscalar({table}
    | where {where} and timestamp > ago({ANCHOR_LOOKBACK})
    | summarize max(timestamp));
{table}
| where {where} and timestamp > _end - {minutes}m and timestamp <= _end"""


def symbols(table):
    # The feed carries the same triple the metadata catalogue is keyed on -
    # (broker, ctidTraderAccountId, symbolId) - and a symbolId alone is shared
    # across brokers, so all three are projected and the caller joins on all
    # three. Collapsing to `distinct symbolId` here would reintroduce exactly
    # the cross-broker ambiguity this query exists to avoid.
    return f"""
{table}
| where timestamp > ago({SYMBOL_LOOKBACK})
| summarize events = count(), last_seen = max(timestamp) by broker, ctidTraderAccountId, symbolId
| order by broker asc, ctidTraderAccountId asc, symbolId asc
"""


def series(table, symbol_id, minutes, bin_seconds, account=None):
    # Last state in each time bucket, plus how many events arrived in it.
    # `imbalance_ratio` rides along so the book-pressure reading in
    # analytics.order_flow needs no second scan.
    return f"""
{_window(table, symbol_id, account, minutes)}
| summarize
    arg_max(timestamp, best_bid, best_ask, rel_spread, rel_vwap_spread),
    imbalance_ratio = avg(imbalance_ratio),
    events = count()
    by bucket = bin(timestamp, {bin_seconds}s)
| order by bucket asc
"""


def latest_book(table, symbol_id, account=None):
    # Returned as strings; app.py parses the JSON. Only two-sided snapshots:
    # when a market closes the feed's final update is an empty book (null
    # best bid/ask), and the last real ladder is the meaningful one to show.
    return f"""
{table}
| where {_where(symbol_id, account)} and timestamp > ago({ANCHOR_LOOKBACK})
    and isnotnull(best_bid) and isnotnull(best_ask)
| top 1 by timestamp desc
| project timestamp, best_bid, best_ask, total_bid, total_ask,
          bids = tostring(bids), asks = tostring(asks)
"""


def activity(table, symbol_id, days, account=None):
    # Event count per hour of day (UTC) over the instrument's last `days` days.
    return f"""
{_window(table, symbol_id, account, days * 1440)}
| summarize events = count() by hour = hourofday(timestamp)
| order by hour asc
"""


def activity_days(table, symbol_id, days, account=None):
    # Event count per UTC calendar day - the activity-calendar heat map. The
    # lower bound also drops the feed's epoch-zero rows (1970-01-01).
    return f"""
{table}
| where {_where(symbol_id, account)} and timestamp > ago({days}d)
| summarize events = count() by day = startofday(timestamp)
| order by day asc
"""


def tick_times(table, symbol_id, minutes, account=None):
    # One row per millisecond with its event count, so gaps can be measured
    # between distinct timestamps.
    return f"""
{_window(table, symbol_id, account, minutes)}
| summarize events = count() by epoch_ms = datetime_diff('millisecond', timestamp, datetime(1970-01-01))
| order by epoch_ms asc
"""


def recent_ticks(table, symbol_id, limit, account=None):
    # The newest raw book updates, newest first - the raw-tick panel. The feed
    # has no trade prints, so these are depth events; their side is inferred
    # from the mid move in analytics.raw_ticks. One extra row is fetched so the
    # oldest shown update has a predecessor to be signed against.
    return f"""
{table}
| where {_where(symbol_id, account)} and timestamp > ago({ANCHOR_LOOKBACK})
| top {limit + 1} by timestamp desc
| project timestamp, eventSeq, best_bid, best_ask, total_bid, total_ask
"""


def vap_ticks(table, symbol_id, minutes, account=None):
    # One scan for every volume-at-price window: fetch the widest window's
    # buckets once (best_bid/best_ask per bucket plus its event count) and slice
    # each lookback from it in Python, so 1h/2h/3h/4h cost one Kusto read.
    #
    # VOLUME_AT_PRICE is a marker so a test reader can tell this query apart from
    # `series()` - the two share the `by bucket = bin(...)` shape.
    #
    # There is no traded size in the feed, so the ladder weights each bucket by
    # its event count (analytics._level_weight's fallback).
    return f"""// VOLUME_AT_PRICE
{_window(table, symbol_id, account, minutes)}
| summarize
    arg_max(timestamp, best_bid, best_ask),
    events = count()
    by bucket = bin(timestamp, 10s)
| order by bucket asc
"""
