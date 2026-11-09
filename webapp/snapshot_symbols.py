"""Snapshot the broker/symbol mapping from Fabric into the local `axioma.db`.

The Fabric-managed SQL database `ctrader_metadata` holds `dbo.symbols`, the
broker's own catalogue: which `symbolId` is which instrument on which trader
account. That triple - `(broker, ctidTraderAccountId, symbolId)` - is what the
dashboard needs to name a symbol and to know which broker account it came from,
and it is the source table's primary key.

This is a **point-in-time snapshot**, not a sync: it reads the table once and
replaces the local `symbol_mapping` table with what it saw, stamping every row
with the pull time. Re-run it whenever a fresh copy is wanted; the table is
swapped atomically so a failed pull leaves the previous snapshot intact.

There are no secrets here - the VM's system-assigned managed identity is the
credential, exactly as `sql_mirror` already does for `vap_run`. The target
(`SQL_SERVER` / `SQL_DATABASE`) is the same one `.env` already configures; with
`SQL_SERVER` blank the snapshot is simply off.

    python snapshot_symbols.py              # pull dbo.symbols into axioma.db
    python snapshot_symbols.py --dry-run    # count what would be written, write nothing
    python snapshot_symbols.py --list       # show the current local snapshot and exit

Run from the webapp folder, so `import db` resolves.
"""
import argparse
import sys

import db
from config import load_config

# The source query. The three key columns are the mapping; `symbolName` is its
# human-readable half. `asset_class` is the broker's own name for the
# instrument, resolved through the broker's own catalogue: the same numeric
# `symbolCategoryId` means a different class on each account (acgmarkets files
# `XAUUSD.pro` under its category 10 = Metals, where icmarketssc's 10 = Bonds),
# so the join carries the *account* as well as the broker, and the class table
# is keyed by assetClassId on that account.
#
# LEFT JOIN, so a symbol whose broker ships no catalogue row still comes through
# with a NULL asset class rather than vanishing from the snapshot.
SOURCE_TABLE = "dbo.symbols"
SELECT_SQL = """
SELECT s.broker,
       s.ctidTraderAccountId,
       s.symbolId,
       s.symbolName,
       ac.name AS assetClass
FROM dbo.symbols AS s
LEFT JOIN dbo.symbols_category AS sc
       ON sc.broker = s.broker
      AND sc.ctidTraderAccountId = s.ctidTraderAccountId
      AND sc.symbolCategoryId = s.symbolCategoryId
LEFT JOIN dbo.asset_classes AS ac
       ON ac.broker = sc.broker
      AND ac.ctidTraderAccountId = sc.ctidTraderAccountId
      AND ac.assetClassId = sc.assetClassId
ORDER BY s.broker, s.ctidTraderAccountId, s.symbolId
"""


def read_symbols(conn):
    """Every mapping row from the Fabric source, as plain tuples.

    A NULL `symbolName` is normalised to the empty string so the local column is
    never NULL and a reader can treat "no name" and "blank name" the same way.
    A NULL asset class is kept as NULL - that is the honest answer for a symbol
    the broker has no catalogue row for.
    """
    rows = conn.execute(SELECT_SQL).fetchall()
    return [
        (broker, str(account), int(symbol_id), name or "", asset_class)
        for broker, account, symbol_id, name, asset_class in rows
    ]


def main(argv=None):
    import sql_mirror

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be written, write nothing")
    parser.add_argument("--list", action="store_true",
                        help="show the current local snapshot and exit")
    args = parser.parse_args(argv)

    cfg = load_config()

    if args.list:
        conn = db.connect(cfg.db_path)
        try:
            rows = db.load_symbol_mapping(conn)
        finally:
            conn.close()
        print(f"local snapshot: {len(rows)} rows in {cfg.db_path}")
        for row in rows:
            print(f"  {row['broker']}\t{row['ctid_trader_account']}\t"
                  f"{row['symbol_id']}\t{row['symbol_name']}\t"
                  f"{row['standardized_name']}\t{row['asset_class']}")
        return 0

    if not cfg.sql_server:
        print("SQL_SERVER is not set - the symbol snapshot is disabled, nothing to do")
        return 0

    try:
        source = sql_mirror.connect(cfg)
    except sql_mirror.SqlMirrorError as exc:
        print(f"ERR {exc}", file=sys.stderr)
        return 1
    try:
        rows = read_symbols(source)
    finally:
        source.close()

    target = f"[{cfg.sql_schema}].{SOURCE_TABLE} -> " \
             f"symbol_mapping in {cfg.db_path}"
    if args.dry_run:
        print(f"dry run: {len(rows)} mapping rows would be snapshotted from {target}")
        return 0

    conn = db.connect(cfg.db_path)
    try:
        written = db.replace_symbol_mapping(conn, rows)
    finally:
        conn.close()

    print(f"snapshotted {written} mapping rows from {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
