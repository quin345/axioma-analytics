#!/usr/bin/env python
"""Verify connectivity to the Fabric SQL analytics endpoint and list tick tables.

Usage:
    python scripts/check_connection.py            # connection + catalog discovery
    python scripts/check_connection.py --query "SELECT TOP 5 * FROM [x].[dbo].[y]"

Exit codes: 0 = success, 1 = failure (message printed to stderr).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db import WarehouseError, connect, discover_tables, get_symbols, query  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Check the Fabric SQL analytics endpoint connection.")
    ap.add_argument("--query", help="Run an ad-hoc SELECT and print the result.")
    ap.add_argument("--schema", help="Show columns of a specific schema.table.")
    args = ap.parse_args()

    s = get_settings()
    mode = "service principal" if s.has_credentials else "az login (access token)"
    print(f"host    : {s.host}")
    print(f"auth    : {mode}")
    print(f"driver  : {s.odbc_driver}")
    if not s.has_credentials:
        print("note    : set FABRIC_TENANT_ID / FABRIC_CLIENT_ID / FABRIC_CLIENT_SECRET to avoid az CLI")
    print()

    try:
        with connect() as conn:
            print("CONNECTED")
            print(query(conn, "SELECT CURRENT_TIMESTAMP AS server_time").to_string(index=False))

            if args.query:
                print()
                print(query(conn, args.query).to_string(index=False))
                return 0

            if args.schema:
                sch, _, tbl = args.schema.partition(".")
                df = query(
                    conn,
                    "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
                    f"WHERE TABLE_SCHEMA = '{sch}' AND TABLE_NAME = '{tbl}' ORDER BY ORDINAL_POSITION",
                )
                print()
                print(df.to_string(index=False) if not df.empty else f"no such table: {args.schema}")
                return 0

            tables = discover_tables(conn)
            if not tables:
                print("\nNo tick-like tables found. Expected a table with a timestamp plus "
                      "bid/ask (or last) columns.")
                return 1

            print(f"\nTick tables discovered: {len(tables)}\n")
            for t in tables[:10]:
                print(f"  {t.label}")
                print(f"    columns : {', '.join(t.columns[:16])}")
                print(f"    mapped  : {t.column_map.to_dict()}")
                syms = get_symbols(conn, t, limit=8)
                if syms:
                    print(f"    symbols : " + ", ".join(f"{x['symbol']} ({x['ticks']:,})" for x in syms))
                print()
            return 0

    except WarehouseError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        if "Client secret" in str(exc) or "no access token" in str(exc):
            print("hint: run `az login`, then add FABRIC_* credentials to .env", file=sys.stderr)
        print("hint: grant the signed-in user or service principal the Viewer role on the "
              "Fabric workspace (Fabric portal > workspace > Manage access).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())