"""Daily executed-trades reports of the autotraders, read-only (served at /analytics/ and /api/analytics).

`autotrade trades-report` (ctrader_autotrade/autotrade/daily.py) writes `<reports>/<instance>/YYYY-MM-DD.json`,
`index.json` and `latest.json`; this module only lists and reads them. Not to be confused with analytics.py
(the market-data statistics).

Safety: the instance must be the name of a directory that exists directly under the reports dir AND match
INSTANCE_RE; the day must match DAY_RE and be a real calendar date. A path is only ever built from those two
validated values, then resolved and required to stay inside the reports dir (symlinks included). Nothing here
writes, and file sizes are capped.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date

INSTANCE_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_BYTES = 4 * 1024 * 1024


class NotFound(Exception):
    """No such instance / day (or an invalid name): the route answers 404."""


def valid_day(day):
    if not isinstance(day, str) or not DAY_RE.fullmatch(day):
        return False
    try:
        date.fromisoformat(day)
    except ValueError:
        return False
    return True


def list_instances(base):
    """Sorted names of the instance directories present under `base` (none when it is missing)."""
    try:
        names = os.listdir(base)
    except OSError:
        return []
    return sorted(n for n in names if INSTANCE_RE.fullmatch(n) and os.path.isdir(os.path.join(base, n)))


def _inside(base, path):
    base_real, real = os.path.realpath(base), os.path.realpath(path)
    return real == base_real or real.startswith(base_real + os.sep)


def _read_json(base, instance, filename):
    """JSON of <base>/<instance>/<filename>; `instance` must be a present directory, `filename` one of ours."""
    if not isinstance(instance, str) or not INSTANCE_RE.fullmatch(instance) or instance not in list_instances(base):
        raise NotFound("instance")
    path = os.path.join(base, instance, filename)
    if not _inside(base, path) or not os.path.isfile(path):
        raise NotFound("report")
    try:
        if os.path.getsize(path) > MAX_BYTES:
            raise NotFound("report")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        raise NotFound("report") from None


def instance_index(base, instance):
    return _read_json(base, instance, "index.json")


def day_report(base, instance, day):
    if not valid_day(day):
        raise NotFound("day")
    return _read_json(base, instance, f"{day}.json")


def overview(base):
    """{"instances": [{name, index | None, error?}]}: every instance directory with its index.json."""
    out = []
    for name in list_instances(base):
        try:
            out.append({"name": name, "index": instance_index(base, name)})
        except NotFound:
            out.append({"name": name, "index": None, "error": "no index.json yet"})
    return {"instances": out}
