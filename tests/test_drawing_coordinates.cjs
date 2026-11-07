const assert=require('node:assert/strict');
global.window={};require('../btcquant/static/drawing-coordinates.js');
const c=window.BTCDrawingCoordinates;
const bars=[{time:1000},{time:1060},{time:1120}];
for(const l of [-10,-.5,0,.3,1,1.8,2,2.5,80]){
  const t=c.timeForLogical(bars,l,60);
  assert.ok(Math.abs(c.logicalForTime(bars,t,60)-l)<1e-9);
}
// A future drawing keeps its timestamp when data is appended or prepended.
const future=c.timeForLogical(bars,5.5,60);
assert.equal(c.logicalForTime([...bars,{time:1180},{time:1240}],future,60),5.5);
assert.equal(c.logicalForTime([{time:940},...bars],future,60),6.5);
const gapped=[{time:1000},{time:1060},{time:1180}];
assert.equal(c.timeForLogical(gapped,1.5,60),1120);
assert.equal(c.logicalForTime(gapped,1120,60),1.5);
assert.equal(c.timeForLogical([],0,60),null);
assert.equal(c.logicalForTime(bars,NaN,60),null);
console.log('Drawing coordinates: fractional, empty space, gaps and history changes passed.');
