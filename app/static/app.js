/* Axioma Analytics dashboard */
"use strict";

const $ = (id) => document.getElementById(id);
const charts = {};

/** Escape text before interpolating into innerHTML (table/column names are data). */
function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

const fmt = {
  n: (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v)) ? "\u2013" : Number(v).toFixed(d)),
  int: (v) => (v === null || v === undefined || Number.isNaN(Number(v))
    ? "\u2013" : Math.round(Number(v)).toLocaleString()),
  pct: (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v))
    ? "\u2013" : `${Number(v) >= 0 ? "+" : ""}${Number(v).toFixed(d)}%`),
  px: (v) => {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "\u2013";
    const n = Number(v);
    return Math.abs(n) >= 100 ? n.toFixed(2) : Math.abs(n) >= 1 ? n.toFixed(4) : n.toFixed(5);
  },
  time: (iso) => {
    if (!iso) return "\u2013";
    const d = new Date(iso);
    // toISOString() throws RangeError on an unparseable date.
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toISOString().replace("T", " ").slice(5, 19);
  },
  /** The update time on the 30-minute refresh grid: 14:47:12 -> 14:30:00.
   * Floored, never rounded up, so "Last update" cannot sit in the future. */
  halfhour: (iso) => {
    if (!iso) return "\u2013";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    d.setUTCMinutes(d.getUTCMinutes() < 30 ? 0 : 30, 0, 0);
    return d.toISOString().replace("T", " ").slice(0, 16);
  },
  /** Full timestamp with millis: "10-06 22:09:14.747". Tick metrics can
   * repeat the same quote many times a second, so second precision made every
   * row in Latest ticks look identical even though the instants differed. */
  timems: (iso) => {
    if (!iso) return "\u2013";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toISOString().replace("T", " ").slice(5, 23);
  },
  num: (v) => {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "\u2013";
    return Number(v).toLocaleString(undefined, { maximumFractionDigits: 6 });
  },
};

Chart.defaults.color = "#8798b4";
Chart.defaults.borderColor = "rgba(35,45,66,.7)";
Chart.defaults.font.size = 11;
Chart.defaults.plugins.legend.display = false;

function baseOpts(extra = {}) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: { tooltip: { backgroundColor: "#0b0f17", borderColor: "#232d42", borderWidth: 1, padding: 9 } },
    scales: {
      x: { grid: { display: false }, ticks: { maxTicksLimit: 7 } },
      y: { grid: { color: "rgba(35,45,66,.55)" }, ticks: { maxTicksLimit: 6 } },
    },
    ...extra,
  };
}

function draw(id, cfg) {
  if (charts[id]) charts[id].destroy();
  const el = $(id);
  if (!el) return;
  charts[id] = new Chart(el.getContext("2d"), cfg);
}

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
}

function setLoading(on) { $("loading").classList.toggle("hidden", !on); }

/**
 * Read a control's value, falling back when the element is absent.
 *
 * The page is assembled from two separately cached files (index.html and
 * app.js). If a browser serves a newer app.js against an older cached HTML, a
 * control this build expects simply does not exist, and reading `.value` off
 * null threw "Cannot read properties of null (reading 'value')" -- reported as
 * a connection problem even though the API was fine. Defaults keep the
 * dashboard working on whatever controls the served HTML does have.
 */
function val(id, fallback = "") {
  const el = $(id);
  return el ? el.value : fallback;
}

function hideBanner() {
  const el = $("banner");
  el.className = "banner";   // reset any state classes (and clear inline border colour)
  el.innerHTML = "";
  el.classList.add("hidden");
}

function banner(messages, kind = "warn") {
  const el = $("banner");
  if (!messages || !messages.length) { hideBanner(); return; }
  // Use a class, not an inline style: an inline border set by bannerErr() would
  // otherwise persist and colour every later "Heads up" banner red.
  el.className = `banner ${kind === "bad" ? "bad" : ""}`;
  el.innerHTML = `<b>${kind === "bad" ? "Connection problem" : "Heads up"}</b>` +
    (kind === "bad" ? "" : `<p>Snapshots refresh every ${REFRESH_MINUTES} minutes, so results may lag the live market.</p>`) +
    "<ul>" + messages.map((m) => `<li>${esc(m)}</li>`).join("") + "</ul>";
  el.classList.remove("hidden");
}

function bannerErr(text) {
  banner([text], "bad");
}

/* ---------------- health + instrument coverage ---------------- */

async function loadHealth() {
  const h = await api("/api/health?refresh=true");
  // Kept for the expanded Instrument-coverage card, which reads feed totals
  // that /api/analytics does not return.
  window.__health = h;
  const dot = $("connDot");
  if (h.connected) {
    dot.className = "dot ok";
    $("connText").textContent = h.has_data ? "Connected" : "Connected \u00b7 no data";
  } else {
    dot.className = "dot bad";
    $("connText").textContent = "Offline";
  }
  // Hints are useful even when connected (empty table, or instruments the
  // symbol dimension does not cover), so surface them in both states. A cache
  // outage arrives as one of those hints: reads still work, they just cost a
  // KQL query, which is a warning rather than a failure.
  if (h.hints && h.hints.length) banner(h.hints, h.connected ? "warn" : "bad");
  // The lookback control's ceiling, and the cache state, on the badge tooltip -
  // both are context for the numbers rather than headline problems.
  $("connDot").title = h.cache_connected === false
    ? (h.cache_error || "The Redis cache is unreachable.")
    : `Window ${Math.round((h.lookback_minutes || 0) / 60 * 10) / 10} h, cached in Redis`
      + (h.cache_refresh_minutes ? `, refreshed every ${h.cache_refresh_minutes} min` : "");
  renderCoverage(h);
  // The timeline is anchored on the newest row the feed has, so a symbol switch
  // (which can change that row) re-anchors it; `buildTimeline` keeps whatever
  // point the user picked rather than sliding it.
  buildTimeline(h.latest_tick || undefined);
  renderFreshness();
  return h;
}

/** Asset-class mix: one row per class, sized by instrument count. */
function renderCoverage(h) {
  const box = $("classes");
  const tag = $("coverage-tag");
  if (!box) return;
  if (!h || !h.connected || !h.has_data) {
    if (tag) tag.textContent = "";
    box.innerHTML = `<p class="note">No instrument coverage available.</p>`;
    return;
  }
  if (tag) {
    tag.textContent = `${h.symbol_count.toLocaleString()} traded \u00b7 ${h.classified_count.toLocaleString()} classified`;
  }
  const rows = window.__classSummary;
  if (!rows || !rows.length) {
    box.innerHTML = `<p class="note">Loading asset classes\u2026</p>`;
    return;
  }
  const max = Math.max(...rows.map((r) => r.count), 1);
  box.innerHTML = rows.map((r) => `
    <div class="classrow">
      <span class="cname">${esc(r.label)}</span>
      <span class="cbar"><i style="width:${((r.count / max) * 100).toFixed(1)}%"></i></span>
      <span class="ccount">${r.count.toLocaleString()}</span>
    </div>`).join("");
}

/** Populate the asset-class dropdown from the catalogue groups. */
function fillAssetClasses(groups) {
  const sel = $("assetClass");
  if (!sel) return;
  const prev = sel.value;
  const total = groups.reduce((n, g) => n + g.count, 0);
  sel.innerHTML = `<option value="">All asset classes (${total.toLocaleString()})</option>` +
    groups.map((g) => `<option value="${esc(g.key)}">${esc(g.label)} (${g.count.toLocaleString()})</option>`).join("");
  // Keep the current pick when it still exists, otherwise show everything.
  sel.value = groups.some((g) => g.key === prev) ? prev : "";
}

async function loadClassSummary() {
  const data = await api("/api/asset-classes");
  window.__classSummary = data.summary || [];
}

/** Load the symbol list, narrowed to the selected asset class. */
async function loadSymbols() {
  const sel = $("assetClass");
  const ac = sel && sel.value;
  const q = ac ? `?asset_class=${encodeURIComponent(ac)}` : "";
  const { groups, symbols, summary, default_symbol } = await api(`/api/symbols${q}`);
  window.__defaultSymbol = default_symbol || null;
  // `groups` is already narrowed to the active asset class, so rebuilding the
  // class dropdown from it would collapse the list to a single option and
  // leave the user stuck. `summary` is the full per-class rollup and is what
  // the picker needs.
  fillAssetClasses(summary || []);
  fillSymbols(symbols);
}

function fillSymbols(symbols) {
  const sel = $("symbol");
  if (!sel) return;
  const prev = sel.value;

  if (!symbols.length) {
    sel.innerHTML = '<option value="">none available</option>';
    return;
  }
  // Always pick one real symbol. Averaging across symbols would mix
  // incomparable price scales and produce meaningless statistics.
  // Show the readable ticker and asset class. The raw symbolId stays the option
  // value (the API needs it) but is kept out of the visible label.
  // Every instrument is selectable; the default (XAUUSD) is simply sorted first
  // and the broker description sits alongside the ticker and asset class.
  sel.innerHTML = symbols.map((s) => {
    const id = s.symbol ?? "";
    const label = s.name || id;
    const cls = s.asset_class_label || "Unclassified";
    const desc = (s.description || "").trim();
    const text = desc ? `${label} \u00b7 ${cls} \u2014 ${desc}` : `${label} \u00b7 ${cls}`;
    return `<option value="${esc(id)}">${esc(text)}</option>`;
  }).join("");
  const stillThere = symbols.some((s) => (s.symbol ?? "") === prev);
  // Default to XAUUSD (the pinned default from /api/symbols) on first load, so
  // the dashboard opens on gold rather than the most-traded instrument.
  const pinned = window.__defaultSymbol
    && symbols.some((s) => (s.symbol ?? "") === window.__defaultSymbol)
    ? window.__defaultSymbol : null;
  const byName = symbols.find((s) => (s.name || "").toUpperCase() === "XAUUSD");
  const fallback = pinned || (byName && byName.symbol) || (symbols[0].symbol ?? "");
  sel.value = stillThere ? prev : fallback;
}

async function fillTimeframes(h) {
  const health = h || (await api("/api/health"));
  const sel = $("timeframe");
  if (!sel) return;
  sel.innerHTML = health.timeframes
    .map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join("");
  sel.value = "1m";
}

/**
 * The point-in-time timeline the user picks a moment from: a four-hour span
 * (`timeline_minutes`) in five-minute steps (`timeline_step_minutes`).
 *
 * Every stop sits on the clock grid (:00, :05, ...) and the newest stop is
 * never behind the feed - it is the next grid mark at or after the newest data.
 * That is what keeps the oldest stop with the widest lookback inside the cached
 * window: span (4 h) + lookback (4 h) = the 8 hours Redis holds, exactly. The
 * chosen point is kept as an absolute instant, so refilling the controls or
 * newer data arriving does not silently move what the user selected.
 */
const timeline = { span: 240, step: 5, earliest: null, latest: null };
window.__asOf = null;        // chosen point in time, epoch ms
window.__asOfLatest = true;  // still the newest stop, so it follows the feed

/** "45 min" / "2 h" - a duration in minutes, readable. */
function durationLabel(m) {
  if (m < 60) return `${m} min`;
  const hr = m / 60;
  return `${Number.isInteger(hr) ? hr : hr.toFixed(1)} h`;
}

/** Rebuild the timeline around `fromIso`, keeping the chosen point if it fits. */
function buildTimeline(fromIso) {
  const h = window.__health || {};
  const step = Math.max(1, Number(h.timeline_step_minutes) || timeline.step);
  const span = Math.max(step, Number(h.timeline_minutes) || timeline.span);
  const stepMs = step * 60000;
  const base = new Date(fromIso || Date.now()).getTime();
  if (Number.isNaN(base)) return;
  // Ceil to the next mark: stops on the grid, newest stop not behind the data.
  const anchor = Math.ceil(base / stepMs) * stepMs;
  timeline.step = step;
  timeline.span = span;
  timeline.latest = anchor;
  timeline.earliest = anchor - span * 60000;

  let point = window.__asOf;
  // Nothing chosen yet, or the user was on the newest stop: follow the feed.
  if (point === null || window.__asOfLatest) point = anchor;
  point = Math.min(timeline.latest, Math.max(timeline.earliest, point));
  point = timeline.earliest + Math.round((point - timeline.earliest) / stepMs) * stepMs;
  window.__asOf = point;
  window.__asOfLatest = point === timeline.latest;

  const slider = $("asOf");
  if (slider) {
    slider.min = "0";
    slider.max = String(span);
    slider.step = String(step);
    slider.value = String(Math.round((point - timeline.earliest) / 60000));
  }
  renderAsOf();
}

/** The chosen point next to the slider: its time, and how far back that is. */
function renderAsOf() {
  const out = $("asOfOut");
  if (!out) return;
  const point = window.__asOf;
  if (point === null || timeline.latest === null) {
    out.textContent = "\u2013";
    return;
  }
  const back = Math.round((timeline.latest - point) / 60000);
  out.textContent = `${fmt.time(new Date(point).toISOString())} UTC`
    + (back > 0 ? ` \u00b7 ${durationLabel(back)} back` : " \u00b7 latest");
}

/** Read the slider back into the chosen point. */
function onAsOfInput() {
  if (timeline.earliest === null) return;
  const slider = $("asOf");
  if (!slider) return;
  window.__asOf = timeline.earliest + Number(slider.value) * 60000;
  window.__asOfLatest = window.__asOf >= timeline.latest;
  renderAsOf();
}

/** The chosen point as an ISO instant for `as_of`, or null before it exists. */
function asOfIso() {
  return window.__asOf === null ? null : new Date(window.__asOf).toISOString();
}

/**
 * Lookback choices at the chosen point, in minutes: every step from the
 * minimum up to the maximum the API publishes.
 *
 * Built from `/api/health` rather than a fixed list, so the control can never
 * offer more history than the cache holds - which is what keeps every choice a
 * free, in-memory narrowing instead of a second KQL read.
 */
function fillLookbacks(h) {
  const sel = $("lookback");
  if (!sel) return;
  const step = Math.max(1, Number(h && h.timeline_step_minutes) || 5);
  const min = Math.max(1, Number(h && h.lookback_min_minutes) || 5);
  const max = Math.max(min,
    Number(h && h.lookback_max_minutes) || Number(h && h.lookback_minutes) || 240);
  const choices = [];
  for (let m = min; m <= max; m += step) choices.push(m);
  if (choices[choices.length - 1] !== max) choices.push(max);
  const keep = Number(val("lookback", 60));
  sel.innerHTML = choices
    .map((m) => `<option value="${m}">${esc(durationLabel(m))}</option>`).join("");
  // Open on an hour where it is offered - enough ticks for the rolling metrics
  // to mean something - otherwise keep the selection or take the widest choice.
  sel.value = String(choices.includes(keep) ? keep
    : (choices.includes(60) ? 60 : max));
}

/* ---------------- KPI tiles ---------------- */

function kpi(label, value, hint, cls = "") {
  return `<div class="kpi"><div class="label">${label}</div>
    <div class="value ${cls}">${value}</div>
    ${hint ? `<div class="hint">${hint}</div>` : ""}</div>`;
}

function renderKpis(s, m) {
  const up = (s.change_pct ?? 0) >= 0;
  const tsign = m.trade_sign || {};
  const tiles = [
    kpi("Ticks", fmt.int(s.ticks), fmt.n(s.duration_seconds / 3600, 1) + " h window"),
    kpi("Last", fmt.px(s.close), `${fmt.time(s.start)} \u2192 ${fmt.time(s.end)}`, up ? "up" : "down"),
    kpi("Change", fmt.pct(s.change_pct), fmt.px(s.change), up ? "up" : "down"),
    kpi("High / Low", `${fmt.px(s.high)} / ${fmt.px(s.low)}`, "session extremes"),
    kpi("Realised vol", `${fmt.n(s.realized_vol_bps, 1)} bps`, `${fmt.n(s.annualised_vol_pct, 1)}% annualised`),
    kpi("Avg spread", `${fmt.n(s.avg_spread, 2)} bps`, `p95 ${fmt.n(s.p95_spread, 2)}`),
    kpi("Ticks / min", fmt.n(s.ticks_per_minute, 1), `gap ${fmt.n(s.avg_interarrival_ms, 1)} ms`),
    kpi("Order flow", fmt.pct((tsign.imbalance ?? 0) * 100, 1), "buy vs sell tick imbalance",
        (tsign.imbalance ?? 0) >= 0 ? "up" : "down"),
  ];
  if (s.total_volume) tiles.push(kpi("Volume", fmt.int(s.total_volume), `${fmt.int(s.large_trades)} large trades`));
  $("kpis").innerHTML = tiles.join("");
}

/* ---------------- data freshness ---------------- */

/** Ingest cadence for the aggregate DOM table, in minutes. */
const REFRESH_MINUTES = 45;

/** Full UTC stamp: "2026-10-05 11:46:10". */
function stamp(iso) {
  if (!iso) return "\u2013";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toISOString().replace("T", " ").slice(0, 19);
}

/** "4m 12s" / "2h 05m" - how long ago something happened, to the second. */
function age(ms) {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) return null;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, "0")}m`;
}

/** Wall-clock time the next ingest is expected, given the newest tick. */
function nextRefresh(lastIso) {
  const d = lastIso ? new Date(lastIso) : null;
  if (!d || Number.isNaN(d.getTime())) return null;
  return new Date(d.getTime() + REFRESH_MINUTES * 60_000);
}

/**
 * Publish when the data was last updated.
 *
 * The header badge reads the feed's newest row (`health.latest_tick`), which is
 * what "Last update" means: when the pipeline last wrote, whichever point in
 * time is on screen. It falls back to `meta.latest_tick` - the newest tick the
 * analytics query returned - when the probe has nothing. The time is floored to
 * the 30-minute refresh step, the grid the ingest cycle lands on, and never
 * rounded up so the badge cannot claim an update from the future. The
 * freshness strip below still describes the loaded window.
 */
function renderFreshness(report) {
  const h = window.__health || {};
  const meta = (report && report.meta) || {};
  // Two different "newest" times, and the difference matters: the header is
  // about the feed (when the pipeline last wrote, whatever point in time is on
  // screen), while the strip below describes the window that was loaded.
  const feed = h.latest_tick || meta.latest_tick || null;
  const windowLatest = meta.latest_tick || feed;
  const rows = meta.rows_analysed || 0;
  const ageMs = feed ? Date.now() - new Date(feed).getTime() : null;
  const ageText = age(ageMs);

  // Only touch className when a class is supplied: assigning unconditionally
  // would strip the styling classes the markup already carries.
  const set = (id, text, cls) => {
    const el = $(id);
    if (!el) return;
    el.textContent = text;
    if (cls) el.className = cls;
  };
  set("lastTick",
      feed ? `Last update ${fmt.halfhour(feed)} UTC` : "Last update unavailable");
  // tickAge always gets a class: it must lose a stale highlight once cleared.
  set("tickAge", ageText ? `${ageText} ago` : "",
      ageText ? (ageMs > REFRESH_MINUTES * 60_000 ? "tickage stale" : "tickage fresh") : "tickage");

  // The strip reports the loaded window; without a report there is nothing new
  // to say, so a health refresh leaves what the last analysis wrote alone.
  if (!report) return;
  const stampText = stamp(windowLatest);
  set("fbLastTick", stampText, ageMs !== null && ageMs > REFRESH_MINUTES * 60_000 ? "warn" : "ok");

  const next = nextRefresh(windowLatest);
  set("fbNext", next ? stamp(next.toISOString()) : "\u2013");
  set("fbRows", fmt.int(rows));
  const badge = $("tickAge");
  if (badge && next) {
    badge.title = `Data last updated ${fmt.halfhour(feed)} UTC. Next refresh expected ${stamp(next.toISOString())} UTC.`;
  }
}

/* ---------------- charts ---------------- */

function renderCharts(r) {
  const bars = r.ohlcv.bars || [];
  $("bars-tag").textContent = bars.length ? `${r.ohlcv.timeframe} \u00b7 ${bars.length} bars` : "no data";

  draw("cPrice", {
    type: "line",
    data: {
      labels: bars.map((b) => fmt.time(b.t)),
      datasets: [
        { label: "close", data: bars.map((b) => b.c), borderColor: "#4c9aff", borderWidth: 1.6,
          pointRadius: 0, tension: 0.15, yAxisID: "y" },
        { label: "volume", data: bars.map((b) => b.v), borderColor: "rgba(139,92,246,.55)",
          borderWidth: 1, pointRadius: 0, fill: true,
          backgroundColor: "rgba(139,92,246,.10)", yAxisID: "y1" },
      ],
    },
    options: baseOpts({
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } },
        y: { position: "left", grid: { color: "rgba(35,45,66,.55)" }, ticks: { maxTicksLimit: 6 } },
        y1: { position: "right", grid: { display: false },
              ticks: { maxTicksLimit: 5, callback: (v) => fmt.int(v) } },
      },
    }),
  });

  const ofi = r.microstructure.order_flow || [];
  const ts = r.microstructure.trade_sign || {};
  draw("cOFI", {
    type: "bar",
    data: {
      labels: ofi.map((p) => fmt.time(p.t)),
      datasets: [{ data: ofi.map((p) => p.imbalance ?? 0),
        backgroundColor: ofi.map((p) => (p.imbalance ?? 0) >= 0 ? "rgba(34,197,94,.6)" : "rgba(239,68,68,.6)") }],
    },
    options: baseOpts({ scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 5 } },
      y: { min: -1, max: 1, grid: { color: "rgba(35,45,66,.55)" } } } }),
  });
  $("ofi-note").textContent =
    `Buy ticks ${fmt.int(ts.buy_ticks)} \u00b7 Sell ticks ${fmt.int(ts.sell_ticks)}`;
  // Book depth panel: only present when the reader supplied resting-size columns.
  const dep = r.depth || { available: false };
  const dcard = $("depthCard");
  if (dcard) dcard.classList.toggle("hidden", !dep.available);
  if (dep.available) {
    draw("cDepth", {
      type: "bar",
      data: {
        labels: (dep.imbalance_series || []).map((p) => fmt.time(p.t)),
        datasets: [{ label: "depth imbalance", data: (dep.imbalance_series || []).map((p) => p.v ?? 0),
          backgroundColor: (dep.imbalance_series || []).map((p) => (p.v ?? 0) >= 0 ? "rgba(34,197,94,.6)" : "rgba(239,68,68,.6)") }],
      },
      options: baseOpts({ scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 5 } },
        y: { min: -1, max: 1, grid: { color: "rgba(35,45,66,.55)" } } } }),
    });
    $("depth-note").textContent =
      `Avg bid depth ${fmt.int(dep.avg_bid_depth)} \u00b7 ask ${fmt.int(dep.avg_ask_depth)} \u00b7 ` +
      `bid share ${fmt.pct((dep.avg_imbalance_share ?? 0) * 100, 1)}` +
      (dep.avg_levels_bid ? ` \u00b7 ${fmt.n(dep.avg_levels_bid, 1)} bid / ${fmt.n(dep.avg_levels_ask, 1)} ask levels` : "");
  }

  const prof = r.volume_profile.bins || [];
  draw("cProfile", {
    type: "bar",
    data: { labels: prof.map((b) => fmt.px(b.price)),
      datasets: [{ data: prof.map((b) => b.volume),
        backgroundColor: prof.map((b) => b.price === r.volume_profile.poc ? "#f59e0b" : "rgba(76,154,255,.65)") }] },
    options: baseOpts({ indexAxis: "y", plugins: { tooltip: { callbacks: { label: (c) => `vol ${fmt.int(c.parsed.x)}` } } },
      scales: { x: { grid: { color: "rgba(35,45,66,.55)" }, ticks: { maxTicksLimit: 5 } },
                y: { grid: { display: false }, ticks: { maxTicksLimit: 9 } } } }),
  });
  $("profile-note").textContent = r.volume_profile.value_area
    ? `POC ${fmt.px(r.volume_profile.poc)} \u00b7 Value area ${fmt.px(r.volume_profile.value_area[0])} \u2013 ${fmt.px(r.volume_profile.value_area[1])}`
    : `POC ${fmt.px(r.volume_profile.poc)}`;

  const hist = r.distribution.histogram || [];
  draw("cHist", {
    type: "bar",
    data: { labels: hist.map((h) => fmt.n(h.bps, 1)),
      datasets: [{ data: hist.map((h) => h.count),
        backgroundColor: hist.map((h) => h.bps >= 0 ? "rgba(34,197,94,.65)" : "rgba(239,68,68,.65)") }] },
    options: baseOpts({ scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } },
      y: { grid: { color: "rgba(35,45,66,.55)" } } } }),
  });
  const st = r.distribution.stats || {};
  $("hist-note").textContent = st.skew === undefined ? "" :
    `Skew ${fmt.n(st.skew, 2)} \u00b7 Excess kurtosis ${fmt.n(st.excess_kurtosis, 2)} \u00b7 ` +
    `Bullish ${fmt.pct((st.bullish_ratio ?? 0) * 100, 1)}`;

  const rv = r.rolling.volatility || [];
  draw("cVol", {
    type: "line",
    data: { labels: rv.map((p) => fmt.time(p.t)),
      datasets: [{ data: rv.map((p) => p.v), borderColor: "#f59e0b", borderWidth: 1.4, pointRadius: 0, fill: true,
        backgroundColor: "rgba(245,158,11,.12)" }] },
    options: baseOpts({ plugins: { tooltip: { callbacks: { label: (c) => `${fmt.n(c.parsed.y, 2)}%` } } },
      scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 5 } },
                y: { grid: { color: "rgba(35,45,66,.55)" } } } }),
  });

  const dd = r.drawdown.series || [];
  draw("cDD", {
    type: "line",
    data: { labels: dd.map((p) => fmt.time(p.t)),
      datasets: [{ data: dd.map((p) => p.dd), borderColor: "#ef4444", borderWidth: 1.3, pointRadius: 0, fill: true,
        backgroundColor: "rgba(239,68,68,.14)" }] },
    options: baseOpts({ scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 5 } },
      y: { max: 0, grid: { color: "rgba(35,45,66,.55)" }, ticks: { callback: (v) => `${fmt.n(v, 1)}%` } } } }),
  });
  const ep = (r.drawdown.episodes || [])[0];
  $("dd-note").textContent = r.drawdown.max_drawdown_pct === null ? "" :
    `Max drawdown ${fmt.n(r.drawdown.max_drawdown_pct, 2)}%` +
    (ep ? ` \u00b7 worst trough ${fmt.time(ep.trough_ts)}` : "");
const sp = r.microstructure.spread || [];
  draw("cSpread", {
    type: "line",
    data: { labels: sp.map((p) => fmt.time(p.t)),
      datasets: [{ data: sp.map((p) => p.spread_bps), borderColor: "#8b5cf6", borderWidth: 1.2, pointRadius: 0 }] },
    options: baseOpts(),
  });

  const ia = r.microstructure.interarrival || [];
  draw("cIArr", {
    type: "bar",
    data: { labels: ia.map((p) => fmt.time(p.t)),
      datasets: [{ data: ia.map((p) => p.ms), backgroundColor: "rgba(76,154,255,.55)" }] },
    options: baseOpts({ scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 5 } },
      y: { grid: { color: "rgba(35,45,66,.55)" }, ticks: { callback: (v) => `${fmt.int(v)}ms` } } } }),
  });

  const hr = r.hourly.hourly || [];
  draw("cHour", {
    type: "bar",
    data: { labels: hr.map((h) => `${String(h.hour).padStart(2, "0")}:00`),
      datasets: [{ label: "ticks", data: hr.map((h) => h.ticks), backgroundColor: "rgba(139,92,246,.6)",
        yAxisID: "y" },
        { label: "vol bps", type: "line", data: hr.map((h) => h.volatility_bps),
          borderColor: "#22c55e", borderWidth: 1.5, pointRadius: 0, yAxisID: "y1" }] },
    options: baseOpts({ scales: { x: { grid: { display: false } },
      y: { grid: { color: "rgba(35,45,66,.55)" } },
      y1: { position: "right", grid: { display: false } } } }),
  });

  const acf = r.behaviour.autocorrelation || [];
  draw("cAcf", {
    type: "bar",
    data: { labels: acf.map((a) => `L${a.lag}`),
      datasets: [{ data: acf.map((a) => a.acf),
        backgroundColor: acf.map((a) => (a.acf ?? 0) >= 0 ? "rgba(34,197,94,.6)" : "rgba(239,68,68,.6)") }] },
    options: baseOpts({ scales: { x: { grid: { display: false } },
      y: { suggestedMin: -0.3, suggestedMax: 0.3, grid: { color: "rgba(35,45,66,.55)" } } } }),
  });
  const b = r.behaviour;
  const regime = b.hurst === null ? "" : b.hurst > 0.55 ? "trending" : b.hurst < 0.45 ? "mean-reverting" : "random walk";
  $("acf-note").textContent =
    `Efficiency ratio ${fmt.n(b.efficiency_ratio, 3)} \u00b7 Hurst ${fmt.n(b.hurst, 2)} (${regime})`;

  const lt = r.microstructure.large_trades || [];
  draw("cLarge", {
    type: "bar",
    data: { labels: lt.map((t) => fmt.time(t.t)),
      datasets: [{ label: "impact (bps, 1 tick later)", data: lt.map((t) => t.impact_bps_1tick),
        backgroundColor: lt.map((t) => (t.impact_bps_1tick ?? 0) >= 0 ? "rgba(34,197,94,.7)" : "rgba(239,68,68,.7)") }] },
    options: baseOpts({ scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } },
      y: { grid: { color: "rgba(35,45,66,.55)" } } } }),
  });
  setNote("large", lt.length
    ? `${fmt.int(lt.length)} trades above p95 volume`
    : "No trades above the p95 volume threshold");

  // Footnotes for the cards that previously had none, so every chart states
  // its own headline number without needing to be expanded.
  setNote("price", `${r.ohlcv.timeframe} bars \u00b7 ${fmt.time(r.summary.start)} \u2192 ${fmt.time(r.summary.end)}`);
  setNote("spread", `${fmt.n(r.summary.avg_spread, 3)} bps avg \u00b7 p95 ${fmt.n(r.summary.p95_spread, 3)}`);
  setNote("iarr", `Mean gap ${fmt.n(r.summary.avg_interarrival_ms, 1)} ms \u00b7 median ${fmt.n(r.summary.median_interarrival_ms, 1)} ms`);
  setNote("vol", `Window ${fmt.int(r.rolling.window)} ticks`);
  setNote("hour", (() => {
    const hours = (r.hourly || {}).hourly || [];
    if (!hours.length) return "No hourly data";
    const top = hours.slice().sort((a, b) => (b.ticks || 0) - (a.ticks || 0))[0];
    return `Busiest ${String(top.hour).padStart(2, "0")}:00 UTC \u00b7 ${fmt.int(top.ticks)} ticks`;
  })());
  setNote("ticks", (() => {
    const n = (r.ticks || []).length;
    return `${fmt.int(n)} tick${n === 1 ? "" : "s"} shown`;
  })());
}

/** Write a card footnote, tolerating HTML that predates the element. */
function setNote(key, text) {
  const el = document.querySelector(`.card[data-card="${key}"] .note`);
  if (el) el.textContent = text;
}

function renderTicks(rows) {
  const t = $("tickTable");
  if (!rows || !rows.length) {
    t.innerHTML = `<thead></thead><tbody><tr><td class="note">No ticks in range.</td></tr></tbody>`;
    return;
  }
  // Consecutive ticks often repeat the same quote while only the instant
  // moves, which read as "all the same values". Collapse those repeats so the
  // table shows each distinct quote plus how many ticks carried it.
  const groups = [];
  for (const r of rows.slice().reverse()) {
    const g = groups[groups.length - 1];
    if (g && g.bid === r.bid && g.ask === r.ask && g.mid === r.mid
        && g.volume === r.volume && g.spread_bps === r.spread_bps) {
      g.repeats += 1;
      g.first_ts = r.ts;
    } else {
      groups.push({ ...r, repeats: 1, first_ts: r.ts });
    }
  }
  const cols = ["ts", "bid", "ask", "mid", "volume", "spread_bps", "tick_dir"];
  const head = cols.map((c) => `<th>${c}</th>`).join("") + `<th title="Snapshots carrying this quote">x</th>`;
  t.innerHTML =
    `<thead><tr>${head}</tr></thead><tbody>` +
    groups.map((r) => `<tr>${cols.map((c) => {
      const v = r[c];
      if (v === null || v === undefined) return "<td>\u2013</td>";
      if (c === "ts") {
        // A held quote spans first..last instant; repeats show the range.
        const first = fmt.timems(r.first_ts || v);
        const lastTs = fmt.timems(v);
        return `<td>${esc(r.repeats > 1 ? `${lastTs} \u2192 ${first}` : lastTs)}</td>`;
      }
      if (c === "tick_dir") return `<td class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "\u25b2" : v < 0 ? "\u25bc" : "\u2013"}</td>`;
      return `<td>${fmt.num(v)}</td>`;
    }).join("")}<td>${r.repeats > 1 ? `\u00d7${r.repeats}` : ""}</td></tr>`).join("") + "</tbody>";
}
/* ---------------- expandable cards ---------------- */

/** One definition row. Values are pre-formatted strings. */
/** One definition row list. Tolerates a missing/null pair list. */
function dl(pairs) {
  const rows = (pairs || []).filter(([, v]) => v !== null && v !== undefined && v !== "");
  if (!rows.length) return "";
  return `<dl class="dl">${rows.map(([k, v]) =>
    `<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join("")}</dl>`;
}

/** A detail block: optional heading, prose, then label/value rows. */
function block(title, prose, pairs) {
  const body = dl(pairs);
  if (!prose && !body) return "";
  return `${title ? `<h3>${esc(title)}</h3>` : ""}` +
    (prose ? `<p class="explain">${prose}</p>` : "") + body;
}

/** The last point of a series, or null. */
function last(series, key) {
  const v = series && series.length ? series[series.length - 1][key] : null;
  return v === null || v === undefined ? null : v;
}

/** Mean of one numeric field across a series, or null. */
function mean(series, key) {
  const vs = (series || []).map((p) => Number(p[key])).filter((n) => Number.isFinite(n));
  return vs.length ? vs.reduce((a, b) => a + b, 0) / vs.length : null;
}

/** Highest and lowest point of a series, or nulls. */
function extremes(series, key) {
  const vs = (series || []).map((p) => Number(p[key])).filter((n) => Number.isFinite(n));
  return vs.length ? { max: Math.max(...vs), min: Math.min(...vs) } : { max: null, min: null };
}

/**
 * Detail copy for every expandable card, keyed by the card's data-card value.
 *
 * Each entry returns the full body for that card; an empty string leaves the
 * card chart-only. Numbers are formatted in the same place as the prose, so
 * the two cannot drift apart.
 */
const DETAILS = {
  price: (r) => {
    const s = r.summary, b = r.ohlcv || {};
    return block("Session",
      "Mid price on the right axis, traded volume on the left, resampled into " +
      `${esc(b.timeframe || "")} bars. Each bar aggregates every tick in its interval.`,
      [["Timeframe", esc(b.timeframe || "\u2013")],
       ["Bars", fmt.int((b.bars || []).length)],
       ["Window start", esc(fmt.time(s.start))],
       ["Window end", esc(fmt.time(s.end))],
       ["Sessions", fmt.int((s.session_span || []).length)],
       ["Open / close", `${fmt.px(s.open)} / ${fmt.px(s.close)}`],
       ["High / low", `${fmt.px(s.high)} / ${fmt.px(s.low)}`],
       ["VWAP", fmt.px(s.vwap)],
       ["Mean / stdev mid", `${fmt.px(s.mean_mid)} / ${fmt.px(s.std_mid)}`],
       ["Change", `${fmt.px(s.change)} (${fmt.pct(s.change_pct)})`],
       ["Total volume", fmt.int(s.total_volume)],
       ["Avg tick size", fmt.num(s.avg_tick_size)]]) +
      block("Coverage",
        "How much history the chosen point and its lookback actually returned, "
        + "within the cached window. Fewer ticks than expected usually means a "
        + "quiet instrument rather than a missing feed.",
        [["Rows analysed", fmt.int(r.meta && r.meta.rows_analysed)],
         ["Lookback", `${fmt.n(s.duration_seconds / 3600, 2)} h`],
         ["Point in time", esc(fmt.time(r.meta && r.meta.as_of))],
         ["Ticks / minute", fmt.n(s.ticks_per_minute, 1)],
         ["Realised vol", `${fmt.n(s.realized_vol_bps, 2)} bps`],
         ["Annualised vol", `${fmt.n(s.annualised_vol_pct, 1)}%`]]);
  },

  ofi: (r) => {
    const ts = r.microstructure.trade_sign || {};
    const ofi = r.microstructure.order_flow || [];
    return block("How it is measured",
      "Aggressive volume signed by tick direction, normalised to \u00b11. Bars above " +
      "zero mean buyers lifted the offer more than sellers hit the bid.", [
        ["Buy ticks", fmt.int(ts.buy_ticks)],
        ["Sell ticks", fmt.int(ts.sell_ticks)],
        ["Unchanged", fmt.int(ts.unchanged_ticks)],
        ["Buy ratio", fmt.pct((ts.buy_ratio ?? 0) * 100, 1)],
        ["Sell ratio", fmt.pct((ts.sell_ratio ?? 0) * 100, 1)],
        ["Net imbalance", fmt.pct((ts.imbalance ?? 0) * 100, 1)],
        ["Latest reading", fmt.n(last(ofi, "imbalance"), 3)],
        ["Window mean", fmt.n(mean(ofi, "imbalance"), 3)],
      ]);
  },

  depth: (r) => {
    const d = r.depth || { available: false };
    if (!d.available) return "";
    const im = d.imbalance_series || [];
    return block("Resting size",
      "Depth comes from the full order-book reconstruction, so it is present whenever the " +
      "pipeline supplied resting sizes. A bid share above " +
      "50% means more resting size sits on the buy side than on the offer.", [
        ["Avg bid depth", fmt.int(d.avg_bid_depth)],
        ["Avg ask depth", fmt.int(d.avg_ask_depth)],
        ["Max bid depth", fmt.int(d.max_bid_depth)],
        ["Max ask depth", fmt.int(d.max_ask_depth)],
        ["Bid share", fmt.pct((d.avg_imbalance_share ?? 0) * 100, 1)],
        ["Bid levels", fmt.n(d.avg_levels_bid, 1)],
        ["Ask levels", fmt.n(d.avg_levels_ask, 1)],
        ["Latest imbalance", fmt.n(last(im, "v"), 3)],
      ]);
  },

  profile: (r) => {
    const p = r.volume_profile || {};
    const va = p.value_area;
    const hvn = p.high_volume_nodes || [];
    const rows = hvn.map((n) => [fmt.px(n.price), `${fmt.int(n.volume)} \u00b7 ${fmt.int(n.ticks)} ticks`]);
    return block("Support and resistance",
      "Volume is bucketed by traded price. The point of control is the busiest price; " +
      "the value area is the narrow band holding 70% of all volume. High-volume " +
      "nodes are candidate support or resistance levels.", [
        ["Point of control", fmt.px(p.poc)],
        ["Value area low", va ? fmt.px(va[0]) : null],
        ["Value area high", va ? fmt.px(va[1]) : null],
        ["Bins", fmt.int((p.bins || []).length)],
      ]) + (hvn.length ? block("High-volume nodes", null, rows) : "");
  },

  hist: (r) => {
    const st = (r.distribution || {}).stats || {};
    return block("Higher moments",
      "Per-tick returns in basis points. Excess kurtosis above zero means fat tails: " +
      "large moves are more common than a normal distribution would predict. A high " +
      "Jarque-Bera statistic likewise rejects normality.", [
        ["Mean", `${fmt.n(st.mean_bps, 3)} bps`],
        ["Std dev", `${fmt.n(st.std_bps, 3)} bps`],
        ["Median", `${fmt.n(st.median_bps, 3)} bps`],
        ["Min / max", `${fmt.n(st.min_bps, 2)} / ${fmt.n(st.max_bps, 2)}`],
        ["1% / 99%", `${fmt.n(st.p01_bps, 2)} / ${fmt.n(st.p99_bps, 2)}`],
        ["Skew", fmt.n(st.skew, 3)],
        ["Excess kurtosis", fmt.n(st.excess_kurtosis, 3)],
        ["Jarque-Bera", fmt.n(st.jarque_bera, 1)],
        ["Bullish ratio", fmt.pct((st.bullish_ratio ?? 0) * 100, 1)],
      ]);
  },

  vol: (r) => {
    const rv = r.rolling.volatility || [];
    const ex = extremes(rv, "v");
    return block("Rolling window",
      `Annualised realised volatility over a rolling window of ${fmt.int(r.rolling.window)} ` +
      "ticks. It rises into turbulent stretches and flattens when the book is quiet.", [
        ["Window", `${fmt.int(r.rolling.window)} ticks`],
        ["Latest", `${fmt.n(last(rv, "v"), 2)}%`],
        ["Window mean", `${fmt.n(mean(rv, "v"), 2)}%`],
        ["Min / max", `${fmt.n(ex.min, 2)}% / ${fmt.n(ex.max, 2)}%`],
        ["Points", fmt.int(rv.length)],
      ]);
  },

  dd: (r) => {
    const d = r.drawdown || {};
    const eps = d.episodes || [];
    const rows = eps.map((e, i) => [
      `#${i + 1} \u00b7 ${fmt.pct(e.depth_pct, 2)}`,
      `${fmt.time(e.peak_ts)} \u2192 ${fmt.time(e.trough_ts)}` +
        (e.recovered_ts ? ` \u2192 ${fmt.time(e.recovered_ts)}` : " \u00b7 unrecovered"),
    ]);
    return block("Worst episodes",
      "Drawdown is measured from the running peak. The deepest episodes are listed " +
      "first; an unrecovered trough means the window ends below its prior high.", [
        ["Max drawdown", fmt.pct(d.max_drawdown_pct, 2)],
        ["Episodes", fmt.int(eps.length)],
      ]) + (rows.length ? block("Episode timeline", null, rows) : "");
  },

  spread: (r) => {
    const sp = r.microstructure.spread || [];
    const ex = extremes(sp, "spread_bps");
    const s = r.summary;
    return block("Quoted cost",
      "Spread in basis points at each tick. Wider quotes mean a more expensive round " +
      "trip, whether or not liquidity was actually taken.", [
        ["Latest", `${fmt.n(last(sp, "spread_bps"), 3)} bps`],
        ["Series mean", `${fmt.n(mean(sp, "spread_bps"), 3)} bps`],
        ["Min / max", `${fmt.n(ex.min, 3)} / ${fmt.n(ex.max, 3)} bps`],
        ["Window average", `${fmt.n(s.avg_spread, 3)} bps`],
        ["Median", `${fmt.n(s.median_spread, 3)} bps`],
        ["95th percentile", `${fmt.n(s.p95_spread, 3)} bps`],
        ["Observations", fmt.int(sp.length)],
      ]);
  },

  iarr: (r) => {
    const ia = r.microstructure.interarrival || [];
    const ex = extremes(ia, "ms");
    const s = r.summary;
    return block("Arrival intensity",
      "Time between consecutive ticks. Short gaps mean an active, liquid market; long " +
      "gaps usually mark a session break or a quiet instrument.", [
        ["Latest", `${fmt.n(last(ia, "ms"), 0)} ms`],
        ["Series mean", `${fmt.n(mean(ia, "ms"), 1)} ms`],
        ["Min / max", `${fmt.n(ex.min, 1)} / ${fmt.n(ex.max, 0)} ms`],
        ["Window average", `${fmt.n(s.avg_interarrival_ms, 1)} ms`],
        ["Median", `${fmt.n(s.median_interarrival_ms, 1)} ms`],
        ["Longest gap", `${fmt.n(s.max_interarrival_ms, 0)} ms`],
      ]);
  },

  hour: (r) => {
    const hr = (r.hourly || {}).hourly || [];
    if (!hr.length) return block("", "No hourly breakdown available for this window.", null);
    const busiest = hr.slice().sort((a, b) => (b.ticks || 0) - (a.ticks || 0))[0];
    const mostVol = hr.slice().sort((a, b) => (b.volatility_bps ?? -1) - (a.volatility_bps ?? -1))[0];
    const quiet = hr.slice().sort((a, b) => (a.ticks || 0) - (b.ticks || 0))[0];
    const hh = (n) => `${String(n).padStart(2, "0")}:00`;
    const rows = hr.map((h) => [hh(h.hour),
      `${fmt.int(h.ticks)} ticks \u00b7 ${fmt.n(h.volatility_bps, 2)} bps vol`]);
    return block("Daily shape",
      "Activity and directional bias by hour of day, UTC. The busiest hour is when the " +
      "instrument is most worth trading; the quietest is when spreads usually widen.", [
        ["Busiest hour", `${hh(busiest.hour)} \u00b7 ${fmt.int(busiest.ticks)} ticks`],
        ["Quietest hour", `${hh(quiet.hour)} \u00b7 ${fmt.int(quiet.ticks)} ticks`],
        ["Most volatile hour", `${hh(mostVol.hour)} \u00b7 ${fmt.n(mostVol.volatility_bps, 2)} bps`],
        ["Hours with data", fmt.int(hr.length)],
      ]) + block("Hour by hour", null, rows);
  },

  acf: (r) => {
    const b = r.behaviour || {};
    const acf = b.autocorrelation || [];
    const ex = extremes(acf, "acf");
    const regime = b.hurst === null || b.hurst === undefined ? "unknown"
      : b.hurst > 0.55 ? "trending" : b.hurst < 0.45 ? "mean-reverting" : "random walk";
    const lag1 = (acf.find((a) => a.lag === 1) || {}).acf;
    const vr = b.variance_ratio || [];
    return block("Price behaviour",
      "Autocorrelation measures whether a return predicts the next one. An efficiency " +
      "ratio near 1 means the price travels in a straight line; near 0 means it chops " +
      "back and forth. Hurst classifies the series overall.", [
        ["Lag-1 autocorrelation", fmt.n(lag1, 4)],
        ["Autocorr min / max", `${fmt.n(ex.min, 3)} / ${fmt.n(ex.max, 3)}`],
        ["Efficiency ratio", fmt.n(b.efficiency_ratio, 4)],
        ["Hurst exponent", `${fmt.n(b.hurst, 3)} \u00b7 ${regime}`],
        ["Lags computed", fmt.int(acf.length)],
      ]) + (vr.length ? block("Variance ratios",
        "A ratio above 1 at lag k means the price drifts more over k steps than " +
        "random-walk variance alone would predict.", vr.slice(0, 10).map((v) =>
          [`Lag ${v.lag}`, fmt.n(v.vr ?? v.variance_ratio, 3)])) : "");
  },

  large: (r) => {
    const lt = r.microstructure.large_trades || [];
    const rows = lt.slice(-10).reverse().map((t) => [
      `${fmt.time(t.t)} \u00b7 ${t.direction}`,
      `${fmt.int(t.volume)} @ ${fmt.px(t.price)} \u2192 ${fmt.n(t.impact_bps_1tick, 2)} bps`,
    ]);
    return block("Impact",
      "A trade above the 95th-percentile volume, plotted by how far the mid moved on " +
      "the following tick. Positive means the trade pushed price up.", [
        ["Threshold (p95 volume)", fmt.int(r.microstructure.large_trade_threshold)],
        ["Large trades", fmt.int(r.summary.large_trades)],
        ["Mean 1-tick impact", `${fmt.n(mean(lt, "impact_bps_1tick"), 3)} bps`],
        ["Latest impact", `${fmt.n(last(lt, "impact_bps_1tick"), 3)} bps`],
      ]) + (rows.length ? block("Most recent", null, rows) : "");
  },

  ticks: (r) => {
    const s = r.summary;
    return block("About this table",
      "The raw per-tick metrics behind every chart above, newest first. Dir is the sign " +
      "of the mid change on that tick.", [
        ["Rows shown", fmt.int((r.ticks || []).length)],
        ["Session start", esc(fmt.time(s.start))],
        ["Session end", esc(fmt.time(s.end))],
        ["Ticks in window", fmt.int(s.ticks)],
        ["Buy / sell / flat", `${fmt.int(s.buy_ticks)} / ${fmt.int(s.sell_ticks)} / ${fmt.int(s.unchanged_ticks)}`],
        ["Columns", "ts, bid, ask, mid, volume, spread_bps, tick_dir"],
      ]);
  },

  coverage: () => {
    const rows = window.__classSummary || [];
    const h = window.__health || {};
    const top = rows.slice().sort((a, b) => (b.ticks || 0) - (a.ticks || 0)).slice(0, 5)
      .map((c) => [esc(c.label), `${fmt.int(c.count)} instruments \u00b7 ${fmt.int(c.ticks)} ticks`]);
    return block("Feed scope",
      "Instruments are grouped by asset class from the gold symbol dimension; the " +
      "family field collapses those into broader groups.", [
        ["Traded instruments", fmt.int(h.symbol_count)],
        ["Classified", fmt.int(h.classified_count)],
        ["Unclassified", fmt.int(h.unclassified_count)],
        ["Asset classes", fmt.int(h.asset_class_count)],
        ["Symbol dimension", fmt.int(h.dimension_rows)],
        ["Tick rows", fmt.int(h.row_count)],
        ["Latest tick", esc(fmt.time(h.latest_tick))],
      ]) + (top.length ? block("Busiest classes", null, top) : "");
  },

  calendar: () => {
    const day = window.__calSelected;
    if (!day) return "";
    return block(`Selected day \u00b7 ${day.date}`,
      "One calendar day of the loaded window, aggregated from the same bars the price " +
      "chart is drawn from.", [
        ["Date", esc(day.date)],
        ["Bars", fmt.int(day.bars)],
        ["Ticks", fmt.int(day.ticks)],
        ["Volume", fmt.int(day.volume)],
        ["Open / close", `${fmt.px(day.open)} / ${fmt.px(day.close)}`],
        ["High / low", `${fmt.px(day.high)} / ${fmt.px(day.low)}`],
        ["Change", fmt.pct(day.change)],
        ["Mean spread", `${fmt.n(day.spread, 3)} bps`],
        ["Busiest hour", esc(day.busiest)],
      ]);
  },
};

/** Fill every card's detail body from one report. */
function renderDetails(r) {
  document.querySelectorAll(".card-detail[data-detail-for]").forEach((el) => {
    const build = DETAILS[el.dataset.detailFor];
    el.innerHTML = build ? build(r) : "";
  });
}

/**
 * Toggle a card between its compact and expanded form.
 *
 * The whole card is the hit target, but a click that lands inside the detail
 * body (text selection, a control) must not collapse it again.
 */
function toggleCard(card) {
  const open = card.classList.toggle("open");
  card.querySelectorAll(".card-detail").forEach((d) => { d.hidden = !open; });
  card.setAttribute("aria-expanded", open ? "true" : "false");
}

/** Make every card keyboard- and mouse-operable as a disclosure. */
function wireCards() {
  document.querySelectorAll(".card[data-card]").forEach((card) => {
    card.setAttribute("tabindex", "0");
    card.setAttribute("role", "button");
    card.setAttribute("aria-expanded", "false");
    card.addEventListener("click", (e) => {
      if (e.target.closest(".card-detail, a, button, input, select")) return;
      toggleCard(card);
    });
    card.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      toggleCard(card);
    });
  });
}

/* ---------------- calendar ---------------- */

const MONTHS = ["January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];

/** UTC day key. Grouping is UTC throughout, to match the hourly profile. */
function dayKey(ts) {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return null;
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
}

/** Collapse the OHLCV bars into one record per UTC day, for the calendar. */
function calendarDays(r) {
  const out = new Map();
  for (const b of (r.ohlcv || {}).bars || []) {
    const key = dayKey(b.t);
    if (!key) continue;
    const d = out.get(key) || {
      date: key, bars: 0, ticks: 0, volume: 0, open: b.o, close: b.c,
      high: b.h, low: b.l, spreadSum: 0, spreadN: 0, hours: new Map(),
    };
    d.bars += 1;
    d.ticks += b.n || 0;
    d.volume += b.v || 0;
    if (d.open === null || d.open === undefined) d.open = b.o;
    d.close = b.c;
    d.high = d.high === null || d.high === undefined ? b.h : Math.max(d.high, b.h);
    d.low = d.low === null || d.low === undefined ? b.l : Math.min(d.low, b.l);
    if (b.spread !== null && b.spread !== undefined) { d.spreadSum += b.spread; d.spreadN += 1; }
    const h = new Date(b.t).getUTCHours();
    d.hours.set(h, (d.hours.get(h) || 0) + (b.n || 0));
    out.set(key, d);
  }
  return [...out.values()].map((d) => {
    let busiest = null;
    for (const [h, n] of d.hours) if (busiest === null || n > busiest[1]) busiest = [h, n];
    return {
      ...d,
      spread: d.spreadN ? d.spreadSum / d.spreadN : null,
      change: d.open ? (d.close - d.open) / d.open * 100 : null,
      busiest: busiest ? `${String(busiest[0]).padStart(2, "0")}:00` : null,
    };
  }).sort((a, b) => a.date.localeCompare(b.date));
}

const cal = { days: [], selected: null };

/** Paint the month grid for the currently selected day. */
function renderMonth() {
  const box = $("calendar");
  const label = $("calLabel");
  const first = cal.days[0];
  const lastDay = cal.days[cal.days.length - 1];
  const [year, month] = (cal.selected || first.date).split("-").map(Number);
  const byDate = new Map(cal.days.map((d) => [d.date, d]));
  const maxTicks = Math.max(...cal.days.map((d) => d.ticks || 0), 1);
  if (label) {
    label.textContent = `${MONTHS[month - 1]} ${year}` +
      (first.date === lastDay.date ? "" : ` \u00b7 ${first.date} \u2192 ${lastDay.date}`);
  }

  const cells = [];
  const firstDow = new Date(Date.UTC(year, month - 1, 1)).getUTCDay();
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const today = dayKey(new Date().toISOString());
  for (let i = 0; i < firstDow; i += 1) cells.push(`<div class="calday empty" aria-hidden="true"></div>`);

  for (let day = 1; day <= daysInMonth; day += 1) {
    const key = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const d = byDate.get(key);
    if (!d) { cells.push(`<div class="calday empty" aria-hidden="true"></div>`); continue; }
    const dir = d.change === null || d.change === undefined ? "" : (d.change >= 0 ? "up" : "down");
    const width = Math.max(((d.ticks || 0) / maxTicks) * 100, 4);
    cells.push(
      `<button class="calday ${dir}${key === today ? " today" : ""}${key === cal.selected ? " sel" : ""}" ` +
      `data-date="${key}" title="${esc(key)} \u00b7 ${fmt.int(d.ticks)} ticks">` +
      `<span class="cnum">${day}</span><span class="cticks">${fmt.int(d.ticks)}</span>` +
      `<span class="cbar" style="width:${width.toFixed(1)}%"></span></button>`);
  }

  box.innerHTML = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    .map((d) => `<div class="calhead" role="columnheader">${d}</div>`).join("") + cells.join("");
  box.querySelectorAll(".calday[data-date]").forEach((b) => {
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      cal.selected = b.dataset.date;
      renderMonth();
      syncCalendarDetail();
    });
  });
}

function renderCalendar(r) {
  const box = $("calendar");
  if (!box) return;
  cal.days = calendarDays(r);
  const tag = $("cal-tag");
  if (tag) {
    tag.textContent = cal.days.length
      ? `${cal.days.length} day${cal.days.length === 1 ? "" : "s"} in window`
      : "no data";
  }
  if (!cal.days.length) {
    box.innerHTML = `<p class="note">No calendar days in the selected window. Widen the lookback, or move the point in time later.</p>`;
    const label = $("calLabel");
    if (label) label.textContent = "\u2013";
    return;
  }
  // Default to the last day that actually carries data rather than to "today",
  // which usually falls outside a lookback measured in minutes.
  if (!cal.selected || !cal.days.some((d) => d.date === cal.selected)) {
    cal.selected = cal.days[cal.days.length - 1].date;
  }
  renderMonth();
}

/** Step the cursor a whole month, keeping the selection on a loaded day. */
function shiftMonth(delta) {
  if (!cal.days.length) return;
  const [y, m, d] = cal.selected.split("-").map(Number);
  const shifted = new Date(Date.UTC(y, m - 1 + delta, 1));
  const prefix = `${shifted.getUTCFullYear()}-${String(shifted.getUTCMonth() + 1).padStart(2, "0")}`;
  const key = `${prefix}-${String(d).padStart(2, "0")}`;
  if (cal.days.some((x) => x.date === key)) cal.selected = key;
  else {
    // No data on that exact day: fall back to the first loaded day of the month.
    const opts = cal.days.filter((x) => x.date.startsWith(prefix));
    if (opts.length) cal.selected = opts[0].date;
  }
  renderMonth();
  syncCalendarDetail();
}

/** Refresh only the calendar card's detail body from the current selection. */
function syncCalendarDetail() {
  const card = document.querySelector('.card[data-card="calendar"]');
  const el = card && card.querySelector(".card-detail");
  if (!el) return;
  window.__calSelected = cal.days.find((x) => x.date === cal.selected) || null;
  el.innerHTML = DETAILS.calendar ? DETAILS.calendar(null) : "";
}

/* ---------------- orchestration ---------------- */

async function analyse() {
  const btn = $("run");
  if (btn) btn.disabled = true;
  setLoading(true);
  try {
    const p = new URLSearchParams({
      symbol: val("symbol"),
      timeframe: val("timeframe", "1m") || "1m",
      window: val("window", 50) || 50,
      limit: val("limit", 50000) || 50000,
      // Minutes back from the chosen point; the API clamps it to the cached
      // window anyway, so a stale page or a hand-edited URL still returns data
      // rather than a 422.
      lookback_minutes: val("lookback", 60) || 60,
      // The chosen point in time. Absent until the timeline is built, which the
      // API reads as "up to the newest row".
      ...(asOfIso() ? { as_of: asOfIso() } : {}),
    });
    const r = await api(`/api/analytics?${p}`);
    renderKpis(r.summary, r.microstructure);
    renderCharts(r);
    renderTicks(r.ticks);
    renderPriceTag(r.meta);
    renderCalendar(r);
    syncCalendarDetail();
    renderDetails(r);
    renderFreshness(r);
    renderCoverage(window.__health || {});
  } catch (err) {
    bannerErr(err.message);
    $("kpis").innerHTML =
      `<div class="kpi"><div class="label">Error</div><div class="value err">${esc(err.message)}</div></div>`;
  } finally {
    if (btn) btn.disabled = false;
    setLoading(false);
  }
}

/** Name the analysed instrument and its asset class above the price chart. */
function renderPriceTag(meta) {
  const el = $("price-tag");
  if (!el || !meta) return;
  // Only the display name, description and class are shown; the raw symbolId
  // is an internal join key and adds nothing for a reader.
  el.textContent = [meta.symbol_name || meta.symbol || "",
    meta.symbol_description || "", meta.asset_class_label || ""]
    .filter(Boolean).join(" \u00b7 ");
}

async function init() {
  try {
    // One health call, reused for the connection badge, the timeframes, the
    // timeline and the lookback picker's bounds.
    const health = await loadHealth();
    await fillTimeframes(health);
    fillLookbacks(health);
    await loadClassSummary();
    await loadSymbols();
    renderCoverage(health);
  } catch (err) {
    bannerErr(err.message);
  }
  await analyse();
}

// on() tolerates a control the served HTML does not have, so a stale
// HTML/JS mix degrades to a working subset instead of killing the whole page.
function on(id, ev, fn) { const el = $(id); if (el) el.addEventListener(ev, fn); }

on("run", "click", analyse);
// Changing the asset class re-filters the symbol list, then re-analyses.
on("assetClass", "change", async () => {
  try { await loadSymbols(); await analyse(); }
  catch (err) { bannerErr(err.message); }
});
on("refresh", "click", async () => {
  setLoading(true);
  try {
    const health = await loadHealth();
    fillLookbacks(health);
    await loadClassSummary();
    await loadSymbols();
    await analyse();
  }
  catch (err) { bannerErr(err.message); }
  finally { setLoading(false); }
});
on("symbol", "change", analyse);
// Dragging the timeline moves the point in time; the label follows the drag,
// the analysis waits for the "Run" click so a drag costs one query, not fifty.
on("asOf", "input", onAsOfInput);
on("calPrev", "click", () => shiftMonth(-1));
on("calNext", "click", () => shiftMonth(1));
on("calToday", "click", () => {
  // Jump to the most recent loaded day; "today" is usually outside the window.
  if (cal.days.length) cal.selected = cal.days[cal.days.length - 1].date;
  renderMonth();
  syncCalendarDetail();
});

document.addEventListener("DOMContentLoaded", () => {
  wireCards();
  init();
});
