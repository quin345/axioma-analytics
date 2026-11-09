"""Run from the webapp folder:  python -m tests.test_backend"""
import dataclasses
import math
import os
import sys
import tempfile
import types
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub the Azure SDK so the module imports without a real login.
azure = types.ModuleType("azure")
identity = types.ModuleType("azure.identity")
kusto = types.ModuleType("azure.kusto")
kusto_data = types.ModuleType("azure.kusto.data")
identity.DefaultAzureCredential = lambda: None
kusto_data.KustoClient = lambda kcsb: None
kusto_data.KustoConnectionStringBuilder = types.SimpleNamespace(with_azure_token_credential=lambda *a: None)
sys.modules.update({"azure": azure, "azure.identity": identity, "azure.kusto": kusto, "azure.kusto.data": kusto_data})

os.environ.update({
    "KUSTO_CLUSTER_URI": "https://example.kusto.fabric.microsoft.com",
    "KUSTO_DATABASE": "db",
    "KUSTO_TABLE": "order_book_aggregate",
    "PRICE_SCALE": "100",
    "FRONTEND_DIST": "/tmp/no-such-dist",
    # A real file, not :memory: - each db.connect() opens its own connection, so
    # an in-memory path would give every call an empty database. Unique per
    # process so a re-run never sees a previous run's rows: the "no record yet,
    # so derive from Kusto" test below would otherwise read a stale local stop.
    "DB_PATH": os.path.join(
        tempfile.gettempdir(), f"axioma-test-vap-{os.getpid()}.db"
    ),
})

import analytics  # noqa: E402
import app as app_module  # noqa: E402
import db as db_module  # noqa: E402
import sql_mirror  # noqa: E402


def bucket(i):
    return f"2026-11-06T10:00:{i * 10:02d}Z"


def series_row(i, bid, ask, events):
    return {"bucket": bucket(i), "best_bid": bid, "best_ask": ask, "events": events,
            "rel_spread": None, "rel_vwap_spread": None}


# Mids 100, 101, 102, 101 with spread 0.5 each.
ROWS = [
    series_row(0, 99.75, 100.25, 10),
    series_row(1, 100.75, 101.25, 20),
    series_row(2, 101.75, 102.25, 30),
    series_row(3, 100.75, 101.25, 40),
]


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


def test_kpis():
    k = analytics.kpis(ROWS, 10)
    assert k["ticks"] == 100
    assert close(k["last_mid"], 101.0)
    assert close(k["change_abs"], 1.0)
    assert close(k["change_pct"], 1.0)           # 100 -> 101
    assert close(k["high"], 102.0) and close(k["low"], 100.0)
    assert close(k["avg_spread"], 0.5)
    rets = [math.log(101 / 100), math.log(102 / 101), math.log(101 / 102)]
    m = sum(rets) / 3
    sd = math.sqrt(sum((x - m) ** 2 for x in rets) / 2)
    assert close(k["realised_vol_pct"], sd * math.sqrt(365 * 24 * 360) * 100)


def test_kpis_empty_book_side():
    rows = [series_row(0, None, None, 5)]
    k = analytics.kpis(rows, 10)
    assert k["ticks"] == 5 and k["last_mid"] is None and k["realised_vol_pct"] is None


def test_spread():
    s = analytics.spread(ROWS)
    assert len(s["series"]) == 4
    assert close(s["mean"], 0.5) and close(s["median"], 0.5)
    assert close(s["series"][0]["rel_spread"], 0.5 / 100)


def test_rolling_volatility_alignment():
    v = analytics.rolling_volatility(ROWS, 10, 2)
    assert len(v) == 2                       # 3 returns, window 2 -> 2 values
    assert v[0]["bucket"] == bucket(2)       # window ends at mids[2]
    assert v[1]["bucket"] == bucket(3)


def test_autocorrelation_alternating():
    # Alternating up/down mids give strongly negative lag-1 autocorrelation.
    mids = [100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100]
    rows = [series_row(i, m - 0.25, m + 0.25, 1) for i, m in enumerate(mids)]
    acf = analytics.autocorrelation(rows, 2)
    assert acf["lags"][0]["lag"] == 1 and acf["lags"][0]["acf"] < -0.8
    assert acf["band"] > 0


def test_tick_gaps():
    rows = [
        {"epoch_ms": 1000, "events": 3},   # 3 events share one ms: 2 are repeats
        {"epoch_ms": 1003, "events": 1},   # gap 3 ms
        {"epoch_ms": 1020, "events": 1},   # gap 17 ms
    ]
    g = analytics.tick_gaps(rows)
    assert g["events"] == 5 and g["distinct_timestamps"] == 3
    assert close(g["shared_timestamp_share"], 2 / 5)
    assert close(g["median_gap_ms"], 10)
    counts = {(b["from_ms"], b["to_ms"]): b["count"] for b in g["bins"]}
    assert counts[(2, 5)] == 1 and counts[(10, 20)] == 1


class FakeReader:
    """Returns fixtures based on which query was asked for."""
    def __init__(self):
        self.calls = []

    def run(self, query):
        self.calls.append(query)
        if "hourofday" in query:
            return [{"hour": 10, "events": 5}, {"hour": 11, "events": 7}]
        if "epoch_ms" in query:
            return [{"epoch_ms": 1000, "events": 3}, {"epoch_ms": 1003, "events": 1}]
        if "VOLUME_AT_PRICE" in query:
            return [dict(r) for r in VAP_RAW]
        # Fresh copies each time: the route scales rows in place, as a real client gives new rows.
        return [dict(r) for r in ROWS_RAW]


# Kusto returns raw prices (x PRICE_SCALE); the route divides them back down.
ROWS_RAW = [
    {**r, "best_bid": r["best_bid"] * 100, "best_ask": r["best_ask"] * 100} for r in ROWS
]


def test_routes():
    fake = FakeReader()
    with mock.patch.object(app_module, "reader", fake):
        client = app_module.app.test_client()
        r = client.get("/api/kpis?symbol=136&minutes=240")
        assert r.status_code == 200 and r.json["ticks"] == 100
        assert "ago(240m)" in fake.calls[-1]
        assert client.get("/api/spread?symbol=136").json["median"] == 0.5
        assert len(client.get("/api/volatility?symbol=136&window=2").json) == 2
        assert client.get("/api/acf?symbol=136&max_lag=2").json["lags"][0]["lag"] == 1
        act = client.get("/api/activity?symbol=136&days=2").json
        assert act == [{"hour": 10, "events": 5}, {"hour": 11, "events": 7}]
        tg = client.get("/api/tick-gaps?symbol=136").json
        assert tg["events"] == 4 and tg["distinct_timestamps"] == 2

        # Input validation still applies to new routes.
        assert client.get("/api/kpis?symbol=abc").status_code == 400
        assert client.get("/api/volatility?symbol=136&window=1").status_code == 400
        assert client.get("/api/acf?symbol=136&max_lag=500").status_code == 400
        assert client.get("/api/activity?symbol=136&days=0").status_code == 400
        assert client.get("/api/volume-at-price?symbol=136&window=30").status_code == 400


# --- Volume-at-price ---------------------------------------------------------

def vap_row(i, mid, events):
    return {"bucket": bucket(i), "best_bid": mid - 0.25, "best_ask": mid + 0.25,
            "events": events, "volume": None}


# A frame whose rows sit on two price levels, so slicing a window changes the
# ladder and the POC: binning is over each window's own range, never one shared grid.
VAP_ROWS = (
    [vap_row(i, 100.0, 50) for i in range(12)]        # dense at 100
    + [vap_row(i + 12, 102.0, 2) for i in range(12)]  # sparse at 102
)

# Raw prices x PRICE_SCALE (100), as Kusto would return them.
VAP_RAW = [
    {**r, "best_bid": r["best_bid"] * 100, "best_ask": r["best_ask"] * 100}
    for r in VAP_ROWS
]


def test_volume_at_price_poc_and_value_area():
    p = analytics.volume_at_price(VAP_ROWS, 1.0)
    assert len(p["bins"]) == analytics.VAP_BINS
    # Rows sit at 100 and 102; the POC bin is the one holding 100 (the cluster
    # with 600 of the 624 units), labelled by its midpoint.
    assert close(p["poc"], 100.0, 0.05)
    assert p["total_volume"] == 12 * 50 + 12 * 2
    prices = [b["price"] for b in p["bins"]]
    assert prices == sorted(prices)          # ascending, as value_area requires
    lo, hi = p["value_area"]
    assert lo <= p["poc"] <= hi
    # The 100 cluster alone is well past the 70% share, so the value area stays
    # tight around the POC rather than spanning the whole 100..102 range.
    assert close(lo, p["poc"], 0.05) and close(hi, p["poc"], 0.05)


def test_volume_at_price_flat_window_collapses():
    rows = [vap_row(i, 100.0, 3) for i in range(5)]
    p = analytics.volume_at_price(rows, 1.0)
    assert len(p["bins"]) == 1
    assert close(p["poc"], 100.0) and p["total_volume"] == 15


def test_volume_at_price_no_book_is_empty():
    p = analytics.volume_at_price(
        [{"bucket": bucket(0), "best_bid": None, "best_ask": None, "events": 5}], 1.0)
    assert p["bins"] == [] and p["poc"] is None


def test_profiles_by_window_are_distinct():
    # Spread the frame over hours so a 60-minute slice genuinely drops the older
    # rows: the newest hour holds only the 102 cluster, the full frame also holds
    # the busier 100 cluster, so the two POCs cannot coincide.
    def row(minute, mid, events):
        h, m = divmod(minute, 60)
        stamp = f"2026-11-06T{10 + h:02d}:{m:02d}:00+00:00"
        return {"bucket": stamp, "best_bid": mid - 0.25, "best_ask": mid + 0.25,
                "events": events, "volume": None}

    rows = (
        [row(i, 100.0, 50) for i in range(0, 180, 15)]      # 0-180 min: dense at 100
        + [row(i, 102.0, 5) for i in range(190, 300, 15)]   # newest: sparse at 102
    )
    profiles = analytics.profiles_by_window(rows, 1.0, (60, 240))
    assert set(profiles) == {60, 240}
    # The 60-min slice sees only the 102 cluster, so its POC lands there; the
    # 240-min slice also holds the busier 100 cluster and lands there instead.
    assert close(profiles[60]["poc"], 102.0, 0.05)
    assert close(profiles[240]["poc"], 100.0, 0.05)
    assert profiles[60]["total_volume"] != profiles[240]["total_volume"]


# --- Local record-keeping database -------------------------------------------

def test_db_roundtrip_and_upsert():
    conn = db_module.connect(":memory:")
    try:
        profile = analytics.volume_at_price(VAP_ROWS, 1.0)
        db_module.save_profile(conn, "136", 60, "2026-11-06T10:00:00+00:00", profile)
        # A second run inside the same stop must upsert, not append.
        db_module.save_profile(conn, "136", 60, "2026-11-06T10:00:00+00:00", profile)

        assert conn.execute("SELECT COUNT(*) FROM vap_run").fetchone()[0] == 1
        stored = db_module.load_profile(conn, "136", 60)
        assert close(stored["poc"], profile["poc"])
        assert len(stored["bins"]) == len(profile["bins"])
        assert stored["value_area"] == profile["value_area"]
    finally:
        conn.close()


def test_db_load_returns_newest_stop():
    conn = db_module.connect(":memory:")
    try:
        profile = analytics.volume_at_price(VAP_ROWS, 1.0)
        db_module.save_profile(conn, "136", 60, "2026-11-06T09:55:00+00:00", profile)
        db_module.save_profile(conn, "136", 60, "2026-11-06T10:00:00+00:00", profile)
        assert db_module.load_profile(conn, "136", 60)["as_of"] == "2026-11-06T10:00:00+00:00"
    finally:
        conn.close()


def test_db_prune_keeps_newest():
    conn = db_module.connect(":memory:")
    try:
        profile = analytics.volume_at_price(VAP_ROWS, 1.0)
        for i in range(5):
            db_module.save_profile(conn, "136", 60, f"2026-11-06T10:0{i}:00+00:00", profile)
        db_module.prune(conn, keep=2)
        stops = [r["as_of"] for r in
                 conn.execute("SELECT as_of FROM vap_run ORDER BY as_of DESC").fetchall()]
        assert stops == ["2026-11-06T10:04:00+00:00", "2026-11-06T10:03:00+00:00"]
        # Levels persist for the runs that survive.
        assert conn.execute("SELECT COUNT(*) FROM vap_level").fetchone()[0] > 0
    finally:
        conn.close()

def test_db_prune_runs_default_keeps_everything():
    """`prune_runs` with no bounds must not touch the durable record."""
    conn = db_module.connect(":memory:")
    try:
        profile = analytics.volume_at_price(VAP_ROWS, 1.0)
        for i in range(5):
            db_module.save_profile(conn, "136", 60, f"2026-11-06T10:0{i}:00+00:00", profile)
        assert db_module.prune_runs(conn, keep=None, before=None) == 0
        assert conn.execute("SELECT COUNT(*) FROM vap_run").fetchone()[0] == 5
    finally:
        conn.close()


def test_db_prune_levels_keeps_runs_and_summary():
    """The whole point of the split: trimming bins must NOT delete the stop.

    An old stop loses its ladder (`bins == []`) but keeps its `vap_run` summary
    (poc / value area / total_volume), which is what downstream analytics reads.
    """
    conn = db_module.connect(":memory:")
    try:
        profile = analytics.volume_at_price(VAP_ROWS, 1.0)
        # Four stops, an hour apart, well in the past.
        stamps = [f"2020-01-01T0{i}:00:00+00:00" for i in range(4)]
        for as_of in stamps:
            db_module.save_profile(conn, "136", 60, as_of, profile)

        runs_before = conn.execute("SELECT COUNT(*) FROM vap_run").fetchone()[0]
        levels_before = conn.execute("SELECT COUNT(*) FROM vap_level").fetchone()[0]
        assert levels_before == runs_before * len(profile["bins"])

        # Keep one hour of bins: every stop is older than that, so all bins go,
        # but not a single run may disappear.
        removed = db_module.prune_levels(conn, keep_hours=1)
        assert removed == levels_before
        assert conn.execute("SELECT COUNT(*) FROM vap_level").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM vap_run").fetchone()[0] == runs_before

        # The stop still loads, with its scalar summary intact and no ladder.
        stored = db_module.load_profile(conn, "136", 60, as_of=stamps[-1])
        assert stored is not None
        assert stored["bins"] == []
        assert close(stored["poc"], profile["poc"])
        assert stored["value_area"] == profile["value_area"]
    finally:
        conn.close()


def test_db_prune_levels_keeps_recent_runs_bins():
    """`keep_runs` keeps the newest stops' bins per (symbol, window) regardless of age."""
    conn = db_module.connect(":memory:")
    try:
        profile = analytics.volume_at_price(VAP_ROWS, 1.0)
        stamps = [f"2026-11-06T10:0{i}:00+00:00" for i in range(5)]
        for as_of in stamps:
            db_module.save_profile(conn, "136", 60, as_of, profile)

        db_module.prune_levels(conn, keep_runs=2)
        # Only the two newest stops still have bins.
        run_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM vap_run ORDER BY as_of DESC"
            ).fetchall()
        ]
        with_bins = {
            r["run_id"] for r in conn.execute(
                "SELECT DISTINCT run_id FROM vap_level"
            ).fetchall()
        }
        assert with_bins == set(run_ids[:2])
        # Every stop row survives - only its detail was trimmed.
        assert conn.execute("SELECT COUNT(*) FROM vap_run").fetchone()[0] == 5
    finally:
        conn.close()


def test_db_empty_profile_does_not_clobber_good_stop():
    conn = db_module.connect(":memory:")
    try:
        good = analytics.volume_at_price(VAP_ROWS, 1.0)
        empty = analytics.volume_at_price([], 1.0)
        stop = "2026-11-06T10:00:00+00:00"
        db_module.save_profile(conn, "136", 60, stop, good)
        # A later run inside the same stop that derived nothing (feed empty)
        # must leave the good ladder in place.
        db_module.save_profile(conn, "136", 60, stop, empty)

        stored = db_module.load_profile(conn, "136", 60)
        assert len(stored["bins"]) == len(good["bins"])
        assert close(stored["poc"], good["poc"])

        # A brand-new stop that derived nothing is not recorded at all, so it
        # cannot shadow the good stop that is already on file.
        db_module.save_profile(conn, "136", 60, "2026-11-06T10:05:00+00:00", empty)
        newest = db_module.load_profile(conn, "136", 60)
        assert close(newest["poc"], good["poc"])
        assert conn.execute("SELECT COUNT(*) FROM vap_run").fetchone()[0] == 1
    finally:
        conn.close()


def test_vap_routes_local_then_refresh():
    fake = FakeReader()
    with mock.patch.object(app_module, "reader", fake):
        client = app_module.app.test_client()
        # No record yet: the first read derives from Kusto and records it.
        first = client.get("/api/volume-at-price?symbol=136&window=60")
        assert first.status_code == 200
        assert first.json["source"] == "kusto" and first.json["poc"] is not None

        # Now it is on record: the next read is local, with no further Kusto call.
        before = len(fake.calls)
        second = client.get("/api/volume-at-price?symbol=136&window=60")
        assert second.json["source"] == "local"
        assert len(fake.calls) == before

        # Every lookback is recorded, each with its own POC.
        windows = client.get("/api/volume-at-price/windows?symbol=136").json
        assert set(windows) == {"60", "120", "180", "240"}

        posted = client.post("/api/volume-at-price/refresh?symbol=136")
        assert posted.status_code == 200
        assert set(posted.json["windows"]) == {"60", "120", "180", "240"}


# --- Maintenance / holding page ----------------------------------------------

def test_holding_page_when_maintenance_on():
    """MAINTENANCE_MODE serves the holding page at / with a 503, so proxies and
    uptime checks do not cache an under-construction site as healthy."""
    maintenance_cfg = dataclasses.replace(app_module.cfg, maintenance=True)
    with mock.patch.object(app_module, "cfg", maintenance_cfg):
        client = app_module.app.test_client()
        r = client.get("/")
        assert r.status_code == 503
        assert b"Service not available" in r.data
        assert b"still working on it" in r.data


def test_dashboard_served_when_maintenance_off():
    """With the flag off, / is the built dashboard again."""
    live_cfg = dataclasses.replace(app_module.cfg, maintenance=False)
    with mock.patch.object(app_module, "cfg", live_cfg):
        client = app_module.app.test_client()
        r = client.get("/")
        # The stub FRONTEND_DIST does not exist, so this is a 404 from the
        # static send - the point is that it is no longer the 503 holding page.
        assert r.status_code != 503
        assert b"Service not available" not in r.data


def test_unavailable_page_is_always_reachable():
    """The page is served at its own path regardless of the flag, so it can be
    previewed while the dashboard is live."""
    live_cfg = dataclasses.replace(app_module.cfg, maintenance=False)
    with mock.patch.object(app_module, "cfg", live_cfg):
        r = app_module.app.test_client().get("/unavailable")
        assert r.status_code == 200
        assert b"Service not available" in r.data


def test_holding_page_is_self_contained():
    """It must render while the dashboard is being worked on, so it carries no
    dependency on the built bundle."""
    page = os.path.join(app_module.STATIC_DIR, "maintenance.html")
    with open(page, encoding="utf-8") as handle:
        html = handle.read()
    assert "<style>" in html
    assert "/assets/" not in html          # no built JS/CSS
    assert "AXIOMA" in html


# --- The Fabric SQL mirror ----------------------------------------------------
#
# These stay offline: the ODBC connection is faked, so the suite runs on a box
# with no SQL target and no login. What is worth pinning down is the *statement*
# the mirror builds - in particular that the merge is keyed on the natural
# triple rather than the reusable local `id`, and that a login failure surfaces
# the administrator's grant rather than a bare ODBC error.

def _sql_cfg(server="sql.example.com", database="meta"):
    return dataclasses.replace(
        app_module.cfg, sql_server=server, sql_database=database,
        sql_schema="dbo", sql_table="vap_run",
    )


class FakeCursor:
    """Records executed statements and replays canned results.

    `rowcounts` are the values `@@ROWCOUNT` should report, consumed one per
    MERGE - 1 for an inserted row, 2 for an updated one.
    """

    def __init__(self, conn):
        self._conn = conn
        self.rowcount = -1
        self._result = None

    def execute(self, sql, params=None):
        self._conn.statements.append((sql, params))
        self.rowcount = self._conn.rowcounts.pop(0) if self._conn.rowcounts else 1
        self._result = (self.rowcount,)
        return self

    def fetchone(self):
        return self._result

    def fetchall(self):
        return []

    def close(self):
        pass


class FakeConnection:
    def __init__(self, rowcounts=None):
        self.statements = []
        self.rowcounts = list(rowcounts or [])
        self.commits = 0

    def execute(self, sql, params=None):
        return FakeCursor(self).execute(sql, params)

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def test_sql_mirror_merge_is_keyed_on_the_natural_triple():
    """The local `id` is AUTOINCREMENT and reused after a prune, so it must
    never be the merge key - the (symbol, window, as_of) triple must be."""
    conn = FakeConnection(rowcounts=[1])
    cfg = _sql_cfg()

    inserted, updated = sql_mirror.upsert_runs(
        conn, cfg, [(1, "136", 60, "2026-11-06T10:00:00+00:00", "2026-11-06T10:00:01+00:00",
                     100.0, 99.0, 101.0, 500.0, "[]")]
    )
    merge, params = conn.statements[0]
    assert "MERGE [dbo].[vap_run]" in merge
    assert "SELECT @@ROWCOUNT;" in merge
    assert "t.[symbol_id] = s.[symbol_id]" in merge
    assert "t.[window_minutes] = s.[window_minutes]" in merge
    assert "t.[as_of] = s.[as_of]" in merge
    # The key must not include id.
    assert "t.[id] = s.[id]" not in merge.split("WHEN MATCHED")[0]
    # The insert reads the source alias, never a bare bracketed column list.
    assert "VALUES (s.[id], s.[symbol_id]" in merge
    assert inserted == 1 and updated == 0


def test_sql_mirror_upsert_rowcount_classification():
    """A MERGE reports 1 for an insert and 2 for an update."""
    conn = FakeConnection(rowcounts=[2, 2, 1])
    rows = [(i, "136", 60, f"2026-11-06T10:0{i}:00+00:00", "x", 1.0, 1.0, 1.0, 1.0, "[]")
            for i in range(3)]
    inserted, updated = sql_mirror.upsert_runs(conn, _sql_cfg(), rows)
    assert updated == 2 and inserted == 1


def test_sql_mirror_empty_write_is_a_noop():
    conn = FakeConnection()
    assert sql_mirror.upsert_runs(conn, _sql_cfg(), []) == (0, 0)
    assert conn.statements == []


def test_sql_mirror_read_runs_preserves_column_order():
    """`read_runs` must select COLUMNS in the same order the DDL declares, so
    the tuples it hands the MERGE line up with the parameter list."""
    conn = FakeConnection()
    calls = []
    def capture(sql, params=None):
        calls.append(sql)
        return types.SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])
    conn.execute = capture
    sql_mirror.read_runs(conn)
    sql = calls[0]
    assert "FROM vap_run" in sql
    # Order matters: id first, and the key triple present.
    positions = [sql.index(name) for name in sql_mirror.COLUMNS]
    assert positions == sorted(positions)
    for name in sql_mirror.KEY_COLUMNS:
        assert name in sql


def test_sql_mirror_login_failure_names_the_grant():
    """An unprovisioned identity must produce the fix, not a raw ODBC error."""
    cfg = _sql_cfg()

    class Failing:
        def get_token(self, scope):
            return types.SimpleNamespace(token="fake")

    import pyodbc as real_pyodbc
    original = real_pyodbc.connect
    def boom(*a, **k):
        raise real_pyodbc.InterfaceError(
            "('28000', \"[28000] [Microsoft][ODBC Driver 18 for SQL Server]"
            "[SQL Server]Login failed for user ''. (18456) (SQLDriverConnect)\")"
        )
    real_pyodbc.connect = boom
    try:
        try:
            sql_mirror.connect(cfg, credential=Failing())
        except sql_mirror.SqlMirrorError as exc:
            text = str(exc)
            assert "CREATE USER [axiomavm01] FROM EXTERNAL PROVIDER" in text
            assert "db_datawriter" in text
            assert cfg.sql_database in text
        else:
            raise AssertionError("expected SqlMirrorError")
    finally:
        real_pyodbc.connect = original


def test_sql_mirror_ensure_table_creation_sql():
    """The mirror DDL keeps the JSON column textual and keys the table.

    `ensure_table` probes INFORMATION_SCHEMA and sys.indexes first; both probes
    must answer "missing" for the DDL to be emitted.
    """
    conn = FakeConnection()
    # fetchone() returning None == "not found", for the table and the index.
    emitted = []
    def capture(sql, params=None):
        emitted.append(sql)
        return types.SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])
    conn.execute = capture
    sql_mirror.ensure_table(conn, _sql_cfg())
    ddl = " ".join(emitted)
    assert "CREATE TABLE [dbo].[vap_run]" in ddl
    assert "[high_volume_nodes] NVARCHAR(MAX) NOT NULL" in ddl
    assert "[symbol_id] NVARCHAR(64) NOT NULL" in ddl
    assert "CREATE UNIQUE INDEX [IX_vap_run_key]" in ddl
    assert "ON [dbo].[vap_run] ([symbol_id], [window_minutes], [as_of])" in ddl


def test_sql_mirror_token_is_packed_for_attrs_before():
    """The token must be a length-prefixed UTF-16LE blob.

    pyodbc's `access_token=` keyword is silently ignored against Fabric SQL
    Database, so `connect` passes the token through `attrs_before` - which means
    the packing itself is load-bearing: a wrong length prefix, or a NUL
    terminator, and the server answers 18456.
    """
    import struct

    packed = sql_mirror._token_bytes("abc")
    encoded = "abc".encode("utf-16-le")
    assert isinstance(packed, bytes)
    assert packed[:4] == struct.pack("<I", len(encoded))
    assert packed[4:] == encoded
    # No NUL terminator.
    assert packed[-2:] != b"\x00\x00"


# --- The local symbol mapping snapshot ----------------------------------------
#
# `snapshot_symbols` reads dbo.symbols from Fabric and swaps the local
# `symbol_mapping` table. These stay offline: the read is faked, and the point
# worth pinning down is that the mapping is keyed on the (broker, account,
# symbol) triple and that a re-run replaces rather than appends.

def test_db_replace_symbol_mapping_is_keyed_on_the_triple():
    """The same symbol id on two accounts is two rows, never a collision."""
    conn = db_module.connect(":memory:")
    rows = [
        ("acgmarkets", "49208255", 136, "XAUUSD.pro", "Metals (Pro)"),
        ("icmarketssc", "48664595", 136, "GOLD", "Metals"),
    ]
    written = db_module.replace_symbol_mapping(conn, rows, captured_at="2026-11-09T00:00:00Z")
    assert written == 2
    got = db_module.load_symbol_mapping(conn)
    assert [(r["broker"], r["ctid_trader_account"], r["symbol_id"]) for r in got] == [
        ("acgmarkets", "49208255", 136),
        ("icmarketssc", "48664595", 136),
    ]
    assert all(r["captured_at"] == "2026-11-09T00:00:00Z" for r in got)
    # The broker's own labels collapse onto the canonical classes.
    assert [r["asset_class"] for r in got] == ["Commodities", "Commodities"]


def test_db_replace_symbol_mapping_replaces_not_appends():
    """A snapshot is a replace: re-running it must not accumulate old rows."""
    conn = db_module.connect(":memory:")
    db_module.replace_symbol_mapping(
        conn, [("acgmarkets", "49208255", 1, "EURUSD", "Forex (Spot)"),
               ("acgmarkets", "49208255", 2, "GBPUSD", "Forex (Spot)")])
    # The second pull saw only one of them (a symbol was delisted upstream).
    db_module.replace_symbol_mapping(
        conn, [("acgmarkets", "49208255", 1, "EURUSD", "Forex (Spot)")])
    got = db_module.load_symbol_mapping(conn)
    assert len(got) == 1 and got[0]["symbol_name"] == "EURUSD"


def test_db_symbol_mapping_can_be_narrowed_by_account():
    conn = db_module.connect(":memory:")
    db_module.replace_symbol_mapping(conn, [
        ("acgmarkets", "49208255", 1, "EURUSD", "Forex (Spot)"),
        ("icmarketssc", "48664595", 1, "EURUSD", "Forex"),
    ])
    got = db_module.load_symbol_mapping(conn, broker="icmarketssc")
    assert len(got) == 1 and got[0]["ctid_trader_account"] == "48664595"


def test_canonical_asset_class_maps_every_broker_label():
    """Every class the brokers actually ship lands on a canonical value.

    These are the real labels from `dbo.asset_classes`, across both brokers.
    """
    expected = {
        "Forex": "FX",
        "Forex (Pro)": "FX",
        "Forex (Raw)": "FX",
        "Forex (Spot)": "FX",
        "Indices": "Index",
        "Indices (Pro)": "Index",
        "Indices (Raw)": "Index",
        "Metals": "Commodities",
        "Metals (Pro)": "Commodities",
        "Metals (Raw)": "Commodities",
        "Oil": "Commodities",
        "Oil (Pro)": "Commodities",
        "Oil (Raw)": "Commodities",
        "Commodities": "Commodities",
        "Futures Commodities": "Commodities",
        "Cryptocurrencies": "Crypto",
        "EU Equities (CFDs)": "Equities",
        "US Equities (CFDs)": "Equities",
    }
    for label, want in expected.items():
        assert db_module.canonical_asset_class(label) == want, label


def test_canonical_asset_class_is_never_off_the_canonical_set():
    """Whatever the broker ships - known, unknown or absent - the answer is one
    of the five (or the explicit Other), never a raw broker label."""
    allowed = set(db_module.ASSET_CLASSES) | {db_module.ASSET_CLASS_UNKNOWN}
    for label in list(db_module.ASSET_CLASS_MAP) + [
        "Bonds", "Futures", "Whatever (Pro)", None, "", "  ",
    ]:
        assert db_module.canonical_asset_class(label) in allowed


def test_canonical_asset_class_leaves_bonds_and_missing_as_other():
    """Bonds and a symbol with no catalogue row have no home in the five."""
    assert db_module.canonical_asset_class("Bonds") == db_module.ASSET_CLASS_UNKNOWN
    assert db_module.canonical_asset_class("Futures") == db_module.ASSET_CLASS_UNKNOWN
    assert db_module.canonical_asset_class(None) == db_module.ASSET_CLASS_UNKNOWN


def test_standardize_symbol_strips_the_account_tier_only():
    """`.pro`/`.raw` are the account tier and come off; a real dot stays."""
    assert db_module.standardize_symbol("AUDCAD.pro") == "AUDCAD"
    assert db_module.standardize_symbol("AUDCAD.raw") == "AUDCAD"
    # Already-standard names are untouched.
    assert db_module.standardize_symbol("AUDCAD") == "AUDCAD"
    assert db_module.standardize_symbol("XAUUSD") == "XAUUSD"
    # A dot that is not a tier suffix is part of the contract, not the account:
    # a blanket rsplit('.') would mangle every one of these.
    for name in ("WTI_H6", "GCZ26_CFD", "Corn_H6", "VIX_F6"):
        assert db_module.standardize_symbol(name) == name
    # Only a trailing suffix is a tier; an interior "pro" is the instrument.
    assert db_module.standardize_symbol("PROTON") == "PROTON"


def test_db_standardized_name_is_derived_on_write():
    """The stored standardized_name is derived, not carried from the source."""
    conn = db_module.connect(":memory:")
    db_module.replace_symbol_mapping(conn, [
        ("acgmarkets", "49208255", 104, "AUDCAD.pro", "Forex (Pro)"),
        ("acgmarkets", "49208255", 204, "AUDCAD.raw", "Forex (Raw)"),
    ])
    got = db_module.load_symbol_mapping(conn)
    # Both tiers collapse to the same instrument.
    assert {r["standardized_name"] for r in got} == {"AUDCAD"}
    assert {r["symbol_name"] for r in got} == {"AUDCAD.pro", "AUDCAD.raw"}


def test_snapshot_symbols_reads_the_source_triple_in_order():
    """`read_symbols` selects the three key columns plus the name and class, in
    order, and normalises a NULL name to the empty string."""
    import snapshot_symbols

    calls = []
    def capture(sql, params=None):
        calls.append(sql)
        return types.SimpleNamespace(
            fetchall=lambda: [("acgmarkets", "49208255", 136, None, "Metals (Pro)")])
    conn = types.SimpleNamespace(execute=capture)
    rows = snapshot_symbols.read_symbols(conn)

    assert "FROM dbo.symbols" in calls[0]
    # The class is resolved through the broker's own catalogue, on the account.
    assert "LEFT JOIN dbo.symbols_category" in calls[0]
    assert "LEFT JOIN dbo.asset_classes" in calls[0]
    assert "sc.ctidTraderAccountId = s.ctidTraderAccountId" in calls[0]
    assert "ORDER BY s.broker, s.ctidTraderAccountId, s.symbolId" in calls[0]
    # An account id is text on the source; it stays text, and a NULL name
    # becomes "" so the local column is never NULL.
    assert rows == [("acgmarkets", "49208255", 136, "", "Metals (Pro)")]


def test_db_symbol_mapping_migrates_an_older_snapshot_table():
    """A database written before the class/name columns existed is widened.

    `CREATE TABLE IF NOT EXISTS` never alters an existing table, so the older
    `symbol_mapping` (no standardized_name / asset_class) has to be migrated or
    every read of the new columns fails.
    """
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # The previous release's table, already populated with a stale snapshot.
    conn.execute(
        "CREATE TABLE symbol_mapping ("
        " broker TEXT NOT NULL, ctid_trader_account TEXT NOT NULL,"
        " symbol_id INTEGER NOT NULL, symbol_name TEXT,"
        " captured_at TEXT NOT NULL,"
        " PRIMARY KEY (broker, ctid_trader_account, symbol_id))"
    )
    conn.execute(
        "INSERT INTO symbol_mapping VALUES ('acgmarkets','49208255',104,'AUDCAD.pro','t')"
    )

    db_module.init_db(conn)  # must not raise, must add the columns

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(symbol_mapping)")}
    assert {"standardized_name", "asset_class"} <= cols
    # The old row survives and the new columns read as NULL until the next pull.
    row = conn.execute(
        "SELECT symbol_name, standardized_name, asset_class FROM symbol_mapping"
    ).fetchone()
    assert row["symbol_name"] == "AUDCAD.pro"
    assert row["standardized_name"] is None and row["asset_class"] is None
    # init_db is idempotent: a second open is a no-op, not a duplicate column.
    db_module.init_db(conn)


# --- The instrument mapping in the app ---------------------------------------
#
# `/api/symbols` and `/api/asset-classes` join the collected (broker, account,
# symbol) triple from the feed to the local `symbol_mapping` snapshot. These stay
# offline: the feed is faked and the mapping is written straight into a temp
# database, so the join logic is exercised without Kusto or Fabric.

class FakeSymbolReader:
    """A feed that collects a fixed set of (broker, account, symbol) rows."""

    def __init__(self, rows):
        self.rows = rows

    def run(self, query):
        return [dict(r) for r in self.rows]


def feed_row(broker, account, symbol_id, events=1):
    return {"broker": broker, "ctidTraderAccountId": account,
            "symbolId": symbol_id, "events": events}


def _mapping_db(rows):
    """A temp database holding exactly `rows` in symbol_mapping."""
    path = os.path.join(tempfile.gettempdir(), f"axioma-test-map-{os.getpid()}.db")
    if os.path.exists(path):
        os.remove(path)
    conn = db_module.connect(path)
    try:
        db_module.replace_symbol_mapping(conn, rows)
    finally:
        conn.close()
    return path


def test_symbols_enriches_collected_triples_with_the_mapping():
    """The join is on broker+account+symbol, and an unmapped triple still comes
    through with its feed provenance rather than being dropped."""
    feed = [
        feed_row("acgmarkets", "49208255", 136),
        feed_row("icmarkets", "48449283", 114),
    ]
    rows = [
        ("acgmarkets", "49208255", 136, "XAUUSD.pro", "Metals (Pro)"),
        # Same symbol id, a different broker: must not match the row above.
        ("acgmarkets", "49208255", 114, "EURCHF.pro", "Forex (Spot)"),
    ]
    cfg = dataclasses.replace(app_module.cfg, db_path=_mapping_db(rows))
    with mock.patch.object(app_module, "cfg", cfg), \
         mock.patch.object(app_module, "reader", FakeSymbolReader(feed)):
        client = app_module.app.test_client()
        out = client.get("/api/symbols").json

    gold, chf = out
    assert gold["symbolId"] == "136"
    assert gold["standardizedName"] == "XAUUSD" and gold["assetClass"] == "Commodities"
    assert gold["broker"] == "acgmarkets" and gold["account"] == "49208255"

    # The feed's icmarkets/48449283 symbol 114 has NO catalogue row; the mapping's
    # 114 belongs to acgmarkets. Joining on the id alone would mislabel it, so
    # the strict triple join must leave it unmapped - but keep its provenance,
    # with the broker reported under the catalogue's spelling.
    assert chf["symbolId"] == "114"
    assert chf["broker"] == "icmarketssc"  # feed said "icmarkets"
    assert chf["account"] == "48449283"
    assert chf["standardizedName"] is None and chf["assetClass"] is None


def test_broker_alias_joins_the_feeds_spelling_to_the_catalogue():
    """The feed's `icmarkets` must match the catalogue's `icmarketssc`.

    Without the alias a raw string compare fails and every one of that broker's
    instruments comes through unmapped.
    """
    feed = [feed_row("icmarkets", "48664595", 41)]
    rows = [("icmarketssc", "48664595", 41, "XAUUSD", "Metals")]
    cfg = dataclasses.replace(app_module.cfg, db_path=_mapping_db(rows))
    with mock.patch.object(app_module, "cfg", cfg), \
         mock.patch.object(app_module, "reader", FakeSymbolReader(feed)):
        client = app_module.app.test_client()
        out = client.get("/api/symbols").json

    assert out[0]["assetClass"] == "Commodities"
    assert out[0]["standardizedName"] == "XAUUSD"
    assert out[0]["broker"] == "icmarketssc"


def test_canonical_broker_leaves_unknown_names_untouched():
    """An alias is applied; anything else is returned as it stands, not guessed
    onto a known broker."""
    assert app_module.canonical_broker("icmarkets") == "icmarketssc"
    assert app_module.canonical_broker("ICMarkets") == "icmarketssc"
    assert app_module.canonical_broker("acgmarkets") == "acgmarkets"
    assert app_module.canonical_broker("some-new-broker") == "some-new-broker"
    assert app_module.canonical_broker(None) is None


def test_symbols_never_matches_on_symbol_id_alone():
    """A catalogue row for the same id on another broker must not be used."""
    feed = [feed_row("icmarkets", "48449283", 114)]
    rows = [("acgmarkets", "49208255", 114, "EURCHF.pro", "Forex (Spot)")]
    cfg = dataclasses.replace(app_module.cfg, db_path=_mapping_db(rows))
    with mock.patch.object(app_module, "cfg", cfg), \
         mock.patch.object(app_module, "reader", FakeSymbolReader(feed)):
        client = app_module.app.test_client()
        out = client.get("/api/symbols").json

    assert out[0]["assetClass"] is None
    assert out[0]["standardizedName"] is None


def test_asset_classes_counts_the_collected_triples():
    """Counts are over the collected triples, in canonical order, zeroes kept."""
    feed = [
        feed_row("acgmarkets", "49208255", 136),
        feed_row("acgmarkets", "49208255", 137),
        feed_row("acgmarkets", "49208255", 1),
    ]
    rows = [
        ("acgmarkets", "49208255", 136, "XAUUSD.pro", "Metals (Pro)"),
        ("acgmarkets", "49208255", 137, "XAGUSD.pro", "Metals (Pro)"),
        ("acgmarkets", "49208255", 1, "EURUSD", "Forex (Spot)"),
    ]
    cfg = dataclasses.replace(app_module.cfg, db_path=_mapping_db(rows))
    with mock.patch.object(app_module, "cfg", cfg), \
         mock.patch.object(app_module, "reader", FakeSymbolReader(feed)):
        client = app_module.app.test_client()
        out = client.get("/api/asset-classes").json

    by_class = {c["assetClass"]: c["count"] for c in out}
    assert by_class["FX"] == 1 and by_class["Commodities"] == 2
    # The canonical order is the server's, and empty classes are still listed.
    order = [c["assetClass"] for c in out]
    assert order[: len(db_module.ASSET_CLASSES)] == list(db_module.ASSET_CLASSES)
    assert by_class["Equities"] == 0 and by_class["Crypto"] == 0
    assert sum(by_class.values()) == len(feed)


def test_asset_classes_reports_unmapped_under_other():
    """A collected triple the mapping lacks is counted, under the unknown key."""
    feed = [feed_row("icmarkets", "48449283", 114)]
    cfg = dataclasses.replace(app_module.cfg, db_path=_mapping_db([]))
    with mock.patch.object(app_module, "cfg", cfg), \
         mock.patch.object(app_module, "reader", FakeSymbolReader(feed)):
        client = app_module.app.test_client()
        out = client.get("/api/asset-classes").json

    by_class = {c["assetClass"]: c["count"] for c in out}
    assert by_class[db_module.ASSET_CLASS_UNKNOWN] == 1


def test_build_vap_collapses_the_triple_to_distinct_symbol_ids():
    """The VAP job records per symbol id, so it dedupes the triple's ids."""
    import build_vap

    feed = [
        feed_row("acgmarkets", "49208255", 136),
        feed_row("icmarkets", "48449283", 136),  # same id, another broker
        feed_row("acgmarkets", "49208255", 114),
    ]
    ids = build_vap._symbols(FakeSymbolReader(feed), "t")
    assert ids == ["136", "114"]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} passed")
