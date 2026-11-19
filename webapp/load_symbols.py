"""Load hand-maintained instrument names into the local `symbol_manual` table.

The Fabric snapshot (`snapshot_symbols.py`) is the broker catalogue and is the
base for naming a symbol. This loader is for names you already know and want the
app to show without waiting on that snapshot, or for an instrument the catalogue
does not describe. Rows live in `symbol_manual`, which the app lays over the
snapshot, so a manual name wins for its (broker, account, symbol id) triple and
a snapshot re-run does not remove it.

The CSV has one row per instrument, with a header line:

    broker,ctid_trader_account,symbol_id,symbol_name,asset_class
    acgmarkets,49208255,136,XAUUSD,Commodities

* `ctid_trader_account` is required. A symbol id is shared across brokers and
  means a different instrument on each account, so an id without its account
  would be ambiguous. The loader refuses such a row rather than guessing.
* `asset_class` is collapsed onto the canonical set exactly as the snapshot does
  (`Commodities`, `FX`, `Equities`, `Index`, `Crypto`; anything unknown is `Other`).

The load is all-or-nothing: if any row is invalid, nothing is written.

    python load_symbols.py                       # load symbols_manual.csv
    python load_symbols.py path/to/other.csv     # load another file
    python load_symbols.py --dry-run             # validate and report, write nothing
    python load_symbols.py --list                # show the manual rows and exit

Run from the webapp folder, so `import db` resolves.
"""
import argparse
import csv
import os
import sys

import db
from config import load_config

DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "symbols_manual.csv")
COLUMNS = ("broker", "ctid_trader_account", "symbol_id", "symbol_name", "asset_class")


class SymbolFileError(ValueError):
    """A row in the CSV that cannot be loaded. The message names the line."""


def read_rows(path):
    """Parse and validate the CSV into `(broker, account, symbol_id, name, asset_class)` tuples."""
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise SymbolFileError(f"{path}: header is missing column(s): {', '.join(missing)}")
        rows = []
        for line_no, raw in enumerate(reader, start=2):  # line 1 is the header
            rows.append(_parse_row(raw, f"{path}:{line_no}"))
    return rows


def _parse_row(raw, where):
    values = {c: (raw.get(c) or "").strip() for c in COLUMNS}
    for column in ("broker", "ctid_trader_account", "symbol_id", "symbol_name"):
        if not values[column]:
            raise SymbolFileError(f"{where}: {column} is blank")
    if not values["ctid_trader_account"].isdigit():
        raise SymbolFileError(f"{where}: ctid_trader_account must be the numeric account id")
    try:
        symbol_id = int(values["symbol_id"])
    except ValueError:
        raise SymbolFileError(f"{where}: symbol_id must be a whole number") from None
    return (values["broker"], values["ctid_trader_account"], symbol_id,
            values["symbol_name"], values["asset_class"] or None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", nargs="?", default=DEFAULT_CSV,
                        help=f"the symbol file to load (default: {DEFAULT_CSV})")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and report what would be written, write nothing")
    parser.add_argument("--list", action="store_true",
                        help="show the current manual rows and exit")
    args = parser.parse_args(argv)

    cfg = load_config()

    if args.list:
        conn = db.connect(cfg.db_path)
        try:
            rows = db.load_symbol_manual(conn)
        finally:
            conn.close()
        print(f"manual symbols: {len(rows)} rows in {cfg.db_path}")
        for row in rows:
            print(f"  {row['broker']}\t{row['ctid_trader_account']}\t{row['symbol_id']}\t"
                  f"{row['symbol_name']}\t{row['standardized_name']}\t{row['asset_class']}")
        return 0

    try:
        rows = read_rows(args.csv)
    except (OSError, SymbolFileError) as exc:
        print(f"ERR {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"dry run: {len(rows)} rows in {args.csv} would be written to "
              f"symbol_manual in {cfg.db_path}")
        return 0

    conn = db.connect(cfg.db_path)
    try:
        written = db.upsert_symbol_manual(conn, rows)
    finally:
        conn.close()
    print(f"wrote {written} rows from {args.csv} to symbol_manual in {cfg.db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
