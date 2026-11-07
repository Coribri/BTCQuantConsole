(() => {
  "use strict";
  if (document.documentElement.dataset.accountMode === "live") return;

  const $=id=>document.getElementById(id), sidebar=document.querySelector(".sidebar");
  const isFutures=!!$("fSubmitBtn"), isGrid=!!$("gStartBtn");
  const symbol=window.BTCSymbols?.current || "BTCUSDT";
  const num=v=>Number(v || 0), money=v=>Number.isFinite(v)?v.toLocaleString("en-US",{maximumFractionDigits:2}):"—";
  const order=sidebar?.querySelector(".order-card") || $("gStartBtn")?.closest("section");
  const account=sidebar?.querySelector(".account-card") || sidebar?.firstElementChild;
  if(order && sidebar) sidebar.prepend(order);
  if($("fCalc")) $("fCalc").open=false;
  if(sidebar) sidebar.querySelectorAll("section").forEach(section=>{if(section.querySelector("#fAutoToggle,#modelSeg"))section.classList.add("strategy-card");});
  document.querySelectorAll(".main-nav a").forEach(a=>{const u=new URL(a.href);u.searchParams.set("symbol",symbol);a.href=u.pathname+u.search;});

  const strip=document.createElement("div");strip.className="workspace-strip";
  const mode=isFutures?"合约模拟 · 现货标记价":isGrid?"现货网格 · 本地模拟":"现货 · 执行模式见右上角";
  const context=document.createElement("span");const label=document.createElement("span");label.className="context-label";label.textContent="WORKSPACE";context.append(label,mode);
  const actions=document.createElement("span"), marketBtn=document.createElement("button"), link=document.createElement("a");
  marketBtn.className="workspace-market-btn workspace-mobile-market";marketBtn.textContent="行情与资金流";marketBtn.setAttribute("aria-expanded","false");
  link.textContent="事件 × ML 研究 →";link.href=`/research?symbol=${symbol}`;link.style.marginLeft="16px";
  actions.append(marketBtn,link);strip.append(context,actions);document.querySelector(".topbar")?.after(strip);
  marketBtn.onclick=()=>{const rail=document.querySelector(".market-rail");if(rail){const open=rail.classList.toggle("workspace-market-open");marketBtn.setAttribute("aria-expanded",String(open));if(open)rail.scrollIntoView({block:"nearest",behavior:"smooth"});}};
  document.querySelectorAll(".form-field").forEach(field=>{const label=field.querySelector("label"),input=field.querySelector("input[id],select[id]");if(label && input)label.htmlFor=input.id;});
  document.querySelectorAll(".icon-btn[title]").forEach(b=>b.setAttribute("aria-label",b.title));
  if(isFutures){
    const risk=document.createElement("div");risk.className="workspace-risk";$("fSubmitBtn").closest(".form-actions").before(risk);
    function update(){
      const n=num($("fNotional").value), lev=Math.max(1,num($("fLeverageRange").value)), sl=num($("fSl").value);
      const short=$("fSideToggle").querySelector(".active")?.dataset.side==="SHORT";
      const text=$("fQtyHint").textContent, priceText=text.split("@")[1]?.split("USDT")[0]?.replace(/,/g,"").trim();
      const p=$("fOrderType").value==="LIMIT"?num($("fLimit").value):num(priceText);
      const loss=p>0 && sl>0?n*Math.abs(p-sl)/p:null;
      risk.replaceChildren();
      for(const [k,v] of [["预计初始保证金",money(n/lev)+" USDT"],["开仓手续费 · 0.05%",money(n*.0005)+" USDT"],["止损价差损失 · 未含滑点",loss==null?"未设置止损":money(loss)+" USDT"]]){const row=document.createElement("div"),b=document.createElement("b");row.textContent=k;b.textContent=v;row.append(b);risk.append(row);}
      if(!sl || lev>10 || (p>0&&sl>0&&(short?sl<=p:sl>=p))){const warn=document.createElement("p");warn.className="risk-warning";warn.style.margin="7px 0 0";warn.textContent=!sl?"未设置止损，请先确认风险。":lev>10?"高杠杆：价格小幅波动也可能造成较大损失。":"止损方向与开仓方向不匹配。";risk.append(warn);}
    }
    sidebar.addEventListener("input",update);sidebar.addEventListener("change",update);$("fSideToggle").addEventListener("click",()=>setTimeout(update,0));setInterval(update,2000);update();
  }
  if(isGrid){
    const preview=document.createElement("div");preview.className="grid-preview";$("gStartBtn").closest(".form-actions").before(preview);
    function update(){const lo=num($("gLower").value),hi=num($("gUpper").value),grids=num($("gGrids").value),initial=num($("gInitial").value)/100,stop=num($("gStop").value)/100;
      preview.replaceChildren();const rows=hi>lo&&grids>0?[["单格间距",$("gMode").value==="geometric"?((Math.pow(hi/lo,1/grids)-1)*100).toFixed(2)+"%":money((hi-lo)/grids)+" USDT"],["初始仓位比例",(initial*100).toFixed(0)+"%"],["下边界止损价",money(lo*(1-stop))+" USDT"]]:[["参数提示","上边界必须高于下边界"]];for(const[k,v]of rows){const d=document.createElement("div"),b=document.createElement("b");d.textContent=k;b.textContent=v;d.append(b);preview.append(d);}}
    sidebar.addEventListener("input",update);sidebar.addEventListener("change",update);setInterval(update,3000);update();
  }
})();
