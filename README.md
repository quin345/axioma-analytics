# Axioma Analytics

Tick-data microstructure analytics over a single pre-aggregated order-book
snapshot source, normalised into ticks and rendered as a full microstructure
dashboard: OHLCV bars, order-flow imbalance, spread evolution, volume-at-price,
return distribution, drawdown, autocorrelation and large-trade market impact.

Storage internals (warehouse, database, table and endpoint names) are never sent
to the browser. The API exposes two opaque sources - `live` and `synthetic` - and
the UI labels them "Live" and "Demo".

---

## Quick start

```bash
pip install -r requirements.txt
python run.py             # dashboard on http://127.0.0.1:8000
python run.py --env prod  # start against the "prod" endpoint
python run.py --list-envs # show configured endpoints
```

If the data source is unreachable the app automatically falls back to the
synthetic demo source, so the dashboard is always demonstrable.

---

## Configuration (`.env`)

```ini
# One endpoint per environment (names are free-form; dev/test/prod shown first).
SQL_ENV="test"                     # which endpoint is active
SQL_ENDPOINT_DEV="<dev-host>"
SQL_ENDPOINT_TEST="<test-host>"
SQL_ENDPOINT_PROD="<prod-host>"

# Credentials (Entra ID service principal).
FABRIC_TENANT_ID="<tenant-guid>"
FABRIC_CLIENT_ID="<app-registration-client-id>"
FABRIC_CLIENT_SECRET="<client-secret>"

# The single book-snapshot table to read. Resolved server-side only.
DATA_TABLE="gold.agg_dom_book_snapshot"

FABRIC_ALLOW_SYNTHETIC="true"   # demo fallback when the data source is unreachable
MAX_TICKS="200000"
```

`.env` is already git-ignored (see `.env.example` for the template). Never commit
the secret.

### Environments (dev / test / prod)

Any number of Fabric SQL analytics endpoints can be declared as
`SQL_ENDPOINT_<NAME>`. The active one is chosen in three ways, in priority order:

1. **Dashboard** — the *Environment* dropdown (`POST /api/environments`).
2. **Startup flag** — `python run.py --env prod` (`--list-envs` prints them).
3. **`.env`** — `SQL_ENV` sets the default when nothing else is chosen.

Switching rebuilds the connection settings and drops every cached catalog /
symbol / report fragment, so the next request reads from the new endpoint. If
`SQL_ENV` names an unconfigured environment, the first configured one is used;
an unknown name passed to the API is rejected with `400`. A legacy single
`SQL_ANALYTICS_ENDPOINT` still works and is exposed as the `default` environment.

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

The service principal needs this role on **every** workspace it reads from, so
each of `dev_axioma`, `test_axioma` and `prod_axioma` must carry its own
assignment — adding it to one does not cover the others.

#### Grant it to all three at once

```bash
python scripts/grant_workspace_access.py            # dev_axioma, test_axioma, prod_axioma
python scripts/grant_workspace_access.py --dry-run  # preview only
python scripts/grant_workspace_access.py --list     # show workspaces + ids
```

Resolves the principal object ID from `FABRIC_CLIENT_ID`, matches workspaces by
display name, and is idempotent — it skips workspaces already holding the role,
updates the role when it differs, and creates the assignment only when missing.
Run it while signed in with `az login` as a workspace **Admin**. Pass
`--role Viewer` for a read-only grant, or `--workspaces <name>` to target one.

---

## Data source shape

The configured table holds one pre-aggregated row per symbol and timestamp, with
`best_bid`, `best_ask`, `total_bid`, `total_ask`, `imbalance`, `imbalance_ratio`,
`vwap_bid`, `vwap_ask`, `vwap_spread` and `rel_spread`. `app/books.py` normalises
it to the canonical tick frame (`ts | symbol | bid | ask | last | volume`) so every
dashboard panel works unchanged. The pipeline's own imbalance is used as the
directional signal; one-sided rows (no best bid or ask) are dropped because they
cannot produce a mid.

The readers also understand per-level snapshots, raw L2 event deltas and
conventional tick tables, so the same engine generalises to other shapes.

### Symbol names

A symbol dimension (`symbolId -> symbolName`) is discovered automatically, so the
UI shows `BTCUSD`, `ETHUSD`, `EURUSD` instead of raw numeric ids. The dimension
table's name is likewise never exposed.

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
  config.py      .env -> Settings, environment selection (secrets redacted)
  auth.py        az CLI / service-principal token minting
  db.py          connection, table resolution, tick fetch, symbol dimension
  books.py       readers for book snapshot shapes (+ classification)
  schema.py      tolerant column mapping -> canonical tick frame
  analytics.py   all computations (pure functions, no I/O)
  synthetic.py   realistic tick generator (fallback/demo)
  service.py     source resolution, health probing, TTL cache
  main.py        FastAPI app + static dashboard
  static/        dashboard (Chart.js)
scripts/
  grant_workspace_access.py   grant the SP a role on dev/test/prod workspaces
tests/
  test_analytics.py / test_books.py / test_l2.py / test_config.py   no data source required
```

The canonical tick frame is:

```
ts (datetime, UTC) | symbol | bid | ask | last | volume | mid
```

`schema.ALIASES` maps many spellings (`bid`, `BidPrice`, `bid_price`, `b`…) onto
those fields, so most column layouts work without configuration.

---

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health?refresh=true` | Connection status and hints (no storage details) |
| `GET /api/environments` | Selectable endpoints (masked) + the active one |
| `POST /api/environments?env=` | Switch the active endpoint and re-probe it |
| `GET /api/sources` | The `live` / `synthetic` sources + symbols |
| `GET /api/symbols?source=` | Symbols for one source |
| `GET /api/analytics` | Full analytics bundle |
| `POST /api/query?sql=` | One read-only `SELECT` (schema exploration) |

`/api/analytics` parameters: `source` (`live` or `synthetic`), `symbol`,
`timeframe`, `window`, `bins`, `limit`, `lookback_days`.

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
profile mass conservation, drawdown sign, strict JSON serialisability, a
degenerate flat-price series (guards against divide-by-zero), and environment
resolution (ordering, selection, blank endpoints, runtime switching, masking).

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