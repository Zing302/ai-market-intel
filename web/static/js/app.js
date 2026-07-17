const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const actionClass = (a) => (["buy", "sell", "hold"].includes((a || "").toLowerCase()) ? (a || "").toLowerCase() : "");

const state = { symbol: null, period: "1m", chart: null };

async function getJSON(url) {
  const r = await fetch(url);
  return r.json();
}

function stamp() {
  $("last-updated").textContent = "Updated " + new Date().toLocaleTimeString();
}

/* ── Watchlist ─────────────────────────────────────────────────────────── */

function renderWatchlist(data) {
  const el = $("watchlist");
  if (!data.success) { el.innerHTML = `<p class="loading">Market unavailable</p>`; return; }
  el.innerHTML = data.watchlist.map((t) => {
    const dir = (t.pct_change || 0) >= 0 ? "up-text" : "down-text";
    const pct = t.pct_change == null ? "—" : `${t.pct_change >= 0 ? "+" : ""}${t.pct_change}%`;
    const price = t.price == null ? "—" : `$${t.price}`;
    const active = t.symbol === state.symbol ? " active" : "";
    return `<div class="watchlist-card${active}" data-symbol="${esc(t.symbol)}">
      <div class="watchlist-symbol">${esc(t.symbol)}</div>
      <div class="watchlist-price">${price}</div>
      <div class="watchlist-change ${dir}">${pct}</div>
      <div class="watchlist-src">${esc(t.source)}</div>
    </div>`;
  }).join("");
  el.querySelectorAll(".watchlist-card").forEach((card) =>
    card.addEventListener("click", () => loadStock(card.dataset.symbol)));
}

/* ── Workspace: profile + chart + news ─────────────────────────────────── */

function renderChart(chartData) {
  const canvas = $("stock-chart");
  if (state.chart) { state.chart.destroy(); state.chart = null; }
  if (!chartData || chartData.length < 2) return;

  const ctx = canvas.getContext("2d");
  const closes = chartData.map((c) => c.close);
  const isUp = closes[closes.length - 1] >= closes[0];
  const color = isUp ? "#10b981" : "#f43f5e";
  const fill = ctx.createLinearGradient(0, 0, 0, 300);
  fill.addColorStop(0, isUp ? "rgba(16, 185, 129, 0.15)" : "rgba(244, 63, 94, 0.15)");
  fill.addColorStop(1, "rgba(0, 0, 0, 0)");

  state.chart = new Chart(ctx, {
    type: "line",
    data: {
      labels: chartData.map((c) => c.label),
      datasets: [{
        data: closes,
        borderColor: color,
        borderWidth: 2,
        pointRadius: closes.length > 50 ? 0 : 2,
        pointHoverRadius: 6,
        pointBackgroundColor: color,
        fill: true,
        backgroundColor: fill,
        tension: 0.15,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          mode: "index",
          intersect: false,
          backgroundColor: "rgba(13, 18, 36, 0.95)",
          titleColor: "#ffffff",
          bodyColor: "#e2e8f0",
          titleFont: { family: "Inter", size: 12, weight: "600" },
          bodyFont: { family: "JetBrains Mono", size: 12 },
          borderColor: "rgba(255, 255, 255, 0.08)",
          borderWidth: 1,
          displayColors: false,
          callbacks: { label: (c) => `Price: $${c.parsed.y.toFixed(2)}` },
        },
      },
      scales: {
        x: {
          grid: { color: "rgba(255, 255, 255, 0.03)" },
          ticks: { color: "#64748b", font: { family: "Inter", size: 10 }, maxTicksLimit: 8 },
        },
        y: {
          grid: { color: "rgba(255, 255, 255, 0.03)" },
          ticks: {
            color: "#64748b",
            font: { family: "JetBrains Mono", size: 10 },
            callback: (v) => "$" + v,
          },
        },
      },
    },
  });
}

function renderProfile(symbol, data) {
  $("profile-loading").classList.add("hidden");
  $("profile-content").classList.remove("hidden");
  const info = data.info || {};
  $("stock-symbol").textContent = info.symbol || symbol;
  $("stock-name").textContent = info.name || symbol;
  $("stock-price").textContent = info.price == null ? "—" : `$${info.price}`;

  const rec = data.recommendation;
  $("pipeline-rec").innerHTML = rec
    ? `<div class="rec-block ${actionClass(rec.action)}">
        <div class="rec-block-head">
          <span class="action-badge ${actionClass(rec.action)}">${esc(rec.action)} ${rec.score.toFixed(2)}</span>
          <span class="rec-meta">${esc(rec.status)} · ${esc(rec.created_at || "")}</span>
        </div>
        <div class="rec-rationale">${esc(rec.rationale || "")}</div>
      </div>`
    : `<p class="loading">No pipeline recommendation yet.</p>`;
}

function renderNews(symbol, news) {
  $("news-symbol").textContent = symbol;
  const el = $("news");
  if (!news || !news.length) { el.innerHTML = `<p class="loading">No news for ${esc(symbol)}.</p>`; return; }
  el.innerHTML = news.slice(0, 6).map((n) => {
    const title = /^https?:\/\//i.test(n.url || "")
      ? `<a class="news-link" href="${esc(n.url)}" target="_blank" rel="noopener"><span class="news-title">${esc(n.title)}</span></a>`
      : `<span class="news-title">${esc(n.title)}</span>`;
    return `<div class="news-card">
      <span class="news-publisher">${esc(n.publisher)}</span>
      ${title}
    </div>`;
  }).join("");
}

function setActiveCard(symbol) {
  document.querySelectorAll(".watchlist-card").forEach((c) =>
    c.classList.toggle("active", c.dataset.symbol === symbol));
}

async function loadStock(symbol, period) {
  state.symbol = symbol;
  state.period = period || state.period;
  setActiveCard(symbol);
  $("profile-content").classList.add("hidden");
  $("profile-loading").classList.remove("hidden");
  $("profile-loading").querySelector("p").textContent = `Fetching ${symbol}…`;

  let data;
  try {
    data = await getJSON(`/api/stock/${encodeURIComponent(symbol)}?period=${encodeURIComponent(state.period)}`);
  } catch (err) {
    data = { success: false };
  }
  if (!data.success) {
    $("profile-loading").querySelector("p").textContent = `Could not load ${symbol}.`;
    renderChart(null);
    return;
  }
  renderProfile(symbol, data);
  renderChart(data.chart);
  renderNews(symbol, data.news);
}

/* ── Recommendations + intel ───────────────────────────────────────────── */

function renderRecommendations(data) {
  const el = $("recommendations");
  if (!data.success) { el.innerHTML = `<p class="loading">Intel unavailable</p>`; return; }
  if (!data.recommendations.length) { el.innerHTML = `<p class="loading">No approved recommendations today.</p>`; return; }
  el.innerHTML = data.recommendations.map((r) => {
    const cls = actionClass(r.action);
    const chips = (r.techniques_used || "").split(",").filter(Boolean)
      .map((t) => `<span class="chip">${esc(t.trim())}</span>`).join("");
    return `<div class="rec-card ${cls}" data-symbol="${esc(r.symbol)}">
      <div class="rec-head"><span class="sym">${esc(r.symbol)}</span>
        <span class="action-badge ${cls}">${esc(r.action)} ${r.score.toFixed(2)}</span></div>
      <div class="rec-rationale">${esc(r.rationale || "")}</div>
      <div class="chips">${chips}</div>
      <div class="rec-time">${esc(r.created_at || "")}</div>
    </div>`;
  }).join("");
  el.querySelectorAll(".rec-card").forEach((card) =>
    card.addEventListener("click", () => loadStock(card.dataset.symbol)));
}

function renderIntel(data) {
  if (!data.success) {
    ["alerts", "trends", "earnings"].forEach((k) =>
      $(k).innerHTML = `<p class="loading">Intel unavailable</p>`);
    return;
  }
  $("alerts").innerHTML = data.alerts.length ? data.alerts.map((a) =>
    `<div class="intel-item"><span class="sym">${esc(a.symbol)}</span> ${esc(a.alert_type)}
     <span class="${(a.change_pct||0)>=0?'up-text':'down-text'}">${a.change_pct}%</span>
     <span class="when">${esc(a.triggered_at)}</span></div>`
  ).join("") : `<p class="loading">No alerts today.</p>`;

  $("trends").innerHTML = data.trends.length ? data.trends.map((t) =>
    `<div class="intel-item"><span class="sym">${esc(t.symbol)}</span> ${t.headline_count} AI headlines
     <span class="when">${esc(t.detected_at)}</span></div>`
  ).join("") : `<p class="loading">No AI keyword spikes.</p>`;

  $("earnings").innerHTML = data.earnings.length ? data.earnings.map((e) =>
    `<div class="intel-item"><span class="sym">${esc(e.symbol)}</span>${e.quarter ? " (" + esc(e.quarter) + ")" : ""}
     ${e.ai_capex_flag ? '<span class="flag">AI capex</span>' : ""}
     <span class="when">${esc(e.filing_date)}</span></div>`
  ).join("") : `<p class="loading">No recent earnings.</p>`;
}

/* ── Boot + events ─────────────────────────────────────────────────────── */

async function loadAll() {
  try {
    const [recs, intel, market] = await Promise.all([
      getJSON("/api/recommendations"), getJSON("/api/intel"), getJSON("/api/market"),
    ]);
    renderRecommendations(recs);
    renderIntel(intel);
    renderWatchlist(market);
    if (!state.symbol && market.success && market.watchlist.length) {
      loadStock(market.watchlist[0].symbol);
    }
  } catch (err) {
    renderRecommendations({success: false});
    renderIntel({success: false});
    renderWatchlist({success: false});
  }
  stamp();
}

$("chart-periods").addEventListener("click", (e) => {
  const btn = e.target.closest(".period-btn");
  if (!btn || !state.symbol) return;
  document.querySelectorAll(".period-btn").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  loadStock(state.symbol, btn.dataset.period);
});

$("ticker-search").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const sym = e.target.value.trim().toUpperCase();
  if (!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(sym)) return;
  e.target.value = "";
  loadStock(sym);
});

loadAll();
setInterval(async () => {
  try {
    renderWatchlist(await getJSON("/api/market"));
  } catch (err) {
    renderWatchlist({success: false});
  }
  stamp();
}, 65000);
