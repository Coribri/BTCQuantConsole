/* Fractional timestamp anchors work in empty future/past chart space and
   survive newly appended candles or a different history window. */
(() => {
  "use strict";
  const seconds = interval => { const m=/^(\d+)([smhd])$/.exec(interval); return m?Number(m[1])*({s:1,m:60,h:3600,d:86400}[m[2]]):3600; };
  function logicalForTime(bars,time,step){
    if(!bars?.length || !Number.isFinite(time)) return null;
    if(time<=bars[0].time) return (time-bars[0].time)/step;
    const n=bars.length-1;
    if(time>=bars[n].time) return n+(time-bars[n].time)/step;
    let lo=0,hi=n;
    while(hi-lo>1){const mid=(lo+hi)>>1;if(bars[mid].time<=time)lo=mid;else hi=mid;}
    return lo+(time-bars[lo].time)/(bars[hi].time-bars[lo].time);
  }
  function timeForLogical(bars,logical,step){
    if(!bars?.length || !Number.isFinite(logical)) return null;
    if(logical<=0) return bars[0].time+logical*step;
    const n=bars.length-1;
    if(logical>=n) return bars[n].time+(logical-n)*step;
    const i=Math.floor(logical);return bars[i].time+(logical-i)*(bars[i+1].time-bars[i].time);
  }
  window.BTCDrawingCoordinates={seconds,logicalForTime,timeForLogical,
    toX(chart,bars,interval,time){const l=logicalForTime(bars,Number(time),seconds(interval));return l===null?null:chart.timeScale().logicalToCoordinate(l);},
    fromX(chart,bars,interval,x){return timeForLogical(bars,chart.timeScale().coordinateToLogical(x),seconds(interval));}
  };
})();
