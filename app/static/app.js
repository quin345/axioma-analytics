/* Axioma Analytics dashboard */
"use strict";

const $ = (id) => document.getElementById(id);
const charts = {};

/* ---- display time zone --------------------------------------------------
 * Every wall-clock time on the page is rendered in one IANA zone, published
 * by /api/health as `display_timezone`. Until the first health answer lands
 * we fall back to UTC, which is what the ISO strings we hold already are. The
 * formatters below read it at call time, so assigning DISPLAY_TZ re-labels the
 * clock, the time badges and the hourly profile without a re-render. */
let DISPLAY_TZ = "UTC";

/** Short zone abbreviation ("EDT", "UTC"), for the badges next to a time. */
function tzAbbr() {
  return new Intl.DateTimeFormat("en-US", { timeZone: DISPLAY_TZ, timeZoneName: "short" })
    .formatToParts(new Date())
    .find((p) => p.type === "timeZoneName")?.value || DISPLAY_TZ;
}

/** The wall-clock parts of one instant in the display zone, all strings. */
function zoneParts(iso, opts) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-US", Object.assign({ timeZone: DISPLAY_TZ }, opts))
    .formatToParts(d);
  const o = {};
  for (const x of parts) if (x.type !== "literal") o[x.type] = x.value;
  return o;
}

/** A local calendar date (y/mo/d, the zone's wall clock) for a given instant. */
function zoneDate(iso) {
  const p = zoneParts(iso, { year: "numeric", month: "2-digit", day: "2-digit" }) || {};
  return { y: Number(p.year || 0), mo: Number(p.month || 1) - 1, d: Number(p.day || 1) };
}

/** The UTC instant of a wall-clock time (y/mo/d h:mi) in the display zone. */
function wallToInstant(y, mo, d, h, mi) {
  let t = Date.UTC(y, mo, d, h, mi);
  // Resolve the zone offset at that local time; two passes converge across DST.
  for (let i = 0; i < 2; i++) {
    const p = zoneParts(t, { year: "numeric", month: "2-digit", day: "2-digit",
                             hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    if (!p) break;
    const asIf = Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day),
                          Number(p.hour), Number(p.minute));
    const delta = Date.UTC(y, mo, d, h, mi) - asIf;
    if (!delta) break;
    t += delta;
  }
  return t;
}

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
    const p = zoneParts(iso, { month: "2-digit", day: "2-digit", hour: "2-digit",
                               minute: "2-digit", second: "2-digit", hourCycle: "h23" });
    if (!p) return String(iso);
    return `${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}`;
  },
  /** The update time on the 1-minute refresh grid: 14:47:12 -> 14:47.
   * Floored to the minute (seconds dropped), never rounded up, so "Last
   * update" cannot sit in the future. The cache refresh cycle runs every
   * minute, so the badge lands on the same grid the window is written on. */
  minute: (iso) => {
    if (!iso) return "\u2013";
    const p = zoneParts(iso, { year: "numeric", month: "2-digit", day: "2-digit",
                               hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    if (!p) return String(iso);
    return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
  },
  /** "HH:MM:SS" in the display zone, for the clock-grid badges. */
  hm: (iso) => {
    if (!iso) return "\u2013";
    const p = zoneParts(iso, { hour: "2-digit", minute: "2-digit", second: "2-digit",
                               hourCycle: "h23" });
    return p ? `${p.hour}:${p.minute}:${p.second}` : String(iso);
  },
  /** Full timestamp with millis: "10-06 22:09:14.747". Tick metrics can
   * repeat the same quote many times a second, so second precision made every
   * row in Latest ticks look identical even though the instants differed. */
  timems: (iso) => {
    if (!iso) return "\u2013";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    const p = zoneParts(iso, { month: "2-digit", day: "2-digit", hour: "2-digit",
                               minute: "2-digit", second: "2-digit", hourCycle: "h23" });
    const ms = String(d.getMilliseconds()).padStart(3, "0");
    return p ? `${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}.${ms}` : String(iso);
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

/**
 * Set an element's text, optionally its class.
 *
 * Only touch className when a class is supplied: assigning unconditionally
 * would strip the styling classes the markup already carries. Top-level so both
 * loadHealth() (the cadence strip) and renderFreshness() can use it - as a
 * local inside renderFreshness it was out of scope for loadHealth, which threw
 * "set is not defined" and surfaced as a Connection problem banner.
 */
function set(id, text, cls) {
  const el = $(id);
  if (!el) return;
  el.textContent = text;
  if (cls) el.className = cls;
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
  // The IANA zone every wall-clock time on the page is rendered in, published
  // by /api/health so one setting drives the clock, the time badges and the
  // hourly profile. UTC until the first answer keeps the pre-load paint sane.
  DISPLAY_TZ = h.display_timezone || "UTC";
  const hh = $("hourlyHeading");
  if (hh) hh.textContent = `Activity by hour (${tzAbbr()})`;
  const dot = $("connDot");
  if (h.connected) {
    dot.className = "dot ok";
    $("connText").textContent = h.has_data ? "Connected" : "Connected \u00b7 no data";
    // The badge tooltip keeps the window and cache state for context rather
    // than noise: how much Redis holds and how often the cycle tops it up.
    dot.title = h.cache_connected === false
      ? (h.cache_error || "The Redis cache is unreachable.")
      : `Window ${Math.round((h.lookback_minutes || 0) / 60 * 10) / 10} h, cached in Redis`
        + (h.cache_refresh_minutes ? `, refreshed every ${h.cache_refresh_minutes} min` : "");
  } else {
    dot.className = "dot bad";
    $("connText").textContent = "Offline";
  }
  // Hints are useful even when connected (empty table, or instruments the
  // symbol dimension does not cover), so surface them in both states. A cache
  // outage arrives as one of those hints: reads still work, they just cost a
  // KQL query, which is a warning rather than a failure.
  if (h.hints && h.hints.length) banner(h.hints, h.connected ? "warn" : "bad");
  // The controls are configured from the API so they can never drift from what
  // the service actually offers. The refresh cycle runs just past the minute
  // clock marks (+15 s), so 05:00:15, 05:01:15, ...
  REFRESH_MINUTES = Number(h.cache_refresh_minutes) || 1;
  REFRESH_OFFSET_SECONDS = Number(h.cache_refresh_offset_seconds) || 0;
  // The cadence strip: "Data refreshes every minute", mirrored from the API.
  set("fbCadence", `Data refreshes every ${REFRESH_MINUTES} minutes`);
  renderCoverage(h);
  // The timeline is anchored on the newest `metrics_5m` stop - the point the
  // headline metrics are served from - so the selector's latest bound is the
  // last precomputed timestamp, not merely the last raw tick. It falls back to
  // the newest tick when the grid is empty or the probe predates it, so the
  // control still lands on something usable rather than an empty range.
  // The field may sit after the report, so anchor it on the newest data and
  // rebuild the earliest/latest bounds before renderFreshness paints the badge.
  buildTimeline(h.metrics_latest_as_of || h.latest_tick || undefined);
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

/** Bar timeframes for the picker, read from `/api/health` so the control can
 * only ever offer one the analytics endpoint accepts. */
async function fillTimeframes(h) {
  const health = h || (await api("/api/health"));
  const sel = $("timeframe");
  if (!sel) return;
  sel.innerHTML = (health.timeframes || [])
    .map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join("");
  sel.value = "1m";
}

/**
 * The point-in-time timeline the user picks a moment from: a four-hour span
 * (`timeline_minutes`) in five-minute steps (`timeline_step_minutes`).
 *
 * Every stop sits on the clock grid (:00, :05, ...) and the newest stop is
 * never behind the feed - it is the next grid mark at or after the newest data.
 * That is what keeps the oldest stop with the widest window inside the cached
 * window: span (4 h) + window (4 h) = the 8 hours Redis holds, exactly. The
 * chosen point is kept as an absolute instant, so refilling the controls or
 * newer data arriving does not silently move a point the user picked; while the
 * point still sits on the newest stop it follows the feed instead, which is how
 * the control's default stays the latest `metrics_5m` timestamp.
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

/** How far the chosen point sits behind the newest stop: "45 min" / "1 h 55 min". */
function backLabel(minutes) {
  const m = Math.round(minutes);
  if (m < 60) return `${m} min back`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min back`;
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

  // The input is a HH:MM wall-clock time, so intersect the typed slot with the
  // 4-hour window and keep the stop grid: the field can never show a point the
  // analysis does not honour. A point the user moved off the newest stop stays
  // put when newer data arrives; one still sitting on it follows the feed,
  // which is what makes the selector's default the *latest* `metrics_5m`
  // timestamp rather than whichever one happened to be newest at page load.
  const input = $("asOf");
  const typed = input ? String(input.value || "").trim() : "";
  const follows = window.__asOfLatest !== false;
  if (typed && !follows) {
    // The typed HH:MM is a wall-clock time in the display zone: resolve it
    // against that zone's calendar day rather than UTC's.
    const { y, mo, d } = zoneDate(anchor);
    const hh = Math.min(23, Number(input.value.split(":")[0] || "0"));
    const mm = Math.min(59, Number(input.value.split(":")[1] || "0"));
    const cand = wallToInstant(y, mo, d, hh, mm);
    if (cand > timeline.latest + 12 * 3600000) cand -= 86400000;  // same slot, yesterday
    else if (cand < timeline.earliest - 12 * 3600000) cand += 86400000;
    let point = Math.min(timeline.latest, Math.max(timeline.earliest, cand));
    point = timeline.earliest + Math.round((point - timeline.earliest) / stepMs) * stepMs;
    window.__asOf = point;
    window.__asOfLatest = point === timeline.latest;
    input.value = fmt.hm(new Date(point).toISOString());
  } else {
    // Nothing chosen yet - a first paint - or the chosen point is still the
    // newest stop. Pin the control to that stop, which for the `metrics_5m`
    // path is the last precomputed timestamp, so the field shows the point the
    // headline metrics were served for instead of sitting empty while the API
    // silently resolves the newest row.
    window.__asOf = timeline.latest;
    window.__asOfLatest = true;
    if (input) input.value = fmt.hm(new Date(timeline.latest).toISOString());
  }
  renderAsOf();
}

/** The chosen point next to the input: its time and how far back that is. */
function renderAsOf() {
  const out = $("asOfOut");
  const rng = $("asOfRange");
  if (!out) return;
  const point = window.__asOf;
  if (point === null || timeline.latest === null || timeline.earliest === null) {
    out.textContent = "\u2013";
    rng.textContent = "";
    return;
  }
  const back = Math.round((timeline.latest - point) / 60000);
  // The label carries the full display-zone stamp (the window can straddle midnight),
  // plus how far behind the newest stop the point sits.
  out.textContent = `${fmt.time(new Date(point).toISOString())} ${tzAbbr()}`
    + (back > 0 ? ` · ${backLabel(back)}` : " · latest");
  // The window the field may range inside, so a time field always stays inside.
  rng.textContent = `${fmt.time(new Date(timeline.earliest).toISOString())} – ${fmt.time(new Date(timeline.latest).toISOString())} ${tzAbbr()}`;
}

/** Type a time on the 5-minute grid: parse, snap to the step and clamp to the
 * 4-hour window. A bare HH:MM carries no date, so one slot that sits past the
 * newest stop was typed yesterday, and one before the oldest stop tomorrow.
 * The field is rewritten with exactly what the analysis honours. */
function onAsOfInput() {
  if (timeline.earliest === null) return;
  const input = $("asOf");
  if (!input) return;
  const stepMs = timeline.step * 60000;
  const m = /^(\d{1,2}):(\d{2})/.exec(String(input.value || "").trim());
  if (!m) return;
  const hh = Math.min(23, Number(m[1]));
  const mm = Math.min(59, Number(m[2]));
  const { y, mo, d } = zoneDate(timeline.latest);
  let cand = wallToInstant(y, mo, d, hh, mm);
  if (cand > timeline.latest + 12 * 3600000) cand -= 86400000;   // same slot, yesterday
  else if (cand < timeline.earliest - 12 * 3600000) cand += 86400000;
  let point = Math.min(timeline.latest, Math.max(timeline.earliest, cand));
  point = timeline.earliest + Math.round((point - timeline.earliest) / stepMs) * stepMs;
  window.__asOf = point;
  window.__asOfLatest = point === timeline.latest;
  input.value = fmt.hm(new Date(point).toISOString());
  renderAsOf();
}

/** The chosen point as an ISO instant for `as_of`, or null before it exists. */
function asOfIso() {
  return window.__asOf === null ? null : new Date(window.__asOf).toISOString();
}

/**
 * The aggregate window: how many minutes the analysis runs for, ending at the
 * chosen point in time. The offered set is the fixed choices `/api/health`
 * publishes (`window_choices_minutes`), which mirror the four trailing windows
 * the precomputed `metrics_5m` table always ships - 1 h / 2 h / 3 h / 4 h - so
 * the control can never offer more history than the table precomputes, and a
 * window change is always a free, in-memory narrowing. When the API has not
 * published the fixed set (an older cached payload), fall back to building the
 * choices from the legacy `window_min_minutes`..`window_max_minutes` bounds.
 */
function fillWindows(h) {
  const sel = $("windowMinutes");
  if (!sel) return;
  const choices = Array.isArray(h && h.window_choices_minutes)
    ? h.window_choices_minutes
        .map((m) => Number(m))
        .filter((m) => Number.isFinite(m) && m > 0)
    : [];
  let opts = choices;
  if (!opts.length) {
    // Legacy fallback: every step from the minimum up to the maximum the API
    // publishes, so it can never offer more history than the cache holds.
    const step = Math.max(1, Number(h && h.timeline_step_minutes) || 5);
    const min = Math.max(1, Number(h && h.window_min_minutes) || 5);
    const max = Math.max(min,
      Number(h && h.window_max_minutes) || Number(h && h.lookback_minutes) || 240);
    opts = [];
    for (let m = min; m <= max; m += step) opts.push(m);
    if (opts[opts.length - 1] !== max) opts.push(max);
  }
  opts = [...new Set(opts)].sort((a, b) => a - b);
  const keep = Number(val("windowMinutes", 60));
  sel.innerHTML = opts
    .map((m) => `<option value="${m}">${esc(durationLabel(m))}</option>`).join("");
  // Keep the current choice if it is still offered, else default to 1 h (60),
  // else the widest on offer.
  sel.value = String(opts.includes(keep) ? keep : (opts.includes(60) ? 60 : opts[opts.length - 1]));
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
let REFRESH_MINUTES = 1;

/** Delay past each clock mark before the cycle fires, in seconds. */
let REFRESH_OFFSET_SECONDS = 15;

/** Full timestamp in the display zone: "2026-10-05 11:46:10". */
function stamp(iso) {
  if (!iso) return "\u2013";
  const p = zoneParts(iso, { year: "numeric", month: "2-digit", day: "2-digit",
                             hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return p ? `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}` : String(iso);
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

/**
 * Wall-clock time the next ingest is expected: the first clock slot the
 * cycle runs on (minute marks plus the offset, e.g. :00:15, :01:15)
 * strictly after now, in
 * local time. The cycle is scheduled against the local wall clock, so this
 * mirrors that; two refreshes separated by the full interval can never be
 * missed, and the badge never hints at a slot that has already passed.
 */
function nextRefresh(intervalMinutes, offsetSeconds) {
  const interval = Math.max(1, Number(intervalMinutes) || REFRESH_MINUTES) * 60_000;
  const offset = ((Number(offsetSeconds ?? REFRESH_OFFSET_SECONDS) || 0) * 1000) % interval;
  const now = new Date();
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0);
  const elapsed = now.getTime() - midnight.getTime() - offset;
  // The cycle lands on the next full interval slot plus the offset, never one
  // exactly at now: waiting a full interval avoids double-firing and keeps
  // the grid aligned.
  const slot = Math.floor(elapsed / interval) * interval + interval + offset;
  return new Date(midnight.getTime() + slot);
}

/**
 * Publish when the data was last updated.
 *
 * The header badge reads the feed's newest row (`health.latest_tick`), which is
 * what "Last update" means: when the pipeline last wrote, whichever point in
 * time is on screen. It falls back to `meta.latest_tick` - the newest tick the
 * analytics query returned - when the probe has nothing. The time is floored to
 * the one-minute refresh step, the grid the ingest cycle lands on, and never
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

  // Only touch className when a class is supplied (see set() above).
  set("lastTick",
      feed ? `Last update ${fmt.minute(feed)} ${tzAbbr()}` : "Last update unavailable");
  // tickAge always gets a class: it must lose a stale highlight once cleared.
  set("tickAge", ageText ? `${ageText} ago` : "",
      ageText ? (ageMs > REFRESH_MINUTES * 60_000 ? "tickage stale" : "tickage fresh") : "tickage");

  // The strip reports the loaded window; without a report there is nothing new
  // to say about the window itself, but the cycle stamps (next refresh, cache
  // write) are about the feed and must move on a health-only poll too -
  // otherwise an open tab never shows the one-minute updates.
  const next = nextRefresh(REFRESH_MINUTES, REFRESH_OFFSET_SECONDS);
  set("fbNext", next ? stamp(next.toISOString()) : "\u2013");
  // Proof the Redis cache is moving: when the refresh cycle last finished,
  // floored to the minute grid it runs on (e.g. 14:47:12 -> 14:47).
  const cacheStamp = h.cache_refreshed_at || null;
  set("fbCache", cacheStamp ? `${fmt.minute(cacheStamp)} ${tzAbbr()}` : "\u2013",
      cacheStamp ? "ok" : "");
  if (!report) return;
  set("fbLastTick", `${fmt.hm(windowLatest)} ${tzAbbr()}`,
      ageMs !== null && ageMs > REFRESH_MINUTES * 60_000 ? "warn" : "ok");

  set("fbRows", fmt.int(rows));
  const badge = $("tickAge");
  if (badge && next) {
    badge.title = `Data last updated ${fmt.hm(feed)} ${tzAbbr()}. Next ingest expected ${fmt.hm(next.toISOString())} ${tzAbbr()}, just past the minute marks.`;
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

  const chg = r.price_change.series || [];
  draw("cChg", {
    type: "line",
    data: { labels: chg.map((p) => fmt.time(p.t)),
      datasets: [{ data: chg.map((p) => p.pct), borderColor: "#4c9aff", borderWidth: 1.3, pointRadius: 0,
        fill: { target: "origin" }, backgroundColor: "rgba(76,154,255,.14)" }] },
    options: baseOpts({ scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 5 } },
      y: { grid: { color: "rgba(35,45,66,.55)" }, ticks: { callback: (v) => `${fmt.n(v, 1)}%` } } } }),
  });
  $("chg-note").textContent = r.price_change.change_pct === null ? "" :
    `Change since window start ${fmt.pct(r.price_change.change_pct, 2)}` +
    (r.price_change.reference_ts ? ` \u00b7 reference ${fmt.time(r.price_change.reference_ts)}` : "");
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
    return `Busiest ${String(top.hour).padStart(2, "0")}:00 ${tzAbbr()} \u00b7 ${fmt.int(top.ticks)} ticks`;
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
        "How much history the aggregate window actually returned, within the " +
        "cached window. Fewer ticks than expected usually means a " +
        "quiet instrument rather than a missing feed.",
        [["Rows analysed", fmt.int(r.meta && r.meta.rows_analysed)],
         ["Window", `${fmt.n(s.duration_seconds / 3600, 2)} h`],
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

  chg: (r) => {
    const c = r.price_change || {};
    const pts = c.series || [];
    return block("Reference point",
      "Every reading is the mid price's percent change from one fixed baseline: " +
      "the first tick of the window, so the path starts at 0% and moves either " +
      "side of it. Nothing is measured against a running high, so a recovery to " +
      "the opening price reads as 0% however far the path wandered in between.", [
        ["Reference", `${fmt.px(c.reference_mid)} \u00b7 ${esc(fmt.time(c.reference_ts))}`],
        ["Change since reference", fmt.pct(c.change_pct, 2)],
        ["Highest", `${fmt.pct(c.max_pct, 2)} \u00b7 ${esc(fmt.time(c.max_ts))}`],
        ["Lowest", `${fmt.pct(c.min_pct, 2)} \u00b7 ${esc(fmt.time(c.min_ts))}`],
        ["Points plotted", fmt.int(pts.length)],
      ]);
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
    const zone = (r.hourly || {}).timezone || DISPLAY_TZ;
    const busiest = hr.slice().sort((a, b) => (b.ticks || 0) - (a.ticks || 0))[0];
    const mostVol = hr.slice().sort((a, b) => (b.volatility_bps ?? -1) - (a.volatility_bps ?? -1))[0];
    const quiet = hr.slice().sort((a, b) => (a.ticks || 0) - (b.ticks || 0))[0];
    const hh = (n) => `${String(n).padStart(2, "0")}:00`;
    const rows = hr.map((h) => [hh(h.hour),
      `${fmt.int(h.ticks)} ticks \u00b7 ${fmt.n(h.volatility_bps, 2)} bps vol`]);
    return block("Daily shape",
      `Activity and directional bias by hour of day, ${zone}. The busiest hour is when the ` +
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

/** Day key in the display zone, so the calendar groups by the same zone as the
 * hourly profile and the two never disagree at a day boundary. */
function dayKey(ts) {
  const p = zoneParts(ts, { year: "numeric", month: "2-digit", day: "2-digit" });
  return p ? `${p.year}-${p.month}-${p.day}` : null;
}

/** Collapse the OHLCV bars into one record per display-zone day, for the calendar. */
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
    const hp = zoneParts(b.t, { hour: "2-digit" });
    const h = hp ? Number(hp.hour) : 0;
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
    box.innerHTML = `<p class="note">No calendar days in the selected window. Widen the window, or move the point in time later.</p>`;
    const label = $("calLabel");
    if (label) label.textContent = "\u2013";
    return;
  }
  // Default to the last day that actually carries data rather than to "today",
  // which usually falls outside a window measured in minutes.
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

/**
 * Run one analysis. Two reads go out together:
 *
 * - `/api/analytics` - the tick-derived panels: OHLCV bars, tick tape, the
 *   episode calendar and the microstructure/distribution details.
 * - `/api/metrics` - every headline metric and the volume profile, read from
 *   the precomputed `metrics_5m` grid (a Redis lookup, not a re-scan).
 *
 * Both are asked for the same symbol, window span and point in time. The
 * metrics bundle supplies only the pre-binned volume-at-price ladder
 * (`volume_profiles[selected_window_minutes]`), which is overlaid onto the
 * report's own tick-derived profile; every headline tile and panel comes from
 * `/api/analytics`, so no panel has to know which source the profile came from.
 *
 * When the metrics grid cannot be read the tick-derived profile stands in and
 * the banner says so, rather than blanking a page whose other panels are fine.
 */
async function analyse() {
  const btn = $("run");
  if (btn) btn.disabled = true;
  setLoading(true);
  try {
    // The chosen point in time. Absent until the timeline is built, which both
    // APIs read as "the newest row" / "the newest stop".
    const point = asOfIso();
    // How wide the aggregate window runs ending at the chosen point; the API
    // clamps it to the cached window anyway, so a stale page or a
    // hand-edited URL still returns data rather than a 422.
    const windowMinutes = val("windowMinutes", 60) || 60;
    const p = new URLSearchParams({
      symbol: val("symbol"),
      timeframe: val("timeframe", "1m") || "1m",
      window: val("window", 50) || 50,
      limit: val("limit", 50000) || 50000,
      window_minutes: windowMinutes,
      ...(point ? { as_of: point } : {}),
    });
    // The metrics read needs only what identifies one row of the grid: the
    // symbol, the span the `metrics_5m` table ships a column set for, and the
    // point in time.
    const mp = new URLSearchParams({
      symbol: val("symbol"),
      window_minutes: windowMinutes,
      ...(point ? { as_of: point } : {}),
    });
    // The metrics bundle failing must not take the tick panels down with it, so
    // it is caught and reported while the report still renders.
    let metricsFailed = null;
    const [r, m] = await Promise.all([
      api(`/api/analytics?${p}`),
      api(`/api/metrics?${mp}`).catch((err) => { metricsFailed = err; return null; }),
    ]);
    window.__lastReport = r;
    window.__metrics = m;
    if (m) {
      // The headline tiles are NOT overlaid from `metrics_5m`.
      //
      // Every tile on the strip is a function of the same tick frame: `ticks`,
      // `ticks_per_minute` and `avg_interarrival_ms` are three views of one
      // count over one span, and `change` / `change_pct` are the same move in
      // two units. Overlaying the scalars the table carries (`ticks`,
      // `change_pct`, `total_volume`, `realized_vol_bps`, `avg_spread`) while
      // their dependents kept their tick-derived values broke that: the two
      // bundles answer *different windows*, because `metrics_5m` snaps `as_of`
      // down to its 5-minute stop while the tick path ends at the instant asked
      // for. A strip showing `ticks` from one span beside `ticks_per_minute`
      // from another reported 6,226 ticks next to 150.8/min (9,050 ticks/hour),
      // and +0.14% next to an absolute 2.4300 whose own open is 4205.00.
      //
      // `analytics.summary` already derives every tile from one frame, so
      // reading it whole is consistent by construction - and costs nothing
      // extra: `/api/analytics` is fetched regardless for the OHLC, tick tape
      // and microstructure panels, so the summary is computed either way.
      // What `metrics_5m` genuinely saves is the volume-at-price ladder, which
      // ships pre-binned; that overlay stays.
      const profile = (m.volume_profiles || {})[String(m.selected_window_minutes)];
      if (profile) r.volume_profile = profile;
      r.metrics_source = (m.meta || {}).source || null;
    } else if (metricsFailed) {
      banner([`Headline metrics could not be read from metrics_5m: ${metricsFailed.message}`]);
    }
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
    // timeline and the window picker's bounds.
    const health = await loadHealth();
    await fillTimeframes(health);
    fillWindows(health);
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
    fillWindows(health);
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
on("asOf", "change", onAsOfInput);
on("asOf", "blur", onAsOfInput);
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
  // Re-read /api/health every minute so an open tab picks up each one-minute
  // refresh cycle: the header "Last update", the "Next refresh" and
  // "Cache updated" stamps all move without a manual reload. Health-only:
  // the loaded charts are left alone until the user re-runs.
  setInterval(async () => {
    try {
      const h = window.__health || {};
      const prevCache = h.cache_refreshed_at || null;
      const prevTick = h.latest_tick || null;
      const fresh = await loadHealth();
      renderFreshness(null);
      // The cache just turned over: pull the new window automatically so the
      // charts follow the feed instead of sitting on the old one.
      if (fresh.cache_refreshed_at && fresh.cache_refreshed_at !== prevCache) {
        await analyse();
      } else if (fresh.latest_tick && fresh.latest_tick !== prevTick) {
        renderFreshness(window.__lastReport || null);
      }
    } catch (err) { bannerErr(err.message); }
  }, 60_000);
});
