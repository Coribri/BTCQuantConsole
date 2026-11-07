const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync,existsSync,readdirSync} = require('node:fs');
const {execFileSync} = require('node:child_process');
const {join} = require('node:path');
const {JSDOM, VirtualConsole} = require('./ui/node_modules/jsdom');
const staticDir = (existsSync(join(__dirname,'../_internal/btcquant/static'))
  ? join(__dirname,'../_internal/btcquant/static') : join(__dirname,'../btcquant/static')) + '/';
async function setup(page='index', {symbol='BTCUSDT', fail=false, respond, pending, fastTimeout=false, venue=null, legacy=false, settleMs=50}={}) {
  const errors=[], calls=[];
  const virtualConsole=new VirtualConsole();
  virtualConsole.on('jsdomError',error=>errors.push(error));
  const query=new URLSearchParams({account:'live',symbol});
  if (venue) query.set('venue',venue);
  const dom = new JSDOM(readFileSync(staticDir+page+'.html','utf8'), {
    url:'http://localhost/'+(page==='index'?'':page)+'?'+query, runScripts:'outside-only', virtualConsole,
  });
  const w=dom.window;
  w.document.documentElement.dataset.accountMode='live';
  w.BTCSymbols={current:symbol,label:symbol.replace('USDT','/USDT')};
  w.setInterval=()=>0;
  if (fastTimeout) {
    const schedule=w.setTimeout.bind(w);
    w.setTimeout=(fn, delay, ...args)=>schedule(fn, delay===45000 ? 30 : delay, ...args);
  }
  if (pending) w.localStorage.setItem(pending, 'pending');
  const risk={test_mode:true,max_spot_notional:10,max_futures_margin:10,max_grid_budget:50,max_leverage:10};
  let accountFail=fail;
  w.fetch=async (url,options={})=>{
    const path=new URL(url,'http://localhost').pathname;
    const body=options.body ? JSON.parse(options.body) : null;
    calls.push([path,options.method||'GET',body]);
    const overridden=await respond?.(path,body,options);
    if (overridden !== undefined) {
      const envelope=overridden && typeof overridden==='object' && typeof overridden.status==='number'
        && 'body' in overridden && Object.keys(overridden).every(key=>key==='status'||key==='body');
      if (envelope) {
        return {ok:overridden.status>=200&&overridden.status<300,status:overridden.status,json:async()=>overridden.body};
      }
      return {ok:true,status:200,json:async()=>overridden};
    }
    let result={};
    if(path==='/api/live/features') result={manual:true,grid:true,auto:['binance']};
    if(path==='/api/live/manual-risk') result=risk;
    if(path.startsWith('/api/exchange/')) result={configured:true};
    if(path==='/api/live/account') result=accountFail ? {error:'-2015 Invalid API-key'} : {configured:true,trade_permission:'confirmed',balances:[{asset:'USDT',free:'15',total:'15'},{asset:symbol.slice(0,-4),free:symbol==='DOGEUSDT'?'100':'0.00013',total:'0.00013'}]};
    if(path==='/api/live/rules') result=symbol==='DOGEUSDT' ? {step:'1',min_qty:'1',min_notional:'5',price:'.08'} : {step:'.00001000',min_qty:'.00001000',min_notional:'5',price:'76000'};
    if(path==='/api/live/strategy') result={plan:null};
    if(path==='/api/live/orders') result={orders:[{id:'test',symbol,side:'BUY',status:'FILLED',type:'MARKET',filled:'.00013',quote_filled:'9.88',created_at:'2026-11-06T10:00:00Z'}]};
    if(path==='/api/models') result={models:[]};
    if(path==='/api/live/account/audit') result={read_ok:true,summary:{total:0,match:0,local_only:0,exchange_only:0,pending:0},diffs:[]};
    if(path==='/api/market/overview') result={watch:[],flow:{},leverage:{}};
    if(path==='/api/market/depth') result={asks:[],bids:[]};
    return {ok:true,status:200,json:async()=>result};
  };
  if (!legacy) w.eval(readFileSync(staticDir+'capabilities.js','utf8'));
  w.eval(readFileSync(staticDir+'live-ticket.js','utf8'));
  w.eval(readFileSync(staticDir+'live-chart-resize.js','utf8'));
  // Make swallowed initialization failures visible in this isolated DOM test.
  w.eval(readFileSync(staticDir+'ui.js','utf8').replaceAll('.catch(() => {});','.catch(error => console.error(error.stack));'));
  virtualConsole.on('error',error=>errors.push(error));
  if (settleMs) await new Promise(resolve=>setTimeout(resolve,settleMs));
  return {w,errors,calls,close:()=>dom.window.close(), fail:()=>{accountFail=true;}};
}
test('spot mounts, updates budget while typing and clears amount on order-type switch',async()=>{
  const app=await setup();
  try {
    assert.deepEqual(app.errors,[]);
    const d=app.w.document, form=d.querySelector('.live-trade form');
    assert.ok(d.querySelector('.live-strategy'),'AUTO card mounted');
    form.elements.amount.value='10';form.elements.amount.dispatchEvent(new app.w.Event('input'));
    assert.match(d.querySelector('[data-live-estimate]').textContent,/9.88/);
    assert.equal(form.querySelector('[type=submit]').disabled,false);
    form.elements.amount.value='11';form.elements.amount.dispatchEvent(new app.w.Event('input'));
    assert.equal(form.querySelector('[type=submit]').disabled,true);
    form.elements.kind.value='LIMIT';form.elements.kind.dispatchEvent(new app.w.Event('change',{bubbles:true}));
    assert.equal(form.elements.amount.value,'');
    assert.equal(app.calls.some(([path,method])=>method!=='GET'),false);
  } finally {app.close();}
});
test('100% integer-step sell fills 100, not 1; failure clears stale balances',async()=>{
  const app=await setup('index',{symbol:'DOGEUSDT'});
  try {
    assert.deepEqual(app.errors,[]);
    const d=app.w.document,form=d.querySelector('.live-trade form');
    form.querySelector('[data-direction=SELL]').click();
    await new Promise(resolve=>setTimeout(resolve,20));
    form.elements.percent.value='100';form.elements.percent.dispatchEvent(new app.w.Event('input'));
    assert.equal(form.elements.amount.value,'100');
    assert.equal(form.querySelector('[type=submit]').disabled,false);
    app.fail();d.querySelector('[data-refresh-account]').click();
    await new Promise(resolve=>setTimeout(resolve,20));
    assert.match(d.querySelector('[data-live-available]').textContent,/等待账户读取/);
    assert.equal(form.querySelector('[type=submit]').disabled,true);
    assert.equal(d.querySelector('#liveAssetPie').hidden,true);
  } finally {app.close();}
});
test('local order filters retain historical records and show empty open state',async()=>{
  const app=await setup();
  try {
    assert.deepEqual(app.errors,[]);
    const d=app.w.document;
    d.querySelector('[data-filter=open]').click();
    assert.match(d.querySelector('.live-orders .live-order-list').textContent,/暂无/);
    d.querySelector('[data-filter=history]').click();
    assert.match(d.querySelector('.live-orders .live-order-list').textContent,/已成交/);
  } finally {app.close();}
});
test('grid has budget units, invalid-range feedback and spacing estimate',async()=>{
  const app=await setup('grid');
  try {
    assert.deepEqual(app.errors,[]);
    const d=app.w.document,form=d.querySelector('.live-strategy form');
    form.elements.lower.value='80000';form.elements.upper.value='70000';
    form.dispatchEvent(new app.w.Event('input'));
    assert.match(d.querySelector('.live-grid-summary').textContent,/上限必须高于下限/);
    form.elements.upper.value='90000';form.dispatchEvent(new app.w.Event('input'));
    assert.match(d.querySelector('.live-grid-summary').textContent,/1666.6667/);
    assert.match(d.querySelector('#liveRiskSummary').textContent,/网格预算 50 USDT/);
  } finally {app.close();}
});
test('live chart drag resizes outer iframe, clamps, persists and resets',async()=>{
  const app=await setup();
  try {
    assert.deepEqual(app.errors,[]);
    const d=app.w.document,frame=d.querySelector('.live-chart iframe'),handle=d.querySelector('.live-chart-resizer');
    frame.getBoundingClientRect=()=>({height:parseFloat(frame.style.height)||500});
    handle.setPointerCapture=()=>{};
    handle.hasPointerCapture=()=>false;
    const pointer=(name,y)=>{const e=new app.w.Event(name);Object.assign(e,{button:0,pointerId:1,clientY:y});handle.dispatchEvent(e);};
    pointer('pointerdown',500);pointer('pointermove',680);pointer('pointerup',680);
    assert.equal(frame.style.height,'680px');
    assert.equal(frame.style.pointerEvents,'');
    assert.equal(app.w.localStorage.getItem('btcquant.liveChartHeight.v1./'),'680');
    handle.dispatchEvent(new app.w.KeyboardEvent('keydown',{key:'ArrowDown'}));
    assert.equal(frame.style.height,'704px');
    pointer('pointerdown',500);pointer('pointermove',-500);pointer('pointercancel',-500);
    assert.equal(frame.style.height,'360px');
    handle.dispatchEvent(new app.w.Event('dblclick'));
    assert.equal(frame.style.height,'');
    assert.equal(app.w.localStorage.getItem('btcquant.liveChartHeight.v1./'),null);
  } finally {app.close();}
});

const token='bq'+'1'.repeat(32);
const receiptKey='btcquant.live-receipt.v1:binance:spot:BTCUSDT:'+token;
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function enterBuy(app) {
  const form=app.w.document.querySelector('.live-trade form');
  form.elements.amount.value='10';
  form.elements.amount.dispatchEvent(new app.w.Event('input'));
  return form;
}
function submit(app, form) {
  form.dispatchEvent(new app.w.Event('submit',{cancelable:true,bubbles:true}));
}

test('preview and confirmation freeze fields and double activation only submits once',async()=>{
  let respondPreview;
  const app=await setup('index',{respond:(path,body)=>{
    if(path==='/api/live/preview') return new Promise(resolve=>{
      respondPreview=()=>resolve({confirmation:token,expires_in:60,order:{...body,id:token}});
    });
    if(path==='/api/live/order') return {id:token,quote:'10',status:'PARTIALLY_FILLED',filled:'0.00005',quote_filled:'3.8'};
  }});
  try {
    const d=app.w.document,form=enterBuy(app);
    submit(app,form); submit(app,form);
    assert.equal(form.elements.amount.disabled,true);
    assert.equal(form.elements.kind.disabled,true);
    respondPreview();
    await tick();
    assert.match(d.querySelector('#btcModalBody').textContent,/10 USDT/);
    d.querySelector('#btcModalOk').click();d.querySelector('#btcModalOk').click();
    await tick();
    assert.deepEqual(app.calls.filter(([path])=>path==='/api/live/order'),[['/api/live/order','POST',{confirmation:token}]]);
    assert.equal(app.calls.filter(([path])=>path==='/api/live/preview').length,1);
    assert.equal(form.elements.amount.disabled,false);
    assert.equal(app.w.localStorage.getItem(receiptKey),null);
    assert.match(d.querySelector('[data-order-feedback]').textContent,/部分成交.*3\.8/);
  } finally {app.close();}
});

test('a browser timeout keeps the receipt across page loads; recovery never re-sends the order',async()=>{
  const app=await setup('index',{fastTimeout:true,respond:(path,body,options)=>{
    if(path==='/api/live/preview') return {confirmation:token,expires_in:60,order:{...body,id:token}};
    if(path==='/api/live/order') return new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(Error('aborted'))));
  }});
  try {
    const form=enterBuy(app), d=app.w.document;
    submit(app,form); await tick();
    d.querySelector('#btcModalOk').click();
    await new Promise(resolve=>setTimeout(resolve,60));
    assert.equal(app.w.localStorage.getItem(receiptKey),'pending');
    assert.equal(d.querySelector('.live-receipt').hidden,false);
    assert.equal(form.querySelector('[type=submit]').disabled,true);
    assert.match(d.querySelector('[data-order-feedback]').textContent,/等待超时/);
  } finally {app.close();}
  const reopened=await setup('index',{pending:receiptKey,respond:path=>{
    if(path==='/api/live/order/recover') return {state:'not_submitted'};
  }});
  try {
    const d=reopened.w.document,form=enterBuy(reopened);
    assert.equal(form.querySelector('[type=submit]').disabled,true);
    d.querySelector('[data-recover-order]').click(); await tick();
    assert.equal(form.querySelector('[type=submit]').disabled,false);
    assert.equal(reopened.w.localStorage.getItem(receiptKey),null);
    assert.deepEqual(reopened.calls.filter(([,method])=>method==='POST'),[['/api/live/order/recover','POST',{confirmation:token}]]);
  } finally {reopened.close();}
});

/* 1.0 T-UI-01..05: capability four states, fixed copy, probe count and error envelope (api_spec §1-2). */
const HINTS = {
  OK:'已探测可用。',
  AUTH_REJECTED:'Binance 拒绝认证（-2015）。请依次核对：① API Key 是否有效且未被删除；② 当前出口 IP 是否在 Key 的白名单内；③ Key 是否具备所需读取/交易权限。',
  PENDING_RECONCILE:'存在待核对（-2013 或回执未确认）的订单，相关交易对禁止开新单。请按原确认号核对，系统不会重复提交，也不会据此清账。',
  UNVERIFIED:'尚未核验该能力或交易权限，状态未知。点击“重新检测”获取实时结果。',
};
const TITLES = {
  READY:'账户已连接 · 可预览订单',
  PARTIAL:'账户可读取 · 部分能力受限',
  BLOCKED:'交易未就绪 · 下单锁定',
  UNKNOWN:'状态未核验 · 下单锁定',
};
const BADGES = {
  READY:['交易就绪','mini-badge green','ready'],
  PARTIAL:['部分可用','mini-badge neutral','partial'],
  BLOCKED:['不可用','mini-badge neutral danger','blocked'],
  UNKNOWN:['未核验','mini-badge neutral','unknown'],
};
function nodeFor(state) {
  if (state==='READY') return {state:'READY',reason:'OK',hint:HINTS.OK,retryable:false,support:'supported',evidence:'probe',code:null,checked_at:'2026-11-07T09:58:10Z'};
  if (state==='PARTIAL') return {state:'PARTIAL',reason:'PENDING_RECONCILE',hint:HINTS.PENDING_RECONCILE,retryable:true,support:'supported',evidence:'probe',code:-2013,checked_at:'2026-11-07T09:58:10Z',blocked_symbols:['BTCUSDT']};
  if (state==='BLOCKED') return {state:'BLOCKED',reason:'AUTH_REJECTED',hint:HINTS.AUTH_REJECTED,retryable:true,support:'supported',evidence:'probe',code:-2015,checked_at:'2026-11-07T09:58:10Z',blocked_symbols:[]};
  return {state:'UNKNOWN',reason:'UNVERIFIED',hint:HINTS.UNVERIFIED,retryable:true,support:'supported',evidence:'config',code:null,checked_at:null};
}
function capabilityMatrix(state) {
  const group = market => {
    const nodes={quote:'USDT'};
    for (const id of ['market_data','account_read','manual_order','auto_strategy','grid']) {
      nodes[id]=market==='futures'&&id==='grid'
        ? {state:'BLOCKED',reason:'NOT_IMPLEMENTED',hint:'当前版本不支持该组合。如需接入请单独立项。',retryable:false,support:'unsupported',evidence:'config',code:null,checked_at:null}
        : nodeFor(state);
    }
    return nodes;
  };
  const spot=group('spot'), futures=group('futures');
  return {schema_version:1,generated_at:'2026-11-07T09:58:12Z',env:'live',refresh:false,
    capabilities:{binance:{spot,futures},coinw:{spot,futures},hyperliquid:{futures:group('futures')}}};
}
async function expectState(page, state, venue) {
  const app=await setup(page,{venue,respond:path=>path==='/api/capabilities'?capabilityMatrix(state):undefined});
  try {
    const d=app.w.document;
    assert.deepEqual(app.errors,[]);
    assert.equal(d.querySelector('[data-connection-title]').textContent,TITLES[state],page+' title');
    const badge=d.querySelector('#liveBadge');
    assert.equal(badge.textContent,BADGES[state][0],page+' badge text');
    assert.equal(badge.className,BADGES[state][1],page+' badge class');
    assert.equal(badge.dataset.state,BADGES[state][2],page+' badge dataset');
    assert.equal(d.querySelector('.live-connection-bar').dataset.state,BADGES[state][2],page+' bar state');
    assert.equal(d.querySelectorAll('.live-capability-details').length,1,page+' details');
  } finally {app.close();}
}
test('[UI-01] index 四态',async()=>{
  for (const state of ['READY','PARTIAL','BLOCKED','UNKNOWN']) await expectState('index',state);
});
test('[UI-01] futures 四态',async()=>{
  for (const state of ['READY','PARTIAL','BLOCKED','UNKNOWN']) await expectState('futures',state);
});
test('[UI-01] grid 四态',async()=>{
  for (const state of ['READY','PARTIAL','BLOCKED','UNKNOWN']) await expectState('grid',state);
});
test('[UI-02] 连接不等于可交易行与页面归属',async()=>{
  for (const page of ['index','futures','grid']) {
    const app=await setup(page,{respond:path=>path==='/api/capabilities'?capabilityMatrix('READY'):undefined});
    try {
      const text=app.w.document.querySelector('[data-connection-message]').textContent;
      assert.ok(text.startsWith('连接不等于可交易。'),page+' prefix: '+text);
      for (const prefix of ['行情：','账户：','手动交易：','AUTO：','网格：']) assert.ok(text.includes(prefix),page+' '+prefix);
      const html=readFileSync(staticDir+page+'.html','utf8');
      assert.equal((html.match(/capabilities\.js/g)||[]).length,1,page+' script count');
      assert.ok(html.indexOf('capabilities.js')<html.indexOf('ui.js'),page+' script order');
    } finally {app.close();}
  }
  assert.deepEqual(readdirSync(staticDir).filter(name=>name.endsWith('.html')).sort(),
    ['futures.html','grid.html','index.html','models.html','research.html']);
  for (const page of ['research','models']) {
    assert.ok(!readFileSync(staticDir+page+'.html','utf8').includes('capabilities.js'),page+' no capabilities');
  }
});
test('[UI-03] 探测调用次数',async()=>{
  const app=await setup('index',{respond:path=>path==='/api/capabilities'?capabilityMatrix('READY'):undefined});
  try {
    const healthCalls=()=>app.calls.filter(([path])=>path==='/api/exchange/health').length;
    assert.equal(healthCalls(),1,'entry probes once');
    app.w.document.querySelector('[data-refresh-account]').click();
    await new Promise(resolve=>setTimeout(resolve,40));
    assert.equal(healthCalls(),2,'manual recheck probes once');
    const select=app.w.document.querySelector('#liveExchange');
    select.value='coinw';
    select.dispatchEvent(new app.w.Event('change',{bubbles:true}));
    await new Promise(resolve=>setTimeout(resolve,40));
    assert.equal(healthCalls(),3,'exchange switch probes once');
    await new Promise(resolve=>setTimeout(resolve,300));
    assert.equal(healthCalls(),3,'no polling');
  } finally {app.close();}
});
test('[UI-04] 409 有 code 显示 hint',async()=>{
  const hint=HINTS.AUTH_REJECTED;
  const app=await setup('index',{respond:path=>{
    if(path==='/api/capabilities') return capabilityMatrix('READY');
    if(path==='/api/live/preview') return {status:409,body:{detail:'订单被拒绝',code:-2015,reason:'AUTH_REJECTED',hint,retryable:true}};
    return undefined;
  }});
  try {
    const d=app.w.document,form=d.querySelector('.live-trade form');
    form.elements.amount.value='10';
    form.elements.amount.dispatchEvent(new app.w.Event('input'));
    form.dispatchEvent(new app.w.Event('submit',{cancelable:true,bubbles:true}));
    await new Promise(resolve=>setTimeout(resolve,40));
    const feedback=d.querySelector('[data-order-feedback]');
    assert.ok(feedback,'feedback node');
    assert.ok(feedback.textContent.includes(hint),'feedback shows hint: '+feedback.textContent);
  } finally {app.close();}
});
test('[UI-04] 403 无 code 显示 detail',async()=>{
  const detail='PAPER 模式已禁用实盘接口';
  const app=await setup('index',{respond:path=>{
    if(path==='/api/capabilities') return capabilityMatrix('READY');
    if(path==='/api/live/account') return {status:403,body:{detail}};
    return undefined;
  }});
  try {
    assert.ok(app.w.document.querySelector('#liveStatus').textContent.includes(detail),'status shows detail');
  } finally {app.close();}
  const source=readFileSync(staticDir+'ui.js','utf8');
  assert.ok(!/\/-2015\//.test(source),'no -2015 regex');
  assert.ok(!/\/-2013\//.test(source),'no -2013 regex');
  assert.ok(!/\/6003\//.test(source),'no 6003 regex');
  assert.ok(source.includes('error.code === -2015'),'refresh keyed on structured code');
});
test('[UI-05] capabilities 模块与无新页面',async()=>{
  const app=await setup('index',{respond:path=>path==='/api/capabilities'?capabilityMatrix('READY'):undefined});
  try {
    const caps=app.w.BTCCapabilities;
    assert.equal(typeof caps,'object');
    assert.equal(typeof caps.REASONS,'object');
    for (const name of ['stateLabel','reasonText','errorText','load','health','renderBar','mountDetails']) {
      assert.equal(typeof caps[name],'function',name);
    }
    assert.equal(app.w.document.querySelectorAll('.live-connection-bar .live-capability-details').length,1);
    assert.equal(app.w.document.querySelectorAll('.live-capability-details').length,1,'details not a page');
    for (const file of ['capabilities.js','ui.js']) {
      execFileSync(process.execPath,['--check',join(staticDir,file)]);
    }
  } finally {app.close();}
  for (const page of ['futures','grid']) {
    const other=await setup(page,{respond:path=>path==='/api/capabilities'?capabilityMatrix('READY'):undefined});
    try {assert.equal(other.w.document.querySelectorAll('.live-capability-details').length,1,page);}
    finally {other.close();}
  }
  const dex=await setup('futures',{venue:'hyperliquid'});
  try {assert.equal(dex.w.document.querySelector('.live-connection-bar'),null,'hyperliquid has no bar');}
  finally {dex.close();}
});

/* 1.0 S1 / M1 / M2: no READY before probes, read-only audit entry, UNVERIFIED branch. */
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const PROBE_TIMEOUT_TEXT='读取失败或超时，结果未知。请检查网络后手动重新检测；已提交的订单请继续按确认号核对，不要重复提交。';
const PAPER_HINT='当前为 PAPER 模式，实盘接口按设计禁用。切换到实盘模式后重新检测。';
function healthFor(state) {
  const capabilities={};
  for (const id of ['market_data','account_read','manual_order','auto_strategy','grid']) capabilities[id]=nodeFor(state);
  return {schema_version:1,env:'live',checked_at:'2026-11-07T09:58:10Z',capabilities};
}
function badgeOf(app) {
  const d=app.w.document;
  return {
    state:d.querySelector('#liveBadge').dataset.state,
    text:d.querySelector('#liveBadge').textContent,
    title:d.querySelector('[data-connection-title]').textContent,
  };
}
test('[UI-06] 首屏不得先 READY',async()=>{
  const delay=300;
  const app=await setup('index',{settleMs:0,respond:path=>{
    if(path==='/api/capabilities') return new Promise(resolve=>setTimeout(()=>resolve(capabilityMatrix('BLOCKED')),delay));
    if(path==='/api/exchange/health') return new Promise(resolve=>setTimeout(()=>resolve(healthFor('BLOCKED')),delay));
    return undefined;
  }});
  try {
    const d=app.w.document;
    await sleep(40);
    const early=badgeOf(app);
    assert.notEqual(early.state,'ready','t≈40ms must not be ready: '+JSON.stringify(early));
    assert.ok(!/交易就绪|账户已连接 · 可预览订单/.test(early.title),'t≈40ms title: '+early.title);
    await sleep(300);
    const mid=badgeOf(app);
    assert.notEqual(mid.state,'ready','t≈340ms must not be ready: '+JSON.stringify(mid));
    assert.ok(!/交易就绪|账户已连接 · 可预览订单/.test(mid.title),'t≈340ms title: '+mid.title);
    await sleep(150);
    const after=badgeOf(app);
    assert.equal(after.state,'blocked','after probes: '+JSON.stringify(after));
    assert.equal(after.title,TITLES.BLOCKED);
    assert.equal(after.text,BADGES.BLOCKED[0]);
    assert.deepEqual(app.errors,[]);
    assert.ok(d.querySelector('.live-connection-bar').dataset.state==='blocked');
  } finally {app.close();}
  // Literal analyst fixture: both endpoints return a bare `{state,reason}` node after 300ms.
  const literal=await setup('index',{settleMs:0,respond:path=>{
    if(path==='/api/capabilities'||path==='/api/exchange/health') {
      return new Promise(resolve=>setTimeout(()=>resolve({state:'BLOCKED',reason:'PAPER_MODE'}),delay));
    }
    return undefined;
  }});
  try {
    await sleep(40);
    assert.notEqual(badgeOf(literal).state,'ready','literal t≈40ms must not be ready');
    await sleep(320);
    assert.equal(badgeOf(literal).state,'blocked','literal after landing');
    assert.equal(badgeOf(literal).title,TITLES.BLOCKED);
  } finally {literal.close();}
});
test('[UI-06] legacy 降级不得先 READY',async()=>{
  // The capability module is missing: a single account read cannot verify four states, so only
  // UNKNOWN (or a definitive BLOCKED) is allowed — never READY (requirements §13.1).
  const app=await setup('index',{legacy:true,settleMs:0,respond:path=>{
    if(path==='/api/live/account') return new Promise(resolve=>setTimeout(
      ()=>resolve({configured:true,trade_permission:'confirmed',balances:[{asset:'USDT',free:'15',total:'15'}]}),500));
    return undefined;
  }});
  try {
    await sleep(40);
    assert.equal(badgeOf(app).state,'unknown','t≈40ms legacy');
    await sleep(300);
    assert.notEqual(badgeOf(app).state,'ready','t≈340ms legacy');
    await sleep(300);
    assert.notEqual(badgeOf(app).state,'ready','legacy never READY without capability probe');
    assert.equal(badgeOf(app).text,'未核验');
    assert.deepEqual(app.errors,[]);
  } finally {app.close();}
});
test('[UI-07] 核对账户入口只读',async()=>{
  for (const page of ['index','futures','grid']) {
    const app=await setup(page,{respond:path=>{
      if(path==='/api/capabilities') return capabilityMatrix('BLOCKED');
      if(path==='/api/live/account/audit') return {status:403,body:{detail:'PAPER 模式已禁用实盘接口',code:null,reason:'PAPER_MODE',hint:PAPER_HINT,retryable:false}};
      return undefined;
    }});
    try {
      const d=app.w.document, bar=d.querySelector('.live-connection-bar');
      assert.equal(d.querySelectorAll('nav a').length,5,page+' nav entries unchanged');
      const button=bar.querySelector('.live-capability-details [data-action="audit-account"]');
      assert.ok(button,page+' audit button inside details');
      assert.equal(button.textContent,'核对账户');
      const before=app.calls.length;
      button.click();
      await sleep(80);
      const audits=app.calls.filter(([path])=>path==='/api/live/account/audit');
      assert.equal(audits.length,1,page+' exactly one audit request');
      assert.equal(audits[0][1],'GET',page+' audit is GET');
      assert.equal(app.calls.slice(before).some(([,method])=>['POST','PUT','DELETE'].includes(method)),false,page+' audit is read-only');
      const status=bar.querySelector('[data-audit-status]');
      assert.ok(status.textContent.includes(PAPER_HINT),page+' shared errorText: '+status.textContent);
      assert.ok(status.textContent.includes('PAPER_MODE'),page+' reason shown');
      assert.ok(/code=—|code=null/.test(status.textContent),page+' code shown');
      assert.equal(button.disabled,false,page+' button re-enabled');
      await sleep(150);
      assert.equal(app.calls.filter(([path])=>path==='/api/live/account/audit').length,1,page+' no auto retry');
      assert.equal(d.querySelectorAll('.live-capability-details').length,1,page+' still one details');
    } finally {app.close();}
  }
  assert.equal(readdirSync(staticDir).filter(name=>name.endsWith('.html')).length,5,'still five pages');
});
test('[UI-08] UNVERIFIED 与探测超时文案',async()=>{
  const app=await setup('index',{respond:path=>{
    if(path==='/api/capabilities') return {state:'UNKNOWN',reason:'UNVERIFIED'};
    if(path==='/api/exchange/health') return {status:200,body:{error:{reason:'PROBE_TIMEOUT',retryable:true}}};
    return undefined;
  }});
  try {
    const d=app.w.document, badge=d.querySelector('#liveBadge');
    assert.equal(badge.dataset.state,'unknown');
    assert.equal(badge.textContent,BADGES.UNKNOWN[0]);
    assert.equal(d.querySelector('[data-connection-title]').textContent,TITLES.UNKNOWN);
    assert.equal(d.querySelector('[data-refresh-account]').textContent,'重新检测');
    assert.equal(app.w.BTCCapabilities.errorText('PROBE_TIMEOUT'),PROBE_TIMEOUT_TEXT);
    assert.equal(app.w.BTCCapabilities.REASONS.PROBE_TIMEOUT.text,PROBE_TIMEOUT_TEXT);
    const message=d.querySelector('[data-connection-message]').textContent;
    assert.ok(message.includes(PROBE_TIMEOUT_TEXT),'bar hint: '+message);
    assert.ok(!/已探测可用|交易就绪/.test(message),'no READY wording: '+message);
    assert.deepEqual(app.errors,[]);
  } finally {app.close();}
});

