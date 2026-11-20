"""Record every metric the terminal computes in a 5-minute research table.

One row per instrument per 5-minute slot. Each row carries the instrument's
identity, the slot's microstructure (the same `analytics` functions behind
`/api/kpis`, `/api/spread`, `/api/acf`, `/api/order-flow`, `/api/impact` and
`/api/tick-gaps`, applied to that slot's 10-second buckets) and the
volume-at-price summary for each lookback the VAP card shows (1h/2h/3h/4h,
ending at the slot's close).

Time convention (UTC throughout): a slot is the half-open interval
`[slot_start, slot_end)` and a row is keyed on `slot_end`, so a row only uses
data that was available at `slot_end` - no look-ahead.

Storage: the `metrics_5m` table in the local SQLite database (`DB_PATH`,
default `webapp/data/axioma.db`), next to `vap_run`. Writes are upserts on the
primary key `(ctid_trader_account, symbol_id, slot_end)`, so re-running a slot
or overlapping a backfill replaces rows instead of duplicating them.

Usage (see docs/flat-export.md):

    python export_flat.py                      # the last 2 completed slots (cron)
    python export_flat.py --start 2026-11-10 --end 2026-11-15   # backfill
    python export_flat.py --list               # what is on record, no Kusto
    python export_flat.py --coverage           # how much history the feed holds
"""
import argparse
import math
import sys
import time
from datetime import datetime, timedelta, timezone

import analytics
import db
import queries
from config import load_config

SLOT_MINUTES = 5
BIN_SECONDS = 10                    # the terminal's bucket width (app.BIN_SECONDS)
VAP_WINDOWS = (60, 120, 180, 240)   # the VAP card's lookbacks (app.WINDOWS)
ACF_LAGS = 3
DEFAULT_RECENT_SLOTS = 2            # re-export the previous slot too, to catch late ticks
DEFAULT_CHUNK_MINUTES = 60
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

IDENTITY_COLUMNS = [
    "slot_start", "slot_end", "broker", "ctid_trader_account", "symbol_id",
    "symbol_name", "standardized_name", "asset_class",
]
SLOT_COLUMNS = [
    "has_data", "events", "book_buckets",
    "mid_open", "mid_high", "mid_low", "mid_close", "log_return", "change_pct",
    "spread_mean", "spread_median", "spread_min", "spread_max", "rel_spread_mean",
    "realised_vol_pct",
    *[f"acf_lag{k}" for k in range(1, ACF_LAGS + 1)], "acf_band",
    "flow_buy_events", "flow_sell_events", "flow_imbalance", "book_imbalance",
    "burst_threshold", "burst_count", "burst_impact_60s_bps",
    "tick_distinct_timestamps", "tick_shared_timestamp_share", "tick_median_gap_ms",
]
VAP_FIELDS = ("poc", "va_low", "va_high", "volume_events", "hvn_count", "hvn_top_price", "coverage")
VAP_COLUMNS = [f"vap_{w}m_{f}" for w in VAP_WINDOWS for f in VAP_FIELDS]
COLUMNS = IDENTITY_COLUMNS + SLOT_COLUMNS + VAP_COLUMNS


# --- Time grid ---------------------------------------------------------------

def floor_slot(moment):
    """`moment` floored onto the 5-minute grid (UTC)."""
    moment = moment.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return moment.replace(minute=(moment.minute // SLOT_MINUTES) * SLOT_MINUTES)


def slot_ends(start, end):
    """Every slot end in `(floor(start), floor(end)]`, on the grid."""
    step = timedelta(minutes=SLOT_MINUTES)
    cursor = floor_slot(start) + step
    last = floor_slot(end)
    out = []
    while cursor <= last:
        out.append(cursor)
        cursor += step
    return out


def parse_time(text):
    """ISO date or datetime -> aware UTC datetime. A bare date means 00:00 UTC."""
    value = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ms(moment):
    return moment.timestamp() * 1000


def _between(rows, start, end):
    """Rows whose stamp lies in [start, end)."""
    lo, hi = _ms(start), _ms(end)
    return [r for r in rows if (s := analytics._stamp(r)) is not None and lo <= s < hi]


def _scaled(rows, price_scale):
    """Copies in display units, as `app._series_rows` hands rows to analytics."""
    out = []
    for r in rows:
        row = dict(r)
        for side in ("best_bid", "best_ask"):
            if row.get(side) is not None:
                row[side] = row[side] / price_scale
        out.append(row)
    return out


# --- Metrics for one slot ----------------------------------------------------

def slot_metrics(rows, tick_rows):
    """The slot's microstructure from its 10s buckets (display units) and ms tick counts."""
    k = analytics.kpis(rows, BIN_SECONDS)
    s = analytics.spread(rows)
    acf = analytics.autocorrelation(rows, ACF_LAGS)
    flow = analytics.order_flow(rows)
    impact = analytics.burst_impact(rows, bin_seconds=BIN_SECONDS)
    gaps = analytics.tick_gaps(tick_rows)

    mids = [m for _, m, _ in analytics._valid_mids(rows)]
    first = mids[0] if mids else None
    last = mids[-1] if mids else None
    rel = [p["rel_spread"] for p in s["series"]]
    lags = {lag["lag"]: lag["acf"] for lag in acf["lags"]}
    headline = next((c["mean_bps"] for c in impact["curve"] if c["horizon_s"] == 60), None)

    out = {
        "has_data": 1 if k["ticks"] else 0,
        "events": k["ticks"],
        "book_buckets": len(mids),
        "mid_open": first,
        "mid_high": k["high"],
        "mid_low": k["low"],
        "mid_close": last,
        "log_return": math.log(last / first) if first and last and first > 0 and last > 0 else None,
        "change_pct": k["change_pct"],
        "spread_mean": s["mean"],
        "spread_median": s["median"],
        "spread_min": s["min"],
        "spread_max": s["max"],
        "rel_spread_mean": sum(rel) / len(rel) if rel else None,
        "realised_vol_pct": k["realised_vol_pct"],
        "acf_band": acf["band"],
        "flow_buy_events": flow["buy_events"],
        "flow_sell_events": flow["sell_events"],
        "flow_imbalance": flow["imbalance"],
        "book_imbalance": flow["book_imbalance"],
        "burst_threshold": impact["threshold"],
        "burst_count": impact["bursts"],
        "burst_impact_60s_bps": headline,
        "tick_distinct_timestamps": gaps["distinct_timestamps"],
        "tick_shared_timestamp_share": gaps["shared_timestamp_share"],
        "tick_median_gap_ms": gaps["median_gap_ms"],
    }
    for lag in range(1, ACF_LAGS + 1):
        out[f"acf_lag{lag}"] = lags.get(lag)
    return out


def vap_metrics(raw_rows, slot_end, price_scale, data_start):
    """Each lookback's profile summary ending at `slot_end` (raw-price rows).

    `coverage` is the share of the window after `data_start` (the earliest
    moment read), so a window clipped by the start of history is flagged
    instead of silently shorter.
    """
    out = {}
    for window in VAP_WINDOWS:
        start = slot_end - timedelta(minutes=window)
        profile = analytics.volume_at_price(_between(raw_rows, start, slot_end), price_scale)
        area = profile["value_area"] or [None, None]
        nodes = profile["high_volume_nodes"]
        covered = (slot_end - max(start, data_start)).total_seconds() / (window * 60)
        p = f"vap_{window}m_"
        out[p + "poc"] = profile["poc"]
        out[p + "va_low"] = area[0]
        out[p + "va_high"] = area[1]
        out[p + "volume_events"] = profile["total_volume"]
        out[p + "hvn_count"] = len(nodes)
        out[p + "hvn_top_price"] = nodes[0]["price"] if nodes else None
        out[p + "coverage"] = max(0.0, min(1.0, covered))
    return out


def build_rows(identity, series_raw, tick_rows, ends, price_scale, data_start):
    """One output row per slot end for one instrument."""
    out = []
    step = timedelta(minutes=SLOT_MINUTES)
    for end in ends:
        start = end - step
        slot_series = _scaled(_between(series_raw, start, end), price_scale)
        slot_ticks = [t for t in tick_rows if _ms(start) <= t["epoch_ms"] < _ms(end)]
        row = {**identity, "slot_start": _iso(start), "slot_end": _iso(end)}
        row.update(slot_metrics(slot_series, slot_ticks))
        row.update(vap_metrics(series_raw, end, price_scale, data_start))
        out.append(row)
    return out



# --- Kusto reads -------------------------------------------------------------

def load_mapping(db_path):
    """The local symbol catalogue keyed on (account, symbol id).

    `ctidTraderAccountId` is globally unique in cTrader, so (account, symbol)
    is a complete key without the broker spelling (see app.BROKER_ALIASES).
    Hand-maintained `symbol_manual` rows are laid over the Fabric snapshot, as
    the app does, so a manual name wins and an instrument the snapshot lacks
    is still named.
    """
    conn = db.connect(db_path)
    try:
        rows = db.load_symbol_mapping(conn) + db.load_symbol_manual(conn)
    finally:
        conn.close()
    return {(str(r["ctid_trader_account"]), str(r["symbol_id"])): r for r in rows}


def instruments(reader, table, start, end, mapping):
    """Every (broker, account, symbol) that ticked in [start, end), enriched."""
    out, seen = [], set()
    for row in reader.run(queries.symbols_range(table, start, end)):
        account, symbol_id = str(row["ctidTraderAccountId"]), str(row["symbolId"])
        if (account, symbol_id) in seen:
            continue
        seen.add((account, symbol_id))
        mapped = mapping.get((account, symbol_id), {})
        out.append({
            "broker": row.get("broker"),
            "ctid_trader_account": account,
            "symbol_id": symbol_id,
            "symbol_name": mapped.get("symbol_name"),
            "standardized_name": mapped.get("standardized_name"),
            "asset_class": mapped.get("asset_class"),
        })
    return out


def export_chunk(cfg, reader, identity, ends, data_start=None):
    """Rows for one instrument's consecutive slot ends: two Kusto reads in total.

    The series read starts the widest VAP lookback before the first slot, so
    every window is complete; `data_start` (backfill start) only marks how much
    of each window the feed could actually supply.
    """
    first_start = ends[0] - timedelta(minutes=SLOT_MINUTES)
    lookback_start = ends[0] - timedelta(minutes=max(VAP_WINDOWS))
    symbol_id, account = identity["symbol_id"], identity["ctid_trader_account"]
    series_raw = reader.run(queries.series_range(
        cfg.table, symbol_id, lookback_start, ends[-1], BIN_SECONDS, account))
    tick_rows = reader.run(queries.tick_times_range(
        cfg.table, symbol_id, first_start, ends[-1], account))
    return build_rows(identity, series_raw, tick_rows, ends, cfg.price_scale,
                      data_start or EPOCH)


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# --- Storage -----------------------------------------------------------------

def write_rows(rows, db_path):
    """Upsert the rows into `metrics_5m` in the local database. Returns the count."""
    conn = db.connect(db_path)
    try:
        return db.save_metrics(conn, rows, COLUMNS)
    finally:
        conn.close()


# --- Entry point -------------------------------------------------------------

def coverage(reader, table):
    """The feed's first and last tick - how far back a backfill can go."""
    rows = reader.run(f"""
{table}
| where timestamp > datetime(2000-01-01)
| summarize first_tick = min(timestamp), last_tick = max(timestamp), events = count()
""")
    return rows[0] if rows else {}


def plan_range(args, now):
    """The slot ends for a backfill (--start/--end) or for the live recent slots."""
    if args.start:
        start = parse_time(args.start)
        end = parse_time(args.end) if args.end else now
        return slot_ends(start, end)
    end = floor_slot(now)
    start = end - timedelta(minutes=SLOT_MINUTES * max(1, args.recent))
    return slot_ends(start, end)


def feed_start(reader, table):
    """The feed's first tick: VAP windows reaching before it are flagged partial."""
    first = coverage(reader, table).get("first_tick")
    return parse_time(first) if first else None


def main(argv=None, reader=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", help="backfill from this UTC date/time (ISO)")
    parser.add_argument("--end", help="backfill up to this UTC date/time (default: now)")
    parser.add_argument("--recent", type=int, default=DEFAULT_RECENT_SLOTS,
                        help="without --start: the last N completed slots (default 2)")
    parser.add_argument("--symbol", help="only this symbol id")
    parser.add_argument("--account", help="only this trader account")
    parser.add_argument("--list", action="store_true",
                        help="print what metrics_5m holds per instrument and exit (no Kusto)")
    parser.add_argument("--chunk-minutes", type=int, default=DEFAULT_CHUNK_MINUTES,
                        help="slots read per Kusto query, in minutes (default 60)")
    parser.add_argument("--delay", type=float, default=None,
                        help="live mode: seconds to wait before reading (default VAP_GRID_DELAY)")
    parser.add_argument("--coverage", action="store_true",
                        help="print the feed's first/last tick and exit")
    args = parser.parse_args(argv)

    cfg = load_config()
    if args.list:
        conn = db.connect(cfg.db_path)
        try:
            summary = db.metrics_summary(conn)
        finally:
            conn.close()
        for r in summary:
            print(f"{r['broker']} {r['ctid_trader_account']}:{r['symbol_id']} "
                  f"{r['standardized_name'] or '-'} slots={r['slots']} live={r['live_slots']} "
                  f"{r['first_slot']}..{r['last_slot']}")
        if not summary:
            print(f"{db.METRICS_TABLE} is empty")
        return 0

    if reader is None:
        from kusto_client import KustoReader
        reader = KustoReader(cfg)

    if args.coverage:
        info = coverage(reader, cfg.table)
        print(f"first_tick={info.get('first_tick')} last_tick={info.get('last_tick')} "
              f"events={info.get('events')}")
        return 0

    if not args.start:
        # Same reason as build_vap: let the slot's last ticks land first.
        delay = cfg.vap_grid_delay if args.delay is None else args.delay
        if delay and delay > 0:
            time.sleep(delay)
    ends = plan_range(args, datetime.now(timezone.utc))
    # Only a backfill can reach back to the start of history; the live 4h
    # lookback is always inside the feed, so it skips the extra scan.
    data_start = feed_start(reader, cfg.table) if args.start else None
    if not ends:
        print("no complete 5-minute slot in the requested range", file=sys.stderr)
        return 1

    targets = instruments(reader, cfg.table, ends[0] - timedelta(minutes=SLOT_MINUTES),
                          ends[-1], load_mapping(cfg.db_path))
    if args.symbol:
        targets = [t for t in targets if t["symbol_id"] == args.symbol]
    if args.account:
        targets = [t for t in targets if t["ctid_trader_account"] == args.account]

    if not targets:
        # Normal when the market is shut (weekend): nothing ticked, nothing to write.
        print(f"no instrument ticked in {_iso(ends[0] - timedelta(minutes=SLOT_MINUTES))}"
              f"..{_iso(ends[-1])}; nothing written")
        return 0

    per_chunk = max(1, args.chunk_minutes // SLOT_MINUTES)
    rows, failures = [], 0
    for identity in targets:
        label = db.instrument_key(identity["symbol_id"], identity["ctid_trader_account"])
        try:
            got = []
            for chunk in _chunks(ends, per_chunk):
                got.extend(export_chunk(cfg, reader, identity, chunk, data_start))
            rows.extend(got)
            print(f"OK  {label} slots={len(got)}")
        except Exception as exc:                    # one bad instrument must not stop the run
            failures += 1
            print(f"ERR {label}: {exc}", file=sys.stderr)

    written = write_rows(rows, cfg.db_path)
    print(f"wrote {db.METRICS_TABLE} rows={written} ({cfg.db_path})")
    return 1 if targets and failures == len(targets) else 0


if __name__ == "__main__":
    raise SystemExit(main())

