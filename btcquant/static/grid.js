/* Spot long-only grid simulator page. */
(() => {
  "use strict";
  if (document.documentElement.dataset.accountMode === "live") return;

  const $ = (id) => document.getElementById(id);
  const ask = (message, options) => (window.BTCUI ? window.BTCUI.confirm(message, options) : Promise.resolve(window.confirm(message)));
  const SYMBOL = window.BTCSymbols ? window.BTCSymbols.current : "BTCUSDT";
  const BASE = window.BTCSymbols ? window.BTCSymbols.base : "BTC";
  const PAIR = window.BTCSymbols ? window.BTCSymbols.label : "BTC/USDT";
  const HKT = "Asia/Hong_Kong";
  const INTERVALS = {"30s":30,"1m":60,"3m":180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400 };
  const state = {
    status: null, interval: "1h", chart: null, candleSeries: null, markLine: null,
    gridLines: [], chartFit: false, busy: false, editing: false, configInit: false, historyLimit: 800,
  };
  const nf = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const money = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? "—" : nf.format(Number(v)));
  const qty = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return n.toFixed(8).replace(/0+$/, "").replace(/\.$/, "") || "0";
  };
  const signed = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return `${n >= 0 ? "+" : ""}${money(n)}`;
  };
  const pct = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return `${n >= 0 ? "+" : ""}${(n * 100).toFixed(2)}%`;
  };
  const cls = (v) => (Number(v) > 0 ? "positive" : Number(v) < 0 ? "negative" : "");
  const timeHKT = (v) => {
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? "—" : d.toLocaleTimeString("zh-CN", { timeZone: HKT, hour12: false });
  };
  const dateHKT = (v) => {
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("zh-CN", {
      timeZone: HKT, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
    });
  };
  const hktParts = (ts) => {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: HKT, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit",second:"2-digit", hourCycle: "h23",
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
    const el = $("gToast");
    el.textContent = message;
    el.className = `toast show ${kind}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.className = "toast"; }, 4200);
  }

  async function api(url, options = {}) {
    if (url.startsWith("/api/")) { const u = new URL(url, window.location.origin); u.searchParams.set("symbol", SYMBOL); url = `${u.pathname}${u.search}`; }
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
    const el = $("gChart");
    if (!el || !window.LightweightCharts) return;
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
        vertLine: { color: C.cross, style: 2 }, horzLine: { color: C.cross, style: 2 },
      },
      rightPriceScale: { borderColor: C.border, scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: C.border, timeVisible: true, secondsVisible: false, rightOffset: 5, barSpacing: 7, tickMarkFormatter: tick },
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
      axisLabelVisible: true, title: "LIVE",
    });
    chart.subscribeCrosshairMove((param) => {
      const last = state.rawCandles && state.rawCandles[state.rawCandles.length - 1];
      if (!param || !param.time || !param.seriesData) { if (last) showOHLC(last); return; }
      const bar = param.seriesData.get(state.candleSeries);
      if (bar) showOHLC(bar);
    });
    new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth, height: el.clientHeight })).observe(el);
    state.volume=window.BTCVolumePane?.create({chart,series:state.candleSeries,wrap:el});
    document.addEventListener("btcquant:ui", applyChartTheme);
  }

  function showOHLC(bar) {
    if (!bar) return;
    $("gO").textContent = money(bar.open);
    $("gH").textContent = money(bar.high);
    $("gL").textContent = money(bar.low);
    $("gC").textContent = money(bar.close);
  }

  async function loadCandles(fit = false) {
    const request=state.candleRequest=(state.candleRequest||0)+1;$("gChartStatus").textContent=state.interval==='30s'?'正在聚合真实 1 秒数据，首次历史加载可能较慢…':'正在加载 '+state.interval.toUpperCase()+' K 线…';
    try {
      const data = await api(`/api/candles?interval=${state.interval}&limit=${state.historyLimit}`);
      if(request!==state.candleRequest)return;state.chart.applyOptions({timeScale:{secondsVisible:state.interval==='30s'}});
      state.rawCandles = data.candles || [];
      state.volume?.setData(state.rawCandles,state.interval);
      state.candleSeries.setData(state.rawCandles.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));
      if (!state.chartFit || fit) { state.chart.timeScale().fitContent(); state.chartFit = true; }
      if (state.rawCandles.length) {
        const last = state.rawCandles[state.rawCandles.length - 1];
        showOHLC(last);
        const p = hktParts(last.time);
        $("gChartStatus").textContent = `${SYMBOL} · ${data.interval} · ${state.rawCandles.length} 根 · 最后 ${p.month}-${p.day} ${p.hour}:${p.minute} HKT`;
      }
    } catch (err) {
      if(request!==state.candleRequest)return;
      $("gChartStatus").textContent = `行情加载失败：${err.message}`;
    }
  }

  function updateNextBar() {
    const seconds = INTERVALS[state.interval] || 3600;
    const now = Date.now();
    const next = Math.ceil(now / (seconds * 1000)) * seconds * 1000;
    const d = Math.max(0, next - now);
    const h = Math.floor(d / 3600000);
    const m = Math.floor((d % 3600000) / 60000);
    const s = Math.floor((d % 60000) / 1000);
    const clock = new Date(next).toLocaleTimeString("zh-CN", { timeZone: HKT, hour: "2-digit", minute: "2-digit", hour12: false });
    $("gNextBar").textContent = `下一根 ${state.interval.toUpperCase()}: ${clock} HKT · ${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")} 后`;
  }

  function clearGridLines() {
    state.gridLines.forEach((line) => { try { state.candleSeries.removePriceLine(line); } catch (_) { /* ignore */ } });
    state.gridLines = [];
  }

  function syncGridLines(levels, price) {
    clearGridLines();
    levels.forEach((level) => {
      const p = Number(level.price);
      const holding = level.state === "holding";
      const color = holding ? "#16c784" : (p < price ? "#2a4a3d" : "#4a2f35");
      state.gridLines.push(state.candleSeries.createPriceLine({
        price: p, color, lineWidth: 1, lineStyle: 1, axisLabelVisible: false, title: "",
      }));
    });
  }

  function render(s) {
    state.status = s;
    const a = s.account || {};
    const running = !!s.running;
    if (!s.running && !state.configInit && Number(s.price) > 0) {
      const p = Number(s.price);
      const decimals = p < 1 ? 5 : p < 10 ? 4 : p < 100 ? 3 : 0;
      const step = p < 1 ? 0.0001 : p < 10 ? 0.001 : p < 100 ? 0.01 : 100;
      $("gLower").value = (p * 0.85).toFixed(decimals);
      $("gUpper").value = (p * 1.15).toFixed(decimals);
      $("gLower").step = String(step);
      $("gUpper").step = String(step);
      state.configInit = true;
    }
    $("gPrice").textContent = money(s.price);
    $("gEquity").textContent = money(a.equity);
    $("gSessionPnl").textContent = signed(a.session_pnl);
    $("gSessionPnl").className = cls(a.session_pnl);
    $("gCash").textContent = money(a.cash);
    $("gGridCash").textContent = money(a.grid_cash);
    $("gInventory").textContent = `${qty(a.inventory)} ${BASE}`;
    $("gAvgCost").textContent = money(a.avg_cost);
    $("gRealized").textContent = signed(a.realized_pnl);
    $("gRealized").className = cls(a.realized_pnl);
    $("gUnrealized").textContent = signed(a.unrealized_pnl);
    $("gUnrealized").className = cls(a.unrealized_pnl);
    $("gRunBadge").textContent = running ? "RUNNING" : "STOPPED";
    $("gRunBadge").className = `mini-badge ${running ? "green" : "neutral"}`;
    $("gStatusBadge").textContent = running ? "RUNNING" : "STOPPED";
    $("gStatusBadge").className = `mini-badge ${running ? "green" : "neutral"}`;
    $("gStartBtn").disabled = running || state.busy;
    $("gStopBtn").disabled = !running || state.busy;
    ["gLower", "gUpper", "gGrids", "gInvestment", "gMode", "gInitial", "gStop"].forEach((id) => { $(id).disabled = running; });

    const levels = s.levels || [];
    $("gLevelCount").textContent = String(levels.length);
    if (!levels.length) {
      $("gLevels").innerHTML = `<div class="empty-state">尚未启动</div>`;
    } else {
      $("gLevels").innerHTML = levels.slice().reverse().map((l) => {
        const isHolding = l.state === "holding";
        return `<div class="level-row">
          <span class="lprice">${money(l.price)}</span>
          <span class="lstate ${isHolding ? "holding" : "idle"}">${isHolding ? "持仓" : "等待"}</span>
          <span class="target">${isHolding && l.target !== null ? "→ " + money(levels[l.target]?.price) : "—"}</span>
        </div>`;
      }).join("");
    }

    const fills = s.fills || [];
    const events = s.events || [];
    const rows = fills.slice(0, 10).map((f) => `<div class="event-item ${f.side === "BUY" ? "trade" : ""}">
      <span class="event-time">${timeHKT(f.time)}</span>
      <span>${f.side} ${qty(f.qty)} @ ${money(f.price)} · ${f.pnl ? signed(f.pnl) : f.reason}</span>
    </div>`);
    const evs = events.slice(-8).reverse().map((e) => `<div class="event-item ${e.level}">
      <span class="event-time">${timeHKT(e.time)}</span><span>${e.message}</span>
    </div>`);
    $("gFills").innerHTML = [...rows, ...evs].join("") || `<div class="event-item"><span class="event-time">--:--:--</span><span>暂无成交</span></div>`;
    if (state.markLine) state.markLine.applyOptions({ price: s.price, color: "#e7edf5", title: "LIVE" });
    syncGridLines(levels, s.price);
    $("gLastUpdate").textContent = s.updated_at ? `api ${timeHKT(s.updated_at)} HKT` : "—";
    if (s.last_error) {
      $("gAlert").textContent = `服务错误：${s.last_error}`;
      $("gAlert").classList.remove("hidden");
    } else {
      $("gAlert").classList.add("hidden");
    }
    setTimeout(() => $("gLoading").classList.add("hide"), 80);
  }

  async function refresh() {
    try {
      const s = await api("/api/grid/status");
      render(s);
    } catch (err) {
      $("gAlert").textContent = `无法连接服务：${err.message}`;
      $("gAlert").classList.remove("hidden");
    }
  }

  function configPayload() {
    const lower = Number($("gLower").value);
    const upper = Number($("gUpper").value);
    const grids = Number($("gGrids").value);
    const investment = Number($("gInvestment").value);
    const initial = Number($("gInitial").value) / 100;
    const stop = Number($("gStop").value) / 100;
    if (![lower, upper, grids, investment, initial, stop].every(Number.isFinite)) throw new Error("请填写完整参数");
    return { lower, upper, grids, investment, mode: $("gMode").value, initial_ratio: initial, stop_loss_pct: stop };
  }

  async function startGrid() {
    if (state.busy) return;
    let payload;
    try { payload = configPayload(); } catch (err) { toast(err.message, "error"); return; }
    if (!(await ask(`${PAIR} · 启动现货网格\n区间 ${payload.lower}–${payload.upper}，${payload.grids} 格，投入 ${payload.investment} USDT`, { title: "启动网格" }))) return;
    state.busy = true;
    try {
      const s = await api("/api/grid/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      render(s);
      toast("网格已启动", "ok");
    } catch (err) { toast(`启动失败：${err.message}`, "error"); }
    finally { state.busy = false; }
  }

  async function stopGrid() {
    if (!(await ask("停止网格？库存会保留，不会自动卖出。", { title: "停止网格", danger: true }))) return;
    state.busy = true;
    try {
      const s = await api("/api/grid/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ liquidate: false }) });
      render(s);
      toast("网格已停止；库存保留", "ok");
    } catch (err) { toast(`停止失败：${err.message}`, "error"); }
    finally { state.busy = false; }
  }

  async function resetGrid() {
    if (!(await ask("重置网格模拟账户？所有库存、成交和盈亏会清空。", { title: "重置网格账户", danger: true }))) return;
    try {
      const s = await api("/api/grid/reset", { method: "POST" });
      render(s);
      toast("网格账户已重置", "ok");
    } catch (err) { toast(`重置失败：${err.message}`, "error"); }
  }

  function bind() {
    $("gIntervalSeg").addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-interval]");
      if (!btn || btn.dataset.interval === state.interval) return;
      state.interval = btn.dataset.interval;
      [...$("gIntervalSeg").querySelectorAll("button")].forEach((b) => b.classList.toggle("active", b === btn));
      state.chartFit = false;
      updateNextBar();
      loadCandles(true);
    });
    $("historyLimit").addEventListener("change", () => {
      state.historyLimit = Number($("historyLimit").value) || 800;
      state.chartFit = false;
      loadCandles(true);
    });
    $("gStartBtn").addEventListener("click", startGrid);
    $("gStopBtn").addEventListener("click", stopGrid);
    $("gResetBtn").addEventListener("click", resetGrid);
    $("gRefresh").addEventListener("click", () => { refresh(); loadCandles(false); });
  }

  function restructureLayout() {
    const main = document.querySelector("main.subpage-grid");
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
    const pair = $("gPair");
    if (pair) pair.innerHTML = `${BASE}<span>/${window.BTCSymbols ? window.BTCSymbols.quote : "USDT"}</span>`;
    if ($("gChartSymbol")) $("gChartSymbol").textContent = SYMBOL;
    if ($("gBaseLabel")) $("gBaseLabel").textContent = BASE;
    document.title = `${PAIR} 网格 · BTC Quant`;
  }

  async function boot() {
    const requested=new URLSearchParams(location.search).get('interval');if(requested in INTERVALS)state.interval=requested;
    document.querySelectorAll('#gIntervalSeg [data-interval]').forEach(b=>b.classList.toggle('active',b.dataset.interval===state.interval));
    restructureLayout();
    applySymbolLabels();
    initChart();
    bind();
    updateNextBar();
    if(document.documentElement.classList.contains('pane-mode')){await loadCandles(true);setInterval(()=>loadCandles(false),15000);setInterval(updateNextBar,1000);$("gLoading").classList.add('hide');return;}
    await Promise.all([refresh(), loadCandles(true)]);
    setInterval(refresh, 2000);
    setInterval(() => loadCandles(false), 15000);
    setInterval(updateNextBar, 1000);
    setTimeout(() => $("gLoading").classList.add("hide"), 8000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
