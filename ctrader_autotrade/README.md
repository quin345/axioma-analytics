# ctrader_autotrade

Demo-account cTrader autotrader for the `poc_reversion_4h` hypothesis, a forward test of the
research signal (expected P&L is negative). Design and decisions: [DESIGN.md](DESIGN.md).

Status: built and running on the demo account as the `ctrader-autotrade` systemd unit (see
Deployment). Its bars come from the Fabric KQL table `dom_ctrader.agg_dom` by default (see Data source).

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
| `autotrade probe` | Connects to the demo host, handshakes and prints (never any secret) the account id, isLive, broker, account type, money digits, balance, each resolved symbol (digits, lot/min/step/max volume, slDistance and distanceSetIn, schedule summary, timezone) and one spot per symbol with its spread. Subscribes spots only, never depth. Places no orders. Disconnects. |
| `autotrade run --mode dry-run` | Full bot with the simulated broker: nothing is ever sent to the order API. Journals bars, signals, shadows and simulated trades. |
| `autotrade run --mode demo [--exec taker\|maker]` | Same, with real orders on the demo account (taker by default, as in DESIGN.md). Refuses to start trading unless the session is a verified demo account. |
| `autotrade run ... --hours N` | Stop after N hours (flattens, flushes, exits 0). For bounded test runs. |
| `autotrade flatten` | Connects (demo), cancels own resting orders (label `axq-poc4h-v1`), closes own positions. Foreign orders and positions are never touched. |
| `autotrade parity` | P4 drift check: runs the research feature code over the bot's own `bars` table and compares with the features journalled in `signals`. |
| `autotrade report [--since-hours N]` | Forward-test report from the journal (see below), including the kill-switch numbers. |
| `autotrade kill-reset` | Re-arms the P&L kill switch: writes a new baseline at now. Does not remove `state/KILL` or `state/PAUSE` (it prints the command). |
| `autotrade kql-check [--minutes 30] [--symbol-ids 41,10026] [--verify]` | Read-only. Runs the bot's KQL bucket query for its own streams and prints buckets, events, watermark lag and gaps. `--verify` recomputes the buckets from the raw rows and compares them. Does not connect to cTrader. Symbol ids default to those in the journal's recent bars. |

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

## Control files (in `state/`)

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

`autotrade report` prints plain-text sections; the research numbers are the holdout references from DESIGN.md.

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

## Deployment

`deploy/ctrader-autotrade.service` is the unit installed at `/etc/systemd/system/ctrader-autotrade.service`
(copy it there by hand after a change, then `systemctl daemon-reload`). It runs
`autotrade run --mode demo --exec taker` as root from this directory with sandboxing (`ProtectSystem=strict`,
writes only to `state/` and `logs/`), `Restart=on-failure`, `MemoryMax=512M` and `CPUQuota=50%` because this
host serves the production webapp. The KQL settings are read from `[data] env_file`, so the unit needs no
`EnvironmentFile`.
