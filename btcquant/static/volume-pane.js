/* Separate spot-volume pane sharing the main chart's logical time range. */
(function(root){'use strict';
function prepare(candles){
 const rows=[],ma5=[],ma10=[];let sum5=0,sum10=0;
 for(let i=0;i<candles.length;i++){
  const c=candles[i],v=Number(c.volume);if(!Number.isFinite(v)||v<0)throw Error('成交量数据无效');
  sum5+=v;sum10+=v;if(i>=5)sum5-=Number(candles[i-5].volume);if(i>=10)sum10-=Number(candles[i-10].volume);
  rows.push({time:c.time,value:v,color:Number(c.close)>=Number(c.open)?'#0ecb81':'#f6465d'});
  ma5.push(i>=4?{time:c.time,value:sum5/5}:{time:c.time});ma10.push(i>=9?{time:c.time,value:sum10/10}:{time:c.time});
 }
 return{rows,ma5,ma10};
}
if(typeof module!=='undefined'&&module.exports)module.exports={prepare};if(!root?.document)return;
const compact=n=>n==null?'—':new Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:2}).format(n);
function create({chart,series,wrap}){
 if(!root.LightweightCharts||!wrap)return null;
 const panel=document.createElement('section');panel.className='volume-panel';panel.setAttribute('aria-label','成交量副图');
 const header=document.createElement('div');header.className='volume-legend';header.textContent='成交量 · 正在加载…';panel.append(header);
 const canvas=document.createElement('div');canvas.className='volume-chart';panel.append(canvas);(wrap.closest('.chart-wrap')||wrap).after(panel);
 const mainOptions=chart.options(),vchart=root.LightweightCharts.createChart(canvas,{width:Math.max(50,wrap.clientWidth),height:110,
  layout:{background:{color:'transparent'},textColor:'#8b94a3',fontSize:10},grid:{vertLines:{color:'#202731'},horzLines:{color:'#202731'}},
  rightPriceScale:{borderColor:'#2b3139',minimumWidth:80,scaleMargins:{top:.12,bottom:.02}},
  timeScale:{borderColor:'#2b3139',timeVisible:true,secondsVisible:false,rightOffset:80,barSpacing:mainOptions.timeScale.barSpacing,tickMarkFormatter:mainOptions.timeScale.tickMarkFormatter},
  localization:{timeFormatter:mainOptions.localization.timeFormatter},handleScroll:true,handleScale:true,
 });
 chart.applyOptions({rightPriceScale:{minimumWidth:80}});
 const histogram=vchart.addHistogramSeries({priceFormat:{type:'volume'},priceLineVisible:false,lastValueVisible:false}),
  line5=vchart.addLineSeries({color:'#f0b90b',lineWidth:1,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false}),
  line10=vchart.addLineSeries({color:'#b276d3',lineWidth:1,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false});
 let candles=[],prepared=null,lookup=new Map(),syncing=false,shown=true;
 const key='btcquant.volume.'+location.pathname;try{shown=localStorage.getItem(key)!=='off';}catch(_){}
 const button=document.createElement('button');button.type='button';button.className='tool-btn volume-toggle';button.textContent='成交量';button.title='显示 / 隐藏成交量副图';
 const tools=wrap.closest('.chart-panel')?.querySelector('.draw-bar,.chart-tools,.panel-head');tools?.append(button);
 function show(value){shown=value;panel.hidden=!value;button.setAttribute('aria-pressed',String(value));button.classList.toggle('active',value);chart.applyOptions({timeScale:{visible:!value}});if(value){resize();matchMain();}}
 button.onclick=()=>{show(!shown);try{localStorage.setItem(key,shown?'on':'off');}catch(_){}};
 function matchMain(){if(!shown||!candles.length)return;const range=chart.timeScale().getVisibleLogicalRange();if(range&&!syncing){syncing=true;vchart.timeScale().setVisibleLogicalRange(range);syncing=false;}}
 chart.timeScale().subscribeVisibleLogicalRangeChange(matchMain);
 vchart.timeScale().subscribeVisibleLogicalRangeChange(range=>{if(!syncing&&range&&candles.length&&shown){syncing=true;chart.timeScale().setVisibleLogicalRange(range);syncing=false;}});
 function legend(index){
  const c=candles[index];if(!c){header.textContent='该位置没有已收盘成交量数据';return;}
  const q=c.quote_volume==null?null:Number(c.quote_volume),time=new Date(c.time*1000).toLocaleString('zh-CN',{timeZone:'Asia/Hong_Kong',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
  header.replaceChildren();for(const [text,color]of [[`Vol(${root.BTCSymbols?.base||'币'}) ${compact(c.volume)}`,''],[`Vol(USDT) ${compact(q)}`,''],[`MA5 ${compact(prepared.ma5[index]?.value)}`,'#f0b90b'],[`MA10 ${compact(prepared.ma10[index]?.value)}`,'#b276d3'],[time+' HKT · 已收盘现货量','']]){const s=document.createElement('span');s.textContent=text;if(color)s.style.color=color;header.append(s);}
 }
 chart.subscribeCrosshairMove(param=>{if(syncing||!shown||!candles.length)return;const i=param.time?lookup.get(param.time):candles.length-1;legend(i);syncing=true;if(i===undefined)vchart.clearCrosshairPosition();else vchart.setCrosshairPosition(Number(candles[i].volume),candles[i].time,histogram);syncing=false;});
 vchart.subscribeCrosshairMove(param=>{if(syncing||!shown||!candles.length)return;const i=param.time?lookup.get(param.time):candles.length-1;legend(i);syncing=true;if(i===undefined)chart.clearCrosshairPosition();else chart.setCrosshairPosition(Number(candles[i].close),candles[i].time,series);syncing=false;});
 function resize(){if(!shown||!panel.clientWidth)return;vchart.resize(panel.clientWidth,Math.max(55,canvas.clientHeight));const width=chart.priceScale('right').width();if(width>0)vchart.applyOptions({rightPriceScale:{minimumWidth:Math.max(80,width)}});matchMain();}
 new ResizeObserver(resize).observe(panel);
 function theme(){const light=document.documentElement.dataset.theme==='light';vchart.applyOptions({layout:{textColor:light?'#687382':'#8b94a3'},grid:{vertLines:{color:light?'#e9edf2':'#202731'},horzLines:{color:light?'#e9edf2':'#202731'}}});}
 document.addEventListener('btcquant:ui',theme);theme();show(shown);
 return{setData(data,interval){candles=data;lookup=new Map(data.map((c,i)=>[c.time,i]));try{prepared=prepare(data);histogram.setData(prepared.rows);line5.setData(prepared.ma5);line10.setData(prepared.ma10);panel.dataset.volumeCount=String(data.length);vchart.applyOptions({timeScale:{secondsVisible:interval==='30s'}});legend(data.length-1);matchMain();resize();}catch(e){header.textContent=e.message;histogram.setData([]);line5.setData([]);line10.setData([]);}},chart:vchart};
}
root.BTCVolumePane={create,prepare};
})(typeof window==='undefined'?null:window);
