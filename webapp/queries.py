def symbols(table):
    return f"""
{table}
| where timestamp > ago(1d)
| distinct symbolId
| order by symbolId asc
"""


def series(table, symbol_id, minutes, bin_seconds):
    # Last state in each time bucket, plus how many events arrived in it.
    return f"""
{table}
| where symbolId == '{symbol_id}' and timestamp > ago({minutes}m)
| summarize
    arg_max(timestamp, best_bid, best_ask, rel_spread, rel_vwap_spread),
    events = count()
    by bucket = bin(timestamp, {bin_seconds}s)
| order by bucket asc
"""


def latest_book(table, symbol_id):
    # Returned as strings; app.py parses the JSON.
    return f"""
{table}
| where symbolId == '{symbol_id}'
| top 1 by timestamp desc
| project timestamp, best_bid, best_ask, bids = tostring(bids), asks = tostring(asks)
"""

def activity(table, symbol_id, days):
    # Event count per hour of day (UTC) over the last `days` days.
    return f"""
{table}
| where symbolId == '{symbol_id}' and timestamp > ago({days}d)
| summarize events = count() by hour = hourofday(timestamp)
| order by hour asc
"""


def tick_times(table, symbol_id, minutes):
    # One row per millisecond with its event count, so gaps can be measured
    # between distinct timestamps.
    return f"""
{table}
| where symbolId == '{symbol_id}' and timestamp > ago({minutes}m)
| summarize events = count() by epoch_ms = datetime_diff('millisecond', timestamp, datetime(1970-01-01))
| order by epoch_ms asc
"""


def vap_ticks(table, symbol_id, minutes):
    # One scan for every volume-at-price window: fetch the widest window's
    # buckets once (best_bid/best_ask per bucket plus its event count) and slice
    # each lookback from it in Python, so 1h/2h/3h/4h cost one Kusto read.
    #
    # VOLUME_AT_PRICE is a marker so a test reader can tell this query apart from
    # `series()` - the two share the `by bucket = bin(...)` shape.
    #
    # `volume` is projected as `volume` when the table carries a traded size and
    # as `0` when it does not - the ladder then falls back to event count in
    # analytics._level_weight. Kept as one column name so the shape is stable.
    return f"""
{table}
| where symbolId == '{symbol_id}' and timestamp > ago({minutes}m)
| summarize
    arg_max(timestamp, best_bid, best_ask),
    events = count()
    by bucket = bin(timestamp, 10s)
| order by bucket asc
"""
