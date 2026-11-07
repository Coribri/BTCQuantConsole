(() => {
  "use strict";
  if (document.documentElement.dataset.accountMode === "live") return;

  const $ = (id) => document.getElementById(id);
  const card = $("portfolioCard");
  if (!card || document.documentElement.classList.contains("chart-window")) return;
  const futures = card.dataset.market === "futures";

  const COLORS = ["#36caaa", "#a597ff", "#f0b90b", "#3d9cf0", "#ef6a6a", "#d58cff", "#7f8fa4"];
  const nf = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
  const qf = new Intl.NumberFormat("en-US", { maximumFractionDigits: 6 });
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

  function renderAllocation(rows, emptyText) {
    const total = rows.reduce((sum, row) => sum + row.value, 0);
    const share = row => total > 0 ? row.value / total * 100 : 0;
    if (!rows.length) {
      $("portfolioPie").style.background = "var(--panel-2)";
      $("portfolioLegend").textContent = emptyText;
      return total;
    }
    let cursor = 0;
    const segments = rows.map((r, i) => {
      const start = cursor;
      cursor += share(r);
      return `${COLORS[i % COLORS.length]} ${start.toFixed(2)}% ${cursor.toFixed(2)}%`;
    });
    $("portfolioPie").style.background = total > 0 ? `conic-gradient(${segments.join(",")})` : "var(--panel-2)";
    $("portfolioLegend").innerHTML = rows.map((r, i) => {
      const quantity = r.qty == null ? "" : `${qf.format(r.qty)} · `;
      return `<div class="portfolio-legend-row"><i style="background:${COLORS[i % COLORS.length]}"></i><span>${esc(r.asset)}</span><b>${nf.format(r.value)}</b><small>${quantity}${share(r).toFixed(1)}%</small></div>`;
    }).join("");
    return total;
  }

  if (futures) {
    window.addEventListener("btcquant:futures", ({ detail }) => {
      const account = detail?.account;
      if (!account) return;
      const rows = [
        { asset: "可用保证金", value: Number(account.available) },
        { asset: "已占用保证金", value: Number(account.margin) },
      ].filter(row => Number.isFinite(row.value) && row.value >= 0);
      renderAllocation(rows, "暂无保证金数据");
    });
    return;
  }

  function render(rows, note) {
    const pricedRows = rows.filter(row => row.qty > 0 && Number.isFinite(row.value) && row.value > 0);
    renderAllocation(pricedRows, "没有可显示的现货余额。");
    $("portfolioNote").textContent = note || "按公开报价估算";
  }

  function renderPaper(s) {
    if (!s) return;
    const a = s.account || {};
    const base = window.BTCSymbols?.base || (s.symbol || "BTCUSDT").replace("USDT", "");
    const price = Number(s.price) || 0;
    const qty = Number(a.btc_total) || 0;
    const cash = (Number(a.usdt_free) || 0) + (Number(a.usdt_locked) || 0);
    const rows = [
      { asset: "USDT", qty: cash, value: cash },
      { asset: base, qty, value: qty * price },
    ].filter((r) => r.qty > 0);
    const source = s.mode === "testnet" ? "TESTNET" : "PAPER";
    render(rows, `${source} · 当前币种与 USDT · 按公开报价估算`);
  }

  window.addEventListener("btcquant:spot", ({ detail }) => renderPaper(detail));
})();
