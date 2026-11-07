(() => {
  "use strict";
  if (document.documentElement.dataset.accountMode === "live") return;

  const $ = (id) => document.getElementById(id);
  const form = $("binanceApiForm");
  if (!form || document.documentElement.classList.contains("chart-window")) return;

  const saveButton = $("binanceApiSave");
  const testButton = $("binanceApiTest");
  let configured = false;
  let busy = false;

  async function api(path, data) {
    const res = await fetch(path, {
      cache: "no-store",
      ...(data === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "请求失败");
    return body;
  }

  function setStatus(text, error = false) {
    $("binanceApiStatus").textContent = text;
    $("binanceApiStatus").classList.toggle("error", error);
  }

  function setBusy(value) {
    busy = value;
    saveButton.disabled = busy;
    testButton.disabled = busy || !configured;
  }

  function setSettings(settings) {
    configured = Boolean(settings.configured);
    const badge = $("binanceApiBadge");
    badge.textContent = configured ? "已配置" : "未配置";
    badge.className = `mini-badge ${configured ? "green" : "neutral"}`;
    $("binanceApiKey").placeholder = settings.api_key_masked || "只读权限";
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const key = $("binanceApiKey").value.trim();
    const secret = $("binanceApiSecret").value.trim();
    if (!key || !secret) { setStatus("请输入 API Key 和 Secret", true); return; }
    setBusy(true);
    try {
      setSettings(await api("/api/exchange/binance/settings", { api_key: key, api_secret: secret }));
      form.reset();
      setStatus("连接已保存。点击“测试只读”检查余额读取。");
      window.dispatchEvent(new CustomEvent("btcquant:binance-changed"));
    } catch (err) {
      setStatus(err.message, true);
    } finally {
      setBusy(false);
    }
  });

  testButton.addEventListener("click", async () => {
    if (busy || !configured) return;
    setBusy(true);
    setStatus("正在读取 Binance 真实账户…");
    try {
      const snapshot = await api("/api/exchange/binance/test", {});
      if (!snapshot.configured) setSettings(snapshot);
      setStatus(snapshot.error || (snapshot.configured ? "读取成功。资产分布已更新。" : "请先保存只读连接。"), Boolean(snapshot.error));
      window.dispatchEvent(new CustomEvent("btcquant:binance-changed", { detail: snapshot }));
    } catch (err) {
      setStatus(err.message, true);
    } finally {
      setBusy(false);
    }
  });

  setBusy(true);
  api("/api/exchange/binance").then(setSettings).catch(err => {
    $("binanceApiBadge").textContent = "读取失败";
    setStatus(err.message, true);
  }).finally(() => setBusy(false));
})();
