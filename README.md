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

# Authentication - see the table below.
FABRIC_MANAGED_IDENTITY="true"

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

Three modes, tried in order:

| Mode | When | Notes |
|---|---|---|
| **Managed identity** | `FABRIC_MANAGED_IDENTITY="true"` | Headless, recommended on Azure. The ODBC driver mints and refreshes its own tokens from the instance metadata endpoint, so there is no secret on the box and no `az login` session for a background service to lose. Needs the **Contributor** workspace role (see below). |
| **Service principal** | `FABRIC_*` credentials present | Headless. Needs **Contributor**. Ignored while managed identity is on, so a leftover secret cannot silently change the auth path. |
| **`az login` token** | Neither of the above | Mints a token via the Azure CLI and passes it as `SQL_COPT_SS_ACCESS_TOKEN`. Convenient for local debugging; not suitable for a service. |

Give the identity a role with `scripts/grant_workspace_access.py`, or set it by
hand in the Fabric portal (*workspace → Manage access*). The principal id to
grant for a system-assigned identity is the **VM's own client id**:

```bash
az vm get -g <resource-group> -n <vm> --query identity.principalId -o tsv
```

Verify access without starting the app:

```bash
python scripts/grant_workspace_access.py --list
```

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

## Deployment (production)

The app is a single-process FastAPI/uvicorn server. nginx terminates TLS and is
the only public entry point; the app itself binds to loopback, so it is
unreachable except through the proxy.

```
internet ──TLS──> nginx (443) ──HTTP──> uvicorn (127.0.0.1:8000) ──TDS──> Fabric
```

### systemd unit

`/etc/systemd/system/axioma.service` runs the app as `azureuser`:

```ini
[Service]
Type=exec
User=azureuser
WorkingDirectory=/home/azureuser/axioma-analytics
ExecStart=/usr/bin/python3 /home/azureuser/axioma-analytics/run.py --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
```

Production means **no `--reload`** (that is a single-process development server)
and a **loopback bind**. The unit also sets `ProtectSystem=strict` with
`ReadWritePaths` for the repo, drops all capabilities, and filters syscalls.
Managed identity needs only outbound HTTPS to the instance metadata endpoint, so
no extra capability is granted.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now axioma.service
systemctl status axioma.service
journalctl -u axioma.service -f
```

### nginx

`/etc/nginx/sites-available/axiomanalytics` is symlinked into `sites-enabled`.
It redirects HTTP to HTTPS (with the ACME challenge path carved out so
`certbot renew` keeps working) and proxies three location groups:

| Location | Why it differs |
|---|---|
| `/api/` | `limit_req` at 10 r/s, 120 s read timeout, buffering off. Analytics queries scan the gold table and return large JSON; the default 60 s timeout would 504 while the app is still computing a correct answer, and buffering would hold big payloads in nginx memory. |
| `/static/` | Short `expires`, so unversioned filenames still revalidate. |
| `/` | Dashboard and everything else. |

`client_max_body_size` is raised to 8 MB because analytics payloads run past
nginx's 1 MB default.

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### Verifying

```bash
curl -s localhost:8000/api/health | python3 -m json.tool     # app, direct
curl -sk https://axiomanalytics.info/api/health            # through nginx
```

`connected: true` with a non-zero `row_count` means the full path works.

### Deploying an update

```bash
git -C /home/azureuser/axioma-analytics pull
sudo systemctl restart axioma.service
```

`/etc/nginx/sites-available/axiomanalytics.bak` keeps the previous proxy
config for reference.

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
  auth.py        managed identity / service principal / az CLI token minting
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
| `Could not login because the authentication failed (18456)` | The identity has no workspace role. Grant **Contributor**. |
| `Invalid value specified for connection string attribute 'Authentication'` | Wrong keyword spelling. The driver wants `ActiveDirectoryMSI`, not `ActiveDirectoryManagedIdentity`; `connection_string()` emits the accepted form. |
| App exits immediately with a segmentation fault | The `az login` token path (`SQL_COPT_SS_ACCESS_TOKEN`) crashes pyodbc 5.3 against this driver. Switch to managed identity or a service principal. |
| `Invalid value specified for connection string attribute 'PWD'` | Secret missing/blank in `.env`. |
| `SQL_ENDPOINT_PROD is not set` | Add it to `.env` (or set `SQL_ANALYTICS_ENDPOINT`). |
| `The production endpoint is not configured` | Same, reached through the API. |
| `The gold snapshot table is empty` | The ingest pipeline has not written rows yet. |
| Instruments show as ids with no ticker | They come from a feed other than icmarkets; see the `/api/health` hint. |
| `external policy action ... was denied` | **Viewer** role: OneLake security filters Viewers and hides whole tables. Grant **Contributor**. |