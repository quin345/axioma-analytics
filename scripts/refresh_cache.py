#!/usr/bin/env python
"""Re-read the fixed cache window once, from a timer instead of the app.

The app refreshes its own cache every `CACHE_REFRESH_MINUTES` (30 by default)
from a background task, so there is normally nothing to run here. This script
does the same single cycle out of process, for operators who would rather keep
the KQL work out of the web process:

    python scripts/refresh_cache.py
    python scripts/refresh_cache.py --json | python3 -m json.tool

Typical cron entry (every 30 minutes, log to the journal):

    */30 * * * * /usr/bin/python3 /home/azureuser/axioma-analytics/scripts/refresh_cache.py \
        >> /var/log/axioma-refresh.log 2>&1

`*/30` fires on the same wall-clock marks as the in-process cycle (:00 and
:30), so the two schedules line up rather than drift against each other.

Or a systemd timer on the `axioma.service` host (see README, *Deployment*).
Only run it that way with `CACHE_REFRESH_MINUTES=0` in `.env`, otherwise both the
app and the timer will refresh the same window.

Exit status: 0 when the cycle completed (partial symbol failures are reported in
the summary), 1 when it could not run at all - so a timer surfaces a broken
endpoint rather than logging a success that did nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = ap.parse_args()

    from app import service
    from app.config import get_settings

    settings = get_settings()
    if not settings.is_configured:
        print("KQL_ENDPOINT_PROD not set. Add it to .env before refreshing.", file=sys.stderr)
        return 1

    summary = service.refresh_cache()

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        window = summary["cache_window_minutes"] / 60
        print(f"Refreshed {summary['symbols']} symbol(s) over {window:g} h, "
              f"{summary['rows']} row(s), in {summary['seconds']}s "
              f"(next cycle in {summary['interval_minutes']} min).")
        for err in summary["errors"]:
            print(f"  ! {err}", file=sys.stderr)

    # Every symbol failing is the endpoint being down, not a data gap; make it
    # visible to the timer's failure handling.
    return 0 if summary["symbols"] or not summary["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
