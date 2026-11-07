/* Left market rail: all-symbol watchlist + compact flow / leverage / long-short. */
(() => {
  "use strict";
  if (document.documentElement.dataset.accountMode === "live") return;

  const $ = (id) => document.getElementById(id);
  const SYMBOL = window.BTCSymbols ? window.BTCSymbols.current : "BTCUSDT";
  let tab = "flow";
  let data = null;

  const fmtPrice = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    if (n >= 1000) return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
    if (n >= 1) return n.toLocaleString("en-US", { maximumFractionDigits: 4 });
    return n.toLocaleString("en-US", { maximumFractionDigits: 6 });
  };
  const fmtCompact = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
    if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
    if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
    return n.toFixed(2);
  };
  const pct = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
  };

  async function load() {
    try {
      const res = await fetch(`/api/market/overview?symbol=${encodeURIComponent(SYMBOL)}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
      renderWatch();
      renderPanel();
    } catch (err) {
      const watch = $("watchlist");
      if (watch) watch.innerHTML = `<div class="rail-empty">行情暂不可用</div>`;
      const panel = $("railPanel");
      if (panel) panel.innerHTML = `<div class="rail-empty">${err.message}</div>`;
    }
  }

  function renderWatch() {
    const el = $("watchlist");
    if (!el || !data) return;
    el.innerHTML = (data.watch || []).map((item) => {
      const active = item.symbol === SYMBOL;
      const cls = Number(item.change_pct) >= 0 ? "positive" : "negative";
      return `<button class="coin-row ${active ? "active" : ""}" data-coin="${item.symbol}">
        <span class="coin-base">${item.base}</span>
        <span class="coin-price">${fmtPrice(item.price)}</span>
        <span class="coin-chg ${cls}">${pct(item.change_pct)}</span>
        <span class="coin-meta">H ${fmtPrice(item.high)} · L ${fmtPrice(item.low)} · Vol ${fmtCompact(item.quote_volume)}</span>
      </button>`;
    }).join("");
    el.querySelectorAll("[data-coin]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (btn.dataset.coin === SYMBOL) return;
        window.BTCSymbols.go(btn.dataset.coin);
      });
    });
  }

  function renderPanel() {
    const el = $("railPanel");
    if (!el || !data) return;
    if (tab === "flow") {
      const f = data.flow || {};
      const buy = Number(f.buy) || 0, sell = Number(f.sell) || 0;
      const total = buy + sell || 1;
      const bars = (f.bars || []).slice(-12);
      const max = Math.max(1, ...bars.flatMap((b) => [b.buy, b.sell]));
      el.innerHTML = `
        <div class="rail-label">近 24h 主动成交（USDT）</div>
        <div class="rail-big ${f.net >= 0 ? "positive" : "negative"}">${f.net >= 0 ? "+" : ""}${fmtCompact(f.net)}</div>
        <div class="flow-bar"><span class="buy" style="width:${(buy / total) * 100}%"></span><span class="sell" style="width:${(sell / total) * 100}%"></span></div>
        <div class="flow-legend"><span>买入 ${fmtCompact(buy)}</span><span>卖出 ${fmtCompact(sell)}</span></div>
        <div class="flow-mini">${bars.map((b) => `<i class="buy" style="height:${Math.max(4, (b.buy / max) * 34)}px"></i><i class="sell" style="height:${Math.max(4, (b.sell / max) * 34)}px"></i>`).join("")}</div>
        <div class="rail-note">现货 15m K 线的主动买入/卖出估算，不是交易所资金流。</div>`;
    } else if (tab === "lev") {
      const l = data.leverage || {};
      const has = l.funding_rate !== null && l.funding_rate !== undefined;
      el.innerHTML = has ? `
        <div class="rail-label">资金费率（8h）</div>
        <div class="rail-big ${l.funding_rate >= 0 ? "negative" : "positive"}">${(l.funding_rate * 100).toFixed(4)}%</div>
        <div class="rail-kv"><span>标记价</span><b>${fmtPrice(l.mark_price)}</b></div>
        <div class="rail-kv"><span>未平仓量 OI</span><b>${fmtCompact(l.open_interest)}</b></div>
        <div class="rail-kv"><span>多空比</span><b>${l.long_short_ratio ? Number(l.long_short_ratio).toFixed(2) : "—"}</b></div>
        <div class="rail-note">Binance Futures 公共数据；仅作参考，不连接合约交易。</div>
      ` : `<div class="rail-empty">杠杆数据暂不可用<br><span class="rail-note">${l.error || "公共接口未返回"}</span></div>`;
    } else {
      const f = data.flow || {};
      const l = data.leverage || {};
      let longPct = l.long_pct, shortPct = l.short_pct;
      let proxy = false;
      if (!longPct || !shortPct) {
        const buy = Number(f.buy) || 0, sell = Number(f.sell) || 0;
        const total = buy + sell || 1;
        longPct = buy / total; shortPct = sell / total; proxy = true;
      }
      el.innerHTML = `
        <div class="rail-label">${proxy ? "主动买卖占比（现货代理）" : "账户多空比"}</div>
        <div class="rail-big">${l.long_short_ratio ? Number(l.long_short_ratio).toFixed(2) : (longPct / (shortPct || 1)).toFixed(2)}</div>
        <div class="rail-meter"><span class="long" style="width:${longPct * 100}%"></span><span class="short" style="width:${shortPct * 100}%"></span></div>
        <div class="rail-kv"><span>多</span><b>${(longPct * 100).toFixed(1)}%</b></div>
        <div class="rail-kv"><span>空</span><b>${(shortPct * 100).toFixed(1)}%</b></div>
        <div class="rail-note">${proxy ? "未取得合约多空比，暂用现货主动成交估算。" : "Binance Futures 全球账户多空比。"}</div>`;
    }
  }

  function init() {
    const tabs = $("railTabs");
    if (!tabs) return;
    tabs.querySelectorAll("[data-tab]").forEach((btn) => {
      btn.addEventListener("click", () => {
        tab = btn.dataset.tab;
        tabs.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b === btn));
        renderPanel();
      });
    });
    load();
    setInterval(load, 6000);
  }

  if (!document.documentElement.classList.contains("chart-window")) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
  }
})();
