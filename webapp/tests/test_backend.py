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


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} passed")
