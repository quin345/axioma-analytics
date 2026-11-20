# System status page: permissions and operations

`/status` and `/api/status` (README "System status") run inside the web app, so
everything they read is read as the gunicorn user **`axioma`** under the
`axioma.service` sandbox (`NoNewPrivileges=true`, `PrivateTmp=true`,
`ProtectSystem=full`; no `ProtectHome`, no `RestrictAddressFamilies`, no
`PrivateDevices`). Checked on 2026-11-20 as `axioma` (`runuser -u axioma`, and
`setpriv --no-new-privs` for the NoNewPrivileges part):

| What | Result for `axioma` |
| --- | --- |
| `systemctl show / list-units / list-unit-files` (D-Bus, world-writable `/run/dbus/system_bus_socket`) | works, also with no_new_privs |
| `/etc/cron.d/*`, `/etc/crontab` (0644) | readable |
| `/var/spool/cron/crontabs` (root:crontab 1730) | not listable: user crontabs are reported as `readable: false` (there are none today) |
| `/var/log/axioma/flat-{5m,1m}.log` (0644) | readable |
| `/etc/ctrader-collector/` (root 0700) | not listable: "env file but no unit" detection is skipped, noted in `notes` |
| `/root` (ACL `user:axioma:--x`), repo 0755 | traversable |
| `ctrader_autotrade/config*.toml`, `state/` dir, `state/autotrade.db` (+ `-wal`/`-shm`, 0644), `logs/autotrade.jsonl` | readable (SQLite opened `mode=ro`) |
| `ctrader_autotrade/state/kill_status.json`, `kill_baseline.json` (0600) | not readable, and not needed (only the existence of `KILL`/`PAUSE` is checked) |
| `webapp/data/axioma.db` | readable (owner) |

`ProtectSystem=full` makes `/usr`, `/boot` and `/etc` read-only, which does not
affect reads; `PrivateTmp` is irrelevant.

Optional, only if you want collector instances that have an env file but no
enabled unit flagged: let `axioma` list (not read) the directory with
`setfacl -m u:axioma:rx /etc/ctrader-collector`. The files stay 0600 root, so
their contents stay unreadable; the page only ever uses the file names. Not
applied.

Where things come from:

- Collector units: `ctrader-collector@<broker>-<account>-<SYMBOL>.service`, from
  `list-units --all`, the `multi-user.target.wants` directory and (if listable)
  the env-file names. The symbol id is looked up in the app's catalogue
  (`symbol_manual` over `symbol_mapping`) by broker, account and symbol name.
- Autotraders: every `ctrader-autotrade*.service`; its files are found from the
  unit's `WorkingDirectory` and `--config` (default `config.toml`):
  `state_dir`/`log_dir` and `[instance] name/timeframe` come from that file, so a
  second bot with its own config (e.g. `config.1m.toml`) shows up when installed.
- Cron: lines in `/etc/crontab` and `/etc/cron.d/*` that mention
  axioma/ctrader/export_flat. The flat-export logs have no timestamps, so
  "last wrote" is the log's mtime when the log ends on a `wrote ...` line; the
  authoritative freshness is `max(computed_at)` of `metrics_5m` / `metrics_1m`.
