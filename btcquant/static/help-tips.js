/* Contextual explanations: mouse, keyboard and touch. No external service. */
(()=>{'use strict';
const tips={
'买入金额（USDT）':'手动买入使用的USDT金额，与AUTO的风险预算分开。实际币数根据成交价、手续费及交易数量规则计算。',
'价格':'每单位基础币的USDT价格。报价不是总投入金额，需结合数量或名义价值理解。',
'数量':'这笔交易涉及的币数，例如BTC数量；不是USDT金额。数量×价格约等于名义价值。',
'预计初始保证金':'按当前名义价值÷杠杆估算的开仓保证金，不含所有后续费用，也不是最大损失。',
'止损价差损失 · 未含滑点':'按开仓参考价与止损价的差额×数量估算，尚未包括滑点；止损跳空时实际损失可能更大。',
'钱包余额':'合约模拟钱包中已结算的余额，不含当前仓位浮动盈亏。当前AUTO以此计算单笔风险预算。',
'可用保证金':'钱包余额减去已被仓位占用的保证金。它不等于账户权益，也不是当前能承受的最大亏损。',
'已实现盈亏':'通过已完成平仓结算的盈亏。请结合单独列出的手续费和账户净值判断最终收益。',
'开仓均价':'当前仓位的平均开仓成交价；同方向加仓后可能按数量加权变化。',
'标记价':'本地合约当前使用现货参考价模拟盈亏与触发，不是Binance Futures真实标记价格。',
'下边界 USDT':'网格允许交易的下限价格；持续跌破时可能触发所设止损。不是支撑必然有效的保证。',
'上边界 USDT':'网格允许交易的上限价格；本模拟触顶时会清仓并停止网格。',
'初始仓位比例':'网格开始时买入基础币所占的资金比例，其余保留为现金等待后续买入。',
'跌破下边界额外止损（%）':'在网格下边界再下跌指定比例时清仓停止。价格跳空与滑点可能使实际成交更差。',
'执行模型':'选择负责提出买卖信号的模型。默认模型使用本地规则；自创模型可选择外部程序或AI。切换图表不会切换执行模型。',
'AUTO 执行周期':'模型每次判断所使用的已收盘K线周期，与图表周期独立。例如选1小时，模型按每根小时K线收盘后的条件判断；不会每秒追着价格开仓。',
'单笔风险（%）':'这是一次交易的计划止损预算，不是投入金额。例如账户10,000 USDT、风险0.5%，预算约50 USDT。程序根据止损距离计算仓位；跳空或滑点可能使实际损失更大。',
'AUTO 杠杆 ≤ 10x':'自动模型最多使用10倍杠杆。杠杆影响保证金占用；仓位仍受风险预算与资金上限限制，并非把全部余额放大10倍交易。',
'单次投入上限':'现货AUTO单次买入不超过账户权益25%，并受到可用USDT和止损风险预算约束。',
'趋势连续确认':'连续两根已收盘K线满足趋势条件，快均线近三根的方向也一致。目的是减少短暂波动触发的交易。',
'更高周期同向':'更大周期的趋势也要支持当前方向。1小时执行会参考完整4小时K线；4小时执行参考完整日线。其他周期按程序的聚合倍率计算。',
'成交量有效':'当前K线成交量至少达到最近24根平均成交量的设定比例。例如0.65倍表示至少达到均量的65%；它不能证明真实买卖意图。',
'波动覆盖成本':'行情波动需足以覆盖手续费和滑点，同时不能超过模型允许的最高波动。过低或过高都会暂停新开仓。',
'避免过度追价':'检查价格与快均线的距离。默认不超过2倍ATR；距离过大时等待，降低快速上涨后追多或快速下跌后追空的机会。',
'单笔风险预算（账户权益 %）':'定义这个模型允许的每笔风险上限。交易页可以进一步调低，不能调高到超过模型上限。合约当前按钱包余额计算预算。',
'快均线 EMA':'较灵敏的指数移动平均线。周期较小通常反应更快，也更容易受短期波动影响。',
'慢均线 EMA':'较平滑的指数移动平均线，与快均线比较来判断趋势方向。必须大于快均线周期。',
'趋势均线 EMA':'更长周期的方向过滤。价格位于它上方或下方，会影响默认模型允许的交易方向。',
'ADX 最低值':'ADX衡量趋势强度，不判断涨跌。门槛调高通常筛选更明显的趋势，但信号也会减少。',
'初始止损 × ATR':'初始止损距离=开仓时ATR×该参数。例如ATR为100、参数2.5，价格止损距离约250。距离更大时，在相同风险预算下仓位会更小。',
'移动距离 × 开仓 ATR':'移动保护启动后，跟随最有利价格的回撤距离。例如开仓ATR为100、参数2，参考距离约200；保护价只会收紧。',
'移动保护启动 × R':'R是实际开仓价到初始止损价的距离。设置1R表示有利价格移动达到该距离后，才启动移动保护。不是收益率100%。',
'允许追价距离 × ATR':'价格偏离快均线允许的最大距离。调小会减少追价，也可能错过强势趋势。',
'最低相对成交量':'当前量÷最近24根均量。例如0.65即均量的65%。调高会更严格地过滤低量行情。',
'最高 ATR / 价格（%）':'允许的最大相对波动。例如6%表示ATR不能超过价格的6%；极端波动时避免新开仓。',
'盈利后移动保护':'达到启动浮盈后跟随有利价格，回撤触及保护价时退出。关闭它仍保留初始止损。保护由本地程序执行，需要终端保持运行。',
'AI 模式':'关闭时不调用AI；复核模式只能保留或否决候选方向；代理模式可建议方向与退出。余额、止损和风险限制始终由本地程序管理。',
'自定义 Prompt':'写你希望AI关注的事实与约束，例如：事件风险高时等待，说明信号失效条件。Prompt不能取消本地风控，也不能保证预测准确。',
'计算方式':'本地规则无需运行额外程序。外部HTTP用于连接自己编写并保持运行的模型服务；它不是DeepSeek的API地址。',
'外部预测地址':'例如http://127.0.0.1:9001/predict。需先启动自己的Python模型服务，再填入它的监听地址；只填网址不会自动创建或启动模型。',
'模型 ID':'模型的唯一标识，使用小写字母、数字、连字符或下划线。外部程序返回的model_id必须与它一致。',
'版本':'记录模型的策略版本。修改参数或Prompt后建议更改版本，便于区分结果与排查问题。',
'名义价值（USDT）':'仓位按标记价格计算的总价值，不是保证金。例如名义价值1,000 USDT、10倍杠杆，初始保证金约100 USDT，另计手续费。',
'名义价值 USDT':'仓位总价值=币数×价格。它与保证金、钱包余额是不同概念。',
'保证金模式':'逐仓将该仓位保证金单独计算；全仓参考本地账户余额。本程序为简化模拟，不完全复刻Binance分档保证金与强平规则。',
'杠杆':'杠杆影响初始保证金要求。例如1,000 USDT名义仓位、5倍杠杆，初始保证金约200 USDT。更高杠杆通常使逐仓强平价更接近开仓价。',
'订单类型':'市价按当前模拟价格加滑点成交；限价等待价格触及设定价。当前限价模拟不包含真实撮合队列和部分成交。',
'限价（USDT）':'你希望成交的价格。挂单后需要行情触及才能模拟成交；没有触及不代表程序出错。',
'止盈价（可选）':'价格向有利方向达到设定值时平仓。它是固定价格止盈，与盈利后跟随价格的移动保护不同。',
'止损价（可选）':'价格向不利方向触及时退出，用于控制计划损失。本地轮询、跳空与滑点可能使成交价差于设定价格。',
'ROE':'未实现盈亏÷该仓位保证金，不等同于价格涨跌幅或整个账户收益率。杠杆不同，即使同样币数与盈亏，ROE也可能不同。',
'强平价':'简化模拟中保证金不足时触发强制平仓的参考价。当前使用现货参考价与简化维持保证金计算，不能作为真实交易所强平价。',
'保证金':'为仓位占用的资金。它不等于最大可能损失，也不等于仓位名义价值。',
'未实现盈亏':'持仓按当前参考价计算的浮动盈亏，尚未通过平仓结算；真实净收益还受退出费用和滑点影响。',
'手续费':'交易费用。当前本地现货通常按配置收取；合约模拟费率与页面配置一致，不代表你的Binance VIP等级或真实费率。',
'初始止损':'新仓建立时设置的第一道保护价，按照ATR距离与本次风险预算计算。',
'单边手续费 / bps':'1 bps=0.01%。单边10 bps=0.1%；一次完整买卖通常有两次手续费。回测需同时考虑退出费用。',
'单边滑点 / bps':'模拟成交价相对参考价的不利偏差。1 bps=0.01%；滑点随方向作用，不能当作固定盈利或真实成交保证。',
'历史长度':'加载的K线数量，不等于时间长度。3000根1小时约125天；3000根4小时约500天。图表根数不等于训练样本充分。',
'信号周期':'研究模型使用的K线周期。当前研究固定持有4根，因此1H对应约4小时，4H对应约16小时。',
'上涨分数':'ML模型对研究标签的原始评分，未经独立概率校准。0.7不代表真实交易胜率70%，也没有包含AUTO完整止损退出逻辑。',
'胜率':'已完成交易中盈利交易的比例。高胜率不一定赚钱，还需要结合平均盈亏、费用和最大回撤。',
'盈亏因子 PF':'盈利交易总收益÷亏损交易总损失。没有亏损或交易太少时，该指标难以可靠解释。',
'最大回撤':'账户净值从历史高点到后续低点的最大下降幅度。它描述已观察风险，不是未来损失上限。',
'投入 USDT':'分配给本地现货网格的虚拟资金。网格可能同时持有币和现金，震荡成交收益不能覆盖持续下跌风险。',
'网格数量':'在上下边界之间划分多少格。格数越多，单格价差越小，手续费更可能侵蚀每格收益。',
'初始仓位（%）':'网格启动时先买入多少比例的资金，其余保留为买单现金。并非一次交易的风险比例。',
'分布':'等差网格各格价格差相同；等比网格各格涨跌百分比相同。',
'成交量':'每根K线期间的现货成交数量，红绿按该根K线涨跌区分，不能直接当成主动买入和主动卖出的量。MA5/MA10是成交量均线。',
'参考公开事件':'只把已收到、解析且仍在有效期内的关联事件交给AI。未勾选不读取这类变量；勾选但无有效数据时暂停新开仓。',
'参考ML研究评分':'先在研究页训练同币种、同周期模型，再对最新行情计算评分。要求7天内、样本外至少100行与30笔；这些门槛不等于已验证盈利。'
};
const ids={fAutoRisk:'单笔风险（%）',spotAutoRisk:'单笔风险（%）',fAutoInterval:'AUTO 执行周期',spotAutoInterval:'AUTO 执行周期',fAutoLeverage:'AUTO 杠杆 ≤ 10x',fNotional:'名义价值（USDT）',fLeverageRange:'杠杆',fLeverage:'杠杆',fTp:'止盈价（可选）',fSl:'止损价（可选）',mRisk:'单笔风险预算（账户权益 %）',mAiMode:'AI 模式',mPrompt:'自定义 Prompt',mAiEvents:'参考公开事件',mAiMl:'参考ML研究评分'};
let popup,active=null,hideTimer;
function hide(){if(active){active.removeAttribute('aria-describedby');active=null;}popup?.remove();popup=null;}
function show(target,text){clearTimeout(hideTimer);if(active===target)return;hide();active=target;popup=document.createElement('div');popup.className='help-tooltip';popup.id='quantHelpTooltip';popup.setAttribute('role','tooltip');popup.textContent=text;document.body.append(popup);target.setAttribute('aria-describedby',popup.id);popup.addEventListener('mouseenter',()=>clearTimeout(hideTimer));popup.addEventListener('mouseleave',()=>{hideTimer=setTimeout(hide,120);});place();}
function place(){if(!popup||!active?.isConnected){hide();return;}const r=active.getBoundingClientRect(),w=popup.offsetWidth,h=popup.offsetHeight;let top=r.top-h-9;if(top<8)top=r.bottom+9;popup.style.left=Math.max(8,Math.min(innerWidth-w-8,r.left+r.width/2-w/2))+'px';popup.style.top=Math.max(8,Math.min(innerHeight-h-8,top))+'px';}
function decorate(node,key){if(!node||node.dataset.helpReady||!tips[key])return;node.dataset.helpReady='1';node.classList.add('help-label');node.tabIndex=0;node.setAttribute('role','button');node.setAttribute('aria-label',key+'：查看说明');const icon=document.createElement('span');icon.className='help-marker';icon.setAttribute('aria-hidden','true');icon.textContent='ⓘ';node.append(icon);node.addEventListener('mouseenter',()=>show(node,tips[key]));node.addEventListener('mouseleave',()=>{hideTimer=setTimeout(hide,150);});node.addEventListener('focus',()=>show(node,tips[key]));node.addEventListener('blur',()=>{hideTimer=setTimeout(hide,150);});node.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();show(node,tips[key]);});node.addEventListener('keydown',e=>{if(e.key==='Escape')hide();if(e.key==='Enter'||e.key===' '){e.preventDefault();show(node,tips[key]);}});}
function scan(){
 for(const label of document.querySelectorAll('label')){
  if(label.querySelector('.help-label'))continue;
  const control=label.querySelector('input,select,textarea')||(label.htmlFor?document.getElementById(label.htmlFor):null),textNodes=[...label.childNodes].filter(n=>n.nodeType===3&&n.textContent.trim());
  const text=textNodes.map(n=>n.textContent.trim()).join(' '),key=ids[control?.id]||Object.keys(tips).find(k=>text===k)||(/^杠杆/.test(text)?'杠杆':null);
  if(!key||!textNodes.length)continue;
  const span=document.createElement('span');span.textContent=text;if(control&&!control.getAttribute('aria-label'))control.setAttribute('aria-label',text);
  label.insertBefore(span,textNodes[0]);textNodes.forEach(n=>n.remove());decorate(span,key);
 }
 for(const node of document.querySelectorAll('th,.quant-condition>span:first-child,.kv-label,.meter-row>span:first-child')){
  const key=node.textContent.trim();
  if(node.tagName!=='TH'){decorate(node,key);continue;}
  if(!tips[key]||node.querySelector('.help-label'))continue;
  const label=document.createElement('span');
  label.append(...node.childNodes);
  node.append(label);
  decorate(label,key);
 }
 if(active&&!active.isConnected)hide();
}
function init(){scan();let queued=false;new MutationObserver(records=>{if(records.every(r=>r.target.closest?.('.help-tooltip')))return;if(!queued){queued=true;requestAnimationFrame(()=>{queued=false;scan();});}}).observe(document.body,{childList:true,subtree:true});window.addEventListener('resize',place);window.addEventListener('scroll',hide,true);document.addEventListener('keydown',e=>{if(e.key==='Escape')hide();});document.addEventListener('pointerdown',e=>{if(!e.target.closest('.help-label,.help-tooltip'))hide();});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
