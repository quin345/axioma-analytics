# AXIOMA Analytics

Market microstructure analytics: a Flask API over the Fabric Kusto order-book
feed, a React analytics terminal, and a product landing page (public; the rest is behind the pilot login).

## Repository layout

```
.
├── run.py              # Local dev launcher (Flask's built-in server)
├── gunicorn.conf.py    # Production server settings (gunicorn on 127.0.0.1:8000)
├── deploy/axioma.service  # reference systemd unit; adjust User, paths and EnvironmentFile to the host
├── webapp/             # Flask backend: API, page serving, cron jobs
│   ├── app.py          #   routes (pages + /api/*)
│   ├── config.py       #   environment-driven Config
│   ├── analytics.py    #   pure analytics (VAP, order flow, volatility, ...)
│   ├── queries.py      #   KQL builders
│   ├── kusto_client.py #   Fabric Kusto reader
│   ├── db.py           #   local SQLite record store (webapp/data/, untracked)
│   ├── build_vap.py    #   5-minute volume-at-price job (cron/systemd)
│   ├── export_vap_run.py / sql_mirror.py  # daily Fabric SQL mirror
│   ├── export_flat.py  #   5-minute research table `metrics_5m` in axioma.db (docs/flat-export.md)
│   ├── snapshot_symbols.py
│   ├── load_symbols.py / symbols_manual.csv  # hand-maintained instrument names (symbol_manual)
│   ├── static/maintenance.html  # holding page (MAINTENANCE_MODE)
│   ├── static/login.html        # standalone sign-in page (/login)
│   └── tests/test_backend.py
├── terminal/           # React + Vite analytics terminal  -> served at /terminal/
├── landing/            # React + Vite product landing page -> served at /
├── research/           # Quant research pipeline over webapp/data/axioma.db (research/README.md)
└── docs/
    ├── volume-at-price.md
    ├── flat-export.md
    └── system-status.md
```

## Routes

Every route is behind the pilot login except the landing page and the ones
marked public (deny by
default: new routes and unknown paths need a session too). Signed-out page
requests redirect to `/login?next=<path>`; `/api/*` answers 401.

| Path | Serves |
| --- | --- |
| `/login` | Standalone sign-in page (public); redirects on if already signed in |
| `/` | Landing page (`landing/dist/index.html`, public) |
| `/assets/*` | Landing page bundle and images (public) |
| `/favicon.*`, `/icon-*.png` | Landing page icons (public) |
| `/terminal/` | Analytics terminal (holding page with 503 when `MAINTENANCE_MODE=1`) |
| `/terminal/*` | Terminal bundle, images and icons (built with `base: '/terminal/'`) |
| `/research/` | Published research report (`research/site/`, built by `research/studies/build_report.py`) |
| `/unavailable` | Holding page preview |
| `/status` | System status page: collectors, autotraders, KQL, cron jobs, app units (read-only, auto-refresh 15 s) |
| `/api/status` | The status as JSON (see [System status](#system-status)) |
| `/analytics/` | Trade analytics page: a card per autotrader instance, day history with a cumulative P&L chart, one day's full report (read-only, linkable with `#i=<instance>&d=<YYYY-MM-DD>`) |
| `/api/analytics` | Instances found under the reports dir, each with its `index.json` (empty list when there is none) |
| `/api/analytics/<instance>/<YYYY-MM-DD>` | One day report (JSON schema in `ctrader_autotrade/README.md`, "Daily trades report"); 400 for a malformed name or date, 404 when absent |
| `/api/*` | JSON API used by the terminal (401 without a session) |
| `/api/login`, `/api/logout`, `/api/session` | Sign-in endpoints (public) |

## System status

`/status` (page) and `/api/status` (JSON) show, live and read-only, whether the
data pipeline is healthy. Both are behind the login wall (signed out: `/status`
redirects to `/login?next=%2Fstatus`, `/api/status` answers 401). The page polls
the API every 15 s; the server collects at most once per 10 s per gunicorn
worker and never answers 500 (a failing check becomes `unknown`). The
collection is `webapp/status.py`; permissions and operations are in
`docs/system-status.md`. There are no start/stop buttons.

```text
{ checked_at, overall: ok|warn|down, notes: [...],
  sections: {
    units:       [ {name, status, detail, ActiveState, SubState, LoadState, UnitFileState,
                    since, uptime_s, NRestarts, MainPID, Result, ExecMainStatus, configured} ],
    collectors:  [ {name (instance), status, detail, unit_status, data_status, broker, account,
                    symbol, symbolId, rows_15m, last_event, last_event_age_s, ingestion_lag_s,
                    ActiveState, SubState, since, uptime_s, NRestarts, MainPID, configured} ],
    autotraders: [ {name (unit), status, detail, unit fields, mode, config, instance, timeframe,
                    slot_s, kill_file, pause_file, run_id, run_mode, is_live, last_decision,
                    last_decision_age_s, last_decision_value, last_slot_end, open_positions,
                    last_data_event: {kind, at, age_s, symbolId, summary} | null, log_age_s} ],
    kql:         {name, status, detail, database, table, latency_s, error?,
                  streams: [ {name, status, detail, broker, account, symbolId, symbol, rows_15m,
                              last_event, last_event_age_s, ingestion_lag_s, collector} ]},
    cron:        {name, status, detail, service: {cron.service unit},
                  jobs: [ {name (table), status, detail, schedule, user, runs_as, command,
                           source, table, expected_interval_s, warn_after_s, down_after_s,
                           log, last_log, last_log_age_s, last_wrote, last_wrote_rows,
                           last_run_errors, table_written, table_age_s} ],
                  sources: [ {path, readable, relevant_lines?} ]},
    app:         [ axioma.service, nginx.service, cron.service (unit fields), {name: "record store"} ] } }
```

`status` is `ok`, `warn`, `down`, `unknown` (the check itself failed) or `n/a`
(nothing to check: not configured, or deliberately disabled and stopped).
`overall` is the worst status anywhere, with `unknown` counted as `warn` and
`n/a` as `ok`. A field the web user cannot read is the string `"not readable"`.

Rules (constants at the top of `webapp/status.py`):

| Check | ok | warn | down |
| --- | --- | --- | --- |
| systemd unit | `active`/`running` | `activating`/`reloading`, started < 60 s ago, or `NRestarts` > 0 and started < 10 min ago (flapping), or `active` but not `running` | `inactive`/`failed` (including enabled but not running), not installed |
| | | collector env file present but unit not enabled | (disabled and stopped: `n/a`) |
| depth stream (per collector) | last event < 60 s | 60 s - 30 min ("idle / market closed?"); silent inside the gold/silver daily break or weekend (Fri 20:55 - Sun 23:10 UTC, daily 20:55-23:10 UTC) | silent > 30 min outside those windows; collector active but no row in 24 h |
| KQL | query < 5 s | query >= 5 s | error or 10 s timeout (error class only) |
| cron job `metrics_5m` (every 5 min) | table written <= 10 min ago | 10-20 min | > 20 min, table empty/missing, or `cron.service` not active |
| cron job `metrics_1m` (every minute) | <= 3 min | 3-10 min | > 10 min, or as above |
| | | | job not in cron: `n/a` |
| cron job `trades-report <instance>` (daily 00:20 UTC, `/etc/cron.d/axioma-trades-report`) | newest `reports/<instance>/YYYY-MM-DD.json` is today's or yesterday's (UTC) | 2 days old; no daily file yet | 3 or more days old, or `cron.service` not active; reports exist but the job is not in cron: `n/a` |
| autotrader | unit ok | `PAUSE` file; no decision for 3 slots + 60 s while active; last data gate not `ok` within 2 slots | `KILL` file; unit down |

Cost per collection: three `systemctl` calls (`list-units`, `list-unit-files`,
one `show` for every unit; 3 s timeout each, argument lists only, unit names
whitelisted), one KQL query over 24 h of `agg_dom` (about 0.1-0.6 s), a few
read-only SQLite reads and file stats. The response never carries env-file
contents, unit command lines, secrets or full cron commands (cron commands are
reduced to script and options, e.g. `export_flat.py --timeframe 1m --recent 5`).

## Configuration (`.env`)

| Variable | Required | Purpose |
| --- | --- | --- |
| `KUSTO_CLUSTER_URI`, `KUSTO_DATABASE`, `KUSTO_TABLE` | yes | Order-book feed source |
| `PRICE_SCALE` | yes | Integer price -> decimal divisor |
| `TERMINAL_DIST` | yes | Path to `terminal/dist` (legacy name `FRONTEND_DIST` still accepted) |
| `LANDING_DIST` | no | Path to `landing/dist` (defaults to `<repo>/landing/dist`) |
| `TERMINAL_USERNAME`, `TERMINAL_PASSWORD` | no | Shared pilot login for the whole site; both blank = no login wall |
| `SECRET_KEY` | with login | Signs the session cookie; must be long, random and the same for every worker |
| `SESSION_COOKIE_SECURE` | no | Default `1` (HTTPS-only cookie); set `0` only for plain-HTTP local testing |
| `RESEARCH_DIST` | no | Path to the published research site (defaults to `<repo>/research/site`) |
| `ANALYTICS_DIR` | no | Directory of the autotraders' daily trade reports, `<instance>/YYYY-MM-DD.json` (defaults to `<repo>/ctrader_autotrade/reports`; served read-only at `/analytics/`) |
| `MAINTENANCE_MODE` | no | `1` swaps `/terminal/` for the holding page |
| `DB_PATH`, `VAP_*`, `CACHE_REFRESH_MINUTES` | no | Record store and VAP job tuning, see `docs/volume-at-price.md` |
| `SQL_SERVER`, `SQL_DATABASE`, `SQL_SCHEMA`, `SQL_TABLE` | no | Fabric SQL mirror |

## Terminal login

The landing page is public; everything else is behind the login. A signed-out
visitor to any other page lands on
`/login`; the server checks the credentials and sets a signed, HttpOnly session
cookie that lasts 12 hours, then sends them on to the page they asked for.
The check is server-side, so deep links redirect to `/login?next=...` and the
API answers 401. Only `/` and `/assets/*` (the landing page and its bundle), `/login`, the
sign-in endpoints and the landing icons are public; with `TERMINAL_USERNAME`/`TERMINAL_PASSWORD` blank there is no wall.
To change the credentials, edit `.env` and
`sudo systemctl restart axioma`; changing `SECRET_KEY` signs everyone out.

## Build and run

```bash
# Frontends (Node 22, pnpm; see each app's .mise.toml)
(cd landing  && pnpm install && pnpm build)
(cd terminal && pnpm install && pnpm build)

# Backend, local development
venv/bin/python3 run.py --host 127.0.0.1 --port 8000
```

## Production

nginx terminates TLS for `axiomanalytics.info`, `www.` and `app.` and proxies
everything to gunicorn on `127.0.0.1:8000`, run by the `axioma` systemd unit
(`deploy/axioma.service`). Server tuning is in `gunicorn.conf.py`
(`GUNICORN_WORKERS`, `GUNICORN_THREADS` and `GUNICORN_BIND` override it).

```bash
venv/bin/pip install -r requirements.txt            # once
(cd landing && pnpm build) && (cd terminal && pnpm build)
sudo systemctl restart axioma                        # or: reload, for a graceful worker swap
journalctl -u axioma -f                              # access and error logs
```

Development servers: `pnpm dev` in `terminal/` (port 5173, proxies `/api` to
:8000) and in `landing/` (port 5174).

## Tests

```bash
cd webapp && ../venv/bin/python3 tests/test_backend.py
```

The suite is self-contained (Kusto and ODBC are faked) and needs no credentials.
