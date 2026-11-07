/* Simulated perpetual futures page. All orders are local paper fills. */
(() => {
  "use strict";
  if (document.documentElement.dataset.accountMode === "live") return;

  const $ = (id) => document.getElementById(id);
  const ask = (message, options) => (window.BTCUI ? window.BTCUI.confirm(message, options) : Promise.resolve(window.confirm(message)));
  const SYMBOL = window.BTCSymbols ? window.BTCSymbols.current : "BTCUSDT";
  const BASE = window.BTCSymbols ? window.BTCSymbols.base : "BTC";
  const PAIR = window.BTCSymbols ? window.BTCSymbols.label : "BTC/USDT";
  const HKT = "Asia/Hong_Kong";
  const MA_DEFAULTS = {
    ema20: true, ema100: true, ema200: true,
    ma5: false, ma10: false, ma20: true, ma60: false,
  };
  const state = {
    status: null, interval: "1h", side: "LONG", chart: null, candleSeries: null,
    markLine: null, entryLine: null, liqLine: null, tpLine: null, slLine: null, chartFit: false, busy: false,
    marginMode: "ISOLATED", bookTab: "pos", historyLimit: 800,
    ma: { ...MA_DEFAULTS }, maData: {},
    draw: { tool: "select", items: [], draft: null, canvas: null, ctx: null, w: 0, h: 0, pointerId: null },
  };

  const nf = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const qf = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return n.toFixed(6).replace(/0+$/, "").replace(/\.$/, "") || "0";
  };
  const money = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? "—" : nf.format(Number(v)));
  const pct = (v, digits = 2) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return `${n >= 0 ? "+" : ""}${(n * 100).toFixed(digits)}%`;
  };
  const signed = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return `${n >= 0 ? "+" : ""}${money(n)}`;
  };
  const cls = (v) => (Number(v) > 0 ? "positive" : Number(v) < 0 ? "negative" : "");
  const timeHKT = (value) => {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleTimeString("zh-CN", { timeZone: HKT, hour12: false });
  };
  const dateHKT = (value) => {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleString("zh-CN", { timeZone: HKT, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
  };
  const hktParts = (ts) => {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: HKT, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
    }).formatToParts(new Date(Number(ts) * 1000));
    return Object.fromEntries(parts.map((p) => [p.type, p.value]));
  };
  const chartTime = (ts) => { const p = hktParts(ts); return `${p.month}-${p.day} ${p.hour}:${p.minute}${state.interval==='30s'?':'+p.second:''}`; };
  const tick = (ts, type) => {
    const p = hktParts(ts);
    if (type === 0) return p.year;
    if (type === 1) return `${Number(p.month)}月`;
    if (type === 2) return `${Number(p.day)}日`;
    if (type === 4) return `${p.hour}:${p.minute}:${p.second}`;
    return `${p.hour}:${p.minute}`;
  };

  let toastTimer = null;
  function toast(message, kind = "") {
    const el = $("fToast");
    el.textContent = message;
    el.className = `toast show ${kind}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.className = "toast"; }, 4200);
  }

  async function api(url, options = {}) {
    if (url.startsWith("/api/")) {
      const u = new URL(url, window.location.origin);
      u.searchParams.set("symbol", SYMBOL);
      url = `${u.pathname}${u.search}`;
    }
    const res = await fetch(url, { cache: "no-store", ...options });
    const text = await res.text();
    let payload = null;
    try { payload = text ? JSON.parse(text) : null; } catch (_) { /* text */ }
    if (!res.ok) {
      const detail = payload && payload.detail ? payload.detail : (text || `HTTP ${res.status}`);
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return payload;
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
      rightPriceScale: { borderColor: C.border }, timeScale: { borderColor: C.border },
    });
    if (state.markLine) state.markLine.applyOptions({ color: C.mark });
  }

  function initChart() {
    const el = $("fChart");
    if (!el || !window.LightweightCharts) {
      $("fChartStatus").textContent = "图表库加载失败";
      return;
    }
    const LC = window.LightweightCharts;
    const C = chartColors();
    const chart = LC.createChart(el, {
      width: el.clientWidth, height: el.clientHeight,
      layout: {
        background: { type: LC.ColorType ? LC.ColorType.Solid : "solid", color: C.bg },
        textColor: C.text, fontFamily: "-apple-system, Segoe UI, sans-serif", fontSize: chartFontSize(),
      },
      localization: { locale: "zh-CN", timeFormatter: chartTime },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      crosshair: {
        mode: LC.CrosshairMode ? LC.CrosshairMode.Normal : 0,
        vertLine: { color: C.cross, style: 2 },
        horzLine: { color: C.cross, style: 2 },
      },
      rightPriceScale: { borderColor: C.border, scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: {
        borderColor: C.border, timeVisible: true, secondsVisible: false,
        rightOffset: 5, barSpacing: 7, tickMarkFormatter: tick,
      },
    });
    state.chart = chart;
    state.candleSeries = chart.addCandlestickSeries({
      upColor: "#16c784", downColor: "#ea3943", borderUpColor: "#16c784",
      borderDownColor: "#ea3943", wickUpColor: "#16c784", wickDownColor: "#ea3943",
      lastValueVisible: false, priceLineVisible: false,
    });
    state.markLine = state.candleSeries.createPriceLine({
      price: 0, color: "#e7edf5", lineWidth: 1,
      lineStyle: LC.LineStyle ? LC.LineStyle.Dashed : 2,
      axisLabelVisible: true, title: "MARK",
    });
    const line = (color) => chart.addLineSeries({
      color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    state.series = {
      ema20: line("#f0b90b"), ema100: line("#5b8def"), ema200: line("#b06ab3"),
      ma5: line("#22d3ee"), ma10: line("#f472b6"), ma20: line("#94a3b8"), ma60: line("#fb923c"),
    };
    chart.subscribeCrosshairMove((param) => {
      const last = state.rawCandles && state.rawCandles[state.rawCandles.length - 1];
      if (!param || !param.time || !param.seriesData) { if (last) showOHLC(last); return; }
      const bar = param.seriesData.get(state.candleSeries);
      if (bar) showOHLC(bar);
    });
    new ResizeObserver(() => {
      chart.applyOptions({ width: el.clientWidth, height: el.clientHeight });
    }).observe(el);
    state.volume=window.BTCVolumePane?.create({chart,series:state.candleSeries,wrap:el});
    document.addEventListener("btcquant:ui", applyChartTheme);
    setupDrawing();
    setupMaControls();
  }

  function showOHLC(bar) {
    if (!bar) return;
    $("fO").textContent = money(bar.open);
    $("fH").textContent = money(bar.high);
    $("fL").textContent = money(bar.low);
    $("fC").textContent = money(bar.close);
  }

  function applyMaVisibility() {
    Object.keys(MA_DEFAULTS).forEach((key) => {
      const series = state.series && state.series[key];
      if (!series) return;
      const show = !!state.ma[key];
      series.setData(show ? (state.maData[key] || []) : []);
      const legend = $(`legend-${key}`);
      if (legend) legend.classList.toggle("hidden", !show);
    });
  }

  function setupMaControls() {
    try {
      const saved = JSON.parse(localStorage.getItem("btcquant.futures.ma.v1") || "{}");
      state.ma = { ...MA_DEFAULTS, ...saved };
    } catch (_) { state.ma = { ...MA_DEFAULTS }; }
    document.querySelectorAll("#fMaPanel input[data-fma]").forEach((input) => {
      input.checked = !!state.ma[input.dataset.fma];
      input.addEventListener("change", () => {
        state.ma[input.dataset.fma] = input.checked;
        localStorage.setItem("btcquant.futures.ma.v1", JSON.stringify(state.ma));
        applyMaVisibility();
      });
    });
    const panel = $("fMaPanel");
    $("fMaBtn").addEventListener("click", (event) => {
      event.stopPropagation();
      panel.classList.toggle("hidden");
    });
    document.addEventListener("click", (event) => {
      if (!event.target.closest(".ma-menu")) panel.classList.add("hidden");
    });
    applyMaVisibility();
  }

  async function loadCandles(fit = false) {
    const request=state.candleRequest=(state.candleRequest||0)+1;$("fChartStatus").textContent=state.interval==='30s'?'正在聚合真实 1 秒数据，首次历史加载可能较慢…':'正在加载 '+state.interval.toUpperCase()+' K 线…';
    if(document.documentElement.classList.contains('pane-mode')){const url=new URL(location.href);url.searchParams.set('interval',state.interval);history.replaceState(null,'',url);}
    try {
      const data = await api(`/api/candles?interval=${state.interval}&limit=${state.historyLimit}`);
      if(request!==state.candleRequest)return;state.chart.applyOptions({timeScale:{secondsVisible:state.interval==='30s'}});
      state.rawCandles = data.candles || [];
      state.volume?.setData(state.rawCandles,state.interval);
      state.candleSeries.setData(state.rawCandles.map((c) => ({
        time: c.time, open: c.open, high: c.high, low: c.low, close: c.close,
      })));
      state.maData = {
        ema20: data.ema20 || [], ema100: data.ema100 || [], ema200: data.ema200 || [],
        ma5: data.ma5 || [], ma10: data.ma10 || [], ma20: data.ma20 || [], ma60: data.ma60 || [],
      };
      applyMaVisibility();
      if (!state.chartFit || fit) { state.chart.timeScale().fitContent(); state.chartFit = true; }
      if (state.rawCandles.length) {
        const last = state.rawCandles[state.rawCandles.length - 1];
        showOHLC(last);
        const p = hktParts(last.time);
        $("fChartStatus").textContent = `${SYMBOL} · ${data.interval} · ${state.rawCandles.length} 根 · 最后 ${p.month}-${p.day} ${p.hour}:${p.minute} HKT`;
      }
      redrawDrawings();
    } catch (err) {
      if(request!==state.candleRequest)return;
      $("fChartStatus").textContent = `行情加载失败：${err.message}`;
    }
  }

  // ------------------------------------------------------------- drawings
  function drawingKey() {
    return `btcquant.futures.drawings.v1.${SYMBOL}.${state.interval}`;
  }
  function loadDrawings() {
    try {
      const raw = localStorage.getItem(drawingKey());
      state.draw.items = raw ? JSON.parse(raw) : [];
    } catch (_) { state.draw.items = []; }
    state.draw.draft = null;
    redrawDrawings();
  }
  function saveDrawings() {
    try { localStorage.setItem(drawingKey(), JSON.stringify(state.draw.items)); } catch (_) { /* ignore */ }
  }
  function toXY(point) {
    if (!point || !state.chart) return null;
    const x = window.BTCDrawingCoordinates.toX(state.chart,state.rawCandles || [],state.interval,point.time);
    const y = state.candleSeries.priceToCoordinate(point.price);
    return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
  }
  function priceToY(price) {
    const y = state.candleSeries.priceToCoordinate(price);
    return Number.isFinite(y) ? y : null;
  }
  function resizeDrawCanvas() {
    const wrap = $("fChartWrap"), canvas = $("fDrawCanvas");
    if (!wrap || !canvas) return;
    const rect = wrap.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(rect.width)), h = Math.max(1, Math.round(rect.height));
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
    const color = item.color || (item.type === "pen" ? "#22d3ee" : "#f0b90b");
    ctx.save();
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = draft ? 1.5 : 2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    if (draft) ctx.setLineDash([5, 4]);
    if (item.type === "hline") {
      const y = priceToY(item.price);
      if (y === null) { ctx.restore(); return; }
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(state.draw.w, y); ctx.stroke();
      ctx.setLineDash([]);
      const text = money(item.price);
      ctx.font = "10px Consolas, monospace";
      const tw = ctx.measureText(text).width;
      ctx.fillStyle = "rgba(240,185,11,.94)";
      ctx.fillRect(state.draw.w - tw - 18, y - 9, tw + 12, 18);
      ctx.fillStyle = "#081018";
      ctx.fillText(text, state.draw.w - tw - 12, y + 3);
    } else if (item.type === "trend") {
      const a = toXY(item.points && item.points[0]), b = toXY(item.points && item.points[1]);
      if (!a || !b) { ctx.restore(); return; }
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      if (!draft) [a, b].forEach((p) => { ctx.beginPath(); ctx.arc(p.x, p.y, 3, 0, Math.PI * 2); ctx.fill(); });
    } else if (item.type === "pen") {
      let started = false;
      ctx.beginPath();
      (item.points || []).forEach((p) => {
        const q = toXY(p);
        if (!q) { started = false; return; }
        if (!started) { ctx.moveTo(q.x, q.y); started = true; } else ctx.lineTo(q.x, q.y);
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
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left, y = event.clientY - rect.top;
    const time = window.BTCDrawingCoordinates.fromX(state.chart,state.rawCandles || [],state.interval,x);
    const price = state.candleSeries.coordinateToPrice(y);
    if (time === null || time === undefined || typeof time === "object" || !Number.isFinite(price)) return null;
    return { time, price };
  }
  function distSeg(px, py, a, b) {
    const dx = b.x - a.x, dy = b.y - a.y;
    if (!dx && !dy) return Math.hypot(px - a.x, py - a.y);
    const t = Math.max(0, Math.min(1, ((px - a.x) * dx + (py - a.y) * dy) / (dx * dx + dy * dy)));
    return Math.hypot(px - (a.x + t * dx), py - (a.y + t * dy));
  }
  function hitTest(x, y) {
    for (let i = state.draw.items.length - 1; i >= 0; i -= 1) {
      const it = state.draw.items[i];
      if (it.type === "hline") {
        const py = priceToY(it.price);
        if (py !== null && Math.abs(y - py) <= 9) return i;
      } else if (it.type === "trend") {
        const a = toXY(it.points && it.points[0]), b = toXY(it.points && it.points[1]);
        if (a && b && distSeg(x, y, a, b) <= 9) return i;
      } else if (it.type === "pen") {
        for (let j = 1; j < (it.points || []).length; j += 1) {
          const a = toXY(it.points[j - 1]), b = toXY(it.points[j]);
          if (a && b && distSeg(x, y, a, b) <= 9) return i;
        }
      }
    }
    return -1;
  }
  function setDrawTool(tool) {
    state.draw.tool = tool;
    state.draw.draft = null;
    document.querySelectorAll(".draw-bar [data-tool]").forEach((btn) => btn.classList.toggle("active", btn.dataset.tool === tool));
    const active = tool !== "select";
    if (state.draw.canvas) state.draw.canvas.classList.toggle("active", active);
    if (state.chart) state.chart.applyOptions({ handleScroll: !active, handleScale: !active });
    const tips = { select: "选择/缩放；选一个工具后在图上拖动", trend: "拖动两个点画趋势线", hline: "点击放置水平线", pen: "按住鼠标自由涂鸦", erase: "点击线条删除" };
    $("fDrawTip").textContent = tips[tool] || "";
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
      const a = toXY(draft.points[0]), b = toXY(draft.points[1]);
      if (a && b && Math.hypot(a.x - b.x, a.y - b.y) > 5) d.items.push(draft);
    } else if (draft.type === "pen" && draft.points.length > 1) d.items.push(draft);
    saveDrawings();
    redrawDrawings();
  }
  function setupDrawing() {
    const canvas = $("fDrawCanvas");
    if (!canvas || !state.chart) return;
    resizeDrawCanvas();
    loadDrawings();
    setDrawTool("select");
    const space=document.createElement('select');space.className='future-space';space.setAttribute('aria-label','右侧绘图留白');
    space.innerHTML='<option value="20">留白 20 根</option><option value="80" selected>留白 80 根</option><option value="160">留白 160 根</option><option value="320">留白 320 根</option>';
    document.querySelector('.draw-bar').append(space);space.addEventListener('change',()=>state.chart.applyOptions({timeScale:{rightOffset:Number(space.value)}}));state.chart.applyOptions({timeScale:{rightOffset:80}});
    new ResizeObserver(resizeDrawCanvas).observe($("fChartWrap"));
    setInterval(redrawDrawings, 100);
    document.querySelectorAll(".draw-bar [data-tool]").forEach((btn) => btn.addEventListener("click", () => setDrawTool(btn.dataset.tool)));
    $("fUndoDraw").addEventListener("click", () => { state.draw.items.pop(); saveDrawings(); redrawDrawings(); });
    $("fClearDraw").addEventListener("click", async () => {
      if (!state.draw.items.length || !(await ask("清空当前周期的所有划线？", { title: "清空划线", danger: true }))) return;
      state.draw.items = []; saveDrawings(); redrawDrawings();
    });
    canvas.addEventListener("pointerdown", (event) => {
      if (state.draw.tool === "select") return;
      event.preventDefault();
      const point = pointFromEvent(event);
      if (!point) { toast("这里没有有效价格，换一个位置试试", "error"); return; }
      if (state.draw.tool === "erase") {
        const rect = canvas.getBoundingClientRect();
        const idx = hitTest(event.clientX - rect.left, event.clientY - rect.top);
        if (idx >= 0) { state.draw.items.splice(idx, 1); saveDrawings(); redrawDrawings(); }
        return;
      }
      if (state.draw.tool === "hline") {
        state.draw.items.push({ type: "hline", price: point.price, color: "#f0b90b" });
        saveDrawings(); redrawDrawings(); return;
      }
      state.draw.draft = { type: state.draw.tool, points: state.draw.tool === "pen" ? [point] : [point, point], color: state.draw.tool === "pen" ? "#22d3ee" : "#f0b90b" };
      state.draw.pointerId = event.pointerId;
      try { canvas.setPointerCapture(event.pointerId); } catch (_) { /* ignore */ }
      redrawDrawings();
    }, { passive: false });
    canvas.addEventListener("pointermove", (event) => {
      if (!state.draw.draft) return;
      const point = pointFromEvent(event);
      if (!point) return;
      if (state.draw.draft.type === "trend") state.draw.draft.points[1] = point;
      else {
        const points = state.draw.draft.points;
        const a = toXY(points[points.length - 1]), b = toXY(point);
        if (!a || !b || Math.hypot(a.x - b.x, a.y - b.y) > 1.5) {
          points.push(point);
          if (points.length > 1800) points.shift();
        }
      }
      redrawDrawings();
    });
    canvas.addEventListener("pointerup", finishDraft);
    canvas.addEventListener("pointercancel", finishDraft);
    canvas.addEventListener("pointerleave", (event) => { if (state.draw.draft && event.buttons === 0) finishDraft(); });
    window.addEventListener("keydown", (event) => {
      const tag = (event.target && event.target.tagName) || "";
      if (event.key === "Escape") { $("fMaPanel").classList.add("hidden"); if (tag !== "INPUT" && tag !== "TEXTAREA") setDrawTool("select"); }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z" && tag !== "INPUT" && tag !== "TEXTAREA") {
        if (!state.draw.items.length) return;
        event.preventDefault(); state.draw.items.pop(); saveDrawings(); redrawDrawings();
      }
    });
  }

  function removeLine(key) {
    if (state[key]) {
      try { state.candleSeries.removePriceLine(state[key]); } catch (_) { /* ignore */ }
      state[key] = null;
    }
  }

  function drawPositionLines(position) {
    removeLine("entryLine");
    removeLine("liqLine");
    removeLine("tpLine");
    removeLine("slLine");
    removeLine("trailLine");
    if (position) {
      state.entryLine = state.candleSeries.createPriceLine({
        price: position.entry_price, color: "#5b8def", lineWidth: 1,
        lineStyle: 2, axisLabelVisible: true, title: "ENTRY",
      });
      state.liqLine = state.candleSeries.createPriceLine({
        price: position.liq_price, color: "#ea3943", lineWidth: 1,
        lineStyle: 2, axisLabelVisible: true, title: "LIQ",
      });
      if (position.take_profit) {
        state.tpLine = state.candleSeries.createPriceLine({
          price: position.take_profit, color: "#16c784", lineWidth: 1,
          lineStyle: 2, axisLabelVisible: true, title: "TP",
        });
      }
      if (position.stop_loss) {
        state.slLine = state.candleSeries.createPriceLine({
          price: position.stop_loss, color: "#f0b90b", lineWidth: 1,
          lineStyle: 2, axisLabelVisible: true, title: "SL",
        });
      }
      if(position.trailing_enabled && position.trailing_stop){state.trailLine=state.candleSeries.createPriceLine({price:position.trailing_stop,color:'#a597ff',lineWidth:2,lineStyle:2,axisLabelVisible:true,title:'TRAIL'});}
    }
  }

  function render(s) {
    state.status = s;
    const a = s.account || {};
    const positions = s.positions || (s.position ? [s.position] : []);
    const p = positions[0];
    const totalQty = positions.reduce((sum, item) => sum + Number(item.qty || 0), 0);
    const totalMargin = positions.reduce((sum, item) => sum + Number(item.margin || 0), 0);
    const totalPnl = positions.reduce((sum, item) => sum + Number(item.unrealized_pnl || 0), 0);
    const totalNotional = positions.reduce((sum, item) => sum + Number(item.entry_price || 0) * Number(item.qty || 0), 0);
    const weightedEntry = totalQty ? totalNotional / totalQty : 0;
    const mixed = positions.some((item) => item.side === "LONG") && positions.some((item) => item.side === "SHORT");
    $("fPrice").textContent = money(s.price);
    const activeMargin = positions.length && positions.every((item) => item.margin_mode === "CROSS") ? "CROSS" : "ISOLATED";
    $("fMarginBadge").textContent = positions.length > 1 ? "HEDGE" : activeMargin === "CROSS" ? "CROSS" : "ISOLATED";
    $("fEquity").textContent = money(a.equity);
    $("fSessionPnl").textContent = signed(a.session_pnl);
    $("fSessionPnl").className = cls(a.session_pnl);
    $("fWallet").textContent = money(a.wallet);
    $("fAvailable").textContent = money(a.available);
    $("fUnrealized").textContent = signed(a.unrealized_pnl);
    $("fUnrealized").className = cls(a.unrealized_pnl);
    $("fRealized").textContent = signed(a.realized_pnl);
    $("fRealized").className = cls(a.realized_pnl);
    $("fPosSide").textContent = positions.length === 0 ? "FLAT" : mixed ? "HEDGE" : `${positions.length} POSITION${positions.length > 1 ? "S" : ""}`;
    $("fPosBadge").textContent = positions.length ? `${qf(totalQty)} ${BASE}` : `0 ${BASE}`;
    $("fPosBadge").className = `mini-badge ${mixed ? "amber" : p ? (p.side === "LONG" ? "green" : "red") : "neutral"}`;
    $("fPosQty").textContent = positions.length ? qf(totalQty) : "—";
    $("fPosEntry").textContent = positions.length ? money(weightedEntry) : "—";
    $("fPosMark").textContent = p ? money(p.mark_price) : "—";
    $("fPosLev").textContent = positions.length === 1 ? `${p.leverage}x` : positions.length ? "多个" : "—";
    $("fPosLiq").textContent = positions.length === 1 ? money(p.liq_price) : positions.length ? "见列表" : "—";
    $("fPosMargin").textContent = positions.length ? money(totalMargin) : "—";
    $("fPosPnl").textContent = positions.length ? signed(totalPnl) : "—";
    $("fPosPnl").className = positions.length ? cls(totalPnl) : "";
    $("fPosRoe").textContent = positions.length && totalMargin ? pct(totalPnl / totalMargin) : "—";
    $("fPosRoe").className = positions.length && totalMargin ? cls(totalPnl / totalMargin) : "";
    $("fPosTp").textContent = positions.length === 1 && p.take_profit ? money(p.take_profit) : "—";
    $("fPosSl").textContent = positions.length === 1 && p.stop_loss ? money(p.stop_loss) : "—";
    $("fPosMode").textContent = positions.length === 0 ? "—" : mixed ? "混合" : (p.margin_mode === "CROSS" ? "全仓 CROSS" : "逐仓 ISOLATED");
    if (positions.length === 1 && p.margin_mode) {
      state.marginMode = p.margin_mode;
      [...$("fMarginToggle").querySelectorAll("button")].forEach((b) => b.classList.toggle("active", b.dataset.margin === state.marginMode));
    }
    [...$("fMarginToggle").querySelectorAll("button")].forEach((b) => { b.disabled = positions.length > 0; });
    $("fCloseBtn").disabled = !positions.length || state.busy;
    $("fCloseLimitBtn").disabled = !p || state.busy;
    $("fResetBtn").disabled = state.busy;
    const auto = s.auto || {};
    $("fAutoToggle").checked = !!auto.enabled;
    if (!document.querySelector('.quant-controls')?.dataset.dirty || auto.enabled) {
      if (document.activeElement !== $("fAutoInterval")) $("fAutoInterval").value = auto.interval || "1h";
      if (document.activeElement !== $("fAutoLeverage")) $("fAutoLeverage").value = String(auto.leverage || 5);
    }
    window.dispatchEvent(new CustomEvent("btcquant:futures",{detail:s}));
    $("fAutoHint").textContent = auto.enabled
      ? `运行中：${String(auto.interval || "30m").toUpperCase()} 趋势模型 · ${auto.leverage || 5}x · 多空双向`
      : "开启后按选定周期的趋势信号自动模拟开多 / 开空 / 平仓；多空双向。";
    drawPositionLines(p);
    if (state.markLine) {
      const ref = state.rawCandles && state.rawCandles.length ? state.rawCandles[state.rawCandles.length - 1].close : s.price;
      state.markLine.applyOptions({ price: s.price, color: s.price >= ref ? "#16c784" : "#ea3943", title: "MARK" });
    }
    renderOrders(s.orders || []);
    renderPending(s.pending || []);
    renderEvents(s.events || []);
    renderBook(s);
    $("fLastUpdate").textContent = s.updated_at ? `api ${timeHKT(s.updated_at)} HKT` : "—";
    if (s.last_error) {
      $("fAlert").textContent = `服务错误：${s.last_error}`;
      $("fAlert").classList.remove("hidden");
    } else {
      $("fAlert").classList.add("hidden");
    }
    updateCalculator();
    updateQtyHint();
    if ($("fLoading")) setTimeout(() => $("fLoading").classList.add("hide"), 80);
  }

  function renderOrders(orders) {
    const body = $("fOrdersBody");
    const visible = orders.slice(0, 20);
    $("fOrderCount").textContent = String(visible.length);
    if (!visible.length) {
      body.innerHTML = `<tr class="empty-row"><td colspan="7">暂无订单</td></tr>`;
      return;
    }
    body.innerHTML = visible.map((o) => {
      const sideCls = o.side === "LONG" ? "side-buy" : o.side === "SHORT" ? "side-sell" : "";
      return `<tr>
        <td>${dateHKT(o.time)}</td>
        <td>${o.action || ""}</td>
        <td class="${sideCls}">${o.side || ""}</td>
        <td>${money(o.price)}</td>
        <td>${qf(o.qty)}</td>
        <td>${o.leverage ? o.leverage + "x" : "—"}</td>
        <td class="${cls(o.pnl)}">${o.pnl === undefined ? "—" : signed(o.pnl)}</td>
      </tr>`;
    }).join("");
  }

  function renderBook(s) {
    const body = $("fBookBody");
    if (!body) return;
    if(body.contains(document.activeElement) && ['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName))return;
    const positions = s.positions || (s.position ? [s.position] : []);
    if (state.bookTab === "pos") {
      if (!positions.length) {
        body.innerHTML = `<div class="book-empty">暂无持仓。开仓后，多单和空单会分别出现在这里。</div>`;
        return;
      }
      body.innerHTML = `<table>
        <thead><tr><th>方向</th><th>数量</th><th>开仓价</th><th>标记价</th><th>强平价</th><th>杠杆</th><th>保证金模式</th><th>保证金</th><th>未实现盈亏</th><th>ROE</th><th>固定止盈</th><th>初始止损</th><th>移动止盈</th><th>操作</th></tr></thead>
        <tbody>${positions.map((p) => {
          const sideCls = p.side === "LONG" ? "book-side-long" : "book-side-short";
          return `<tr>
            <td class="${sideCls}">${p.side}</td><td>${qf(p.qty)} ${BASE}</td><td>${money(p.entry_price)}</td>
            <td>${money(p.mark_price)}</td><td>${money(p.liq_price)}</td><td>${p.leverage}x</td><td>${p.margin_mode === "CROSS" ? "全仓" : "逐仓"}</td>
            <td>${money(p.margin)}</td><td class="${cls(p.unrealized_pnl)}">${signed(p.unrealized_pnl)}</td>
            <td class="${cls(p.roe)}">${pct(p.roe)}</td>
            <td><input class="book-input" data-book-tp="${p.id}" type="number" step="0.01" value="${p.take_profit ?? ""}" placeholder="—"></td>
            <td><input class="book-input" data-book-sl="${p.id}" type="number" step="0.01" value="${p.stop_loss ?? ""}" placeholder="—"></td>
            <td><button class="book-trailing-btn" data-trailing-id="${p.id}">${p.trailing_enabled ? '调整保护' : '启用保护'}</button><span class="terminal-trailing-status">${p.trailing_enabled ? (p.trailing_active ? '保护价 '+money(p.trailing_stop) : '等待浮盈 '+((p.trail_activation_pct||0)*100).toFixed(2)+'%') : '未启用'}</span></td>
            <td><button class="table-action" data-book-tpsl="${p.id}">保存</button> <button class="table-action" data-book-close="${p.id}">平仓</button></td>
          </tr>`;
        }).join("")}</tbody></table>`;
      body.querySelectorAll("[data-book-close]").forEach((btn) => {
        btn.addEventListener("click", () => closePosition(btn.dataset.bookClose));
      });
      body.querySelectorAll("[data-book-tpsl]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const id = btn.dataset.bookTpsl;
          const tp = body.querySelector(`[data-book-tp="${id}"]`).value;
          const sl = body.querySelector(`[data-book-sl="${id}"]`).value;
          try {
            const res = await api("/api/futures/tpsl", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ position_id: id, take_profit: tp === "" ? null : Number(tp), stop_loss: sl === "" ? null : Number(sl) }),
            });
            render(res);
            toast("止盈/止损已更新", "ok");
          } catch (err) { toast(`设置失败：${err.message}`, "error"); }
        });
      });
      return;
    }
    if (state.bookTab === "orders") {
      const pending = s.pending || [];
      if (!pending.length) { body.innerHTML = `<div class="book-empty">没有挂单。</div>`; return; }
      body.innerHTML = `<table>
        <thead><tr><th>类型</th><th>方向</th><th>数量</th><th>限价</th><th>杠杆</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>${pending.map((o) => `<tr>
          <td>${o.action === "CLOSE" ? "限价平仓" : "限价开仓"}</td><td class="${o.side === "LONG" ? "book-side-long" : "book-side-short"}">${o.side}</td>
          <td>${qf(o.qty)} ${BASE}</td><td>${money(o.limit_price)}</td><td>${o.leverage}x</td><td>PENDING</td>
          <td><button class="table-action" data-book-cancel="${o.id}">撤单</button></td>
        </tr>`).join("")}</tbody></table>`;
      body.querySelectorAll("[data-book-cancel]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          try {
            const res = await api("/api/futures/cancel", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ order_id: btn.dataset.bookCancel }),
            });
            render(res.status);
          } catch (err) { toast(`撤单失败：${err.message}`, "error"); }
        });
      });
      return;
    }
    const orders = (s.orders || []).slice(0, 20);
    if (!orders.length) { body.innerHTML = `<div class="book-empty">暂无成交。</div>`; return; }
    body.innerHTML = `<table>
      <thead><tr><th>时间</th><th>动作</th><th>方向</th><th>价格</th><th>数量</th><th>杠杆</th><th>盈亏</th></tr></thead>
      <tbody>${orders.map((o) => `<tr>
        <td>${dateHKT(o.time)}</td><td>${o.action || ""}</td><td class="${o.side === "LONG" ? "book-side-long" : "book-side-short"}">${o.side || ""}</td>
        <td>${money(o.price)}</td><td>${qf(o.qty)} ${BASE}</td><td>${o.leverage ? o.leverage + "x" : "—"}</td>
        <td class="${cls(o.pnl)}">${o.pnl === undefined ? "—" : signed(o.pnl)}</td>
      </tr>`).join("")}</tbody></table>`;
  }

  function renderPending(pending) {
    const el = $("fPending");
    if (!pending.length) { el.textContent = "没有挂单"; return; }
    el.innerHTML = pending.map((o) => `
      <div class="level-row pending-row">
        <span>${o.action === "CLOSE" ? "限价平仓" : "限价开仓"} · ${o.side} ${qf(o.qty)} ${BASE} @ ${money(o.limit_price)} · ${o.leverage}x</span>
        <button class="table-action" data-cancel="${o.id}">撤单</button>
      </div>`).join("");
    el.querySelectorAll("[data-cancel]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          const res = await api("/api/futures/cancel", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ order_id: btn.dataset.cancel }),
          });
          render(res.status);
          toast("挂单已撤销", "ok");
        } catch (err) { toast(`撤单失败：${err.message}`, "error"); }
      });
    });
  }

  function renderEvents(events) {
    const el = $("fEvents");
    if (!events.length) { el.innerHTML = `<div class="event-item"><span class="event-time">--:--:--</span><span>暂无事件</span></div>`; return; }
    el.innerHTML = events.slice(-20).reverse().map((e) => `
      <div class="event-item ${e.level || ""}"><span class="event-time">${timeHKT(e.time)}</span><span>${e.message}</span></div>
    `).join("");
  }

  async function refresh() {
    try {
      const s = await api("/api/futures/status");
      render(s);
    } catch (err) {
      $("fAlert").textContent = `无法连接服务：${err.message}`;
      $("fAlert").classList.remove("hidden");
    }
  }

  function updateQtyHint() {
    const notional = Number($("fNotional").value);
    const type = $("fOrderType").value;
    const limitPrice = Number($("fLimit").value);
    const price = type === "LIMIT" && limitPrice > 0 ? limitPrice : Number(state.status && state.status.price);
    const hint = $("fQtyHint");
    if (!hint) return;
    if (!Number.isFinite(notional) || notional <= 0 || !Number.isFinite(price) || price <= 0) {
      hint.textContent = `输入 USDT 名义价值，自动折算 ${BASE} 数量`;
    } else {
      hint.textContent = `≈ ${qf(notional / price)} ${BASE} @ ${money(price)} USDT`;
    }
  }

  function orderPayload() {
    if (state.status?.symbol && state.status.symbol !== SYMBOL) throw new Error("交易对状态不一致，请刷新页面");
    const type = $("fOrderType").value;
    const notional = Number($("fNotional").value);
    const leverage = Number($("fLeverageRange").value);
    const limitPrice = Number($("fLimit").value);
    if (!Number.isFinite(notional) || notional < 5) throw new Error("名义价值至少 5 USDT");
    if (type === "LIMIT" && (!Number.isFinite(limitPrice) || limitPrice <= 0)) throw new Error("请输入限价");
    const price = type === "LIMIT" ? limitPrice : Number(state.status && state.status.price);
    if (!Number.isFinite(price) || price <= 0) throw new Error("行情价格未就绪");
    const qty = Number((notional / price).toFixed(8));
    if (qty < 0.00001) throw new Error("名义价值太小，折算数量不足");
    const tp = Number($("fTp").value);
    const sl = Number($("fSl").value);
    if ($("fTp").value && (!Number.isFinite(tp) || tp <= 0)) throw new Error("止盈价无效");
    if ($("fSl").value && (!Number.isFinite(sl) || sl <= 0)) throw new Error("止损价无效");
    return {
      side: state.side, action: "OPEN", order_type: type, qty, leverage,
      limit_price: type === "LIMIT" ? limitPrice : null, notional,
      take_profit: $("fTp").value ? tp : null,
      stop_loss: $("fSl").value ? sl : null,
      margin_mode: state.marginMode,
    };
  }

  async function submitOrder() {
    if (state.busy) return;
    let payload;
    try { payload = orderPayload(); } catch (err) { toast(err.message, "error"); return; }
    const label = `${payload.side} ${payload.order_type} ${money(payload.notional)} USDT ≈ ${qf(payload.qty)} ${BASE} · ${payload.leverage}x`;
    if (!(await ask(`${PAIR} · 模拟合约\n${label}`, { title: "确认合约单" }))) return;
    state.busy = true;
    $("fSubmitBtn").disabled = true;
    try {
      const res = await api("/api/futures/order", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      render(res.status);
      toast(`${payload.side} ${payload.order_type} 已提交`, "ok");
    } catch (err) {
      toast(`下单失败：${err.message}`, "error");
    } finally {
      state.busy = false;
      $("fSubmitBtn").disabled = false;
    }
  }

  async function closePosition(positionId = null) {
    if (!state.status || !(state.status.positions || []).length || state.busy) return;
    if (!(await ask(positionId ? `市价平掉这笔 ${PAIR} 仓位？` : `市价平掉 ${PAIR} 的全部模拟仓位？`, { title: "市价平仓", danger: true }))) return;
    state.busy = true;
    try {
      const suffix = positionId ? `?position_id=${encodeURIComponent(positionId)}` : "";
      const res = await api(`/api/futures/close${suffix}`, { method: "POST" });
      render(res.status);
      toast("已平仓", "ok");
    } catch (err) { toast(`平仓失败：${err.message}`, "error"); }
    finally { state.busy = false; }
  }

  async function placeCloseLimit() {
    const p = state.status && state.status.position;
    if (!p || state.busy) return;
    const price = Number($("fCloseLimit").value);
    if (!Number.isFinite(price) || price <= 0) { toast("请输入限价平仓价", "error"); return; }
    if (p.side === "LONG" && price <= state.status.price) { toast("多单限价卖出应高于当前价", "error"); return; }
    if (p.side === "SHORT" && price >= state.status.price) { toast("空单限价买回应低于当前价", "error"); return; }
    if (!(await ask(`${PAIR} · 限价平仓\n${p.side} ${qf(p.qty)} ${BASE} @ ${money(price)} USDT`, { title: "限价平仓" }))) return;
    state.busy = true;
    try {
      const res = await api("/api/futures/order", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          side: p.side, action: "CLOSE", order_type: "LIMIT",
          qty: p.qty, leverage: p.leverage, limit_price: price,
        }),
      });
      render(res.status);
      toast("限价平仓已挂出", "ok");
    } catch (err) {
      toast(`挂单失败：${err.message}`, "error");
    } finally {
      state.busy = false;
    }
  }

  async function resetAccount() {
    if (!(await ask("重置合约模拟账户？所有仓位和订单会清空。", { title: "重置合约账户", danger: true }))) return;
    try {
      const s = await api("/api/futures/reset", { method: "POST" });
      render(s);
      toast("合约模拟账户已重置", "ok");
    } catch (err) { toast(`重置失败：${err.message}`, "error"); }
  }

  function updateCalculator() {
    const entry = Number($("calcEntry").value);
    const exit = Number($("calcExit").value);
    const notional = Number($("calcNotionalInput").value);
    const lev = Math.max(1, Math.min(100, Number($("calcLev").value) || 1));
    if (![entry, exit, notional, lev].every(Number.isFinite) || entry <= 0 || notional <= 0) return;
    const qty = notional / entry;
    const dir = state.side === "LONG" ? 1 : -1;
    const pnl = (exit - entry) * qty * dir;
    const margin = notional / lev;
    const roe = pnl / margin;
    const liq = state.side === "LONG"
      ? entry * (1 - 1 / lev + 0.005)
      : entry * (1 + 1 / lev - 0.005);
    $("calcSide").textContent = state.side;
    $("calcSide").className = state.side === "LONG" ? "positive" : "negative";
    $("calcQtyOut").textContent = qf(qty);
    $("calcMargin").textContent = money(margin);
    $("calcPnl").textContent = signed(pnl);
    $("calcPnl").className = cls(pnl);
    $("calcRoe").textContent = pct(roe);
    $("calcRoe").className = cls(roe);
    $("calcLiq").textContent = money(liq);
  }

  function bind() {
    let autoBusy = false;
    async function saveAuto(enabled, reviewedSettings=null) {
      const interval = $("fAutoInterval").value;
      const leverage = Math.max(1, Math.min(100, Number($("fAutoLeverage").value) || 5));
      try {
        const s = await api("/api/futures/auto", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(reviewedSettings || { enabled, interval, leverage:Math.min(10,leverage),model_id:$("fModelSelect")?.value || "trend-v2",risk_fraction:Number($("fAutoRisk")?.value || .5)/100 }),
        });
        render(s);
        toast(enabled ? `合约量化已开启 · ${interval.toUpperCase()}` : "合约量化已关闭", "ok");
      } catch (err) {
        toast(`量化设置失败：${err.message}`, "error");
        $("fAutoToggle").checked = false;
      }
    }
    $("fAutoToggle").addEventListener("change", async (event) => {
      if (autoBusy) return;
      const enabled = event.target.checked;
      autoBusy=true; event.target.disabled=true;
      try {
        let reviewed=null;
        if (enabled) {
          event.target.checked=false;
          if (!window.BTCQuantGuide) { toast('请刷新页面，交易规则预览尚未加载','error'); return; }
          reviewed=await window.BTCQuantGuide.review(true,true);
          if (!reviewed.approved) return;
        }
        await saveAuto(enabled,reviewed?.settings);
      } finally { autoBusy=false; event.target.disabled=false; }
    });
    $("fAutoInterval").addEventListener("change", () => {
      if ($("fAutoToggle").checked) saveAuto(true);
    });
    $("fAutoLeverage").addEventListener("change", () => {
      if ($("fAutoToggle").checked) saveAuto(true);
    });
    $("fIntervalSeg").addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-interval]");
      if (!btn || btn.dataset.interval === state.interval) return;
      state.interval = btn.dataset.interval;
      [...$("fIntervalSeg").querySelectorAll("button")].forEach((b) => b.classList.toggle("active", b === btn));
      state.chartFit = false;
      loadDrawings();
      loadCandles(true);
    });
    $("historyLimit").addEventListener("change", () => {
      state.historyLimit = Number($("historyLimit").value) || 800;
      state.chartFit = false;
      loadCandles(true);
    });
    $("fBookTabs").addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-book]");
      if (!btn) return;
      state.bookTab = btn.dataset.book;
      [...$("fBookTabs").querySelectorAll("button")].forEach((b) => b.classList.toggle("active", b === btn));
      if (state.status) renderBook(state.status);
    });
    $("fMarginToggle").addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-margin]");
      if (!btn || btn.disabled) return;
      state.marginMode = btn.dataset.margin;
      [...$("fMarginToggle").querySelectorAll("button")].forEach((b) => b.classList.toggle("active", b === btn));
    });
    $("fSideToggle").addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-side]");
      if (!btn) return;
      state.side = btn.dataset.side;
      [...$("fSideToggle").querySelectorAll("button")].forEach((b) => {
        b.classList.toggle("active", b === btn);
      });
      updateCalculator();
    });
    $("fOrderType").addEventListener("change", () => {
      $("fLimitWrap").style.display = $("fOrderType").value === "LIMIT" ? "block" : "none";
      updateQtyHint();
    });
    $("fLeverageRange").addEventListener("input", () => {
      $("fLeverageLabel").textContent = `${$("fLeverageRange").value}x`;
      $("calcLev").value = $("fLeverageRange").value;
      updateCalculator();
    });
    ["calcEntry", "calcExit", "calcNotionalInput", "calcLev"].forEach((id) => $(id).addEventListener("input", updateCalculator));
    $("fNotional").addEventListener("input", updateQtyHint);
    $("fLimit").addEventListener("input", updateQtyHint);
    $("fSubmitBtn").addEventListener("click", submitOrder);
    $("fCloseBtn").addEventListener("click", () => closePosition());
    $("fCloseLimitBtn").addEventListener("click", placeCloseLimit);
    $("fResetBtn").addEventListener("click", resetAccount);
    $("fRefresh").addEventListener("click", () => { refresh(); loadCandles(false); });
  }

  function applySymbolLabels() {
    const pair = $("fPair");
    if (pair) pair.innerHTML = `${BASE}<span>/${window.BTCSymbols ? window.BTCSymbols.quote : "USDT"}</span>`;
    if ($("fChartSymbol")) $("fChartSymbol").textContent = SYMBOL;
    if ($("fCalcQtyLabel")) $("fCalcQtyLabel").textContent = BASE;
    document.title = `${PAIR} 合约 · BTC Quant`;
  }

  async function boot() {
    const requested=new URLSearchParams(location.search).get('interval');if(['30s','1m','3m','5m','15m','30m','1h','4h','1d'].includes(requested))state.interval=requested;
    document.querySelectorAll('#fIntervalSeg [data-interval]').forEach(b=>b.classList.toggle('active',b.dataset.interval===state.interval));
    applySymbolLabels();
    initChart();
    bind();
    updateCalculator();
    if(document.documentElement.classList.contains('pane-mode')){await loadCandles(true);setInterval(()=>loadCandles(false),15000);const quote=async()=>{try{const t=await api('/api/market/price');state.markLine?.applyOptions({price:t.price});}catch(_){}};quote();setInterval(quote,3000);$("fLoading").classList.add('hide');return;}
    await Promise.all([refresh(), loadCandles(true)]);
    setInterval(refresh, 2000);
    setInterval(() => loadCandles(false), 15000);
    setTimeout(() => $("fLoading").classList.add("hide"), 8000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
