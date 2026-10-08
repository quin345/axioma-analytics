#!/usr/bin/env python
"""Start the Axioma Analytics dashboard against the production KQL endpoint.

    python run.py                # http://127.0.0.1:8000
    python run.py --port 9000 --reload
"""
from __future__ import annotations

import argparse
import logging
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


def _configure_logging() -> None:
    """Send the app's own log lines to stderr, where systemd collects them.

    Uvicorn configures only its own loggers, so without this the refresh cycle's
    "Cache refresh: {...}" line would be swallowed and `journalctl -u axioma`
    would show request logs but nothing about the window being re-read. Only the
    ``app.*`` loggers are raised to INFO: azure-identity and azure-core log every
    token request (URL, headers, SDK version) at INFO, which would bury the one
    line that matters. Their warnings and errors still come through.
    """
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("app").setLevel(logging.INFO)


def main() -> None:
    _force_utf8()
    _configure_logging()
    ap = argparse.ArgumentParser(description="Axioma Analytics server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    args = ap.parse_args()

    from app.config import get_settings

    settings = get_settings()
    if not settings.is_configured:
        print("\n  KQL_ENDPOINT_PROD not set. Add it to .env before starting.\n")
        raise SystemExit(1)

    print(f"\n  Axioma Analytics  \u2192  http://{args.host}:{args.port}")
    print(f"  Data (KQL)  : {settings.kql_database}.{settings.kql_table} "
          f"+ {settings.symbol_table}")
    cache = (f"{settings.redis_host}:{settings.redis_port}"
             if settings.redis_configured else "not configured - reading KQL per request")
    print(f"  Cache       : {cache}, window {settings.cache_lookback_hours} h")
    cadence = (f"every {settings.cache_refresh_minutes} min"
               if settings.cache_refresh_seconds else "off - cached on demand")
    print(f"  Refresh     : {cadence}\n")
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()