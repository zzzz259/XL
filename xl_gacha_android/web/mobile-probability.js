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
 const recordSort=(a,b)=>a.timestamp-b.timestamp||a.index-b.index||a.record_id-b.record_id||a.pool_id-b.pool_id;
 function eventTimeline(report,options={}){
  const selected=options.poolIds==null?null:new Set([...options.poolIds].map(String));
  const categories=options.categories||['limited','collection'],positions=new Map(),order=new Map();
  // Events retain their in-pool pull position but not record index. Recover source order for same-second cross-pool draws.
  for(const [rank,record] of [...(report?.records||[])].sort(recordSort).entries()){
   const id=String(record.pool_id);let position=positions.get(id)||0;
   for(const drop of record.drops||[]){
    if(drop.rarity===5)for(let n=1;n<=drop.count;n++)order.set(`${id}:${position+n}`,rank);
    position+=drop.count;
   }
   positions.set(id,position);
  }
  const events=[];
  for(const pool of report?.analysis?.pools||[]){
   if(!categories.includes(pool.category)||!['limited','collection'].includes(pool.category))continue;
   const id=String(pool.pool_id),group=pool.pity_group||report.pool_metadata?.[id]?.pity_group||pool.category;
   for(const event of pool.events||[])events.push({event,pool,group:`${pool.category}:${group}`,selected:!selected||selected.has(id),eligible:pool.hard_pity>0,order:order.get(`${id}:${event.in_pool_pull}`)});
  }
  return events.sort((a,b)=>(a.event.timestamp||0)-(b.event.timestamp||0)||
   (a.order!==undefined&&b.order!==undefined?a.order-b.order:0)||a.pool.pool_id-b.pool.pool_id||(a.event.in_pool_pull||0)-(b.event.in_pool_pull||0));
 }
 function upCycles(report,options={}){
  const pending=new Map(),cycles=new Map();
  for(const {event,pool,group,selected,eligible} of eventTimeline(report,options)){
   if(!selected||!eligible){pending.delete(group);continue;}
   const pulls=Number(event.pulls_since_five??event.pulls),valid=event.interval_exact===true&&Number.isSafeInteger(pulls)&&pulls>0&&pulls<=pool.hard_pity;
   if(!valid||event.guarantee_conflict)pending.delete(group);
   if(event.outcome==='off'){
    pending.set(group,{pulls,valid:valid&&!event.guarantee_conflict,hardPity:pool.hard_pity});continue;
   }
   if(event.outcome!=='up'){pending.delete(group);continue;}
   const before=pending.get(group);let cost=null;
   if(valid&&!event.guarantee_conflict){
    if(event.guaranteed===false&&!before)cost=pulls;
    else if(event.guaranteed===true&&before?.valid&&before.hardPity===pool.hard_pity)cost=before.pulls+pulls;
   }
   cycles.set(event,{up_cycle_pulls:cost,up_cycle_exact:cost!==null});
   pending.delete(group);
  }
  return cycles;
 }
 function eventGrade(event,hardPity,category='limited'){
  if(event?.outcome!=='up'||!event.interval_exact||event.guarantee_conflict||!['limited','collection'].includes(category)||hardPity!==70)return 'normal';
  const interval=Number(event.pulls_since_five??event.pulls);
  if(!Number.isSafeInteger(interval)||interval<1||interval>hardPity)return 'normal';
  const cost=event.up_cycle_exact===true?Number(event.up_cycle_pulls):event.up_cycle_exact===undefined&&event.guaranteed===false?interval:NaN;
  if(!Number.isSafeInteger(cost)||cost<1||cost>hardPity*2)return 'normal';
  if(cost<=thresholds.luckyMax)return 'lucky';
  if(cost>=thresholds.unluckyMin)return 'rough';
  return 'normal';
 }
 return {distribution,eventGrade,thresholds,eventTimeline,upCycles};
})();

if(typeof module!=='undefined')module.exports=MobileProbability;
