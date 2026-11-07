/* BTC Quant — Testnet console.
 * No framework, no build step. Polls the local FastAPI service.
 */
(() => {
  "use strict";
  if (document.documentElement.dataset.accountMode === "live") return;


  const $ = (id) => document.getElementById(id);
  const ask = (message, options) => (window.BTCUI ? window.BTCUI.confirm(message, options) : Promise.resolve(window.confirm(message)));
  const SYMBOL = window.BTCSymbols ? window.BTCSymbols.current : "BTCUSDT";
  const BASE = window.BTCSymbols ? window.BTCSymbols.base : "BTC";
  const PAIR = window.BTCSymbols ? window.BTCSymbols.label : "BTC/USDT";
  const HKT_TZ = "Asia/Hong_Kong";
  const MA_DEFAULTS = {
    ema20: true, ema100: true, ema200: true,
    ma5: false, ma10: false, ma20: true, ma60: false,
  };
  const state = {
    status: null,
    config: null,
    candles: null,
    interval: "4h",
    modelInterval: "4h",
    historyLimit: 800,
    chart: null,
    series: {},
    maData: {},
    ma: { ...MA_DEFAULTS },
    chartReady: false,
    chartFit: false,
    lastClose: null,
    priceLine: null,
    firstStatus: true,
    autoBusy: false,
    orderBusy: false,
    draw: {
      tool: "select",
      items: [],
      draft: null,
      canvas: null,
      ctx: null,
      w: 0,
      h: 0,
      pointerId: null,
    },
  };

  const money = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const priceFmt = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function fmtMoney(v) {
    return v === null || v === undefined || Number.isNaN(Number(v)) ? "—" : money.format(Number(v));
  }
  function fmtPrice(v) {
    return v === null || v === undefined || Number.isNaN(Number(v)) ? "—" : priceFmt.format(Number(v));
  }
  function fmtQty(v) {
    if (v === null || v === undefined) return "—";
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return n.toFixed(8).replace(/0+$/, "").replace(/\.$/, "") || "0";
  }
  function fmtPct(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    const n = Number(v) * 100;
    return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
  }
  function fmtSigned(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    return `${n >= 0 ? "+" : ""}${money.format(n)}`;
  }
  function pnlClass(v) {
    const n = Number(v);
    if (!Number.isFinite(n) || n === 0) return "";
    return n > 0 ? "positive" : "negative";
  }
  function timeShort(value) {
    if (!value) return "—";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleTimeString("zh-CN", { timeZone: HKT_TZ, hour12: false });
  }
  function dateTime(value) {
    if (!value) return "—";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleString("zh-CN", {
      timeZone: HKT_TZ,
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
    });
  }
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
    }[c]));
  }

  let toastTimer = null;
  function toast(message, kind = "") {
    const el = $("toast");
    el.textContent = message;
    el.className = `toast show ${kind}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.className = "toast"; }, 4200);
  }

  async function fetchJSON(url, options = {}) {
    if (url.startsWith("/api/")) {
      const u = new URL(url, window.location.origin);
      u.searchParams.set("symbol", SYMBOL);
      url = `${u.pathname}${u.search}`;
    }
    const res = await fetch(url, { cache: "no-store", ...options });
    const text = await res.text();
    let payload = null;
    try { payload = text ? JSON.parse(text) : null; } catch (_) { /* text response */ }
    if (!res.ok) {
      const detail = payload && payload.detail ? payload.detail : (text || `HTTP ${res.status}`);
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return payload;
  }

  // ------------------------------------------------------------------ chart
  const INTERVAL_SECONDS = {
    "30s":30,"1m":60,"3m":180,
    "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
    "8h": 28800, "12h": 43200, "1d": 86400,
  };

  function hktParts(timestamp) {
    const d = new Date(Number(timestamp) * 1000);
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: HKT_TZ,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      hourCycle: "h23",
    }).formatToParts(d);
    return Object.fromEntries(parts.map((p) => [p.type, p.value]));
  }

  function formatChartTime(timestamp) {
    const p = hktParts(timestamp);
    return `${p.month}-${p.day} ${p.hour}:${p.minute}${state.interval==='30s'?':'+p.second:''}`;
  }

  function formatTickMark(time, tickMarkType) {
    const p = hktParts(time);
    if (tickMarkType === 0) return p.year;
    if (tickMarkType === 1) return `${Number(p.month)}月`;
    if (tickMarkType === 2) return `${Number(p.day)}日`;
    if (tickMarkType === 4) return `${p.hour}:${p.minute}:${p.second}`;
    return `${p.hour}:${p.minute}`;
  }

  function updateNextBar() {
    const span = $("nextBar");
    if (!span) return;
    const seconds = INTERVAL_SECONDS[state.interval] || 14400;
    const period = seconds * 1000;
    const now = Date.now();
    const next = Math.ceil(now / period) * period;
    const diff = Math.max(0, next - now);
    const h = Math.floor(diff / 3600000);
    const m = Math.floor((diff % 3600000) / 60000);
    const s = Math.floor((diff % 60000) / 1000);
    const clock = new Date(next).toLocaleTimeString("zh-CN", {
      timeZone: HKT_TZ, hour: "2-digit", minute: "2-digit", hour12: false,
    });
    span.textContent = `下一根 ${state.interval.toUpperCase()}: ${clock} HKT · ${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")} 后`;
  }

  function chartColors() {
    const light = document.documentElement.dataset.theme === "light";
    return light
      ? { bg: "#ffffff", text: "#526273", grid: "#e8eef4", border: "#d7e0e9", cross: "#8fa2b3", mark: "#26384a" }
      : { bg: "#0b1118", text: "#7f8c9b", grid: "#131f2a", border: "#1a2733", cross: "#3b536a", mark: "#e7edf5" };
  }

  function chartFontSize() {
    const scale = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--font-scale")) || 1;
    return Math.max(9, Math.round(10 * scale));
  }

  function applyChartTheme() {
    if (!state.chart) return;
    const C = chartColors();
    state.chart.applyOptions({
      layout: { background: { color: C.bg }, textColor: C.text, fontSize: chartFontSize() },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      crosshair: { vertLine: { color: C.cross }, horzLine: { color: C.cross } },
      rightPriceScale: { borderColor: C.border },
      timeScale: { borderColor: C.border },
    });
    if (state.priceLine) state.priceLine.applyOptions({ color: C.mark });
  }

  function updateLivePriceLine(price) {
    if (!state.priceLine) return;
    const value = Number(price);
    if (!Number.isFinite(value) || value <= 0) return;
    const reference = Number(state.lastClose);
    const color = Number.isFinite(reference) && reference > 0
      ? (value >= reference ? "#16c784" : "#ea3943")
      : "#e7edf5";
    try {
      state.priceLine.applyOptions({ price: value, color, title: "LIVE" });
    } catch (_) { /* series may not be ready yet */ }
  }

  function initChart() {
    const el = $("chart");
    if (!el || !window.LightweightCharts) {
      $("chartStatus").textContent = "图表库加载失败";
      return;
    }
    const LC = window.LightweightCharts;
    const C = chartColors();
    const chart = LC.createChart(el, {
      width: el.clientWidth,
      height: el.clientHeight,
      layout: {
        background: { type: LC.ColorType ? LC.ColorType.Solid : "solid", color: C.bg },
        textColor: C.text,
        fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
        fontSize: chartFontSize(),
      },
      localization: {
        locale: "zh-CN",
        timeFormatter: formatChartTime,
      },
      grid: {
        vertLines: { color: C.grid },
        horzLines: { color: C.grid },
      },
      crosshair: {
        mode: LC.CrosshairMode ? LC.CrosshairMode.Normal : 0,
        vertLine: { color: C.cross, width: 1, style: 2, labelBackgroundColor: C.border },
        horzLine: { color: C.cross, width: 1, style: 2, labelBackgroundColor: C.border },
      },
      rightPriceScale: {
        borderColor: C.border,
        scaleMargins: { top: 0.08, bottom: 0.08 },
      },
      timeScale: {
        borderColor: C.border,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 6,
        barSpacing: 7,
        tickMarkFormatter: formatTickMark,
      },
      handleScroll: { vertTouchDrag: false },
    });
    state.chart = chart;
    state.series.candles = chart.addCandlestickSeries({
      upColor: "#16c784",
      downColor: "#ea3943",
      borderUpColor: "#16c784",
      borderDownColor: "#ea3943",
      wickUpColor: "#16c784",
      wickDownColor: "#ea3943",
      priceLineVisible: false,
      lastValueVisible: false,
    });
    state.priceLine = state.series.candles.createPriceLine({
      price: 0,
      color: "#e7edf5",
      lineWidth: 1,
      lineStyle: LC.LineStyle ? LC.LineStyle.Dashed : 2,
      axisLabelVisible: true,
      title: "LIVE",
    });
    state.series.ema20 = chart.addLineSeries({
      color: "#f0b90b", lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    state.series.ema100 = chart.addLineSeries({
      color: "#5b8def", lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    state.series.ema200 = chart.addLineSeries({
      color: "#b06ab3", lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    state.series.ma5 = chart.addLineSeries({
      color: "#22d3ee", lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    state.series.ma10 = chart.addLineSeries({
      color: "#f472b6", lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    state.series.ma20 = chart.addLineSeries({
      color: "#94a3b8", lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    state.series.ma60 = chart.addLineSeries({
      color: "#fb923c", lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    chart.subscribeCrosshairMove((param) => {
      if (!param || !param.time || !param.seriesData) {
        const last = state.candles && state.candles.candles[state.candles.candles.length - 1];
        if (last) updateOHLC(last);
        return;
      }
      const bar = param.seriesData.get(state.series.candles);
      if (bar) updateOHLC(bar);
    });

    if (window.ResizeObserver) {
      new ResizeObserver(() => {
        chart.applyOptions({ width: el.clientWidth, height: el.clientHeight });
        resizeDrawCanvas();
      }).observe(el);
    }
    state.chartReady = true;
    setupDrawing();
    setupMaControls();
    state.volume=window.BTCVolumePane?.create({chart,series:state.series.candles,wrap:el});
    document.addEventListener("btcquant:ui", applyChartTheme);
  }

  function updateOHLC(bar) {
    if (!bar) return;
    $("oVal").textContent = fmtPrice(bar.open);
    $("hVal").textContent = fmtPrice(bar.high);
    $("lVal").textContent = fmtPrice(bar.low);
    $("cVal").textContent = fmtPrice(bar.close);
    $("cVal").className = Number(bar.close) >= Number(bar.open) ? "positive" : "negative";
  }

  async function loadCandles(forceFit = false) {
    if (!state.chartReady) return;
    const request=state.candleRequest=(state.candleRequest||0)+1;
    $("chartStatus").textContent=state.interval==='30s'?'正在聚合真实 1 秒数据；首次加载 30 秒历史可能需要较长时间…':'正在加载 '+state.interval.toUpperCase()+' K 线…';
    if(document.documentElement.classList.contains('pane-mode')){const url=new URL(location.href);url.searchParams.set('interval',state.interval);history.replaceState(null,'',url);}
    try {
      const data = await fetchJSON(`/api/candles?interval=${encodeURIComponent(state.interval)}&limit=${state.historyLimit}`);
      if(request!==state.candleRequest)return;
      state.chart.applyOptions({timeScale:{secondsVisible:state.interval==='30s'}});
      state.candles = data;
      const bars = (data.candles || []).map((c) => ({
        time: c.time, open: c.open, high: c.high, low: c.low, close: c.close,
      }));
      state.series.candles.setData(bars);
      state.volume?.setData(data.candles || [],state.interval);
      state.maData = {
        ema20: data.ema20 || [],
        ema100: data.ema100 || [],
        ema200: data.ema200 || [],
        ma5: data.ma5 || [],
        ma10: data.ma10 || [],
        ma20: data.ma20 || [],
        ma60: data.ma60 || [],
      };
      applyMaVisibility();
      state.series.candles.setMarkers((data.markers || []).map((m) => ({
        time: m.time, position: m.position, color: m.color, shape: m.shape, text: m.text,
      })));
      if (!state.chartFit || forceFit) {
        state.chart.timeScale().fitContent();
        state.chartFit = true;
      }
      if (bars.length) {
        const last = bars[bars.length - 1];
        state.lastClose = last.close;
        updateOHLC(last);
        updateChangeFromBars(bars);
        const live = state.status && Number(state.status.price);
        updateLivePriceLine(live > 0 ? live : last.close);
        if (!state.status || state.status.price === null || state.status.price === undefined) {
          $("price").textContent = fmtPrice(last.close);
        }
        const lastLabel = new Date(last.time * 1000).toLocaleString("zh-CN", {
          timeZone: HKT_TZ,
          month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
        });
        $("chartStatus").textContent = `${data.symbol || SYMBOL} · ${data.interval || state.interval} · ${bars.length} · 最后一根 ${lastLabel} HKT · ${data.data_source || "data"} signal data`;
      }
      $("lastUpdate").textContent = `chart ${timeShort(new Date().toISOString())}`;
      redrawDrawings();
    } catch (err) {
      if(request!==state.candleRequest)return;
      $("chartStatus").textContent = `行情加载失败：${err.message}`;
    }
  }

  function updateChangeFromBars(bars) {
    if (!bars.length) return;
    const last = bars[bars.length - 1];
    const target = last.time - 86400;
    let ref = bars[0];
    for (const b of bars) {
      if (b.time <= target) ref = b; else break;
    }
    if (ref && ref.close) {
      const pct = (last.close / ref.close - 1) * 100;
      const el = $("change");
      el.textContent = `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}% 24H`;
      el.className = `change ${pct >= 0 ? "up" : "down"}`;
    }
  }

  // -------------------------------------------------------------- MA lines
  function applyMaVisibility() {
    Object.keys(MA_DEFAULTS).forEach((key) => {
      const series = state.series[key];
      if (!series) return;
      const show = !!state.ma[key];
      series.setData(show ? (state.maData[key] || []) : []);
      const legend = $(`legend-${key}`);
      if (legend) legend.classList.toggle("hidden", !show);
    });
  }

  function saveMaSettings() {
    try { localStorage.setItem("btcquant.ma.v1", JSON.stringify(state.ma)); } catch (_) { /* ignore */ }
  }

  function setupMaControls() {
    try {
      const saved = JSON.parse(localStorage.getItem("btcquant.ma.v1") || "{}");
      state.ma = { ...MA_DEFAULTS, ...saved };
    } catch (_) {
      state.ma = { ...MA_DEFAULTS };
    }
    document.querySelectorAll("#maPanel input[data-ma]").forEach((input) => {
      input.checked = !!state.ma[input.dataset.ma];
      input.addEventListener("change", () => {
        state.ma[input.dataset.ma] = input.checked;
        saveMaSettings();
        applyMaVisibility();
      });
    });
    const panel = $("maPanel");
    $("maBtn").addEventListener("click", (event) => {
      event.stopPropagation();
      panel.classList.toggle("hidden");
    });
    document.addEventListener("click", (event) => {
      if (!event.target.closest(".ma-menu")) panel.classList.add("hidden");
    });
    applyMaVisibility();
  }

  // ------------------------------------------------------------- drawings
  const DRAW_PREFIX = "btcquant.drawings.v1";

  function drawingStorageKey() {
    const symbol = SYMBOL;
    return `${DRAW_PREFIX}.${symbol}.${state.interval}`;
  }

  function loadDrawings() {
    try {
      const raw = localStorage.getItem(drawingStorageKey());
      const parsed = raw ? JSON.parse(raw) : [];
      state.draw.items = Array.isArray(parsed) ? parsed : [];
    } catch (_) {
      state.draw.items = [];
    }
    state.draw.draft = null;
    redrawDrawings();
  }

  function saveDrawings() {
    try {
      localStorage.setItem(drawingStorageKey(), JSON.stringify(state.draw.items));
    } catch (_) { /* private mode / quota */ }
  }

  function toXY(point) {
    if (!point || !state.chart) return null;
    const x = window.BTCDrawingCoordinates.toX(state.chart,state.candles?.candles || [],state.interval,point.time);
    const y = state.series.candles.priceToCoordinate(point.price);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return { x, y };
  }

  function priceToY(price) {
    const y = state.series.candles.priceToCoordinate(price);
    return Number.isFinite(y) ? y : null;
  }

  function resizeDrawCanvas() {
    const wrap = $("chartWrap");
    const canvas = $("drawCanvas");
    if (!wrap || !canvas) return;
    const rect = wrap.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(rect.width));
    const h = Math.max(1, Math.round(rect.height));
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    state.draw.canvas = canvas;
    state.draw.ctx = ctx;
    state.draw.w = w;
    state.draw.h = h;
    redrawDrawings();
  }

  function drawItem(ctx, item, draft) {
    if (!item || !item.type) return;
    const color = item.color || (item.type === "pen" ? "#22d3ee" : "#f0b90b");
    ctx.save();
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = draft ? 1.5 : 2;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    if (draft) ctx.setLineDash([5, 4]);

    if (item.type === "hline") {
      const y = priceToY(item.price);
      if (y === null) { ctx.restore(); return; }
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(state.draw.w, y);
      ctx.stroke();
      ctx.setLineDash([]);
      const text = fmtPrice(item.price);
      ctx.font = "10px SFMono-Regular, Consolas, monospace";
      const tw = ctx.measureText(text).width;
      ctx.fillStyle = "rgba(240,185,11,.94)";
      ctx.fillRect(state.draw.w - tw - 18, y - 9, tw + 12, 18);
      ctx.fillStyle = "#081018";
      ctx.fillText(text, state.draw.w - tw - 12, y + 3);
    } else if (item.type === "trend") {
      const a = toXY(item.points && item.points[0]);
      const b = toXY(item.points && item.points[1]);
      if (!a || !b) { ctx.restore(); return; }
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
      if (!draft) {
        [a, b].forEach((p) => {
          ctx.beginPath();
          ctx.arc(p.x, p.y, 3.2, 0, Math.PI * 2);
          ctx.fill();
        });
      }
    } else if (item.type === "pen") {
      const points = item.points || [];
      let started = false;
      ctx.beginPath();
      points.forEach((p) => {
        const q = toXY(p);
        if (!q) { started = false; return; }
        if (!started) { ctx.moveTo(q.x, q.y); started = true; }
        else ctx.lineTo(q.x, q.y);
      });
      ctx.stroke();
    }
    ctx.restore();
  }

  function redrawDrawings() {
    const d = state.draw;
    if(d.canvas)d.canvas.dataset.drawingCount=String(d.items.length);
    if (!d.ctx || !state.chart) return;
    d.ctx.clearRect(0, 0, d.w, d.h);
    d.items.forEach((item) => drawItem(d.ctx, item, false));
    if (d.draft) drawItem(d.ctx, d.draft, true);
  }

  function pointFromEvent(event) {
    const canvas = state.draw.canvas;
    if (!canvas || !state.chart) return null;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const time = window.BTCDrawingCoordinates.fromX(state.chart,state.candles?.candles || [],state.interval,x);
    const price = state.series.candles.coordinateToPrice(y);
    if (time === null || time === undefined || typeof time === "object") return null;
    if (!Number.isFinite(price)) return null;
    return { time, price };
  }

  function distanceToSegment(px, py, a, b) {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    if (dx === 0 && dy === 0) return Math.hypot(px - a.x, py - a.y);
    const t = Math.max(0, Math.min(1, ((px - a.x) * dx + (py - a.y) * dy) / (dx * dx + dy * dy)));
    return Math.hypot(px - (a.x + t * dx), py - (a.y + t * dy));
  }

  function hitTest(x, y) {
    for (let i = state.draw.items.length - 1; i >= 0; i -= 1) {
      const item = state.draw.items[i];
      if (item.type === "hline") {
        const py = priceToY(item.price);
        if (py !== null && Math.abs(y - py) <= 9) return i;
      } else if (item.type === "trend") {
        const a = toXY(item.points && item.points[0]);
        const b = toXY(item.points && item.points[1]);
        if (a && b && distanceToSegment(x, y, a, b) <= 9) return i;
      } else if (item.type === "pen") {
        const pts = item.points || [];
        for (let j = 1; j < pts.length; j += 1) {
          const a = toXY(pts[j - 1]);
          const b = toXY(pts[j]);
          if (a && b && distanceToSegment(x, y, a, b) <= 9) return i;
        }
      }
    }
    return -1;
  }

  function setDrawTool(tool) {
    state.draw.tool = tool;
    state.draw.draft = null;
    document.querySelectorAll(".draw-bar [data-tool]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tool === tool);
    });
    const active = tool !== "select";
    if (state.draw.canvas) state.draw.canvas.classList.toggle("active", active);
    if (state.chart) {
      state.chart.applyOptions({ handleScroll: !active, handleScale: !active });
    }
    const tips = {
      select: "选择/缩放；右侧空白区也支持绘图",
      trend: "拖动两个点画趋势线",
      hline: "点击放置水平线",
      pen: "按住鼠标涂鸦，可画到右侧未来区域",
      erase: "点击线条删除",
    };
    $("drawTip").textContent = tips[tool] || "";
    redrawDrawings();
  }

  function finishDraft(event) {
    const d = state.draw;
    if (!d.draft) return;
    if(event && Number.isFinite(event.clientX)){const p=pointFromEvent(event);if(p){if(d.draft.type==='trend')d.draft.points[1]=p;else d.draft.points.push(p);}}
    const draft = d.draft;
    d.draft = null;
    if (d.pointerId !== null && d.canvas && d.canvas.hasPointerCapture(d.pointerId)) {
      try { d.canvas.releasePointerCapture(d.pointerId); } catch (_) { /* ignore */ }
    }
    d.pointerId = null;
    if (draft.type === "trend") {
      const a = toXY(draft.points[0]);
      const b = toXY(draft.points[1]);
      if (a && b && Math.hypot(a.x - b.x, a.y - b.y) > 5) d.items.push(draft);
    } else if (draft.type === "pen" && draft.points.length > 1) {
      d.items.push(draft);
    }
    saveDrawings();
    redrawDrawings();
  }

  function setupDrawing() {
    const canvas = $("drawCanvas");
    if (!canvas || !state.chart) return;
    resizeDrawCanvas();
    loadDrawings();
    setDrawTool("select");
    const space = document.createElement("select");
    space.className="future-space";space.setAttribute("aria-label","右侧绘图留白");
    space.innerHTML='<option value="20">留白 20 根</option><option value="80" selected>留白 80 根</option><option value="160">留白 160 根</option><option value="320">留白 320 根</option>';
    document.querySelector('.draw-bar').append(space);
    space.addEventListener('change',()=>state.chart.applyOptions({timeScale:{rightOffset:Number(space.value)}}));
    state.chart.applyOptions({timeScale:{rightOffset:80}});
    if (window.ResizeObserver) new ResizeObserver(resizeDrawCanvas).observe($("chartWrap"));
    setInterval(redrawDrawings, 100);

    document.querySelectorAll(".draw-bar [data-tool]").forEach((btn) => {
      btn.addEventListener("click", () => setDrawTool(btn.dataset.tool));
    });
    $("undoDraw").addEventListener("click", () => {
      state.draw.items.pop();
      saveDrawings();
      redrawDrawings();
    });
    $("clearDraw").addEventListener("click", async () => {
      if (!state.draw.items.length) return;
      if (!(await ask("清空当前周期的所有划线？", { title: "清空划线", danger: true }))) return;
      state.draw.items = [];
      saveDrawings();
      redrawDrawings();
    });

    canvas.addEventListener("pointerdown", (event) => {
      if (state.draw.tool === "select") return;
      event.preventDefault();
      const point = pointFromEvent(event);
      if (!point) {
        toast("这里没有有效价格，换一个位置试试", "error");
        return;
      }
      if (state.draw.tool === "erase") {
        const rect = canvas.getBoundingClientRect();
        const idx = hitTest(event.clientX - rect.left, event.clientY - rect.top);
        if (idx >= 0) {
          state.draw.items.splice(idx, 1);
          saveDrawings();
          redrawDrawings();
        }
        return;
      }
      if (state.draw.tool === "hline") {
        state.draw.items.push({ type: "hline", price: point.price, color: "#f0b90b" });
        saveDrawings();
        redrawDrawings();
        return;
      }
      state.draw.draft = {
        type: state.draw.tool,
        points: state.draw.tool === "pen" ? [point] : [point, point],
        color: state.draw.tool === "pen" ? "#22d3ee" : "#f0b90b",
      };
      state.draw.pointerId = event.pointerId;
      try { canvas.setPointerCapture(event.pointerId); } catch (_) { /* ignore */ }
      redrawDrawings();
    }, { passive: false });

    canvas.addEventListener("pointermove", (event) => {
      if (!state.draw.draft) return;
      const point = pointFromEvent(event);
      if (!point) return;
      if (state.draw.draft.type === "trend") {
        state.draw.draft.points[1] = point;
      } else {
        const points = state.draw.draft.points;
        const last = points[points.length - 1];
        const a = toXY(last);
        const b = toXY(point);
        if (!a || !b || Math.hypot(a.x - b.x, a.y - b.y) > 1.5) {
          points.push(point);
          if (points.length > 1800) points.shift();
        }
      }
      redrawDrawings();
    });

    canvas.addEventListener("pointerup", finishDraft);
    canvas.addEventListener("pointercancel", finishDraft);
    canvas.addEventListener("pointerleave", (event) => {
      if (state.draw.draft && event.buttons === 0) finishDraft();
    });

    window.addEventListener("keydown", (event) => {
      const tag = (event.target && event.target.tagName) || "";
      if (event.key === "Escape") {
        $("maPanel").classList.add("hidden");
        if (tag !== "INPUT" && tag !== "TEXTAREA") setDrawTool("select");
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z" && tag !== "INPUT" && tag !== "TEXTAREA") {
        if (!state.draw.items.length) return;
        event.preventDefault();
        state.draw.items.pop();
        saveDrawings();
        redrawDrawings();
      }
    });
  }

  // ----------------------------------------------------------------- status
  function updateSignal(signal, interval) {
    const entry = !!signal.entry_signal;
    const exit = !!signal.exit_signal;
    const sigBadge = $("signalBadge");
    if (entry && !exit) {
      sigBadge.textContent = "多头条件成立";
      sigBadge.className = "mini-badge green";
    } else if (exit) {
      sigBadge.textContent = Number(state.status?.account?.position_qty || 0) > 0 ? "退出条件成立" : "多头条件未满足";
      sigBadge.className = "mini-badge red";
    } else {
      sigBadge.textContent = "观望";
      sigBadge.className = "mini-badge neutral";
    }
    $("emaFast").textContent = fmtPrice(signal.ema_fast);
    $("emaSlow").textContent = fmtPrice(signal.ema_slow);
    $("emaRegime").textContent = fmtPrice(signal.ema_regime);
    $("adx").textContent = signal.adx14 !== undefined ? Number(signal.adx14).toFixed(1) : "—";
    $("atr").textContent = fmtPrice(signal.atr14);
    $("strategyEyebrow").textContent = `STRATEGY / ${String(interval).toUpperCase()}`;
    $("signalHint").textContent = entry && !exit
      ? `EMA 多头排列 + 价格在 EMA200 上方 + ADX 达标；等待 ${interval.toUpperCase()} 收盘后执行。`
      : exit
        ? "趋势转弱或价格低于 EMA200；这是退出条件。空仓时保持观望，只有开启 AUTO 才会自动执行。"
        : "当前不满足入场条件，策略保持空仓。空仓也是仓位。";
  }

  function renderStatus(s) {
    window.dispatchEvent(new CustomEvent("btcquant:spot",{detail:s}));
    const account = s.account || {};
    const signal = s.signal || {};
    const mode = s.mode || "paper";
    const connected = !!s.connected;

    const badge = $("modeBadge");
    badge.textContent = mode === "testnet" ? "TESTNET" : "PAPER";
    badge.className = `badge ${mode === "testnet" ? (connected ? "badge-testnet" : "badge-error") : "badge-paper"}`;
    $("connectionDot").className = `dot ${connected ? "ok" : "bad"}`;
    $("reconnectBtn").classList.toggle("hidden", !(mode === "testnet" && !connected));
    $("accountMode").textContent = mode === "testnet" ? "TESTNET" : "PAPER";
    $("accountMode").className = `mini-badge ${mode === "testnet" ? "green" : "amber"}`;

    if (s.price !== null && s.price !== undefined) {
      $("price").textContent = fmtPrice(s.price);
      updateLivePriceLine(s.price);
    }

    $("equity").innerHTML = `${fmtMoney(account.equity)} <small>USDT</small>`;
    const pnl = account.session_pnl;
    $("sessionPnl").textContent = fmtSigned(pnl);
    $("sessionPnl").className = pnlClass(pnl);
    $("usdtFree").textContent = fmtMoney(account.usdt_free);
    $("btcTotal").textContent = `${fmtQty(account.btc_total)} ${BASE}`;
    $("unrealized").textContent = fmtSigned(account.unrealized_pnl);
    $("unrealized").className = pnlClass(account.unrealized_pnl);
    $("realized").textContent = fmtSigned(account.realized_pnl);
    $("realized").className = pnlClass(account.realized_pnl);

    const qty = Number(account.position_qty || 0);
    const isLong = qty > 0.00000001;
    const canTrade = mode === "paper" || connected;
    $("buyBtn").disabled = !canTrade || state.orderBusy;
    $("positionSide").textContent = isLong ? "LONG" : "FLAT";
    $("positionPill").textContent = `${fmtQty(qty)} ${BASE}`;
    $("positionPill").className = `mini-badge ${isLong ? "green" : "neutral"}`;
    $("posQty").textContent = fmtQty(qty);
    $("posEntry").textContent = isLong ? fmtPrice(account.entry_price) : "—";
    $("sellBtn").disabled = !canTrade || !isLong || state.orderBusy;

    if (state.modelInterval === (s.auto?.interval || "4h")) updateSignal(signal,s.auto?.interval || "4h");

    $("autoToggle").checked = !!s.auto_enabled;
    $("pollLabel").textContent = `POLL ${s.poll_seconds || 5}s`;
    $("strategyLabel").textContent = `${s.strategy || "trend"} · ${s.params ? s.params.fast + "/" + s.params.slow : ""}`;
    $("lastUpdate").textContent = s.last_update ? `api ${timeShort(s.last_update)}` : "—";

    if (mode === "paper") {
      $("setupBar").classList.remove("hidden");
      $("resetBtn").classList.remove("hidden");
    } else {
      $("setupBar").classList.add("hidden");
      $("resetBtn").classList.add("hidden");
    }
    if (s.error) {
      $("alertBar").textContent = `服务错误：${s.error}`;
      $("alertBar").classList.remove("hidden");
    } else {
      $("alertBar").classList.add("hidden");
    }

    renderOrders(s.orders || []);
    renderEvents(s.events || []);
  }

  function renderOrders(orders) {
    const body = $("ordersBody");
    const visible = orders.filter((o) => o.side !== "ADOPT").slice(0, 16);
    $("orderCount").textContent = String(visible.length);
    if (!visible.length) {
      body.innerHTML = `<tr class="empty-row"><td colspan="7">暂无成交</td></tr>`;
      return;
    }
    body.innerHTML = visible.map((o) => {
      const side = String(o.side || "").toUpperCase();
      const sideCls = side === "BUY" ? "side-buy" : side === "SELL" ? "side-sell" : "side-adopt";
      const pnl = o.net_pnl !== undefined && o.net_pnl !== null
        ? `<span class="trade-pnl ${pnlClass(o.net_pnl)}">${fmtSigned(o.net_pnl)}</span>` : "—";
      return `<tr>
        <td>${dateTime(o.time)}</td>
        <td class="${sideCls}">${escapeHtml(side)}</td>
        <td>${fmtPrice(o.price)}</td>
        <td>${fmtQty(o.qty)}</td>
        <td>${fmtMoney(o.quote)}</td>
        <td>${fmtMoney(o.fee)}</td>
        <td>${escapeHtml(o.reason || o.status || "")} ${pnl !== "—" ? pnl : ""}</td>
      </tr>`;
    }).join("");
  }

  function renderEvents(events) {
    const list = $("eventsList");
    const visible = events.slice(-24).reverse();
    if (!visible.length) {
      list.innerHTML = `<div class="event-item"><span class="event-time">--:--:--</span><span>暂无事件</span></div>`;
      return;
    }
    list.innerHTML = visible.map((e) => `
      <div class="event-item ${escapeHtml(e.level || "")}">
        <span class="event-time">${timeShort(e.time)}</span>
        <span>${escapeHtml(e.message || "")}</span>
      </div>`).join("");
  }

  // ------------------------------------------------------------------ boot
  async function refreshStatus() {
    try {
      const s = await fetchJSON("/api/status");
      state.status = s;
      renderStatus(s);
      if (state.firstStatus) {
        state.firstStatus = false;
        setTimeout(() => $("loading").classList.add("hide"), 160);
      }
    } catch (err) {
      $("alertBar").textContent = `无法连接本地服务：${err.message}`;
      $("alertBar").classList.remove("hidden");
      $("connectionDot").className = "dot bad";
      if (state.firstStatus) {
        state.firstStatus = false;
        setTimeout(() => $("loading").classList.add("hide"), 160);
      }
    }
  }

  async function loadConfig() {
    try {
      const c = await fetchJSON("/api/config");
      state.config = c;
      if (c.quote_amount) $("quoteAmount").value = String(c.quote_amount);
      $("pollLabel").textContent = `POLL ${c.poll_seconds || 5}s`;
      $("strategyLabel").textContent = `${c.strategy || "trend"} · ${c.params ? c.params.fast + "/" + c.params.slow : ""}`;
    } catch (_) { /* status will show errors */ }
  }

  async function placeOrder(side) {
    if (state.orderBusy) return;
    if (state.status?.symbol && state.status.symbol !== SYMBOL) {
      toast("交易对状态不一致，请刷新页面", "error");
      return;
    }
    const amount = Number($("quoteAmount").value);
    const isPaper = state.status && state.status.mode === "paper";
    if (side === "BUY" && (!Number.isFinite(amount) || amount < 5)) {
      toast("买入金额至少 5 USDT", "error");
      return;
    }
    const prompt = side === "BUY"
      ? `确认${isPaper ? "本地模拟" : " Binance Testnet "}市价买入 ${amount} USDT 的 ${BASE}？`
      : `确认${isPaper ? "本地模拟" : " Binance Testnet "}市价卖出全部 ${BASE}？`;
    if (!(await ask(prompt, { title: "确认下单" }))) return;

    state.orderBusy = true;
    $("buyBtn").disabled = true;
    $("sellBtn").disabled = true;
    try {
      const payload = { side, quote_amount: side === "BUY" ? amount : null, reason: "manual_console" };
      const res = await fetchJSON("/api/order", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.status) {
        state.status = res.status;
        renderStatus(res.status);
      }
      toast(`${side === "BUY" ? "买入" : "卖出"}成交：${fmtQty(res.order && res.order.qty)} ${BASE}`, "ok");
      await loadCandles(true);
    } catch (err) {
      toast(`下单失败：${err.message}`, "error");
    } finally {
      state.orderBusy = false;
      const canTrade = !!state.status && (state.status.mode === "paper" || state.status.connected);
      $("buyBtn").disabled = !canTrade;
      $("sellBtn").disabled = !canTrade || !(state.status && state.status.account && Number(state.status.account.position_qty || 0) > 0);
    }
  }

  async function loadModel() {
    try {
      const data = await fetchJSON(`/api/model?interval=${state.modelInterval}`);
      updateSignal(data.signal || {}, state.modelInterval);
    } catch (err) {
      $("signalHint").textContent = `模型计算失败：${err.message}`;
    }
  }

  function bind() {
    $("historyLimit").addEventListener("change", () => {
      state.historyLimit = Number($("historyLimit").value) || 800;
      state.chartFit = false;
      loadCandles(true);
    });
    $("modelSeg").addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-model]");
      if (!btn) return;
      state.modelInterval = btn.dataset.model;
      [...$("modelSeg").querySelectorAll("button")].forEach((b) => b.classList.toggle("active", b === btn));
      loadModel();
    });
    $("autoToggle").addEventListener("change", async (event) => {
      if (state.autoBusy) return;
      const enabled = event.target.checked;
      let reviewed = null;
      if (enabled) {
        if (!window.BTCQuantGuide) { event.target.checked=false; toast('请刷新页面，交易规则预览尚未加载', 'error'); return; }
        state.autoBusy = true;
        event.target.checked = false;
        event.target.disabled = true;
        try { reviewed = await window.BTCQuantGuide.review(false, true); }
        finally { state.autoBusy=false; event.target.disabled=false; }
        if (!reviewed?.approved) return;
      }
      state.autoBusy = true;
      event.target.disabled = true;
      try {
        const s = await fetchJSON("/api/auto", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(reviewed?.settings || { enabled,interval:$("spotAutoInterval")?.value || "4h",model_id:$("spotModelSelect")?.value || "spot-trend-v2",risk_fraction:Number($("spotAutoRisk")?.value || .5)/100 }),
        });
        state.status = s;
        renderStatus(s);
        toast(`自动策略已${enabled ? "开启" : "关闭"}`, "ok");
      } catch (err) {
        event.target.checked = !enabled;
        toast(`切换失败：${err.message}`, "error");
      } finally {
        state.autoBusy = false;
        event.target.disabled = false;
      }
    });

    $("refreshBtn").addEventListener("click", () => {
      refreshStatus();
      loadCandles(false);
    });

    $("reconnectBtn").addEventListener("click", async () => {
      $("reconnectBtn").disabled = true;
      try {
        const s = await fetchJSON("/api/reconnect", { method: "POST" });
        state.status = s;
        renderStatus(s);
        await loadCandles(true);
        toast(s.connected ? "Testnet 已连接" : "Testnet 仍未连接", s.connected ? "ok" : "error");
      } catch (err) {
        toast(`重连失败：${err.message}`, "error");
      } finally {
        $("reconnectBtn").disabled = false;
      }
    });

    $("intervalSeg").addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-interval]");
      if (!btn) return;
      const next = btn.dataset.interval;
      if (next === state.interval) return;
      saveDrawings();
      state.interval = next;
      state.draw.items = [];
      state.draw.draft = null;
      [...$("intervalSeg").querySelectorAll("button")].forEach((b) => b.classList.toggle("active", b === btn));
      state.chartFit = false;
      updateNextBar();
      loadDrawings();
      loadCandles(true);
    });

    $("buyBtn").addEventListener("click", () => placeOrder("BUY"));
    $("sellBtn").addEventListener("click", () => placeOrder("SELL"));

    document.querySelectorAll(".quick-amounts button").forEach((btn) => {
      btn.addEventListener("click", () => {
        $("quoteAmount").value = btn.dataset.amount;
      });
    });

    $("resetBtn").addEventListener("click", async () => {
      const amountText = window.BTCUI
        ? await window.BTCUI.prompt("输入新的虚拟 USDT 余额（100–100,000,000）", "10000", { title: "重置现货虚拟账户" })
        : window.prompt("新的虚拟 USDT 余额", "10000");
      if (amountText === null || amountText === "") return;
      const amount = Number(amountText);
      if (!Number.isFinite(amount) || amount < 100 || amount > 100_000_000) {
        toast("虚拟余额需在 100–100,000,000 USDT 之间", "error");
        return;
      }
      if (!(await ask(`重置现货模拟账户？\n虚拟余额将设为 ${amount.toLocaleString("en-US")} USDT，所有持仓和成交会清空。`, { title: "确认重置", danger: true }))) return;
      try {
        const s = await fetchJSON(`/api/reset?amount=${amount}`, { method: "POST" });
        state.status = s;
        renderStatus(s);
        await loadCandles(true);
        toast(`已重置为 ${amount.toLocaleString("en-US")} USDT`, "ok");
      } catch (err) {
        toast(`重置失败：${err.message}`, "error");
      }
    });

    if (!document.documentElement.classList.contains("pane-mode")) document.addEventListener("visibilitychange", () => {
      if (!document.hidden) refreshStatus();
    });
  }

  function restructureLayout() {
    const main = document.querySelector("main.layout");
    if (!main || main.querySelector(".main-column")) return;
    const chart = main.querySelector(".chart-panel");
    const bottom = main.querySelector(".bottom-grid");
    if (!chart) return;
    const column = document.createElement("div");
    column.className = "main-column";
    main.insertBefore(column, chart);
    column.appendChild(chart);
    if (bottom) column.appendChild(bottom);
  }

  function applySymbolLabels() {
    const pair = $("pairLabel");
    if (pair) pair.innerHTML = `${BASE}<span>/${window.BTCSymbols ? window.BTCSymbols.quote : "USDT"}</span>`;
    if ($("chartSymbol")) $("chartSymbol").textContent = SYMBOL;
    if ($("baseHoldLabel")) $("baseHoldLabel").textContent = BASE;
    if ($("buyBtn")) $("buyBtn").textContent = `BUY ${BASE}`;
    if ($("sellBtn")) $("sellBtn").textContent = `SELL ${BASE}`;
    const setup = $("setupBar");
    if (setup && SYMBOL !== "BTCUSDT") {
      setup.innerHTML = `<strong>${BASE} 现货优先使用 Binance Spot Testnet。</strong> 未配置 API Key 或连接失败时回退本地 PAPER；不会发送主网订单。`;
    }
    document.title = `${PAIR} · BTC Quant`;
  }

  async function boot() {
    const requested=new URLSearchParams(location.search).get('interval');if(requested in INTERVAL_SECONDS)state.interval=requested;
    document.querySelectorAll('#intervalSeg [data-interval]').forEach(b=>b.classList.toggle('active',b.dataset.interval===state.interval));
    restructureLayout();
    applySymbolLabels();
    initChart();
    bind();
    if(document.documentElement.classList.contains('pane-mode')){await loadCandles(true);setInterval(()=>loadCandles(false),15000);setInterval(updateNextBar,1000);const quote=async()=>{try{const t=await fetchJSON('/api/market/price');state.status={price:t.price};updateLivePriceLine(t.price);}catch(_){}};quote();setInterval(quote,3000);$("loading").classList.add('hide');return;}
    await loadConfig();
    await Promise.all([refreshStatus(), loadCandles(true)]);
    updateNextBar();
    setInterval(refreshStatus, 3000);
    setInterval(() => loadCandles(false), 15000);
    setInterval(updateNextBar, 1000);
    // If the backend is slow to start, hide the splash eventually.
    setTimeout(() => $("loading").classList.add("hide"), 8000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
