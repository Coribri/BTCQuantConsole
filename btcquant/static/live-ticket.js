/* Pure display/validation helpers. The exchange preview remains authoritative. */
(function (root) {
  "use strict";
  const precision = step => {
    const [mantissa, exponent = "0"] = String(step || 1).toLowerCase().split("e");
    return Math.min(12, Math.max(0, (mantissa.split(".")[1] || "").length - Number(exponent)));
  };
  const floor = (value, step) => {
    const unit = Number(step);
    if (!(unit > 0) || !Number.isFinite(value)) return 0;
    const scaled = value / unit;
    return Number((Math.floor(scaled + Number.EPSILON * Math.max(1, Math.abs(scaled)) * 2) * unit).toFixed(precision(step)));
  };
  const quantity = (value, step) => {
    const text = floor(value, step).toFixed(precision(step));
    return text.includes(".") ? text.replace(/0+$/, "").replace(/\.$/, "") : text;
  };
  function rulesFor(rules, kind) {
    if (!rules) return null;
    if (kind === "LIMIT") return {...rules, min_notional:rules.limit_min_notional ?? rules.min_notional,
      max_notional:rules.limit_max_notional};
    return {...rules, step:rules.market_step ?? rules.step,
      min_qty:rules.market_min_qty ?? rules.min_qty, max_qty:rules.market_max_qty ?? rules.max_qty,
      min_notional:rules.market_min_notional ?? rules.min_notional, max_notional:rules.market_max_notional};
  }
  function aligned(value, step) {
    if (!(Number(step) > 0)) return true;
    const units = value / Number(step);
    return Math.abs(units - Math.round(units)) <= Number.EPSILON * Math.max(1, Math.abs(units)) * 4;
  }
  function spot({amount, price, kind, side, rules, baseFree, quoteFree, cap}) {
    const value = Number(amount), rate = Number(price);
    if (!(value > 0) || !Number.isFinite(value)) return {error:"请输入大于 0 的金额或数量。"};
    if (!(rate > 0) || !Number.isFinite(rate)) return {error:kind === "LIMIT" ? "请输入有效的委托价格。" : "参考价格暂不可用，请刷新账户与交易规则。"};
    rules = rulesFor(rules, kind);
    if (!rules || !(Number(rules.step) > 0)) return {error:"交易规则暂不可用，请刷新后重试。"};
    const budget = side === "BUY" && kind === "MARKET";
    const quoteMarket = budget && rules.quote_market === true;
    if (budget && rules.quote_market === false) return {error:"该交易对不支持按金额市价买入。"};
    if (budget && !aligned(value, rules.quote_step)) return {error:"买入金额须按 " + rules.quote_step + " USDT 的步长填写。"};
    if (!budget && !aligned(value, rules.step)) return {error:"数量须按 " + rules.step + " 的步长填写。"};
    const tick = rules.price_filter;
    if (kind === "LIMIT" && tick && (!aligned(rate, tick.tickSize) ||
      (Number(tick.minPrice) > 0 && rate < Number(tick.minPrice)) ||
      (Number(tick.maxPrice) > 0 && rate > Number(tick.maxPrice)))) {
      return {error:"限价须符合交易所价格范围及 " + tick.tickSize + " USDT 步长。"};
    }
    const qty = budget ? floor(value / rate, rules.step) : value;
    const notional = qty * rate;
    // Binance accepts quoteOrderQty; its engine decides the actual base quantity.
    const checkQty = quoteMarket ? value / rate : qty;
    const checkNotional = quoteMarket ? value : notional;
    const result = {qty, notional, remainder:budget ? Math.max(0, value - notional) : 0};
    if (checkQty < Number(rules.min_qty || 0) || checkQty <= 0 || checkNotional + 1e-9 < Number(rules.min_notional || 0))
      return {...result, error:"低于最小订单要求（" + rules.min_notional + " USDT / " + rules.min_qty + "）。"};
    if ((Number(rules.max_qty) > 0 && checkQty > Number(rules.max_qty)) ||
      (Number(rules.max_notional) > 0 && checkNotional > Number(rules.max_notional) + 1e-9))
      return {...result, error:"超过交易所单笔数量或金额上限。"};
    if (side === "SELL" && baseFree != null && value > baseFree + 1e-12)
      return {...result, error:"卖出数量超过可用余额。"};
    if (side === "BUY" && quoteFree != null && (budget ? value : value * rate) > quoteFree + 1e-9)
      return {...result, error:"买入金额超过可用 USDT 余额。"};
    if (cap != null && (budget ? value : value * rate) > cap + 1e-9)
      return {...result, error:"超过本机单笔上限 " + cap + " USDT。"};
    return result;
  }
  const api = {precision, floor, quantity, rulesFor, spot};
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.BTCLiveTicket = api;
})(typeof window === "undefined" ? globalThis : window);
