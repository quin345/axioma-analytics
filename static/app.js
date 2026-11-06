const REFRESH_MS = 5000;
const charts = {};

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} returned ${res.status}`);
  return res.json();
}

function setStatus(text) {
  document.getElementById("status").textContent = text;
}

function draw(canvasId, type, labels, datasets) {
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(document.getElementById(canvasId), {
    type,
    data: { labels, datasets },
    options: { animation: false, maintainAspectRatio: false },
  });
}

function line(label, values, color) {
  return { label, data: values, borderColor: color, pointRadius: 0, tension: 0 };
}

async function loadSymbols() {
  const symbols = await getJson("/api/symbols");
  document.getElementById("symbol").innerHTML =
    symbols.map((s) => `<option value="${s}">${s}</option>`).join("");
}

function drawSeries(series) {
  const labels = series.map((r) => new Date(r.bucket).toLocaleTimeString());

  draw("priceChart", "line", labels, [
    line("best bid", series.map((r) => r.best_bid), "#27ae60"),
    line("best ask", series.map((r) => r.best_ask), "#c0392b"),
  ]);

  draw("spreadChart", "line", labels, [
    line("rel spread", series.map((r) => r.rel_spread), "#2c3e50"),
    line("rel vwap spread", series.map((r) => r.rel_vwap_spread), "#8e44ad"),
  ]);

  draw("eventsChart", "bar", labels, [
    { label: "events per bucket", data: series.map((r) => r.events), backgroundColor: "#7f8c8d" },
  ]);
}

function drawBook(book) {
  const tbody = document.querySelector("#book tbody");
  if (!book) {
    tbody.innerHTML = "<tr><td colspan='3'>No book data yet</td></tr>";
    return;
  }
  // Highest ask at the top, best bid just below the spread.
  const asks = [...book.asks].reverse();
  const rows = [
    ...asks.map(([price, volume]) => bookRow("ask", price, volume)),
    ...book.bids.map(([price, volume]) => bookRow("bid", price, volume)),
  ];
  tbody.innerHTML = rows.join("");
}

function bookRow(side, price, volume) {
  return `<tr class="${side}"><td>${side}</td><td>${price.toFixed(5)}</td><td>${volume}</td></tr>`;
}

async function refresh() {
  const symbol = document.getElementById("symbol").value;
  const minutes = document.getElementById("minutes").value;
  if (!symbol) return;

  try {
    const [series, book] = await Promise.all([
      getJson(`/api/series?symbol=${symbol}&minutes=${minutes}&bin_seconds=10`),
      getJson(`/api/book?symbol=${symbol}`),
    ]);
    drawSeries(series);
    drawBook(book);
    setStatus(`updated ${new Date().toLocaleTimeString()}`);
  } catch (err) {
    setStatus(`error: ${err.message}`);
  }
}

document.getElementById("symbol").addEventListener("change", refresh);
document.getElementById("minutes").addEventListener("change", refresh);

loadSymbols()
  .then(refresh)
  .catch((err) => setStatus(`error: ${err.message}`));

setInterval(refresh, REFRESH_MS);