"""Tests for the cache-backed tick path (no data source required).

The window is fixed: one KQL read covers the whole ``CACHE_LOOKBACK_HOURS`` per
symbol and every narrower duration is served from that copy. These tests pin
the two properties the UI depends on - a duration change costs no query, and a
cache outage degrades to KQL rather than to an error.
"""
from __future__ import annotations

import contextlib

import numpy as np
import pandas as pd
import pytest

from app import kql, service
from app.config import Settings
from app.errors import DataSourceError


def _grid(periods: int = 5, symbol: str = "1") -> pd.DataFrame:
    """A `metrics_5m` grid: `periods` 5-minute stops, every window present.

    The shape the precomputed table actually ships - one row per `as_of` stop
    with each metric as `<name>_<suffix>` across the four trailing windows - so
    the cycle's metrics push can be asserted without a KQL table.
    """
    rows = []
    for i, at in enumerate(pd.date_range("2026-10-08T00:00", periods=periods,
                                         freq="5min", tz="UTC")):
        row = {"symbolId": symbol, "as_of": at.to_pydatetime()}
        for suffix, _ in kql.WINDOW_SUFFIXES:
            row[f"ticks_{suffix}"] = 100 + i
            row[f"open_{suffix}"] = 1.0850
            row[f"close_{suffix}"] = 1.0855
            row[f"high_{suffix}"] = 1.0860
            row[f"low_{suffix}"] = 1.0845
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


@pytest.fixture
def settings() -> Settings:
    """A four-hour window, so the clamps and the anchoring are unambiguous."""
    return Settings(kql_host="https://kql.example.invalid", kql_database="ctrader_dom",
                    kql_table="agg_dom", redis_host="cache.example.invalid",
                    cache_lookback_hours=4, max_ticks=10000)


@pytest.fixture(autouse=True)
def _offline(monkeypatch, settings):
    """Everything but `get_settings` is faked, so nothing touches the network."""
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    yield


def _window(periods: int = 240, freq: str = "1min", symbol: str = "1") -> pd.DataFrame:
    """Raw aggregate rows over one symbol, oldest first.

    `periods` rows spaced by `freq`: the default is one row per minute, which
    makes a "60 minutes" cut land on an exact row boundary.
    """
    ts = pd.date_range("2026-10-08T00:00:00", periods=periods, freq=freq, tz="UTC")
    n = len(ts)
    mid = 1.0850 + np.linspace(0, 1e-3, n)
    return pd.DataFrame({
        "timestamp": ts,
        "symbolId": symbol,
        "total_bid": np.full(n, 100.0),
        "total_ask": np.full(n, 120.0),
        "best_bid": mid - 1e-5,
        "best_ask": mid + 1e-5,
        "imbalance": np.zeros(n),
        "imbalance_ratio": np.zeros(n),
        "vwap_bid": mid - 1e-5,
        "vwap_ask": mid + 1e-5,
        "vwap_spread": np.full(n, 2e-5),
        "rel_spread": np.full(n, 1e-5),
        "rel_vwap_spread": np.full(n, 1e-5),
    })


# ----------------------------------------------------------------------
# Clamping: the UI may narrow the window, never widen it
# ----------------------------------------------------------------------

def test_no_window_means_the_whole_cache():
    assert service.clamp_window(None) == 240


def test_a_narrower_window_is_kept():
    assert service.clamp_window(30) == 30


def test_a_wider_window_is_clamped_to_the_cache():
    """More history than the cache holds is served with what there is."""
    assert service.clamp_window(1440) == 240


def test_a_window_of_zero_becomes_one_minute():
    assert service.clamp_window(0) == 1


def test_a_nonsense_window_falls_back_to_the_cache():
    assert service.clamp_window("banana") == 240


def test_the_clamp_honours_a_configured_cache():
    assert service.clamp_window(600, Settings(cache_lookback_hours=1)) == 60


# ----------------------------------------------------------------------
# Narrowing: anchored on the newest row, then capped
# ----------------------------------------------------------------------

def test_narrowing_keeps_the_tail_not_the_head():
    raw = _window(240)
    out = service._narrow(raw, 60, 100000)

    assert out["timestamp"].min() == raw["timestamp"].max() - pd.Timedelta(minutes=60)
    assert out["timestamp"].max() == raw["timestamp"].max()
    assert len(out) == 61          # the boundary minute is included


def test_narrowing_of_the_full_window_changes_nothing():
    raw = _window(240)
    assert len(service._narrow(raw, 240, 100000)) == len(raw)


# ----------------------------------------------------------------------
# Point in time: the slice ends where the user chose
# ----------------------------------------------------------------------

def test_a_point_in_time_ends_the_window_there():
    """Stepping back along the timeline returns what led up to that moment,
    not the newest rows."""
    raw = _window(240)                     # 00:00 - 03:59
    out = service._narrow(raw, 60, 100000, "2026-10-08T02:59:00Z")

    assert out["timestamp"].max() == pd.Timestamp("2026-10-08T02:59:00", tz="UTC")
    assert out["timestamp"].min() == pd.Timestamp("2026-10-08T01:59:00", tz="UTC")
    assert len(out) == 61


def test_a_point_ahead_of_the_data_means_up_to_the_newest_row():
    """The timeline runs up to "now" while the cache runs to the newest row, so
    a point in the future is clamped rather than returning nothing."""
    raw = _window(240)
    out = service._narrow(raw, 60, 100000, "2026-10-08T04:30:00Z")

    assert out["timestamp"].max() == raw["timestamp"].max()
    assert len(out) == 61


def test_a_point_older_than_the_window_leaves_nothing():
    raw = _window(240)
    assert service._narrow(raw, 60, 100000, "2026-10-07T23:00:00Z").empty


def test_an_unreadable_point_in_time_is_refused():
    """Silently re-anchoring on the newest row would show a report the user did
    not ask for, so the request fails loudly instead."""
    raw = _window(240)
    with pytest.raises(DataSourceError):
        service._narrow(raw, 60, 100000, "not a timestamp")


def test_the_row_cap_is_applied_to_the_narrowed_tail():
    raw = _window(3600, freq="1s")
    out = service._narrow(raw, 60, 10)

    assert len(out) == 10
    assert out["timestamp"].max() == raw["timestamp"].max()


def test_an_empty_window_narrows_to_an_empty_frame():
    assert service._narrow(_window(0), 60, 10).empty


# ----------------------------------------------------------------------
# The Redis-backed tick path
# ----------------------------------------------------------------------

@pytest.fixture
def redis(monkeypatch) -> dict:
    """A dict-backed stand-in for `app.cache`, recording what the service did."""
    store: dict = {"frames": {}, "written": [], "read": [], "deleted": []}

    def get_frame(k, settings=None):
        store["read"].append(k)
        return store["frames"].get(k)

    def set_frame(k, frame, ttl, settings=None):
        store["written"].append((k, ttl))
        store["frames"][k] = frame
        return True

    def keys(settings=None, *parts):
        """The stored keys matching `prefix:parts*`, as Redis would list them."""
        pattern = ":".join(["axioma", *(str(p) for p in parts)]) + "*"
        head = pattern[:-1]
        return sorted(k for k in store["frames"] if k.startswith(head))

    monkeypatch.setattr(service.cache, "get_frame", get_frame)
    monkeypatch.setattr(service.cache, "set_frame", set_frame)
    monkeypatch.setattr(service.cache, "keys", keys)
    monkeypatch.setattr(service.cache, "delete",
                        lambda settings=None, *keys: store["deleted"].extend(keys))
    return store


@pytest.fixture
def kql_rows(monkeypatch) -> dict:
    """A stand-in for the one KQL read, counting how often it is made.

    It behaves like the real read on the time filter: a `start` slices the
    frame from that timestamp onwards, so the refresh cycle's incremental
    fetch returns only the new rows. `fail_on` lists symbols whose read
    raises, so the refresh cycle can be shown leaving a good entry in place
    when one fetch goes wrong.
    """
    calls: dict = {"load_raw": 0, "fail_on": set(), "starts": [], "lookbacks": [],
                   "metrics": 0}

    def load_raw(symbol=None, *, limit=None, lookback_minutes=None,
                 start=None, end=None):
        calls["load_raw"] += 1
        calls["symbol"] = symbol
        calls["lookback_minutes"] = lookback_minutes
        calls["starts"].append(start)
        calls["lookbacks"].append(lookback_minutes)
        if symbol in calls["fail_on"]:
            raise DataSourceError(f"No usable aggregate rows for {symbol}.")
        frame = calls["frame"]
        if start is not None:
            ts = pd.to_datetime(frame["timestamp"], utc=True)
            return frame.loc[ts >= pd.Timestamp(start)].reset_index(drop=True)
        return frame

    def connect(settings=None):
        """A KQL connection for the metrics read the cycle now makes."""
        yield object()

    def fetch_metrics(client, *, symbol=None, start=None, settings=None):
        """The precomputed grid, sliced from `start` the way the real read is.

        Stubbed at the KQL layer rather than at `metrics.refresh_symbol`, so the
        cycle's own push - the cache write, the merge, the summary counts - is
        the real code, not a fake that has to duplicate it to stay in sync.
        """
        calls["metrics"] += 1
        grid = _grid()
        if start is not None:
            keep = [pd.Timestamp(st, tz="UTC") >= pd.Timestamp(start)
                    for st in grid["as_of"]]
            return grid.loc[keep].reset_index(drop=True)
        return grid

    calls["frame"] = _window(240)
    monkeypatch.setattr(service, "load_raw", load_raw)
    monkeypatch.setattr(kql, "connect", contextlib.contextmanager(connect))
    monkeypatch.setattr(kql, "fetch_metrics", fetch_metrics)
    return calls


def test_a_cold_symbol_reads_kql_once_and_caches_the_full_window(redis, kql_rows):
    frame = service.load_ticks_cached("1", limit=100000, window_minutes=60)

    assert len(frame) == 61
    assert kql_rows["load_raw"] == 1
    # The cached copy is the whole fixed window, whatever duration was asked for.
    key, ttl = redis["written"][0]
    assert key == "axioma:ctrader_dom:agg_dom:ticks:1"
    assert ttl == service.get_settings().cache_ttl_seconds
    assert len(redis["frames"][key]) == 240
    assert kql_rows["lookback_minutes"] == 240


def test_a_warm_symbol_is_served_from_redis(redis, kql_rows):
    service.load_ticks_cached("1", limit=100000, window_minutes=240)
    before = kql_rows["load_raw"]

    frame = service.load_ticks_cached("1", limit=100000, window_minutes=15)

    assert kql_rows["load_raw"] == before      # no second query
    assert len(frame) == 16


def test_the_same_window_serves_any_window(redis, kql_rows):
    """The point of the fixed window: switching the window is free."""
    for minutes in (5, 30, 120, 240):
        service.load_ticks_cached("1", limit=100000, window_minutes=minutes)

    assert kql_rows["load_raw"] == 1


def test_the_same_window_serves_any_point_in_time(redis, kql_rows):
    """Moving along the timeline costs nothing either: the cached entry holds
    the whole window, so a point in time only trims the copy in memory."""
    service.load_ticks_cached("1", limit=100000, window_minutes=60)
    before = kql_rows["load_raw"]

    frame = service.load_ticks_cached("1", limit=100000, window_minutes=60,
                                      end_time="2026-10-08T02:59:00Z")

    assert kql_rows["load_raw"] == before
    assert frame["ts"].max() == pd.Timestamp("2026-10-08T02:59:00", tz="UTC")
    assert len(frame) == 61


def test_a_point_in_time_with_no_rows_is_a_data_error(redis, kql_rows):
    """A point older than the window leaves nothing to analyse, and the error
    names the point so the message is not read as a feed problem."""
    with pytest.raises(DataSourceError) as exc:
        service.load_ticks_cached("1", limit=100000, window_minutes=60,
                                  end_time="2026-10-07T23:00:00Z")

    assert "ending at 2026-10-07T23:00:00Z" in str(exc.value)
    assert kql_rows["load_raw"] == 1


def test_each_symbol_gets_its_own_cache_entry(redis, kql_rows):
    service.load_ticks_cached("1", limit=100000, window_minutes=60)
    kql_rows["frame"] = _window(240, symbol="2")
    service.load_ticks_cached("2", limit=100000, window_minutes=60)

    assert [k for k, _ in redis["written"]] == [
        "axioma:ctrader_dom:agg_dom:ticks:1",
        "axioma:ctrader_dom:agg_dom:ticks:2",
    ]
    assert kql_rows["load_raw"] == 2


def test_a_symbol_less_read_is_cached_under_all(redis, kql_rows):
    service.load_ticks_cached(None, limit=100000, window_minutes=60)
    assert redis["written"][0][0] == "axioma:ctrader_dom:agg_dom:ticks:all"


def test_a_cache_outage_still_returns_data(monkeypatch, kql_rows):
    """Every cache call fails; the read falls through to KQL rather than raising."""
    monkeypatch.setattr(service.cache, "get_frame", lambda k, settings=None: None)
    monkeypatch.setattr(service.cache, "set_frame", lambda k, f, ttl, settings=None: False)

    frame = service.load_ticks_cached("1", limit=100000, window_minutes=60)

    assert len(frame) == 61
    assert kql_rows["load_raw"] == 1


def test_an_empty_window_is_reported_as_a_data_error(redis, kql_rows):
    redis["frames"]["axioma:ctrader_dom:agg_dom:ticks:1"] = _window(0)

    with pytest.raises(DataSourceError) as exc:
        service.load_ticks_cached("1", limit=100000, window_minutes=60)

    assert "60 minute" in str(exc.value)


def test_reset_drops_the_catalogue_and_every_symbol_key(monkeypatch, redis):
    monkeypatch.setattr(service, "_instruments",
                        {"1": service.Instrument("1", "EURUSD", "", "fx_major", 5)})
    monkeypatch.setattr(service, "status", lambda **_: service.Status(connected=True))

    service.reset()

    assert sorted(redis["deleted"]) == [
        "axioma:ctrader_dom:agg_dom:catalogue",
        "axioma:ctrader_dom:agg_dom:ticks:1",
        "axioma:ctrader_dom:agg_dom:ticks:all",
        # The precomputed grid rides along with the ticks: a reset that left it
        # behind would have a stale metrics copy served after a fresh read.
        "axioma:ctrader_dom:metrics_5m:metrics:1",
        "axioma:ctrader_dom:metrics_5m:metrics:all",
    ]
    assert service._instruments is None


def test_reset_also_forgets_the_refresh_stamp(monkeypatch, redis):
    """Nothing is cached after a reset, so no cycle can vouch for it."""
    monkeypatch.setattr(service, "_instruments", None)
    monkeypatch.setattr(service, "_last_refresh", {"symbols": 2})
    monkeypatch.setattr(service, "status", lambda **_: service.Status(connected=True))

    service.reset()

    assert service.last_refresh() is None


# ----------------------------------------------------------------------
# The refresh cycle: the fixed window is slid forward every 30 minutes
# ----------------------------------------------------------------------

@pytest.fixture
def catalogue(monkeypatch):
    """A catalogue builder; the default ticker is present but untraded.

    Untraded by default so the cycle refreshes exactly what is cached.
    """
    def build(ticks: int = 0) -> dict:
        rows = {"41": service.Instrument("41", "XAUUSD", "Gold vs US Dollar", "metal", ticks)}
        monkeypatch.setattr(service, "_instruments", rows)
        monkeypatch.setattr(service, "instruments", lambda refresh=False: rows)
        monkeypatch.setattr(service, "_last_refresh", None)
        return rows

    build()
    return build


def test_cached_symbols_are_listed_from_redis(redis, kql_rows):
    """The refresh set is what is stored, not a memory list that can drift."""
    service.load_ticks_cached("1", limit=100000, window_minutes=60)
    service.load_ticks_cached("2", limit=100000, window_minutes=60)

    assert service.cached_symbols() == ["1", "2"]


def test_the_cycle_slides_every_cached_symbol(redis, kql_rows, catalogue):
    service.load_ticks_cached("1", limit=100000, window_minutes=60)
    service.load_ticks_cached("2", limit=100000, window_minutes=60)
    redis["written"].clear()
    before = kql_rows["load_raw"]

    service.refresh_cache()

    # Both cached symbols were re-read and re-written, under the TTL - ticks
    # first, then the precomputed grid the cycle pushes alongside them.
    assert redis["written"] == [
        ("axioma:ctrader_dom:agg_dom:ticks:1", 2700),
        ("axioma:ctrader_dom:agg_dom:ticks:2", 2700),
        ("axioma:ctrader_dom:metrics_5m:metrics:1", 2700),
        ("axioma:ctrader_dom:metrics_5m:metrics:2", 2700),
    ]
    assert kql_rows["load_raw"] == before + 2
    # A warm symbol is fetched only from its newest cached row - never over
    # the whole window, and with no window re-anchoring.
    assert kql_rows["starts"][-2:] == [
        "2026-10-08T03:59:00+00:00", "2026-10-08T03:59:00+00:00",
    ]
    assert kql_rows["lookbacks"][-2:] == [None, None]
    # The slide re-writes the same window: nothing had aged out of it yet.
    assert len(redis["frames"]["axioma:ctrader_dom:agg_dom:ticks:1"]) == 240


def test_the_cycle_purges_the_earliest_30_minutes(redis, kql_rows, catalogue):
    """The window only ever holds four hours: the oldest slice is dropped."""
    service.load_ticks_cached("1", limit=100000, window_minutes=60)
    redis["written"].clear()
    # The feed advances half an hour: 30 new minutes arrive on top of the 4 h.
    kql_rows["frame"] = _window(270)

    summary = service.refresh_cache()

    key = "axioma:ctrader_dom:agg_dom:ticks:1"
    stored = redis["frames"][key]
    ts = pd.to_datetime(stored["timestamp"], utc=True)
    # 00:00-04:29 in, 00:00-00:28 out: four hours to the minute, newest last.
    assert len(stored) == 241
    assert ts.min() == pd.Timestamp("2026-10-08T00:29:00", tz="UTC")
    assert ts.max() == pd.Timestamp("2026-10-08T04:29:00", tz="UTC")
    assert ts.max() - ts.min() <= pd.Timedelta(minutes=240)
    # Only the new slice was read, and the purge is reported in the summary.
    assert kql_rows["starts"][-1] == "2026-10-08T03:59:00+00:00"
    assert summary["purged"] == 29


def test_the_slide_keeps_the_newer_copy_of_an_overlapping_row(redis, kql_rows, catalogue):
    """The slice starts at the cached newest row, so it arrives twice."""
    service.load_ticks_cached("1", limit=100000, window_minutes=60)
    kql_rows["frame"] = _window(270)

    service.refresh_cache()

    key = "axioma:ctrader_dom:agg_dom:ticks:1"
    ts = pd.to_datetime(redis["frames"][key]["timestamp"], utc=True)
    assert ts.duplicated().sum() == 0
    assert ts.is_monotonic_increasing


def test_a_cold_symbol_in_the_cycle_reads_the_whole_window(redis, kql_rows, catalogue):
    """No cached entry to slide from - a restart still reads all four hours."""
    catalogue(ticks=24)
    service.load_ticks_cached("41", limit=100000, window_minutes=60)
    # Evict the entry's rows but keep the key listed: the cycle sees a target
    # with nothing usable and falls back to the full-window read.
    redis["frames"]["axioma:ctrader_dom:agg_dom:ticks:41"] = _window(0)
    redis["written"].clear()
    kql_rows["starts"].clear()
    kql_rows["lookbacks"].clear()

    service.refresh_cache()

    assert kql_rows["starts"] == [None]
    assert kql_rows["lookbacks"] == [240]
    assert len(redis["frames"]["axioma:ctrader_dom:agg_dom:ticks:41"]) == 240


def test_the_slide_never_exceeds_the_row_cap():
    """Even a runaway feed is capped, keeping the newest rows."""
    raw = service._slide_window(_window(3600, freq="1s"), None, 240, 100)

    assert len(raw) == 100
    assert raw["timestamp"].max() == pd.Timestamp("2026-10-08T00:59:59", tz="UTC")


def test_the_slide_purges_anchored_on_the_newest_row():
    """A stalled feed keeps its rows: the purge moves only when data does."""
    cached = _window(240)                       # 00:00-03:59, no new rows
    slid = service._slide_window(cached, _window(0), 240, 10000)

    assert len(slid) == 240


def test_the_cycle_summary_describes_the_run(redis, kql_rows, catalogue):
    service.load_ticks_cached("1", limit=100000, window_minutes=60)
    redis["written"].clear()

    summary = service.refresh_cache()

    assert summary["symbols"] == 1
    assert summary["rows"] == 240
    assert summary["purged"] == 0            # nothing had aged out yet
    assert summary["cache_window_minutes"] == 240
    assert summary["interval_minutes"] == 30
    assert summary["errors"] == []
    assert summary["finished_at"] >= summary["started_at"]
    assert service.last_refresh() == summary


def test_the_cycle_with_an_empty_cache_refreshes_nothing(redis, kql_rows, catalogue):
    """The cycle works whatever data it has: nothing cached means nothing read.

    No default warming - a cold cache stays empty until a request lazily loads
    it, so the run only rebuilds the catalogue."""
    catalogue(ticks=24)
    assert service.cached_symbols() == []

    summary = service.refresh_cache()

    assert redis["written"] == []
    assert summary["symbols"] == 0


def test_an_empty_cache_stays_empty_when_the_default_is_untraded(redis, kql_rows, catalogue):
    """An untraded default changes nothing: the cycle had no targets either way."""
    assert service.cached_symbols() == []

    summary = service.refresh_cache()

    assert redis["written"] == []
    assert summary["symbols"] == 0


def test_a_cached_symbol_is_refreshed_once(redis, kql_rows, catalogue):
    """A cached symbol is read once, not twice - there is no second target set."""
    catalogue(ticks=24)
    service.load_ticks_cached("41", limit=100000, window_minutes=60)
    redis["written"].clear()

    service.refresh_cache()

    assert redis["written"] == [("axioma:ctrader_dom:agg_dom:ticks:41", 2700),
                                ("axioma:ctrader_dom:metrics_5m:metrics:41", 2700)]


def test_the_cycle_keeps_the_symbol_less_entry(redis, kql_rows, catalogue):
    service.load_ticks_cached(None, limit=100000, window_minutes=60)
    redis["written"].clear()

    service.refresh_cache()

    assert redis["written"] == [("axioma:ctrader_dom:agg_dom:ticks:all", 2700),
                                ("axioma:ctrader_dom:metrics_5m:metrics:all", 2700)]
    assert kql_rows["symbol"] is None


def test_a_failing_symbol_keeps_its_previous_entry(redis, kql_rows, catalogue):
    """A partial failure must not evict data that is merely older."""
    service.load_ticks_cached("1", limit=100000, window_minutes=60)
    service.load_ticks_cached("2", limit=100000, window_minutes=60)
    redis["written"].clear()
    kql_rows["fail_on"] = {"2"}

    summary = service.refresh_cache()

    assert summary["symbols"] == 1
    assert summary["errors"] == ["2: No usable aggregate rows for 2."]
    # The failed tick window is not re-written, but its metrics grid - read
    # through a separate path - still refreshes alongside the good symbol.
    assert redis["written"] == [
        ("axioma:ctrader_dom:agg_dom:ticks:1", 2700),
        ("axioma:ctrader_dom:metrics_5m:metrics:1", 2700),
        ("axioma:ctrader_dom:metrics_5m:metrics:2", 2700),
    ]
    assert "axioma:ctrader_dom:agg_dom:ticks:2" in redis["frames"]


def test_a_failed_catalogue_still_refreshes_the_ticks(monkeypatch, redis, kql_rows):
    """The catalogue and the rows are separate reads; one failing is reported."""
    def boom(refresh=False):
        raise DataSourceError("KQL query failed: Forbidden (403-Forbidden)")

    monkeypatch.setattr(service, "_instruments", None)
    monkeypatch.setattr(service, "instruments", boom)
    monkeypatch.setattr(service, "_last_refresh", None)
    service.load_ticks_cached("1", limit=100000, window_minutes=60)
    redis["written"].clear()

    summary = service.refresh_cache()

    assert summary["symbols"] == 1
    assert summary["errors"] == [
        "catalogue: KQL query failed: Forbidden (403-Forbidden)",
    ]


def test_a_cache_that_cannot_be_listed_refreshes_nothing(monkeypatch, kql_rows, catalogue):
    """A cache outage leaves the cycle with an empty set, not an error."""
    monkeypatch.setattr(service.cache, "keys", lambda settings=None, *parts: [])
    monkeypatch.setattr(service.cache, "set_frame",
                        lambda k, f, ttl, settings=None: False)

    summary = service.refresh_cache()

    assert summary["symbols"] == 0
    assert summary["errors"] == []



# ----------------------------------------------------------------------
# The metrics push
# ----------------------------------------------------------------------

def test_the_cycle_pushes_the_precomputed_grid_to_redis(redis, kql_rows, catalogue):
    """The headline metrics are served from Redis, so the cycle - not the page
    load - is what puts them there."""
    service.load_ticks_cached("1", limit=100000, window_minutes=60)
    redis["written"].clear()

    summary = service.refresh_cache()

    assert kql_rows["metrics"] == 1                 # one grid read per symbol
    assert summary["metrics_symbols"] == 1
    assert summary["metrics_rows"] == 5
    key = "axioma:ctrader_dom:metrics_5m:metrics:1"
    assert key in redis["frames"]
    assert len(redis["frames"][key]) == 5


def test_the_metrics_push_is_switched_off_with_the_source(redis, kql_rows,
                                                          catalogue, monkeypatch,
                                                          settings):
    """A stalled or half-appended table must not leave the cycle pushing a grid
    the operator cannot trust."""
    off = Settings(kql_host="https://kql.example.invalid", kql_database="ctrader_dom",
                   kql_table="agg_dom", redis_host="cache.example.invalid",
                   cache_lookback_hours=4, max_ticks=10000,
                   _metrics_source="ticks")
    monkeypatch.setattr(service, "get_settings", lambda: off)
    try:
        summary = service.refresh_cache()
    finally:
        monkeypatch.setattr(service, "get_settings", lambda: settings)

    assert kql_rows["metrics"] == 0
    assert summary["metrics_symbols"] == 0
    assert summary["metrics_rows"] == 0


def test_a_metrics_read_that_fails_is_reported_and_keeps_the_old_grid(
        redis, kql_rows, catalogue, monkeypatch):
    """A partial failure must not evict a grid that is merely older - the same
    contract the tick window follows."""
    service.load_ticks_cached("1", limit=100000, window_minutes=60)
    service.refresh_cache()  # pushes the grid; the tick read alone does not
    key = "axioma:ctrader_dom:metrics_5m:metrics:1"
    assert key in redis["frames"]
    before = redis["frames"][key]

    def boom(symbol=None, settings=None):
        raise DataSourceError("KQL query failed: Forbidden (403-Forbidden)")

    monkeypatch.setattr(service.metrics, "refresh_symbol", boom)

    summary = service.refresh_cache()

    assert any("metrics 1: " in e for e in summary["errors"])
    assert redis["frames"][key] is before            # untouched, not evicted

