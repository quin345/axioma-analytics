"""Record every instrument's volume-at-price profiles into the local database.

The cron/systemd entry point, run every 5 minutes on the VM:

    # /etc/cron.d/axioma-vap  (5-minute grid, 15s past the mark)
    */5 * * * *  azureuser  cd /home/azureuser/axioma-analytics/webapp && \
        sleep 15 && ../venv/bin/python build_vap.py >> /var/log/axioma-vap.log 2>&1

One `/api/volume-at-price/refresh` per symbol does exactly the same work; this
script exists so the refresh does not depend on the web process being up, and so
a failure lands in a log rather than in a user's browser.

Usage:
    python build_vap.py                # every symbol the feed lists
    python build_vap.py --symbol 136   # one symbol
    python build_vap.py --list         # show what is on record, compute nothing
    python build_vap.py --prune        # retention only: trim vap_level, no Kusto
    python build_vap.py --vacuum       # reclaim file space (checkpoint + VACUUM)
"""
import argparse
import os
import sys

import analytics
import db
import queries
from config import load_config
from kusto_client import KustoReader

WINDOWS = (60, 120, 180, 240)


def _symbols(reader, table):
    """The distinct symbol ids the feed collects, for the volume-at-price job.

    `queries.symbols` returns the full (broker, account, symbol) triple so the
    dashboard can join on all three, but this job records its profiles per
    symbol id - `vap_run` is keyed on `(symbol_id, window_minutes, as_of)` - so
    the ids are collapsed and de-duplicated here.
    """
    seen = []
    for row in reader.run(queries.symbols(table)):
        symbol_id = str(row["symbolId"])
        if symbol_id not in seen:
            seen.append(symbol_id)
    return seen


def refresh_one(cfg, reader, symbol_id):
    """Derive and record one symbol's profiles. Returns (as_of, {window: poc})."""
    rows = reader.run(queries.vap_ticks(cfg.table, symbol_id, max(WINDOWS)))
    profiles = analytics.profiles_by_window(rows, cfg.price_scale, WINDOWS)
    as_of = _as_of(rows)

    conn = db.connect(cfg.db_path)
    try:
        db.save_profiles(conn, symbol_id, profiles, as_of)
        # Retention: `vap_run` is the durable record (only capped if
        # VAP_KEEP_RUNS is set); `vap_level` is trimmed to the hour window.
        db.prune_runs(conn, keep=cfg.vap_keep_runs)
        db.prune_levels(conn, keep_hours=cfg.vap_level_hours)
    finally:
        conn.close()
    return as_of, {w: p["poc"] for w, p in profiles.items()}


# The refresh grid, in minutes. Same variable `app.py` reads.
REFRESH_MINUTES = int(os.environ.get("CACHE_REFRESH_MINUTES", "5") or 5) or 5


def _as_of(rows):
    """The stop a frame is 'as of' - its newest stamp, floored to the 5-min grid.

    Flooring to the grid (not the raw tick time) is what makes two refreshes
    inside one 5-minute slot collapse onto the same row, so record-keeping stays
    one row per stop instead of one per run. Must match `app._vap_as_of`, or the
    cron job and the HTTP route would key the same stop two different ways.
    """
    from datetime import datetime, timezone

    stamps = [s for s in (analytics._stamp(r) for r in rows) if s is not None]
    if not stamps:
        return datetime.now(timezone.utc).isoformat()
    newest = datetime.fromtimestamp(max(stamps) / 1000, tz=timezone.utc)
    floored = newest.replace(second=0, microsecond=0)
    return floored.replace(
        minute=(floored.minute // REFRESH_MINUTES) * REFRESH_MINUTES
    ).isoformat()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", help="refresh only this symbol id")
    parser.add_argument("--list", action="store_true",
                        help="print the recorded stops and exit")
    parser.add_argument("--prune", action="store_true",
                        help="apply retention only (no Kusto) and exit")
    parser.add_argument("--vacuum", action="store_true",
                        help="checkpoint the WAL and VACUUM the file, then exit")
    args = parser.parse_args(argv)

    cfg = load_config()

    if args.list:
        conn = db.connect(cfg.db_path)
        try:
            rows = conn.execute(
                """SELECT symbol_id, window_minutes,
                          MAX(as_of) AS newest,
                          COUNT(*) AS stops,
                          (SELECT COUNT(*) FROM vap_level l
                            WHERE l.run_id IN (
                                SELECT id FROM vap_run r2
                                 WHERE r2.symbol_id = r.symbol_id
                                   AND r2.window_minutes = r.window_minutes
                            )) AS bins
                   FROM vap_run r
                   GROUP BY symbol_id, window_minutes
                   ORDER BY symbol_id, window_minutes"""
            ).fetchall()
        finally:
            conn.close()
        for r in rows:
            print(f"{r['symbol_id']} {r['window_minutes']:>4}m "
                  f"newest={r['newest']} stops={r['stops']} bins={r['bins']}")
        return 0

    if args.prune:
        conn = db.connect(cfg.db_path)
        try:
            runs = db.prune_runs(conn, keep=cfg.vap_keep_runs)
            bins = db.prune_levels(conn, keep_hours=cfg.vap_level_hours)
        finally:
            conn.close()
        print(f"pruned runs={runs} bins={bins} "
              f"(keep_runs={cfg.vap_keep_runs}, level_hours={cfg.vap_level_hours})")
        return 0

    if args.vacuum:
        conn = db.connect(cfg.db_path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            conn.commit()
        finally:
            conn.close()
        print("vacuumed")
        return 0

    reader = KustoReader(cfg)
    targets = [args.symbol] if args.symbol else _symbols(reader, cfg.table)

    failures = 0
    for symbol_id in targets:
        try:
            as_of, pocs = refresh_one(cfg, reader, symbol_id)
            summary = " ".join(f"{w}m={poc:.5f}" if poc is not None else f"{w}m=-"
                               for w, poc in sorted(pocs.items()))
            print(f"OK  {symbol_id} as_of={as_of} {summary}")
        except Exception as exc:                      # keep going: one bad symbol
            failures += 1                             # must not stop the run
            print(f"ERR {symbol_id}: {exc}", file=sys.stderr)

    return 1 if failures and failures == len(targets) else 0


if __name__ == "__main__":
    raise SystemExit(main())
