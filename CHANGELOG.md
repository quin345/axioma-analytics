# Changelog

Notable changes to Axioma Analytics, newest first.

## 2026-10-18 — Classification reads each broker's own class catalogue

Scope: the asset class is taken from the instrument's **own broker**'s catalogue
(`symbols_category` → `asset_classes`) instead of one broker's numeric
`symbolCategoryId` codes. acgmarkets files `XAUUSD.pro` under its category 10 =
*Metals*, but the numeric fallback read 10 as the Oil id, so gold was grouped
under Energy.

### Backend
- [x] `app/meta.py`: `symbol_catalogue()` left-joins `symbols_category` and
      `asset_classes` — on the account (`ctrader_account_id` =
      `ctidTraderAccountId`) **and** the category/class id, since the same
      `symbolCategoryId` means different things per account — and projects
      `asset_class_id` + `asset_class_name` (the broker's label, e.g.
      `"Metals (Pro)"`). Every projected column is qualified because the join
      brings in colliding names. The configured table (`symbols_metadata` by
      default) stays the FROM target, so the `enabled` filter is unchanged, and
      an empty read keeps the two new columns.
- [x] `app/meta.py`: the `symbolId` int cast now lands after the
      dedup/`reset_index`, so the read no longer raises pandas'
      `SettingWithCopyWarning`.
- [x] `app/assets.py`: `BROKER_CLASSES` now also carries the labels the brokers
      actually ship — `Energies (Spot)`/`Energy (Spot)`, `Crypto Currency
      (Spot)`, `Currency Index (Spot)`, `Commodities Indices (Spot)`,
      `Treasuries`, `Forwards…`, `Perpetual …`, `Perpetual Cryptocurrency` —
      each mapped to the same client key as its canonical name, with the
      account qualifier in parentheses stripped by `_broker_class_key()`.
      Equity/ETF labels stay unmapped on purpose (the taxonomy has no
      single-name share class).
- [x] `app/assets.py`: `_fx_subclass()` reads the pair off the base ticker, so a
      broker account suffix (`EURUSD.pro`, `GBPUSD.raw`) no longer demotes a
      major to a cross.
- [x] `app/service.py`: `_build_catalogue()` passes `row.asset_class_name` into
      `classify_asset_class()`, read with `getattr(row, "asset_class_name",
      None)` so a metadata frame without the join (legacy/fake) still
      classifies from the raw cTrader signals instead of raising.

### Measured effect on the live dimension
- acgmarkets: `XAUUSD.pro`/`XAGUSD.pro` Energy → **Metals**; `USOIL.pro`/
      `UKOIL.pro` Commodities → **Energy**; 28 `Forex (Pro)` rows were landing
      in Metals/Energy → **FX**. All 41 enabled acgmarkets instruments now
      follow `symbols_category`/`asset_classes`.
- pepperstone: 86 `Forex (Spot)` rows (energy/index/metal) → **FX**;
      `Forwards` (energy/index/metal) → **Futures & Forwards**;
      `Perpetual Commodity` → **Commodities**.
- icmarketssc: 5 gold futures (`GCM25`…) stay with the broker's own **Metals**
      class rather than the name-pattern `future`.

### Tests
- [x] `tests/test_assets.py`: every broker label resolves to the right class;
      `XAUUSD.pro` + `"Metals (Pro)"` + category 10 is a **metal** (with the
      numeric-only read asserted as the old misread); energy labels stay energy;
      equity/ETF labels stay unclassified; each `BROKER_CLASSES` entry maps to a
      displayable class; a broker suffix keeps majors majors and exotics
      exotics.
- [x] `tests/test_meta.py` (new): pins the projection, the join keys, the
      configured FROM target, the scope parameters, the unusable-id drop and the
      empty-read shape — no server required.
- [x] `tests/test_service.py`: the catalogue classifies from the broker label,
      and a metadata frame without the column still classifies.
- [x] `python -m pytest tests -q` → 12 failed, 477 passed (58 new; 419 passed
      before). The 12 failures are the pre-existing environment ones (live KQL
      answers `403 Forbidden`, the Redis host in `.env` is unreachable); the
      failure set is byte-identical on the previous commit.

### Docs
- [x] `README.md` §Asset classification / `docs/methodology.md` §10 /
      `docs/dashboard_metrics.md` coverage row: the class comes from the
      broker's own catalogue label, the qualifier is stripped, the extra
      per-broker spellings are listed, and the numeric `symbolCategoryId` map is
      described as the last-resort fallback.

---


## 2026-10-18 — Docs: (symbolId, ctidTraderAccountId) is the composite key onto broker-agnostic metadata

Scope: documentation. Every doc now states the key the code uses — the KQL
facts are per `(symbolId, ctidTraderAccountId)` and that composite maps onto
`symbols_metadata(symbolId, ctrader_account_id)` → `broker_name`, the
broker-agnostic dimension — and references that no longer match the code were
dropped.

### Composite key
- `README.md`: intro, the metadata flow diagram and *Symbol names* document the
  composite → `ctrader_account_id` → `broker_name` mapping; the cache section,
  the `metrics_5m` grain and the API section document `ticks:<symbolId>[:<account>]`
  keys, the `symbol=<symbolId>|<account>` request form and the payload's
  `account` field; the `/api/health` row adds `meta_connected`/`meta_error`.
- `docs/dashboard_metrics.md` / `docs/methodology.md`: table grains are
  `(symbolId, ctidTraderAccountId, tick)` and `(symbolId, ctidTraderAccountId,
  as_of)`; query scoping (`_SYMBOL_MATCH` / `_ACCOUNT_MATCH`), the cache-key
  table, `metrics.slide` dedup and the §8 read discipline all key on the pair;
  the architecture/module maps now include `app/meta.py`.
- `docs/metrics_5m.md`: the producer contract (grain, row shape, Stage 0
  anchor, Stage 1 sort/project, Stage 2 join, Stage 3/4 `by` clauses, Stage 5
  assemble, the `Metrics5mLatest` dedup) groups by the composite, matching what
  `app/kql.py` reads back.

### Dead references dropped
- icmarkets-only dimension wording (README troubleshooting, methodology §10/§14)
  → the dimension is account/broker-scoped; instruments without a metadata row
  are hidden from the selector and counted by the `uncovered_count` hint.
- "instrument names came from the same KQL database" → they come from local
  PostgreSQL, reported by `meta_connected`.
- "the cycle also warms `DEFAULT_SYMBOL`" and the test-coverage line's "the
  default symbol warmed" → no default warming exists (a cold cache stays empty
  until a request lazily loads it).
- Retired 1-minute cadence: `CACHE_REFRESH_MINUTES=1` / "one-minute slots" in
  `docs/metrics_5m.md`, the 1-minute prose in `.env.example`, and README's
  "at most a minute behind" / "roughly the last minute" / "many times a
  five-minute cycle" → the shipped five-minute cycle (450 s = 1.5 cycles).

---

## 2026-10-18 — Liquidity read path key on (symbolId, ctraderAccountId)
- The tick/count read now groups `agg_dom` by `symbolId` **and** `ctidTraderAccountId`; grouping by the id alone previously folded two feeds for one id into one count and silently assigned it to whichever account row came first.
- `app/kql.py`: `symbol_tick_counts()` builds `(symbolId | ctidTraderAccountId)` keys and `_clean_number()` normalises both halves; null/NaN bins become a blank account (``"41|"``) instead of the bare id.
- `app/metrics.py`: `load()` takes an `account` (the `ctidTraderAccountId` half) so the cached precomputed grid is read per-account; `slide()` dedupes per `(symbolId, ctidTraderAccountId, _ts)` so one id traded under two feeds cannot interleave stops.
- `app/service.py`: `_build_catalogue()` keys the catalogue by the composite; legacy null-account KQL rows still fold into their symbol entry. A KQL pair missing from the dimension under **every** account stays reachable as its own bare pair entry (rather than being swallowed by a metadata-only row for the same symbol id) so a ~470k-tick trade is never orphaned.
- `app/service.py`: `lookup()` prefers the entry with ticks when no cTrader account is configured, so a metadata-only row cannot shadow the traded feed's own pair.
- Tests pin the composite-key behaviour in `test_service.py`, `test_kql.py`, `test_metrics.py`.



---
---

## 2026-10-18 — Symbol name carries its broker

Scope: the name a reader sees for a symbol now includes the broker it came
from (`XAUUSD (ACME)`), resolved per account through the existing
`(symbolId, ctraderAccountId)` composite key.

### Backend
- [x] `app/service.py`: `Instrument` gains a `broker` field (read from the
      dimension row's `broker_name`); `display` — and therefore the payload's
      `name` and the report's `symbol_name` — appends it as `Ticker (Broker)`.
      The bare `name` ticker is unchanged so classification, `lookup()` and the
      `DEFAULT_SYMBOL` match keep comparing exactly; `to_dict()` now also
      exposes `ticker` and `broker` for clients that need an exact value.
      The catalogue stays keyed by `(symbolId, ctraderAccountId)`, which is
      what makes the appended broker the one belonging to that entry's own
      account instead of the first row per symbol id.
- [x] `app/static/app.js`: the XAUUSD exact-name fallback matches `s.ticker`
      (bare) instead of the broker-annotated label.

### Tests
- [x] `tests/test_service.py`: pins that each composite entry appends *its*
      account's broker and that a blank broker keeps the plain name.
- [x] `tests/test_selectors.py`: pins the payload's `name`/`ticker`/`broker`.
- [x] `python -m pytest tests -q` → 427 passed (3 new), 0 failures.

### Docs
- [x] `README.md`: the "Symbol names" section documents the appended broker
      and the `ticker`/`broker` payload fields.

---

## 2026-10-18 — Instrument metadata moves from Fabric KQL to local PostgreSQL

Scope: the instrument dimension leaves the Fabric KQL database and is served
from local PostgreSQL (`symbols_metadata`), broker-agnostic — each symbol row is
bound to its cTrader account id (`ctrader_account_id`) and each account id maps
to its broker name (`broker_name`). Ticks (`agg_dom`) and the precomputed grid
(`metrics_5m`) stay in KQL. `psycopg2-binary` added to requirements.

### Backend
- [x] `app/meta.py` (new): reads `symbols_metadata` from Postgres via psycopg2,
      scoped by `CTRADER_ACCOUNT_ID` / `BROKER_NAME` (blank = all accounts /
      all brokers). Returns `symbolId`, `symbolName`, `symbolCategoryId`,
      `description` plus the account/broker scope; newest write per
      (symbol, account) wins; `symbol_catalogue`, `symbol_count` and `ping`.
- [x] `app/config.py`: `symbol_table` kept as a legacy alias honoring
      `SYMBOL_TABLE`/`PG_SYMBOLS_TABLE` (default `symbols_metadata`). Adds
      `pg_host`/`pg_port`/`pg_database`/`pg_user`/`pg_password`/
      `pg_symbols_table`/`ctrader_account_id`/`broker_name`/`pg_timeout_seconds`.
- [x] `app/service.py`: health probes KQL and Postgres independently
      (`meta_connected`/`meta_error` on `/api/health`); the catalogue merges
      Postgres metadata with KQL tick counts; a metadata outage leaves ticks
      readable but names/classification stale. `kql.symbol_count`/`symbol_catalogue`
      kept as deprecated wrappers over `app.meta`.
- [x] `app/service.py`: the catalogue is keyed by the `(symbolId, ctraderAccountId)`
      composite so the same symbol id bound to several cTrader accounts keeps each
      account's own `symbolName` instead of the first row per id silencing the rest;
      `lookup()` resolves a bare symbol id to the configured account's row
      (`CTRADER_ACCOUNT_ID`) or the first match; `reset()` purges cache entries by
      plain symbol id. New tests pin both behaviours.
- [x] `app/kql.py`: retires the `symbol_catalogue`/`symbol_count` KQL reads;
      both now delegate to `app.meta` with a deprecation warning.
- [x] `app/main.py`: `/api/health` publishes `meta_connected` and `meta_error`.
- [x] `run.py`: startup banner prints the Postgres metadata location.
- [x] `requirements.txt`: adds `psycopg2-binary>=2.9`.
- [x] `.env.example` / `README.md`: document the Postgres metadata block and the
      `symbols_metadata` schema; retire `symbols_icmarkets` references.

### Tests
- [x] `tests/test_status.py`: probes `app.meta` instead of `kql.symbol_count`;
      metadata reachability reported independently of KQL.
- [x] `tests/test_config.py`: `symbol_table`/`pg_symbols_table` default to
      `symbols_metadata`.
- [x] `python -m pytest tests -q` → 424 passed (incl. 2 new composite-key
      catalogue/lookup tests); the 4 `test_analytics_api` failures are
      pre-existing (need the VM's managed identity for live KQL).

## 2026-10-17 — Dashboard login gate

Scope: one username/password now stands between a visitor and the dashboard
(and its data API), opt-in via `LOGIN_PASSWORD`. Separate from the Entra
identity, which only authenticates the data plane.

### Backend
- [x] `app/config.py`: `LOGIN_USERNAME`/`LOGIN_PASSWORD`/`LOGIN_SESSION_HOURS`/
      `LOGIN_COOKIE_SECURE` plus `Settings.login_enabled` — the gate is the
      password alone; blank leaves the site open (local runs, the test suite).
- [x] `app/auth.py`: constant-time credential check (both halves always
      compared, 250 ms delay on failure, one generic 401 — no enumeration),
      in-memory bearer-token sessions (HttpOnly cookie, single-worker process;
      pruned on mint/check) and the public-path list (login flow, `/api/health`,
      `/welcome`, `/unavailable`, `/docs`, `/static/*`, `/` during maintenance).
- [x] `app/main.py`: one gate middleware — pages 307 to `/login`, `/api/*`
      answers 401; new `GET /login`, `POST /api/login`, `POST /api/logout`;
      `/api/health` publishes `login_enabled`.
- [x] `run.py`: prints `Login: enabled (user=…)` or `Login: OFF …` at startup.

### Frontend
- [x] `app/static/login.html`: self-contained branded sign-in page
      (landing.html conventions: inline style, fetch to `/api/login`).
- [x] `app/static/index.html`: **Sign out** button in the header, hidden by
      default.
- [x] `app/static/app.js`: button follows health's `login_enabled`; logout
      posts `/api/logout` and lands on `/login`; `api()` bounces any 401 to
      `/login`, so an expired session costs one redirect, not a page of
      failed panels.

### Config / docs
- [x] `.env` (git-ignored): LOGIN_* block with a generated password, active
      for local runs.
- [x] `.env.example`: LOGIN_* block documented.
- [x] `README.md`: "Dashboard login" section, API rows for `/login` and the
      two POSTs, the `/` row amended, testing coverage and three
      troubleshooting rows.

### Tests
- [x] `tests/conftest.py`: autouse fixture forcing the gate off suite-wide
      (the real `.env` now carries a password) and clearing live sessions;
      binds `cache_clear` so `test_refresh_script`'s stubbed `get_settings`
      cannot break the teardown.
- [x] `tests/test_login.py`: 31 tests — the page (brand, self-contained, the
      form), gate off (open dashboard, 403 sign-in), gate on (307 page
      redirects, 401 API answers, forged cookie), sign-in (wrong pair,
      no enumeration, HttpOnly cookie, dashboard + API + `/login` redirect),
      sign-out, what stays public (health, explainer, outage, docs, static,
      maintenance root), session expiry/revocation, the sign-out control pins.

### Verification
- [x] `python -m pytest tests -q` (clean env) → 418 passed; the 4 failures
      are pre-existing (`test_analytics_api` needs the VM's managed identity
      for live KQL — identical on a pristine baseline clone).
- [x] `node --check app/static/app.js` clean

---

## 2026-10-14 — Microstructure/liquidity grid reflow: OFI and volatility widened

Scope: swap two cards' grid spans so each block's rows fill the 3-column grid.
The order-flow panel carries a signed-volume series plus its normalised
imbalance, and the volatility panel reads better as a wide line, while the
book-depth imbalance series is a single bar set that fits one column.

### Frontend
- [x] `app/static/index.html`: `ofi` card now carries `span2` (two thirds of the
      row); the `depth` card drops to a single column. The *Microstructure* block
      reads `ofi(span2) | depth` then `acf | large(span2)` — both rows full.
- [x] `app/static/index.html`: `vol` card now carries `span2`, so the *Liquidity*
      block's last row is `hour | vol(span2)` instead of `hour | vol` with an
      empty third column.

### Verification
- [x] `python3 -m pytest tests/test_maintenance.py tests/test_landing.py` → 28 passed

---

## 2026-10-14 — Microstructure grid reflow: depth widened, autocorrelation dropped a row

Scope: the *Microstructure* block was `ofi | depth | acf` on one row with the
`large` card (span2) alone on the next, leaving an empty column beside it. The
book-depth panel carries two stacked series (resting size per side plus the
imbalance ratio), so it was the cramped one.

### Frontend
- [x] `app/static/index.html`: the `depth` card now carries `span2` (two thirds of
      the row), matching the `chg` and `large` cards.
- [x] `app/static/index.html`: the `acf` card moved after `large`, so the block
      reads `ofi | depth(span2)` then `acf | large(span2)` — both rows full.

### Verification
- [x] `python3 -m pytest tests/test_maintenance.py tests/test_landing.py` → 28 passed

---

## 2026-10-14 - "Last update" floored to the one-minute grid, not the 5-minute one

Scope: the refresh cycle runs every `CACHE_REFRESH_MINUTES` (now 1) on the
clock, but the header **Last update** badge and the freshness-strip **Cache
updated** chip floored their timestamps to a **5-minute** boundary
(`fmt.fivemin`). A tick at 14:47:12 therefore read as **14:45** — up to ~5 min
"stale" even though the window is rewritten every minute. The KPI cards
themselves were never wrong: they read `agg_dom` via `/api/analytics` (the
*Last* tile is `summary.close`), so this was purely a display-grid bug.

### Frontend
- [x] `app/static/app.js`: `fmt.fivemin` (5-minute floor) replaced by `fmt.minute`
      (floor to the minute — seconds dropped, still never rounded up). Both the
      header **Last update** badge and the **Cache updated** chip now use it, so
      they land on the same one-minute grid the refresh cycle writes on.
- [x] `app/static/app.js`: docstrings/comments that still said "30-minute" /
      "5-minute" (leftovers from older cadences) corrected to the one-minute grid.

### Docs
- [x] `README.md`: systemd timer example corrected to `OnCalendar=*:*:15`
      (was `*:0/5:15`), "at most a minute behind the feed" (was "a few minutes"),
      and the test-coverage line now says one-minute slots (was 5-minute).
- [x] `docs/metrics_5m.md`: the *Refresh* row and the read-path prose corrected
      to the one-minute cadence (were `CACHE_REFRESH_MINUTES=30` / "5-minute slots").
- [x] `app/service.py`: the window-slide docstring now describes a one-minute
      cycle / earliest minute (was 30-minute / earliest half hour).

### Tests
- [x] `tests/test_maintenance.py`: `test_the_header_last_update_is_floored_to_the_5_minute_grid`
      renamed to `..._to_the_minute_grid` and re-pinned on `fmt.minute(feed)`.

### Verification
- [x] `node --check app/static/app.js` clean
- [x] `pytest tests/test_maintenance.py` - 20 passed
- [x] No `fivemin` / stale "5-minute"/"30-minute" refresh-cadence references remain
      in `README.md`, `docs/`, `app/` or `scripts/`.

---

---

## 2026-10-13 - `/api/metrics` serves only the volume-at-price ladder; scalar columns dropped

Scope: `metrics_5m` ships seven scalar window metrics (`ticks`, `vwap`,
`total_volume`, `realized_vol_bps`, `avg_spread_bps`, `ofi_imbalance`,
`change_pct`) plus the volume-at-price ladder, but the dashboard only ever
overlaid the ladder — every headline tile is rendered from `/api/analytics`, one
tick frame. The scalars were dead weight in the Redis grid and in the response,
so the read path now projects only the nested `vap_*`/`poc_*` columns and
`/api/metrics` no longer reports the scalar `windows`/`summary` keys at all.

### Backend
- [x] `app/kql.py`: `WINDOW_METRICS` and `WINDOW_DERIVED` removed; `window_columns()`
      is now just `WINDOW_NESTED` (`vap`/`poc`). `_METRICS_PROJECTION` narrows the
      grid read accordingly, so a renamed/missing column fails loudly rather than
      reading back as null.
- [x] `app/metrics.py`: `SCALAR_FIELDS`, `_VOL_ANNUALISATION` and `window_payload()`
      removed. `report()` drops the `windows`/`summary` keys and serves only
      `volume_profiles` (all four windows), `selected_window_minutes`,
      `requested_window_minutes`, `window_choices_minutes`, `value_area_share`,
      `stops` and `meta`.
- [x] `app/main.py`: `/api/metrics` docstring updated to the ladder-only contract.

### Docs
- [x] `docs/metrics_5m.md`: contract note ("the dashboard reads only the ladder"),
      the `/api/metrics` consumption step, and the *One frame per strip* section
      updated; the KQL producer reference is unchanged (it still builds the
      scalars — they are just no longer read here).
- [x] `docs/dashboard_metrics.md`: the `metrics_5m` section now documents only the
      ladder columns and marks the scalars "built by the KQL, not read by the app".
- [x] `README.md`: endpoint table, the `/api/metrics` prose, and the analytics
      reference updated.

### Tests
- [x] `tests/test_metrics.py`: column-contract test pins the nested-only projection;
      the four `window_payload` tests removed; the report-bundle tests assert the
      ladder-only payload (and that `windows`/`summary` are gone).
- [x] `tests/test_metrics_api.py`: the switching-is-free test asserts on
      `volume_profiles` and the absence of `windows`/`summary`.
- [x] `tests/test_kql.py`: the `fetch_metrics` projection assertion updated.

### Verification
- [x] `pytest` - 382 passed; the 9 failures are pre-existing and environmental
      (`.env` credentials, refresh-interval defaults), identical on the clean
      baseline. The delta from baseline is exactly the four removed tests.
- [x] `node --check app/static/app.js` clean.

---
---

## 2026-10-12 - Drawdown card replaced by price change from a reference point

Scope: the *Drawdown* card read the `mid` column and divided it by its own
running peak (`mid.cummax()`), so every reading was forced to `<= 0` and the
whole panel described loss from a high. It now reports a neutral percent change
from one fixed reference - the first tick of the window.

### Backend
- [x] `app/analytics.py`: `drawdown()` replaced by `price_change()`. The
      reference is `mid.iloc[0]` for the whole window, so the path starts at 0%
      and runs either side of it; the baseline never moves, which makes two
      windows comparable. Payload: `series[].pct`, `reference_ts`,
      `reference_mid`, `change_pct`, `min_pct`/`max_pct` with the timestamp of
      each extreme.
- [x] A zero/missing opening mid yields `None`s instead of inf, so
      `json.dumps(..., allow_nan=False)` still holds on a degenerate frame.
- [x] `build_report` emits `"price_change"`; `_dd_episode()` (peak/trough/
      recovery) deleted with the metric it served.
- [x] `price_change.change_pct` is asserted equal to `summary.change_pct` - the
      same two numbers, derived from the same `mid` column.

### Frontend
- [x] `app/static/index.html`: card `dd` -> `chg`, canvas `cDD` -> `cChg`, note
      `dd-note` -> `chg-note`, title "Drawdown" -> "Price change vs. window start".
- [x] `app/static/app.js`: the line chart now fills to the origin (`fill:
      {target: "origin"}`) rather than down to a hard `y.max: 0`, and uses the
      neutral accent blue instead of the loss-red; the note reads
      "Change since window start +x.xx% * reference <time>".
- [x] `DETAILS.chg` replaces `DETAILS.dd`: the prose states the fixed-baseline
      rule, and the rows are reference price/time, change since reference, and
      the window's high/low with their timestamps. No episode timeline.

### Docs & tests
- [x] `tests/test_analytics.py`: `test_drawdown_is_non_positive` replaced by
      `test_price_change_is_anchored_on_the_window_start` (first point 0%, last
      equals the reported change, values can go below 0), plus an empty-frame
      case and a zero-reference case that pins the no-NaN guard.
- [x] `README.md`, `docs/metrics_5m.md`, `landing.html`, `unavailable.html` and
      the two `styles.css` comments reworded off drawdown; `tests/test_landing.py`
      now expects "price change" in the explainer.

### Verification
- [x] `pytest` - 394 passed
- [x] `node --check app/static/app.js` clean
- [x] Live 3000-tick run: `change_pct -0.1730` matches `summary.change_pct`,
      `reference_mid 1.08500002`, `series[0].pct == 0.0`, payload strict-JSON safe

**Notes:**
- The card keeps `span2`. The episode timeline that originally justified the
  wide cell is gone, but the reference row (price beside a timestamp) and the
  high/low rows still read better across two thirds of the row.

---

## 2026-10-11 - POC marks the busiest price bin, not whatever `poc_*` holds (dev)

Scope: the volume-profile panel marked a bar that was not the tallest one, and
the *Point of control* row showed a value that was not the busiest price. Two
argmaxes over the same bins were allowed to disagree: `metrics_5m`'s `poc_*`
column and the ladder the chart actually draws.

### Backend
- [x] `app/metrics.py`: `volume_profile` takes the POC from the **busiest bin of
      the materialised ladder** (`argmax(volume)`, returning that bin's price)
      instead of trusting the stored `poc_*` as a price. The stored point is now
      only the fallback for a ladder where no bin traded (total volume 0) - a
      value that is not the busiest bin's price can no longer be marked up as it.
      The value area still runs off the same index through `analytics.value_area`.

### Docs
- [x] `docs/metrics_5m.md` Stage 4: `poc = arg_max(vol, center)` - the criterion
      comes first, so the old `arg_max(center, vol)` was returning the bin's
      **volume** (max price -> its volume), i.e. precisely the "POC is not the
      busiest price bin" symptom. Commented in the KQL so the order is not
      "tidied" back.
- [x] `docs/metrics_5m.md` Stage 3: the same argument-order slip in `open`/`close`
      - `arg_min(mid, timestamp)` / `arg_max(mid, timestamp)` return a *datetime*
      in a `real` column; corrected to `arg_min(timestamp, mid)` /
      `arg_max(timestamp, mid)` and the definition table with them.
- [x] Stage 4 prose and the *Design notes* bullet now state that `poc_*` is the
      centre price of the busiest bin and that the read path re-derives it from
      the ladder, so the two can never disagree about the marked bar.

### Tests
- [x] `tests/test_metrics.py`: `test_the_table_s_own_poc_is_used_and_falls_back_to_the_busiest_bin`
      replaced by `test_the_poc_is_the_busiest_bin_whatever_the_stored_column_says`
      - a stored point naming the busiest bin, a different bin, a *volume* (the
      swapped-`arg_max` output) and `None` all resolve to the busiest price, and
      that price is asserted to be the bin with the largest volume.
- [x] `tests/test_metrics.py`: `test_a_ladder_that_traded_nothing_falls_back_to_the_stored_point`
      pins the one case where the stored point is still what gets marked.

### Verification
- [x] `pytest` - 386 passed
- [x] End-to-end through `metrics.report` with a stored `poc_1h = 20.0` (a volume)
      on a ladder whose busiest bin is `1.0855 vol 20`: the payload reports
      `poc = 1.0855`, the marked bar is the tallest, bins still run
      price-descending.
- [x] `node --check app/static/app.js` clean (UI untouched)

**Notes:**
- The UI marks the POC bar with `b.price === volume_profile.poc`, so the reader
  must hand back exactly a bin price - it does, because the value comes straight
  from the ladder bin rather than from the stored column.
- The `metrics_5m` table still needs rebuilding for `poc_*` (and `open_*`/
  `close_*`) to be correct in the table itself; the read path no longer depends
  on it for the chart marker.

---


## 2026-10-11 - Fixed 4-window control aligned to `metrics_5m` (dev)

Scope: reduce the aggregate *Window* control from the 5–240 min slider to the
four fixed trailing windows the precomputed `metrics_5m` KQL table ships
(1 h / 2 h / 3 h / 4 h), keeping `/api/health` as the single source of the
offered set. The pandas read path is unchanged — the `metrics_5m` table is
still being built, so the app keeps recomputing in memory; only the *control*
and its contract move now.

### Backend
- [x] `app/config.py`: `WINDOW_MIN_MINUTES`/`WINDOW_MAX_MINUTES` (5/240) are
      replaced as the driver of the control by `WINDOW_CHOICES_MINUTES`
      (`60,120,180,240`), parsed/de-duped/sorted by a new `_env_int_list`; a
      `window_choices` property caps the choices at the cache and never returns
      empty. The legacy min/max stay (now 60/240) so an older cached health
      payload still renders a sensible control.
- [x] `app/service.py`: `Status.window_choices_minutes` echoes `window_choices`
- [x] `app/main.py`: `/api/health` publishes `window_choices_minutes`

### UI
- [x] `app/static/app.js`: `fillWindows()` builds `#windowMinutes` from
      `window_choices_minutes` (four fixed options), falling back to the legacy
      min..max slider only when the field is absent; defaults to 1 h
- [x] `app/static/index.html`: *Window* label/title now "1, 2, 3 or 4 hours"

### Docs & tests
- [x] `.env.example`, `README.md`, this file updated
- [x] `tests/test_config.py`: window choices default/override/normalise,
      cache-capping and never-empty; isolation set covers the new env keys
- [x] `tests/test_status.py`, `tests/test_analytics_api.py`: health echoes
      `window_choices_minutes == [60,120,180,240]`

### Verification
- [x] `pytest` - 327 passed; `node --check app/static/app.js` clean

**Notes:**
- The `metrics_5m` table (per `docs/metrics_5m.md`) is the source-of-truth for
  the four windows; the control now offers exactly what it precomputes.
- When the table lands, the read path (`app/refresh.py` / `app/service.py`)
  flips from "cache raw ticks, recompute" to "cache the finished metrics, look
  up" — that is a separate change and needs the table first.

---

## Unreleased (dev)
- **Open:** Calendar is a read-only view of report data; a true task list
  (add/edit, persisted) is undecided — needs a storage choice (localStorage vs
  server endpoint + store).

---

## 2026-10-09 — Point-in-time field, aggregate window, 8 h cache, descending ladder
- Backend: cache lookback default 4 → 8 h; new `TIMELINE_*`/`WINDOW_*` config and a
  `max_window_minutes` ceiling capped at the cache.
- Renamed `clamp_lookback` → `clamp_window`; loads take an `end_time` that ends the
  slice at the chosen point (future clamps to newest, past leaves nothing, bad input
  raises). Empty-window errors name the point.
- API: `/api/analytics` takes `window_minutes` (`lookback_minutes` kept as deprecated
  alias) and `as_of`, both echoed in `meta`; `/api/health` publishes the window range.
- Volume-profile bins and nodes now returned in descending price order (POC/value-area
  still computed ascending).
- UI: header badge → "Last update" (feed-based); cadence strip mirrors the 30-min
  refresh; "Next refresh" snaps to the wall-clock grid; point-in-time control is a
  `<input type="time">` on the 5-min grid; "Lookback" relabelled "Window" built from
  `/api/health`; volume-at-price runs highest-price-first.
- Tests: `pytest` 314 passed.

---

## 2026-10-08 — Clock-slot refresh, sliding 4-hour window, development page
- Refresh cycle sleeps to the next wall-clock mark (:00/:30) instead of a fixed
  interval, so restarts don't drift and `*/30` cron aligns; exact landings wait a
  full interval.
- `refresh_cache()` slides the window per symbol (read rows newer than the cached
  newest, merge/dedupe, purge older than the lookback anchored on the newest row);
  summary gains `purged`.
- New self-contained `/unavailable` "under development" page served at `/`
  (dashboard moved to `/dashboard`; `MAINTENANCE_MODE` still 503s `maintenance.html`).
- Tests: `pytest` 285 passed; new `tests/test_maintenance.py` plus script-sanity
  checks guarding `app.js` helper/function renames.

---

## 2026-10-08 — Keep the 4-hour window fresh: refresh every 30 minutes
- Added `CACHE_REFRESH_MINUTES` (30; 0 disables) and an in-process worker-thread cycle
  that refreshes only cached symbols (+ default) and survives failed cycles.
- `cache.keys()` lists cached entries via `KEYS` (this Redis Enterprise returns empty
  pages for `SCAN`); `scripts/refresh_cache.py` offers a cron/systemd alternative.
- `/api/health` publishes `cache_refresh_minutes`, `cache_refreshed_at`,
  `cached_symbols`; UI tooltip and banner show the cadence.
- Tests: `pytest` 248 passed; new `tests/test_refresh.py`, `test_refresh_script.py`.

---

## 2026-10-08 — One source (KQL) + Redis cache, fixed 4-hour window
- Redis cache (Entra identity, no access key) with catalogue + per-symbol Parquet
  blobs; cache is an optimisation — an outage falls through to KQL.
- Removed the SQL read path: deleted `app/db.py`/`app/auth.py`, dropped `pyodbc`,
  added `redis` + `pyarrow`; `DataSourceError` moved to `app/errors.py`.
- Service reads KQL and serves from Redis under a clamped fixed window; `/api/health`
  publishes `lookback_minutes`, `cache_connected`, `cache_error`.
- UI: "Lookback" number input replaced by a "Duration" `<select>` built from
  `health.lookback_minutes`.
- Tests: `pytest` 222 passed.

---

## 2026-10-08 — Aggregate rows move to Fabric KQL (Eventhouse)
- New `app/kql.py` (Kusto client, declarative-parameter queries, `fetch_ticks` with
  anchored lookback); auth mirrors SQL (managed identity → service principal → `az login`).
- Config split into `kql_host`/`kql_database`/`kql_table` (defaults `ctrader_dom`/`agg_dom`);
  both endpoints now required for `is_configured`; `DEFAULT_SYMBOL` and UI ticker → `XAUUSD`.
- `TICK_COLUMNS` moved into `app/frames.py`; `db.py` trimmed to the SQL instrument dimension.
- Tests: `pytest` 191 passed; verified live (XAUUSD id 41, 16.8k ticks/hour). Casts
  `symbolId == tolong(sym)` since it's a `long`.

---

## 2026-10-08 — Terminology: per-tick metrics, not snapshots
- Corrected `agg_dom` naming to derived per-tick order-book metrics (from
  `dom_stream_raw` → `dom_book_flat`); `from_snapshot` → `from_ticks`,
  `SNAPSHOT_COLUMNS` → `METRIC_COLUMNS`, `latest_snapshot` → `latest_tick`.
- README, landing/dashboard copy, `.env.example` and tests updated; all green.

---

## 2026-10-05 — Dashboard cards, calendar, symbolId
- All 15 cards are click/tap/keyboard disclosures with a detail body and rotating
  chevron; grouped into Overview / Microstructure / Liquidity / Data.
- Month calendar built from the OHLCV bars (day sized by tick count, coloured by
  change; prev/next/Today; defaults to last day with data).
- Removed `symbolId` from the UI; wider type scale, 420px min cards, footnotes so no
  chart needs expanding to read its headline.
- Tests: `pytest` 133 passed; `node --check` clean.

---

## 2026-10-05 — Favicon
- Added `favicon.svg` plus a full raster set (`.ico`, `icon-32/180/512.png`) with link
  and `theme-color` tags in `index.html`.
- New dependency-free `scripts/make_favicon.py`; fixed the alpha divisor (was zeroing
  alpha at 512px) and made output byte-reproducible.

---

## 2026-10-05 — Drawdown card overflow
- `dd` card now spans a full grid row; added `.card.span3` with responsive collapse
  (3 → 2 at 1400px → 1 at 900px).
- Removed `white-space:nowrap` on `.dl dd` and pinned `.dl dt` so wide values wrap
  inside their own cell; raised `.dl` min column 230 → 300px.

---

## 2026-10-05 — Overview block re-layout
- Price card takes the full row; `dd` at 2/3, `hist` at 1/3 below it; markup reordered.
- Fixed the `span3` breakpoint to 1339px (was 1400px) to match the 3-column minimum.

---

## 2026-10-05 — Data freshness
- Added `meta.latest_tick` to `/api/analytics`; freshness strip (cadence, latest tick,
  next refresh, row count) and a header age badge that goes amber past the cadence.
- `REFRESH_MINUTES = 45` drives the strip; fixed the "every hour" wording; `set()` no
  longer clobbers `className`.
