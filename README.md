# Axioma Analytics

Microstructure analytics over the **production aggregate DOM feed**. Every
instrument is categorised by asset class using the pipeline's own
classification, and the dashboard renders OHLCV bars, order-flow imbalance,
spread evolution, volume-at-price, return distribution, drawdown,
autocorrelation and large-trade market impact.

The app talks to exactly one source on the production Fabric estate: the **KQL**
(Eventhouse) database `ctrader_dom`. Every table it reads lives there — the
per-tick aggregate DOM metrics (`agg_dom`), the instrument dimension
(`symbols_icmarkets`) and the database clock. Results are cached in **Redis**
(Entra ID, no access key) so a dashboard request never re-scans the table, and
that four-hour window is slid forward every 30 minutes on the clock (:00 and
:30) — the earliest half hour is purged, the newest half hour is read.

There is no environment switcher, no catalog discovery and no synthetic/demo
fallback. Storage internals (endpoint host, database, table) are never sent to
the browser.

---

## Quick start

```bash
pip install -r requirements.txt
python run.py             # dashboard on http://127.0.0.1:8000
```

---

## Configuration (`.env`)

```ini
# The production data source: one KQL database for everything.
KQL_ENDPOINT_PROD="https://<eventhouse>.z<region>.kusto.fabric.microsoft.com"
KQL_DATABASE="ctrader_dom"
KQL_TABLE="agg_dom"
SYMBOL_TABLE="symbols_icmarkets"

# Redis cache (Entra ID; there is no access key).
REDIS_HOST="<name>.<region>.redis.azure.net"
REDIS_PORT="10000"
REDIS_TTL_SECONDS="2700"
CACHE_LOOKBACK_HOURS="4"
CACHE_REFRESH_MINUTES="30"

# Authentication - see the table below.
FABRIC_MANAGED_IDENTITY="true"

MAX_TICKS="200000"

# Ticker pinned as the dashboard default.
DEFAULT_SYMBOL="XAUUSD"
```

`.env` is already git-ignored (see `.env.example` for the full template with
every Redis knob and the alternative credential modes). Never commit the secret.

### Cache and the fixed window

KQL is expensive to re-scan, so query results are cached in Redis: the
instrument catalogue under one key and each symbol's ticks as a Parquet blob
under its own. A cache miss (or a Redis outage) reads KQL and repopulates;
a KQL failure is what sets `connected: false`, never a cache failure.

The cache holds a **fixed window** (`CACHE_LOOKBACK_HOURS`, 4 by default).
`/api/health` publishes it as `lookback_minutes`, the dashboard's *Duration*
control is built from it, and `/api/analytics` clamps every request to it —
asking for more returns the whole window instead of a `422`, so a bookmarked
URL or a stale tab keeps working. A client can therefore narrow the window,
never widen it, and never trigger a second KQL read.

### Keeping the window current

A cache is only useful if it is fresh, so the app slides the fixed window on a
schedule: **every `CACHE_REFRESH_MINUTES` (30 by default), on the clock marks
(:00 and :30 — 1:30, 2:00, 2:30, …)** a background task advances each cached
symbol's window. The cycle is scheduled against the wall clock rather than the
process start time, so restarts do not shift it and it lines up with a
`*/30` cron entry running the one-shot script. What the dashboard serves is
therefore at most half an hour behind the feed, and anything inside the window
still costs no query at all.

Each cycle slides rather than re-reads:

* **the new slice** — only rows newer than the symbol's newest cached row are
  fetched from KQL (roughly the last 30 minutes), and
* **the purge** — rows older than `CACHE_LOOKBACK_HOURS` before the newest row
  are dropped, which on a 30-minute cycle is exactly the earliest half hour.

A cache entry therefore never holds more than four hours of a symbol, and the
KQL read per cycle covers only the interval, not the whole window. The purge is
anchored on the newest row rather than the wall clock, so a stalled feed keeps
its data while no ticks arrive.

| Piece | Value | Why |
|---|---|---|
| Window | `CACHE_LOOKBACK_HOURS="4"` | Each symbol's cache entry holds at most four hours; the UI may narrow it. |
| Refresh | `CACHE_REFRESH_MINUTES="30"` | The maximum age of what a page load shows, on the :00/:30 marks. Set `0` to switch the cycle off and cache lazily, on demand. |
| TTL | `REDIS_TTL_SECONDS="2700"` | 45 minutes = 1.5 cycles, so one missed cycle still leaves the cache populated. Keep the TTL above the interval. |

The first cycle runs immediately, so a restart warms the cache instead of
leaving the next visitor to pay for it: a symbol with **no** cached entry (cold
start, TTL expiry after the cycle was down) is read over the whole window, then
slid incrementally on later cycles. The cycle also refreshes the instrument
catalogue and includes the configured `DEFAULT_SYMBOL` — so the default view is
warm too. A symbol nobody has asked for in a while drops out on its own when
its TTL expires, so the set stays bounded by real use rather than by the size
of the instrument dimension. A symbol whose read fails keeps its previous
entry: a partial failure must not evict data that is merely older.

`/api/health` reports the cycle: `cache_refresh_minutes`, `cache_refreshed_at`
and `cached_symbols`. If `cache_refreshed_at` stops advancing, the cycle is
failing or switched off — reads still work, they just fall back to KQL.

### Asset classification

The pipeline publishes the class on each symbol row, which the app consumes
directly rather than guessing:

```
symbols_icmarkets          symbolId, symbolName, symbolCategoryId, description
  -> app/assets.py         symbolCategoryId -> broker class -> client key
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
| **Managed identity** | `FABRIC_MANAGED_IDENTITY="true"` | Headless, recommended on Azure. The Kusto client and the Redis client mint and refresh their own tokens from the instance metadata endpoint, so there is no secret on the box and no `az login` session for a background service to lose. Needs the **Contributor** workspace role, read access to the KQL database, and **Redis Data Contributor** on the cache (see below). |
| **Service principal** | `FABRIC_*` credentials present | Headless. Needs **Contributor** and access to the cache. Ignored while managed identity is on, so a leftover secret cannot silently change the auth path. |
| **`az login` token** | Neither of the above | Mints a token via the Azure CLI. Convenient for local debugging; not suitable for a service. |

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
workspace, and read on the Eventhouse KQL database this app needs (it also
avoids the OneLake security filtering that can hide whole tables from `Viewer`).

The Redis cache is granted separately: assign the same identity **Redis Data
Contributor** on the Azure Cache for Redis / Redis Enterprise resource (or a
data-access policy scoped to the `axioma*` key prefix). Redis Enterprise for
Azure authenticates through Entra ID, so there is no access key to store.

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
internet ──TLS──> nginx (443) ──HTTP──> uvicorn (127.0.0.1:8000) ──HTTPS──> Fabric KQL
                                                              └─────HTTPS──> Redis
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

The refresh cycle runs inside this process. That is deliberate: there is exactly
one uvicorn worker, so one refresher, and nothing to coordinate with a lock or a
second unit. `Restart=always` restarts the cycle with the app, and because the
cycle refreshes immediately there is no cold window after a restart.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now axioma.service
systemctl status axioma.service
journalctl -u axioma.service -f          # one "Cache refresh: {...}" line per cycle
```

#### Refreshing from a timer instead

If the KQL work should not sit in the web process, switch the cycle off in
`.env` (`CACHE_REFRESH_MINUTES="0"`) and run one cycle per timer. The script does
what the in-process cycle does, and exits non-zero when the endpoint is
unreachable so a timer surfaces it:

```bash
*/30 * * * * /usr/bin/python3 /home/azureuser/axioma-analytics/scripts/refresh_cache.py \
    >> /var/log/axioma-refresh.log 2>&1
```

```ini
# /etc/systemd/system/axioma-refresh.service   (+ .timer, OnCalendar=*:0/30)
[Service]
Type=oneshot
User=azureuser
WorkingDirectory=/home/azureuser/axioma-analytics
ExecStart=/usr/bin/python3 /home/azureuser/axioma-analytics/scripts/refresh_cache.py
```

### nginx

`/etc/nginx/sites-available/axiomanalytics` is symlinked into `sites-enabled`.
It redirects HTTP to HTTPS (with the ACME challenge path carved out so
`certbot renew` keeps working) and proxies three location groups:

| Location | Why it differs |
|---|---|
| `/api/` | `limit_req` at 10 r/s, 120 s read timeout, buffering off. A cold analytics request may still be scanning KQL and returns large JSON; the default 60 s timeout would 504 while the app is still computing a correct answer, and buffering would hold big payloads in nginx memory. |
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

`connected: true` with a non-zero `row_count` means the full path works: rows
came back from KQL and the instrument names came from the same KQL database.
`cache_connected` reports the Redis cache separately — it decides whether a
request costs a KQL query, but a cache failure is never an outage, because
reads fall through to KQL. `cache_refreshed_at` (with `cached_symbols`) is the
age of what the cache is serving: on a healthy 30-minute cycle it moves every
half hour, and `cache_refresh_minutes` is the interval it is aiming for.

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
so it can be previewed directly. While the app is under development, `/` serves
the development notice and the dashboard itself lives at `/dashboard`.

### The main page while under development

`/` currently serves `app/static/unavailable.html` — a self-contained "under
development" notice with gear artwork — because the app is being rebuilt. The
dashboard is retained at `/dashboard` (and in `index.html`), and the branded
outage page (`app/static/maintenance.html`) is retained at `/unavailable` for
future maintenance windows; `MAINTENANCE_MODE=1` still serves *that* page from
`/` with a 503. When the build is finished, deleting the maintenance route in
`app/main.py` restores the dashboard to `/`.

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

`ctrader_dom.agg_dom` (KQL, Eventhouse) holds **derived per-tick order-book
metrics**, one row per symbol and tick, with `best_bid`, `best_ask`,
`total_bid`, `total_ask`, `imbalance`, `imbalance_ratio`, `vwap_bid`,
`vwap_ask`, `vwap_spread` and `rel_spread`. Each row is computed from a full
order-book reconstruction, downstream of the raw event feed — it is a
per-tick metric, not a point-in-time snapshot:

```
dom_stream_raw    ->   dom_book_flat        ->   agg_dom
(raw DOM events)       (reconstructed            (derived per-tick metrics:
                        full order book)         top-of-book, resting size,
                                                 imbalance, spreads)
```

The column names are unchanged from the old SQL table, so the move was
transport-only (`app.kql` instead of a T-SQL query in the retired `app.db`). The
measured schema is `timestamp: datetime`, `symbolId: long`, the price and size
columns `real`, plus the pipeline's own `eventId`, `eventSeq` and `eventDate`.
Because `symbolId` is a `long`, the queries cast the `string` parameter
(`symbolId == tolong(sym)`) instead of the column; a blank or non-numeric
parameter simply selects no rows.
`app/frames.py` normalises the rows to the canonical tick frame
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
computed it. A lone crossed tick inside an otherwise sound window is still
dropped.

Rows are also not one-per-millisecond: the pipeline writes every book event under
the same millisecond `timestamp` (12 rows on the busiest millisecond observed),
distinguishable only by `eventSeq`/`eventId`. They are kept as separate ticks,
since they are separate events; the projection simply does not carry `eventId` to
the dashboard.

### Symbol names

`symbols_icmarkets` (KQL, same database) supplies `symbolId -> symbolName`, so
the UI shows `XAUUSD`, `EURUSD`, `BTCUSD` instead of raw numeric ids. Table names
are never exposed.

---

## Architecture

```
app/
  config.py      .env -> Settings (KQL endpoint, Redis, credentials, object names)
  cache.py       Redis client (Entra ID), catalogue + per-symbol Parquet caching
  refresh.py     the background cycle that slides the fixed window on the clock
  errors.py      DataSourceError, shared by the transport and the API layer
  kql.py         Kusto client, tick-metric queries and probes (KQL)
  frames.py      tick-metric rows -> canonical tick frame
  assets.py      asset-class taxonomy and classification
  analytics.py   all computations (pure functions, no I/O)
  service.py     instrument catalogue, health probing, fixed cache window
  main.py        FastAPI app + static pages
  static/        dashboard (Chart.js), development notice, holding page, explainer
scripts/
  grant_workspace_access.py   grant the SP a role on the workspace
  refresh_cache.py            one cycle from cron/systemd instead of the app
tests/
  test_analytics.py / test_frames.py / test_assets.py / test_config.py
  test_kql.py         KQL query text, parameters and credential selection
  test_service.py     cache-backed reads, window clamping, cache-outage fallthrough
  test_refresh.py     the refresh cycle (clock slots, first pass, repeat, shutdown)
  test_refresh_script.py    the one-shot script's summary and exit codes
  test_status.py      the health probe (KQL gates it, cache is reported)
  test_selectors.py / test_unavailable.py / test_maintenance.py   HTTP-level behaviour
```

The canonical tick frame is:

```
ts (datetime, UTC) | symbol | bid | ask | last | volume | mid
```

---

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health?refresh=true` | Connection status (`connected` from the KQL probe, `cache_connected`/`cache_error` for Redis), the published `lookback_minutes` window, the refresh cycle (`cache_refresh_minutes`, `cache_refreshed_at`, `cached_symbols`), coverage and hints (no storage details) |
| `GET /api/asset-classes` | The class taxonomy plus a per-class instrument rollup |
| `GET /api/symbols?asset_class=&family=&include_idle=` | Instruments grouped by asset class |
| `GET /api/analytics` | Full analytics bundle |
| `GET /` | The development notice (`unavailable.html`) while the app is under development |
| `GET /dashboard` | The dashboard itself, retained at its own path |
| `GET /unavailable` | The branded outage page, retained for future maintenance windows |
| `GET /welcome` | The front-facing explainer page (the `www` root proxies here) |

`/api/symbols` returns `groups` (per asset class), a flat `symbols` list and a
`summary`; omitting `asset_class` returns everything.

`/api/analytics` parameters: `symbol`, `timeframe`, `window`, `bins`, `limit`,
`lookback_minutes`. `lookback_minutes` is clamped to the cache's fixed window
(`lookback_minutes` in `/api/health`), never rejected. Its `meta` block echoes
the request's window and the symbol's `asset_class`.

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

Covers tick-metric reading (one-sided and crossed books, resting-size volume,
pipeline imbalance), the Redis cache path (cold read -> one KQL call, warm reads
served from cache, per-symbol keys, cache-outage fallthrough, window clamping),
the refresh cycle (clock-aligned :00/:30 slots, immediate first pass, repeat, a
failing cycle that keeps the loop alive, the sliding window's purge of the
earliest half hour, the default symbol warmed, a failing symbol keeping its
previous entry, cancellation on shutdown, the one-shot script's exit codes),
the development notice and the retained pages at `/`, `/dashboard` and
`/unavailable`,
asset classification from both the pipeline category and the fallback,
production-endpoint resolution, bar consistency, OFI bounds, volume-profile mass
conservation, drawdown sign, strict JSON serialisability, and a degenerate
flat-price series (guards against divide-by-zero).

Redis is not required to run the tests: the KQL layer and the Redis client are
both stubbed, and the cycle is driven with a stub instead of the network.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Could not login because the authentication failed (18456)` | The identity has no workspace role. Grant **Contributor**. |
| Health shows `cache_connected: false` | The Redis cache is unreachable or the identity has no data-access role on it. The dashboard still works (the read falls through to KQL); check the Redis resource's data-access policy / **Redis Data Contributor** assignment. |
| Cached ticks look stale after a pipeline backfill | The cycle slides each entry incrementally, so rows older than the cached newest row are only picked up by a full read. Delete the keys (`axioma:ctrader_dom:agg_dom:ticks:*`, `axioma:ctrader_dom:agg_dom:catalogue`) and the next cycle re-reads the whole window; the TTL (`REDIS_TTL_SECONDS`) bounds it anyway. |
| Health's `cache_refreshed_at` is not advancing | The cycle is failing or switched off (`CACHE_REFRESH_MINUTES=0`). Check `journalctl -u axioma.service` for `Cache refresh cycle failed`; the dashboard still works, every request just falls through to KQL. |
| Every request is slow again | The cache is empty or unreachable, so each read costs a KQL scan. Check `cache_connected` and whether the symbols are cached (`cached_symbols`). |
| `KQL_ENDPOINT_PROD is not set` | Add it to `.env` (or set `KQL_ENDPOINT`). |
| `Principal ... is not authorized to read database 'ctrader_dom'` | The identity authenticates but has no read access to the KQL database. Grant **Contributor** on the workspace (it covers read on the Eventhouse items too), or add the identity as a **Database viewer** on the Eventhouse database (→ *Manage permissions*). |
| `The production endpoint is not configured` | Same, reached through the API. |
| `The KQL aggregate table is empty` | The ingest pipeline has not written rows yet. |
| Instruments show as ids with no ticker | They come from a feed other than icmarkets; see the `/api/health` hint. |
| `external policy action ... was denied` | **Viewer** role: OneLake security filters Viewers and hides whole tables. Grant **Contributor**. |