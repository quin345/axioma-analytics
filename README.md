# AXIOMA Analytics

Market microstructure analytics: a Flask API over the Fabric Kusto order-book
feed, a React analytics terminal, and a public product landing page.

## Repository layout

```
.
├── run.py              # Local dev launcher (Flask's built-in server)
├── gunicorn.conf.py    # Production server settings (gunicorn on 127.0.0.1:8000)
├── deploy/axioma.service  # systemd unit as installed on the VM
├── webapp/             # Flask backend: API, page serving, cron jobs
│   ├── app.py          #   routes (pages + /api/*)
│   ├── config.py       #   environment-driven Config
│   ├── analytics.py    #   pure analytics (VAP, order flow, volatility, ...)
│   ├── queries.py      #   KQL builders
│   ├── kusto_client.py #   Fabric Kusto reader
│   ├── db.py           #   local SQLite record store (webapp/data/, untracked)
│   ├── build_vap.py    #   5-minute volume-at-price job (cron/systemd)
│   ├── export_vap_run.py / sql_mirror.py  # daily Fabric SQL mirror
│   ├── snapshot_symbols.py
│   ├── static/maintenance.html  # holding page (MAINTENANCE_MODE)
│   └── tests/test_backend.py
├── terminal/           # React + Vite analytics terminal  -> served at /terminal/
├── landing/            # React + Vite product landing page -> served at /
└── docs/
    └── volume-at-price.md
```

## Routes

| Path | Serves |
| --- | --- |
| `/` | Landing page (`landing/dist/index.html`) |
| `/assets/*` | Landing page bundle and images |
| `/favicon.*`, `/icon-*.png` | Landing page icons |
| `/terminal/` | Analytics terminal, behind the pilot login (holding page with 503 when `MAINTENANCE_MODE=1`) |
| `/terminal/*` | Terminal bundle, images and icons (built with `base: '/terminal/'`) |
| `/unavailable` | Holding page preview |
| `/api/*` | JSON API used by the terminal, behind the pilot login (401 without a session) |
| `/api/login`, `/api/logout`, `/api/session` | Sign-in endpoints (public) |

## Configuration (`.env`)

| Variable | Required | Purpose |
| --- | --- | --- |
| `KUSTO_CLUSTER_URI`, `KUSTO_DATABASE`, `KUSTO_TABLE` | yes | Order-book feed source |
| `PRICE_SCALE` | yes | Integer price -> decimal divisor |
| `TERMINAL_DIST` | yes | Path to `terminal/dist` (legacy name `FRONTEND_DIST` still accepted) |
| `LANDING_DIST` | no | Path to `landing/dist` (defaults to `<repo>/landing/dist`) |
| `TERMINAL_USERNAME`, `TERMINAL_PASSWORD` | no | Shared pilot login for `/terminal/` and `/api/*`; both blank = no login wall |
| `SECRET_KEY` | with login | Signs the session cookie; must be long, random and the same for every worker |
| `SESSION_COOKIE_SECURE` | no | Default `1` (HTTPS-only cookie); set `0` only for plain-HTTP local testing |
| `MAINTENANCE_MODE` | no | `1` swaps `/terminal/` for the holding page |
| `DB_PATH`, `VAP_*`, `CACHE_REFRESH_MINUTES` | no | Record store and VAP job tuning, see `docs/volume-at-price.md` |
| `SQL_SERVER`, `SQL_DATABASE`, `SQL_SCHEMA`, `SQL_TABLE` | no | Fabric SQL mirror |

## Terminal login

The landing page is public. "Explore the terminal" opens a sign-in dialog; the
server checks the credentials and sets a signed, HttpOnly session cookie that
lasts 12 hours. The check is server-side, so going straight to `/terminal/`
without a session redirects to `/?login=1`, which opens the dialog, and the
API answers 401. To change the credentials, edit `.env` and
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
