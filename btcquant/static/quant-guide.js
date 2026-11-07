/* Readable trading plans. Calculations are estimates, never order instructions. */
(function(root){
'use strict';
const defaults={fast:20,slow:60,regime:200,adx_min:22,atr_stop:2.5,activation_r:1,trail_atr:2,trailing_enabled:true,min_volume_ratio:.65,max_extension_atr:2,max_atr_pct:.06};
const num=v=>v!==null&&v!==''&&v!==undefined&&Number.isFinite(Number(v))?Number(v):null;
const money=v=>num(v)===null?'—':Number(v).toLocaleString('en-US',{maximumFractionDigits:2});
const pct=v=>num(v)===null?'—':money(Number(v)*100)+'%';
const period=v=>({ '1m':'1分钟','5m':'5分钟','15m':'15分钟','30m':'30分钟','1h':'1小时','2h':'2小时','4h':'4小时','8h':'8小时','12h':'12小时','1d':'1天'})[v]||v;
const parameters=m=>({...defaults,...m?.parameters});
function estimate({state,model,settings,signal,config={},futures}){
 const p=parameters(model),price=num(state?.price),atr=num(signal?.atr14),account=state?.account||{};
 const base=num(futures?account.wallet:account.equity),fraction=Math.min(Number(settings.risk_fraction),model?.risk_fraction??.005);
 const fee=num(config.fee_rate),slip=num(config.slippage_bps),distance=atr*p.atr_stop,leverage=futures?Number(settings.leverage):1;
 const valid=price>0&&atr>0&&base>0&&fee!==null&&slip!==null&&fee>=0&&slip>=0&&fraction>=.001&&fraction<=.02&&(!futures||(leverage>=1&&leverage<=10));
 if(!valid)return {valid:false,budget:base>0&&fraction>0?base*fraction:null,reason:'行情、账户或费用数据不完整，暂时不能估算。请刷新后重试。'};
 const cost=price*2*(fee+slip/10000),budget=base*fraction;
 let notional=Math.min(budget/(distance+cost)*price,base*(futures?Math.min(1,.25*leverage):Math.min(.25,config.max_fraction??.25)));
 if(!futures)notional=Math.min(notional,Math.max(0,num(account.usdt_free)??0));
 const qty=notional/price,loss=qty*(distance+cost),margin=futures?notional/leverage:notional;
 let reason=null;
 if(distance>=price*(futures?.3:1))reason=futures?'当前止损距离过大，执行器将拒绝新开仓。':'当前波动参考距离过大，模型将跳过买入。';
 if(futures&&distance/price>=1/leverage-(config.mmr??.005)-2*(fee+slip/10000))reason='止损接近或超过逐仓强平缓冲，请降低杠杆。';
 if(notional<(futures?5:0))reason='可用资金或估算仓位过小，可能不满足最小下单要求。';
 return{valid:true,price,atr,distance,budget,notional,margin,qty,loss,fraction,reason,
  longStop:price-distance,shortStop:price+distance,activation:p.activation_r*distance,trail:p.trail_atr*atr};
}
function rules(model,futures,interval){
 const p=parameters(model),external=model?.kind==='http',agent=model?.ai_mode==='agent';
 const entry=external?'外部程序返回买入 / 做多、做空或等待；其具体算法由外部程序决定。':agent?'AI 根据提供的数据建议方向；本地程序检查风险与行情条件后决定是否执行。':
 `顺势交易：快均线 EMA${p.fast} 高于慢均线 EMA${p.slow}，价格站上 EMA${p.slow} / EMA${p.regime}，趋势强度 ADX≥${p.adx_min}，连续两根收盘确认；更高周期同向。`;
 const exit=`${futures?'多单':'现货'}：EMA${p.fast} 跌到 EMA${p.slow} 下方，或收盘跌破 EMA${p.regime} 时退出。`+(futures?` 空单：均线反向转多，或收盘突破 EMA${p.regime} 时退出。`:'');
 return{entry,exit,short:futures?'做空采用相反的下跌趋势条件；多空都有自己的止损。':'只买入与卖出已有持仓，不借币做空。',
  filter:`成交量至少为近24根均量的 ${p.min_volume_ratio} 倍；价格距快均线不超过 ${p.max_extension_atr} 倍波动；波动需覆盖交易成本且不超过价格的 ${pct(p.max_atr_pct)}。`,
  protection:futures
   ? `每次新开仓附带 ${p.atr_stop} × ATR14 初始止损。`+(p.trailing_enabled?`盈利达到 ${p.activation_r}R 后，按开仓时 ${p.trail_atr} × ATR14 的距离跟随有利价格，保护价只收紧。`:'移动保护已关闭，仍保留初始止损。')
   : p.trailing_enabled?`浮盈达到 ${money(p.atr_stop*p.activation_r)} × 开仓 ATR14 后启动移动止盈，回撤距离为 ${p.trail_atr} × 开仓 ATR14。`:'移动止盈已关闭。',
  cadence:`按${period(interval)}已收盘K线判断；图表周期独立。退出后等待一个执行周期再考虑新开仓。`,
  ai:model?.ai_mode==='filter'?'AI只能保留或否决候选方向，不能反向开仓。':agent?'AI可建议方向与退出，本地风险限制仍然生效。':'未接入AI，不会调用AI API。'};
}
function status(signal,side,external){
 const rows=signal.conditions||[],key=side==='short'?'short':'long',missing=rows.filter(c=>!c[key]).map(c=>c.label);
 if(external)return '以外部 / AI 的有效回复为准；下列条件仅说明本地检查，不代表外部模型已发出信号。';
 if(!rows.length)return '正在获取已收盘行情，暂不判断方向。';
 return missing.length?'还在等待：'+missing.join('、')+'。':'本地方向条件已满足，仍需通过资金、风控与所选AI检查。';
}
const exports={estimate,rules,status,parameters};
if(typeof module!=='undefined'&&module.exports)module.exports=exports;
if(!root?.document)return;
const $=id=>document.getElementById(id),el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
const clock=v=>v?new Date(v).toLocaleString('zh-CN',{timeZone:'Asia/Hong_Kong',hour12:false})+' HKT':'—';
let latest=null,modal=null;
function settings(futures){return{enabled:true,model_id:$(futures?'fModelSelect':'spotModelSelect').value,interval:$(futures?'fAutoInterval':'spotAutoInterval').value,
 risk_fraction:Number($(futures?'fAutoRisk':'spotAutoRisk').value)/100,...(futures?{leverage:Number($('fAutoLeverage').value)}:{})};}
function addSection(parent,title,text){const s=el('section','plan-section');s.append(el('h4','',title),el('p','',text));parent.append(s);return s;}
function sourceText(name,data,selected){if(!selected)return name+'：未参与本次决策（未勾选）';if(!data)return name+'：等待检查数据，开启前预览会核对';return name+'：'+(data.status==='ready'?'已提供 · '+(name==='事件'?data.items.length+'条有效事件':'研究评分 '+money(data.up_score)+'，不是胜率'):data.reason||'尚不可用');}
function renderAI(parent,signal,model){
 parent.replaceChildren();const report=signal.ai_report,context=signal.optional_context||{};
 parent.append(el('h4','','AI 分析汇报'));
 if(model?.ai_mode==='off'||!model?.ai_mode)parent.append(el('p','','当前模型不使用AI。可在模型工坊选择AI复核或AI代理；事件与ML可分别勾选。'));
 else if(report?.status==='ready'){
  parent.append(el('p','ai-vote','AI复核后的方向：'+({LONG:'买入 / 做多',SHORT:'做空',WAIT:'等待'}[report.direction]||'等待')),
   el('p','ai-report-text',report.reason||'AI未提供说明。'),el('small','',report.provider_model+' · 对应K线 '+clock(report.bar_time)));
  parent.append(el('p','',signal.model_error?'执行状态：'+signal.model_error:(signal.entry_signal||signal.short_entry_signal?'仍需执行器检查余额、已有仓位和冷却状态；AI建议不等于已经下单。':'执行状态：当前没有可执行的新开仓信号；AI建议不能越过本地过滤。')));
 }else parent.append(el('p','',report?.reason||signal.model_error||'尚无有效AI回复；当前显示的指标由本地计算，未冒充AI分析。'));
 parent.append(el('p','context-line',sourceText('事件',context.events,model?.ai_events)),el('p','context-line',sourceText('ML',context.ml,model?.ai_ml)));
 if(context.events?.status==='ready')for(const e of context.events.items.slice(0,3)){
  const link=el('a','event-source',e.title);try{const u=new URL(e.url);if(u.protocol==='https:'){link.href=u.href;link.target='_blank';link.rel='noopener noreferrer';}}catch(_){};
  parent.append(link,el('small','',`${e.source} · 发布 ${clock(e.published_at)} · 可用 ${clock(e.available_at)}`));
 }
 if(context.ml?.status==='ready')parent.append(el('small','',`ML ${context.ml.model_version} / ${context.ml.model_fingerprint} · 训练 ${clock(context.ml.trained_at)} · 样本外 ${context.ml.oos_trades}笔；${context.ml.validation}`));
}
function mount(quant,futures){
 const toolbar=el('div','quant-plan-toolbar'),preview=el('button','plan-preview-btn','查看交易规则与风险');preview.type='button';preview.id='quantPlanPreview';preview.onclick=()=>review(futures,false);
 toolbar.append(el('span','','先看懂它怎样交易，再决定是否开启。'),preview);quant.querySelector('.quant-controls').after(toolbar);
 const plan=el('div','quant-plan');plan.id='quantPlan';toolbar.after(plan);
 const ai=el('details','quant-ai-report'),summary=el('summary','','AI 分析与信息来源');ai.append(summary,el('div','quant-ai-body'));ai.id='quantAIReport';quant.querySelector('.quant-guard').before(ai);
 const more=el('details','quant-technical'),head=el('summary','','展开指标条件与数值');more.append(head,document.getElementById('quantConditions'));quant.querySelector('.quant-states').append(more);
}
function update({state,model,signal,futures}){
 latest={state,model,signal,futures};const plan=$('quantPlan');if(!plan)return;
 const p=parameters(model),external=model?.kind==='http'||model?.ai_mode==='agent';
 const activeId=state.auto?.model_id||(futures?'trend-v2':'spot-trend-v2'),currentId=$(futures?'fModelSelect':'spotModelSelect').value;
 if(currentId!==activeId){plan.replaceChildren(el('p','plan-selection-note','正在编辑另一模型；下面的实时状态仍属于已保存模型。查看交易规则与风险，可预览所选设置。'));return;}
 plan.replaceChildren();
 for(const[side,title]of (futures?[['long','什么时候做多'],['short','什么时候做空']]:[['long','什么时候买入'],['exit','什么时候卖出']])){
  const card=el('section','plan-direction '+side);card.append(el('h4','',title));
  if(side==='exit')card.append(el('p','',`已有持仓可在趋势转弱时卖出。模型买入的持仓也可在盈利后启动移动止盈。`));
  else{card.append(el('p','',external?'由所选外部 / AI 模型提出方向，本地规则与资金检查决定能否执行。':side==='long'?`先确认上涨趋势，再等成交量、波动与追价距离合适；仅在${period(state.auto?.interval||'4h')}K线收盘后判断。`:'先确认下跌趋势，再等成交量、波动与追价距离合适；不是价格一跌就开空。'),el('p','plan-missing',status(signal,side,external)));}
  plan.append(card);
 }
 renderAI($('quantAIReport').querySelector('.quant-ai-body'),signal,model);
 const reason=$('quantReason'),positions=futures?state.positions||[]:Number(state.account?.position_qty)>0?[state.account]:[];
 if(!signal.model_error&&!((futures?state.auto?.snapshot?.guard:signal.data_guard))){
  if(positions.length)reason.textContent=futures?(positions.some(p=>p.reason!=='auto')?'已有手动仓位：AUTO不会接管，也不会在当前仓位之外新开仓。':'已有AUTO仓位：当前重点是按趋势与保护价管理退出。'):'已有现货持仓：AUTO开启时会按模型管理卖出；可在持仓区查看移动止盈状态。';
  else if(external)reason.textContent=signal.model_reason||'等待外部模型的有效信号。';
  else if(!signal.entry_signal&&!signal.short_entry_signal)reason.textContent=futures?'两个方向都未形成完整入场条件；下方分别列出还差什么。':'买入条件尚未全部满足；保持空仓，等待下一次收盘检查。';
 }
 const cells=$('quantConditions');for(const row of cells.querySelectorAll('.quant-condition:not(:first-child)')){
  for(const cell of [...row.children].slice(1))if(cell.textContent==='—')cell.textContent='未满足';else if(cell.textContent==='✓')cell.textContent='满足';
 }
 const notes=el('p','plan-metric-note',`均线：${money(signal.ema_fast)} / ${money(signal.ema_slow)} / ${money(signal.ema_regime)} · 趋势强度 ${money(signal.adx14)}（需≥${p.adx_min}） · 相对成交量 ${money(signal.volume_ratio)}倍 · 距快均线 ${money(signal.extension_atr)} ATR（需≤${p.max_extension_atr}）`);cells.append(notes);
}
async function read(path,futures){const u=new URL(path,location.origin);u.searchParams.set('symbol',root.BTCSymbols?.current||'BTCUSDT');const r=await fetch(u,{cache:'no-store'});if(!r.ok)throw Error('读取配置或行情失败，请重试');return r.json();}
async function review(futures,canEnable=true){
 if(modal)return {approved:false};
 const chosen=settings(futures),dialog=el('dialog','quant-review-dialog');modal=dialog;dialog.setAttribute('aria-labelledby','quantReviewTitle');
 const top=el('div','plan-dialog-head');top.append(el('div','',futures?'合约模拟 · 开启前预览':'现货 · 开启前预览'));const close=el('button','plan-close','×');close.type='button';close.setAttribute('aria-label','关闭预览');top.append(close);
 const title=el('h3','','先确认模型会怎样交易');title.id='quantReviewTitle';const body=el('div','plan-dialog-body'),bottom=el('div','plan-dialog-actions');const cancel=el('button','plan-secondary',canEnable?'暂不开启':'关闭预览'),confirm=el('button','plan-primary','确认规则并开启模型');cancel.type=confirm.type='button';confirm.disabled=true;
 bottom.append(cancel);if(canEnable)bottom.append(confirm);dialog.append(top,title,body,bottom);document.body.append(dialog);
 body.append(el('p','','正在读取本次设置、账户和波动数据…'));dialog.showModal();
 const approved=new Promise(resolve=>{dialog.addEventListener('close',()=>{modal=null;dialog.remove();resolve({approved:dialog.returnValue==='enable',settings:chosen});},{once:true});});
 close.onclick=cancel.onclick=()=>dialog.close('cancel');confirm.onclick=()=>dialog.close('enable');
 void (async()=>{try{
  const [state,library,config,observation]=await Promise.all([read(futures?'/api/futures/status':'/api/status',futures),read('/api/models',futures),read('/api/config',futures),read('/api/model?interval='+chosen.interval,futures)]);
  if(!dialog.isConnected)return approved;
  const model=library.models.find(m=>m.id===chosen.model_id);if(!model)throw Error('所选模型已不存在，请重新选择');
  if(!Number.isFinite(chosen.risk_fraction)||chosen.risk_fraction<.001||chosen.risk_fraction>.02)throw Error('单笔风险须为0.1%–2%，请检查输入');
  if(futures&&(!Number.isInteger(chosen.leverage)||chosen.leverage<1||chosen.leverage>10))throw Error('AUTO杠杆须为1–10之间的整数');
  if(chosen.risk_fraction>model.risk_fraction)throw Error('单笔风险超过模型上限，请先调低风险设置');
  const signal=observation.signal||{},r=rules(model,futures,chosen.interval),p=parameters(model),e=estimate({state,model,settings:chosen,signal,config:futures?state.config:config,futures});
  body.replaceChildren();body.append(el('p','plan-model-name',`${model.name} · v${model.version} · ${period(chosen.interval)} · ${futures?'多空双向 / '+chosen.leverage+'倍杠杆':'仅买入与卖出'}`));
  addSection(body,'01 · 它会怎样交易',r.entry+' '+r.short+' '+r.cadence);addSection(body,'本地仍会检查',r.filter);
  if(futures){
  const riskSection=addSection(body,'02 · 预计承担多少风险','按当前价格和所选周期的波动估算，实际开仓时重新计算；风险预算不是投入金额。');
  const metrics=el('div','plan-risk-grid');for(const[label,value]of [['单笔风险预算',e.budget===null?'暂不可用':'≤ '+money(e.budget)+' USDT'],[futures?'预计名义仓位':'预计买入金额',e.valid?money(e.notional)+' USDT':'暂不可用'],[futures?'预计占用保证金':'预计使用现金',e.valid?money(e.margin)+' USDT':'暂不可用'],['估算止损损失 · 含费用滑点',e.valid?'约 '+money(e.loss)+' USDT':'暂不可用']]){const cell=el('div');cell.append(el('span','',label),el('b','',value));metrics.append(cell);}riskSection.append(metrics);
  riskSection.append(el('p','plan-footnote',(futures?'风险预算按钱包余额计算，AUTO名义价值≤钱包1倍、初始保证金≤25%。':'单次买入上限为权益25%，并受可用USDT约束。')+' 止损损失不是保证上限；跳空、网络中断或滑点扩大时可能超出预算。'));
  if(e.valid){riskSection.append(el('p','',`参考价 ${money(e.price)} USDT；ATR14 ${money(e.atr)}；初始止损距离约 ${money(e.distance)} USDT（${pct(e.distance/e.price)}）。`),el('p','',futures?`以该价开仓举例：多单初始止损约 ${money(e.longStop)}；空单约 ${money(e.shortStop)} USDT。`:`以该价买入举例：初始止损约 ${money(e.longStop)} USDT。`));}
  } else {
   const amount=e.valid?money(e.notional)+' USDT':'暂不可用';
   addSection(body,'02 · 预计买入金额',`当前估算 ${amount}。按账户权益、ATR 波动和交易成本计算，并受可用 USDT 与单次投入上限约束。`);
  }
  addSection(body,'03 · 什么情况下退出',r.exit+' '+r.protection+(e.valid&&p.trailing_enabled?` 以上述参考价为例，有利移动约 ${money(e.activation)} USDT 后启动，跟踪距离约 ${money(e.trail)} USDT。`:'')+(futures?' 关闭 AUTO 后，已有止损与移动保护继续运行。':p.trailing_enabled?' 关闭 AUTO 后，已有移动止盈继续运行。':''));
  if(futures)addSection(body,'已有仓位与委托','已有手动仓位不会被AUTO接管；当前有仓位时不新增AUTO仓位。预览不修改已有仓位、止损或委托。');
  else addSection(body,'已有现货持仓','开启后，模型可能按趋势退出卖出当前现货持仓。旧仓没有完整开仓波动记录时，不会补造模型移动保护；请先检查持仓区的现有保护。');
  const sources=addSection(body,'04 · AI与信息来源',r.ai+' '+sourceText('事件',null,model.ai_events)+'；'+sourceText('ML',null,model.ai_ml)+'。');
  let contextGuard=null;
  if(model.ai_events||model.ai_ml){
   const contextResult=await read('/api/models/'+encodeURIComponent(model.id)+'/context?interval='+chosen.interval,futures);
   if(!dialog.isConnected)return approved;
   const context=contextResult.context;sources.lastChild.textContent=r.ai+' '+sourceText('事件',context.events,model.ai_events)+'；'+sourceText('ML',context.ml,model.ai_ml)+'。';contextGuard=contextResult.guard;
  }
  sources.append(el('p','plan-footnote','此预览只读取本地数据，不调用AI、不下单。勾选的来源缺失、过期或不匹配时暂停新开仓。AI仅接收公开行情、配置及选中的公开事件/研究结果，不接收余额、持仓或密钥。'));
  const ai=el('div','plan-preview-ai');if((latest?.state.auto?.model_id||'trend-v2')===model.id)renderAI(ai,latest?.signal||{},model);else renderAI(ai,{},model);sources.append(ai);
  if(futures){const glossary=el('details','plan-glossary');glossary.append(el('summary','','术语说明：ATR、R、杠杆和止损'),el('p','','ATR是最近行情的波动尺度；1R是开仓价到初始止损价的距离。杠杆影响保证金占用，不代表模型一定赚更多。移动保护是价格回撤时触发退出，不保证成交价等于保护价。'));body.append(glossary);}
  body.append(el('p','plan-footnote','行情与波动参考：'+clock(signal.bar_time)+' 开盘的已收盘K线。当前预览不是回测，也不是收益承诺。'));
  const active=futures?state.auto?.enabled:state.auto_enabled;
  const minutes={'1m':1,'5m':5,'15m':15,'30m':30,'1h':60,'2h':120,'4h':240,'8h':480,'12h':720,'1d':1440}[chosen.interval],barEnd=new Date(signal.bar_time).getTime()+minutes*60000;
  const dataGuard=!Number.isFinite(barEnd)||barEnd>Date.now()||Date.now()-barEnd>minutes*60000*1.5?'所选周期行情尚未收盘或已过期，请刷新后再开启。':null;
  const unavailable=!e.valid||e.reason||contextGuard||dataGuard||(model.ai_mode!=='off'&&!library.ai_configured)||state.error||state.last_error;
  if(unavailable)body.append(el('p','plan-warning',String(e.reason||contextGuard||dataGuard||(!e.valid?e.reason:model.ai_mode!=='off'&&!library.ai_configured?'所选模型需要AI，请先配置DeepSeek Key。':state.error||state.last_error))));
  if(active)body.append(el('p','plan-warning','模型已经运行中，本次仅查看规则；不会重复开启。'));
  confirm.disabled=!!unavailable||!!active;
 }catch(err){if(dialog.isConnected){body.replaceChildren(el('p','plan-warning',err.message));confirm.disabled=true;}}})();
 return approved;
}
root.BTCQuantGuide={...exports,mount,update,review};
})(typeof window==='undefined'?null:window);
