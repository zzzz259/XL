'use strict';

const MobileProbability=(()=>{
 function rate(k){return k<60?0.02:k<70?0.07+0.05*(k-60):1;}
 function distribution(maxPulls=140){
  let a=new Map([[0,1]]),b=new Map(),rows=[],cumulative=0;
  for(let pull=1;pull<=maxPulls;pull++){
   const nextA=new Map(),nextB=new Map();let probability=0;
   for(const [k,value] of a){const r=rate(k);probability+=value*r*0.5;nextB.set(0,(nextB.get(0)||0)+value*r*0.5);if(k<70)nextA.set(k+1,(nextA.get(k+1)||0)+value*(1-r));}
   for(const [k,value] of b){const r=rate(k);probability+=value*r;if(k<70)nextB.set(k+1,(nextB.get(k+1)||0)+value*(1-r));}
   a=nextA;b=nextB;cumulative+=probability;rows.push({pull,probability,cumulative});
  }
  return rows;
 }
 const rows=distribution(),thresholds={luckyMax:0,unluckyMin:140};
 for(const row of rows){if(row.cumulative>=0.05&&thresholds.luckyMax===0)thresholds.luckyMax=row.pull;if(thresholds.unluckyMin===140&&row.cumulative>=0.95)thresholds.unluckyMin=row.pull;}
 function eventGrade(event,hardPity,category='limited'){
  if(!event?.interval_exact||!['limited','collection'].includes(category)||hardPity!==70)return 'normal';
  if(event.pulls<=thresholds.luckyMax)return 'lucky';
  if(event.pulls>=thresholds.unluckyMin&&event.guaranteed===false)return 'rough';
  return 'normal';
 }
 return {distribution,eventGrade,thresholds};
})();

if(typeof module!=='undefined')module.exports=MobileProbability;
