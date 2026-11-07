(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const pct = value => value == null ? "—" : `${(value*100).toFixed(2)}%`;
  const time = value => value ? new Date(value).toLocaleString("zh-CN", {timeZone:"Asia/Hong_Kong",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false})+" HKT" : "—";
  const category = {macro:"宏观",regulation:"监管",security:"安全",exchange:"交易所",adoption:"采用",other:"其他"};
  let last = null, resultStamp = "", eventStamp = "", polling = false, formDirty = false;
  try { document.documentElement.dataset.theme = localStorage.getItem("btcquant.theme") || "dark"; } catch (_) {}
  const pair = new URLSearchParams(location.search).get("symbol");
  if ([...$("labSymbol").options].some(o => o.value === pair)) $("labSymbol").value = pair;

  async function api(path, data) {
    const res = await fetch(path, {cache:"no-store", ...(data === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)})});
    const body = await res.json();
    if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "输入参数无效，请检查后重试");
    return body;
  }
  function message(value, error=false) { $("jobMessage").textContent=value; $("jobMessage").classList.toggle("error",error); }

  function drawChart(comparisons) {
    const colors=["#758598","#a597ff","#36caaa"], all=comparisons.flatMap(r => r.curve.map(v => v.equity)).filter(Number.isFinite);
    if (!all.length) { $("equityChart").innerHTML=""; return; }
    const min=Math.min(...all,10000), max=Math.max(...all,10000), range=Math.max(max-min,10);
    const magnitude=10**Math.floor(Math.log10(range/4));
    const tick=([1,2,2.5,5,10].find(n=>n*magnitude>=range/4)||10)*magnitude;
    const lo=Math.floor(min/tick)*tick;
    const hi=Math.max(lo+tick, Math.ceil(max/tick)*tick), span=hi-lo;
    const y=v=>185-(v-lo)/span*160;
    let svg="";
    for(let i=0;i<=Math.round(span/tick);i++) {const v=lo+tick*i;svg+=`<line x1="58" x2="782" y1="${y(v)}" y2="${y(v)}" stroke="currentColor" opacity=".1"/><text x="7" y="${y(v)+4}" fill="currentColor" opacity=".6" font-size="10">${v.toLocaleString("en-US",{maximumFractionDigits:2})}</text>`;}
    comparisons.forEach((r,k)=>{const points=r.curve.map((v,i)=>`${58+i/Math.max(1,r.curve.length-1)*724},${y(v.equity)}`).join(" ");svg+=`<polyline fill="none" stroke="${colors[k]}" stroke-width="2" points="${points}"/>`;});
    const c=comparisons[0].curve;
    svg+=`<text x="58" y="211" fill="currentColor" opacity=".5" font-size="10">${esc(time(c[0].time))}</text><text x="782" y="211" text-anchor="end" fill="currentColor" opacity=".5" font-size="10">${esc(time(c.at(-1).time))}</text>`;
    $("equityChart").innerHTML=svg;
  }

  function renderResult(r) {
    if(!formDirty){
      $("labSymbol").value=r.symbol;$("labInterval").value=r.interval;
      $("labBars").value=String([1000,3000,5000].find(n=>n>=r.bars) || 5000);
      $("labFee").value=r.fee_bps;$("labSlip").value=r.slippage_bps;
    }
    document.querySelector('.signal-card > .lab-step').textContent=`SIGNAL / ${r.symbol} / ${r.interval.toUpperCase()}`;
    $("emptyResult").classList.add("hidden"); $("results").classList.remove("hidden");
    $("resultMeta").textContent=`${r.symbol} · ${r.interval.toUpperCase()} · ${r.bars} 根`;
    $("oosRows").textContent=r.oos_rows.toLocaleString(); $("accuracy").textContent=pct(r.oos_accuracy);
    $("brier").textContent=r.brier.toFixed(3); $("coverage").textContent=r.event_covered_bars;
    $("comparisonBody").innerHTML=r.comparisons.map(v=>`<tr><td>${esc(v.name)}</td><td class="${v.return>=0?"positive":"negative"}">${pct(v.return)}</td><td class="negative">${pct(v.max_drawdown)}</td><td>${v.trades}</td><td>${pct(v.win_rate)}</td><td>${v.profit_factor==null?"—":v.profit_factor.toFixed(2)}</td></tr>`).join("");
    $("resultWarnings").innerHTML=r.notes.map(n=>`<p>· ${esc(n)}</p>`).join("");
    $("foldDetails").innerHTML=r.folds.map((f,i)=>`<p>Fold ${i+1} · 训练 ${esc(time(f.train_start))} → ${esc(time(f.train_end))}（${f.train_rows} 根）<br>验证 ${esc(time(f.test_start))} → ${esc(time(f.test_end))}（${f.test_rows} 根）· 清洗间隔 ${f.purge_bars} 根</p>`).join("");
    $("featureDetails").innerHTML=r.coefficients.map(f=>`<span class="lab-feature">${esc(f.feature)} ${f.weight>=0?"+":""}${f.weight.toFixed(3)}</span>`).join("");
    const s=r.signal, stale=Date.now()-new Date(s.as_of).getTime()>(r.interval==="1h"?7200000:28800000);
    $("signalDirection").textContent=stale?"数据过期":({LONG:"候选做多",SHORT:"候选做空",WAIT:"观望 WAIT"}[s.direction]);
    $("signalDirection").className="lab-direction "+(s.direction==="LONG"?"positive":s.direction==="SHORT"?"negative":"");
    $("signalReason").textContent=stale?"行情快照已过期，请重新运行。":s.event_blocked?"事件风险否决了新的候选信号；请查看事件来源和有效时间。":s.direction==="WAIT"?"趋势或模型分数未满足固定条件，保持观望。":"趋势与模型过滤条件一致。仅供模拟验证，不会发送订单。";
    $("upScore").textContent=s.up_score.toFixed(3); $("scoreFill").style.width=`${s.up_score*100}%`;
    $("trendCheck").textContent=s.trend===1?"多头趋势":s.trend===-1?"空头趋势":"无趋势";
    $("eventCheck").textContent=s.event_blocked?"已阻止":r.event_covered_bars<30?"未触发 · 历史不足":"未触发";
    $("signalTime").textContent=time(s.as_of); drawChart(r.comparisons);
  }

  function renderEvents(events) {
    $("eventsListLab").innerHTML=events.length?events.map(e=>{
      const a=e.analysis, assets=a?.assets.join(" / ") || "待识别";
      // Links never become script URLs even if a database was edited externally.
      const safeUrl=/^https:\/\//i.test(e.url)?e.url:"#";
      return `<article class="lab-event"><div class="lab-event-meta"><span>${esc(e.source)}</span><span>发布 ${esc(time(e.published_at))}</span><span class="lab-tag">${a?esc(category[a.category]):"待 AI 解析"}</span>${a?`<span class="${a.risk==="high"?"negative":""}">${a.risk==="high"?"高风险":a.risk==="medium"?"中风险":"低风险"}</span>`:""}</div><h4><a href="${esc(safeUrl)}" target="_blank" rel="noopener noreferrer">${esc(e.title)} ↗</a></h4>${a?`<p>${esc(a.summary)}</p><p>${esc(assets)} · 方向假设 ${a.sentiment.toFixed(2)} · 提取置信度 ${pct(a.confidence)} · 有效 ${a.horizon_hours}h</p>`:"<p>尚未提取结构化特征，不参与事件风险过滤。</p>"}<p>首次采集 ${esc(time(e.received_at))} · AI 可用 ${esc(time(e.analyzed_at))}</p></article>`;
    }).join(""):"<div class='lab-empty'><p>事件库为空。采集公开 RSS，或录入带来源的事件。</p></div>";
  }

  async function refresh() {
    if(polling) return;
    polling=true;
    try {
      last=await api("/api/research/status");
      message(last.job.message,last.job.state==="error");
      $("runBtn").disabled=last.job.state==="running";
      $("runBtn").textContent=last.job.state==="running"?"正在验证…":"运行样本外验证 ↗";
      $("aiStatus").textContent=last.deepseek.configured?"DeepSeek 已配置":"DeepSeek 未连接";
      $("aiDot").style.background=last.deepseek.configured?"var(--green)":"var(--amber)";
      $("aiModel").textContent=last.deepseek.model;
      $("outlookBtn").disabled=!last.deepseek.configured;
      $("outlookBtn").title=last.deepseek.configured?"调用 DeepSeek 生成条件式多空展望，会消耗 API 额度":"先配置 DeepSeek API Key";
      $("eventMessage").textContent=last.event_message;
      $("syncBtn").disabled=last.event_busy;
      $("analyzeBtn").disabled=last.event_busy || !last.deepseek.configured;
      $("analyzeBtn").title=last.deepseek.configured?"会发送公开事件文本到 DeepSeek，消耗 API 额度":"先配置 DeepSeek API Key";
      const sig=JSON.stringify(last.events.map(e=>[e.id,e.analyzed_at]));
      if(sig!==eventStamp){eventStamp=sig;renderEvents(last.events);}
      if(last.result && last.result.generated_at!==resultStamp){resultStamp=last.result.generated_at;renderResult(last.result);}
    } catch(e) {message("研究服务连接失败："+e.message,true);} finally {polling=false;}
  }
  $("runForm").addEventListener("input",()=>{formDirty=true;});
  $("runForm").addEventListener("change",()=>{formDirty=true;});
  $("runForm").addEventListener("submit",async e=>{e.preventDefault();$("runBtn").disabled=true;try{await api("/api/research/run",{symbol:$("labSymbol").value,interval:$("labInterval").value,bars:Number($("labBars").value),fee_bps:Number($("labFee").value),slippage_bps:Number($("labSlip").value)});await refresh();}catch(err){message(err.message,true);$("runBtn").disabled=false;}});
  for(const [id,kind] of [["syncBtn","sync"],["analyzeBtn","analyze"]]) $(id).addEventListener("click",async()=>{try{await api(`/api/research/events/${kind}`,{});await refresh();}catch(e){message(e.message,true);}});
  function openSettings(){ $("apiModel").value=last?.deepseek.model || "deepseek-flash";$("settingsMessage").textContent="";$("settingsDialog").showModal(); }
  $("settingsBtn").onclick=openSettings;$("settingsBtn2").onclick=openSettings;
  $("closeSettings").onclick=()=>{$("apiKey").value="";$("settingsDialog").close();};
  $("settingsDialog").addEventListener("close",()=>{$("apiKey").value="";});
  $("settingsForm").addEventListener("submit",async e=>{e.preventDefault();try{await api("/api/research/settings",{api_key:$("apiKey").value,model:$("apiModel").value});$("apiKey").value="";$("settingsMessage").textContent="已保存到本机 .env。可关闭窗口并解析事件。";await refresh();}catch(err){$("settingsMessage").textContent=err.message;}});
  $("eventForm").addEventListener("submit",async e=>{e.preventDefault();try{const date=new Date($("eventPublished").value+":00+08:00");const r=await api("/api/research/events",{title:$("eventTitle").value,text:$("eventText").value,url:$("eventUrl").value,published_at:date.toISOString()});$("eventForm").reset();message(r.added?"事件已保存。配置 DeepSeek 后可解析。":"相同标题已存在，保留首次采集时间。");await refresh();}catch(err){message(err.message,true);}});
  $("outlookBtn").onclick=async()=>{
    const symbol=$("labSymbol").value, interval=$("labInterval").value;
    $("outlookBtn").disabled=true;$("outlookHint").textContent="正在结合行情、研究快照和本地公开事件分析…";
    try{
      const r=await api(`/api/research/outlook?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(interval)}`,{});
      const cls=r.direction==="LONG"?"outlook-long":r.direction==="SHORT"?"outlook-short":"outlook-neutral";
      const label=r.direction==="LONG"?"偏多 LONG":r.direction==="SHORT"?"偏空 SHORT":"中性 / 观望 NEUTRAL";
      $("outlookResult").innerHTML=`<div class="outlook-dir ${cls}">${label}</div><div class="outlook-meta">置信度 ${(Number(r.confidence)*100).toFixed(0)}% · 周期 ${esc(r.horizon||"—")} · 使用事件 ${r.events_used} 条 · ${esc(r.model)}</div><b>核心逻辑</b><ul>${(r.thesis||[]).map(x=>`<li>${esc(x)}</li>`).join("")}</ul><b>主要风险</b><ul>${(r.risks||[]).map(x=>`<li>${esc(x)}</li>`).join("")}</ul><b>失效条件</b><ul>${(r.invalidations||[]).map(x=>`<li>${esc(x)}</li>`).join("")}</ul><p class="outlook-note">${esc(r.event_impact||"")}<br>${esc(r.disclaimer||"")}</p>`;
      $("outlookResult").classList.remove("hidden");
      $("outlookHint").textContent="分析完成。AI 结论是条件式研究，不是下单指令。";
    }catch(err){$("outlookHint").textContent=err.message;$("outlookResult").classList.add("hidden");}
    finally{$("outlookBtn").disabled=false;}
  };
  refresh();setInterval(refresh,4000);
})();
