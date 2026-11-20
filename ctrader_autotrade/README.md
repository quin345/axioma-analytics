# ctrader_autotrade

Demo-account cTrader autotrader for the `poc_reversion_4h` hypothesis, a forward test of the
research signal (expected P&L is negative). Design and decisions: [DESIGN.md](DESIGN.md).

Status: built (T0-T5): runnable bot, CLI, forward-test report and end-to-end tests. Not yet run
against the live demo server: start with `probe`.

## Setup

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    echo /root/data-mining-project/src > .venv/lib/python3*/site-packages/ctrader_open_api_src.pth
    .venv/bin/pip install -e .
    cp .env.example .env   # fill in credentials (never committed)

`ctrader_open_api` comes from `/root/data-mining-project/src` via a .pth file, because installing
that whole project would pull the Azure/pyodbc dependencies. Non-secret settings live in `config.toml`.

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
| `autotrade report [--since-hours N]` | Forward-test report from the journal (see below). |

Exit codes: 0 normal (including KILL), 1 error or timeout, 2 refused (live account, wrong host,
account not in the token's list), 3 SAFE mode (token expired without a refresh token).

## Recommended rollout

1. `autotrade probe`. Check: not live, the right broker, both symbols resolved (aliases in
   `config.toml`), plausible spreads, slDistance/distanceSetIn (the PERCENTAGE unit is an assumption in
   `strategy.py`), account type HEDGED/NETTED, money digits.
2. `autotrade run --mode dry-run` for 24-48 hours (e.g. `--hours 48`, or under `nohup`). It trades
   only in the simulator but journals everything. The first ~4 hours rely on approximated history
   (`approx_share > 0`); dry-run journals those signals anyway.
3. `autotrade report` and `autotrade parity`. A forward IC t of about 3 needs ~7 four-hour blocks (about 28 h of
   market). Check the taker shadow against the research figures, the maker-sim numbers, spreads, risk
   events, and the parity drift.
4. Only then `autotrade run --mode demo` (or the systemd unit, see below). In demo mode entries on
   approximated bars are blocked (reason `approx`) until `state/PARITY_OK` exists: create it (empty
   file) after reviewing the parity report and the DESIGN.md section 4 gate
   (Spearman(poc_dist_240) >= 0.98, side agreement >= 97%). Without it the bot waits for four hours
   of fully live history after every restart.

## Control files (in `state/`)

- `state/KILL`: cancels own orders, flattens, exits the process (code 0). Checked every 5 s. Remove
  the file before restarting or the bot stops again at once.
- `state/PAUSE`: no new entries (existing positions still exit on schedule). Delete to resume.
- `state/PARITY_OK`: demo only, lifts the approximate-bar block (see rollout step 4).
- `state/token.json`: refreshed access token (mode 0600), only if `refresh_token` is configured.

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
- **Risk events**: counts by kind (entry_blocked, order_reject, disconnected, flatten, kill, ...).

## Deployment

`deploy/ctrader-autotrade.service` is a systemd unit as text only; it is not installed by anything.
It runs as `User=autotrade` with `Restart=on-failure`, `MemoryMax=512M` and `CPUQuota=50%` because this
host serves the production webapp. The repo currently lives under `/root`; running as root from
there (or giving an unprivileged user access) means adjusting `User=` and the paths in the unit.
