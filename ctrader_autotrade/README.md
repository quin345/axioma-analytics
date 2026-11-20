# ctrader_autotrade

Demo-account cTrader autotrader for the `poc_reversion_4h` hypothesis, a forward test of the
research signal (expected P&L is negative). Design and decisions: [DESIGN.md](DESIGN.md).

Status: built and running on the demo account as the `ctrader-autotrade` systemd unit (see
Deployment). Its bars come from the Fabric KQL table `dom_ctrader.agg_dom` by default (see Data source).

The same code runs as several **instances** (see "Instances" below): `ax5m`, the original 5-minute bot
(`config.toml`), and `ax1m`, the 1-minute screen's `poc_reversion_1h` (`config.1m.toml`, unit
`deploy/ctrader-autotrade-1m.service` at the repo root). Each has its own state, journal, logs, kill switch
and order label on the same demo account.

## Setup

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    echo /root/data-mining-project/src > .venv/lib/python3*/site-packages/ctrader_open_api_src.pth
    .venv/bin/pip install -e .
    cp .env.example .env   # fill in credentials (never committed)

`ctrader_open_api` comes from `/root/data-mining-project/src` via a .pth file, because installing
that whole project would pull the Azure/pyodbc dependencies. `requirements.txt` includes
`azure-identity` and `azure-kusto-data` for the KQL data source; they are imported only when
`[data] source = "kql"`. Non-secret settings live in `config.toml`.

## Tests

    .venv/bin/pytest -q

The end-to-end tests (`tests/test_e2e.py`) drive the whole bot through a fake transport on a
test clock; nothing connects to cTrader in the test suite.

## Usage

Global options go before the subcommand: `autotrade [--env PATH] [--config PATH] [-v] <command>`.
Run from this directory (`state/` and `logs/` are relative to it, see `config.toml`).

| Command | What it does |
|---|---|
| `autotrade probe` | Connects to the demo host, handshakes and prints (never any secret) the instance, the account id, isLive, broker, account type (HEDGED = hedging / NETTED = netting, with what it means for two instances), leverage, money digits, balance, each resolved symbol (digits, lot/min/step/max volume, slDistance and distanceSetIn, schedule summary, timezone), the open positions/orders with their label and owner (this instance / other), the account's orders of the last 24 h by label, and one spot per symbol with its spread and the notional/margin of one and of two instances' orders. Read-only requests only (Reconcile, OrderList); subscribes spots, never depth. Places no orders. Disconnects. |
| `autotrade run --mode dry-run` | Full bot with the simulated broker: nothing is ever sent to the order API. Journals bars, signals, shadows and simulated trades. |
| `autotrade run --mode demo [--exec taker\|maker]` | Same, with real orders on the demo account (taker by default, as in DESIGN.md). Refuses to start trading unless the session is a verified demo account. |
| `autotrade run ... --hours N` | Stop after N hours (flattens, flushes, exits 0). For bounded test runs. |
| `autotrade flatten` | Connects (demo), cancels the instance's own resting orders and closes its own positions (label = instance name; for `ax5m` also its pre-instance label `axq-poc4h-v1`). Other instances' and foreign orders and positions are never touched. |
| `autotrade parity` | P4 drift check: runs the research feature code over the bot's own `bars` table and compares with the features journalled in `signals`. |
| `autotrade report [--since-hours N]` | Forward-test report from the journal (see below), including the kill-switch numbers. |
| `autotrade trades-report (--date YYYY-MM-DD \| --last-day \| --backfill \| --today) [--out-dir DIR] [--stdout]` | Daily executed-trades report of this instance, one JSON file per UTC day (see "Daily trades report"). Reads the journal read-only. `--stdout` prints a text rendering and writes nothing. |
| `autotrade kill-reset` | Re-arms the P&L kill switch: writes a new baseline at now. Does not remove `state/KILL` or `state/PAUSE` (it prints the command). |
| `autotrade kql-check [--minutes 30] [--symbol-ids 41,10026] [--verify]` | Read-only. Prints the instance's data gates, times the slot-close query (two slots of buckets) three times, runs the bot's KQL bucket query for its own streams and prints buckets, events, watermark lag and gaps. `--verify` recomputes the buckets from the raw rows and compares them. Does not connect to cTrader. Symbol ids default to those in the journal's recent bars. |

Exit codes: 0 normal (including KILL), 1 error or timeout, 2 refused (live account, wrong host,
account not in the token's list), 3 SAFE mode (token expired without a refresh token).

## Recommended rollout

1. `autotrade probe`. Check: not live, the right broker, both symbols resolved (aliases in
   `config.toml`), plausible spreads, slDistance/distanceSetIn (the PERCENTAGE unit is an assumption in
   `strategy.py`), account type HEDGED/NETTED, money digits.
2. `autotrade kql-check --verify` (the KQL data source; needs the account's collector units running), then
   `autotrade run --mode dry-run` for 24-48 hours (e.g. `--hours 48`, or under `nohup`). It trades only
   in the simulator but journals everything.
3. `autotrade report` and `autotrade parity`. A forward IC t of about 3 needs ~7 four-hour blocks (about 28 h of
   market). Check the taker shadow against the research figures, the maker-sim numbers, spreads, risk
   events, and the parity drift.
4. Only then `autotrade run --mode demo` (or the systemd unit, see below). With the default
   `source = "kql"` the bars are exact from the start. With `source = "ctrader"`, demo entries on
   approximated bars are blocked (reason `approx`) until `state/PARITY_OK` exists. Create it (an empty
   file) after reviewing the parity report and the DESIGN.md section 4 gate
   (Spearman(poc_dist_240) >= 0.98, side agreement >= 97%). Without it the bot waits for four hours
   of fully live history after every restart.

## Instances

    [instance]
    name = "ax5m"            # cTrader label on every order, clientOrderId prefix; 1-16 chars [A-Za-z0-9_.-]
    timeframe = "5m"         # "5m" or "1m" (autotrade/timeframe.py)
    claim_unlabelled = false # default instance only: also adopt/flatten positions WITHOUT a label

| | `ax5m` (default) | `ax1m` |
|---|---|---|
| config | `config.toml` (also the default when `[instance]` is absent) | `config.1m.toml` |
| bars | 5 minutes, POC window 240 min, z over 48 bars | 1 minute, POC window 60 min, z over 60 bars |
| signal | `poc_reversion_4h` (journals `range_reversal_1h`) | `poc_reversion_1h` (journals `range_reversal_12m`) |
| state / logs | `state/`, `logs/` (unchanged) | `state/ax1m/`, `logs/ax1m/` |
| owns labels | `ax5m`, `axq-poc4h-v1` (its orders before instances) | `ax1m` |
| unit | `ctrader-autotrade` | `ctrader-autotrade-1m` |

What tells the two apart in cTrader:

- **label** (`ProtoOANewOrderReq.label`, shown on the position as `tradeData.label`, and on its protective stop
  and closing orders) = instance name;
- **comment** = `<instance> <strategy> z=<z_poc> sig=<symbolId>-<slot end s>`, e.g.
  `ax1m poc_reversion_1h z=-1.37 sig=41-1795176360` (exit orders on a netting account: `ax1m exit sig=...`);
- **clientOrderId** = `<instance>-<SYMBOL>-<slot start s>-E|X`, e.g. `ax1m-XAUUSD-1795176300-E`.

Deals (`ProtoOADeal`) have no label; they belong to the position (`positionId`) and so to its label.
Closes (`ProtoOAClosePositionReq`) carry no label either: they name the instance's own position id.

Each instance reconciles, adopts, flattens (KILL, shutdown, kill switch, `autotrade flatten`) and counts
("unrealised (own positions)") only positions/orders with a label it owns. Positions without a label (manual
trades: the account had one in the last 24 h) are left alone by both unless `claim_unlabelled = true` on the
default instance. The account must be **hedging** (`autotrade probe`: `account type: HEDGED`); on a netting
account the 1-minute instance refuses to start (exit 2) unless it has its own account (see below).

Run the second bot:

    .venv/bin/autotrade --config config.1m.toml probe
    .venv/bin/autotrade --config config.1m.toml kql-check --symbol-ids 41,10026 --minutes 60 --verify
    .venv/bin/autotrade --config config.1m.toml run --mode dry-run --hours 2     # optional
    systemctl start ctrader-autotrade-1m                                        # demo orders (Deployment)

Every command takes `--config config.1m.toml` to act on the 1-minute instance (`report`, `parity`, `flatten`,
`kill-reset`, `kql-check`); without it, it acts on `ax5m`. The state dir holds `INSTANCE.json`; a run of another
instance (or timeframe) on it is refused, and `run` holds `state/.../instance.lock` (one process per instance).
On its own demo account: `--env /path/to/.env.ax1m` (its `ctidTraderAccountId` / `access_token`) and
`[instance] netted_sole = true` if that account is netting.

## Control files (in `state/`, `state/ax1m/` for the 1-minute instance)

- `state/KILL`: cancels own orders, flattens, exits the process (code 0). Checked every 5 s and at start-up
  (a file present at start flattens and exits at once). Written by the kill switch too. Remove
  the file before restarting or the bot stops again at once.
- `state/PAUSE`: no new entries (existing positions still exit on schedule). Delete to resume. Written by the
  kill switch when `profit_action = "pause"`.
- `state/kill_baseline.json`, `state/kill_status.json`: the kill switch's baseline and its latest numbers (mode 0600).
- `state/PARITY_OK`: demo with `source = "ctrader"` only. It lifts the approximate-bar block (see
  rollout step 4). KQL bars are never approximate.
- `state/token.json`: refreshed access token (mode 0600), only if `refresh_token` is configured.

## Data source

    [data]
    source = "kql"        # or "ctrader": own depth feed + bucket store + tick gap fill (the old path)
    env_file = "../.env"  # KUSTO_CLUSTER_URI, KUSTO_DATABASE, KUSTO_TABLE, AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET
    grace_s = 3           # first query 3 s after each slot end
    retry_s = 2           # retry while KQL has not reached the slot end
    retries = 3
    deadline_s = 10       # then decide with what is there (must be < strategy grace_s + 10)
    timeout_s = 10
    freshness_s = 15      # KQL behind by more than this -> "kql_stale"
    gap_max_s = 60        # open-session hole in the 240 min window longer than this -> "kql_gap"

With `kql` the bot reads 10 s buckets for its account and symbols from `dom_ctrader.agg_dom`, which the
`ctrader-collector@<broker>-<account>-<SYMBOL>` units fill. It subscribes to spots only (for orders,
spreads and the stale guard), never depth.

- **Start-up.** One query fetches 16.5 h of history, the bars are exact, and the bot can trade at the next
  slot close. There is no 4-hour approximate period and no tick fill.
- **Each slot.** The bot queries KQL 3 s after the slot end, retrying until the data reaches the slot end
  (normally the first attempt does).
- **Data gate.** A bar is not tradable when KQL failed (`kql_error`), lags more than 15 s (`kql_stale`),
  or the 240-minute window has a hole of more than 60 s while the market was open (`kql_gap`). The gold
  break and weekends do not count. A stream that started less than 4 hours ago is still a hole. Such a
  bar opens nothing and rolls nothing over, and an open position is time-exited as usual. Flattening
  (KILL, session close, disconnect) and the server-side stop do not depend on KQL.
- **Logging.** `signals.reason` holds the gate. Each change of gate is a `data_gate` risk event, and each
  slot without any data is a `kql_error` risk event. Once KQL catches up, the affected bars are rebuilt.

Prerequisites:

- the bot's account (`ctidTradeAccountId` in `.env`) must have running collector units for its symbols
  (`systemctl status 'ctrader-collector@*'`);
- the Kusto keys must be in `env_file`.

Switch back with `source = "ctrader"` and a restart. DESIGN.md section 4 has the details.

## Kill switch and daily reset

Settings in `config.toml` (amounts in the account currency, positive numbers):

    [kill]
    enabled = true
    max_loss = 500            # cumulative P&L since the baseline <= -500: flatten, write state/KILL, stop
    profit_target = 1000      # cumulative P&L since the baseline >= 1000: profit_action; 0 = off
    profit_action = "kill"    # "kill" (as max_loss) or "pause" (write state/PAUSE: no new entries, exits continue)
    daily_max_loss = 200      # P&L since the last daily reset <= -200: flatten, no entries until the next reset; 0 = off
    check_interval_s = 5
    # since = "2026-11-20T05:00:00Z"   # optional; overrides the baseline file

    [risk]
    daily_reset = "00:00"     # HH:MM local time in daily_reset_tz
    daily_reset_tz = "UTC"    # IANA name, e.g. "America/New_York" with "17:00" for the forex day

What it does. Every `check_interval_s` the bot adds the realised P&L (closed trades of this account and run
mode since the baseline, net of commission and swap) and the unrealised P&L of its own open positions (DEMO: the
server's `netUnrealizedPnL`, falling back to the touch mark; DRY_RUN: the touch mark; unknown, so realised-only,
if a symbol's quote asset is not the deposit asset, with a one-off warning). On a breach it logs a WARNING, writes
a `risk_events` row (`kill_max_loss`, `kill_profit_target`, `daily_max_loss`) with the numbers, then:

- `max_loss`, or `profit_target` with `profit_action = "kill"`: cancels orders, closes positions, writes
  `state/KILL` and exits with code 0 (systemd's `Restart=on-failure` leaves it stopped).
- `profit_target` with `"pause"`: writes `state/PAUSE`; exits still run.
- `daily_max_loss`: closes positions and blocks entries until the next daily reset; no `KILL` file. It is
  derived from the journal, so a restart on the same day restores it. The day's unrealised P&L is the whole
  current unrealised, including a position opened before the reset.

The baseline is `state/kill_baseline.json`, created the first time the bot runs with `kill.enabled` (a new
one when the account id changes). The cumulative limits survive restarts because they are recomputed from the
journal, so deleting `KILL` is not enough: re-arm with

    rm state/KILL && .venv/bin/autotrade kill-reset && systemctl start ctrader-autotrade

(`kill-reset` also needs `rm state/PAUSE` after a profit pause; it removes neither file.) `kill.since` in
`config.toml` overrides the file, so remove it if you want `kill-reset` to take effect. `autotrade report` has a
"Kill switch" section: baseline, realised and unrealised P&L against the limits, today's P&L and the next reset.

Daily reset. Every daily quantity uses `daily_reset` in `daily_reset_tz`: the daily loss latch
(`daily_loss_pct` / `daily_loss_bps`), `daily_max_loss`, `trades_today` / `max_trades_day`, `pnl_today_*` and
its start-of-day balance. The reset is wall-clock time in that zone, so a DST change moves it in UTC (that day
is 23 or 25 hours long). A bad time or zone is a config error at start-up. Settings are read at start: restart
the service to apply a change.

## Where things live

- `state/autotrade.db`: SQLite journal (WAL): `runs`, `buckets` (the warm-up store), `bars`,
  `signals`, `orders`, `fills`, `trades`, `risk_events`, `transitions`.
- `logs/autotrade.jsonl`: JSON lines (events, signals, orders, plus the log output); rotated at 5 MB x 5.
  Stdout carries the same log, concise. Secrets are never written.
- Shutdown (SIGTERM/SIGINT, or `--hours`): stops new entries, cancels own resting orders, flattens
  (demo: if `close_on_shutdown`; dry-run always), flushes the journal and exits within ~25 s.

## What the report means

`autotrade report` prints plain-text sections for ONE instance (`--config config.1m.toml report` for `ax1m`): its
own journal, so its own signals, orders and trades. The header names the instance, its timeframe, strategy and
labels, and repeats that the expected P&L is negative (research taker net: -0.31 bp/trade at 5m, -0.84 at 1m).
The research numbers are the holdout references from DESIGN.md (5m) and docs/findings-1m-screen.md (1m: IC
+0.138, taker gross -0.16 / net -0.84 bp, IC per 1 h block, no maker reference). The text below describes 5m.

- **Forward IC**: rank IC per 4 h block of `sig_poc` (= -poc_dist_240) against the next 5 minute log mid
  return, pooled over symbols, with the same statistic as `research/pipeline/screen.py`
  (`block_ics` / `summarise_ics`). Research holdout: +0.16. This is the main test of the signal.
- **Taker shadow**: for every bar with |z_poc| > entry_z, enter at the touch at t and leave at the
  touch at t+1. `net` is that touch-to-touch result; `gross` is the mid-to-mid return of the same
  side. Research: +0.39 gross, -0.31 net (bp per trade). Per-trade sd is 10-14 bp, so thousands of
  trades are needed to resolve 0.4 bp: read it as a sanity check on spreads and costs, not a verdict.
- **Maker sim**: a limit at our side of the touch at t is counted filled if a later quote within
  `limit_ttl_s` crosses it (cTrader semantics: a buy fills when the ask reaches it). Reports the fill
  rate, pnl per fill (limit to the exit touch) and adverse selection (side-signed mid return of
  filled minus unfilled signals; negative means fills happen before price moves against us).
  Research: fill ~82%, about -2 bp per fill.
- **Trades**: closed trades by run mode (`dry_run` = simulator, `demo` = real demo orders): mean
  price-based bp, net account-currency pnl, the share closed by the protective stop, exit reasons.
  Dry-run money is an approximation (USD-quoted, zero commission).
- **Risk events**: counts by kind (entry_blocked, order_reject, disconnected, flatten, kill, data_gate,
  kql_error, ...).
- **Kill switch**: baseline, realised and unrealised P&L against `max_loss` / `profit_target`, today's P&L against
  `daily_max_loss`, the next daily reset and whether KILL / PAUSE exist. Unrealised comes from the running bot's
  `state/kill_status.json` (n/a when it is not running).

## Daily trades report

`autotrade [--config config.1m.toml] trades-report ...` turns ONE instance's journal into one report per UTC day
(`autotrade/daily.py`), for the web page `/analytics/` (login-gated, `webapp/trade_reports.py`). It reads the
journal read-only and writes only into `--out-dir` (default `reports/<instance name>/` here; the web app reads
`ANALYTICS_DIR`, default the same `reports/`):

    reports/ax5m/2026-11-19.json   one day (schema_version 1, below)
    reports/ax5m/index.json        instance meta + research expectation + kill switch + one headline row per day
    reports/ax5m/latest.json       copy of the newest COMPLETED day

Files are written to a temp file and renamed (atomic, idempotent: re-running a day replaces it), mode 0644,
directories 0755, so the web user can read them. The `reports/` content is data, not code.

Day selection: `--date D` (any UTC day), `--last-day` (yesterday, what cron runs), `--today` (the day so far,
`meta.partial = true`; replaced by the full file at the next `--last-day`), `--backfill` (every UTC day up to
yesterday on which a run was active or a trade closed). A day is reported when a run overlapped it or it had
trades; `--date` / `--last-day` also write a zero-trade report for a day on which the bot did not run, as long as it is
not before the instance's first run (so cron leaves no holes); nothing is written before the first run.

Rules: only trades of runs with mode DEMO count (dry_run trades are only counted in
`summary.excluded_dry_run_trades`); a trade belongs to the UTC day of its `exit_ms` (a trade opened before midnight
shows on the next day); win = net P&L > 0 (else bp); bp = `pnl_bps_price`, falling back to `pnl_bps_mid`;
the money columns (`pnl_net`, `gross`, `commission`, `swap`) are already scaled by `10**money_digits` when the bot
journals a trade, so they are shown as stored and `money_digits` is only listed; volumes are broker units (1/100 of
a unit); size = "half" when a trade's volume is below the largest volume ever traded on that symbol.

Day file (`schema_version` 1; `null` = not available; floats rounded to 6 digits):

| key | content |
|---|---|
| `meta` | `instance`, `timeframe`, `strategy`, `labels`, `date`, `day_start`, `day_end`, `generated_at`, `partial`, `as_of`, `runs` {`count`, `modes`, `restarts`, `started_in_day`}, `journal` (file name only), `note` |
| `summary` | `trades`, `excluded_dry_run_trades`, `wins`, `losses`, `flat`, `win_rate`, `net`, `gross`, `commission`, `swap`, `net_missing`, `avg_bp_price`, `median_bp_price`, `avg_bp_mid`, `median_bp_mid`, `t_stat_bp` (null below 3 trades), `avg_win`, `avg_loss`, `profit_factor` (null without losses), `best_trade`, `worst_trade`, `max_drawdown` (of cumulative net within the day), `avg_hold_s`, `max_hold_s`, `volume`, `stop_share`, `money_digits` |
| `breakdown` | `by_symbol`, `by_side`, `by_exit_reason` (as stored), `by_hour_utc` (exit hour), `by_abs_z` (entry \|z_poc\|: `<1`, `1-1.5`, `1.5-2`, `>2`, `unlinked` = no signal row), `by_size` (`full`/`half`); each group: `trades`, `wins`, `win_rate`, `net`, `avg_bp`, `volume` |
| `execution_quality` | `slippage_bp` {`n`, `mean`, `median`: realised bp minus the signal's simulated `taker_pnl_bps`}, `avg_adverse_bp`, `avg_markout_bp`, `avg_spread_entry_bp` (signals.spread / entry price), `orders`, `order_status`, `rejects_or_errors`, `error_codes` |
| `funnel` | `entry_z`, `signal_rows`, `above_entry_z`, `entered`, `rollover`, `gated` + `gated_reasons` (`approx`, `kql_*`, risk reasons, `busy:*`), `none` |
| `cumulative` | since the first demo trade up to the end of this day: `since`, `trades`, `wins`, `win_rate`, `net`, `avg_bp`, `research_taker_net_bp` (`report.REFS`: -0.31 at 5m, -0.84 at 1m; DESIGN.md section 1 / docs/findings-1m-screen.md), `vs_research_bp` |
| `research_expectation` | `taker_net_bp_per_trade`, `taker_gross_bp_per_trade`, `source` |
| `kill_switch` | as of generation, the numbers of the `report` "Kill switch" section: `cumulative`, `max_loss`, `distance_to_max_loss`, `today`, `daily_max_loss`, `distance_to_daily_limit`, `realised`, `unrealised` (+ note when the bot is not running), files; only attached to reports of the last two days, else `{"available": false}` |
| `trades` | up to 500 rows: `entry`, `exit`, `symbol`, `side`, `volume`, `entry_price`, `exit_price`, `reason`, `bp`, `net`, `z_poc`, `hold_s`, `size`; `trades_total`, `trades_truncated` |
| `risk_events` | per kind: `count` and the last 5 `{ts, detail}` (detail trimmed to 200 characters, secrets and absolute paths blanked) |

`index.json`: `schema_version`, `instance`, `timeframe`, `strategy`, `labels`, `generated_at`, `research_taker_net_bp`,
`first_day`, `latest`, `kill_switch`, and `days`: one row per day file (`date`, `partial`, `trades`, `wins`, `losses`,
`win_rate`, `net`, `avg_bp`, `cum_net`, `cum_trades`, `cum_win_rate`, `cum_avg_bp`, `runs`, `restarts`).

Cron (`/root/axioma-analytics/deploy/axioma-trades-report.cron`, copy to `/etc/cron.d/axioma-trades-report`; logs
rotated by `deploy/axioma-trades-report.logrotate`): one job per instance at 00:20 UTC, as root (the state dirs are
root-owned), `flock -n` per instance, `.venv/bin/autotrade --config <cfg> trades-report --last-day`.

One-off backfill after installing (or after a fix to the generator; safe to repeat, every day file is replaced):

    cd /root/axioma-analytics/ctrader_autotrade
    .venv/bin/autotrade --config config.toml trades-report --backfill
    .venv/bin/autotrade --config config.1m.toml trades-report --backfill
    # optional: today so far (partial)
    .venv/bin/autotrade --config config.toml trades-report --today
    .venv/bin/autotrade --config config.1m.toml trades-report --today

## Deployment

Two units, one per instance. Installing or (re)starting them is a manual step:

- `ctrader-autotrade` (`deploy/ctrader-autotrade.service`, here): `ax5m`, unchanged.
- `ctrader-autotrade-1m` (`/root/axioma-analytics/deploy/ctrader-autotrade-1m.service`): `ax1m`, runs
  `autotrade --config config.1m.toml run --mode demo --exec taker`, writes only `state/ax1m` and `logs/ax1m`
  (created by its `ExecStartPre`), same caps and restart policy. Install:
  `cp /root/axioma-analytics/deploy/ctrader-autotrade-1m.service /etc/systemd/system/ && systemctl daemon-reload &&
  systemctl enable --now ctrader-autotrade-1m`.

`deploy/ctrader-autotrade.service` is the unit installed at `/etc/systemd/system/ctrader-autotrade.service`
(copy it there by hand after a change, then `systemctl daemon-reload`). It runs
`autotrade run --mode demo --exec taker` as root from this directory with sandboxing (`ProtectSystem=strict`,
writes only to `state/` and `logs/`), `Restart=on-failure`, `MemoryMax=512M` and `CPUQuota=50%` because this
host serves the production webapp. The KQL settings are read from `[data] env_file`, so the unit needs no
`EnvironmentFile`.
