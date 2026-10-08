# Axioma Analytics

Microstructure analytics over the **production aggregate DOM feed**. Every
instrument is categorised by asset class using the pipeline's own
classification, and the dashboard renders OHLCV bars, order-flow imbalance,
spread evolution, volume-at-price, return distribution, drawdown,
autocorrelation and large-trade market impact.

The app talks to exactly two sources on the production Fabric estate: the
per-tick aggregate DOM rows in the **KQL** (Eventhouse) database
(`ctrader_dom.agg_dom`), and the instrument dimension in
`[ctrader_lakehouse].[gold]` on the **SQL analytics endpoint**. There is no
environment switcher, no catalog discovery and no synthetic/demo fallback.
Storage internals (endpoint host, database, table) are never sent to the
browser.

---

## Quick start

```bash
pip install -r requirements.txt
python run.py             # dashboard on http://127.0.0.1:8000
```

---

## Configuration (`.env`)

```ini
# The production endpoints: rows on KQL, dimension on SQL.
KQL_ENDPOINT_PROD="https://<eventhouse>.z<region>.kusto.fabric.microsoft.com"
KQL_DATABASE="ctrader_dom"
KQL_TABLE="agg_dom"

SQL_ENDPOINT_PROD="<prod-host>"

# Authentication - see the table below.
FABRIC_MANAGED_IDENTITY="true"

# Instrument dimension (SQL).
GOLD_DATABASE="ctrader_lakehouse"
GOLD_SCHEMA="gold"
SYMBOL_TABLE="symbols_icmarkets"

MAX_TICKS="200000"

# Ticker pinned as the dashboard default.
DEFAULT_SYMBOL="XAUUSD"
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

Note that `agg_dom` is shared and accumulates rows from every feed that writes to
it, while `symbols_icmarkets` describes only icmarkets. Any other feed's
instruments therefore appear as bare ids with no ticker — the health hint names
that explicitly rather than leaving it to be guessed at.

---

## Authentication

Three modes, tried in order:

| Mode | When | Notes |
|---|---|---|
| **Managed identity** | `FABRIC_MANAGED_IDENTITY="true"` | Headless, recommended on Azure. The ODBC driver and the Kusto client mint and refresh their own tokens from the instance metadata endpoint, so there is no secret on the box and no `az login` session for a background service to lose. Needs the **Contributor** workspace role and read access to the KQL database (see below). |
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
curl -sk https://app.axiomanalytics.info/api/health            # through nginx
curl -s https://www.axiomanalytics.info/ | grep -c "What it measures"  # front page
```

`connected: true` with a non-zero `row_count` **and** a non-zero `dimension_rows`
means the full path works: the rows came back from KQL and the instrument names
from SQL. Health reports both, because a single green flag hid a dimension
failure that then broke every selector.

### Deploying an update

```bash
git -C /home/azureuser/axioma-analytics pull
sudo systemctl restart axioma.service
```

`/etc/nginx/sites-available/axiomanalytics.bak` keeps the previous proxy
config for reference.

The certificate must cover **three** names — `axiomanalytics.info`,
`app.axiomanalytics.info` and `www.axiomanalytics.info`. `app` and `www` are
CNAMEs to the bare domain in DNS.

`www` is the **front door**: it serves the explainer page (see *Front page*).
It is only reachable over TLS once the name resolves **and** is on the
certificate, so the two steps are ordered:

1. **DNS first.** Add the `www` record at the registrar — see *Pointing `www`
   at the server*. Until the name resolves, Let's Encrypt's HTTP-01 challenge
   cannot validate it.
2. **Then expand the certificate:**

```bash
sudo certbot certonly --nginx \
  -d axiomanalytics.info -d app.axiomanalytics.info -d www.axiomanalytics.info \
  --expand
```

Do not run `--expand` before the DNS record exists: the challenge fails, and a
failed expansion can leave the already-working names pointing at the old
certificate line.

The `www` server block now serves the explainer at `/` (proxied to the app's
`/welcome`), proxies `/static/` for the brand assets, and 301s every other path
to `https://app.axiomanalytics.info` — `www` is not a site of its own.

`Strict-Transport-Security: max-age=31536000` is set on the HTTPS servers
without `includeSubDomains` or `preload`. Those directives would apply to every
subname, including any that is not on this certificate; a mismatch makes the
HSTS warning sticky and un-dismissable. Leave them off until every subname is
deliberate.

### Front page

`www.axiomanalytics.info` is the front-facing explainer: a single static page
(`app/static/landing.html`, served at `/welcome`) that describes what AXIOMA is,
what it measures (the ten analytic blocks), where the numbers come from, and
links into the dashboard on `app.axiomanalytics.info`.

Like the holding page it is **self-contained** — inline styles, no dependency on
`styles.css`, `app.js` or the analytics API — so it renders even while the
dashboard is in maintenance mode. It is served at its own path rather than `/`,
so `/` stays the dashboard and the page can be previewed directly.

### Pointing `www` at the server (GoDaddy)

DNS for `axiomanalytics.info` is managed at GoDaddy
(`ns31.domaincontrol.com` / `ns32.domaincontrol.com`). `www` currently has no
record at all, so nothing resolves for it. Add one:

1. Sign in to GoDaddy → **My Products** → `axiomanalytics.info` → **DNS**
   (or *Manage DNS*).
2. **Add** a record in the DNS records table:

   | Field | Value |
   |---|---|
   | Type | `CNAME` |
   | Name (Host) | `www` |
   | Value (Points to) | `axiomanalytics.info` |
   | TTL | Default (1 hour is fine) |

   If GoDaddy already has a `www` record, **edit** it to the same value instead
   of adding a second one. (An `A` record `www → 102.37.108.66` works equally
   well; a CNAME is preferred so the record follows the bare domain if the server
   IP ever changes.)
3. Save. Propagation is usually minutes but can take up to an hour.
4. Confirm it resolves, then expand the certificate:

```bash
dig +short www.axiomanalytics.info      # must print the server IP before certbot
sudo certbot certonly --nginx \
  -d axiomanalytics.info -d app.axiomanalytics.info -d www.axiomanalytics.info \
  --expand
sudo systemctl reload nginx
```

5. Verify:

```bash
curl -sI https://www.axiomanalytics.info/ | head -1          # HTTP/2 200
curl -s  https://www.axiomanalytics.info/ | grep -c "What it measures"
```

GoDaddy forwarding must stay **off** for `www`: an enabled *Forwarding* rule
short-circuits the DNS record and sends visitors somewhere else before they ever
reach nginx.

---

## Data source shape

`ctrader_dom.agg_dom` (KQL, Eventhouse) holds one pre-aggregated row per symbol
and timestamp, with `best_bid`, `best_ask`, `total_bid`, `total_ask`,
`imbalance`, `imbalance_ratio`, `vwap_bid`, `vwap_ask`, `vwap_spread` and
`rel_spread`. Each row is a point-in-time state snapshot — top-of-book quotes
and aggregate resting sizes — rather than a reconstruction of the full order
book. The column names are unchanged from the old SQL table, so the move is
transport-only (`app.kql` instead of a T-SQL query in `app.db`). The measured
schema is `timestamp: datetime`, `symbolId: long`, the price and size columns
`real`, plus the pipeline's own `eventId`, `eventSeq` and `eventDate`. Because
`symbolId` is a `long`, the queries cast the `string` parameter
(`symbolId == tolong(sym)`) instead of the column; a blank or non-numeric
parameter simply selects no rows.
`app/frames.py` normalises the rows to the canonical state snapshot frame
(`ts | symbol | bid | ask | last | volume`) so every dashboard panel works
unchanged. The pipeline's own imbalance is used as the directional signal;
one-sided rows (no best bid or ask) are dropped, as is a crossed book
(`bid >= ask`), because neither can produce a valid mid.

A *majority*-crossed frame is treated differently: it means the pipeline emitted
`best_bid`/`best_ask` the wrong way round rather than the rows being out of sync,
so the quotes are swapped back and the pipeline's relative spread with them.
Measured on the live table, every row of a one-hour window is crossed, the
`best_bid`/`best_ask` pair sits about 19x wider than the pipeline's own
`vwap_spread` (36.6 vs 1.94 on XAUUSD), and the two quotes straddle the vwap mid
rather than bracketing it. Swapping therefore restores the sign of the spread and
leaves the mid — the price every panel is drawn from — exactly as the pipeline
computed it. A lone crossed snapshot inside an otherwise sound window is still
dropped.

Rows are also not one-per-millisecond: the pipeline writes every book event under
the same millisecond `timestamp` (12 rows on the busiest millisecond observed),
distinguishable only by `eventSeq`/`eventId`. They are kept as separate ticks,
since they are separate events; the projection simply does not carry `eventId` to
the dashboard.

### Symbol names

`gold.symbols_icmarkets` (SQL) supplies `symbolId -> symbolName`, so the UI shows
`XAUUSD`, `EURUSD`, `BTCUSD` instead of raw numeric ids. Table names are never
exposed.

---

## Architecture

```
app/
  config.py      .env -> Settings (both endpoints, credentials, object names)
  auth.py        managed identity / service principal / az CLI token minting
  kql.py         Kusto client, aggregate-row queries and probes (KQL)
  db.py          SQL connection, identifier quoting, symbol catalogue
  frames.py      aggregate rows -> canonical state snapshot frame
  assets.py      asset-class taxonomy and classification
  analytics.py   all computations (pure functions, no I/O)
  service.py     instrument catalogue, health probing, TTL cache
  main.py        FastAPI app + static dashboard
  static/        dashboard (Chart.js), holding page, front-facing explainer
scripts/
  grant_workspace_access.py   grant the SP a role on the workspace
tests/
  test_analytics.py / test_frames.py / test_assets.py / test_config.py
  test_kql.py         KQL query text, parameters and credential selection
  test_status.py      the two-source health probe
  test_selectors.py / test_unavailable.py   HTTP-level behaviour
```

The canonical state snapshot frame is:

```
ts (datetime, UTC) | symbol | bid | ask | last | volume | mid
```

---

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health?refresh=true` | Connection status for both sources (`row_count` from KQL, `dimension_rows` from SQL), coverage and hints (no storage details) |
| `GET /api/asset-classes` | The class taxonomy plus a per-class instrument rollup |
| `GET /api/symbols?asset_class=&family=&include_idle=` | Instruments grouped by asset class |
| `GET /api/analytics` | Full analytics bundle |
| `GET /welcome` | The front-facing explainer page (the `www` root proxies here) |

`/api/symbols` returns `groups` (per asset class), a flat `symbols` list and a
`summary`; omitting `asset_class` returns everything.

`/api/analytics` parameters: `symbol`, `timeframe`, `window`, `bins`, `limit`,
`lookback_minutes`. Its `meta` block echoes the symbol's `asset_class`.

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
| `KQL_ENDPOINT_PROD is not set` | Add it to `.env` (or set `KQL_ENDPOINT`). |
| `Principal ... is not authorized to read database 'ctrader_dom'` | The identity authenticates but has no read access to the KQL database. Grant **Contributor** on the workspace (it covers read on the Eventhouse items too), or add the identity as a **Database viewer** on the Eventhouse database (→ *Manage permissions*). |
| `The production endpoint is not configured` | Same, reached through the API. |
| `The KQL aggregate table is empty` | The ingest pipeline has not written rows yet. |
| `Failed to complete the command because the underlying location does not exist` / `24596` | The SQL analytics endpoint still points at Delta parquet files that are gone (the gold table was rewritten or vacuumed and the endpoint has not resynced). The old `agg_dom_book_snapshot` fails the same way, so it is not caused by the KQL move. Re-run the pipeline that writes the gold tables, or wait for the endpoint to resync. Health reports it as `dimension_rows: null` with its own hint. |
| Instruments show as ids with no ticker | They come from a feed other than icmarkets; see the `/api/health` hint. |
| `external policy action ... was denied` | **Viewer** role: OneLake security filters Viewers and hides whole tables. Grant **Contributor**. |