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
  el.innerHTML = `<b>${kind === "bad" ? "Connection problem" : "Heads up"}</b><ul>` +
    messages.map((m) => `<li>${esc(m)}</li>`).join("") + "</ul>";
  el.classList.remove("hidden");
}

function bannerErr(text) {
  banner([text], "bad");
}

/* ---------------- health + instrument coverage ---------------- */

async function loadHealth() {
  const h = await api("/api/health?refresh=true");
  const dot = $("connDot");
  if (h.connected) {
    dot.className = "dot ok";
    $("connText").textContent = h.has_data ? "Connected \u00b7 live data" : "Connected \u00b7 no data";
  } else {
    dot.className = "dot bad";
    $("connText").textContent = "Offline";
  }
  // Hints are useful even when connected (empty table, or instruments the
  // symbol dimension does not cover), so surface them in both states.
  if (h.hints && h.hints.length) banner(h.hints, h.connected ? "warn" : "bad");
  renderCoverage(h);
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
  const { groups, symbols } = await api(`/api/symbols${q}`);
  fillAssetClasses(groups);
  fillSymbols(symbols);
}

function fillSymbols(symbols) {
  const sel = $("symbol");
  const prev = sel.value;

  if (!symbols.length) {
    sel.innerHTML = '<option value="">none available</option>';
    return;
  }
  // Always pick one real symbol. Averaging across symbols would mix
  // incomparable price scales and produce meaningless statistics.
  sel.innerHTML = symbols.map((s) => {
    const id = s.symbol ?? "";
    const name = s.name || "";
    const cls = s.asset_class_label || "Unclassified";
    return `<option value="${esc(id)}">${esc(name || id)}${name ? ` (${esc(id)})` : ""} \u00b7 ${esc(cls)}</option>`;
  }).join("");
  const stillThere = symbols.some((s) => (s.symbol ?? "") === prev);
  sel.value = stillThere ? prev : (symbols[0].symbol ?? "");
}

async function fillTimeframes(h) {
  const health = h || (await api("/api/health"));
  $("timeframe").innerHTML = health.timeframes
    .map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join("");
  $("timeframe").value = "1m";
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
  // Book depth panel: only meaningful for snapshot/level sources.
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
}

function renderTicks(rows) {
  const t = $("tickTable");
  if (!rows || !rows.length) {
    t.innerHTML = `<thead></thead><tbody><tr><td class="note">No ticks in range.</td></tr></tbody>`;
    return;
  }
  const cols = ["ts", "bid", "ask", "mid", "volume", "spread_bps", "tick_dir"];
  t.innerHTML =
    `<thead><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>` +
    rows.slice().reverse().map((r) => `<tr>${cols.map((c) => {
      const v = r[c];
      if (v === null || v === undefined) return "<td>\u2013</td>";
      if (c === "ts") return `<td>${esc(fmt.time(v))}</td>`;
      if (c === "tick_dir") return `<td class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "\u25b2" : v < 0 ? "\u25bc" : "\u2013"}</td>`;
      return `<td>${fmt.num(v)}</td>`;
    }).join("")}</tr>`).join("") + "</tbody>";
}
/* ---------------- orchestration ---------------- */

async function analyse() {
  const btn = $("run");
  btn.disabled = true;
  setLoading(true);
  try {
    const p = new URLSearchParams({
      symbol: $("symbol").value || "",
      timeframe: $("timeframe").value || "1m",
      window: $("window").value || 50,
      limit: $("limit").value || 50000,
      lookback_days: $("lookback").value || 3,
    });
    const r = await api(`/api/analytics?${p}`);
    renderKpis(r.summary, r.microstructure);
    renderCharts(r);
    renderTicks(r.ticks);
    renderPriceTag(r.meta);
  } catch (err) {
    bannerErr(err.message);
    $("kpis").innerHTML =
      `<div class="kpi"><div class="label">Error</div><div class="value err">${esc(err.message)}</div></div>`;
  } finally {
    btn.disabled = false;
    setLoading(false);
  }
}

/** Name the analysed instrument and its asset class above the price chart. */
function renderPriceTag(meta) {
  const el = $("price-tag");
  if (!el || !meta) return;
  const name = (meta.symbol_name && meta.symbol_name !== meta.symbol)
    ? `${meta.symbol_name} (${meta.symbol})`
    : (meta.symbol || "");
  el.textContent = meta.asset_class_label ? `${name} \u00b7 ${meta.asset_class_label}` : name;
}

async function init() {
  try {
    // One health call, reused for the connection badge and the timeframes.
    const health = await loadHealth();
    await fillTimeframes(health);
    await loadClassSummary();
    await loadSymbols();
    renderCoverage(health);
  } catch (err) {
    bannerErr(err.message);
  }
  await analyse();
}

$("run").addEventListener("click", analyse);
// Changing the asset class re-filters the symbol list, then re-analyses.
$("assetClass").addEventListener("change", async () => {
  try { await loadSymbols(); await analyse(); }
  catch (err) { bannerErr(err.message); }
});
$("refresh").addEventListener("click", async () => {
  setLoading(true);
  try { await loadHealth(); await loadClassSummary(); await loadSymbols(); await analyse(); }
  catch (err) { bannerErr(err.message); }
  finally { setLoading(false); }
});
$("symbol").addEventListener("change", analyse);

document.addEventListener("DOMContentLoaded", init);
