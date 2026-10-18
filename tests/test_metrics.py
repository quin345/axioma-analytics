"""Tests for the precomputed `metrics_5m` read path (no data source required).

The table already holds every metric aggregated over the four fixed trailing
windows, so the read is a **lookup**: pick the row for the chosen point in time
and the column set for the chosen window. These tests pin the properties that
make that safe to serve - the grid resolves a point to a stop rather than
failing, a window the table does not ship is never answered with a wider one,
and the cache-through read never re-scans KQL.
"""
from __future__ import annotations

import contextlib

import numpy as np
import pandas as pd
import pytest

from app import cache, kql, metrics
from app.analytics import volume_profile as pandas_profile
from app.config import Settings
from app.errors import DataSourceError


@pytest.fixture
def settings() -> Settings:
    return Settings(kql_host="https://kql.example.invalid", kql_database="ctrader_dom",
                    kql_table="agg_dom", metrics_table="metrics_5m",
                    redis_host="cache.example.invalid", cache_lookback_hours=4)


def _grid(periods: int = 5, symbol: str = "1", offset: int = 0) -> pd.DataFrame:
    """A `metrics_5m` grid: `periods` 5-minute stops, every window present.

    Mirrors the table's real schema - the nested `vap_*`/`poc_*` columns the read
    projects, the scalar window columns the table also ships but the app does not
    read, plus the `computed_at` stamp the refresh writes, and deliberately **no**
    `open/high/low/close` columns.
    """
    rows = []
    for i, at in enumerate(pd.date_range("2026-10-10T09:00", periods=periods,
                                         freq="5min", tz="UTC")):
        row = {"symbolId": symbol, "as_of": at.to_pydatetime(),
               "computed_at": at.to_pydatetime()}
        for suffix, _ in kql.WINDOW_SUFFIXES:
            row[f"ticks_{suffix}"] = 100 + i + offset
            row[f"vwap_{suffix}"] = 1.0852
            row[f"change_pct_{suffix}"] = 0.046
            row[f"total_volume_{suffix}"] = 1234.5
            row[f"realized_vol_bps_{suffix}"] = 12.5
            row[f"avg_spread_bps_{suffix}"] = 1.5
            row[f"ofi_imbalance_{suffix}"] = 0.25
            row[f"vap_{suffix}"] = [
                {"price": 1.0850 + n * 0.001, "volume": 10.0 - n, "ticks": 3}
                for n in range(5)
            ]
            row[f"poc_{suffix}"] = 1.0850
        rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# The column contract
# --------------------------------------------------------------------------

def test_the_window_columns_pin_the_four_trailing_windows():
    """Read by name, so a renamed or missing column must fail the read rather
    than hand back a panel of nulls."""
    cols = kql.window_columns()

    # Only the volume-at-price ladder and its POC: the scalars come from the
    # tick path, so they are not projected from `metrics_5m` at all.
    assert len(cols) == 4 * len(kql.WINDOW_NESTED)
    assert "vap_2h" in cols and "poc_3h" in cols
    assert "vap_1h" in cols and "poc_4h" in cols
    assert "open_5m" not in cols          # the slider is gone: only fixed windows
    # The scalar window metrics the table ships are deliberately not read: the
    # headline tiles come from `/api/analytics`, one tick frame.
    for metric in ("ticks", "vwap", "total_volume", "realized_vol_bps",
                   "avg_spread_bps", "ofi_imbalance", "change_pct"):
        assert f"{metric}_1h" not in cols
    # OHLC is not in the table's schema either: the tick path supplies it.
    for metric in ("open", "high", "low", "close"):
        assert f"{metric}_1h" not in cols


def test_a_window_the_table_ships_resolves_to_its_suffix():
    assert kql.window_suffix(60) == "1h"
    assert kql.window_suffix(240) == "4h"
    assert kql.window_suffix(90) is None


def test_a_window_the_table_does_not_ship_snaps_down_not_up():
    """Never answer with a wider window than asked: that would report a span the
    user did not choose, and one the cache may not even hold."""
    assert kql.best_window(90) == "1h"        # 60 is the widest no wider than 90
    assert kql.best_window(120) == "2h"
    assert kql.best_window(150) == "2h"
    assert kql.best_window(30) == "1h"        # narrower than any: the narrowest
    assert kql.best_window(480) == "4h"       # wider than any: the widest


# --------------------------------------------------------------------------
# Stop selection
# --------------------------------------------------------------------------

def test_no_point_means_the_newest_stop():
    row = metrics.pick_stop(_grid())

    assert pd.Timestamp(row["as_of"]) == pd.Timestamp("2026-10-10T09:20", tz="UTC")


def test_a_point_between_two_stops_takes_the_one_before_it():
    """\"As of\" is inclusive of the chosen moment, exactly as the tick path
    ends its window at the requested point."""
    row = metrics.pick_stop(_grid(), "2026-10-10T09:06:30Z")

    assert pd.Timestamp(row["as_of"]) == pd.Timestamp("2026-10-10T09:05", tz="UTC")


def test_a_point_ahead_of_the_data_clamps_to_the_newest_stop():
    """A stale tab or a bookmarked URL resolves rather than failing."""
    row = metrics.pick_stop(_grid(), "2099-01-01T00:00:00Z")

    assert pd.Timestamp(row["as_of"]) == pd.Timestamp("2026-10-10T09:20", tz="UTC")


def test_a_point_before_the_grid_clamps_to_the_earliest_stop():
    row = metrics.pick_stop(_grid(), "2020-01-01T00:00:00Z")

    assert pd.Timestamp(row["as_of"]) == pd.Timestamp("2026-10-10T09:00", tz="UTC")


def test_an_unreadable_point_is_refused_not_re_anchored():
    """Silently falling back to the newest row would attribute a report to the
    wrong moment without saying so."""
    with pytest.raises(DataSourceError) as exc:
        metrics.pick_stop(_grid(), "yesterday lunchtime")

    assert "yesterday lunchtime" in str(exc.value)


def test_stops_lists_the_grid_the_ui_can_build_a_timeline_from():
    """Published so the picker cannot offer a slot with nothing behind it when a
    metrics cycle has missed and left a gap."""
    assert metrics.stops(_grid()) == [
        "2026-10-10T09:00:00+00:00", "2026-10-10T09:05:00+00:00",
        "2026-10-10T09:10:00+00:00", "2026-10-10T09:15:00+00:00",
        "2026-10-10T09:20:00+00:00",
    ]


def test_an_empty_grid_has_no_stops_and_no_row():
    empty = pd.DataFrame()

    assert metrics.stops(empty) == []
    assert metrics.pick_stop(empty) is None



# --------------------------------------------------------------------------
# Window resolution
# --------------------------------------------------------------------------

def test_an_absent_window_defaults_to_the_narrowest_the_ui_offers():
    """The dashboard's own default is 1 h, so the API must agree with the control
    or the first paint would show a different span from the one selected."""
    assert metrics.requested_window(None) == 60
    assert metrics.requested_window("nonsense") == 60
    assert metrics.requested_window(240) == 240
    # A request narrower than any shipped window still gets a real column.
    assert metrics.requested_window(30) == 60


# --------------------------------------------------------------------------
# The volume profile from the stored ladder
# --------------------------------------------------------------------------

def _ladder() -> list[dict]:
    """A 5-bin ladder, ascending by price - the shape `vap_*` ships."""
    return [
        {"price": 1.0850, "volume": 10.0, "ticks": 5},
        {"price": 1.0855, "volume": 20.0, "ticks": 9},
        {"price": 1.0860, "volume": 15.0, "ticks": 7},
        {"price": 1.0865, "volume": 8.0, "ticks": 4},
        {"price": 1.0870, "volume": 6.0, "ticks": 3},
    ]


def test_the_profile_from_the_table_runs_the_shared_value_area():
    """Both paths call `analytics.value_area`, the one implementation of the
    expansion - so a profile read from the table and one binned in pandas cannot
    disagree about where the value area sits. Hand-checked: volumes
    [10, 20, 15, 8, 6] total 59, 70 % = 41.3; from the POC (20) the expansion
    takes 15 upward (35), then 10 downward (45 >= 41.3) - so the area spans the
    first three bins, contains the POC and covers the required share."""
    from app import analytics

    ladder = _ladder()
    vols = [b["volume"] for b in ladder]
    profile = metrics.volume_profile(ladder, poc=1.0855)

    # The same function, on the same ascending bins, is what the panel shows
    # (`clean` widens its tuple to the list the JSON payload carries).
    assert profile["value_area"] == list(analytics.value_area(ladder, 1))

    assert profile["value_area"] == [1.0850, 1.0860]
    assert profile["value_area"][0] <= profile["poc"] <= profile["value_area"][1]
    assert sum(vols[0:3]) >= 0.70 * sum(vols)      # the share is actually covered

    # And the pandas path, given its own bins, reports an area with the same
    # properties rather than a different kind of answer.
    prices = [b["price"] for b in ladder]
    ts = pd.date_range("2026-10-10T09:00", periods=len(ladder), freq="5min", tz="UTC")
    frame = pd.DataFrame({
        "ts": ts,
        "symbol": "1",
        "mid": prices,                  # `volume_profile` bins on the mid
        "bid": [p - 1e-5 for p in prices],
        "ask": [p + 1e-5 for p in prices],
        "last": prices,
        "volume": [b["volume"] for b in ladder],
    })
    from_pandas = pandas_profile(frame)
    # The pandas path re-bins to 60 bins, so its POC is a bin centre rather than
    # the ladder's own price - the property that matters is the same: an area
    # around a real point of control, not a fixed number.
    assert from_pandas["poc"] == pytest.approx(1.0855, abs=0.001)
    assert from_pandas["value_area"][0] <= from_pandas["poc"] <= from_pandas["value_area"][1]


def test_the_profile_is_inverted_for_the_ladder_view():
    """The UI reads top-to-bottom from the highest price."""
    prices = [b["price"] for b in metrics.volume_profile(_ladder())["bins"]]

    assert prices == sorted(prices, reverse=True)


def test_the_poc_is_the_busiest_bin_whatever_the_stored_column_says():
    """The panel draws the ladder and marks the POC bin by matching its price, so
    the marked bar *must* be the tallest one. `metrics_5m`'s own `poc_*` column is
    a second argmax over the same bins and can disagree - a swapped `arg_max` in
    the KQL put the bin's *volume* there, which marked a bar that was not the
    busiest price. The ladder wins; the stored point is only a fallback for a
    ladder that traded nothing."""
    ladder = _ladder()                            # volumes [10, 20, 15, 8, 6]
    busiest = max(ladder, key=lambda b: b["volume"])
    assert busiest["price"] == pytest.approx(1.0855)

    # A stored point that names the busiest bin, one that names a different bin,
    # one that is the bin's volume rather than its price, and no point at all all
    # resolve to the same, correct, price.
    for stored in (busiest["price"], 1.0860, busiest["volume"], None):
        point = metrics.volume_profile(ladder, poc=stored)["poc"]
        assert point == pytest.approx(1.0855)

    # That price is a real bin, and it is the one carrying the most volume.
    marked = [b for b in ladder if b["price"] == pytest.approx(1.0855)]
    assert marked and marked[0]["volume"] == busiest["volume"]
    assert marked[0]["volume"] == max(b["volume"] for b in ladder)


def test_a_ladder_that_traded_nothing_falls_back_to_the_stored_point():
    """No bin has volume, so there is no busiest bin to mark; the table's own
    point is the only thing there is to point at."""
    flat = [{"price": 1.0850 + i * 0.0005, "volume": 0.0, "ticks": 0} for i in range(3)]

    profile = metrics.volume_profile(flat, poc=1.0855)

    assert profile["poc"] == 1.0855
    assert profile["value_area"] is None
    assert len(profile["bins"]) == 3


def test_an_empty_ladder_is_an_empty_panel_rather_than_an_error():
    assert metrics.volume_profile([]) == {
        "bins": [], "poc": None, "value_area": None, "high_volume_nodes": []}


def test_the_ladder_survives_a_parquet_round_trip():
    """Redis stores frames as Parquet, so the nested dynamic comes back as a
    numpy object array of dicts - the shape `_ladder` has to accept."""
    import io

    buf = io.BytesIO()
    pd.DataFrame({"vap_1h": [_ladder()]}).to_parquet(buf, index=False)
    buf.seek(0)

    back = pd.read_parquet(buf)
    bins = metrics._ladder(back["vap_1h"].iloc[0])

    assert [b["price"] for b in bins] == [b["price"] for b in _ladder()]
    assert bins[0]["ticks"] == 5




# --------------------------------------------------------------------------
# The cache-through read
# --------------------------------------------------------------------------

@pytest.fixture
def store(monkeypatch):
    """An in-memory Redis, recording writes under the real key layout."""
    data: dict = {"frames": {}, "docs": {}, "written": [], "deleted": []}

    monkeypatch.setattr(cache, "get_frame",
                        lambda k, settings=None: data["frames"].get(k))
    monkeypatch.setattr(cache, "set_frame",
                        lambda k, f, ttl, settings=None:
                        data["frames"].__setitem__(k, f)
                        or data["written"].append((k, ttl)) or True)
    monkeypatch.setattr(cache, "get_json",
                        lambda k, settings=None: data["docs"].get(k))
    monkeypatch.setattr(cache, "set_json",
                        lambda k, v, ttl, settings=None:
                        data["docs"].__setitem__(k, v)
                        or data["written"].append((k, ttl)) or True)
    monkeypatch.setattr(cache, "delete",
                        lambda settings=None, *keys:
                        data["deleted"].extend(keys)
                        or [data["docs"].pop(k, None) for k in keys])
    monkeypatch.setattr(cache, "keys",
                        lambda settings=None, *parts: sorted(
                            list(data["frames"]) + list(data["docs"])))
    return data


@pytest.fixture
def fake_kql(monkeypatch):
    """A KQL read returning a fixed grid, counting how often it is hit."""
    calls: dict = {"fetch": 0}

    @contextlib.contextmanager
    def connect(settings=None):
        yield object()

    monkeypatch.setattr(kql, "connect", connect)
    monkeypatch.setattr(kql, "fetch_metrics",
                        lambda client, symbol=None, account=None, start=None, settings=None:
                        calls.__setitem__("fetch", calls["fetch"] + 1) or _grid())
    return calls


def test_a_cold_read_hits_kql_once_and_writes_the_grid(settings, store, fake_kql):
    frame = metrics.load("1", settings=settings)

    assert len(frame) == 5
    assert fake_kql["fetch"] == 1
    assert store["written"] == [
        ("axioma:ctrader_dom:metrics_5m:metrics:1", settings.cache_ttl_seconds)]
    assert len(store["frames"]["axioma:ctrader_dom:metrics_5m:metrics:1"]) == 5


def test_a_warm_read_never_re_scans_the_table(settings, store, fake_kql):
    """The whole point of `metrics_5m`: a second request is a Redis read."""
    metrics.load("1", settings=settings)

    frame = metrics.load("1", settings=settings)

    assert fake_kql["fetch"] == 1
    assert len(frame) == 5


def test_the_metrics_keys_are_namespaced_by_the_metrics_table(settings):
    """`agg_dom` and `metrics_5m` hold different shapes under the same symbol;
    one namespace for both would let a stale tick frame be read back as metrics."""
    assert metrics.metrics_key("41", settings) == \
        "axioma:ctrader_dom:metrics_5m:metrics:41"
    assert metrics.metrics_key(None, settings) == \
        "axioma:ctrader_dom:metrics_5m:metrics:all"


# --------------------------------------------------------------------------
# The built report cache
# --------------------------------------------------------------------------

def test_the_first_report_builds_and_caches_the_bundle(settings, store, fake_kql):
    first = metrics.report("1", settings=settings)

    rk = metrics.report_key("1", first["meta"]["as_of"], settings)
    assert store["docs"][rk]["volume_profiles"] == first["volume_profiles"]


def test_a_repeat_report_is_a_json_read_not_a_rebuild(settings, store, fake_kql,
                                                      monkeypatch):
    """Same symbol, same stop: the profiles come back from Redis."""
    first = metrics.report("1", window_minutes=60, settings=settings)
    builds = {"n": 0}
    real_profile = metrics.volume_profile
    monkeypatch.setattr(metrics, "volume_profile",
                        lambda *a, **k: builds.__setitem__("n", builds["n"] + 1)
                        or real_profile(*a, **k))

    second = metrics.report("1", window_minutes=240, settings=settings)

    assert builds["n"] == 0
    assert second["volume_profiles"] == first["volume_profiles"]
    # Per-request fields still follow the request, not the stored copy.
    assert second["requested_window_minutes"] == 240
    assert second["selected_window_minutes"] == 240


def test_two_instants_at_one_stop_share_a_report_entry(settings, store, fake_kql):
    """The key is the resolved stop: either side of it hits the same bundle."""
    metrics.report("1", as_of="2026-10-10T09:21:00Z", settings=settings)

    body = metrics.report("1", as_of="2026-10-10T09:23:00Z", settings=settings)

    assert body["meta"]["as_of"] == "2026-10-10T09:20:00+00:00"
    assert body["meta"]["requested_as_of"] == "2026-10-10T09:23:00Z"
    assert sum(k.count("report:") for k, _ in store["written"]) == 1


def test_a_rewritten_grid_purges_the_symbol_s_reports(settings, store, fake_kql):
    """Newest-row-wins can recompute a stop already built: stale bundles go."""
    first = metrics.report("1", settings=settings)
    rk = metrics.report_key("1", first["meta"]["as_of"], settings)
    assert rk in store["docs"]

    metrics.refresh_symbol("1", settings=settings)

    assert rk not in store["docs"]
    assert rk in store["deleted"]


def test_report_entries_are_not_mistaken_for_cached_symbols(settings, store,
                                                            fake_kql):
    """The refresh set is grid entries only; report bundles hang a level deeper."""
    metrics.report("1", settings=settings)

    assert metrics.cached_symbols(settings) == ["1"]
    # The tick path keeps its own namespace.
    assert cache.key(settings, "ticks", "41") == "axioma:ctrader_dom:agg_dom:ticks:41"


def test_cached_symbols_lists_what_redis_holds(settings, store, fake_kql):
    metrics.load("1", settings=settings)
    metrics.load("2", settings=settings)

    assert metrics.cached_symbols(settings) == ["1", "2"]


def test_a_cache_outage_falls_through_to_kql(monkeypatch, settings, fake_kql):
    """A cache failure costs a query; it is never an outage."""
    monkeypatch.setattr(cache, "get_frame", lambda k, settings=None: None)
    monkeypatch.setattr(cache, "set_frame", lambda k, f, ttl, settings=None: False)

    frame = metrics.load("1", settings=settings)

    assert len(frame) == 5
    assert fake_kql["fetch"] == 1


# --------------------------------------------------------------------------
# Merging a refreshed slice
# --------------------------------------------------------------------------

def test_the_slide_keeps_the_newest_row_per_stop():
    """Every cycle recomputes the same stops, and the newest computation is the
    correct one even when the two disagree or arrive out of order."""
    merged = metrics.slide(_grid(3), _grid(3, offset=50), limit=100)

    assert len(merged) == 3
    assert merged["ticks_1h"].tolist() == [150, 151, 152]


def test_the_slide_drops_oldest_stops_past_the_limit():
    merged = metrics.slide(_grid(5), None, limit=3)

    assert len(merged) == 3
    assert merged["as_of"].iloc[-1] == pd.Timestamp("2026-10-10T09:20", tz="UTC")


def test_the_slide_of_an_empty_cache_is_the_fresh_slice():
    assert len(metrics.slide(pd.DataFrame(), _grid(4), limit=100)) == 4



# --------------------------------------------------------------------------
# The whole report
# --------------------------------------------------------------------------

def test_the_report_carries_every_window_at_the_chosen_stop(settings, store,
                                                            fake_kql):
    rep = metrics.report("1", window_minutes=60, settings=settings)

    assert rep["meta"]["as_of"] == "2026-10-10T09:20:00+00:00"
    assert rep["selected_window_minutes"] == 60
    assert rep["window_choices_minutes"] == [60, 120, 180, 240]
    # Switching window is free: all four ladders are column sets on the same row.
    assert sorted(rep["volume_profiles"]) == ["120", "180", "240", "60"]
    assert rep["volume_profiles"]["60"]["bins"]
    assert rep["volume_profiles"]["240"]["bins"]
    # The scalar window metrics are not served from `metrics_5m` at all: the
    # headline tiles come from the tick path, one frame.
    assert "windows" not in rep
    assert "summary" not in rep


def test_a_window_the_table_does_not_ship_is_reported_as_snapped(settings, store,
                                                                 fake_kql):
    rep = metrics.report("1", window_minutes=90, settings=settings)

    assert rep["requested_window_minutes"] == 90
    assert rep["selected_window_minutes"] == 60   # snapped down, and said so


def test_the_report_names_its_source_and_stop_inventory(settings, store, fake_kql):
    rep = metrics.report("1", settings=settings)

    assert rep["meta"]["source"] == "metrics_5m"
    assert rep["meta"]["stops_available"] == 5
    assert rep["meta"]["symbol"] == "1"
    assert len(rep["stops"]) == 5
    assert rep["value_area_share"] == 0.70


def test_the_report_refuses_an_unreadable_point_in_time(settings, store, fake_kql):
    with pytest.raises(DataSourceError):
        metrics.report("1", as_of="not a moment", settings=settings)


def test_a_grid_with_no_rows_is_reported_rather_than_a_broken_panel(
        monkeypatch, settings, fake_kql):
    """An empty table means the metrics job has not run - named in the error so
    it is not read as a feed outage."""
    monkeypatch.setattr(kql, "fetch_metrics",
                        lambda client, symbol=None, account=None, start=None, settings=None:
                        pd.DataFrame())

    with pytest.raises(DataSourceError) as exc:
        metrics.report("1", settings=settings)

    assert "metrics_5m" in str(exc.value)

