/* Global symbol picker. Reloading the page lets each page initialise its own
   charts and per-symbol simulator state without leaking one market into another. */
(() => {
  "use strict";
  const LIST = [
    { symbol: "BTCUSDT", base: "BTC", quote: "USDT", name: "Bitcoin" },
    { symbol: "ETHUSDT", base: "ETH", quote: "USDT", name: "Ethereum" },
    { symbol: "BNBUSDT", base: "BNB", quote: "USDT", name: "BNB" },
    { symbol: "XRPUSDT", base: "XRP", quote: "USDT", name: "XRP" },
    { symbol: "TRXUSDT", base: "TRX", quote: "USDT", name: "TRON" },
    { symbol: "DOGEUSDT", base: "DOGE", quote: "USDT", name: "Dogecoin" },
  ];
  const bySymbol = Object.fromEntries(LIST.map((item) => [item.symbol, item]));
  const params = new URLSearchParams(window.location.search);
  const fromUrl = (params.get("symbol") || "").toUpperCase();
  let stored = "";
  try { stored = localStorage.getItem("btcquant.symbol") || ""; } catch (_) { /* ignore */ }
  const meta = bySymbol[fromUrl] || bySymbol[stored] || LIST[0];
  const current = meta.symbol;
  try { if(params.get('pane')!=='1')localStorage.setItem("btcquant.symbol", current); } catch (_) { /* ignore */ }

  window.BTCSymbols = {
    current,
    base: meta.base,
    quote: meta.quote,
    label: `${meta.base}/${meta.quote}`,
    list: LIST,
    meta,
    go,
  };

  function go(symbol) {
    const next = bySymbol[symbol];
    if (!next) return;
    try { if(params.get('pane')!=='1')localStorage.setItem("btcquant.symbol", next.symbol); } catch (_) { /* ignore */ }
    const url = new URL(window.location.href);
    url.searchParams.set("symbol", next.symbol);
    window.location.href = url.toString();
  }

  function init() {
    for (const link of document.querySelectorAll(".main-nav a, .brand-link")) {
      const url = new URL(link.href, location.href);
      if (url.origin !== location.origin) continue;
      url.searchParams.set("symbol", current);
      link.href = url.pathname + url.search + url.hash;
    }
    const label = document.getElementById("symbolLabel");
    const btn = document.getElementById("symbolBtn");
    const menu = document.getElementById("symbolMenu");
    if (!label || !btn || !menu) return;
    label.textContent = window.BTCSymbols.label;
    menu.querySelectorAll("[data-symbol]").forEach((item) => {
      const selected = item.dataset.symbol === current;
      item.classList.toggle("active", selected);
      item.addEventListener("click", (event) => {
        event.stopPropagation();
        go(item.dataset.symbol);
      });
    });
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      menu.classList.toggle("hidden");
    });
    menu.addEventListener("click", (event) => event.stopPropagation());
    document.addEventListener("click", () => menu.classList.add("hidden"));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") menu.classList.add("hidden");
    });
    document.title = `${window.BTCSymbols.label} · BTC Quant Console 1.0`;
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
