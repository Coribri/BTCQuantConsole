/* Shared display settings: theme + font scale. */
(() => {
  "use strict";
  const root = document.documentElement;
  const THEME_KEY = "btcquant.theme";
  const FONT_KEY = "btcquant.fontScale";
  const $ = (id) => document.getElementById(id);

  function readNumber(key, fallback) {
    try {
      const stored = localStorage.getItem(key);
      const value = stored === null ? fallback : Number(stored);
      return Number.isFinite(value) ? value : fallback;
    } catch (_) { return fallback; }
  }

  let modalResolve = null;

  function ensureModal() {
    let mask = document.getElementById("btcModal");
    if (mask) return mask;
    mask = document.createElement("div");
    mask.id = "btcModal";
    mask.className = "modal-mask hidden";
    mask.innerHTML = `
      <div class="modal-card" role="dialog" aria-modal="true">
        <div class="modal-head"><span id="btcModalTitle">请确认</span><button id="btcModalX" class="modal-x" aria-label="关闭">×</button></div>
        <div id="btcModalBody" class="modal-body"></div>
        <input id="btcModalInput" class="modal-input hidden" type="text" inputmode="decimal" autocomplete="off">
        <div class="modal-actions">
          <button id="btcModalCancel" class="modal-cancel">取消</button>
          <button id="btcModalOk" class="modal-ok">确定</button>
        </div>
      </div>`;
    document.body.appendChild(mask);
    const close = (value) => {
      mask.classList.add("hidden");
      if (modalResolve) {
        const resolve = modalResolve;
        modalResolve = null;
        resolve(mask._promptMode && value === false ? null : value);
      }
    };
    mask.querySelector("#btcModalOk").addEventListener("click", () => {
      const input = mask.querySelector("#btcModalInput");
      close(mask._promptMode ? input.value.trim() : true);
    });
    mask.querySelector("#btcModalCancel").addEventListener("click", () => close(false));
    mask.querySelector("#btcModalX").addEventListener("click", () => close(false));
    mask.addEventListener("click", (event) => { if (event.target === mask) close(false); });
    mask._close = close;
    return mask;
  }

  function confirmDialog(message, options = {}) {
    if (modalResolve) return Promise.resolve(false);
    const mask = ensureModal();
    const title = mask.querySelector("#btcModalTitle");
    const body = mask.querySelector("#btcModalBody");
    const ok = mask.querySelector("#btcModalOk");
    const cancel = mask.querySelector("#btcModalCancel");
    title.textContent = options.title || "请确认";
    body.textContent = String(message || "");
    ok.textContent = options.confirmText || "确定";
    cancel.textContent = options.cancelText || "取消";
    ok.classList.toggle("danger", !!options.danger);
    mask._promptMode = false;
    mask.querySelector("#btcModalInput").classList.add("hidden");
    mask.classList.remove("hidden");
    setTimeout(() => (options.focusCancel ? cancel : ok).focus(), 30);
    return new Promise((resolve) => { modalResolve = resolve; });
  }

  function promptDialog(message, defaultValue = "", options = {}) {
    const mask = ensureModal();
    mask.querySelector("#btcModalTitle").textContent = options.title || "请输入";
    mask.querySelector("#btcModalBody").textContent = String(message || "");
    const input = mask.querySelector("#btcModalInput");
    input.value = String(defaultValue);
    input.classList.remove("hidden");
    mask.querySelector("#btcModalOk").textContent = options.confirmText || "确定";
    mask.querySelector("#btcModalCancel").textContent = "取消";
    mask.querySelector("#btcModalOk").classList.remove("danger");
    mask._promptMode = true;
    mask.classList.remove("hidden");
    setTimeout(() => { input.focus(); input.select(); }, 30);
    return new Promise((resolve) => { modalResolve = resolve; });
  }

  function noticeDialog(message, options = {}) {
    return confirmDialog(message, { ...options, confirmText: "知道了", cancelText: "关闭" });
  }

  window.BTCUI = { confirm: confirmDialog, prompt: promptDialog, notice: noticeDialog };

  const chartSymbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT"];
  let multiLayout = 2;

  function ensureMulti() {
    let mask = document.getElementById("multiChart");
    if (mask) return mask;
    mask = document.createElement("div");
    mask.id = "multiChart";
    mask.className = "mc-mask hidden";
    mask.innerHTML = `
      <div class="mc-window">
        <div class="mc-head">
          <div><b>多图 · 均线</b><span class="mc-hint">拖动每个窗格右下角可拉伸</span></div>
          <div class="mc-actions">
            <button data-mc-layout="2" class="active">2 图</button>
            <button data-mc-layout="4">4 图</button>
            <button id="mcClose" class="mc-close" aria-label="关闭">×</button>
          </div>
        </div>
        <div id="mcGrid" class="mc-grid layout-2"></div>
      </div>`;
    document.body.appendChild(mask);
    const close = () => mask.classList.add("hidden");
    mask.querySelector("#mcClose").addEventListener("click", close);
    mask.addEventListener("click", (event) => { if (event.target === mask) close(); });
    mask.querySelectorAll("[data-mc-layout]").forEach((btn) => {
      btn.addEventListener("click", () => {
        multiLayout = Number(btn.dataset.mcLayout) === 4 ? 4 : 2;
        mask.querySelectorAll("[data-mc-layout]").forEach((b) => b.classList.toggle("active", b === btn));
        buildMulti();
      });
    });
    return mask;
  }

  function buildMulti() {
    const mask = ensureMulti();
    const grid = mask.querySelector("#mcGrid");
    const current = (window.BTCSymbols && window.BTCSymbols.current) || "BTCUSDT";
    const symbols = [current, ...chartSymbols.filter((s) => s !== current)].slice(0, multiLayout);
    grid.className = `mc-grid layout-${multiLayout}`;
    grid.innerHTML = symbols.map((symbol) => {
      const url = new URL(window.location.href);
      url.searchParams.set("symbol", symbol);
      url.searchParams.set("window", "1");
      url.searchParams.set("pane", "1");
      return `<div class="mc-pane"><iframe src="${url.toString()}" title="${symbol} 图表" loading="lazy"></iframe></div>`;
    }).join("");
  }

  function openMulti() {
    const mask = ensureMulti();
    mask.classList.remove("hidden");
    buildMulti();
  }

  function initMultiWindow() {
    if (window.location.pathname==='/' || window.location.pathname==='/futures' || window.location.pathname==='/grid') return;
    const btn = document.getElementById("multiBtn");
    if (btn) btn.addEventListener("click", openMulti);
  }

  const pageParams = new URLSearchParams(window.location.search);
  if (pageParams.get("window") === "1") document.documentElement.classList.add("chart-window");
  if (pageParams.get("pane") === "1") root.classList.add("pane-mode");
  const tradeNames = { "/": "现货", "/futures": "合约", "/grid": "现货网格" };
  const chartOnly = root.classList.contains("chart-window") || root.classList.contains("pane-mode");
  const liveMode = !chartOnly && pageParams.get("account") === "live";
  if (chartOnly && pageParams.get("context") === "live-reference") root.classList.add("live-reference");
  root.dataset.accountMode = liveMode ? "live" : "paper";
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initMultiWindow);
  else initMultiWindow();

  function apply(theme, scale, persist = true) {
    const nextTheme = theme === "light" ? "light" : "dark";
    const nextScale = Math.max(0.9, Math.min(1.3, Number(scale) || 1));
    root.dataset.theme = nextTheme;
    root.style.setProperty("--font-scale", String(nextScale));
    if (persist) {
      try {
        localStorage.setItem(THEME_KEY, nextTheme);
        localStorage.setItem(FONT_KEY, String(nextScale));
      } catch (_) { /* ignore */ }
    }
    const label = $("uiFontLabel");
    if (label) label.textContent = `${Math.round(nextScale * 100)}%`;
    const range = $("uiFont");
    if (range) {
      range.value = String(Math.round(nextScale * 100));
      $("uiSmaller").disabled = Number(range.value) <= 90;
      $("uiLarger").disabled = Number(range.value) >= 130;
    }
    document.querySelectorAll("[data-ui-theme]").forEach((btn) => {
      const active = btn.dataset.uiTheme === nextTheme;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", String(active));
    });
    window.dispatchEvent(new CustomEvent("btcquant:ui", {
      detail: { theme: nextTheme, fontScale: nextScale },
    }));
  }

  function initMode() {
    const brand = document.querySelector(".topbar .brand");
    if (!brand || chartOnly) return;
    const control = document.createElement("button");
    control.type = "button";
    control.className = "account-switch";
    control.setAttribute("role", "switch");
    control.setAttribute("aria-label", "切换到真实账户交易");
    control.title = liveMode ? "返回虚拟仓，不使用主网账户" : "切换到交易所真实账户";
    control.setAttribute("aria-checked", String(liveMode));
    control.innerHTML = '<span>模拟交易</span><span>实盘交易</span>';
    control.addEventListener("click", () => {
      const url = new URL(location.href);
      if (window.BTCSymbols?.current) url.searchParams.set("symbol", window.BTCSymbols.current);
      if (liveMode) {
        url.searchParams.delete("account");
        url.searchParams.delete("venue");
      } else url.searchParams.set("account", "live");
      location.assign(url);
    });
    brand.after(control);
    if (!liveMode) return;
    for (const link of document.querySelectorAll(".main-nav a, .brand-link")) {
      const url = new URL(link.href);
      url.searchParams.set("account", "live");
      link.href = url.pathname + url.search + url.hash;
    }
    if (tradeNames[location.pathname]) {
      const subtitle = brand.querySelector("p");
      if (subtitle) subtitle.textContent = "LIVE ACCOUNT · CONFIRM EACH ORDER";
      mountLiveAccount();
    }
  }

  async function liveJson(path, data, method = data === undefined ? "GET" : "POST", timeout = 0) {
    const controller = timeout ? new AbortController() : null;
    const timer = controller && setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(path, {
        cache: "no-store",
        method,
        signal: controller?.signal,
        ...(data === undefined ? {} : {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        }),
      });
      if (response.status === 404) {
        const error = new Error("当前后端不支持此接口。");
        error.unsupported = true;
        error.status = 404;
        throw error;
      }
      let value = {};
      try { value = await response.json(); } catch (_) { value = {}; }
      if (!response.ok || value.error) {
        const message = value.error || (typeof value.detail === "string" ? value.detail : "请求失败");
        const error = new Error(typeof message === "string" ? message : "请求失败");
        error.status = response.status;
        error.code = value.code ?? null;
        error.reason = value.reason ?? null;
        error.hint = value.hint ?? null;
        error.retryable = Boolean(value.retryable);
        throw error;
      }
      return value;
    } catch (error) {
      if (controller?.signal.aborted) throw Error("本机请求等待超时");
      throw error;
    } finally {
      if (controller) clearTimeout(timer);
    }
  }

  function exchangeError(error) {
    const capabilities = window.BTCCapabilities;
    if (capabilities?.errorText) return capabilities.errorText(error);
    return typeof error === "string" ? error : error?.message || "请求失败";
  }

  function mountLiveAccount() {
    document.body.classList.add("trading-terminal");
    const top = document.querySelector(".topbar");
    const name = tradeNames[location.pathname];
    const futures = location.pathname === "/futures";
    const dex = futures && pageParams.get("venue") === "hyperliquid";
    const grid = location.pathname === "/grid";
    const symbol = window.BTCSymbols?.current || "BTCUSDT";
    const label = window.BTCSymbols?.label || "BTC/USDT";
    const section = document.createElement("main");
    section.className = "live-workspace";
    section.innerHTML = `
      <section class="live-summary panel">
        <div><span class="live-tag">真实资金</span><h2 id="liveTitle"></h2></div>
        <label for="liveExchange">交易所 <select id="liveExchange"><option value="binance">Binance</option><option value="coinw">CoinW</option>${futures ? '<option value="hyperliquid">Hyperliquid · 链上</option>' : ""}</select></label>
      </section>
      <section class="live-connection-bar panel" aria-label="交易连接状态">
        <div><strong data-connection-title>正在核验账户</strong><p data-connection-message role="status">公开行情与交易账户独立连接。</p></div>
        <button type="button" class="secondary" data-refresh-account aria-label="重新检测账户与能力状态">重新检测</button>
      </section>
      <section class="live-chart panel">
        <h3>市场参考图 <small>Binance 公开行情</small></h3>
        <iframe title="公开市场 K 线"></iframe>
      </section>
      <aside class="live-account panel">
        <div class="live-account-head"><h3 id="liveAccountTitle"></h3><span id="liveBadge" class="mini-badge neutral">检查中</span></div>
        <p id="liveStatus" class="hint" role="status">正在检查连接…</p>
        <div id="liveBalances"></div>
        <div id="liveAssetPie" class="live-asset-pie" hidden></div>
        <details class="live-risk-settings">
          <summary>手动下单上限 <span id="liveRiskSummary">测试模式</span></summary>
          <form id="liveRiskForm">
            <label>单笔现货上限（USDT）<input name="max_spot_notional" type="number" min="0" step="0.01" required></label>
            <label>单笔合约保证金上限（USDT）<input name="max_futures_margin" type="number" min="0" step="0.01" required></label>
            <label>网格总预算上限（USDT）<input name="max_grid_budget" type="number" min="0" step="0.01" required></label>
            <label>日亏损上限（USDT）<input name="max_daily_loss" type="number" min="0" step="0.01" required></label>
            <label>最大杠杆<input name="max_leverage" type="number" min="1" step="1" required></label>
            <label class="live-risk-check"><input name="test_mode" type="checkbox"> 测试模式（建议保持开启）</label>
            <button type="submit" class="primary">保存上限</button>
            <p class="hint" data-live-risk-status></p>
          </form>
        </details>
        <details class="live-connection">
          <summary>API 连接设置</summary>
          <form id="liveConnectionForm">
            <label>API Key<input name="key" type="password" autocomplete="off" required></label>
            <label>API Secret<input name="secret" type="password" autocomplete="off" required></label>
            <div class="live-actions">
              <button type="submit" class="primary">保存连接</button>
              <button type="button" id="liveRead" class="secondary">读取账户</button>
            </div>
            <button type="button" id="liveClear" class="live-clear">清除本机连接</button>
          </form>
        </details>
      </aside>
      <section class="live-orders panel">
        <div><h3 id="liveOrdersTitle"></h3><p class="hint" id="liveExecutionStatus"></p></div>
      </section>`;
    top.after(section);
    $("liveTitle").textContent = name + " · " + label;
    $("liveOrdersTitle").textContent = grid ? "实盘网格" : "实盘委托";
    $("liveExecutionStatus").textContent = grid ? "真实挂单与成交对账待接通。" : "真实交易执行待接通。当前可读取账户。";
    const exchange = $("liveExchange");
    const providerKey = "btcquant.liveExchange." + (futures ? "futures" : "spot");
    function changeDexVenue(useDex) {
      const url = new URL(location.href);
      if (useDex) url.searchParams.set("venue", "hyperliquid");
      else {
        url.searchParams.delete("venue");
        try { localStorage.setItem(providerKey, exchange.value); } catch (_) {}
      }
      location.assign(url);
    }
    if (dex) {
      section.querySelector(".live-connection-bar").remove();
      exchange.value = "hyperliquid";
      exchange.addEventListener("change", () => changeDexVenue(false));
      mountHyperliquid(section, symbol.slice(0, -4));
      return;
    }
    section.classList.add("cex-workspace");
    mountLiveMarket(section, symbol, futures);
    section.querySelector("iframe").src = "/?window=1&pane=1&layout=1&context=live-reference&symbol=" + encodeURIComponent(symbol);
    window.BTCLiveChartResize?.(section.querySelector('.live-chart'));
    try { exchange.value = localStorage.getItem(providerKey) === "coinw" ? "coinw" : "binance"; } catch (_) {}
    const form = $("liveConnectionForm");
    const status = $("liveStatus");
    const badge = $("liveBadge");
    const connection = form.closest("details");
    const riskForm = $("liveRiskForm");
    const riskStatus = riskForm?.querySelector("[data-live-risk-status]");
    const riskSummary = $("liveRiskSummary");
    function fillRisk(value) {
      if (!riskForm || !value) return;
      for (const name of ["max_spot_notional", "max_futures_margin", "max_grid_budget", "max_daily_loss", "max_leverage"]) {
        riskForm.elements[name].value = value[name] ?? "";
      }
      riskForm.elements.test_mode.checked = Boolean(value.test_mode);
      if (riskSummary) riskSummary.textContent = (value.test_mode ? "测试模式" : "已解锁")
        + (grid ? " · 网格预算 " + Number(value.max_grid_budget || 0)
          : futures ? " · 单笔保证金 " + Number(value.max_futures_margin || 0)
            : " · 单笔现货 " + Number(value.max_spot_notional || 0)) + " USDT";
    }
    async function loadRisk() {
      try { fillRisk(await liveJson("/api/live/manual-risk")); }
      catch (error) { if (riskStatus) riskStatus.textContent = error.message; }
    }
    riskForm?.addEventListener("submit", async event => {
      event.preventDefault();
      const body = {
        max_spot_notional: Number(riskForm.elements.max_spot_notional.value),
        max_futures_margin: Number(riskForm.elements.max_futures_margin.value),
        max_grid_budget: Number(riskForm.elements.max_grid_budget.value),
        max_daily_loss: Number(riskForm.elements.max_daily_loss.value),
        max_leverage: Number(riskForm.elements.max_leverage.value),
        test_mode: riskForm.elements.test_mode.checked,
      };
      if (!body.test_mode && !await confirmDialog("关闭测试模式会解除 10U 单笔保护。确认继续？", {
        title: "解除测试上限", confirmText: "确认解锁", danger: true, focusCancel: true,
      })) return;
      try {
        riskStatus.textContent = "正在保存…";
        const saved = await liveJson("/api/live/manual-risk", body);
        fillRisk(saved);
        window.dispatchEvent(new CustomEvent("btcquant:risk", {detail:saved}));
        riskStatus.textContent = body.test_mode ? "已保存，测试上限生效。" : "已保存，测试模式已关闭。";
      } catch (error) { riskStatus.textContent = error.message; }
    });
    void loadRisk();
    let revision = 0;
    /* S1 (requirements §13.1): the account read is not a capability probe. Until a result
       actually lands (success or failure), the legacy title/badge must stay UNKNOWN and the
       shared renderer must never turn `{ready:false}` into READY or a stale READY hint. */
    let accountSettled = false;
    let configured = false;
    let trading = null;
    let strategy = null;
    let tradingAvailable = false;
    let lastSnapshot = null;
    let prices = { USDT: 1 };
    void loadPrices();
    const snapshotCanTrade = () => Boolean(lastSnapshot?.configured && !lastSnapshot.error
      && (lastSnapshot.trade_permission === "confirmed" || exchange.value === "coinw" && lastSnapshot.trade_permission === "unknown"));
    const connectionBar = section.querySelector(".live-connection-bar");
    const refreshButton = section.querySelector("[data-refresh-account]");
    const applyPermissions = (message, pending = !accountSettled) => {
      const ready = snapshotCanTrade();
      const capabilities = window.BTCCapabilities;
      if (capabilities?.renderBar) {
        // Four-state contract lives in capabilities.js (requirements §6.1/§6.2); `pending` keeps
        // the bar UNKNOWN until both /api/capabilities and /api/exchange/health settle (S1).
        capabilities.renderBar(section, {ready, message, pending: Boolean(pending)});
      } else {
        // Legacy degradation without the capability module: the four states cannot be verified
        // from a single account read, so never render READY here — UNKNOWN (or a definitive
        // BLOCKED after a failed read) is the only honest output (requirements §13.1).
        const failed = accountSettled && !ready;
        connectionBar.dataset.state = failed ? "blocked" : "unknown";
        connectionBar.dataset.connectionState = failed ? "blocked" : "unknown";
        connectionBar.querySelector("[data-connection-title]").textContent = failed
          ? "交易未就绪 · 下单锁定" : "状态未核验 · 下单锁定";
        connectionBar.querySelector("[data-connection-message]").textContent = message || "正在核验账户权限…";
        badge.textContent = failed ? "不可用" : "未核验";
        badge.className = failed ? "mini-badge neutral danger" : "mini-badge neutral";
        badge.dataset.state = failed ? "blocked" : "unknown";
      }
      trading?.enabled(snapshotCanTrade(), message);
      strategy?.enabled(snapshotCanTrade());
      const flatten = section.querySelector("[data-live-flatten]");
      if (flatten) flatten.disabled = !snapshotCanTrade() || exchange.value !== "binance";
    };

    function endpoint() {
      if (exchange.value === "binance") return futures ? "binance-futures" : "binance";
      return futures ? "coinw" : "coinw-spot";
    }
    function row(name, value) {
      const line = document.createElement("div");
      const label = document.createElement("span");
      const amount = document.createElement("b");
      label.textContent = name;
      amount.textContent = value ?? "—";
      line.append(label, amount);
      return line;
    }
    async function loadPrices() {
      try {
        const data = await liveJson("/api/market/overview");
        for (const item of data.watch || []) prices[item.base] = Number(item.price) || 0;
        await Promise.all(["BTC","ETH","BNB","XRP","TRX","DOGE"].map(async base => {
          if (prices[base]) return;
          try { const item = await liveJson("/api/market/price?symbol=" + base + "USDT"); prices[base] = Number(item.price) || 0; } catch (_) {}
        }));
      } catch (_) { /* keep last prices */ }
      if (lastSnapshot) renderSnapshot(lastSnapshot);
    }
    const PIE_COLORS = ["#36caaa", "#a597ff", "#f0b90b", "#3d9cf0", "#ef6a6a", "#d58cff", "#7f8fa4"];
    function renderLivePie(items, total) {
      const box = $("liveAssetPie");
      if (!box) return;
      if (!items.length || total <= 0) {
        box.hidden = true;
        box.replaceChildren();
        return;
      }
      let cursor = 0;
      const segments = items.map((item, index) => {
        const start = cursor;
        cursor += item.value / total * 100;
        return PIE_COLORS[index % PIE_COLORS.length] + " " + start.toFixed(2) + "% " + cursor.toFixed(2) + "%";
      });
      const pie = document.createElement("div");
      pie.className = "live-pie";
      pie.style.background = "conic-gradient(" + segments.join(",") + ")";
      const hole = document.createElement("span");
      hole.className = "live-pie-hole";
      hole.textContent = total.toFixed(2);
      pie.append(hole);
      const legend = document.createElement("div");
      legend.className = "live-pie-legend";
      for (const [index, item] of items.entries()) {
        const line = document.createElement("div");
        const dot = document.createElement("i");
        dot.style.background = PIE_COLORS[index % PIE_COLORS.length];
        const name = document.createElement("span");
        name.textContent = item.asset + " · " + (item.value / total * 100).toFixed(1) + "%";
        const value = document.createElement("b");
        value.textContent = item.value.toFixed(2);
        line.append(dot, name, value);
        legend.append(line);
      }
      box.replaceChildren(pie, legend);
      box.hidden = false;
    }
    function renderSnapshot(snapshot) {
      const account = snapshot.account;
      let rows;
      if (snapshot.balances) {
        let total = 0;
        const pieItems = [];
        const lines = snapshot.balances.map(item => {
          const quantity = Number(item.total ?? item.free ?? 0);
          const price = Number(prices[item.asset] ?? (item.asset === "USDT" ? 1 : 0));
          const value = quantity * price;
          total += value;
          if (quantity > 0) pieItems.push({ asset: item.asset, value });
          return row(item.asset + " 可用", String(item.free) + " · " + value.toFixed(2) + " USDT");
        });
        rows = [row("账户总值 · USDT", total.toFixed(2)), ...lines];
        renderLivePie(pieItems.sort((a, b) => b.value - a.value), total);
      } else {
        renderLivePie([], 0);
        rows = [["账户权益 · USDT", account?.equity], ["可用保证金 · USDT", account?.available_usdt],
           ["未实现盈亏 · USDT", account?.unrealized], ["已用保证金 · USDT", account?.margin_used]]
            .filter(([, value]) => value != null).map(([key, value]) => row(key, value));
      }
      $("liveBalances").replaceChildren(...(rows.length ? rows : [row("账户暂无可用资产", "—")]));
      const pane = section.querySelector("[data-live-positions]");
      if (pane) {
        const list = (snapshot.positions || []).map(item => row(
          [item.symbol || item.instrument, item.side === "LONG" ? "多仓" : item.side === "SHORT" ? "空仓" : item.side].filter(Boolean).join(" "),
          [item.qty, item.leverage ? item.leverage + "×" : null,
           item.entry != null ? "开仓 " + item.entry : null, item.liq != null ? "强平 " + item.liq : null,
           item.unrealized != null ? "浮盈亏 " + item.unrealized : null].filter(value => value != null).join(" · ")
        ));
        pane.replaceChildren(...(list.length ? list : [row("暂无持仓", "—")]));
      }
    }
    async function connect(action = "", data) {
      const run = ++revision;
      const provider = endpoint();
      const venue = exchange.selectedOptions[0].textContent;
      const base = "/api/exchange/" + provider;
      const fields = [...form.elements];
      let supported = true;
      accountSettled = false;
      refreshButton.disabled = true;
      refreshButton.textContent = "核验中…";
      trading?.account({});
      renderLivePie([], 0);
      $("liveBalances").replaceChildren();
      fields.forEach(field => { field.disabled = true; });
      lastSnapshot = null;
      applyPermissions("正在核对连接与交易权限…", true);
      status.textContent = "正在检查 " + venue + " 连接…";
      try {
        const result = action === "/test"
          ? await liveJson("/api/live/account?" + new URLSearchParams({exchange:exchange.value, market:futures?"futures":"spot", symbol}))
          : action === "/clear" ? await liveJson(base, undefined, "DELETE") : await liveJson(base + action, data);
        if (run !== revision) return;
        configured = Boolean(result.configured);
        if (result.error) throw Error(result.error);
        if (result.api_key_masked) form.elements.key.placeholder = result.api_key_masked;
        if (action === "/settings") form.reset();
        if (action === "/clear") {
          form.reset();
          form.elements.key.placeholder = "";
          $("liveBalances").replaceChildren();
          section.querySelector("[data-live-positions]")?.replaceChildren();
        }
        if (action !== "/test" && result.configured) return connect("/test");
        if (action === "/test" && result.configured) {
          renderSnapshot(result);
          lastSnapshot = result;
          trading?.account(result);
        }
        accountSettled = true;
        const ready = snapshotCanTrade();
        status.textContent = !configured ? "请在此配置该市场的主网 API。"
          : result.trade_permission === "confirmed" ? "账户读取成功 · 交易权限已核验。"
            : result.trade_permission === "denied" ? "读取成功，但没有交易权限。"
              : "读取成功，交易权限未核验。" + (result.permission_error ? " " + exchangeError(result.permission_error) : "");
        // Connection title/badge are owned by capabilities.js (four states); applyPermissions() renders them.
        applyPermissions((ready ? status.textContent : "下单已锁定。 " + status.textContent)
          + " · 核验 " + new Date().toLocaleTimeString("zh-CN", {hour12:false}));
        connection.open = !ready;
      } catch (error) {
        if (run !== revision) return;
        supported = !error.unsupported;
        accountSettled = true;
        lastSnapshot = null;
        status.textContent = exchangeError(error);
        trading?.account({});
        renderLivePie([], 0);
        applyPermissions("账户读取失败。" + exchangeError(error));
        $("liveBalances").replaceChildren();
        section.querySelector("[data-live-positions]")?.replaceChildren();
        connection.hidden = !supported;
        connection.open = supported;
      } finally {
        if (run === revision) {
          refreshButton.disabled = false;
          refreshButton.textContent = "重新检测";
          fields.forEach(field => { field.disabled = !supported; });
          $("liveRead").disabled = !supported || !configured;
          $("liveClear").disabled = !supported || !configured;
        }
      }
    }
    function selectExchange() {
      if (exchange.value === "hyperliquid") return changeDexVenue(true);
      try { localStorage.setItem(providerKey, exchange.value); } catch (_) {}
      form.reset();
      connection.hidden = false;
      trading?.exchangeChanged();
      strategy?.exchangeChanged();
      configured = false;
      lastSnapshot = null;
      form.elements.key.placeholder = "";
      $("liveBalances").replaceChildren();
      section.querySelector("[data-live-positions]")?.replaceChildren();
      $("liveAccountTitle").textContent = exchange.selectedOptions[0].textContent + " · " + (futures ? "合约账户" : "现货账户");
      void connect();
    }
    refreshButton.addEventListener("click", () => connect());
    exchange.addEventListener("change", selectExchange);
    $("liveRead").addEventListener("click", () => connect("/test"));
    $("liveClear").addEventListener("click", async () => {
      const venue = exchange.value;
      if (await confirmDialog("清除本机保存的连接？交易所的持仓和挂单会保留。", {title:"清除连接", danger:true})
          && venue === exchange.value) void connect("/clear");
    });
    form.addEventListener("submit", event => {
      event.preventDefault();
      void connect("/settings", { api_key: form.elements.key.value.trim(), api_secret: form.elements.secret.value.trim() });
    });
    selectExchange();
    liveJson("/api/live/features").then(features => {
      tradingAvailable = Boolean(features.manual);
      if (tradingAvailable) {
        const subtitle = document.querySelector(".topbar .brand p");
        if (subtitle) subtitle.textContent = "LIVE TRADING";
      }
      if (features.manual && !grid) {
        trading = mountLiveTrade(section, exchange, symbol, futures, () => connect("/test", {}));
        if (lastSnapshot) trading.account(lastSnapshot);
        applyPermissions(status.textContent);
      }
      if ((grid && features.grid) || (!grid && features.auto?.length)) {
        strategy = mountLiveStrategy(section, exchange, symbol, futures, grid, features);
        strategy.enabled(snapshotCanTrade());
      }
      mountLiveRisk(section, exchange, futures, snapshotCanTrade, () => connect("/test"));
      // /api/live/features carries a cache-only capability snapshot (zero venue requests); feed it to
      // the shared four-state renderer without re-probing the exchange.
      window.BTCCapabilities?.renderBar?.(section, null, features.capabilities);
    }).catch(() => {});
  }

  function mountLiveMarket(section, symbol, futures) {
    const center = document.createElement("div");
    center.className = "live-center";
    const watch = document.createElement("aside");
    watch.className = "live-watch panel";
    watch.innerHTML = '<h3>市场观察</h3><p>Binance 公开行情</p><div data-market-list></div>'
      + '<div class="live-depth"><div class="live-depth-head"><h4>现货盘口</h4><small data-depth-time>—</small></div>'
      + '<div data-depth-rows></div><div class="live-depth-mid" data-depth-mid>—</div></div>'
      + '<div class="live-longshort"><div class="live-depth-head"><h4>多空比</h4><small>Binance Futures</small></div>'
      + '<div class="live-ls-ratio" data-ls-ratio>—</div>'
      + '<div class="live-ls-bar"><i data-ls-long-bar></i></div>'
      + '<div class="live-ls-row"><span>多</span><b data-ls-long>—</b></div>'
      + '<div class="live-ls-row"><span>空</span><b data-ls-short>—</b></div>'
      + '<p>Binance Futures 全球账户多空比</p></div>'
      + '<div class="live-flow"><div class="live-depth-head"><h4>资金流</h4><small>近 24h 主动买卖</small></div>'
      + '<div class="live-flow-net" data-flow-net>—</div>'
      + '<div class="live-ls-row"><span>主动买入</span><b data-flow-buy>—</b></div>'
      + '<div class="live-ls-row"><span>主动卖出</span><b data-flow-sell>—</b></div>'
      + '<div class="live-flow-bars" data-flow-bars></div></div>'
      + '<div class="live-lev"><div class="live-depth-head"><h4>杠杆</h4><small>Binance Futures</small></div>'
      + '<div class="live-ls-row"><span>资金费率</span><b data-lev-funding>—</b></div>'
      + '<div class="live-ls-row"><span>未平仓量</span><b data-lev-oi>—</b></div>'
      + '<div class="live-ls-row"><span>标记价格</span><b data-lev-mark>—</b></div>'
      + '<div class="live-ls-row"><span>行情来源</span><b data-lev-source>Binance 现货参考</b></div></div>';
    const summary = section.querySelector(".live-summary");
    const strip = document.createElement("dl");
    strip.className = "live-market-strip";
    strip.innerHTML = '<div><dt>现货参考价</dt><dd data-live-quote>—</dd></div><div><dt>24H 涨跌</dt><dd data-live-change>—</dd></div>'
      + '<div><dt>24H 最高 / 最低</dt><dd data-live-range>—</dd></div>'
      + '<div><dt>24H 成交额 · USDT</dt><dd data-live-volume>—</dd></div>'
      + (futures ? '<div><dt>Binance 资金费率</dt><dd data-live-funding>—</dd></div>' : '');
    summary.append(strip);
    section.querySelector(".live-connection-bar").after(watch, center);
    if (futures) {
      const heading = section.querySelector(".live-chart h3");
      // Requirement 6.4: keep the honest "spot reference" label until /api/futures/market
      // returns native=true; never switch the title early.
      heading.innerHTML = '现货参考图 <small>Binance 现货 · 非合约成交价</small>';
      const venue = section.querySelector("#liveExchange")?.value || "binance";
      window.BTCCapabilities?.futures?.({exchange: venue, symbol})
        .then(data => {
          if (!data || data.native !== true || data.mark_price == null) return;
          const funding = data.funding_rate == null ? "—" : (Number(data.funding_rate) * 100).toFixed(4) + "%";
          heading.innerHTML = "合约图 <small>Binance Futures 原生行情 · 标记价 " + data.mark_price
            + " · 资金费率 " + funding + "</small>";
          const source = section.querySelector("[data-lev-source]");
          if (source) {
            const at = data.fetched_at ? new Date(data.fetched_at).toLocaleTimeString("zh-CN", {hour12: false}) : "—";
            source.textContent = "Binance Futures 原生 · " + at
              + (Array.isArray(data.degradations) && data.degradations.includes("margin_mode_unavailable") ? " · 保证金/持仓模式未读取" : "");
          }
        })
        .catch(() => {});
    }
    const orders = section.querySelector(".live-orders");
    if (futures) {
      const positions = document.createElement("section");
      positions.className = "panel live-positions";
      positions.innerHTML = '<h3>交易所持仓</h3><div data-live-positions></div>';
      center.append(section.querySelector(".live-chart"), positions, orders);
    } else center.append(section.querySelector(".live-chart"), orders);
    const list = watch.querySelector("[data-market-list]");
    const depthRows = watch.querySelector("[data-depth-rows]");
    const depthMid = watch.querySelector("[data-depth-mid]");
    const depthTime = watch.querySelector("[data-depth-time]");
    const lsRatio = watch.querySelector("[data-ls-ratio]");
    const lsLong = watch.querySelector("[data-ls-long]");
    const lsShort = watch.querySelector("[data-ls-short]");
    const lsBar = watch.querySelector("[data-ls-long-bar]");
    const flowNet = watch.querySelector("[data-flow-net]");
    const flowBuy = watch.querySelector("[data-flow-buy]");
    const flowSell = watch.querySelector("[data-flow-sell]");
    const flowBars = watch.querySelector("[data-flow-bars]");
    const levFunding = watch.querySelector("[data-lev-funding]");
    const levOi = watch.querySelector("[data-lev-oi]");
    const levMark = watch.querySelector("[data-lev-mark]");
    function renderLongShort(leverage) {
      const longPct = Number(leverage?.long_pct);
      const shortPct = Number(leverage?.short_pct);
      if (!Number.isFinite(longPct) || !Number.isFinite(shortPct)) {
        lsRatio.textContent = "—";
        lsLong.textContent = "—";
        lsShort.textContent = "—";
        lsBar.style.width = "0%";
        return;
      }
      lsRatio.textContent = leverage.long_short_ratio == null
        ? (longPct / Math.max(0.01, shortPct)).toFixed(2)
        : Number(leverage.long_short_ratio).toFixed(2);
      lsLong.textContent = (longPct * 100).toFixed(1) + "%";
      lsShort.textContent = (shortPct * 100).toFixed(1) + "%";
      lsBar.style.width = Math.max(0, Math.min(100, longPct * 100)) + "%";
    }
    let depthBusy = false;
    const depthPrice = value => Number(value).toLocaleString("en-US", {maximumFractionDigits: 2});
    function renderDepth(data) {
      const bids = (data.bids || []).slice(0, 8);
      const asks = (data.asks || []).slice(0, 8);
      const line = (row, side) => {
        const item = document.createElement("div");
        item.className = "live-depth-row " + side;
        const price = document.createElement("span");
        price.textContent = depthPrice(row[0]);
        const qty = document.createElement("b");
        qty.textContent = Number(row[1]).toFixed(5);
        item.append(price, qty);
        return item;
      };
      depthRows.replaceChildren(...asks.map(row => line(row, "ask")), ...bids.map(row => line(row, "bid")));
      const mid = asks.length && bids.length ? (Number(asks[0][0]) + Number(bids[0][0])) / 2 : null;
      depthMid.textContent = mid == null ? "—" : depthPrice(mid);
      depthTime.textContent = data.received_at ? new Date(data.received_at).toLocaleTimeString("zh-CN", {hour12: false}) : "—";
      watch.querySelector(".live-depth h4").textContent = futures ? "合约盘口" : "现货盘口";
    }
    function money(value) {
      const number = Number(value);
      if (!Number.isFinite(number)) return "—";
      const sign = number >= 0 ? "+" : "-";
      return sign + Math.abs(number).toLocaleString("en-US", {maximumFractionDigits: 2}) + " USDT";
    }
    function renderFlow(flow) {
      if (!flow || flow.error) {
        flowNet.textContent = "资金流暂不可用";
        flowNet.className = "live-flow-net muted";
        flowBuy.textContent = "—";
        flowSell.textContent = "—";
        flowBars.replaceChildren();
        return;
      }
      flowNet.textContent = money(flow.net);
      flowNet.className = "live-flow-net " + (Number(flow.net) >= 0 ? "positive" : "negative");
      flowBuy.textContent = money(flow.buy);
      flowSell.textContent = money(-Math.abs(Number(flow.sell) || 0));
      const bars = (flow.bars || []).slice(-14);
      const peak = Math.max(1, ...bars.flatMap(bar => [Number(bar.buy) || 0, Number(bar.sell) || 0]));
      flowBars.replaceChildren(...bars.map(bar => {
        const item = document.createElement("div");
        const buy = document.createElement("i");
        const sell = document.createElement("i");
        buy.style.height = Math.max(1, (Number(bar.buy) || 0) / peak * 22) + "px";
        sell.style.height = Math.max(1, (Number(bar.sell) || 0) / peak * 22) + "px";
        item.append(buy, sell);
        return item;
      }));
    }
    function renderLeverage(leverage) {
      const funding = Number(leverage?.funding_rate);
      const oi = Number(leverage?.open_interest);
      const mark = Number(leverage?.mark_price);
      levFunding.textContent = Number.isFinite(funding) ? (funding * 100).toFixed(4) + "%" : "—";
      levOi.textContent = Number.isFinite(oi) ? oi.toLocaleString("en-US", {maximumFractionDigits: 0}) : "—";
      levMark.textContent = Number.isFinite(mark) && mark > 0 ? mark.toLocaleString("en-US", {maximumFractionDigits: 2}) : "—";
    }
    async function refreshDepth() {
      if (document.hidden || depthBusy) return;
      depthBusy = true;
      try {
        const query = new URLSearchParams({market: futures ? "futures" : "spot", symbol});
        renderDepth(await liveJson("/api/live/depth?" + query));
      } catch (_) {
        depthTime.textContent = "盘口暂不可用";
      } finally { depthBusy = false; }
    }
    const fmt = (n, digits = 4) => n == null || !Number.isFinite(Number(n)) ? "—"
      : Number(n).toLocaleString("en-US", {maximumFractionDigits:digits});
    async function refresh() {
      if (document.hidden) return;
      try {
        const data = await liveJson("/api/market/overview?" + new URLSearchParams({symbol}));
        const market = data.watch.find(item => item.symbol === symbol);
        if (!market) return;
        const change = item => item.price > 0 ? (item.change_pct >= 0 ? "+" : "") + fmt(item.change_pct, 2) + "%" : "—";
        const set = (name, value) => { strip.querySelector("[data-live-" + name + "]").textContent = value; };
        set("quote", market.price > 0 ? fmt(market.price) : "—");
        set("change", change(market));
        strip.querySelector("[data-live-change]").className = market.change_pct >= 0 ? "positive" : "negative";
        set("range", market.high > 0 ? fmt(market.high) + " / " + fmt(market.low) : "—");
        set("volume", market.price > 0 ? fmt(market.quote_volume, 0) : "—");
        if (futures) set("funding", data.leverage?.funding_rate == null ? "—" : (data.leverage.funding_rate * 100).toFixed(4) + "%");
        renderLongShort(data.leverage);
        renderFlow(data.flow);
        renderLeverage(data.leverage);
        list.replaceChildren(...data.watch.map(item => {
          const button = document.createElement("button");
          button.type = "button";
          button.classList.toggle("active", item.symbol === symbol);
          button.setAttribute("aria-pressed", String(item.symbol === symbol));
          for (const [value, style] of [[item.base, ""], [item.price > 0 ? fmt(item.price) : "—", ""],
            [change(item), item.change_pct >= 0 ? "positive" : "negative"]]) {
            const node = document.createElement("span");
            node.textContent = value; node.className = style; button.append(node);
          }
          button.addEventListener("click", () => window.BTCSymbols?.go(item.symbol));
          return button;
        }));
      } catch (_) { watch.querySelector("p").textContent = "公开行情暂不可用"; }
    }
    void refresh();
    void refreshDepth();
    setInterval(refresh, 20000);
    setInterval(refreshDepth, 2500);
  }

  function mountLiveRisk(section, exchange, futures, canTrade, refreshAccount) {
    const details = document.createElement("details");
    details.className = "panel live-risk";
    details.innerHTML = `
      <summary><strong>实盘 AUTO 风控</strong><span>Binance · 本机</span></summary>
      <form class="live-order-form">
        <label>单笔名义上限（USDT）<input name="max_order" type="number" min="0.01" step="0.01" required></label>
        <label>单币敞口上限（USDT）<input name="max_symbol" type="number" min="0.01" step="0.01" required></label>
        <label>合约初始保证金上限（USDT）<input name="max_margin" type="number" min="0.01" step="0.01" required></label>
        <label>当日亏损上限（USDT）<input name="max_daily_loss" type="number" min="0.01" step="0.01" required></label>
        <label>连续亏损次数<input name="max_losses" type="number" min="1" max="30" required></label>
        <button class="primary" type="submit">保存风控</button>
      </form>
      <p class="hint">以香港时间划分交易日。合约统计 Binance 损益、手续费及资金费，现货统计本机 AUTO 已完成交易。触及上限时暂停新增敞口。</p>
      <p data-risk-feedback class="hint" role="status"></p>`;
    section.querySelector(".live-center").append(details);
    const form = details.querySelector("form");
    const feedback = details.querySelector("[data-risk-feedback]");
    const show = data => { for (const input of form.querySelectorAll("input")) input.value = data[input.name] ?? ""; };
    liveJson("/api/live/risk").then(show).catch(error => { feedback.textContent = error.message; });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      try {
        show(await liveJson("/api/live/risk", Object.fromEntries(new FormData(form))));
        feedback.textContent = "风控已保存。";
      } catch (error) { feedback.textContent = error.message; }
    });
    if (!futures) return;
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.liveFlatten = "";
    button.className = "live-clear";
    button.textContent = "市价全平 · Binance";
    button.disabled = !canTrade() || exchange.value !== "binance";
    section.querySelector(".live-positions").prepend(button);
    button.addEventListener("click", async () => {
      if (!canTrade() || exchange.value !== "binance") return;
      button.disabled = exchange.disabled = true;
      try {
        const preview = await liveJson("/api/live/flatten/preview", {});
        const orders = preview.positions.map(row => row.symbol + " " + row.position + " " + row.quantity);
        if (!await confirmDialog("将市价平掉当前 Binance USDT 合约账户的以下仓位。\n" + orders.join("\n"), {
          title:"确认真实账户全平", confirmText:"确认市价全平", danger:true, focusCancel:true,
        })) return;
        const result = await liveJson("/api/live/flatten", {confirmation:preview.confirmation});
        const message = result.message || "已确认全部平仓。";
        await noticeDialog(message + "\n已提交 " + result.orders.length + " 笔，未提交 " + result.remaining + " 笔。", {title:"全平结果"});
        refreshAccount();
      } catch (error) { await noticeDialog(exchangeError(error), {title:"全平未完成"}); }
      finally { exchange.disabled = false; button.disabled = !canTrade() || exchange.value !== "binance"; }
    });
  }

  function mountHyperliquid(section, coin) {
    const title = $("liveTitle");
    title.textContent = "Hyperliquid · " + coin + " 永续";
    const subtitle = document.querySelector(".topbar .brand p");
    if (subtitle) subtitle.textContent = "ONCHAIN PERPETUALS";
    section.classList.add("dex-workspace");
    section.querySelector(".live-tag").textContent = "链上只读";
    const marketPanel = section.querySelector(".live-chart");
    const walletPanel = section.querySelector(".live-account");
    const bookPanel = section.querySelector(".live-orders");
    marketPanel.innerHTML = `
      <div class="dex-head">
        <h3>永续行情 <small>Hyperliquid</small></h3>
        <label>网络<select data-network><option value="mainnet">主网</option><option value="testnet" selected>测试网</option></select></label>
        <label>周期<select data-interval><option value="1h">1H</option><option value="4h">4H</option></select></label>
      </div>
      <dl class="dex-metrics">
        <div><dt>标记价 · USD</dt><dd data-market="mark">—</dd></div>
        <div><dt>资金费率</dt><dd data-market="funding">—</dd></div>
        <div><dt>未平仓量 · <span data-coin></span></dt><dd data-market="open_interest">—</dd></div>
        <div><dt>24H 成交额 · USD</dt><dd data-market="quote_volume">—</dd></div>
      </dl>
      <div class="dex-chart" aria-label="Hyperliquid 永续 K 线"></div>
      <p class="dex-market-status hint" role="status"></p>`;
    walletPanel.innerHTML = `
      <div class="live-account-head"><h3>钱包查询</h3><span class="mini-badge neutral">公开地址</span></div>
      <form class="live-connection">
        <label data-wallet-picker hidden>选择浏览器钱包<select data-wallet-provider><option value="">请选择</option></select></label>
        <div class="dex-wallet-actions">
          <button class="secondary" type="button" data-wallet-connect>连接钱包</button>
          <button class="secondary" type="button" data-wallet-disconnect hidden>断开钱包</button>
        </div>
        <p class="hint" data-connection-status role="status"></p>
        <label>公开钱包地址<input name="address" autocomplete="off" spellcheck="false" placeholder="0x…" required pattern="0x[0-9a-fA-F]{40}"></label>
        <p class="hint">可用浏览器钱包授权读取地址，或手动填写。点击查询才会将地址发送至 Hyperliquid。</p>
        <button class="secondary" type="submit" data-wallet-query>查询持仓与委托</button>
      </form>
      <p class="hint" data-wallet-status role="status"></p>
      <div id="liveBalances"></div>
      <div class="dex-funding-hint" data-wallet-funding hidden>
        <strong>这是 Hyperliquid 测试网账户</strong>
        <p>钱包余额不等于合约权益。这里显示 0，说明测试网账户还没有保证金。先打开 Hyperliquid 测试网领测试 USDC，再回来查询。</p>
        <a href="https://app.hyperliquid-testnet.xyz/" target="_blank" rel="noopener">打开 Hyperliquid 测试网</a>
      </div>`;
    bookPanel.innerHTML = `
      <div class="live-account-head"><h3>链上持仓与委托</h3><span class="mini-badge neutral" data-book-network>测试网</span></div>
      <p class="hint">持仓与委托来自当前网络的 Hyperliquid 账户。</p>
      <div class="dex-books">
        <div><h3>持仓</h3><div data-positions>查询钱包后显示</div></div>
        <div><h3>委托</h3><div data-orders>查询钱包后显示</div></div>
      </div>`;
    marketPanel.querySelector("[data-coin]").textContent = coin;
    const network = marketPanel.querySelector("[data-network]");
    const interval = marketPanel.querySelector("[data-interval]");
    const chartNode = marketPanel.querySelector(".dex-chart");
    const marketStatus = marketPanel.querySelector(".dex-market-status");
    const walletStatus = walletPanel.querySelector("[data-wallet-status]");
    const fundingHint = walletPanel.querySelector("[data-wallet-funding]");
    const form = walletPanel.querySelector("form");
    const walletButton = form.querySelector("[data-wallet-query]");
    const address = form.elements.address;
    const positions = bookPanel.querySelector("[data-positions]");
    const orders = bookPanel.querySelector("[data-orders]");
    const metricNodes = [...marketPanel.querySelectorAll("[data-market]")];
    const hkt = new Intl.DateTimeFormat("zh-HK", {timeZone:"Asia/Hong_Kong", month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit", hour12:false});
    const numbers = new Intl.NumberFormat("en-US", {maximumFractionDigits: 8});
    const display = value => value == null || value === "" || !Number.isFinite(Number(value)) ? "—" : numbers.format(Number(value));
    const percent = value => display(value == null ? null : Number(value) * 100);
    const lib = window.LightweightCharts;
    const chart = lib?.createChart(chartNode, {
      autoSize: true, localization:{locale:"zh-HK", timeFormatter: stamp => hkt.format(stamp * 1000)},
      timeScale:{timeVisible:true, secondsVisible:false, tickMarkFormatter: stamp => hkt.format(stamp * 1000)},
    });
    const candles = chart?.addCandlestickSeries({
      upColor:"#16c784", downColor:"#ea3943", borderVisible:false, wickUpColor:"#16c784", wickDownColor:"#ea3943",
    });
    function chartTheme() {
      if (!chart) return;
      const css = getComputedStyle(root);
      const color = name => css.getPropertyValue(name).trim();
      chart.applyOptions({
        layout:{background:{color:color("--panel")}, textColor:color("--muted")},
        grid:{vertLines:{color:color("--line-soft")}, horzLines:{color:color("--line-soft")}},
      });
    }
    chartTheme();
    window.addEventListener("btcquant:ui", chartTheme);
    let marketRequest = 0, walletRequest = 0;

    async function refreshMarket(reset = false) {
      const request = ++marketRequest;
      if (reset) {
        candles?.setData([]);
        metricNodes.forEach(node => { node.textContent = "—"; });
        marketStatus.textContent = "正在读取行情…";
      }
      const query = new URLSearchParams({network:network.value, interval:interval.value, coin});
      try {
        const snapshot = await liveJson("/api/live/dex/hyperliquid/market?" + query);
        if (request !== marketRequest) return;
        metricNodes.forEach(node => {
          const key = node.dataset.market;
          const text = key === "funding" ? percent(snapshot[key]) : display(snapshot[key]);
          node.textContent = key === "funding" && text !== "—" ? text + "%" : text;
        });
        const data = snapshot.candles;
        if (data.length && candles) {
          const precision = Math.min(8, Math.max(2, String(snapshot.mark ?? data.at(-1).close).split(".")[1]?.length || 0));
          candles.applyOptions({priceFormat:{type:"price", precision, minMove:10 ** -precision}});
          candles.setData(data);
          if (reset) chart.timeScale().fitContent();
        } else candles?.setData([]);
        const timestamp = hkt.format(new Date(snapshot.updated_at));
        marketStatus.textContent = !chart ? "图表组件不可用 · "
          + timestamp + " HKT" : !data.length ? "此周期暂时没有 K 线 · " + timestamp + " HKT"
            : "行情更新 " + timestamp + " HKT";
      } catch (error) {
        if (request === marketRequest) marketStatus.textContent = error.message + (reset ? "" : " · 图表保留上次成功的快照");
      }
    }

    function entry(name, value) {
      const line = document.createElement("div");
      const label = document.createElement("span");
      const amount = document.createElement("b");
      label.textContent = name;
      amount.textContent = value;
      line.append(label, amount);
      return line;
    }
    function bookRows(node, rows, empty) {
      node.replaceChildren(...rows.map(cells => {
        const line = document.createElement("div");
        line.className = "dex-book-row";
        cells.forEach((cell, index) => {
          const value = document.createElement(index === 0 ? "strong" : "span");
          value.textContent = cell;
          line.append(value);
        });
        return line;
      }));
      if (!rows.length) node.textContent = empty;
    }
    function clearWallet() {
      walletRequest += 1;
      walletButton.disabled = false;
      walletStatus.textContent = "";
      $("liveBalances").replaceChildren();
      fundingHint.hidden = true;
      positions.textContent = orders.textContent = "查询钱包后显示";
    }
    const browserWallet = bindBrowserWallet(form, address);
    const trading = mountDexTrading(section, walletPanel, network, coin, browserWallet, () => form.requestSubmit());
    form.addEventListener("submit", async event => {
      event.preventDefault();
      clearWallet();
      const request = walletRequest;
      walletButton.disabled = true;
      walletStatus.textContent = "正在查询…";
      try {
        const snapshot = await liveJson("/api/live/dex/hyperliquid/wallet", {network:network.value, address:address.value});
        if (request !== walletRequest) return;
        $("liveBalances").replaceChildren(
          entry("合约权益 · USD", display(snapshot.equity)),
          entry("已用保证金 · USD", display(snapshot.margin_used)),
          entry("可提取 · USD", display(snapshot.withdrawable)),
        );
        bookRows(positions, snapshot.positions.map(p => [
          p.coin + " · " + (Number(p.szi) > 0 ? "多仓" : "空仓"),
          "数量 " + display(Math.abs(Number(p.szi))),
          "开仓价 " + display(p.entryPx),
          "强平价 " + display(p.liquidationPx),
          "未实现盈亏 " + display(p.unrealizedPnl) + " USD",
        ]), "当前地址暂无合约持仓");
        bookRows(orders, snapshot.orders.map(o => [
          o.coin + " · " + (o.side === "B" ? "买入" : "卖出"),
          "数量 " + display(o.sz),
          (o.isTrigger ? "触发价 " + display(o.triggerPx) : "限价 " + display(o.limitPx)),
          o.reduceOnly ? "只减仓" : "订单 " + (o.oid ?? "—"),
        ]), "当前地址暂无合约委托");
        const noEquity = new URLSearchParams(location.search).get("venue") === "hyperliquid"
          && Number(snapshot.equity || 0) <= 0;
        fundingHint.hidden = !(network.value === "testnet" && noEquity);
        walletStatus.textContent = "查询于 " + hkt.format(new Date(snapshot.updated_at)) + " HKT";
      } catch (error) {
        if (request === walletRequest) walletStatus.textContent = error.message;
      } finally {
        if (request === walletRequest) walletButton.disabled = false;
      }
    });
    address.addEventListener("input", () => { clearWallet(); trading.refresh(); });
    network.addEventListener("change", () => {
      bookPanel.querySelector("[data-book-network]").textContent = network.selectedOptions[0].textContent;
      clearWallet();
      trading.refresh();
      void refreshMarket(true);
    });
    interval.addEventListener("change", () => refreshMarket(true));
    void refreshMarket(true);
    setInterval(() => { if (!document.hidden) void refreshMarket(); }, 30000);
  }

  function bindBrowserWallet(form, address) {
    const picker = form.querySelector("[data-wallet-picker]");
    const choices = picker.querySelector("select");
    const connect = form.querySelector("[data-wallet-connect]");
    const disconnect = form.querySelector("[data-wallet-disconnect]");
    const status = form.querySelector("[data-connection-status]");
    const wallets = new Map();
    const isProvider = provider => typeof provider?.request === "function"
      && typeof provider.on === "function" && typeof provider.removeListener === "function";
    let session = null;

    // Discover locally. The caller's explicit click requests wallet access.
    window.addEventListener("eip6963:announceProvider", event => {
      const {info, provider} = event.detail || {};
      if (!isProvider(provider) || typeof info?.uuid !== "string" || !info.uuid
          || typeof info.name !== "string" || !info.name.trim() || wallets.has(info.uuid)) return;
      const name = info.name.trim().slice(0, 80);
      wallets.set(info.uuid, {provider, name});
      choices.add(new Option(name, info.uuid));
    });

    function detach(message) {
      const current = session;
      session = null;
      if (current) {
        current.provider.removeListener("accountsChanged", current.onAccounts);
        current.provider.removeListener("disconnect", current.onDisconnect);
        if (current.address) {
          address.value = "";
          address.readOnly = false;
          address.dispatchEvent(new Event("input"));
        }
      }
      connect.hidden = false;
      disconnect.hidden = true;
      choices.disabled = false;
      choices.value = "";
      status.textContent = message;
    }

    function showAccount(current, accounts) {
      if (session !== current) return;
      const next = Array.isArray(accounts) ? accounts[0] : null;
      if (typeof next !== "string" || !/^0x[0-9a-fA-F]{40}$/.test(next)) {
        detach("钱包未提供可用的公开地址。");
        return;
      }
      const changed = next !== current.address;
      current.address = next;
      address.value = next;
      address.readOnly = true;
      picker.hidden = true;
      disconnect.textContent = "断开钱包";
      status.textContent = "已连接 " + current.name + "。点击查询读取当前网络账户。";
      if (changed) address.dispatchEvent(new Event("input"));
    }

    connect.addEventListener("click", async () => {
      if (session) return;
      window.dispatchEvent(new Event("eip6963:requestProvider"));
      picker.hidden = wallets.size < 2;
      if (!picker.hidden && !choices.value) {
        status.textContent = "请选择要连接的钱包。";
        choices.focus();
        return;
      }
      const wallet = wallets.size > 1 ? wallets.get(choices.value)
        : wallets.values().next().value || {provider:window.ethereum, name:"浏览器钱包"};
      if (!isProvider(wallet?.provider)) {
        status.textContent = "未检测到兼容的浏览器钱包，可手动填写公开地址。";
        return;
      }
      const current = {...wallet, address:null, accountEvent:false};
      current.onAccounts = accounts => {
        current.accountEvent = true;
        showAccount(current, accounts);
      };
      current.onDisconnect = () => {
        if (session === current) detach("钱包已断开。");
      };
      session = current;
      choices.disabled = true;
      connect.hidden = true;
      disconnect.hidden = false;
      disconnect.textContent = "取消连接";
      status.textContent = "请在钱包中确认公开地址授权。";
      try {
        current.provider.on("accountsChanged", current.onAccounts);
        current.provider.on("disconnect", current.onDisconnect);
        const accounts = await current.provider.request({method:"eth_requestAccounts"});
        if (!current.accountEvent) showAccount(current, accounts);
      } catch (error) {
        if (session !== current) return;
        detach(error?.code === 4001 ? "已取消钱包授权。"
          : error?.code === -32002 ? "请打开钱包扩展，处理等待中的授权。"
            : "钱包连接失败，可重试或手动填写地址。");
      }
    });
    disconnect.addEventListener("click", () => detach("已断开本页钱包连接。"));
    return () => session?.address ? session : null;
  }

  function mountDexTrading(section, walletPanel, network, coin, wallet, refreshWallet) {
    const base = "/api/live/dex/hyperliquid";
    const symbol = coin + "USDT";
    const sidebar = document.createElement("div");
    sidebar.className = "live-trade-sidebar";
    walletPanel.before(sidebar);
    sidebar.append(walletPanel);
    const card = document.createElement("section");
    card.className = "live-trade panel dex-trade";
    card.innerHTML = `
      <div class="live-account-head"><h3>永续下单</h3><span class="mini-badge neutral" data-dex-agent>待授权</span></div>
      <p class="hint" data-dex-mode></p>
      <div class="live-actions">
        <button class="secondary" type="button" data-agent-enable>授权交易代理</button>
        <button class="secondary" type="button" data-agent-disable>停用本机代理</button>
      </div>
      <form class="live-order-form">
        <label>仓位方向<select name="position"><option value="LONG">做多</option><option value="SHORT">做空</option></select></label>
        <label>操作<select name="action"><option value="OPEN">开仓</option><option value="CLOSE">平仓 · 只减仓</option></select></label>
        <label>订单类型<select name="type"><option value="MARKET">市价 · IOC</option><option value="LIMIT">限价 · GTC</option></select></label>
        <label>数量（${coin}）<input name="quantity" type="number" min="0.000001" step="any" required></label>
        <label data-dex-price hidden>限价（USD）<input name="price" type="number" min="0.000001" step="any"></label>
        <button class="primary" type="submit">预览订单</button>
      </form>
      <p class="hint" data-dex-result role="status"></p>`;
    sidebar.append(card);
    const autoCard = document.createElement("section");
    autoCard.className = "live-orders panel dex-auto";
    autoCard.innerHTML = `
      <div class="live-account-head"><h3>合约量化 · 多空双向</h3><span class="mini-badge neutral" data-dex-auto-state>未启动</span></div>
      <p class="hint">按已收盘 K 线评估本地规则模型。开仓附带交易所止损。检测到订单或保护状态不一致时暂停。</p>
      <form class="live-order-form">
        <label>执行模型<select name="model_id"><option value="trend-v2">默认合约趋势模型</option></select></label>
        <label>信号周期<select name="interval"><option value="1h">1H</option><option value="4h">4H</option></select></label>
        <label>单次目标名义价值（USD）<input name="amount" type="number" min="11" step="0.01" required></label>
        <button class="primary" type="submit">预览并启动 AUTO</button>
      </form>
      <button class="secondary" type="button" data-dex-pause>暂停 AUTO</button>
      <p class="hint" data-auto-note role="status"></p>`;
    section.querySelector(".live-orders").before(autoCard);
    const ledger = document.createElement("div");
    ledger.className = "live-order-list";
    const ledgerTitle = document.createElement("h3");
    ledgerTitle.textContent = "本机送单记录";
    section.querySelector("[data-orders]").closest(".live-orders").append(ledgerTitle, ledger);
    const form = card.querySelector("form");
    const auto = autoCard.querySelector("form");
    const enable = card.querySelector("[data-agent-enable]");
    const disable = card.querySelector("[data-agent-disable]");
    const pause = autoCard.querySelector("[data-dex-pause]");
    const message = card.querySelector("[data-dex-result]");
    const autoNote = autoCard.querySelector("[data-auto-note]");
    let agent = null, plan = null, busy = false, generation = 0;
    const testnet = () => network.value === "testnet";
    const ownsAgent = () => Boolean(agent?.agent && wallet()?.address.toLowerCase() === agent.address);
    function render() {
      const running = plan?.status === "running";
      const active = testnet() && ownsAgent();
      section.querySelector(".live-summary .live-tag").textContent = testnet() ? "测试网 · 交易" : "主网 · 只读";
      card.querySelector("[data-dex-agent]").textContent = active ? "代理已授权" : "待授权";
      card.querySelector("[data-dex-mode]").textContent = !testnet() ? "主网提供行情及持仓查询。切到测试网可试单和运行 AUTO。"
        : !wallet() ? "请先连接浏览器钱包。硬件钱包需在浏览器扩展中添加。"
          : "测试网使用模拟资产。钱包授权后，本机代理可签发订单；代理密钥仅在本次程序运行期间保留。";
      enable.disabled = busy || !testnet() || !wallet() || Boolean(agent?.agent);
      disable.disabled = busy || !agent?.agent;
      for (const item of form.elements) item.disabled = busy || !active || running && plan.symbol === symbol;
      for (const item of auto.elements) item.disabled = busy || !active || running;
      pause.disabled = busy || !running;
      form.querySelector("[data-dex-price]").hidden = form.elements.type.value !== "LIMIT";
      form.elements.price.required = form.elements.type.value === "LIMIT";
      autoCard.querySelector("[data-dex-auto-state]").textContent = running ? "运行中" : plan ? "已暂停" : "未启动";
      autoNote.textContent = plan
        ? plan.symbol.slice(0,-4) + " · " + plan.interval.toUpperCase() + " · " + plan.message
        : "逐仓 1–10×。先在 Hyperliquid 确认该交易对没有持仓和挂单。";
    }
    async function refresh() {
      const run = ++generation;
      render();
      try {
        const [current, result] = await Promise.all([liveJson(base + "/agent"), liveJson(base + "/auto")]);
        if (run !== generation || busy) return;
        agent = current;
        plan = result.plan;
        render();
        if (!testnet() || !ownsAgent()) { ledger.textContent = "授权测试网代理后显示。"; return; }
        const history = await liveJson(base + "/orders?" + new URLSearchParams({symbol}));
        if (run !== generation || busy) return;
        ledger.replaceChildren(...history.orders.map(order => {
          const line = document.createElement("div");
          line.className = "live-order-row";
          for (const value of [order.source === "auto" ? "AUTO" : "手动", order.action + " " + order.position,
            order.status, "成交 " + (order.filled || "—"), order.order_id || order.id]) {
            const cell = document.createElement("span"); cell.textContent = value; line.append(cell);
          }
          const buttons = document.createElement("div");
          for (const action of ["query", ...(["NEW","PARTIALLY_FILLED"].includes(order.status) ? ["cancel"] : [])]) {
            const button = document.createElement("button");
            button.type = "button"; button.className = "secondary"; button.textContent = action === "query" ? "查单" : "撤单";
            button.addEventListener("click", () => runAction(async () => {
              if (action === "cancel" && !await confirmDialog("撤销这笔 Hyperliquid 测试网委托？", {title:"测试网撤单"})) return;
              const result = await liveJson("/api/live/order/" + action, {id:order.id});
              message.textContent = result.message || result.status;
              refreshWallet();
            }));
            buttons.append(button);
          }
          line.append(buttons);
          return line;
        }));
        if (!ledger.children.length) ledger.textContent = "当前合约暂无本机送单记录。";
      } catch (error) {
        if (run === generation && !busy) message.textContent = error.message;
      }
    }
    async function runAction(action) {
      if (busy) return;
      busy = true; generation += 1; message.textContent = ""; render();
      try { await action(); }
      catch (error) {
        message.textContent = error?.code === 4001 ? "已取消钱包签名。"
          : error?.code === 4200 || error?.code === -32601 ? "钱包不支持此 EIP-712 签名。请检查硬件钱包与扩展的兼容性。"
            : error?.message || "操作未完成，请查看钱包。";
      } finally { busy = false; void refresh(); }
    }
    enable.addEventListener("click", () => runAction(async () => {
      const current = wallet();
      if (!current || !testnet()) return;
      const account = current.address;
      const chain = await current.provider.request({method:"eth_chainId"});
      const grant = await liveJson(base + "/agent/prepare", {address:account, chain_id:Number(chain)});
      if (!await confirmDialog("在设备上核对 Hyperliquid Testnet 和代理地址。\n" + grant.typed_data.message.agentAddress, {
        title:"授权测试网代理", confirmText:"前往钱包签名", focusCancel:true,
      })) return;
      if (current !== wallet() || current.address !== account || !testnet()) throw Error("账户或网络已变化，请重新授权。");
      const signature = await current.provider.request({method:"eth_signTypedData_v4", params:[account, JSON.stringify(grant.typed_data)]});
      if (current !== wallet() || current.address !== account || !testnet()) throw Error("账户或网络已变化，请重新授权。");
      agent = await liveJson(base + "/agent/enable", {token:grant.token, signature});
      message.textContent = "测试网代理已授权。";
    }));
    disable.addEventListener("click", () => runAction(async () => {
      if (!await confirmDialog("停用本机代理并暂停 AUTO。测试网仓位和挂单保留。要撤销链上授权，请到 Hyperliquid 管理 API 钱包。", {title:"停用代理"})) return;
      agent = await liveJson(base + "/agent/disable", {});
      message.textContent = "已停用本机代理。";
    }));
    form.elements.type.addEventListener("change", render);
    form.addEventListener("submit", event => {
      event.preventDefault();
      const input = Object.fromEntries(new FormData(form));
      if (input.type !== "LIMIT") delete input.price;
      void runAction(async () => {
        if (!testnet() || !ownsAgent()) return;
        const account = wallet().address;
        const preview = await liveJson(base + "/preview", {...input, network:"testnet", address:account, symbol});
        const order = preview.order;
        if (order.symbol !== symbol) throw Error("订单交易对与页面不一致，请重新预览。");
        const action = order.action === "OPEN" ? "开" : "平";
        const direction = order.position === "LONG" ? "多" : "空";
        if (!await confirmDialog(action + direction + " " + order.quantity + " " + coin + "\n"
          + (order.execution_price ? "IOC 保护价 " + order.execution_price : "限价 " + order.price), {
          title:"确认 Hyperliquid 测试网订单", confirmText:"提交测试网订单", focusCancel:true,
        })) return;
        if (!testnet() || wallet()?.address !== account) throw Error("钱包账户或网络已变化。");
        const result = await liveJson("/api/live/order", {confirmation:preview.confirmation});
        message.textContent = result.message || result.status;
        refreshWallet();
      });
    });
    auto.addEventListener("submit", event => {
      event.preventDefault();
      const options = Object.fromEntries(new FormData(auto));
      void runAction(async () => {
        if (!testnet() || !ownsAgent()) return;
        const account = wallet().address;
        const review = await liveJson(base + "/auto/preview", {...options, address:account, symbol});
        if (!await confirmDialog(coin + " · " + options.interval.toUpperCase() + " · " + options.amount + " USD\n逐仓 "
          + review.leverage + "× · 风险预算 " + (review.risk_fraction * 100).toFixed(2) + "% · 止损 " + review.atr_stop
          + " ATR\n启动后由本机代理发送测试网订单。停用或退出程序会停止策略。", {
          title:"确认测试网 AUTO", confirmText:"启动 AUTO", focusCancel:true,
        })) return;
        if (!testnet() || wallet()?.address !== account) throw Error("钱包账户或网络已变化。");
        ({plan} = await liveJson(base + "/auto/start", {confirmation:review.confirmation}));
      });
    });
    pause.addEventListener("click", () => runAction(async () => {
      ({plan} = await liveJson(base + "/auto/pause", {}));
    }));
    liveJson("/api/models?scope=futures").then(({models}) => {
      const select = auto.elements.model_id;
      const options = models.filter(model => model.kind === "rules" && model.ai_mode === "off");
      if (options.length) select.replaceChildren(...options.map(model => new Option(model.name, model.id)));
    }).catch(() => {});
    void refresh();
    setInterval(() => { if (!document.hidden) void refresh(); }, 5000);
    return {refresh};
  }

  function mountLiveTrade(section, exchange, symbol, futures, refreshAccount) {
    const card = document.createElement("section");
    card.className = "live-trade panel";
    card.innerHTML = `
      <div class="live-account-head"><h3>手动下单</h3><span class="live-tag">主网</span></div>
      <form class="live-order-form">
        <div class="live-direction" aria-label="${futures?"仓位方向":"买卖方向"}">
          <button type="button" data-direction="${futures?"LONG":"BUY"}">${futures?"做多 LONG":"买入 " + symbol.slice(0,-4)}</button>
          <button type="button" data-direction="${futures?"SHORT":"SELL"}">${futures?"做空 SHORT":"卖出 " + symbol.slice(0,-4)}</button>
        </div>
        <label hidden>方向<select name="side">${futures
          ? '<option value="LONG">多仓</option><option value="SHORT">空仓</option>'
          : '<option value="BUY">买入</option><option value="SELL">卖出</option>'}</select></label>
        ${futures?'<label>操作<select name="action"><option value="OPEN">开仓</option><option value="CLOSE">平仓</option></select></label>':''}
        <label>订单类型<select name="kind"><option value="MARKET">市价</option><option value="LIMIT">限价</option></select></label>
        <label class="live-quantity"><span>数量</span><input name="amount" type="number" min="0.000000000001" step="any" inputmode="decimal" required></label>
        <label class="live-order-price" hidden>限价（USDT）<input name="price" type="number" min="0.000000000001" step="any" inputmode="decimal"></label>
        <label class="live-percent" hidden><span>卖出比例 <output>0%</output></span><input name="percent" type="range" min="0" max="100" step="1" value="0"></label>
        <div class="live-available" data-live-available>可用余额 —</div>
        <div class="live-quick" aria-label="快捷填写"></div>
        <p class="live-rules" data-live-rules></p>
        <p class="live-estimate" data-live-estimate role="status"></p>
        ${futures?'<label class="live-position" hidden>平仓仓位<select name="position"></select></label><label class="live-leverage" hidden>杠杆<input name="leverage" type="range" min="1" max="1" value="1"></label><label class="live-margin" hidden>保证金模式<select name="margin"><option value="isolated">逐仓</option><option value="cross">全仓</option></select></label><p class="live-limit-note" data-live-leverage></p>':''}
        <button class="primary" type="submit" disabled>预览订单</button>
      </form>
      <p class="hint live-trade-status" role="status">请先配置该市场的 API 连接。</p>
      <p class="hint" data-order-feedback role="status" hidden></p>
      <div class="live-receipt" hidden>
        <p class="hint negative" role="status"></p>
        <button type="button" class="secondary" data-recover-order>核对提交回执</button>
      </div>
      <button type="button" class="live-risk-link" data-open-risk>手动上限设置</button>`;
    const sidebar = document.createElement("div");
    sidebar.className = "live-trade-sidebar";
    const accountCard = section.querySelector(".live-account");
    accountCard.before(sidebar);
    sidebar.append(card, accountCard);
    const form = card.querySelector("form");
    const fields = form.elements;
    const previewBtn = form.querySelector('button[type="submit"]');
    const status = card.querySelector(".live-trade-status");
    const orderFeedback = card.querySelector("[data-order-feedback]");
    card.querySelector("[data-open-risk]")?.addEventListener("click", () => {
      const panel = section.querySelector(".live-risk-settings");
      if (!panel) return;
      panel.open = true;
      panel.scrollIntoView({behavior:"smooth", block:"center"});
    });
    const book = section.querySelector(".live-orders");
    const orders = document.createElement("div");
    orders.className = "live-order-list";
    book.append(orders);
    const description = $("liveExecutionStatus");
    description.textContent = "显示从本终端提交的订单。成交状态以交易所回报为准。";
    let configured = false, busy = false, positions = [], balances = [], rules = null, maxSell = 0, generation = 0, caps = null;
    let accountKnown = false, manualRisk = null, ruleRequest = 0, cachedOrders = [], orderFilter = "all";
    const ticket = window.BTCLiveTicket;
    const quick = card.querySelector(".live-quick");
    const tabs = document.createElement("div");
    tabs.className = "live-order-tabs";
    tabs.setAttribute("role", "group");
    tabs.setAttribute("aria-label", "筛选本机订单");
    for (const [value, label] of [["all","全部"],["open","当前委托"],["history","历史订单"],["attention","待核对"]]) {
      const tab = document.createElement("button");
      tab.type = "button"; tab.dataset.filter = value; tab.textContent = label;
      tab.onclick = () => { orderFilter = value; renderOrders(); };
      tabs.append(tab);
    }
    orders.before(tabs);
    const finalStates = ["FILLED", "CANCELED", "REJECTED", "EXPIRED", "ACKNOWLEDGED"];
    const uncertainStates = ["UNKNOWN", "SUBMITTING", "CANCEL_PENDING", "CANCEL_UNKNOWN"];
    const groupFor = order => uncertainStates.includes(order.status) ? "attention" : finalStates.includes(order.status) ? "history" : "open";
    liveJson("/api/live/manual-risk").then(value => { manualRisk = value; updateFields(); }).catch(() => {});
    window.addEventListener("btcquant:risk", event => { manualRisk = event.detail; updateFields(); });
    const coin = symbol.slice(0, -4);
    const market = futures ? "futures" : "spot";
    const receipt = card.querySelector(".live-receipt");
    const recoverButton = receipt.querySelector("button");
    const receiptPrefix = "btcquant.live-receipt.v1:";
    const confirmationPattern = /^bq[0-9a-f]{32}$/;
    const receiptScope = () => receiptPrefix + exchange.value + ":" + market + ":" + symbol + ":";
    let inMemoryReceipt = null;
    function pendingReceipt() {
      const scope = receiptScope();
      if (inMemoryReceipt?.startsWith(scope)) return inMemoryReceipt;
      try {
        for (let i = 0; i < localStorage.length; i++) {
          const key = localStorage.key(i);
          if (key?.startsWith(scope) && confirmationPattern.test(key.slice(scope.length))) return key;
        }
      } catch (_) { /* storage is verified before sending */ }
      return null;
    }
    function rememberReceipt(confirmation) {
      const key = receiptScope() + confirmation;
      try { localStorage.setItem(key, "pending"); }
      catch (_) { throw Error("浏览器无法保存回执核对记录，已停止提交。"); }
      inMemoryReceipt = key;
      updateFields();
      return key;
    }
    function clearReceipt(key) {
      localStorage.removeItem(key);
      if (inMemoryReceipt === key) inMemoryReceipt = null;
    }
    window.addEventListener("storage", event => {
      if (event.key === inMemoryReceipt && event.newValue == null) inMemoryReceipt = null;
      if (event.key == null || event.key.startsWith(receiptPrefix)) updateFields();
    });
    const statuses = {
      ACCEPTED:"已受理", NEW:"挂单中", PARTIALLY_FILLED:"部分成交", FILLED:"已成交", CANCELED:"已撤销",
      EXPIRED:"已过期", REJECTED:"已拒绝", UNKNOWN:"待核对", SUBMITTING:"待核对",
      CANCEL_PENDING:"撤单待确认", CANCEL_UNKNOWN:"撤单待核对", ACKNOWLEDGED:"已人工核对",
    };

    const coinwFutures = () => futures && exchange.value === "coinw";
    const closing = () => futures && fields.action.value === "CLOSE";
    const quoteMarket = () => !futures && fields.kind.value === "MARKET" && fields.side.value === "BUY";

    const decimals = ticket.precision;
    const floorQty = ticket.floor;
    const baseFree = () => {
      const row = balances.find(item => item.asset === coin);
      return Number(row?.free ?? 0) || 0;
    };
    async function loadRules() {
      if (futures) return;
      const current = ++ruleRequest;
      const venue = exchange.value;
      try {
        const query = new URLSearchParams({exchange: venue, market, symbol});
        const result = await liveJson("/api/live/rules?" + query);
        if (current !== ruleRequest || venue !== exchange.value) return;
        rules = result;
      } catch (_) { if (current !== ruleRequest) return; rules = null; }
      updateFields();
    }
    function updateSell() {
      const label = card.querySelector(".live-percent");
      if (!label) return;
      const isSell = !futures && fields.side.value === "SELL";
      label.hidden = !isSell;
      if (!isSell) { maxSell = 0; return; }
      const applicable = ticket.rulesFor(rules, fields.kind.value);
      const step = applicable?.step;
      maxSell = Math.max(0, floorQty(baseFree(), step));
      if (maxSell < Number(applicable?.min_qty || 0)) maxSell = 0;
      const output = label.querySelector("output");
      const slider = fields.percent;
      slider.disabled = busy || !configured || !rules || maxSell <= 0;
      if (maxSell <= 0) {
        slider.value = "0";
        output.textContent = "0%";
        label.title = "没有可卖出的可用余额";
        return;
      }
      const pct = Math.max(0, Math.min(100, Number(slider.value) || 0));
      slider.value = String(pct);
      output.textContent = pct + "%";
      label.title = "100% = 可卖 " + maxSell.toFixed(Math.min(12, decimals(step))) + " " + coin;
    }
    function updateEstimate() {
      const box = card.querySelector("[data-live-estimate]");
      const available = card.querySelector("[data-live-available]");
      const info = card.querySelector("[data-live-rules]");
      const applicable = ticket.rulesFor(rules, fields.kind.value);
      const quoteFree = Number(balances.find(item => item.asset === "USDT")?.free || 0);
      available.textContent = futures ? "合约账户独立核验 · USDT 保证金"
        : !accountKnown ? "可用余额 — · 等待账户读取"
          : "可用 " + (fields.side.value === "BUY" ? quoteFree.toFixed(4) + " USDT" : baseFree() + " " + coin);
      info.textContent = futures ? ""
        : (applicable ? "最小订单 " + applicable.min_notional + " USDT · 数量步长 " + applicable.step + " " + coin : "交易规则读取中或暂不可用")
          + (manualRisk ? " · 单笔上限 " + manualRisk.max_spot_notional + " USDT" : "");
      quick.replaceChildren();
      if (!futures) {
        const sell = fields.side.value === "SELL";
        for (const value of sell ? [25,50,75,100] : quoteMarket() ? [5,10] : []) {
          const button = document.createElement("button");
          button.type = "button"; button.textContent = value + (sell ? "%" : " USDT");
          button.disabled = busy || !configured || !rules || (sell && maxSell <= 0);
          button.onclick = () => {
            if (sell) { fields.percent.value = String(value); fields.percent.dispatchEvent(new Event("input")); }
            else { fields.amount.value = String(value); updateFields(); }
          };
          quick.append(button);
        }
      }
      if (futures) { box.textContent = ""; return; }
      box.classList.remove("negative");
      if (!fields.amount.value) {
        box.textContent = fields.side.value === "SELL" && accountKnown && rules
          ? "按步长最多可卖 " + ticket.quantity(maxSell, applicable.step) + " " + coin + "。100% 仍受最小订单和单笔上限限制。"
          : "输入" + (quoteMarket() ? "预算" : "数量") + "后查看估算；预览不会提交订单。";
        previewBtn.disabled = true;
        return;
      }
      const price = fields.kind.value === "LIMIT" ? fields.price.value : rules?.price;
      const result = ticket.spot({amount:fields.amount.value, price, kind:fields.kind.value, side:fields.side.value,
        rules, baseFree:accountKnown ? baseFree() : null, quoteFree:accountKnown ? quoteFree : null,
        cap:manualRisk?.max_spot_notional});
      if (result.error) {
        box.textContent = result.error;
        box.classList.add("negative");
        previewBtn.disabled = true;
      } else {
        box.textContent = "预计 " + ticket.quantity(result.qty, applicable.step) + " " + coin + " ≈ " + result.notional.toFixed(2)
          + " USDT" + (result.remainder >= .01 ? " · 预算余量 " + result.remainder.toFixed(2) + " USDT" : "")
          + "。未含手续费；以交易所预览与成交回报为准。";
      }
    }
    function updateFields() {
      const pending = pendingReceipt();
      const uncertain = cachedOrders.some(order => uncertainStates.includes(order.status));
      receipt.hidden = !pending && !uncertain;
      recoverButton.hidden = !pending;
      recoverButton.disabled = busy;
      receipt.querySelector("p").textContent = pending
        ? "上一笔提交回执未确认。请先核对回执，再下新单。"
        : "有委托状态待核对。请在下方「待核对」中查单。";
      for (const field of form.querySelectorAll("input, select")) field.disabled = busy;
      for (const button of form.querySelectorAll("[data-direction]")) {
        button.setAttribute("aria-pressed", String(button.dataset.direction === fields.side.value));
        button.disabled = busy;
      }
      const limit = fields.kind.value === "LIMIT";
      card.querySelector(".live-order-price").hidden = !limit;
      fields.price.required = limit;
      card.querySelector(".live-quantity span").textContent = quoteMarket() ? "买入金额（USDT）"
        : "数量（" + (coinwFutures() && closing() ? "张" : coin) + "）";
      if (futures) {
        card.querySelector(".live-position").hidden = !(coinwFutures() && closing());
        card.querySelector(".live-leverage").hidden = !(coinwFutures() && !closing());
        card.querySelector(".live-margin").hidden = !(coinwFutures() && !closing());
        const previous = fields.position.value;
        const choices = positions.filter(p => p.instrument === coin && p.side === fields.side.value && p.id);
        fields.position.replaceChildren(...choices.map(p => new Option(p.side + " · " + p.qty + " 张", p.id)));
        if (choices.some(p => p.id === previous)) fields.position.value = previous;
        if (caps && coinwFutures()) {
          fields.leverage.min = String(caps.minimum);
          fields.leverage.max = String(caps.manual_max);
        }
        fields.leverage.disabled = !caps || busy;
        form.querySelector("[data-live-leverage]").textContent = caps
          ? (coinwFutures() ? "已选 " + fields.leverage.value + "×" : "交易所当前 " + caps.current + "×")
            + " · 实盘上限 " + caps.manual_max + "×" + (caps.auto_max ? " · AUTO 上限 " + caps.auto_max + "×" : "")
          : "等待读取交易所杠杆规格";
      }
      previewBtn.textContent = busy ? "处理中…" : "预览" + (futures ? (closing() ? "平仓" : "开仓") : fields.side.value === "BUY" ? "买入" : "卖出") + "订单";
      card.dataset.side = fields.side.value;
      previewBtn.disabled = busy || !!pending || uncertain || !configured || futures && !closing() && (!caps || caps.current > caps.manual_max)
        || (coinwFutures() && closing() && !fields.position.value);
      updateSell();
      updateEstimate();
    }
    function setBusy(value) {
      busy = value;
      if (value) ++generation;
      exchange.disabled = value;
      updateFields();
    }
    function showOrderDetails(order) {
      const fmtTime = value => {
        if (!value) return "—";
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", {hour12: false});
      };
      const lines = [
        (order.exchange === "binance" ? "Binance" : "CoinW") + " · " + order.symbol,
        "方向：" + (order.market === "futures"
          ? (order.action === "CLOSE" ? "平仓" : "开仓") + " / " + (order.position === "LONG" ? "多" : "空")
          : order.side === "BUY" ? "买入" : "卖出"),
        "类型：" + (order.type === "LIMIT" ? "限价 " + order.price : "市价"),
        "状态：" + (statuses[order.status] || order.status),
        "订单号：" + (order.order_id || "—"),
        "本地 ID：" + (order.id || "—"),
        "成交数量：" + (order.filled == null ? "待确认" : order.filled + " " + order.symbol.slice(0, -4)),
        "成交金额：" + (order.quote_filled == null ? "—" : order.quote_filled + " USDT"),
        "成交均价：" + (order.average_price == null ? "—" : order.average_price + " USDT"),
        "创建时间：" + fmtTime(order.created_at),
        "更新时间：" + fmtTime(order.updated_at),
      ];
      if (order.message) lines.push("消息：" + order.message);
      void noticeDialog(lines.join("\n"), {title:"订单详情"});
    }
    function announce(text, error = false) {
      orderFeedback.textContent = text;
      orderFeedback.hidden = !text;
      orderFeedback.classList.toggle("negative", error);
    }
    function showReceipt(order, resetAmount = false) {
      cachedOrders = [order, ...cachedOrders.filter(item => item.id !== order.id)];
      if (uncertainStates.includes(order.status)) orderFilter = "attention";
      renderOrders();
      const details = [statuses[order.status]];
      if (order.filled != null) details.push("已成交 " + order.filled + " " + coin);
      if (order.quote_filled != null) details.push("成交额 " + order.quote_filled + " USDT");
      const remainder = Number(order.quote) - Number(order.quote_filled);
      if (order.quote && order.quote_filled != null && remainder >= .01)
        details.push("预算余量 " + remainder.toFixed(2) + " USDT");
      const unfilled = Number(order.quantity) - Number(order.filled);
      if (order.quantity && order.filled != null && unfilled > 1e-12) {
        details.push("未成交 " + Number(unfilled.toFixed(12)) + " " + coin);
      }
      if (order.message) details.push(exchangeError(order.message));
      announce(details.join(" · "), uncertainStates.includes(order.status) || order.status === "REJECTED");
      if (resetAmount && order.status !== "REJECTED") fields.amount.value = "";
      if (["NEW", "FILLED", "PARTIALLY_FILLED", "CANCELED"].includes(order.status)
          || ["-2015", "-2014", "6003"].some(code => String(order.message || "").includes(code))) void refreshAccount();
    }
    const orderClock = value => {
      const date = new Date(value);
      if (!value || Number.isNaN(date.getTime())) return "—";
      const pad = number => String(number).padStart(2, "0");
      return pad(date.getMonth() + 1) + "-" + pad(date.getDate()) + " "
        + pad(date.getHours()) + ":" + pad(date.getMinutes()) + ":" + pad(date.getSeconds());
    };
    async function refreshOrders() {
      const current = ++generation;
      const query = new URLSearchParams({exchange:exchange.value, market, symbol});
      try {
        const value = await liveJson("/api/live/orders?" + query);
        if (current !== generation) return;
        cachedOrders = value.orders || [];
        renderOrders();
        updateFields();
      } catch (error) {
        if (current === generation) { orders.textContent = "订单读取失败：" + error.message; }
      }
    }
    function renderOrders() {
      for (const button of tabs.children) {
        const filter = button.dataset.filter;
        const count = cachedOrders.filter(order => filter === "all" || groupFor(order) === filter).length;
        button.textContent = ({all:"全部",open:"当前委托",history:"历史订单",attention:"待核对"})[filter] + " (" + count + ")";
        button.setAttribute("aria-pressed", String(filter === orderFilter));
      }
      const visible = cachedOrders.filter(order => orderFilter === "all" || groupFor(order) === orderFilter);
      orders.replaceChildren(...visible.map(order => {
          const row = document.createElement("div");
          row.className = "live-order-row";
          const trade = document.createElement("strong");
          const direction = order.market === "futures" ? (order.action === "CLOSE" ? "平" : "开") + (order.position === "LONG" ? "多" : "空") : order.side === "BUY" ? "买入" : "卖出";
          trade.textContent = orderClock(order.created_at) + " · " + direction + " · " + (order.type === "LIMIT" ? "限价 " + order.price : "市价");
          const state = document.createElement("span");
          state.textContent = statuses[order.status] || order.status;
          state.className = "live-order-state " + (uncertainStates.includes(order.status) ? "negative" : order.status === "FILLED" ? "positive" : "");
          state.title = order.message || "";
          const fill = document.createElement("span");
          fill.textContent = (order.filled == null ? "成交量待确认" : "成交量 " + order.filled + " " + coin)
            + (order.quote_filled == null ? "" : " · " + order.quote_filled + " USDT");
          const identity = document.createElement("small");
          identity.textContent = order.order_id || order.id;
          const actions = document.createElement("div");
          const queryBtn = document.createElement("button");
          queryBtn.type = "button"; queryBtn.className = "secondary";
          const final = ["FILLED", "CANCELED", "REJECTED", "EXPIRED", "ACKNOWLEDGED"].includes(order.status);
          queryBtn.textContent = final ? (order.status === "FILLED" ? "已成交" : "已结束") : "查单";
          queryBtn.title = final ? "终态订单不需要再查询" : "向交易所查询这笔订单的最新状态";
          queryBtn.disabled = final;
          queryBtn.onclick = () => manage(order, "query");
          const detailBtn = document.createElement("button");
          detailBtn.type = "button"; detailBtn.className = "secondary"; detailBtn.textContent = "详情";
          detailBtn.title = "查看本地订单记录与成交信息";
          detailBtn.onclick = () => showOrderDetails(order);
          actions.append(detailBtn);
          if (!final) actions.append(queryBtn);
          if (order.source === "manual" && ["NEW", "PARTIALLY_FILLED", "ACCEPTED"].includes(order.status)) {
            const cancel = document.createElement("button");
            cancel.type = "button"; cancel.className = "secondary"; cancel.textContent = "撤单";
            cancel.onclick = () => manage(order, "cancel");
            actions.append(cancel);
          }
          if (["SUBMITTING","UNKNOWN","CANCEL_PENDING","CANCEL_UNKNOWN"].includes(order.status)) {
            const checked = document.createElement("button");
            checked.type = "button"; checked.className = "secondary"; checked.textContent = "已人工核对";
            checked.onclick = () => manage(order, "acknowledge");
            actions.append(checked);
          }
          row.append(trade, state, fill, identity, actions);
          return row;
        }));
      if (!orders.children.length) orders.textContent = "当前筛选下暂无本终端订单。";
    }
    async function manage(order, action) {
      if (busy) return;
      setBusy(true);
      try {
        if (action === "cancel" && !await confirmDialog("交易所订单 " + (order.order_id || order.id), {
          title:"确认撤销实盘委托", confirmText:"确认撤单", danger:true, focusCancel:true,
        })) return;
        if (action === "acknowledge" && !await confirmDialog("请先在交易所核对这笔订单及仓位。此操作仅结束本地跟踪。", {
          title:"标记为已人工核对", confirmText:"我已核对", danger:true, focusCancel:true,
        })) return;
        const result = await liveJson("/api/live/order/" + action, {id:order.id});
        showReceipt(result);
      } catch (error) {
        announce(exchangeError(error), true);
      } finally {
        setBusy(false);
        void refreshOrders();
      }
    }
    form.addEventListener("change", updateFields);
    fields.leverage?.addEventListener("input", updateFields);
    fields.percent?.addEventListener("input", () => {
      if (busy || !maxSell) return;
      const pct = Math.max(0, Math.min(100, Number(fields.percent.value) || 0));
      const step = ticket.rulesFor(rules, fields.kind.value)?.step;
      const qty = floorQty(maxSell * pct / 100, step);
      fields.amount.value = qty > 0 ? ticket.quantity(qty, step) : "";
      card.querySelector(".live-percent output").textContent = pct + "%";
      updateFields();
    });
    fields.amount?.addEventListener("input", () => {
      if (!maxSell || fields.side.value !== "SELL") { updateFields(); return; }
      const pct = Math.max(0, Math.min(100, Math.round((Number(fields.amount.value) || 0) / maxSell * 100)));
      fields.percent.value = String(pct);
      card.querySelector(".live-percent output").textContent = pct + "%";
      updateFields();
    });
    fields.price.addEventListener("input", updateFields);
    fields.kind.addEventListener("change", () => {
      if (busy) return;
      fields.amount.value = "";
      fields.percent.value = "0";
      updateFields();
    });
    for (const button of form.querySelectorAll("[data-direction]")) button.addEventListener("click", () => {
      if (busy) return;
      fields.side.value = button.dataset.direction;
      fields.amount.value = "";
      if (fields.percent) fields.percent.value = "0";
      updateFields();
      void loadRules();
    });
    const requestTimeout = 45000;
    recoverButton.addEventListener("click", async () => {
      const key = pendingReceipt();
      if (!key || busy) return;
      const confirmation = key.slice(receiptScope().length);
      setBusy(true);
      try {
        const result = await liveJson("/api/live/order/recover", {confirmation}, "POST", requestTimeout);
        if (result.state === "recorded" && result.order?.id === confirmation && statuses[result.order.status]) {
          showReceipt(result.order, true);
        } else if (result.state === "not_submitted") {
          announce("本机未送出该订单，原确认已失效。需要下单时请重新预览。");
        } else throw Error("回执数据不完整");
        clearReceipt(key);
      } catch (error) {
        announce("暂时无法核对回执：" + exchangeError(error), true);
      } finally {
        setBusy(false);
        void refreshOrders();
      }
    });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (busy || previewBtn.disabled) return;
      const provider = exchange.value;
      const input = {exchange:provider, market, symbol, type:fields.kind.value};
      if (input.type === "LIMIT") input.price = fields.price.value;
      if (futures) {
        input.position = fields.side.value;
        input.action = fields.action.value;
        if (coinwFutures() && closing()) input.position_id = fields.position.value;
        if (coinwFutures() && !closing()) {
          input.leverage = Number(fields.leverage.value);
          input.cross = fields.margin.value === "cross";
        }
      } else input.side = fields.side.value;
      input[quoteMarket() ? "quote" : "quantity"] = fields.amount.value;
      setBusy(true);
      announce("");
      let sent = false;
      try {
        const preview = await liveJson("/api/live/preview", input, "POST", requestTimeout);
        const order = preview.order;
        if (!confirmationPattern.test(preview.confirmation) || order?.id !== preview.confirmation ||
          ["exchange", "market", "symbol", "type"].some(field => order[field] !== input[field]) ||
          (futures ? order.action !== input.action || order.position !== input.position : order.side !== input.side)) {
          throw Error("订单预览与表单不一致，请重新预览。");
        }
        const expires = Date.now() + Number(preview.expires_in || 0) * 1000;
        const title = futures
          ? (order.action === "OPEN" ? "开" : "平") + (order.position === "LONG" ? "多" : "空")
          : order.side === "BUY" ? "买入" : "卖出";
        const amount = order.quote ? order.quote + " USDT" : order.quantity + " " + (coinwFutures() && closing() ? "张" : coin);
        const mode = futures && !closing() ? (coinwFutures() ? "\n" + order.leverage + "× · " + (order.cross ? "全仓" : "逐仓")
          : "\n使用 Binance 当前杠杆与保证金模式。") : "";
        const approved = await confirmDialog(
          exchange.selectedOptions[0].textContent + " · " + symbol + "\n" + title + " · " + amount
          + "\n" + (order.price ? "限价 " + order.price + " USDT" : "市价撮合") + mode,
          {title:"确认提交主网订单", confirmText:"提交实盘订单", danger:true, focusCancel:true}
        );
        if (!approved) return;
        if (!configured || exchange.value !== provider || !(Date.now() < expires))
          throw Error("连接状态已变或订单确认已过期，请重新预览。");
        if (pendingReceipt() || cachedOrders.some(item => uncertainStates.includes(item.status)))
          throw Error("请先核对上一笔订单。");
        const key = rememberReceipt(preview.confirmation);
        sent = true;
        const result = await liveJson("/api/live/order", {confirmation:preview.confirmation}, "POST", requestTimeout);
        if (result.id !== preview.confirmation || !statuses[result.status]) throw Error("回执数据不完整");
        showReceipt(result, true);
        clearReceipt(key);
      } catch (error) {
        announce((sent ? "未收到可靠回执，请先核对提交结果。 " : "") + exchangeError(error), true);
        if (error.code === -2015) void refreshAccount();
      } finally {
        setBusy(false);
        void refreshOrders();
      }
    });
    void refreshOrders();
    void loadRules();
    setInterval(() => { if (!document.hidden && !busy) void refreshOrders(); }, 5000);
    updateFields();
    return {
      account(snapshot) { accountKnown = !!snapshot.balances; positions = snapshot.positions || []; balances = snapshot.balances || []; caps = snapshot.leverage || null; updateFields(); void loadRules(); },
      enabled(value, message) {
        configured = Boolean(value);
        status.textContent = message || (configured ? "每笔订单需要预览并确认。" : "连接未就绪，下单已锁定。");
        updateFields();
      },
      exchangeChanged() {
        configured = false; accountKnown = false; positions = []; balances = []; rules = null; caps = null;
        ruleRequest += 1;
        cachedOrders = []; renderOrders();
        form.reset();
        announce("");
        status.textContent = "正在检查 API 连接…";
        updateFields();
        void refreshOrders();
        void loadRules();
      },
    };
  }

  function mountLiveStrategy(section, exchange, symbol, futures, grid, features) {
    const card = document.createElement("details");
    card.className = "live-strategy panel";
    card.open = grid;
    const kind = grid ? "grid" : "auto";
    const market = futures ? "futures" : "spot";
    const label = grid ? "投入上限（USDT）" : futures ? "每次开仓名义价值（USDT）" : "每次买入金额（USDT）";
    card.innerHTML = `
      <summary><strong>${grid?"实盘网格":"实盘 AUTO"}</strong><span class="live-strategy-state">未启动</span></summary>
      <form class="live-order-form">
        <label class="strategy-amount">${label}<input name="amount" type="number" min="0.0001" step="0.0001" required></label>
        ${grid ? '<p class="grid-budget-hint" data-grid-budget></p>' : ""}
        ${grid ? `<label>下限（USDT）<input name="lower" type="number" min="0.000000000001" step="any" required></label>
          <label>上限（USDT）<input name="upper" type="number" min="0.000000000001" step="any" required></label>
          <label>网格数<input name="levels" type="number" min="2" max="12" value="6" required></label>
          <label>分布<select name="distribution"><option value="arithmetic">等差</option><option value="geometric">等比</option></select></label>
          <label>低于下边界止损（%）<input name="stop_loss_pct" type="number" min="0" max="50" step="0.1" value="0"></label>
          <label>初始仓位（%）<input name="initial_ratio" type="number" min="0" max="50" step="1" value="0"></label>` :
          `<label>执行周期<select name="interval"><option value="1h">1H</option><option value="4h">4H</option>
            <option value="1m">1M</option><option value="5m">5M</option><option value="15m">15M</option>
            <option value="30m">30M</option><option value="2h">2H</option><option value="8h">8H</option>
            <option value="12h">12H</option><option value="1d">1D</option></select></label>
          <label class="strategy-model">执行模型<select name="model"><option value="${futures?"trend-v2":"spot-trend-v2"}">默认趋势模型</option></select></label>`}
        <button class="primary" type="submit" disabled>预览策略</button>
      </form>
      <p class="hint live-strategy-note" role="status"></p>
      <div class="live-strategy-actions">
        <button type="button" data-strategy="stop" class="secondary" hidden>${grid?"停网并撤委托":"暂停 AUTO"}</button>
        <button type="button" data-strategy="resume" class="secondary" hidden>继续</button>
        ${grid?"":'<button type="button" data-strategy="retire" class="secondary" hidden>结束跟踪</button>'}
      </div>`;
    const sidebar = section.querySelector(".live-trade-sidebar");
    const account = section.querySelector(".live-account");
    if (sidebar) account.before(card);
    else {
      const wrapper = document.createElement("div");
      wrapper.className = "live-trade-sidebar";
      account.before(wrapper);
      wrapper.append(card, account);
    }
    const form = card.querySelector("form");
    const fields = form.elements;
    const status = card.querySelector(".live-strategy-state");
    const note = card.querySelector(".live-strategy-note");
    const button = form.querySelector("button");
    const controls = [...card.querySelectorAll("[data-strategy]")];
    card.querySelector("[data-open-risk]")?.addEventListener("click", () => {
      const panel = section.querySelector(".live-risk-settings");
      if (!panel) return;
      panel.open = true;
      panel.scrollIntoView({behavior:"smooth", block:"center"});
    });
    const book = section.querySelector(".live-orders");
    let configured = false, busy = false, plan = null, request = 0;
    const states = {running:"运行中", paused:"暂停", stopping:"撤单中", stopped:"已停止"};
    const supported = () => grid || features.auto.includes(exchange.value);
    let gridCap = null;
    function updateGridBudget(previewPlan = null) {
      if (!grid) return;
      const box = card.querySelector("[data-grid-budget]");
      if (!box) return;
      const amount = Number(fields.amount.value) || 0;
      const over = gridCap != null && amount > gridCap + 1e-9;
      const pieces = [];
      if (gridCap == null) pieces.push("正在读取网格预算上限…");
      else pieces.push("总预算上限 " + gridCap.toFixed(2) + " USDT");
      if (amount > 0) pieces.push("本单 " + amount.toFixed(2) + " USDT");
      if (gridCap != null && amount > 0) {
        pieces.push(over ? "超出 " + (amount - gridCap).toFixed(2) + " USDT" : "距预算上限 " + (gridCap - amount).toFixed(2) + " USDT（非账户余额）");
      }
      if (previewPlan?.cells?.length) {
        pieces.push("预计 " + previewPlan.cells.length + " 格 · 每格约 " + (amount / previewPlan.cells.length).toFixed(2) + " USDT");
      } else if (amount > 0) {
        pieces.push("预览后显示实际挂单格数与每格金额");
      }
      box.textContent = pieces.join(" · ");
      box.classList.toggle("negative", over);
      box.title = over ? "本单预算超过网格总预算上限，预览会被拒绝" : "";
    }
    async function loadGridBudget() {
      if (!grid) return;
      try {
        const value = await liveJson("/api/live/manual-risk");
        gridCap = Number(value.max_grid_budget) || 0;
      } catch (_) { gridCap = null; }
      updateGridBudget();
      render();
    }

    if (!grid) {
      liveJson("/api/models?scope="+market).then(data => {
        const valid = data.models.filter(m => m.kind==="rules" && m.ai_mode==="off" && m.scope===market);
        if (valid.length) fields.model.replaceChildren(...valid.map(m => new Option(m.name, m.id)));
      }).catch(() => {});
    } else {
      $("liveExecutionStatus").textContent = "网格从当前价以下开始挂买单，买入成交后在上一格挂卖单。";
    }
    const gridRows = document.createElement("div");
    gridRows.className = "live-order-list";
    if (grid) book.append(gridRows);
    if (grid) void loadGridBudget();
    if (grid) {
      const summary = document.createElement("p");
      summary.className = "live-grid-summary hint";
      summary.setAttribute("role", "status");
      button.before(summary);
      const updateRange = () => {
        const lower = Number(fields.lower.value), upper = Number(fields.upper.value), levels = Number(fields.levels.value);
        const invalid = lower > 0 && upper > 0 && upper <= lower;
        fields.upper.setCustomValidity(invalid ? "上限必须高于下限" : "");
        summary.classList.toggle("negative", invalid);
        summary.textContent = invalid ? "价格上限必须高于下限。"
          : lower > 0 && upper > lower && levels >= 2
            ? (fields.distribution.value === "geometric"
              ? "每格价差约 " + ((Math.pow(upper / lower, 1 / levels) - 1) * 100).toFixed(2) + "%"
              : "每格价差约 " + ((upper - lower) / levels).toFixed(4) + " USDT")
              + " · 仅为价格间距，未扣手续费。预览将检查当前价、每格最小金额和 0.5% 最小间距。"
            : "填写区间后查看网格间距。当前价需位于区间内；止损 0 表示关闭。";
        updateGridBudget(); render();
      };
      form.addEventListener("input", updateRange);
      form.addEventListener("change", updateRange);
      updateRange();
    }
    window.addEventListener("btcquant:risk", event => {
      if (!grid || !event.detail) return;
      gridCap = Number(event.detail.max_grid_budget) || gridCap;
      updateGridBudget();
      render();
    });

    function capabilityWord() {
      const state = section.querySelector(".live-connection-bar")?.dataset.state;
      return {ready: "可用", partial: "受限", blocked: "锁定", unknown: "未核验"}[state] || "未核验";
    }
    function render() {
      const active = plan && plan.status !== "stopped";
      status.textContent = (grid ? "网格：" + capabilityWord() + " · " : "")
        + (active ? "计划" + (states[plan.status] || plan.status) + (configured ? "" : " · 连接未就绪") : "未启动");
      status.classList.toggle("negative", Boolean(active && !configured));
      button.disabled = busy || !supported() || !configured || Boolean(active)
        || (grid && gridCap != null && Number(fields.amount.value) > gridCap + 1e-9);
      [...fields].filter(field => field !== button).forEach(field => { field.disabled = Boolean(active) || busy; });
      for (const control of controls) {
        const action = control.dataset.strategy;
        control.hidden = action === "stop"
          ? !active || plan.status === "stopping" || (!grid && plan.status === "paused")
          : plan?.status !== "paused";
        control.disabled = busy;
      }
      if (!supported()) note.textContent = "实盘 AUTO 当前支持 Binance 现货及合约。";
      else if (plan) note.textContent = (!configured ? "账户未通过核验，计划状态不代表执行成功。请读取账户并核对交易所订单。 " : "") + (plan.message || states[plan.status] || "");
      else note.textContent = grid ? "停网会撤销本网格的挂单，已成交的资产保留在账户。"
        : futures ? "按已收盘信号交易。逐仓、双向持仓，杠杆 1–10×。"
        : "按已收盘信号买入或退出。每次买入使用填写的金额。";
      if (grid) {
        updateGridBudget();
        gridRows.replaceChildren(...(plan?.cells || []).map(cell => {
          const row = document.createElement("div");
          row.className = "live-order-row";
          for (const text of ["买 "+cell.buy, "卖 "+cell.sell, "库存 "+cell.held,
            "循环 "+cell.cycle, cell.order ? "已记录委托" : "待挂单"]) {
            const value = document.createElement("span");
            value.textContent = text;
            row.append(value);
          }
          return row;
        }));
      }
    }
    function setBusy(value) {
      busy = value;
      exchange.disabled = value;
      render();
    }
    async function refresh() {
      if (busy || !supported()) return;
      const current = ++request;
      try {
        const data = await liveJson("/api/live/strategy?"+new URLSearchParams({exchange:exchange.value, market, symbol, kind}));
        if (current !== request || busy) return;
        const was = plan?.id;
        plan = data.plan;
        if (plan && was !== plan.id) {
          fields.amount.value = plan.amount;
          if (grid) {
            fields.lower.value = plan.lower;
            fields.upper.value = plan.upper;
            fields.levels.value = plan.levels;
            if (plan.distribution) fields.distribution.value = plan.distribution;
            fields.stop_loss_pct.value = plan.stop_loss_pct ?? 0;
            fields.initial_ratio.value = plan.initial_ratio == null ? 0 : Number(plan.initial_ratio) * 100;
          } else {
            fields.interval.value = plan.interval;
            fields.model.value = plan.model_id;
          }
        }
        render();
      } catch (error) {
        if (current === request && !busy) note.textContent = error.message;
      }
    }
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (button.disabled) return;
      const config = {kind, exchange:exchange.value, market, symbol, amount:fields.amount.value};
      if (grid) Object.assign(config, {lower:fields.lower.value, upper:fields.upper.value, levels:Number(fields.levels.value), distribution:fields.distribution.value, stop_loss_pct:Number(fields.stop_loss_pct.value), initial_ratio:Number(fields.initial_ratio.value || 0) / 100});
      else Object.assign(config, {interval:fields.interval.value, model_id:fields.model.value});
      setBusy(true);
      let error = "";
      try {
        const preview = await liveJson("/api/live/strategy/preview", config);
        const p = preview.plan;
        const lines = [exchange.selectedOptions[0].textContent + " · " + symbol];
        if (grid) { updateGridBudget(p); }
        if (grid) lines.push("投入上限 "+p.amount+" USDT · "+p.lower+" – "+p.upper,
          "将分批提交 "+p.cells.length+" 档买入委托。停网撤销策略委托，已买入资产留在现货账户。",
          "分布 " + (p.distribution === "geometric" ? "等比" : "等差") + " · 止损 " + (p.stop_loss_pct || 0) + "%"
          + (Number(p.initial_budget || 0) > 0 ? " · 初始仓位将市价买入 " + p.initial_budget + " USDT" : ""));
        else {
          lines.push((futures ? "单次目标名义价值 " : "单次买入金额 ") + p.amount + " USDT · "+p.interval.toUpperCase());
          if (futures) lines.push("逐仓 "+p.leverage+"× · 预计初始保证金约 "+(Number(p.amount)/p.leverage).toFixed(2)+" USDT",
            "开仓成交后提交交易所止损。保护委托失败时暂停，请及时查看真实仓位。");
          else lines.push("按模型趋势信号卖出该策略买入的仓位。");
        }
        lines.push("重启后策略暂停，交易所已挂委托仍有效。");
        if (!await confirmDialog(lines.join("\n"), {title:"确认启动实盘策略", confirmText:"启动实盘", danger:true, focusCancel:true})) return;
        if (!grid) {
          const typed = await promptDialog("AUTO 会按模型信号自动向真实账户下单。 请输入 AUTO 确认二次启动：", "", {
            title:"二次确认 AUTO 实盘", confirmText:"继续启动",
          });
          if (typed !== "AUTO") return;
        }
        let initialConfirm = null;
        if (Number(p.initial_budget || 0) > 0) {
          initialConfirm = await promptDialog(
            "初始仓位将立即市价买入约 " + p.initial_budget + " USDT 的 " + symbol.slice(0, -4) + "。\n输入 BUY 确认：",
            "", {title:"确认初始仓位", confirmText:"确认买入"}
          );
          if (initialConfirm !== "BUY") return;
        }
        plan = await liveJson("/api/live/strategy/start", {confirmation:preview.confirmation, initial_confirm:initialConfirm});
      } catch (e) { error = e.message; }
      finally {
        setBusy(false);
        if (error) note.textContent = error;
      }
    });
    controls.forEach(control => {
      control.onclick = async () => {
        if (!plan || busy) return;
        const action = control.dataset.strategy;
        const copy = action==="resume" ? "继续后策略将核对委托并恢复真实执行。"
          : action==="retire" ? "保留交易所现有资产和保护委托，由你手动管理。"
          : grid ? "只撤本网格尚未成交的委托。已成交资产保留在现货账户。" : "暂停 AUTO，已在交易所挂出的保护委托继续有效。";
        setBusy(true);
        let error = "";
        try {
          if (!await confirmDialog(copy, {title:control.textContent, danger:true, focusCancel:true})) return;
          plan = await liveJson("/api/live/strategy/control", {id:plan.id, action});
        } catch (e) { error = e.message; }
        finally {
          setBusy(false);
          if (error) note.textContent = error;
        }
      };
    });
    render();
    void refresh();
    setInterval(refresh, 5000);
    return {
      enabled(value) { configured = Boolean(value); render(); },
      exchangeChanged() {
        request += 1;
        configured = false;
        plan = null;
        form.reset();
        render();
        void refresh();
      },
    };
  }

  function initDisplayMenu() {
    const actions = document.querySelector(".topbar .top-actions");
    if (!actions || chartOnly) return;
    let menu = actions.querySelector(".ui-menu");
    if (!menu) {
      menu = document.createElement("div");
      menu.className = "ui-menu";
      actions.append(menu);
    }
    menu.innerHTML = `
      <button id="uiBtn" type="button" class="icon-btn display-trigger" title="显示设置"
        aria-label="显示设置" aria-expanded="false" aria-controls="uiPanel">Aa</button>
      <div id="uiPanel" class="ui-panel hidden" role="group" aria-label="显示设置">
        <div class="ui-head"><strong>显示设置</strong><button id="uiReset" class="ui-reset" type="button">恢复默认</button></div>
        <div class="ui-row"><span>外观</span><div class="seg ui-seg">
          <button type="button" data-ui-theme="dark">深色</button><button type="button" data-ui-theme="light">浅色</button>
        </div></div>
        <div class="ui-row"><label for="uiFont">字体大小</label><output id="uiFontLabel" for="uiFont">100%</output></div>
        <div class="ui-font-controls">
          <button id="uiSmaller" type="button" aria-label="缩小字体">−</button>
          <input id="uiFont" type="range" min="90" max="130" step="5" value="100">
          <button id="uiLarger" type="button" aria-label="放大字体">＋</button>
        </div>
        <div class="ui-font-scale"><span>90%</span><span>130%</span></div>
      </div>`;
  }

  function init() {
    initMode();
    initDisplayMenu();
    let theme = "dark";
    try { theme = localStorage.getItem(THEME_KEY) || root.dataset.theme || "dark"; } catch (_) { /* ignore */ }
    apply(theme, readNumber(FONT_KEY, 1), false);
    window.addEventListener("storage", event => {
      if (event.key === THEME_KEY || event.key === FONT_KEY) {
        apply(localStorage.getItem(THEME_KEY), readNumber(FONT_KEY, 1), false);
      }
    });
    const btn = $("uiBtn");
    const panel = $("uiPanel");
    if (!btn || !panel) return;
    const setOpen = (open, restoreFocus = false) => {
      panel.classList.toggle("hidden", !open);
      btn.setAttribute("aria-expanded", String(open));
      if (restoreFocus) btn.focus();
    };
    btn.addEventListener("click", () => setOpen(panel.classList.contains("hidden")));
    document.addEventListener("pointerdown", event => {
      if (!event.target.closest(".ui-menu")) setOpen(false);
    });
    const range = $("uiFont");
    const scaleBy = step => apply(root.dataset.theme, (Number(range.value) + step) / 100);
    range.addEventListener("input", () => scaleBy(0));
    $("uiSmaller").addEventListener("click", () => scaleBy(-5));
    $("uiLarger").addEventListener("click", () => scaleBy(5));
    $("uiReset").addEventListener("click", () => apply("dark", 1));
    for (const themeBtn of panel.querySelectorAll("[data-ui-theme]")) {
      themeBtn.addEventListener("click", () => apply(themeBtn.dataset.uiTheme, Number(range.value) / 100));
    }
    document.addEventListener("keydown", event => {
      if (event.key !== "Escape") return;
      if (!panel.classList.contains("hidden")) setOpen(false, true);
      const modal = $("btcModal");
      if (modal && !modal.classList.contains("hidden") && modal._close) modal._close(false);
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
