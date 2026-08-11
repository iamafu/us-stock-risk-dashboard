(function () {
  "use strict";

  const PERIODS = [
    { key: "daily", label: "日" },
    { key: "weekly", label: "週" },
    { key: "monthly", label: "月" },
    { key: "quarterly", label: "季" },
    { key: "yearly", label: "年" },
  ];

  const state = {
    etfs: [],
    indices: [],
    summary: null,
    berkshire: null,
    activeTickerId: null,
    activePeriod: "monthly",
    chart: null,
  };

  function radialGauge(value, size, stroke, color, trackColor) {
    const v = Math.max(0, Math.min(100, value));
    const r = (size - stroke) / 2;
    const c = size / 2;
    const circ = 2 * Math.PI * r;
    const offset = circ * (1 - v / 100);
    return (
      `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">` +
      `<circle cx="${c}" cy="${c}" r="${r}" fill="none" stroke="${trackColor}" stroke-width="${stroke}"/>` +
      `<circle cx="${c}" cy="${c}" r="${r}" fill="none" stroke="${color}" stroke-width="${stroke}" ` +
      `stroke-linecap="round" stroke-dasharray="${circ}" stroke-dashoffset="${offset}" ` +
      `transform="rotate(-90 ${c} ${c})"/>` +
      `<text x="${c}" y="${c + size * 0.09}" text-anchor="middle" font-size="${size * 0.24}" ` +
      `font-weight="700" fill="currentColor" font-family="ui-monospace,Menlo,monospace">${Math.round(v)}</text>` +
      `</svg>`
    );
  }

  function fmtPrice(p) {
    if (p == null) return "—";
    return p >= 1000
      ? p.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : p.toFixed(2);
  }

  function fmtPct(p) {
    if (p == null) return "—";
    return (p >= 0 ? "+" : "") + p.toFixed(2) + "%";
  }

  async function loadAll() {
    const [etfsRes, indicesRes, summaryRes, berkshireRes] = await Promise.allSettled([
      fetch("data/etfs.json").then((r) => r.json()),
      fetch("data/indices.json").then((r) => r.json()),
      fetch("data/summary.json").then((r) => r.json()),
      fetch("data/berkshire_13f.json").then((r) => r.json()),
    ]);

    if (etfsRes.status !== "fulfilled" || summaryRes.status !== "fulfilled") {
      throw new Error("找不到 data/etfs.json 或 data/summary.json，請先執行 scripts/backfill_history.py 與 compute_indicators.py 產生資料");
    }

    state.etfs = etfsRes.value.items || [];
    state.indices = indicesRes.status === "fulfilled" ? indicesRes.value.items || [] : [];
    state.summary = summaryRes.value;
    state.berkshire = berkshireRes.status === "fulfilled" ? berkshireRes.value : null;
  }

  function allTickers() {
    return [...state.etfs, ...state.indices];
  }

  function renderHero() {
    const s = state.summary;
    const zoneColor = s.market_temperature >= 70 ? "var(--up)" : s.market_temperature <= 30 ? "var(--down)" : "var(--accent)";
    document.getElementById("hero-gauge").innerHTML = `
      <div class="gauge-wrap gauge-num">${radialGauge(s.market_temperature, 92, 9, "var(--accent)", "var(--line)")}</div>
      <div class="txt">
        <div class="zone" style="color:${zoneColor}">市場溫度 ${s.market_temperature} · ${s.market_temperature_zone}</div>
        <div class="desc">以道瓊、那斯達克、費半三大指數的 RSI-14、200 日均線乖離率、近 3 年價格百分位、VIX 恐慌指數等權重合成；統計上這個分數能否提升報酬證據薄弱，僅供資訊參考。</div>
        <span class="multiplier-chip">建議本期扣款權重 ${s.dca_multiplier}x</span>
      </div>`;
    document.getElementById("updated-at").textContent = `資料日期 ${s.generated_at_source_date}`;
  }

  function renderTickerGrid() {
    const grid = document.getElementById("ticker-grid");
    grid.innerHTML = "";
    allTickers().forEach((t) => {
      const up = t.change_pct >= 0;
      const el = document.createElement("div");
      el.className = "ticker-card" + (t.id === state.activeTickerId ? " active" : "");
      el.dataset.id = t.id;
      el.innerHTML = `
        <div class="gauge-wrap">${radialGauge(t.composite_score, 52, 5, "var(--accent)", "var(--line)")}</div>
        <div>
          <div class="sym">${t.id}${t.proxy_note ? " *" : ""}</div>
          <div class="nm">${t.name}</div>
          <div class="pr num">${fmtPrice(t.close)}</div>
          <div class="cg num ${up ? "up" : "down"}">${fmtPct(t.change_pct)}</div>
        </div>`;
      el.addEventListener("click", () => {
        state.activeTickerId = t.id;
        renderTickerGrid();
        renderChart();
      });
      grid.appendChild(el);
    });
  }

  function renderPeriodPills() {
    const wrap = document.getElementById("period-pills");
    wrap.innerHTML = "";
    PERIODS.forEach((p) => {
      const btn = document.createElement("button");
      btn.className = "pill" + (p.key === state.activePeriod ? " on" : "");
      btn.type = "button";
      btn.textContent = p.label;
      btn.addEventListener("click", () => {
        state.activePeriod = p.key;
        renderPeriodPills();
        renderChart();
      });
      wrap.appendChild(btn);
    });
  }

  function currentTicker() {
    return allTickers().find((t) => t.id === state.activeTickerId) || allTickers()[0];
  }

  function renderChart() {
    const t = currentTicker();
    if (!t) return;
    state.activeTickerId = t.id;

    document.getElementById("chart-title").textContent = `${t.id} · ${t.name}`;
    document.getElementById("chart-sub").textContent =
      `RSI-14 ${t.rsi14} · 200 日均線乖離 z ${t.ma200_deviation_zscore ?? "—"} · 近 3 年百分位 ${t.price_percentile_3y ?? "—"}` +
      (t.proxy_note ? ` ｜ * ${t.proxy_note}` : "");

    const series = (t.timeframes && t.timeframes[state.activePeriod]) || [];
    const labels = series.map((r) => r.date);
    const closes = series.map((r) => r.close);

    const ctx = document.getElementById("price-chart").getContext("2d");
    if (state.chart) state.chart.destroy();
    state.chart = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: `${t.id} 收盤價`,
          data: closes,
          borderColor: "#e0a458",
          backgroundColor: "rgba(224,164,88,0.15)",
          borderWidth: 2,
          pointRadius: 0,
          fill: true,
          tension: 0.15,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: "#8c96a6", maxTicksLimit: 10 }, grid: { color: "#2c3543" } },
          y: { ticks: { color: "#8c96a6" }, grid: { color: "#2c3543" } },
        },
      },
    });
  }

  function renderDCA() {
    const s = state.summary;
    const dates = ["每月 5 日", "每月 15 日", "每月 25 日"];
    const panel = document.getElementById("dca-panel");
    panel.innerHTML =
      dates.map((d) => `<div class="dca-row"><span class="d">${d}</span><span class="v num">${s.dca_multiplier}x</span></div>`).join("") +
      `<div style="margin-top:10px; font-size:12px; color:var(--dim);">扣款日期為示意佔位（待確認事項 #2），倍數依當下市場溫度分數即時計算，範圍固定在 0.9x–1.1x。</div>`;
  }

  function renderBerkshire() {
    const panel = document.getElementById("berkshire-panel");
    const b = state.berkshire;
    if (!b) {
      panel.innerHTML = `<div style="color:var(--dim); font-size:13px;">尚無資料，請先執行 scripts/fetch_13f.py</div>`;
      return;
    }
    const dirClass = b.direction === "淨買超" ? "buy" : b.direction === "淨賣超" ? "sell" : "flat";
    const listItem = (h) => `<li><span class="name">${h.name}</span><span class="num">${h.change_pct != null ? fmtPct(h.change_pct) : "新進"}</span></li>`;
    panel.innerHTML = `
      <span class="direction-chip ${dirClass}">${b.direction}</span>
      <div style="font-size:12.5px; color:var(--dim);">最新申報 ${b.filing_date}（報告期 ${b.report_period_end}）</div>
      <ul class="holding-list">
        ${(b.top_increased || []).slice(0, 2).map(listItem).join("")}
        ${(b.top_new || []).slice(0, 1).map((h) => `<li><span class="name">${h.name}</span><span class="num">新進</span></li>`).join("")}
        ${(b.top_decreased || []).slice(0, 2).map(listItem).join("")}
      </ul>
      <div style="margin-top:10px; font-size:12px; color:var(--dim);">${b.note}</div>`;
  }

  async function init() {
    try {
      await loadAll();
      state.activeTickerId = state.etfs[0] ? state.etfs[0].id : null;

      renderHero();
      renderTickerGrid();
      renderPeriodPills();
      renderDCA();
      renderBerkshire();

      // 先讓容器可見，Chart.js 量測畫布尺寸時才不會拿到 display:none 的 0x0
      document.getElementById("app-status").style.display = "none";
      document.getElementById("app").style.display = "block";

      renderChart();
    } catch (err) {
      const el = document.getElementById("app-status");
      el.className = "error";
      el.textContent = "載入失敗：" + err.message;
    }
  }

  init();
})();
