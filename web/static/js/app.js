const $ = (id) => document.getElementById(id);
const actionClass = (a) => (a || "").toLowerCase();

async function getJSON(url) {
  const r = await fetch(url);
  return r.json();
}

function renderRecommendations(data) {
  const el = $("recommendations");
  if (!data.success) { el.innerHTML = `<p class="loading">Intel unavailable</p>`; return; }
  if (!data.recommendations.length) { el.innerHTML = `<p class="loading">No approved recommendations today.</p>`; return; }
  el.innerHTML = data.recommendations.map((r) => {
    const cls = actionClass(r.action);
    const chips = (r.techniques_used || "").split(",").filter(Boolean)
      .map((t) => `<span class="chip">${t.trim()}</span>`).join("");
    return `<div class="rec-card ${cls}">
      <div class="rec-head"><span>${r.symbol}</span>
        <span class="rec-action ${cls}">${r.action} ${r.score.toFixed(2)}</span></div>
      <div class="rec-rationale">${r.rationale || ""}</div>
      <div class="chips">${chips}</div>
      <div class="src" style="color:var(--muted);font-size:11px">${r.created_at || ""}</div>
    </div>`;
  }).join("");
}

function renderWatchlist(data) {
  const el = $("watchlist");
  if (!data.success) { el.innerHTML = `<p class="loading">Market unavailable</p>`; return; }
  el.innerHTML = data.watchlist.map((t) => {
    const dir = (t.pct_change || 0) >= 0 ? "up" : "down";
    const pct = t.pct_change == null ? "—" : `${t.pct_change >= 0 ? "+" : ""}${t.pct_change}%`;
    const price = t.price == null ? "—" : `$${t.price}`;
    return `<div class="tile" data-symbol="${t.symbol}">
      <div class="sym">${t.symbol}</div>
      <div class="price">${price}</div>
      <div class="${dir}">${pct}</div>
      <div class="src">${t.source}</div>
    </div>`;
  }).join("");
  el.querySelectorAll(".tile").forEach((tile) =>
    tile.addEventListener("click", () => openDetail(tile.dataset.symbol)));
}

function renderIntel(data) {
  if (!data.success) {
    ["alerts", "trends", "earnings"].forEach((k) =>
      $(k).innerHTML = `<p class="loading">Intel unavailable</p>`);
    return;
  }
  $("alerts").innerHTML = data.alerts.length ? data.alerts.map((a) =>
    `<div class="intel-item">${a.symbol} ${a.alert_type} ` +
    `<span class="${(a.change_pct||0)>=0?'up':'down'}">${a.change_pct}%</span> @ ${a.triggered_at}</div>`
  ).join("") : `<p class="loading">No alerts today.</p>`;

  $("trends").innerHTML = data.trends.length ? data.trends.map((t) =>
    `<div class="intel-item">${t.symbol} — ${t.headline_count} AI headlines (${t.detected_at})</div>`
  ).join("") : `<p class="loading">No AI keyword spikes.</p>`;

  $("earnings").innerHTML = data.earnings.length ? data.earnings.map((e) =>
    `<div class="intel-item">${e.symbol}${e.quarter ? " ("+e.quarter+")" : ""} — ${e.filing_date}` +
    `${e.ai_capex_flag ? ' <span class="flag">AI capex</span>' : ""}</div>`
  ).join("") : `<p class="loading">No recent earnings.</p>`;
}

function sparkline(chart) {
  if (!chart || chart.length < 2) return "<p class='loading'>No chart data.</p>";
  const closes = chart.map((c) => c.close);
  const min = Math.min(...closes), max = Math.max(...closes);
  const span = max - min || 1;
  const w = 600, h = 120;
  const pts = closes.map((c, i) => {
    const x = (i / (closes.length - 1)) * w;
    const y = h - ((c - min) / span) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <path d="M ${pts.join(" L ")}" /></svg>`;
}

async function openDetail(symbol) {
  const overlay = $("detail-overlay");
  const body = $("detail-body");
  body.innerHTML = `<p class="loading">Loading ${symbol}…</p>`;
  overlay.classList.remove("hidden");
  const data = await getJSON(`/api/stock/${symbol}`);
  if (!data.success) { body.innerHTML = `<p class="loading">Could not load ${symbol}.</p>`; return; }
  const info = data.info || {};
  const rec = data.recommendation;
  const recHtml = rec
    ? `<div class="detail-rec"><strong class="${actionClass(rec.action)}">${rec.action} ${rec.score.toFixed(2)}</strong>
        <span style="color:var(--muted)"> · ${rec.status} · ${rec.created_at || ""}</span>
        <div class="rec-rationale">${rec.rationale || ""}</div></div>`
    : `<p class="loading">No pipeline recommendation yet.</p>`;
  const news = (data.news || []).slice(0, 6).map((n) =>
    `<div class="news-item"><a href="${n.url}" target="_blank" rel="noopener">${n.title}</a>
      <span style="color:var(--muted)"> — ${n.publisher}</span></div>`).join("");
  body.innerHTML = `<h2>${info.name || symbol} (${info.symbol || symbol})</h2>
    <div class="price">${info.price == null ? "—" : "$" + info.price}</div>
    ${sparkline(data.chart)}
    <h3>Pipeline view</h3>${recHtml}
    <h3>News</h3>${news || "<p class='loading'>No news.</p>"}`;
}

function stamp() {
  $("last-updated").textContent = "Updated " + new Date().toLocaleTimeString();
}

async function loadAll() {
  const [recs, intel, market] = await Promise.all([
    getJSON("/api/recommendations"), getJSON("/api/intel"), getJSON("/api/market"),
  ]);
  renderRecommendations(recs);
  renderIntel(intel);
  renderWatchlist(market);
  stamp();
}

$("detail-close").addEventListener("click", () => $("detail-overlay").classList.add("hidden"));
$("detail-overlay").addEventListener("click", (e) => {
  if (e.target.id === "detail-overlay") $("detail-overlay").classList.add("hidden");
});

loadAll();
setInterval(async () => { renderWatchlist(await getJSON("/api/market")); stamp(); }, 60000);
