#!/usr/bin/env python
"""Start the Axioma Analytics dashboard.

    python run.py                # http://127.0.0.1:8000
    python run.py --port 9000 --reload
"""
from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
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
    env_name = active_environment() or "default"
    print(f"\n  Axioma Analytics  \u2192  http://{args.host}:{args.port}")
    print(f"  Environment: {env_name} ({mask_host(environment_host())})\n")
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()