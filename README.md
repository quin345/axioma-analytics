# Axioma Analytics

Microstructure analytics over the **production gold database**. Every instrument
is categorised by asset class using the pipeline's own classification, and the
dashboard renders OHLCV bars, order-flow imbalance, spread evolution,
volume-at-price, return distribution, drawdown, autocorrelation and large-trade
market impact.

The app talks to exactly one source: `[ctrader_lakehouse].[gold]` on the
production Fabric SQL analytics endpoint. There is no environment switcher, no
catalog discovery and no synthetic/demo fallback. Storage internals (endpoint
host, database, table) are never sent to the browser.

---

## Quick start

```bash
pip install -r requirements.txt
python run.py             # dashboard on http://127.0.0.1:8000
```

---

## Configuration (`.env`)

```ini
# The production endpoint.
SQL_ENDPOINT_PROD="<prod-host>"

# Entra ID service principal.
FABRIC_TENANT_ID="<tenant-guid>"
FABRIC_CLIENT_ID="<app-registration-client-id>"
FABRIC_CLIENT_SECRET="<client-secret>"

# Gold objects.
GOLD_DATABASE="ctrader_lakehouse"
GOLD_SCHEMA="gold"
SNAPSHOT_TABLE="agg_dom_book_snapshot"
SYMBOL_TABLE="symbols_icmarkets"

MAX_TICKS="200000"
```

`.env` is already git-ignored (see `.env.example` for the template). Never commit
the secret.

### Asset classification

The gold layer publishes its own chain, which the app consumes directly rather
than guessing:

```
gold.symbols_icmarkets          symbolId, symbolName, symbolCategoryId, description
  -> gold.symbols_category_icmarkets    symbolCategoryId -> assetClassId
  -> gold.asset_classes_icmarkets       assetClassId    -> name
```

icmarkets names nine classes; `app/assets.py` maps them to stable client keys
and sub-divides FX (majors / crosses / exotics), which the pipeline lumps
together but a dashboard benefits from:

| Broker class | Client key |
|---|---|
| Forex | `fx_major`, `fx_cross`, `fx_exotic` |
| Metals | `metal` |
| Oil | `energy` |
| Commodities | `commodity` |
| Indices | `index` |
| Cryptocurrencies | `crypto` |
| Bonds | `bond` |
| Futures / Futures Commodities | `future` |

Where the chain has no row — an archive entry, or a new listing — the classifier
falls back to the same cTrader signals present in the symbol rows
(`symbolCategoryId`, the ticker and the description), so no instrument silently
disappears from the selector. Anything still unresolved is reported as
`Unclassified`, and `/api/health` says how many instruments are affected.

Note that `agg_dom_book_snapshot` is shared and accumulates rows from every feed
that writes to it, while `symbols_icmarkets` describes only icmarkets. Any
other feed's instruments therefore appear as bare ids with no ticker — the
health hint names that explicitly rather than leaving it to be guessed at.

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
(*workspace → Manage access → Add people or service principals*), or through the
Fabric REST API:

```powershell
$tok = az account get-access-token --resource https://api.fabric.microsoft.com
$h = @{ Authorization = "Bearer $($tok.accessToken)" }
$body = @{ principal = @{ id = "<sp-object-id>"; type = "ServicePrincipal" }; role = "Contributor" } | ConvertTo-Json -Depth 5
Invoke-RestMethod "https://api.fabric.microsoft.com/v1/workspaces/<workspace-id>/roleAssignments" -Method Post -Headers $h -Body $body -ContentType application/json
```

`Contributor` grants `CONNECT` + `ReadData` on every Lakehouse/Warehouse in the
workspace — the rights this read-only app needs (and it avoids the OneLake
security filtering that can hide whole tables from `Viewer`).

The service principal needs the role on the workspace it reads from (`prod_axioma`).
Run the helper while signed in with `az login` as a workspace **Admin**:

```bash
python scripts/grant_workspace_access.py --list     # show workspaces + ids
python scripts/grant_workspace_access.py --dry-run  # preview only
python scripts/grant_workspace_access.py            # apply
```

Resolves the principal object ID from `FABRIC_CLIENT_ID`, matches workspaces by
display name, and is idempotent — it skips workspaces already holding the role,
updates the role when it differs, and creates the assignment only when missing.
Pass `--role Viewer` for a read-only grant, or `--workspaces <name>` to target one.

---

## Data source shape

`gold.agg_dom_book_snapshot` holds one pre-aggregated row per symbol and
timestamp, with `best_bid`, `best_ask`, `total_bid`, `total_ask`, `imbalance`,
`imbalance_ratio`, `vwap_bid`, `vwap_ask`, `vwap_spread` and `rel_spread`.
`app/frames.py` normalises it to the canonical tick frame
(`ts | symbol | bid | ask | last | volume`) so every dashboard panel works
unchanged. The pipeline's own imbalance is used as the directional signal;
one-sided rows (no best bid or ask) and crossed rows (`bid >= ask`) are dropped
because they cannot produce a valid mid.

### Symbol names

`gold.symbols_icmarkets` supplies `symbolId -> symbolName`, so the UI shows
`BTCUSD`, `ETHUSD`, `EURUSD` instead of raw numeric ids. Table names are never
exposed.

---

## Architecture

```
app/
  config.py      .env -> Settings (endpoint, credentials, gold object names)
  auth.py        az CLI / service-principal token minting
  db.py          connection, gold queries, snapshot fetch, symbol catalogue
  frames.py      gold snapshot reader -> canonical tick frame
  assets.py      asset-class taxonomy and classification
  analytics.py   all computations (pure functions, no I/O)
  service.py     instrument catalogue, health probing, TTL cache
  main.py        FastAPI app + static dashboard
  static/        dashboard (Chart.js)
scripts/
  grant_workspace_access.py   grant the SP a role on the workspace
tests/
  test_analytics.py / test_frames.py / test_assets.py / test_config.py
```

The canonical tick frame is:

```
ts (datetime, UTC) | symbol | bid | ask | last | volume | mid
```

---

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health?refresh=true` | Connection status, coverage and hints (no storage details) |
| `GET /api/asset-classes` | The class taxonomy plus a per-class instrument rollup |
| `GET /api/symbols?asset_class=&family=&include_idle=` | Instruments grouped by asset class |
| `GET /api/analytics` | Full analytics bundle |

`/api/symbols` returns `groups` (per asset class), a flat `symbols` list and a
`summary`; omitting `asset_class` returns everything.

`/api/analytics` parameters: `symbol`, `timeframe`, `window`, `bins`, `limit`,
`lookback_hours`. Its `meta` block echoes the symbol's `asset_class`.

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

Covers snapshot reading (one-sided and crossed books, resting-size volume,
pipeline imbalance), asset classification from both the gold chain and the
fallback, production-endpoint resolution, bar consistency, OFI bounds, volume
profile mass conservation, drawdown sign, strict JSON serialisability, and a
degenerate flat-price series (guards against divide-by-zero).

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Could not login because the authentication failed (18456)` | SP has no workspace role. Grant **Contributor**. |
| `Invalid value specified for connection string attribute 'PWD'` | Secret missing/blank in `.env`. |
| `SQL_ENDPOINT_PROD is not set` | Add it to `.env` (or set `SQL_ANALYTICS_ENDPOINT`). |
| `The production endpoint is not configured` | Same, reached through the API. |
| `The gold snapshot table is empty` | The ingest pipeline has not written rows yet. |
| Instruments show as ids with no ticker | They come from a feed other than icmarkets; see the `/api/health` hint. |
| `external policy action ... was denied` | **Viewer** role: OneLake security filters Viewers and hides whole tables. Grant **Contributor**. |