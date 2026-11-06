import json
import os
import re

from flask import Flask, abort, jsonify, request, send_from_directory

import analytics
import queries
from config import load_config
from kusto_client import KustoReader

BOOK_LEVELS = 10
BIN_SECONDS = 10  # series bucket width; must match the bin_seconds the UI uses
SYMBOL_PATTERN = re.compile(r"^\d+$")

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


if __name__ == "__main__":
    # Bound to localhost only. This app has no login, so don't expose it publicly.
    app.run(host="127.0.0.1", port=5000, debug=False)