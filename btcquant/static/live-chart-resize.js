/* Resize the outer live iframe; inner plots already observe their containers. */
(() => {
  "use strict";
  window.BTCLiveChartResize = function (panel) {
    const frame = panel.querySelector('iframe');
    if (!frame || panel.querySelector('.live-chart-resizer')) return;
    const key = 'btcquant.liveChartHeight.v1.' + location.pathname;
    const min = 360, max = 1400;
    const handle = document.createElement('div');
    handle.className = 'live-chart-resizer';
    handle.tabIndex = 0;
    handle.setAttribute('role','separator');
    handle.setAttribute('aria-orientation','horizontal');
    handle.setAttribute('aria-label','调整实盘图表高度');
    handle.setAttribute('aria-valuemin',String(min));
    handle.setAttribute('aria-valuemax',String(max));
    handle.title = '向下拖动放大图表；方向键调整；双击恢复默认高度';
    handle.textContent = '拖动调整图表高度 · 双击恢复';
    panel.append(handle);
    const keep = value => { try { value == null ? localStorage.removeItem(key) : localStorage.setItem(key,String(value)); } catch (_) {} };
    const size = () => Math.round(frame.getBoundingClientRect().height);
    const describe = height => { handle.setAttribute('aria-valuenow',String(height)); handle.setAttribute('aria-valuetext',height+' 像素'); };
    const apply = height => {
      const next = Math.round(Math.max(min,Math.min(max,height)));
      frame.style.height = next+'px';
      describe(next); keep(next);
    };
    try { const saved=Number(localStorage.getItem(key)); if(saved>=min&&saved<=max)apply(saved); } catch (_) {}
    describe(size());
    let drag = null;
    const end = event => {
      if (!drag || (event.pointerId != null && event.pointerId !== drag.id)) return;
      const id=drag.id;
      drag=null;
      frame.style.removeProperty('pointer-events');
      panel.classList.remove('live-chart-dragging');
      if (handle.hasPointerCapture?.(id)) handle.releasePointerCapture(id);
    };
    handle.addEventListener('pointerdown',event=>{
      if(event.button!==0)return;
      event.preventDefault();
      drag={id:event.pointerId,y:event.clientY,height:size()};
      handle.setPointerCapture(event.pointerId);
      frame.style.pointerEvents='none';
      panel.classList.add('live-chart-dragging');
    });
    handle.addEventListener('pointermove',event=>{if(drag&&event.pointerId===drag.id)apply(drag.height+event.clientY-drag.y);});
    for(const name of ['pointerup','pointercancel','lostpointercapture'])handle.addEventListener(name,end);
    handle.addEventListener('keydown',event=>{
      if(!['ArrowUp','ArrowDown','Home'].includes(event.key))return;
      event.preventDefault();
      if(event.key==='Home')reset(); else apply(size()+(event.key==='ArrowUp'?-24:24));
    });
    function reset(){frame.style.removeProperty('height');keep(null);describe(size());}
    handle.addEventListener('dblclick',reset);
    window.addEventListener('blur',()=>{if(drag)end({pointerId:drag.id});});
  };
})();
