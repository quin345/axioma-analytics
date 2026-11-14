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

# The feed's broker spelling -> the metadata catalogue's spelling.
#
# The two surfaces do not agree on how a broker's name is written: the order-book
# feed ships `icmarkets` while the Fabric `ctrader_metadata` catalogue records the
# same broker as `icmarketssc`. The catalogue is the authority on identity (it
# owns the account and symbol catalogue), so the feed's spelling is normalised to
# it before the join - a raw string comparison would silently fail to match and
# every one of that broker's instruments would come through unmapped.
#
# Only spellings that are *known* variants belong here; an unmapped name is left
# as-is, so a genuinely new broker shows up as unmapped rather than being guessed
# onto an existing one. Keys are the feed's (lowercased) spelling.
BROKER_ALIASES = {
    "icmarkets": "icmarketssc",
}

cfg = load_config()
reader = KustoReader(cfg)
# Built assets (Vite writes them to dist/assets) are served at /assets.
app = Flask(__name__, static_folder=os.path.join(cfg.frontend_dist, "assets"), static_url_path="/assets")
# The holding page lives next to this module, not in the built bundle, so it
# still renders while the dashboard is being worked on.
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _scale(value):
    return None if value is None else value / cfg.price_scale


def _holding_page():
    """The 'service not available' page, sent with a 503.

    503 rather than 200 so proxies and uptime checks do not treat the holding
    page as a healthy dashboard.
    """
    return send_from_directory(STATIC_DIR, "maintenance.html"), 503


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
    # While we are still working on the terminal, `/` is the holding page with
    # a 503; the API routes below stay up so the refresh job and any tooling
    # keep working. Flip MAINTENANCE_MODE off in .env and restart to serve the
    # dashboard again.
    if cfg.maintenance:
        return _holding_page()
    return send_from_directory(cfg.frontend_dist, "index.html")


@app.get("/unavailable")
def unavailable():
    """The holding page at its own path, so it can be previewed even when the
    dashboard is live (`/` only swaps to it under MAINTENANCE_MODE)."""
    return send_from_directory(STATIC_DIR, "maintenance.html")


@app.get("/favicon.png")
def favicon():
    return send_from_directory(cfg.frontend_dist, "favicon.png")


@app.get("/favicon.svg")
def favicon_svg():
    # The vector mark the terminal's index.html prefers; the PNG above is the
    # fallback for browsers that do not take SVG favicons.
    return send_from_directory(cfg.frontend_dist, "favicon.svg",
                               mimetype="image/svg+xml")


@app.get("/api/symbols")
def symbols():
    """The collected instruments, enriched with the broker/symbol mapping.

    The feed is the source of truth for *what is collected*, and it carries the
    same triple the metadata catalogue is keyed on -
    `(broker, ctidTraderAccountId, symbolId)`. The local `symbol_mapping`
    snapshot (from the Fabric `ctrader_metadata` database) is what turns that
    triple into an instrument: its broker name, the standardized name and the
    canonical asset class.

    The join is on the **full triple**, never on `symbolId` alone: a symbol id is
    shared across brokers and means a different instrument on each - the same
    id is `XAUUSD.pro` on one broker and something else on another - so matching
    on the id by itself would mislabel instruments. A collected triple with no
    mapping row still comes through, with null metadata rather than being
    dropped: that is the honest answer for an instrument the catalogue does not
    (yet) describe.
    """
    collected = _collected_instruments()

    conn = db.connect(cfg.db_path)
    try:
        mapping = _mapping_by_triple(conn)
    finally:
        conn.close()

    return jsonify([_instrument(row, mapping.get(_triple(row)))
                    for row in collected])


def _collected_instruments():
    """The collected (broker, account, symbol) rows from the feed."""
    return reader.run(queries.symbols(cfg.table))


def _triple(row):
    """The join key: broker + account + symbol id, all as text.

    Works on both sides of the join, which name the columns differently: the
    feed (Kusto) says `ctidTraderAccountId` / `symbolId`, the local snapshot says
    `ctid_trader_account` / `symbol_id`. Either spelling is accepted so the two
    produce the same key. Everything is normalised to text, so an integer id on
    one side matches the string on the other, and the broker is normalised
    through `BROKER_ALIASES` so the feed's spelling meets the catalogue's.
    """
    return (
        canonical_broker(_first(row, "broker")),
        _text(_first(row, "ctidTraderAccountId", "ctid_trader_account", "account")),
        _text(_first(row, "symbolId", "symbol_id")),
    )


def canonical_broker(name):
    """A broker's catalogue name for its feed spelling.

    The feed and the catalogue write some brokers differently (`icmarkets` vs
    `icmarketssc`); the catalogue owns identity, so the feed's spelling is mapped
    onto it. A name with no alias is returned as it stands - normalised to text
    but otherwise untouched - so an unknown broker is left to come through
    unmapped rather than being guessed onto a known one.
    """
    text = _text(name)
    if text is None:
        return None
    return BROKER_ALIASES.get(text.strip().lower(), text)


def _first(row, *names):
    for name in names:
        if row.get(name) is not None:
            return row[name]
    return None


def _text(value):
    return None if value is None else str(value)


def _mapping_by_triple(conn):
    """The snapshot keyed by (broker, account, symbol id) - the catalogue's key."""
    return {_triple(row): row for row in db.load_symbol_mapping(conn)}


def _instrument(row, mapped):
    """One `/api/symbols` entry: the collected triple plus whatever the map knows."""
    out = {
        "symbolId": _text(_first(row, "symbolId", "symbol_id")),
        # Provenance: the canonical broker name and the account. Reported from
        # the feed when there is no catalogue row, so an unmapped instrument
        # still shows where it came from.
        "broker": canonical_broker(_first(row, "broker")),
        "account": _text(_first(row, "ctidTraderAccountId", "ctid_trader_account", "account")),
        "name": None,
        "standardizedName": None,
        "assetClass": None,
        # When the feed last ticked for this instrument. A closed market (gold
        # at the weekend) is still listed; the UI shows it as not live.
        "lastSeen": _text(row.get("last_seen")),
    }
    if mapped is not None:
        out["name"] = mapped["symbol_name"]
        # The display name the UI shows; falls back to the broker's own name.
        out["standardizedName"] = mapped["standardized_name"] or mapped["symbol_name"]
        out["assetClass"] = mapped["asset_class"]
    return out


@app.get("/api/asset-classes")
def asset_classes():
    """The canonical asset classes and how many collected instruments are in each.

    Counted over the same collected triples `/api/symbols` serves, so the two
    agree. The canonical order is fixed so the UI does not reshuffle between
    polls; a class with no instruments still appears, with a zero, and anything
    unmapped is reported under its own key rather than being hidden.
    """
    collected = _collected_instruments()

    conn = db.connect(cfg.db_path)
    try:
        mapping = _mapping_by_triple(conn)
    finally:
        conn.close()

    names = list(db.ASSET_CLASSES)
    counts = {name: 0 for name in names}
    for row in collected:
        mapped = mapping.get(_triple(row))
        key = mapped["asset_class"] if mapped else None
        if key not in counts:
            # Unmapped, or a class the map did not recognise (e.g. "Other").
            key = key or db.ASSET_CLASS_UNKNOWN
            counts.setdefault(key, 0)
            if key not in names:
                names.append(key)
        counts[key] += 1

    return jsonify([{"assetClass": name, "count": counts[name]} for name in names])



def _account_arg():
    """The optional trader account that scopes a symbol id to one broker.

    Omitted, queries fall back to the bare symbol id (the old behaviour); the
    UI always sends it, because a symbol id alone is shared across brokers.
    """
    value = request.args.get("account", "")
    if value and not SYMBOL_PATTERN.match(value):
        abort(400, "account must be a numeric ctidTraderAccountId")
    return value or None


def _instrument_args():
    return _symbol_arg(), _account_arg()


def _series_rows(symbol_id, minutes, account=None, bin_seconds=BIN_SECONDS):
    rows = reader.run(queries.series(cfg.table, symbol_id, minutes, bin_seconds, account))
    for row in rows:
        row["best_bid"] = _scale(row["best_bid"])
        row["best_ask"] = _scale(row["best_ask"])
    return rows


@app.get("/api/series")
def series():
    symbol_id, account = _instrument_args()
    minutes = _int_arg("minutes", 60, 1, 1440)
    bin_seconds = _int_arg("bin_seconds", 10, 1, 3600)
    return jsonify(_series_rows(symbol_id, minutes, account, bin_seconds))


@app.get("/api/book")
def book():
    symbol_id, account = _instrument_args()
    rows = reader.run(queries.latest_book(cfg.table, symbol_id, account))
    if not rows:
        return jsonify(None)

    row = rows[0]
    bids = json.loads(row["bids"]) if row["bids"] else []
    asks = json.loads(row["asks"]) if row["asks"] else []

    return jsonify({
        "timestamp": row["timestamp"],
        "best_bid": _scale(row["best_bid"]),
        "best_ask": _scale(row["best_ask"]),
        "total_bid": row.get("total_bid"),
        "total_ask": row.get("total_ask"),
        "bids": [[_scale(price), volume] for price, volume in bids[:BOOK_LEVELS]],
        "asks": [[_scale(price), volume] for price, volume in asks[:BOOK_LEVELS]],
    })


@app.get("/api/kpis")
def kpis():
    symbol_id, account = _instrument_args()
    minutes = _int_arg("minutes", 240, 1, 1440)
    return jsonify(analytics.kpis(_series_rows(symbol_id, minutes, account), BIN_SECONDS))


@app.get("/api/spread")
def spread():
    symbol_id, account = _instrument_args()
    minutes = _int_arg("minutes", 240, 1, 1440)
    return jsonify(analytics.spread(_series_rows(symbol_id, minutes, account)))


@app.get("/api/volatility")
def volatility():
    symbol_id, account = _instrument_args()
    minutes = _int_arg("minutes", 240, 1, 1440)
    window = _int_arg("window", 30, 2, 1000)
    return jsonify(analytics.rolling_volatility(
        _series_rows(symbol_id, minutes, account), BIN_SECONDS, window))


@app.get("/api/acf")
def acf():
    symbol_id, account = _instrument_args()
    minutes = _int_arg("minutes", 240, 1, 1440)
    max_lag = _int_arg("max_lag", 30, 1, 100)
    return jsonify(analytics.autocorrelation(_series_rows(symbol_id, minutes, account), max_lag))


@app.get("/api/activity")
def activity():
    symbol_id, account = _instrument_args()
    days = _int_arg("days", 1, 1, 30)
    return jsonify(reader.run(queries.activity(cfg.table, symbol_id, days, account)))


@app.get("/api/activity-days")
def activity_days():
    """Event count per UTC day - the activity calendar."""
    symbol_id, account = _instrument_args()
    days = _int_arg("days", 42, 1, 120)
    rows = reader.run(queries.activity_days(cfg.table, symbol_id, days, account))
    return jsonify([{"day": str(r["day"])[:10], "events": r["events"]} for r in rows])


@app.get("/api/tick-gaps")
def tick_gaps():
    symbol_id, account = _instrument_args()
    minutes = _int_arg("minutes", 60, 1, 1440)
    rows = reader.run(queries.tick_times(cfg.table, symbol_id, minutes, account))
    return jsonify(analytics.tick_gaps(rows))


# --- Trade-flow proxies ------------------------------------------------------
# The feed has no trades; these routes serve the closest book-derived stand-in
# (see analytics' "Trade-flow proxies" section). Each payload names its proxy.

@app.get("/api/order-flow")
def order_flow():
    symbol_id, account = _instrument_args()
    minutes = _int_arg("minutes", 240, 1, 1440)
    bin_seconds = _int_arg("bin_seconds", 60, 10, 3600)
    return jsonify(analytics.order_flow(_series_rows(symbol_id, minutes, account, bin_seconds)))


@app.get("/api/impact")
def impact():
    symbol_id, account = _instrument_args()
    minutes = _int_arg("minutes", 240, 1, 1440)
    return jsonify(analytics.burst_impact(
        _series_rows(symbol_id, minutes, account), bin_seconds=BIN_SECONDS))


@app.get("/api/ticks")
def ticks():
    symbol_id, account = _instrument_args()
    limit = _int_arg("limit", 14, 1, 200)
    rows = reader.run(queries.recent_ticks(cfg.table, symbol_id, limit, account))
    return jsonify(analytics.raw_ticks(rows, cfg.price_scale)[:limit])


# --- Volume-at-price ---------------------------------------------------------

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


def _compute_vap(symbol_id, account=None):
    """Read the tick frame once, derive every window's profile, and record it.

    Prices come back raw (x PRICE_SCALE) from Kusto, which is exactly what
    `analytics.profiles_by_window` expects - it divides them down itself - so the
    stored bins are in display units and match `/api/series`. The record is
    keyed on `db.instrument_key`, so two brokers sharing an id never collide.
    """
    rows = reader.run(queries.vap_ticks(cfg.table, symbol_id, _vap_scan_minutes(), account))
    profiles = analytics.profiles_by_window(rows, cfg.price_scale, WINDOWS)
    as_of = _vap_as_of(rows)

    conn = db.connect(cfg.db_path)
    try:
        db.save_profiles(conn, db.instrument_key(symbol_id, account), profiles, as_of)
        # `vap_run` is the durable record: never capped here. Only the ladder
        # detail (`vap_level`) is trimmed, on the configured hour window.
        db.prune_runs(conn, keep=cfg.vap_keep_runs)
        db.prune_levels(conn, keep_hours=cfg.vap_level_hours)
    finally:
        conn.close()
    return symbol_id, as_of, profiles


def _vap_payload(symbol_id, window, source, account=None):
    conn = db.connect(cfg.db_path)
    try:
        stored = db.load_profile(conn, db.instrument_key(symbol_id, account), window)
    finally:
        conn.close()
    if stored is None:
        return None
    stored["source"] = source
    return stored


@app.get("/api/volume-at-price")
def volume_at_price():
    symbol_id, account = _instrument_args()
    window = _int_arg("window", max(WINDOWS), min(WINDOWS), max(WINDOWS))
    if window not in WINDOWS:
        abort(400, f"window must be one of {', '.join(map(str, WINDOWS))}")
    refresh = request.args.get("refresh", "") in ("1", "true", "yes")

    if refresh:
        _compute_vap(symbol_id, account)
        return jsonify(_vap_payload(symbol_id, window, "kusto", account))

    stored = _vap_payload(symbol_id, window, "local", account)
    if stored is not None:
        return jsonify(stored)

    # Nothing on record yet (first ever run): derive and store on demand.
    _compute_vap(symbol_id, account)
    return jsonify(_vap_payload(symbol_id, window, "kusto", account))


@app.get("/api/volume-at-price/windows")
def volume_at_price_windows():
    """Every lookback's stored profile for one symbol, newest stop each."""
    symbol_id, account = _instrument_args()
    out = {}
    for window in WINDOWS:
        stored = _vap_payload(symbol_id, window, "local", account)
        if stored is not None:
            out[str(window)] = stored
    return jsonify(out)


@app.post("/api/volume-at-price/refresh")
def volume_at_price_refresh():
    """Compute and record every window's profile for the symbol. The cron entry."""
    symbol_id, account = _instrument_args()
    _, as_of, profiles = _compute_vap(symbol_id, account)
    return jsonify({
        "symbol": symbol_id,
        "account": account,
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
