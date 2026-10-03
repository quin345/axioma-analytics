# Axioma Analytics

Tick-data microstructure analytics over a **Microsoft Fabric SQL analytics endpoint**
(TDS / `datawarehouse.fabric.microsoft.com`).

The app auto-discovers your tick table, normalises whatever column names you use,
and renders a full microstructure dashboard: OHLCV bars, order-flow imbalance,
spread evolution, volume-at-price, return distribution, drawdown, autocorrelation
and large-trade market impact.

---

## Quick start

```bash
pip install -r requirements.txt
python scripts/check_connection.py     # verify auth + discover tick tables
python run.py                          # dashboard on http://127.0.0.1:8000
```

---

## Configuration (`.env`)

```ini
SQL_ANALYTICS_ENDPOINT="<workspace-id>.datawarehouse.fabric.microsoft.com"

FABRIC_TENANT_ID="<tenant-guid>"
FABRIC_CLIENT_ID="<app-registration-client-id>"
FABRIC_CLIENT_SECRET="<client-secret>"

# Optional: pin the tick table. Leave blank to auto-discover.
FABRIC_TICK_SCHEMA=
FABRIC_TICK_TABLE=

FABRIC_ALLOW_SYNTHETIC="true"   # demo fallback when the warehouse has no ticks
MAX_TICKS="200000"
```

`.env` is already git-ignored. Never commit the secret.

---

## Authentication

Two modes, tried in order:

| Mode | When | Notes |
|---|---|---|
| **Service principal** | `FABRIC_*` credentials present | Headless, recommended. Needs the **Contributor** workspace role (see below). |
| **`az login` token** | No credentials set | Mints a token via the Azure CLI and passes it as `SQL_COPT_SS_ACCESS_TOKEN`. Convenient for local debugging. |

### Granting access

Fabric workspace roles are **not** Azure RBAC — `az role assignment` does not apply,
and there is no `az fabric` CLI extension. Grant via the Fabric portal
(*workspace → Manage access → Add person or service principal → **Viewer***),
or through the Fabric REST API:

```powershell
$tok = az account get-access-token --resource https://api.fabric.microsoft.com
$h = @{ Authorization = "Bearer $($tok.accessToken)" }
$body = @{ principal = @{ id = "<sp-object-id>"; type = "ServicePrincipal" }; role = "Viewer" } | ConvertTo-Json -Depth 5
Invoke-RestMethod "https://api.fabric.microsoft.com/v1/workspaces/<workspace-id>/roleAssignments" -Method Post -Headers $h -Body $body -ContentType application/json
```

`Viewer` grants `CONNECT` + `ReadData` — the read-only rights this app needs.

---

## Current warehouse state

The endpoint spans **two databases** (one per workspace item):

| Database | Table | Rows | Reader |
|---|---|---:|---|
| `ctrader` | `ctrader.dom_pepperstone` | 37k+ (live) | `l2` |
| `ctrader` | `ctrader.market_depth` / `_v2` | 0 | - |
| `ctrader_lakehouse` | `gold.agg_dom_book_snapshot` | 27,632 | `agg` |
| `ctrader_lakehouse` | `dbo.silver_dom_book_snapshot` | 128,328 | `levels` |
| `ctrader_lakehouse` | `gold.symbols_pepperstone` | 1,941 | symbol dimension |

### Four source shapes, one analytics engine

`app/books.py` classifies each table and normalises it to the canonical tick frame
(`ts | symbol | bid | ask | last | volume`), so every panel works on any source:

- **`agg`** - `gold.agg_dom_book_snapshot`: one pre-aggregated row per symbol/time
  with `best_bid`, `best_ask`, `total_bid`, `total_ask`, `imbalance`,
  `imbalance_ratio`, `vwap_bid`, `vwap_ask`, `vwap_spread`, `rel_spread`.
  The pipeline's own imbalance is used as the directional signal. One-sided rows
  (no best bid or ask) are dropped because they cannot produce a mid.
- **`levels`** - `dbo.silver_dom_book_snapshot`: one row per resting price level
  (`symbolId, quoteId, timestamp, side, price, size`). Collapsed to one snapshot
  per `(symbol, timestamp)` with best bid/ask, top-N depth and a depth curve.
  One-sided and crossed snapshots are skipped.
- **`l2`** - `ctrader.dom_pepperstone`: raw `newQuotes`/`deletedQuotes` deltas
  with scaled integer prices (`/ 10**digits`), replayed by `app/l2.py`.
- **`flat`** - conventional `ts/bid/ask/last/volume` tables.

### Symbol names

`gold.symbols_pepperstone` maps `symbolId -> symbolName`, so the UI shows
`BTCUSD`, `ETHUSD`, `EURUSD` instead of `10028`, `10029`, `1`. It is discovered
automatically (any table with a symbol id + name column), and every tick symbol
resolves (1,868/1,868).

### Two correctness guards in the L2 replay

1. **Session reset** - the feed stops overnight and restarts with fresh quote ids.
   Replaying through a long gap leaves stale levels and produces *crossed books*
   (observed: 65% of ticks). The book is discarded after `session_gap_seconds`
   (default 30 min) of silence.
2. **Crossed-book rejection** - a replay yielding `bid >= ask` is dropped, never
   emitted as a negative spread.

After the guards: **0 crossed books, 100% positive spreads** on the full replay.

---

## Architecture

```
app/
  config.py      .env -> Settings (secrets redacted)
  auth.py        az CLI / service-principal token minting
  db.py          connection, all-database discovery, tick fetch, symbol dimension
  books.py       readers for agg / levels book snapshots (+ classification)
  schema.py      tolerant column mapping -> canonical tick frame
  analytics.py   all computations (pure functions, no I/O)
  synthetic.py   realistic tick generator (fallback/demo)
  service.py     source resolution, health probing, TTL cache
  main.py        FastAPI app + static dashboard
  static/        dashboard (Chart.js)
scripts/
  check_connection.py   CLI: auth check + tick-table discovery
tests/
  test_analytics.py     17 tests, no warehouse required
```

The canonical tick frame is:

```
ts (datetime, UTC) | symbol | bid | ask | last | volume | mid
```

`schema.ALIASES` maps many spellings (`bid`, `BidPrice`, `bid_price`, `b`…) onto
those fields, so most warehouse layouts work without configuration.

---

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health?refresh=true` | Connection status, tables, hints |
| `GET /api/sources` | Available sources + symbols |
| `GET /api/symbols?source=` | Symbols for one source |
| `GET /api/analytics` | Full analytics bundle |
| `POST /api/query?sql=` | One read-only `SELECT` (schema exploration) |

`/api/analytics` parameters: `source`, `symbol`, `timeframe`, `window`, `bins`,
`limit`, `lookback_days`.

---

## Analytics reference

| Block | Metrics |
|---|---|
| Summary | ticks, OHLC, change, realised + annualised vol, spread stats, ticks/min, inter-arrival |
| OHLCV | resampled bars (1s → 1d), VWAP, per-bar spread and tick count |
| Microstructure | order-flow imbalance, spread evolution, inter-arrival, large trades + 1-tick impact |
| Volume profile | volume-at-price, POC, 70% value area, high-volume nodes |
| Rolling | rolling annualised volatility, rolling spread, cumulative return |
| Distribution | histogram, skew, excess kurtosis, Jarque-Bera, bullish ratio |
| Book depth | bid/ask resting size, depth imbalance series, levels per side |
| Drawdown | drawdown path, worst episodes with peak/trough/recovery |
| Behaviour | autocorrelation, efficiency ratio, Hurst, variance ratios |
| Hourly | activity, volume, return and volatility by hour (UTC) |

**Design notes**

- Order-flow imbalance uses signed volume (`volume × tick direction`). With no
  volume column it degrades to a tick-direction imbalance, so the panel always works.
- Realised volatility is scaled by the observed tick rate, making it comparable
  across symbols with different sampling densities.
- Every payload passes through `analytics.clean()`, replacing `NaN`/`Inf` with
  `None` — `json.dumps` emits bare `NaN`, which the browser's `JSON.parse` rejects.
- Empty resample bins are dropped, so charts never receive all-null bars.

---

## Testing

```bash
python -m pytest tests -q
```

Covers column mapping, mid derivation, bar consistency, OFI bounds, volume
profile mass conservation, drawdown sign, strict JSON serialisability, and a
degenerate flat-price series (guards against divide-by-zero).

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Could not login because the authentication failed (18456)` | SP has no workspace role. Grant **Viewer**. |
| `Invalid value specified for connection string attribute 'PWD'` | Secret missing/blank in `.env`. |
| `Invalid value ... 'Authentication'` | `Authentication` combined with a token attribute. |
| `no table matched the tick signature` | Table has no timestamp + bid/ask/last. Check with `scripts/check_connection.py`. |
| `N of M table(s) are empty` | Ingest pipeline has not written rows yet. |
| `external policy action ... was denied` | **Viewer** role: OneLake security filters Viewers and hides whole tables. Grant **Contributor**. |
Until then the **Synthetic** source gives a fully working demo with realistic ticks.