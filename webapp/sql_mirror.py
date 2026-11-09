"""Mirror the local `vap_run` table into the Fabric-managed SQL database.

`vap_run` is the durable volume-at-price record and it lives in a local SQLite
file. The Fabric-managed SQL database `ctrader_metadata` is where the rest of
the platform's analytical state lives, so the daily job here copies `vap_run`
across and keeps the two in step.

There are no secrets anywhere in this module. The VM runs as a **system-assigned
managed identity**, and `DefaultAzureCredential` (the same credential the Kusto
reader already uses) mints a `https://database.windows.net/` token for the SQL
endpoint. The identity must first be provisioned **inside** the database as a
contained user, or the server rejects the login - see `_grant_hint` below, which
turns that one specific failure into the exact statement an administrator has to
run instead of a bare ODBC error.

The copy is **idempotent**: the mirror's natural key is the same
`(symbol_id, window_minutes, as_of)` triple that `db.py` makes unique locally, so
a re-run on the same day (or a backfill over a range already sent) updates the
rows in place rather than appending duplicates. The local `id` is carried purely
for provenance - it is a SQLite AUTOINCREMENT value that can be reused after a
prune, so it is never the key.
"""
import struct

from azure.identity import DefaultAzureCredential

# The SQL Server ODBC driver that is installed on the box.
DRIVER = "ODBC Driver 18 for SQL Server"
# The SQL Database token scope. Not a config value: it is fixed by the service.
SQL_SCOPE = "https://database.windows.net/.default"

# The mirrored columns, in order. `id` is provenance only; the key is the
# (symbol_id, window_minutes, as_of) triple, mirroring db.py's UNIQUE constraint.
COLUMNS = (
    "id", "symbol_id", "window_minutes", "as_of", "computed_at",
    "poc", "va_low", "va_high", "total_volume", "high_volume_nodes",
)
KEY_COLUMNS = ("symbol_id", "window_minutes", "as_of")

# What the mirror's columns look like on the SQL side. `high_volume_nodes` stays
# an NVARCHAR holding the same JSON document the local table stores, so a reader
# can parse it exactly as it does today.
COLUMN_DDL = {
    "id": "BIGINT NULL",
    "symbol_id": "NVARCHAR(64) NOT NULL",
    "window_minutes": "INT NOT NULL",
    "as_of": "NVARCHAR(40) NOT NULL",
    "computed_at": "NVARCHAR(40) NOT NULL",
    "poc": "FLOAT NULL",
    "va_low": "FLOAT NULL",
    "va_high": "FLOAT NULL",
    "total_volume": "FLOAT NOT NULL",
    "high_volume_nodes": "NVARCHAR(MAX) NOT NULL",
}


class SqlMirrorError(RuntimeError):
    """A mirroring failure that the caller should surface as-is."""


def _token_bytes(token):
    """Pack an access token the way the SQL Server ODBC driver expects it.

    The driver takes the token through `SQL_COPT_SS_ACCESS_TOKEN` (1256) as a
    length-prefixed UTF-16LE blob: a 4-byte little-endian length followed by the
    UTF-16LE encoded token, no NUL terminator.
    """
    encoded = token.encode("utf-16-le")
    return struct.pack("<I", len(encoded)) + encoded


# The ODBC connection attribute that carries an Entra access token.
SQL_COPT_SS_ACCESS_TOKEN = 1256


def _connection_string(cfg):
    return (
        f"DRIVER={{{DRIVER}}};"
        f"SERVER={cfg.sql_server},1433;"
        f"DATABASE={cfg.sql_database};"
        "Encrypt=yes;TrustServerCertificate=no;"
    )


def connect(cfg, credential=None):
    """Open the mirror database as the box's managed identity.

    The token goes in through `attrs_before` - the `SQL_COPT_SS_ACCESS_TOKEN`
    attribute - rather than pyodbc's `access_token=` keyword. Against Fabric SQL
    Database the keyword form drops the token and the server rejects the login
    with 18456 ("Login failed for user ''"), even for an identity that is fully
    provisioned; the attribute form authenticates. Verified against
    ctrader_metadata: same connection string, same credential, only the
    `attrs_before` path returns rows.

    Raises `SqlMirrorError` with the administrator's next step when the login is
    refused, which is the expected state until the identity has been granted a
    user inside the database.
    """
    import pyodbc  # imported here so the module loads without the driver present

    credential = credential or DefaultAzureCredential()
    token = credential.get_token(SQL_SCOPE).token
    try:
        return pyodbc.connect(
            _connection_string(cfg),
            attrs_before={SQL_COPT_SS_ACCESS_TOKEN: _token_bytes(token)},
            timeout=30,
        )
    except pyodbc.Error as exc:
        raise SqlMirrorError(_explain(exc, cfg)) from exc



def _explain(exc, cfg):
    """Turn the ODBC failure into something actionable.

    `Login failed for user ''` (18456) is the signature of an identity that can
    reach the server but has no user in the database - by far the most likely
    failure here, and the one with a one-line fix.
    """
    text = str(exc)
    base = f"cannot connect to {cfg.sql_server}/{cfg.sql_database}: {text}"
    if "18456" in text or "Login failed" in text:
        return base + "\n" + _grant_hint(cfg)
    return base


def _grant_hint(cfg):
    """The exact statement an administrator has to run, for this database."""
    return (
        "The managed identity is not provisioned in the database yet. Have an "
        "administrator run:\n"
        "    CREATE USER [axiomavm01] FROM EXTERNAL PROVIDER;\n"
        "    ALTER ROLE db_datareader ADD MEMBER [axiomavm01];\n"
        "    ALTER ROLE db_datawriter ADD MEMBER [axiomavm01];\n"
        "    ALTER ROLE db_ddladmin  ADD MEMBER [axiomavm01];  -- only if the job "
        "creates its own table\n"
        f"against [{cfg.sql_database}]."
    )


def ensure_table(conn, cfg):
    """Create the mirror table and its key index if they are not there yet."""
    qualified = f"[{cfg.sql_schema}].[{cfg.sql_table}]"
    columns = ", ".join(f"[{name}] {COLUMN_DDL[name]}" for name in COLUMNS)
    keys = ", ".join(f"[{name}]" for name in KEY_COLUMNS)

    if conn.execute(
        "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
        (cfg.sql_schema, cfg.sql_table),
    ).fetchone() is None:
        conn.execute(f"CREATE TABLE {qualified} ({columns})")
        conn.commit()

    index = f"IX_{cfg.sql_table}_key"
    if conn.execute(
        "SELECT 1 FROM sys.indexes WHERE name = ? AND object_id = OBJECT_ID(?)",
        (index, qualified),
    ).fetchone() is None:
        # A unique index on the natural key is what makes the MERGE's ON clause
        # safe, and what stops a duplicate slipping in from any other writer.
        conn.execute(f"CREATE UNIQUE INDEX [{index}] ON {qualified} ({keys})")
        conn.commit()


def read_runs(conn):
    """Every recorded stop, oldest first, as plain tuples in COLUMNS order."""
    rows = conn.execute(
        f"SELECT {', '.join(COLUMNS)} FROM vap_run ORDER BY as_of ASC, id ASC"
    ).fetchall()
    return [tuple(row) for row in rows]


def upsert_runs(conn, cfg, rows):
    """Write `rows` into the mirror, idempotently.

    The statement is a set-based MERGE on the natural key: a row already present
    is updated (a stop re-derived inside its 5-minute slot can legitimately
    change), a new one is inserted. Only the mirrored columns are touched, so
    nothing else in a target row is disturbed. Returns (inserted, updated).
    """
    if not rows:
        return 0, 0

    qualified = f"[{cfg.sql_schema}].[{cfg.sql_table}]"
    columns = ", ".join(f"[{name}]" for name in COLUMNS)
    placeholders = ", ".join("?" for _ in COLUMNS)
    # The source columns and the insert list have the same shape, but the
    # `VALUES` clause must read from the source alias, not repeat the bare
    # bracketed names (which T-SQL would reject as unknown columns).
    source = ", ".join(f"s.[{name}]" for name in COLUMNS)
    # Everything except the key columns is refreshed on a match.
    updates = ", ".join(
        f"t.[{n}] = s.[{n}]" for n in COLUMNS if n not in KEY_COLUMNS
    )
    on = " AND ".join(f"t.[{k}] = s.[{k}]" for k in KEY_COLUMNS)

    merge = (
        f"MERGE {qualified} AS t\n"
        f"USING (VALUES ({placeholders})) AS s ({columns})\n"
        f"ON ({on})\n"
        f"WHEN MATCHED THEN UPDATE SET {updates}\n"
        f"WHEN NOT MATCHED THEN INSERT ({columns}) VALUES ({source});"
    )

    inserted = updated = 0
    cursor = conn.cursor()
    for row in rows:
        # `@@ROWCOUNT` is read in the same batch as the MERGE, so it reports that
        # statement's effect: 1 per inserted row, 2 per updated row. Reading it
        # explicitly is more portable across drivers than trusting `rowcount`.
        cursor.execute(merge + "\nSELECT @@ROWCOUNT;", row)
        affected = cursor.fetchone()[0]
        if affected >= 2:
            updated += 1
        else:
            inserted += 1
    conn.commit()
    return inserted, updated
