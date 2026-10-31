'use strict';
const MobileInsights=(()=>{
 const DAY=86400,RECENT_DAYS=45;
 const primary=['normal','limited','collection'];
 const numerals=['','单','双','三','四','五','六','七','八','九','十'];
 const sort=(a,b)=>a.timestamp-b.timestamp||a.index-b.index||a.record_id-b.record_id||a.pool_id-b.pool_id;
 const key=r=>JSON.stringify([r.pool_id,r.timestamp,r.index,r.record_id]);
 const rate=ratio=>ratio<=0.2?['绝世欧皇','lucky']:ratio<=0.4?['欧气不错','good']:ratio<=0.65?['平稳前行','steady']:ratio<=0.9?['略需转运','rough']:['继续加油','quiet'];
 function evaluate(records,hardOf,emptyStatus){
  if(!records.length)return{status:emptyStatus,ratio:null,pulls:0,fiveCount:0,lowConfidence:true,label:null,tone:'quiet'};
  let pulls=0,cost=0,fiveCount=0,unknown=0;
  for(const r of records){
   let quantity=0;
   for(const d of r.drops){
    if(d.rarity===null){unknown+=d.count;continue;}
    quantity+=d.count;
    if(d.rarity===5)fiveCount+=d.count;
   }
   pulls+=quantity;cost+=quantity/hardOf(r);
  }
  const lowConfidence=pulls<100||fiveCount<3;
  if(unknown>0)return{status:'unknown',ratio:fiveCount?cost/fiveCount:null,pulls,fiveCount,lowConfidence,label:null,tone:'quiet'};
  if(pulls<30)return{status:'small',ratio:fiveCount?cost/fiveCount:null,pulls,fiveCount,lowConfidence,label:null,tone:'quiet'};
  if(!fiveCount)return{status:'zero',ratio:null,pulls,fiveCount:0,lowConfidence,label:null,tone:'quiet'};
  const ratio=cost/fiveCount,[label,tone]=rate(ratio);
  return{status:'rated',ratio,pulls,fiveCount,lowConfidence,label,tone};
 }
 function collectTags(records,pools,cats){
  const ten=new Map(),pity=new Map(),events=[];
  for(const r of records){
   let total=0,fives=0,unknown=0;
   for(const d of r.drops){total+=d.count;if(d.rarity===5)fives+=d.count;else if(d.rarity===null)unknown+=d.count;}
   if(total!==10||fives<2)continue;
   const k=unknown>0?'ten_atleast':'ten_'+fives,text=unknown>0?'十连至少双金':`十连${numerals[fives]}金`;
   const e=ten.get(k)||{key:k,text,count:0};e.count++;ten.set(k,e);
  }
  for(const p of pools.values()){
   if(!cats.includes(p.category)||!(p.hard_pity>0))continue;
   for(const ev of p.events||[]){
    if(ev.interval_exact&&ev.pulls_since_five>=p.hard_pity){const k='pity_'+p.hard_pity,text=`抽满${p.hard_pity}井`,e=pity.get(k)||{key:k,text,count:0};e.count++;pity.set(k,e);}
    events.push({timestamp:ev.timestamp||0,pool:p.pool_id,pos:ev.in_pool_pull||0,outcome:ev.outcome});
   }
  }
  events.sort((a,b)=>a.timestamp-b.timestamp||a.pool-b.pool||a.pos-b.pos);
  let up=0,off=0,maxUp=0,maxOff=0;
  for(const e of events){
   if(e.outcome==='up'){up++;off=0;}else if(e.outcome==='off'){off++;up=0;}else{up=0;off=0;}
   if(up>maxUp)maxUp=up;if(off>maxOff)maxOff=off;
  }
  const tags=[...ten.values(),...pity.values()];
  if(maxUp>=2)tags.push({key:'up_streak',text:`${maxUp}连UP`,count:1});
  if(maxOff>=2)tags.push({key:'off_streak',text:`${maxOff}连歪`,count:1});
  return tags;
 }
 function collectMulti(records){
  const groups=new Map();
  for(const r of records){const k=String(r.pool_id);if(!groups.has(k))groups.set(k,[]);groups.get(k).push(r);}
  const events=[],byPool={};let exact=0,unknown=0;
  for(const[k,list]of groups){
   list.sort(sort);let used=0;
   for(const r of list){
    let total=0,fives=0,unknownDrops=0,position=used;const positions=[],characters=[];
    for(const d of r.drops){
     if(d.rarity===5){for(let i=1;i<=d.count;i++){positions.push(position+i);characters.push(d.name);}fives+=d.count;}
     else if(d.rarity===null)unknownDrops+=d.count;
     total+=d.count;position+=d.count;
    }
    used=position;
    if(total!==10||fives<2)continue;
    const event={pool_id:r.pool_id,record_id:r.record_id,timestamp:r.timestamp,time_beijing:r.time_beijing,five_count:fives,exact:unknownDrops===0,positions,characters};
    events.push(event);(byPool[k]||(byPool[k]=[])).push(event);
    if(unknownDrops)unknown++;else exact++;
   }
  }
  events.sort((a,b)=>a.timestamp-b.timestamp||a.record_id-b.record_id||a.pool_id-b.pool_id);
  return{count:events.length,counts:{exact,unknown},events,byPool};
 }
 function analyze(report,options={}){
  const opts=options||{},now=opts.now??Date.now()/1000,category=opts.category||'all';
  const cats=category==='all'?primary:primary.includes(category)?[category]:[];
  const pools=new Map((report?.analysis?.pools||[]).map(p=>[String(p.pool_id),p]));
  const hardOf=r=>pools.get(String(r.pool_id))?.hard_pity??null;
  const seen=new Set(),records=[];
  for(const r of report?.records||[]){
   const k=key(r);if(seen.has(k))continue;seen.add(k);
   const p=pools.get(String(r.pool_id));
   if(p&&cats.includes(p.category)&&p.hard_pity>0)records.push(r);
  }
  records.sort(sort);
  const career=evaluate(records,hardOf,'empty');
  const cutoff=now-RECENT_DAYS*DAY;
  const recent={...evaluate(records.filter(r=>r.timestamp<=now&&r.timestamp>=cutoff),hardOf,'excluded'),days:RECENT_DAYS};
  return{career,recent,tags:collectTags(records,pools,cats),multi:collectMulti(records)};
 }
 return{analyze};
})();
if(typeof module!=='undefined')module.exports=MobileInsights;
