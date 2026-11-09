"""The local record-keeping database for derived volume-at-price profiles.

Every 5 minutes (the same grid the rest of the app runs on) the refresh job
derives, for each instrument and each fixed lookback window (1 h / 2 h / 3 h /
4 h), the volume-at-price ladder and its point of control, and writes the result
here. This is a **record**, not a cache: the request path reads the newest stop
for the asked window, and the rows stay so a later stop can be compared with an
earlier one.

SQLite, from the standard library - no server and no new dependency. One row per
`(symbol_id, window_minutes, as_of)` in `vap_run`, with the ladder's price bins
in the child table `vap_level`. The unique key is what makes a re-run inside the
same 5-minute slot an upsert rather than a duplicate: record-keeping stays one
row per stop.
"""
import json
import os
import sqlite3

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS vap_run (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_id      TEXT    NOT NULL,
    window_minutes INTEGER NOT NULL,
    as_of          TEXT    NOT NULL,
    computed_at    TEXT    NOT NULL,
    poc            REAL,
    va_low         REAL,
    va_high        REAL,
    total_volume   REAL    NOT NULL DEFAULT 0,
    high_volume_nodes TEXT NOT NULL DEFAULT '[]',
    UNIQUE (symbol_id, window_minutes, as_of)
);

CREATE TABLE IF NOT EXISTS vap_level (
    run_id INTEGER NOT NULL REFERENCES vap_run(id) ON DELETE CASCADE,
    price  REAL    NOT NULL,
    volume REAL    NOT NULL,
    ticks  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vap_run_lookup
    ON vap_run (symbol_id, window_minutes, as_of DESC);
"""


def connect(db_path):
    """Open the local database, creating its folder and schema if needed."""
    if db_path != ":memory:":
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # WAL lets the refresh job write while a request reads.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


def init_db(conn):
    """Create the schema and stamp the version. Idempotent."""
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()


def save_profile(conn, symbol_id, window_minutes, as_of, profile, computed_at=None):
    """Persist one window's profile for one stop. Upsert on (symbol, window, as_of).

    Re-running inside the same 5-minute slot replaces the row's levels rather
    than appending a second copy, so the record stays one row per stop.

    A profile that derived **no bins at all** (the feed was momentarily empty,
    or older than the window) is not a real stop, so it is never written on its
    own: it neither replaces an existing stop's ladder nor creates a new empty
    stop that would shadow an older, good one. Returns the run id, or None when
    nothing was recorded.
    """
    computed_at = computed_at or _now()
    area = profile.get("value_area") or [None, None]
    nodes = profile.get("high_volume_nodes") or []
    bins = profile.get("bins") or []

    existing = conn.execute(
        "SELECT id FROM vap_run WHERE symbol_id = ? AND window_minutes = ? AND as_of = ?",
        (str(symbol_id), int(window_minutes), as_of),
    ).fetchone()

    if not bins:
        # Nothing derived for this stop. If a good stop already exists keep it
        # untouched; otherwise skip entirely rather than record an empty stop.
        if existing is None:
            return None
        has_levels = conn.execute(
            "SELECT 1 FROM vap_level WHERE run_id = ? LIMIT 1", (existing["id"],)
        ).fetchone()
        if has_levels is not None:
            return existing["id"]
        run_id = existing["id"]
        conn.execute(
            "UPDATE vap_run SET computed_at = ?, poc = NULL, va_low = NULL, "
            "va_high = NULL, total_volume = 0, high_volume_nodes = '[]' WHERE id = ?",
            (computed_at, run_id),
        )
        conn.execute("DELETE FROM vap_level WHERE run_id = ?", (run_id,))
        conn.commit()
        return run_id

    if existing is None:
        cur = conn.execute(
            """INSERT INTO vap_run
               (symbol_id, window_minutes, as_of, computed_at,
                poc, va_low, va_high, total_volume, high_volume_nodes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(symbol_id), int(window_minutes), as_of, computed_at,
             profile.get("poc"), area[0], area[1],
             float(profile.get("total_volume") or 0.0),
             json.dumps(nodes)),
        )
        run_id = cur.lastrowid
    else:
        run_id = existing["id"]
        conn.execute(
            """UPDATE vap_run
               SET computed_at = ?, poc = ?, va_low = ?, va_high = ?,
                   total_volume = ?, high_volume_nodes = ?
               WHERE id = ?""",
            (computed_at, profile.get("poc"), area[0], area[1],
             float(profile.get("total_volume") or 0.0),
             json.dumps(nodes), run_id),
        )
        conn.execute("DELETE FROM vap_level WHERE run_id = ?", (run_id,))

    conn.executemany(
        "INSERT INTO vap_level (run_id, price, volume, ticks) VALUES (?, ?, ?, ?)",
        [(run_id, float(b["price"]), float(b["volume"]), int(b["ticks"]))
         for b in profile.get("bins") or []],
    )
    conn.commit()
    return run_id


def save_profiles(conn, symbol_id, profiles, as_of, computed_at=None):
    """Persist one stop's worth of profiles, keyed by window minutes.

    `profiles` is `analytics.profiles_by_window`'s output, so the whole 5-minute
    tick - every lookback for one symbol - lands in one transaction.
    """
    computed_at = computed_at or _now()
    ids = {}
    for window, profile in profiles.items():
        ids[int(window)] = save_profile(
            conn, symbol_id, window, as_of, profile, computed_at
        )
    return ids


def load_profile(conn, symbol_id, window_minutes, as_of=None):
    """The stored profile for a window. Omitted `as_of` means the newest stop."""
    params = [str(symbol_id), int(window_minutes)]
    where = "symbol_id = ? AND window_minutes = ?"
    if as_of is not None:
        where += " AND as_of = ?"
        params.append(as_of)

    run = conn.execute(
        f"SELECT * FROM vap_run WHERE {where} ORDER BY as_of DESC LIMIT 1", params
    ).fetchone()
    if run is None:
        return None

    levels = conn.execute(
        "SELECT price, volume, ticks FROM vap_level WHERE run_id = ? ORDER BY price ASC",
        (run["id"],),
    ).fetchall()

    return {
        "symbol_id": run["symbol_id"],
        "window_minutes": run["window_minutes"],
        "as_of": run["as_of"],
        "computed_at": run["computed_at"],
        "poc": run["poc"],
        "value_area": [run["va_low"], run["va_high"]]
        if run["va_low"] is not None else None,
        "total_volume": run["total_volume"],
        "high_volume_nodes": json.loads(run["high_volume_nodes"] or "[]"),
        "bins": [{"price": r["price"], "volume": r["volume"], "ticks": r["ticks"]}
                 for r in levels],
    }


def prune(conn, keep=500):
    """Keep the newest `keep` stops per (symbol, window); delete the rest."""
    conn.execute(
        """DELETE FROM vap_run
           WHERE id NOT IN (
               SELECT id FROM (
                   SELECT id, ROW_NUMBER() OVER (
                       PARTITION BY symbol_id, window_minutes
                       ORDER BY as_of DESC
                   ) AS rank
                   FROM vap_run
               ) WHERE rank <= ?
           )""",
        (int(keep),),
    )
    # Foreign keys cascade from vap_run, but the pragma is per-connection.
    conn.commit()


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
