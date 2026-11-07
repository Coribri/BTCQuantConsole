const {test} = require('node:test');
const assert = require('node:assert/strict');
const {existsSync} = require('node:fs');
const {join} = require('node:path');
const staticDir = existsSync(join(__dirname,'../_internal/btcquant/static'))
  ? join(__dirname,'../_internal/btcquant/static') : join(__dirname,'../btcquant/static');
const ticket = require(join(staticDir,'live-ticket.js'));
const rules = {step:'0.00001000',min_qty:'0.00001000',min_notional:'5',price:76000};
const order = {amount:10,price:76000,kind:'MARKET',side:'BUY',rules,quoteFree:15,baseFree:0,cap:10};

test('integer step sell amount keeps significant trailing zeros', () => {
  assert.equal(ticket.quantity(100,'1'),'100');
  assert.equal(ticket.quantity(120,'10'),'120');
  assert.equal(ticket.quantity(25,'1'),'25');
});
test('percentage quantities round down, including scientific steps and dust', () => {
  assert.equal(ticket.quantity(.00013999,'0.00001000'),'.00013'.replace(/^\./,'0.'));
  assert.equal(ticket.quantity(.000001234,'1e-8'),'0.00000123');
  assert.equal(ticket.quantity(.00000999,'0.00001'),'0');
  assert.equal(ticket.quantity(1.000000000001,'1'),'1');
});
test('budget preview reports rounded quantity, spend and change', () => {
  const result = ticket.spot(order);
  assert.equal(result.error,undefined);
  assert.equal(result.qty,.00013);
  assert.ok(Math.abs(result.notional-9.88)<1e-9);
  assert.ok(Math.abs(result.remainder-.12)<1e-9);
});
test('Binance quote budget is checked before estimating rounded base quantity', () => {
  const binance = {...order, rules:{...rules, quote_market:true, quote_step:'0.00000001'}};
  const result = ticket.spot({...binance,amount:5});
  assert.equal(result.error,undefined);
  assert.ok(result.notional < 5);
  assert.match(ticket.spot({...binance,amount:4.99}).error,/最小订单/);
});
test('over-cap, insufficient balances and invalid prices are rejected', () => {
  assert.match(ticket.spot({...order,amount:10.01}).error,/单笔上限/);
  assert.match(ticket.spot({...order,quoteFree:9}).error,/可用 USDT/);
  assert.match(ticket.spot({...order,side:'SELL',amount:.00013,baseFree:.00012}).error,/可用余额/);
  assert.match(ticket.spot({...order,kind:'LIMIT',amount:.00013,price:0}).error,/委托价格/);
  assert.match(ticket.spot({...order,rules:null}).error,/交易规则/);
});
test('limit estimate uses entered price and applies exchange step', () => {
  assert.match(ticket.spot({...order,kind:'LIMIT',amount:.000139,price:75000,cap:20}).error,/步长/);
  const result = ticket.spot({...order,kind:'LIMIT',amount:.00013,price:75000,cap:20});
  assert.equal(result.error,undefined);
  assert.equal(result.qty,.00013);
  assert.equal(result.notional,9.75);
});
test('a market quantity uses both lot filters while limit price uses its tick', () => {
  const spec = {...rules, market_step:'0.00002', market_min_notional:'0', limit_min_notional:'5',
    price_filter:{minPrice:'0.01', maxPrice:'1000000', tickSize:'0.01'}};
  assert.match(ticket.spot({...order,side:'SELL',amount:.00013,baseFree:1,rules:spec}).error,/步长/);
  assert.equal(ticket.spot({...order,side:'SELL',amount:.00002,baseFree:1,rules:spec}).error,undefined);
  assert.match(ticket.spot({...order,kind:'LIMIT',amount:.00013,price:'75000.001',rules:spec}).error,/限价/);
});
