"""Copy the local `vap_run` table into the Fabric-managed SQL database.

The daily cron/systemd entry point, run on the VM:

    # /etc/cron.d/axioma-sql-mirror  (daily, after the 03:30 retention job)
    45 3 * * * azureuser set -a; . /home/azureuser/axioma-analytics/.env; set +a; \\
        cd /home/azureuser/axioma-analytics/webapp && \\
        /home/azureuser/axioma-analytics/venv/bin/python export_vap_run.py \\
        >> /var/log/axioma-vap.log 2>&1

It runs *after* retention so the mirror reflects the database as it is kept, not
as it was before a prune.

The copy is idempotent - see `sql_mirror` - so running it by hand, or twice in a
day, is safe: rows are merged on `(symbol_id, window_minutes, as_of)`.

The export is off unless `SQL_SERVER` is set, so a box with no SQL target
configured simply prints that and exits 0.

Usage:
    python export_vap_run.py              # mirror every recorded stop
    python export_vap_run.py --dry-run    # count what would move, write nothing
    python export_vap_run.py --create     # create the table/index first
    python export_vap_run.py --limit 50   # only the newest N stops
"""
import argparse
import sys

import db
import sql_mirror
from config import load_config


def _newest_first(rows, limit):
    """Keep the newest `limit` stops. Rows arrive oldest-first from `read_runs`."""
    if limit is None:
        return rows
    return rows[len(rows) - limit:] if len(rows) > limit else rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be written, write nothing")
    parser.add_argument("--create", action="store_true",
                        help="create the mirror table and index if missing")
    parser.add_argument("--limit", type=int, default=None,
                        help="mirror only the newest N stops")
    parser.add_argument("--list", action="store_true",
                        help="show the local stop count and the target, then exit")
    args = parser.parse_args(argv)

    cfg = load_config()

    if not cfg.sql_server:
        print("SQL_SERVER is not set - the vap_run mirror is disabled, nothing to do")
        return 0

    target = f"{cfg.sql_server}/{cfg.sql_database} -> " \
             f"[{cfg.sql_schema}].[{cfg.sql_table}]"

    local = db.connect(cfg.db_path)
    try:
        rows = sql_mirror.read_runs(local)
    finally:
        local.close()

    rows = _newest_first(rows, args.limit)

    if args.list:
        print(f"local stops: {len(rows)}")
        print(f"target:      {target}")
        return 0

    if args.dry_run:
        print(f"dry run: {len(rows)} stops would be merged into {target}")
        return 0

    conn = sql_mirror.connect(cfg)
    try:
        if args.create:
            sql_mirror.ensure_table(conn, cfg)
        inserted, updated = sql_mirror.upsert_runs(conn, cfg, rows)
    except sql_mirror.SqlMirrorError as exc:
        print(f"ERR {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    print(f"mirrored {len(rows)} stops into {target} "
          f"(inserted={inserted} updated={updated})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
