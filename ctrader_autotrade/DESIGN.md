# cTrader demo autotrader for the top-3 hypotheses: design

Status: **accepted** (2026-11-20). Lives in `axioma-analytics/ctrader_autotrade/`. Research source:
`research/output/screen/` (hypothesis screen, discovery Nov 5-12, holdout Nov 12-20 2026).

## 1. Summary

A separate Python/Twisted process trades `poc_reversion_4h` on a **demo** cTrader account.
It builds the research 5-minute bars from the same per-event depth data research uses (the Fabric
KQL table `dom_ctrader.agg_dom`, section 4; its own depth subscription is the fallback), computes the
features with the research code itself (parity tested), and journals everything for a forward test
against the research holdout. Orders, spreads and the stale-data guard use its own spot subscription.

**Expected P&L is negative.** This is a forward test of the signal, not a money-maker.

A second **instance** of the same code, `ax1m`, trades the 1-minute screen's analog on the same demo
account (section 3, "Instances"; `config.1m.toml`): z = rolling z over 60 one-minute bars of
`poc_reversion_1h` (= -poc_dist_60), |z| > 1, hold one 1-minute bar, half size when `events_z > 2`,
`range_reversal_12m` journalled only. Research holdout (docs/findings-1m-screen.md): IC +0.138, taker
gross -0.16 bp, **net -0.84 bp per trade**: expected to lose; deployed knowingly as a forward test.
Everything below describes the 5-minute default; the 1-minute values are in section 3 and section 4.

| Hypothesis | Role in the bot | Why |
|---|---|---|
| #2 `poc_reversion_4h` (holdout IC +0.16) | Direction: enter when the rolling z of `-poc_dist_240` exceeds 1 in absolute value; hold 1 bar | The only directional pick. Taker economics on holdout: +0.39 bp gross, -0.31 bp net (0.70 bp round-trip spread). |
| #3 `range_reversal_1h` (holdout IC +0.10) | Journalled only, not a gate | Agrees in sign with #2 on 90-91% of signals. The disagreeing 9-10% behave inconsistently between discovery and holdout. Its \|z\| > 1 trades lost 0.91 bp gross. |
| #1 `activity_predicts_vol` (holdout IC +0.22) | Size scaler: half size when `events_z > 2` | Predicts *how much* price moves, not direction. |

### The maker idea does not work on cTrader

On cTrader a Buy Limit fills when the *ask* reaches it, so a resting order is filled after price
has moved against you. You do not earn the spread.

A read-only proxy on the research bars (a limit at our side of the touch at signal close, counted
as filled if the next bar's range reaches it) gave:

- fill rate 81-83%;
- **-1.6 bp per fill on discovery and -2.1 bp on holdout**, worse than taker's -0.31 bp;
- about -0.6 bp of that is a random walk's spread cost; the rest is adverse selection.

### What the demo can establish

- **Forward IC.** The holdout block-IC sd is about 0.14, so t = 3 needs about 7 four-hour blocks,
  roughly 28 hours of market.
- **Not per-trade P&L.** A per-trade sd of 10-14 bp means about 3,600 trades are needed to resolve a
  0.4 bp edge.
- **Real-world inputs.** Actual demo-broker spreads and fills.

## 2. Strategy (frozen defaults)

Decisions are made once per instrument per 5-minute bar: at `slot_end + 3 s` with the KQL data
source (later, up to `slot_end + 10 s`, only while KQL catches up; section 4), at `slot_end + 2 s`
with the `ctrader` source.

**Features** are identical to research (`features.py`):

- `poc_dist_240 = (mid_close - vap_240m_poc) / (va_high - va_low)`
- `range_pos_12` with `min_periods = 6`
- `events_z`
- `rolling_z`: window 48, `min_periods = 12`, ddof = 1

**Entry.** Enter when `|z_poc| > 1.0`, on the side given by `sign(z_poc)`. The size multiplier is
0.5 when `events_z > 2`, otherwise 1.0.

**Execution.** `taker` places a market order at t, exactly as research `simulate_signal` does.
`maker` places a GTD limit at our side of the touch with a 120 s time-to-live and no repricing.
**Default: `taker` for real orders**, with `maker` journalled as a shadow on every signal (switch
with `--exec`).

**Exit.** Time exit at the next bar close, on these rules:

- same side at t+1: keep the position (rollover, as in `simulate_signal`);
- side flips: close at market, then enter the other way;
- no signal: close at market.

**Protective stop.** Server-side `relativeStopLoss` at `max(4 × sigma_bar × price, 3 × spread, broker slDistance)`,
which is about 40-60 bp. It is disaster protection only.

## 3. Architecture

```
autotrade/
  types.py      contracts: SymbolSpec, Quote, DepthRow, Bucket, Bar, FeatureRow, Intent, ExecReport
  config.py     .env (clientID, clientSecret, ctidTradeAccountId|ctidTraderAccountId, access_token,
                optional refresh_token) + config.toml
  transport.py  Transport protocol: OpenApiTransport (ctrader_open_api.Client) and FakeTransport (tests)
  session.py    handshake, live-account refusal, reconnect with backoff, token refresh, symbols, subscriptions
  book.py       order book and the collector's stamping rule (row per depth event, last spot timestamp)
  bars.py       10 s buckets to 5-minute bars: vendored analytics.kpis/spread/volume_at_price/value_area
                and export_flat slot/VAP metrics
  kql_source.py [data] source = "kql": 10 s bucket query on agg_dom, watermark, data gates (stream_state,
                window_gaps), Kusto reader in the reactor thread pool
  features.py   FeatureEngine: 5-minute grid with NaN rows, vendored research pandas expressions
  warmup.py     source = "ctrader" only: own bucket store reload, plus ProtoOAGetTickDataReq gap fill;
                provenance in approx_share
  strategy.py   pure decide() -> Intent
  risk.py       limits, KILL/PAUSE files, breakers, sessions, spread and stale-data guards
  execution.py  order/position state machine; DemoBroker (Open API)
  simbroker.py  dry-run fills from live quotes
  journal.py    SQLite (WAL) + JSON-lines log
  report.py     forward vs research comparison
  __main__.py   CLI: run | probe | flatten | parity | report
```

### Handshake

1. ApplicationAuth
2. GetAccountListByAccessToken. **Refuse orders unless the account is listed and `isLive == false`.**
3. AccountAuth, then Trader
4. SymbolsList (resolve by name), then SymbolById
5. Reconcile
6. Warm-up
7. Subscribe to spots (with timestamps), and depth only with `[data] source = "ctrader"`

The host is `demo.ctraderapi.com:5035` only.

### Order and position state machine (at most one position per symbol)

`IDLE -> PENDING_NEW -> WORKING -> (PARTIAL) -> OPEN -> EXIT_PENDING -> CLOSED -> IDLE`,
plus `RECONCILING` (no reply within 5 s) and `CANCEL_PENDING`.

Orders carry `clientOrderId = {instance}-{sym}-{slot start s}-{leg}`, `label = {instance}` and
`comment = "{instance} {strategy} z={z_poc} sig={signal_id}"`. The bot manages only orders and positions with
a label its instance owns. A restart never resends an order and never doubles exposure.

### Instances (2026-11-20)

`[instance]` in the config: `name` (default `ax5m`), `timeframe` (`5m` | `1m`), `legacy_labels`,
`claim_unlabelled`, `netted_sole`. One process per instance, all on the same code.

| | `ax5m` (default, `config.toml`) | `ax1m` (`config.1m.toml`) |
|---|---|---|
| slot / POC window / z-window | 5 min / 240 min / 48 bars (min_periods 12) | 1 min / 60 min / 60 bars (min_periods 15) |
| signal (traded / journalled) | `poc_reversion_4h` / `range_reversal_1h` | `poc_reversion_1h` / `range_reversal_12m` |
| history bars, warm-up fetch | 150, 16.5 h | 120, 3 h |
| state, logs | `state/`, `logs/` (as before instances) | `state/ax1m/`, `logs/ax1m/` |
| labels owned | `ax5m`, `axq-poc4h-v1` (its pre-instance label) | `ax1m` |

- **Timeframe.** `autotrade/timeframe.py` mirrors `research/pipeline/timeframe.py` (`TF_5M`, `TF_1M`) and
  `webapp/export_flat.TIMEFRAMES`; `tests/test_timeframe.py` pins the equality and the unchanged 5-minute values.
  Everything slot-derived reads it: bars (`slot_bar`, `BarBuilder`, `replay_bars`, bucket retention), features
  (research `clean_metrics` / `build_features` / `add_hypothesis_signals` with `tf=`, the POC column and
  hypothesis keys, `_rolling_z` window, the spread median over one z-window), the stop's per-slot sigma
  (x sqrt(30) at 5m, x sqrt(6) at 1m), exit times, the HOLD extension, clientOrderId slot, the slot timer,
  the KQL windows, the repair horizon (one z-window), the bars-since-open gate (min_periods) and the report
  (IC block 4 h / 1 h, research references). Per-bar counts that research keeps in bars stay in bars
  (`range_pos_12`, the 12-bar vol median of the stop).
- **Separate state.** Each instance's state dir holds its own journal, KILL / PAUSE / PARITY_OK, kill
  baseline and status, token file, so all risk counters (daily loss, trades today, consecutive losses,
  rejects, order rate, kill switch) are per instance. A non-default instance must not use `state/` or
  `logs/` (config error). `state/.../INSTANCE.json` records the owner; another instance or timeframe on the
  same dir is refused; `run` holds `instance.lock`.
- **Labels and ownership.** Every `ProtoOANewOrderReq` (entries with their server-side `relativeStopLoss`;
  netting-account exits) carries `label` = the instance name and the comment above; the position inherits
  them (`ProtoOAPosition.tradeData.label/comment`; verified read-only on account 48664595: the 5m bot's open
  position shows `label='axq-poc4h-v1' comment='41-1795176300'`, and its stop and closing orders carry the
  label too). `ProtoOAClosePositionReq` / `ProtoOAAmendPositionSLTPReq` / `ProtoOACancelOrderReq` have no
  label: they name an own position/order id. `ProtoOADeal` has no label: deals belong to their position.
  Execution events reach every connection on the account, so each instance matches events by its own
  clientOrderId (prefixed: the two bots share slot boundaries), order id, position id, or an explicit own
  label, never by "no label". Reconcile, adoption, flatten (KILL, shutdown, kill switch, `autotrade
  flatten`), "unrealised (own positions)" and the report consider only labels the instance owns.
- **Unlabelled positions.** Every order the bot ever sent had a label (`axq-poc4h-v1` before instances), so an
  unlabelled position is manual or another program's (the account had one in the 24 h before this change).
  They are left alone by default. `claim_unlabelled = true` (allowed on the default instance only, never on
  `ax1m`) makes `ax5m` adopt and time-exit them.
- **Account type.** Instances on the same symbols stay independent only on a **hedging** account (each order
  opens its own position). Account 48664595 is `HEDGED` (`autotrade probe`, 2026-11-20). On a `NETTED` account
  the start-up reconcile refuses (exit 2): any instance when a position/order it does not own sits on its
  symbols; a non-default instance always, unless `netted_sole = true` asserts it has the account to itself
  (its own demo account via `--env`). On `HEDGED`, a cheap exposure check logs open volume per symbol and
  warns (`exposure_warning`) if two more orders could pass `maxExposure` / `maxVolume`.

## 4. Feature parity and warm-up

Research definitions to reproduce:

- one row per depth event, stamped with the latest spot timestamp;
- 10 s buckets holding the last book state and an event count;
- slot mids taken from the bucket-last mids;
- realised vol from consecutive bucket mids;
- VAP over 240 minutes: 60 equal bins, event-weighted, POC = first max bin, greedy 70% value area;
- a 5-minute grid with NaN rows (weekends and gold's daily break count against the windows).

### Data source (`[data]` in `config.toml`)

**`source = "kql"` (default).** The bars come from `dom_ctrader.agg_dom`, the table research and the
webapp read. The `ctrader-collector@<broker>-<account>-<SYMBOL>` units write one row per depth event with
the stamping rule above; ingestion takes under 1 s. The bot selects its own streams by
`ctidTraderAccountId` (its account) and `symbolId` (resolved through cTrader, compared as a string).
`autotrade/kql_source.py` asks for 10 s buckets in KQL:

- each bucket holds the last book state and the event count, as in `Bucketiser` and `webapp/queries.py`
  `series_range`;
- ties go to the greatest `eventSeq`, which is what `Bucketiser` does when rows share the last timestamp
  (it keeps the later one). The plain `arg_max(timestamp, ...)` in the webapp picks arbitrarily: about 1%
  of buckets on acgmarkets have such ties. The key is a fixed-width string, so it does not care where
  `eventSeq` starts;
- one query also returns the stream's **watermark** (latest `timestamp`, no upper bound).

Tests prove the query semantics equal `Bucketiser` on recorded and synthetic rows, and the bars equal
`export_flat.build_rows` to 1e-9. `autotrade kql-check --verify` repeats the first check against live KQL.

- **Warm-up.** One query covers the whole history window (`history_slots` × 5 min + 240 min = 16.5 h).
  The buckets are exact (`source "kql"`), so they do not count toward `approx_share`, and the warm-up
  bars count as live for `events_z`. A restart can trade at the next slot close. There is no tick fill and
  no 4-hour wait. If the query fails, every later slot retries the history fetch first.
- **Slot close.** At `slot_end + data.grace_s` (3 s) the bot fetches `[last fetch − one slot, t)` for all its
  streams. A stream is *complete* when the watermark is at or after `t`, or, while the bot's own spot
  feed is up, when the watermark has reached the latest spot timestamp the bot has seen (a quiet
  market). Until every stream is complete the bot retries every `retry_s` (2 s), at most `retries` (3)
  more times, and never past `slot_end + deadline_s` (10 s). Then it decides.
  `deadline_s` must stay below `grace_s + EXIT_BACKSTOP_S` (12 s). Past that, the order manager has
  already time-exited a position the strategy might have rolled over.
- **Data gate** (per symbol and bar; `None` means tradable):
  - `kql_error`: every attempt failed (network, auth, timeout, bad config);
  - `kql_stale`: the watermark is older than `t − freshness_s` (15 s);
  - `kql_gap`: the 240-minute VAP window has a stretch without buckets longer than `gap_max_s` (60 s),
    counting only time when the symbol's session schedule (`SessionCalendar`) is open. Gold's daily
    break and weekends are therefore not gaps. A stream that started less than 4 hours ago also has a
    gap, because its window is truncated.

  Measured on the live feed: in an open session, depth events arrive in every 10 s bucket. The only
  in-session gaps found in 3 days were collector outages.
- **1-minute instance (`config.1m.toml`).** Same mechanism, retuned for a 60 s slot:
  `grace_s = 2`, `retry_s = 1`, `retries = 4`, `deadline_s = 6`, `timeout_s = 5`, `freshness_s = 5`,
  `gap_max_s = 30`. Budget per slot (measured 2026-11-20): the slot-close query (two minutes of buckets) takes
  ~0.14 s warm (0.6 s for the first, cold one), the 1-minute bar ~4 ms and the research features ~56 ms per
  symbol, so the decision and order go out at about slot_end + 2.3 s, at worst at slot_end + 6 s. The time-exit
  backstop is slot_end + grace_s (2) + 10 s = 12 s, after the latest decision and far before the next slot
  (60 s); config validation keeps `deadline_s < grace_s + 10`, `grace_s + 10 < slot / 2`,
  `freshness_s < slot / 2`, `data.grace_s >= grace_s` and `limit_ttl_s < 2 slots`. Freshness 5 s because a
  15 s lag would be a quarter of the bar whose close is the signal. Gap 30 s = more than three consecutive
  missing 10 s buckets in the 60 min window (session-aware as above): on account 48664595 from 05:30 to 12:10
  UTC on 2026-11-20 not one bucket was missing on either stream, so 30 s is far from normal yet tolerates a
  single collector hiccup; the 5-minute 60 s would be a whole missing bar. Load: one query per minute for
  `ax1m` plus one per 5 minutes for `ax5m`, each a two-slot range (the 7-day watermark scan dominates).
  The warm-up is one query of 3 h (120 bars + 60 min).
- **What a gate does.** A gated bar gives no entry and no rollover (reason = the gate in `signals`).
  An open position is time-exited at the slot close, as for any bar without a trusted signal.
  `must_flatten` (session close, disconnect, KILL) runs on the 5 s risk tick from spots and the
  journal, and the server-side protective stop is never touched, so data trouble never blocks
  flattening. Every change of a symbol's gate is a `data_gate` risk event; every slot that got no data
  at all is a `kql_error` risk event. KQL errors never raise into the bot.
- **Repair.** A bar closed on `kql_stale` or `kql_error` data enters the features as non-live. At the next
  complete slot the bot rebuilds the bars since the last complete one (up to 48 back) from the
  re-fetched buckets, in the FeatureEngine and the `bars` table. The feature history then matches what
  research will compute. The signal rows of the gated slots keep what was decided at the time.
- KQL does not depend on the bot's cTrader connection, so a reconnect needs no gap fill.
- Kusto settings: `KUSTO_CLUSTER_URI`, `KUSTO_DATABASE`, `KUSTO_TABLE` and `AZURE_TENANT_ID` /
  `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` (service principal; without them `DefaultAzureCredential`).
  They are read from `data.env_file` (default `../.env`, the webapp's file; only these keys are read)
  or from the process environment, which wins. The Kusto client is blocking, so it runs in the
  reactor's thread pool, with a per-query timeout.

**`source = "ctrader"` (fallback).** The bot subscribes to depth, feeds `Stamper` → `Bucketiser`
itself, stores its buckets in `state/autotrade.db` and warms up from them.
- **Warm-up.** On restart the bot reloads its stored buckets, then gap-fills from tick data over the
  same history window (16.5 h). Tick data counts spot changes, not depth events, so the weighting
  differs: those buckets are approximate (`approx_share > 0`). `events_z` uses live bars only.
  Without `state/PARITY_OK`, DEMO entries wait until the 240-minute window is fully live, about
  4 hours after every restart.

Every signal journals `approx_share` (0 with `kql`).

**Parity tests:**

- P1: vendored functions vs `webapp/analytics.py` and `export_flat`, to 1e-12.
- P2: `metrics_5m` -> FeatureEngine vs research `build_features`, to 1e-9 including the NaN pattern.
- P3: raw-event fixture -> bars vs `metrics_5m`, plus a depth-weighted vs tick-weighted VAP bound.
- P4: nightly drift check.

**Gate for trading on approximate bars:** Spearman(`poc_dist_240`) ≥ 0.98 and side agreement ≥ 97%.

## 5. Risk controls (defaults)

**Account and host**

- A live account or live host is refused for orders.
- `DemoBroker` asserts `verified_demo` on every send.

**Position and order limits**

- Base size is 2 × minVolume. One position per symbol, at most 2 in total.
- At most 200 trades a day and 10 orders a minute.

**Loss limits**

- Daily loss of -1.0% of start-of-day balance or -150 bp: go flat until the next daily reset.
- 15 consecutive losses: pause for 1 hour.

**Daily reset time**

- Every "daily" quantity (the daily loss latch, `trades_today` / `max_trades_day`, `pnl_today_bps` / `pnl_today_pct`
  and its start-of-day balance, the kill switch's daily loss, the `RiskManager` day roll) runs from one reset
  instant to the next: `[risk] daily_reset = "HH:MM"` in `daily_reset_tz` (IANA name). Default `00:00` UTC.
  Example: `17:00` `America/New_York` for the forex day.
- The reset is wall-clock time in that zone, so a DST change moves it in UTC and makes that day 23 or 25 hours
  long. One helper, `autotrade/resetday.py` (`day_start_ms`, `next_reset_ms`), serves all of them.
- A bad time or zone is a `ConfigError` at start-up.

**P&L kill switch** (`[kill]` in `config.toml`, `autotrade/killswitch.py`; off unless `enabled = true`)

- P&L = realised (journal trades of this account and run mode, net of commission and swap, account currency)
  + unrealised of the bot's own open positions. Unrealised: in DEMO the server's `netUnrealizedPnL` per own
  position (`ProtoOAGetPositionUnrealizedPnLReq`, read-only, at most once per `check_interval_s`); on error and in
  DRY_RUN the touch mark `side * (exit touch - entry) * volume / 100`. The mark assumes the quote currency is
  the deposit currency; if a symbol's quote asset differs, a warning is logged once and unrealised is unknown
  (realised-only) unless the server value is available.
- Cumulative P&L counts from a baseline, `state/kill_baseline.json` (0600): created the first time the bot runs
  with the switch on, new when the account id changes, re-armed by `autotrade kill-reset`. `kill.since` overrides it.
- `max_loss`: cumulative <= -max_loss: flatten, write `state/KILL`, exit 0 (the unit's `Restart=on-failure` leaves
  it stopped; a `KILL` file present at start flattens and exits at once).
- `profit_target` (0 = off): cumulative >= target: `profit_action = "kill"` as above, or `"pause"` (write
  `state/PAUSE`: no new entries, exits continue).
- `daily_max_loss` (0 = off): P&L since the last daily reset (realised that day + all current unrealised)
  <= -daily_max_loss: flatten and block entries until the next reset (the same latch as the daily loss limits;
  no `KILL` file).
- Every trigger is a WARNING log and a `risk_events` row (`kill_max_loss`, `kill_profit_target`,
  `daily_max_loss`) holding the numbers. The latest numbers are in `state/kill_status.json` for `autotrade report`.

**Kill switches**

- `state/KILL`: cancel orders, flatten and exit.
- `state/PAUSE`: no new entries.

**Sessions** (from the symbol schedule)

- No entries in the last 10 minutes before a close.
- Flat 2 minutes before a close.
- After an open, wait for the z `min_periods`.

**Market guards**

- Spread guard: more than 2 × the 48-bar median spread.
- Stale guard: 30 s without data.
- 3 rejects within 10 minutes: pause.
- Flatten after a disconnect longer than 120 s.

**Token expiry**

- With `refresh_token`: refresh and persist to `state/token.json` (mode 0600).
- Without it: SAFE mode (no entries) and exit.

## 6. Isolation and deployment

- Own process, connection, venv (`.venv`) and files. It must never touch the depth collector
  (`/root/data-mining-project/collector`). With `source = "kql"` it reads the collector's output in KQL,
  read-only, and needs those collector units running for its own account and symbols.
- One systemd unit per instance: `ctrader-autotrade.service` (`ax5m`) and `ctrader-autotrade-1m.service`
  (`ax1m`, `/root/axioma-analytics/deploy/`, writes only `state/ax1m` and `logs/ax1m`).
- systemd unit `ctrader-autotrade.service` (installed at `/etc/systemd/system`). It runs as root with
  sandboxing (`ProtectSystem=strict`, write access only to `state/` and `logs/`), `Restart=on-failure`,
  `MemoryMax=512M` and `CPUQuota=50%`. The caps are there because this host serves the production
  webapp. The Kusto settings come from `data.env_file`, so the unit needs no extra `EnvironmentFile`.

## 7. Build plan

1. **T0**: scaffold and contracts.
2. In parallel:
   - **T1**: book, bars and features, with the parity tests;
   - **T2**: transport, session and warm-up, against a fake transport;
   - **T3**: execution, sim broker and journal;
   - **T4**: strategy and risk;
   - **S1**: a read-only Kusto parity fixture.
3. **T5**: CLI, end-to-end test, report.
4. Run `probe`, then 24-48 hours of dry-run.
5. **A1**: parity and warm-up review.
6. **A2**: go/no-go for demo orders.

## 8. Decisions (2026-11-20)

- Language/location: Python, in the `axioma-analytics` repo (the crypto-quant-platform copy was a mistake and is deleted).
- Real orders: taker (market) by default; maker simulated as a shadow on every signal.
- No `refresh_token` yet: on token expiry the bot enters SAFE mode and exits (add one to `.env` to enable refresh).
- Host: this server, with the resource caps in section 6; dry-run first.
- 2026-11-20: second instance `ax1m` (1-minute bars) on the same hedging demo account; the 5-minute bot becomes
  instance `ax5m` and labels new orders `ax5m` (it still owns `axq-poc4h-v1`). Unlabelled positions are not
  claimed by default. The 1-minute risk limits scale with the bar count (`max_trades_day = 1000`,
  `daily_loss_bps = 1000`); the account-currency kill limits are the 5-minute ones.
