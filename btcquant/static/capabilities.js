/* 1.0 capability contract: four states, reason -> state closure, connection bar + details.
   Source of truth: release/.../api_spec.md §1-2 and requirements.md §5.2 / §5.8 / §6.
   No polling: render on mount (one /api/capabilities cache read + one /api/exchange/health),
   on manual "重新检测" and on exchange switch only. The capability details also expose the
   read-only UC-03 audit (one GET per explicit click); no READY is rendered before the
   in-session probe settles (requirements §13.1). */
(() => {
  "use strict";

  /* reason -> state / retryable / fallback text (api_spec §1; requirements §5.2/5.4). */
  const REASONS = {
    OK: {state: "READY", retryable: false, text: "已探测可用。"},
    UNVERIFIED: {state: "UNKNOWN", retryable: true, text: "尚未核验该能力或交易权限，状态未知。点击“重新检测”获取实时结果。"},
    PAPER_MODE: {state: "BLOCKED", retryable: false, text: "当前为 PAPER 模式，实盘接口按设计禁用。切换到实盘模式后重新检测。"},
    NOT_CONFIGURED: {state: "BLOCKED", retryable: true, text: "尚未配置该市场的 API 连接。保存 Key 与 Secret 后可重新检测。"},
    AUTH_REJECTED: {state: "BLOCKED", retryable: true, text: "Binance 拒绝认证（-2015）。请依次核对：① API Key 是否有效且未被删除；② 当前出口 IP 是否在 Key 的白名单内；③ Key 是否具备所需读取/交易权限。该错误码无法区分以上三类原因，本机不会自动重试或恢复 AUTO。"},
    AUTH_INVALID_KEY: {state: "BLOCKED", retryable: true, text: "Binance API Key 无效或格式错误（-2014）。请在 Binance 后台重新生成并保存 Key 与 Secret。"},
    SIGNATURE_INVALID: {state: "BLOCKED", retryable: true, text: "交易所签名校验失败（-1022/6001）。请确认 Secret 与 Key 对应；仍失败时请重新保存连接。"},
    TIME_SKEW: {state: "BLOCKED", retryable: true, text: "Binance 拒绝请求时间戳（-1021）。请先同步系统时间，再手动重新检测；本机不会自动重签重试。"},
    PERMISSION_DENIED: {state: "BLOCKED", retryable: true, text: "读取成功，但该 API Key 或账户没有当前市场的交易权限。请到交易所后台开启对应权限。"},
    PENDING_RECONCILE: {state: "PARTIAL", retryable: true, text: "存在待核对（-2013 或回执未确认）的订单，相关交易对禁止开新单。请按原确认号核对，系统不会重复提交，也不会据此清账。"},
    VENUE_UNAVAILABLE: {state: "UNKNOWN", retryable: true, text: "交易所暂时不可用或限流（{code}），结果未知。请稍后手动重新检测。"},
    PROBE_TIMEOUT: {state: "UNKNOWN", retryable: true, text: "读取失败或超时，结果未知。请检查网络后手动重新检测；已提交的订单请继续按确认号核对，不要重复提交。"},
    MARKET_DATA_UNAVAILABLE: {state: "UNKNOWN", retryable: true, text: "公开行情读取失败，无法确认该市场状态。请稍后手动重新检测。"},
    NOT_IMPLEMENTED: {state: "BLOCKED", retryable: false, text: "当前版本不支持该组合。如需接入请单独立项。"},
    OUT_OF_SCOPE: {state: "BLOCKED", retryable: false, text: "本迭代不覆盖该能力（如 USDC 计价）。"},
  };

  const CAPABILITIES = ["market_data", "account_read", "manual_order", "auto_strategy", "grid"];
  const LABELS = {market_data: "行情", account_read: "账户", manual_order: "手动交易", auto_strategy: "AUTO", grid: "网格"};
  const RANK = {READY: 0, UNKNOWN: 1, PARTIAL: 2, BLOCKED: 3};

  /* State labels are verbatim from requirements §6.1 / §6.2. */
  const STATES = {
    READY: {title: "账户已连接 · 可预览订单", badge: "交易就绪", css: "mini-badge green", connectionState: "ready", word: "可用"},
    PARTIAL: {title: "账户可读取 · 部分能力受限", badge: "部分可用", css: "mini-badge neutral", connectionState: "partial", word: "受限"},
    BLOCKED: {title: "交易未就绪 · 下单锁定", badge: "不可用", css: "mini-badge neutral danger", connectionState: "blocked", word: "锁定"},
    UNKNOWN: {title: "状态未核验 · 下单锁定", badge: "未核验", css: "mini-badge neutral", connectionState: "unknown", word: "未核验"},
  };

  const UNSUPPORTED_WORD = "不支持";
  const controllers = new WeakMap();

  function stateLabel(state) {
    const label = STATES[state] || STATES.UNKNOWN;
    return {title: label.title, badge: label.badge, css: label.css, connectionState: label.connectionState, word: label.word};
  }

  function reasonText(reason, code) {
    const fixed = REASONS[reason];
    if (!fixed) return "状态未知。点击“重新检测”获取实时结果。";
    return code == null ? fixed.text : fixed.text.replace("{code}", String(code));
  }

  function errorText(error) {
    if (error == null) return "请求失败";
    if (typeof error === "string") return REASONS[error] ? reasonText(error) : error;
    if (typeof error.hint === "string" && error.hint) return error.hint;
    if (typeof error.reason === "string" && REASONS[error.reason]) return reasonText(error.reason, error.code);
    if (typeof error.detail === "string" && error.detail) return error.detail;
    if (typeof error.message === "string" && error.message) return error.message;
    return "请求失败";
  }

  async function requestJson(path) {
    const response = await fetch(path, {cache: "no-store", method: "GET"});
    let body = null;
    try { body = await response.json(); } catch (_) { body = null; }
    if (!response.ok || response.status >= 400) {
      const detail = body && typeof body.detail === "string" ? body.detail : "";
      const error = new Error(detail || "请求失败");
      error.status = response.status;
      error.code = body ? body.code ?? null : null;
      error.reason = body ? body.reason ?? null : null;
      error.hint = body ? body.hint ?? null : null;
      error.retryable = Boolean(body && body.retryable);
      error.body = body;
      throw error;
    }
    return body || {};
  }

  function query(scope, extras) {
    const params = new URLSearchParams();
    if (scope && scope.exchange) params.set("exchange", scope.exchange);
    if (scope && scope.market) params.set("market", scope.market);
    for (const [key, value] of Object.entries(extras || {})) {
      if (value != null && value !== "") params.set(key, String(value));
    }
    const text = params.toString();
    return text ? "?" + text : "";
  }

  function load(scope, options) {
    const extras = options && options.force ? {refresh: "1"} : {};
    return requestJson("/api/capabilities" + query(scope, extras));
  }

  function health(scope) {
    return requestJson("/api/exchange/health" + query(scope, {}));
  }

  function futures(scope) {
    return requestJson("/api/futures/market" + query(scope, {}));
  }

  /* UC-03 read-only audit (api_spec §2.3). GET only: never re-submits or clears orders. */
  function audit(scope, symbol) {
    return requestJson("/api/live/account/audit" + query(scope, {symbol}));
  }

  function keyOf(scope) {
    return (scope.exchange || "?") + ":" + (scope.market || "?");
  }

  function entriesFromMatrix(matrix, exchange, market) {
    if (!matrix || typeof matrix !== "object") return null;
    const table = matrix.capabilities && !matrix.exchange ? matrix.capabilities : matrix;
    const scope = table && table[exchange] && table[exchange][market];
    if (!scope || typeof scope !== "object") return null;
    const entries = CAPABILITIES.map(id => ({id, node: scope[id]})).filter(entry => entry.node && entry.node.state);
    return entries.length ? entries : null;
  }

  function entriesFromHealth(response) {
    const caps = response && response.capabilities;
    if (!caps || typeof caps !== "object") return null;
    const entries = CAPABILITIES.map(id => ({id, node: caps[id]})).filter(entry => entry.node && entry.node.state);
    return entries.length ? entries : null;
  }

  function reduceState(entries) {
    let worst = "READY";
    let seen = false;
    for (const entry of entries || []) {
      const node = entry.node;
      if (!node || node.support === "unsupported") continue;
      seen = true;
      const state = STATES[node.state] ? node.state : "UNKNOWN";
      if (RANK[state] > RANK[worst]) worst = state;
    }
    return seen ? worst : "UNKNOWN";
  }

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function stamp(iso) {
    const date = iso ? new Date(iso) : null;
    if (!date || Number.isNaN(date.getTime())) return null;
    return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate())
      + " " + pad(date.getHours()) + ":" + pad(date.getMinutes()) + ":" + pad(date.getSeconds());
  }

  function pageMarket() {
    return typeof location !== "undefined" && location.pathname === "/futures" ? "futures" : "spot";
  }

  function barOf(target) {
    if (!target) return document.querySelector(".live-connection-bar");
    if (target.matches && target.matches(".live-connection-bar")) return target;
    return target.querySelector ? target.querySelector(".live-connection-bar") : null;
  }

  function ensureEvidence(bar) {
    let node = bar.querySelector("[data-connection-evidence]");
    if (node) return node;
    node = document.createElement("p");
    node.className = "live-connection-evidence";
    node.setAttribute("data-connection-evidence", "");
    const message = bar.querySelector("[data-connection-message]");
    if (message) message.after(node);
    else {
      const head = bar.querySelector("div");
      (head || bar).append(node);
    }
    return node;
  }

  function ensureDetails(bar, onAudit) {
    let box = bar.querySelector(".live-capability-details");
    if (!box) {
      box = document.createElement("details");
      box.className = "live-capability-details";
      const summary = document.createElement("summary");
      summary.textContent = "能力明细";
      const body = document.createElement("div");
      body.className = "live-capability-body";
      /* UC-03 entry point (requirements §13.4): read-only audit inside the existing details,
         no new page/nav, no polling. The button is wired once by the controller. */
      const auditBox = document.createElement("div");
      auditBox.className = "live-capability-audit";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary";
      button.dataset.action = "audit-account";
      button.textContent = "核对账户";
      const status = document.createElement("p");
      status.className = "live-audit-status hint";
      status.setAttribute("data-audit-status", "");
      status.setAttribute("role", "status");
      auditBox.append(button, status);
      const table = document.createElement("div");
      table.className = "live-capability-table";
      body.append(auditBox, table);
      box.append(summary, body);
      bar.append(box);
    }
    if (!box.dataset.auditBound && typeof onAudit === "function") {
      box.querySelector('[data-action="audit-account"]').addEventListener("click", onAudit);
      box.dataset.auditBound = "1";
    }
    return box;
  }

  function createController(bar, host) {
    const exchangeSelect = host.querySelector("#liveExchange");
    const refreshButton = host.querySelector("[data-refresh-account]");
    /* S1 (requirements §13.1): `healthSettled` gates every non-probe READY source. Until this
       session's /api/exchange/health settles, a cache-only matrix or the legacy two-state hint
       may never render READY — the honest state is UNKNOWN. */
    const state = {matrix: null, healthNodes: null, healthError: null, healthSettled: false, legacy: null};
    let request = 0;

    function scope() {
      return {
        exchange: (exchangeSelect && exchangeSelect.value) || "binance",
        market: pageMarket(),
      };
    }

    function healthForKey(current) {
      return state.healthNodes && state.healthNodes.key === keyOf(current) ? state.healthNodes : null;
    }

    /* A matrix node may claim READY from a previous probe; before the in-session probe settles
       (and in details rows) it is shown as unverified, never READY. */
    function maskState(nodeState, maskReady) {
      return maskReady && nodeState === "READY" ? "UNKNOWN" : nodeState;
    }

    function wordFor(entry, fallbackState, maskReady) {
      if (!entry || !entry.node) return stateLabel(fallbackState).word;
      if (entry.node.support === "unsupported") return UNSUPPORTED_WORD;
      return stateLabel(maskState(entry.node.state, maskReady)).word;
    }

    function overallNow() {
      const current = scope();
      const health = healthForKey(current);
      if (health) return {state: reduceState(health.entries), entries: health.entries, current: health, provisional: false};
      if (state.healthSettled && state.healthError) {
        return {state: state.healthError.state, entries: null, current: null, provisional: false};
      }
      const entries = entriesFromMatrix(state.matrix, current.exchange, current.market);
      if (entries) {
        const probe = entries.some(entry => entry.node.evidence === "probe");
        const node = entries.map(entry => entry.node).find(item => item.checked_at);
        const matrix = {key: keyOf(current), entries, checkedAt: node ? node.checked_at : null,
          evidence: probe ? "probe" : "config", source: "matrix"};
        return {state: state.healthSettled ? reduceState(entries) : "UNKNOWN", entries,
          current: matrix, provisional: !state.healthSettled};
      }
      if (state.legacy) {
        const legacyState = state.legacy.pending ? "UNKNOWN"
          : (!state.legacy.ready ? "BLOCKED" : (state.healthSettled ? "READY" : "UNKNOWN"));
        return {state: legacyState, entries: null, current: null, provisional: false};
      }
      return {state: "UNKNOWN", entries: null, current: null, provisional: false};
    }

    function render() {
      const view = overallNow();
      const {state: overall, entries, current, provisional} = view;
      const label = stateLabel(overall);
      bar.dataset.state = label.connectionState;
      bar.dataset.connectionState = label.connectionState;
      const title = bar.querySelector("[data-connection-title]");
      if (title) title.textContent = label.title;
      const badge = host.querySelector("#liveBadge");
      if (badge) {
        badge.textContent = label.badge;
        badge.className = label.css;
        badge.dataset.state = label.connectionState;
      }
      const pieces = CAPABILITIES.map(id => {
        const entry = entries ? entries.find(item => item.id === id) : null;
        return LABELS[id] + "：" + wordFor(entry, overall, provisional);
      });
      const primaryId = pageMarket() === "spot" && typeof location !== "undefined" && location.pathname === "/grid" ? "grid" : "manual_order";
      const primary = entries ? entries.find(item => item.id === primaryId) : null;
      const hint = (primary && primary.node && primary.node.hint)
        || (primary && primary.node ? reasonText(primary.node.reason, primary.node.code) : "")
        || (state.healthError ? reasonText(state.healthError.reason, state.healthError.code) : "")
        || (state.legacy && state.legacy.message)
        || "";
      const message = bar.querySelector("[data-connection-message]");
      if (message) message.textContent = "连接不等于可交易。" + pieces.join("；") + "。" + hint;
      const evidence = ensureEvidence(bar);
      const checked = stamp(current && current.checkedAt);
      if (checked) evidence.textContent = "来源：" + (current.evidence === "probe" ? "探测" : "本地契约") + " · 核验 " + checked;
      else evidence.textContent = "来源：本地契约 · 尚未探测";
      bar.dataset.evidence = current ? current.evidence : "config";
      renderDetails(bar, entries, overall, provisional);
    }

    function renderDetails(targetBar, entries, overall, provisional) {
      const box = ensureDetails(targetBar, runAudit);
      const body = box.querySelector(".live-capability-table");
      const rows = CAPABILITIES.map(id => {
        const entry = entries ? entries.find(item => item.id === id) : null;
        const node = entry ? entry.node : null;
        const nodeState = node && STATES[node.state] ? maskState(node.state, provisional) : overall;
        const reason = node && provisional && node.state === "READY" ? "UNVERIFIED"
          : node ? (node.support === "unsupported" ? "NOT_IMPLEMENTED" : (node.reason || "UNVERIFIED")) : "UNVERIFIED";
        const line = document.createElement("div");
        line.className = "live-capability-row";
        line.dataset.capability = id;
        line.dataset.state = stateLabel(nodeState).connectionState;
        line.dataset.reason = reason;
        const name = document.createElement("span");
        name.className = "live-capability-name";
        name.textContent = LABELS[id];
        const status = document.createElement("b");
        status.className = "live-capability-state";
        status.textContent = wordFor(entry, overall, provisional);
        const reasonNode = document.createElement("code");
        reasonNode.className = "live-capability-reason";
        reasonNode.textContent = reason;
        const time = document.createElement("small");
        time.className = "live-capability-time";
        time.textContent = node && node.checked_at && !(provisional && node.state === "READY") ? stamp(node.checked_at) || "—" : "—";
        line.append(name, status, reasonNode, time);
        return line;
      });
      body.replaceChildren(...rows);
      box.dataset.state = stateLabel(overall).connectionState;
    }

    function syntheticHealth(response) {
      if (!response || typeof response !== "object") return null;
      const source = response.error && typeof response.error === "object" ? response.error : response;
      const reason = source.reason;
      if (!reason || !REASONS[reason]) return null;
      return {
        state: REASONS[reason].state,
        reason,
        code: source.code ?? null,
        hint: source.hint || reasonText(reason, source.code),
        retryable: source.retryable !== false,
      };
    }

    function applyHealth(response) {
      const current = scope();
      const entries = entriesFromHealth(response);
      state.healthSettled = true;
      if (entries) {
        state.healthNodes = {
          key: keyOf(current),
          entries,
          checkedAt: response.checked_at || null,
          evidence: "probe",
        };
        state.healthError = null;
        state.legacy = null;
      } else {
        state.healthNodes = null;
        state.healthError = syntheticHealth(response);
        if (state.healthError) state.legacy = null;
      }
      render();
    }

    function recheck() {
      const current = scope();
      if (current.exchange === "hyperliquid") return Promise.resolve();
      const token = ++request;
      state.healthSettled = false;
      state.healthError = null;
      return health(current).then(response => {
        if (token !== request || keyOf(scope()) !== keyOf(current)) return;
        applyHealth(response);
      }).catch(error => {
        if (token !== request || keyOf(scope()) !== keyOf(current)) return;
        state.healthSettled = true;
        state.healthNodes = null;
        /* A 404 means this backend predates the contract: fall back to the legacy account hint
           (never READY by itself — the legacy path below still waits for healthSettled). */
        const reason = error && REASONS[error.reason] ? error.reason : null;
        state.healthError = error && error.unsupported ? null
          : reason
            ? {state: REASONS[reason].state, reason, code: error.code ?? null,
               hint: error.hint || reasonText(reason, error.code), retryable: error.retryable !== false}
            : {state: "UNKNOWN", reason: "PROBE_TIMEOUT", code: null,
               hint: reasonText("PROBE_TIMEOUT"), retryable: true};
        render();
      });
    }

    /* UC-03: one GET per click, no polling, no auto-retry. PAPER 403 shows the shared
       errorText plus reason/code so the failure is attributable, not a generic error. */
    function runAudit() {
      const box = ensureDetails(bar, runAudit);
      const button = box.querySelector('[data-action="audit-account"]');
      const status = box.querySelector("[data-audit-status]");
      if (!button || button.disabled) return Promise.resolve();
      const current = scope();
      if (current.exchange === "hyperliquid") return Promise.resolve();
      const symbol = (typeof window !== "undefined" && window.BTCSymbols && window.BTCSymbols.current) || "BTCUSDT";
      button.disabled = true;
      status.dataset.state = "pending";
      status.textContent = "正在核对本机账本与交易所订单…";
      return audit(current, symbol).then(result => {
        const summary = (result && result.summary) || {};
        if (result && result.read_ok === false) {
          const failure = (result && result.read_error) || {};
          const reason = failure.reason && REASONS[failure.reason] ? failure.reason : "PROBE_TIMEOUT";
          status.dataset.state = REASONS[reason].state === "BLOCKED" ? "blocked" : "unknown";
          status.textContent = "核对未完成：" + reasonText(reason, failure.code)
            + "（reason=" + reason + " · code=" + (failure.code ?? "—") + "）";
        } else {
          const pending = Number(summary.pending || 0);
          const mismatch = pending + Number(summary.local_only || 0) + Number(summary.exchange_only || 0);
          status.dataset.state = mismatch > 0 ? "partial" : "ready";
          status.textContent = "核对完成：共 " + Number(summary.total || 0) + " 条 · 匹配 "
            + Number(summary.match || 0) + " · 仅本地 " + Number(summary.local_only || 0)
            + " · 仅交易所 " + Number(summary.exchange_only || 0) + " · 待核对 " + pending + "。";
        }
      }).catch(error => {
        status.dataset.state = "blocked";
        status.textContent = errorText(error) + "（reason=" + (error.reason || "—") + " · code="
          + (error.code == null ? "—" : error.code) + "）";
      }).finally(() => { button.disabled = false; });
    }

    function start(matrix) {
      if (matrix) state.matrix = matrix;
      render();
      void load(scope()).then(response => {
        state.matrix = response || state.matrix;
        render();
      }).catch(() => {});
      void recheck();
    }

    if (refreshButton) {
      refreshButton.addEventListener("click", () => { void recheck(); });
    }
    if (exchangeSelect) {
      exchangeSelect.addEventListener("change", () => {
        state.healthNodes = null;
        state.healthError = null;
        state.healthSettled = false;
        state.legacy = null;
        render();
        void recheck();
      });
    }

    return {
      start,
      render,
      scope,
      details() { return ensureDetails(bar, runAudit); },
      setSource(source) {
        if (!source || typeof source !== "object") return;
        if (typeof source.ready === "boolean") {
          state.legacy = {ready: source.ready, message: source.message || "", pending: Boolean(source.pending)};
          return;
        }
        if (source.state && source.reason) {
          const current = scope();
          state.healthNodes = {key: keyOf(current), entries: [{id: "manual_order", node: source}], checkedAt: source.checked_at || null, evidence: source.evidence === "config" ? "config" : "probe"};
          state.healthError = null;
          state.healthSettled = true;
          return;
        }
        if (source.exchange && source.market) {
          applyHealth(source);
          return;
        }
        if (source.capabilities) state.matrix = source;
      },
      setMatrix(matrix) {
        if (matrix && typeof matrix === "object") state.matrix = matrix;
      },
    };
  }

  function mount(target, matrix) {
    const bar = barOf(target);
    if (!bar) return null;
    let controller = controllers.get(bar);
    if (controller) {
      if (matrix) controller.setMatrix(matrix);
      return controller;
    }
    const host = (target && target.querySelector && target.querySelector(".live-connection-bar") ? target : null)
      || bar.closest(".live-workspace")
      || document;
    controller = createController(bar, host);
    controllers.set(bar, controller);
    controller.start(matrix);
    return controller;
  }

  function renderBar(target, source, matrix) {
    const controller = mount(target, matrix);
    if (!controller) return null;
    if (source) controller.setSource(source);
    if (matrix) controller.setMatrix(matrix);
    controller.render();
    return controller;
  }

  function mountDetails(target, matrix) {
    const controller = mount(target, matrix);
    if (!controller) return null;
    controller.render();
    return controller.details();
  }

  function autoMount() {
    for (const bar of document.querySelectorAll(".live-connection-bar")) {
      if (!controllers.has(bar)) mount(bar.closest(".live-workspace") || document);
    }
  }

  window.BTCCapabilities = {
    REASONS,
    CAPABILITIES,
    LABELS,
    STATES,
    stateLabel,
    reasonText,
    errorText,
    load,
    health,
    futures,
    audit,
    renderBar,
    mountDetails,
    mount,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", autoMount);
  else autoMount();
  if (typeof MutationObserver === "function") {
    new MutationObserver(autoMount).observe(document.documentElement, {childList: true, subtree: true});
  }
})();
