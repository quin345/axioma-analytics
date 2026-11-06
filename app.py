import json
import re

from flask import Flask, abort, jsonify, render_template, request

import queries
from config import load_config
from kusto_client import KustoReader

BOOK_LEVELS = 10
SYMBOL_PATTERN = re.compile(r"^\d+$")

cfg = load_config()
reader = KustoReader(cfg)
app = Flask(__name__)


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
    return render_template("index.html")


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


if __name__ == "__main__":
    # Bound to localhost only. This app has no login, so don't expose it publicly.
    app.run(host="127.0.0.1", port=5000, debug=False)