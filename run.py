#!/usr/bin/env python
"""Start the Axioma Analytics dashboard.

    python run.py                # http://127.0.0.1:8000
    python run.py --port 9000 --reload
"""
from __future__ import annotations

import argparse
import os
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
    ap.add_argument("--env", metavar="NAME",
                    help="SQL analytics endpoint to use (SQL_ENDPOINT_<NAME>); "
                         "defaults to SQL_ENV")
    ap.add_argument("--list-envs", action="store_true",
                    help="list configured environments and exit")
    args = ap.parse_args()

    from app.config import (active_environment, available_environments,
                            environment_host, mask_host, set_environment)

    if args.list_envs:
        envs = available_environments()
        if not envs:
            print("\n  No environments configured. Set SQL_ENDPOINT_<NAME> in .env.\n")
            return
        current = active_environment()
        print("\n  Configured environments:")
        for name, host in envs.items():
            mark = "*" if name == current else " "
            print(f"   {mark} {name:<12} {mask_host(host)}")
        print()
        return

    if args.env:
        try:
            set_environment(args.env)
        except ValueError as exc:
            raise SystemExit(f"\n  {exc}\n")
        # --reload starts the app in a *new* process, which re-imports
        # app.config and cannot see the in-process selection made above.
        # Exporting SQL_ENV makes the choice survive into that child process.
        os.environ["SQL_ENV"] = active_environment()
    env_name = active_environment() or "default"
    print(f"\n  Axioma Analytics  \u2192  http://{args.host}:{args.port}")
    print(f"  Environment: {env_name} ({mask_host(environment_host())})\n")
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()