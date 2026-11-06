"""Run from the webapp folder:  python -m tests.test_backend"""
import math
import os
import sys
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
})

import analytics  # noqa: E402
import app as app_module  # noqa: E402


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


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} passed")
