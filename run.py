#!/usr/bin/env python
"""Start the Axioma Analytics dashboard against the production gold endpoint.

    python run.py                # http://127.0.0.1:8000
    python run.py --port 9000 --reload
"""
from __future__ import annotations

import argparse
import sys

import uvicorn


def _force_utf8() -> None:
    """Windows consoles and redirections default to cp1252, which cannot encode
    the banner's arrow. Without this, printing the banner raises
    UnicodeEncodeError before uvicorn ever starts.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main() -> None:
    _force_utf8()
    ap = argparse.ArgumentParser(description="Axioma Analytics server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    args = ap.parse_args()

    from app.config import get_settings

    settings = get_settings()
    if not settings.is_configured:
        print("\n  SQL_ENDPOINT_PROD is not set. Add it to .env before starting.\n")
        raise SystemExit(1)

    print(f"\n  Axioma Analytics  \u2192  http://{args.host}:{args.port}")
    print(f"  Source: {settings.gold_database}.{settings.gold_schema}\n")
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()