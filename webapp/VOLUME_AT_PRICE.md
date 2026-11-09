# Volume-at-price record keeping

The volume-at-price card shows a **derived** ladder: traded volume binned by
price, with a point of control (POC) and a 70% value area, recomputed for each
lookback window (1 h / 2 h / 3 h / 4 h). Every stop is written to a **local
SQLite database** so the history is kept.

## Where the data lives

| Piece | Path | Notes |
|---|---|---|
| Database | `webapp/data/axioma.db` (`DB_PATH`) | SQLite, WAL mode. Gitignored. |
| Schema | `webapp/db.py` | `vap_run` (one row per stop) + `vap_level` (bins) |
| Derivation | `webapp/analytics.py` | `volume_at_price`, `profiles_by_window` |
| Kusto read | `webapp/queries.py` | `vap_ticks` (one scan, all windows) |
| Job | `webapp/build_vap.py` | the cron/systemd entry point |
| API | `webapp/app.py` | `GET /api/volume-at-price`, `…/windows`, `POST …/refresh` |

Grain is `(symbol_id, window_minutes, as_of)`, so a re-run inside one 5-minute
slot **upserts** the same row instead of duplicating it.

## Cadence: every 5 minutes, on the grid

The job runs **every 5 minutes**, 15 seconds past the mark - the same cadence
the app used for its cache (`CACHE_REFRESH_MINUTES`) and the grain the old
`metrics_5m` table was built on.

### cron

```cron
# /etc/cron.d/axioma-vap
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
# 5-minute grid: run at :00:15, :05:15, :10:15, ...
0,5,10,15,20,25,30,35,40,45,50,55 * * * * azureuser sleep 15 && cd /home/azureuser/axioma-analytics/webapp && ../venv/bin/python build_vap.py >> /var/log/axioma-vap.log 2>&1
```

### systemd

```ini
# /etc/systemd/system/axioma-vap.service
[Unit]
Description=Record volume-at-price profiles
After=network-online.target

[Service]
Type=oneshot
User=azureuser
WorkingDirectory=/home/azureuser/axioma-analytics/webapp
EnvironmentFile=/home/azureuser/axioma-analytics/.env
ExecStart=/home/azureuser/axioma-analytics/venv/bin/python build_vap.py
```

```ini
# /etc/systemd/system/axioma-vap.timer
[Unit]
Description=Run the volume-at-price recorder every 5 minutes

[Timer]
OnCalendar=*:0/5
AccuracySec=10s
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now axioma-vap.timer
systemctl list-timers axioma-vap.timer
```

## Operating the job

```bash
cd webapp
python build_vap.py                # every symbol the feed lists
python build_vap.py --symbol 136   # one symbol
python build_vap.py --list         # what is on record, compute nothing
```

Exit code is 0 unless **every** symbol failed; one bad symbol does not stop the
run. Output goes to the log for the cron/systemd unit to keep.

## Reading it back

```
GET /api/volume-at-price?symbol=136&window=240
```

Serves the newest recorded stop for that window. `refresh=1` re-derives from
Kusto and records it first. `POST /api/volume-at-price/refresh?symbol=136`
records all four windows; `GET /api/volume-at-price/windows?symbol=136` returns
all four from the store in one call.

The payload's `source` is `"local"` when it came from the database and
`"kusto"` when it was derived on that request.

## Retention

The two tables have **different lifetimes**, because they play different roles.

| Table | Role | Default retention |
|---|---|---|
| `vap_run` | The durable analytical record: one small row per stop, holding `poc`, the value area, `total_volume` and the high-volume nodes. | **Kept forever.** |
| `vap_level` | The per-bin ladder detail behind the chart (60 rows per stop per window). | **Last `VAP_LEVEL_HOURS` hours (default 48).** |

`vap_level` is ~60x the row count of `vap_run` and roughly two thirds of the
database on disk, so it is the table that grows. It is trimmed by deleting its
rows **directly** - never by deleting the parent `vap_run` - so an old stop keeps
its summary and simply reports no bins (`"bins": []`). Downstream analytics reads
`vap_run`, so nothing it depends on is ever dropped by the level trim.

```ini
# leave empty to keep every stop (default); set a number to cap per (symbol, window)
VAP_KEEP_RUNS=
# hours of ladder detail to keep; 0 or unset keeps all (default 48)
VAP_LEVEL_HOURS=48
```

At one stop per 5 minutes and 60 bins per stop per window, 48 hours of
`vap_level` for one symbol is about `48 * 12 * 4 * 60 ≈ 138k` rows; `vap_run`
adds only `48 * 12 * 4 ≈ 2.3k` for the same span, which is why it can be kept
indefinitely.

### Maintenance commands

The 5-minute job keeps only the hot path (derive + insert + the level trim). The
heavier, less frequent work is explicit:

```bash
cd webapp
python build_vap.py --prune    # apply retention only, no Kusto
python build_vap.py --vacuum   # WAL checkpoint + VACUUM to reclaim file space
python build_vap.py --list     # stops and bin counts on record per (symbol, window)
```

A daily cron for the reclamation is enough:

```cron
30 3 * * * azureuser set -a; . /home/azureuser/axioma-analytics/.env; set +a; cd /home/azureuser/axioma-analytics/webapp && /home/azureuser/axioma-analytics/venv/bin/python build_vap.py --prune && /home/azureuser/axioma-analytics/venv/bin/python build_vap.py --vacuum >> /var/log/axioma-vap.log 2>&1
```

