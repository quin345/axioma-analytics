import json
import os
import re
from datetime import datetime, timezone

from flask import Flask, abort, jsonify, request, send_from_directory

import analytics
import db
import queries
from config import load_config
from kusto_client import KustoReader

BOOK_LEVELS = 10
BIN_SECONDS = 10  # series bucket width; must match the bin_seconds the UI uses
SYMBOL_PATTERN = re.compile(r"^\d+$")
# The fixed lookback windows a volume-at-price profile is recorded for. Mirrors
# terminal/src/lib/window.ts and the old metrics_5m suffixes (1h/2h/3h/4h).
WINDOWS = (60, 120, 180, 240)
# The refresh grid: the cron/systemd job runs every this many minutes.
REFRESH_MINUTES = int(os.environ.get("CACHE_REFRESH_MINUTES", "5") or 5) or 5
# How many stops to keep on record per (symbol, window) before pruning.
VAP_KEEP = int(os.environ.get("VAP_KEEP", "500") or 500)

cfg = load_config()
reader = KustoReader(cfg)
# Built assets (Vite writes them to dist/assets) are served at /assets.
app = Flask(__name__, static_folder=os.path.join(cfg.frontend_dist, "assets"), static_url_path="/assets")


def _scale(value):
    return None if value is None else value / cfg.price_scale


def _symbol_arg():
    value = request.args.get("symbol", "")
    if not SYMBOL_PATTERN.match(value):
        abort(400, "symbol must be a numeric symbol id")
    return value


def _int_arg(name, default, low, high):
    raw = request.args.get(name, str(default))
    if not raw.isdigit():
        abort(400, f"{name} must be a whole number")
    value = int(raw)
    if not low <= value <= high:
        abort(400, f"{name} must be between {low} and {high}")
    return value


@app.get("/")
def index():
    return send_from_directory(cfg.frontend_dist, "index.html")


@app.get("/favicon.png")
def favicon():
    return send_from_directory(cfg.frontend_dist, "favicon.png")


@app.get("/api/symbols")
def symbols():
    rows = reader.run(queries.symbols(cfg.table))
    return jsonify([row["symbolId"] for row in rows])


@app.get("/api/series")
def series():
    symbol_id = _symbol_arg()
    minutes = _int_arg("minutes", 60, 1, 1440)
    bin_seconds = _int_arg("bin_seconds", 10, 1, 3600)

    rows = reader.run(queries.series(cfg.table, symbol_id, minutes, bin_seconds))
    for row in rows:
        row["best_bid"] = _scale(row["best_bid"])
        row["best_ask"] = _scale(row["best_ask"])
    return jsonify(rows)


@app.get("/api/book")
def book():
    symbol_id = _symbol_arg()
    rows = reader.run(queries.latest_book(cfg.table, symbol_id))
    if not rows:
        return jsonify(None)

    row = rows[0]
    bids = json.loads(row["bids"]) if row["bids"] else []
    asks = json.loads(row["asks"]) if row["asks"] else []

    return jsonify({
        "timestamp": row["timestamp"],
        "best_bid": _scale(row["best_bid"]),
        "best_ask": _scale(row["best_ask"]),
        "bids": [[_scale(price), volume] for price, volume in bids[:BOOK_LEVELS]],
        "asks": [[_scale(price), volume] for price, volume in asks[:BOOK_LEVELS]],
    })


def _series_rows(symbol_id, minutes):
    rows = reader.run(queries.series(cfg.table, symbol_id, minutes, BIN_SECONDS))
    for row in rows:
        row["best_bid"] = _scale(row["best_bid"])
        row["best_ask"] = _scale(row["best_ask"])
    return rows


@app.get("/api/kpis")
def kpis():
    symbol_id = _symbol_arg()
    minutes = _int_arg("minutes", 240, 1, 1440)
    return jsonify(analytics.kpis(_series_rows(symbol_id, minutes), BIN_SECONDS))


@app.get("/api/spread")
def spread():
    symbol_id = _symbol_arg()
    minutes = _int_arg("minutes", 240, 1, 1440)
    return jsonify(analytics.spread(_series_rows(symbol_id, minutes)))


@app.get("/api/volatility")
def volatility():
    symbol_id = _symbol_arg()
    minutes = _int_arg("minutes", 240, 1, 1440)
    window = _int_arg("window", 30, 2, 1000)
    return jsonify(analytics.rolling_volatility(_series_rows(symbol_id, minutes), BIN_SECONDS, window))


@app.get("/api/acf")
def acf():
    symbol_id = _symbol_arg()
    minutes = _int_arg("minutes", 240, 1, 1440)
    max_lag = _int_arg("max_lag", 30, 1, 100)
    return jsonify(analytics.autocorrelation(_series_rows(symbol_id, minutes), max_lag))


@app.get("/api/activity")
def activity():
    symbol_id = _symbol_arg()
    days = _int_arg("days", 1, 1, 30)
    return jsonify(reader.run(queries.activity(cfg.table, symbol_id, days)))


@app.get("/api/tick-gaps")
def tick_gaps():
    symbol_id = _symbol_arg()
    minutes = _int_arg("minutes", 60, 1, 1440)
    rows = reader.run(queries.tick_times(cfg.table, symbol_id, minutes))
    return jsonify(analytics.tick_gaps(rows))


def _vap_scan_minutes():
    """The widest window, so one Kusto scan feeds every lookback."""
    return max(WINDOWS)


def _vap_as_of(rows):
    """The stop a frame is 'as of' - its newest stamp, floored to the 5-min grid.

    Flooring to the grid (not the raw tick time) is what makes two refreshes
    inside one 5-minute slot collapse onto the same row, so record-keeping stays
    one row per stop instead of one per run.
    """
    stamps = [s for s in (analytics._stamp(r) for r in rows) if s is not None]
    if not stamps:
        return datetime.now(timezone.utc).isoformat()

    newest = datetime.fromtimestamp(max(stamps) / 1000, tz=timezone.utc)
    floored = newest.replace(second=0, microsecond=0)
    return floored.replace(
        minute=(floored.minute // REFRESH_MINUTES) * REFRESH_MINUTES
    ).isoformat()


def _compute_vap(symbol_id):
    """Read the tick frame once, derive every window's profile, and record it.

    Prices come back raw (x PRICE_SCALE) from Kusto, which is exactly what
    `analytics.profiles_by_window` expects - it divides them down itself - so the
    stored bins are in display units and match `/api/series`.
    """
    rows = reader.run(queries.vap_ticks(cfg.table, symbol_id, _vap_scan_minutes()))
    profiles = analytics.profiles_by_window(rows, cfg.price_scale, WINDOWS)
    as_of = _vap_as_of(rows)

    conn = db.connect(cfg.db_path)
    try:
        db.save_profiles(conn, symbol_id, profiles, as_of)
        db.prune(conn, VAP_KEEP)
    finally:
        conn.close()
    return symbol_id, as_of, profiles


def _vap_payload(symbol_id, window, source):
    conn = db.connect(cfg.db_path)
    try:
        stored = db.load_profile(conn, symbol_id, window)
    finally:
        conn.close()
    if stored is None:
        return None
    stored["source"] = source
    return stored


@app.get("/api/volume-at-price")
def volume_at_price():
    symbol_id = _symbol_arg()
    window = _int_arg("window", max(WINDOWS), min(WINDOWS), max(WINDOWS))
    refresh = request.args.get("refresh", "") in ("1", "true", "yes")

    if refresh:
        _compute_vap(symbol_id)
        return jsonify(_vap_payload(symbol_id, window, "kusto"))

    stored = _vap_payload(symbol_id, window, "local")
    if stored is not None:
        return jsonify(stored)

    # Nothing on record yet (first ever run): derive and store on demand.
    _compute_vap(symbol_id)
    payload = _vap_payload(symbol_id, window, "kusto")
    if payload is None:
        return jsonify(None)
    return jsonify(payload)


@app.get("/api/volume-at-price/windows")
def volume_at_price_windows():
    """Every lookback's stored profile for one symbol, newest stop each."""
    symbol_id = _symbol_arg()
    out = {}
    conn = db.connect(cfg.db_path)
    try:
        for window in WINDOWS:
            stored = db.load_profile(conn, symbol_id, window)
            if stored is not None:
                stored["source"] = "local"
                out[str(window)] = stored
    finally:
        conn.close()
    return jsonify(out)


@app.post("/api/volume-at-price/refresh")
def volume_at_price_refresh():
    """Compute and record every window's profile for the symbol. The cron entry."""
    symbol_id = _symbol_arg()
    _, as_of, profiles = _compute_vap(symbol_id)
    return jsonify({
        "symbol": symbol_id,
        "as_of": as_of,
        "windows": {
            str(w): {"poc": p["poc"], "value_area": p["value_area"],
                     "total_volume": p["total_volume"], "bins": len(p["bins"])}
            for w, p in profiles.items()
        },
    })


if __name__ == "__main__":
    # Bound to localhost only. This app has no login, so don't expose it publicly.
    app.run(host="127.0.0.1", port=5000, debug=False)