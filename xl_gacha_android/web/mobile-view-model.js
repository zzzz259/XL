'use strict';

const MobileProbabilityApi=typeof MobileProbability!=='undefined'?MobileProbability:require('./mobile-probability.js');
const MobileView=(()=>{
 const primary=['normal','limited','collection'];
 const names={normal:'普通池',limited:'限定池',collection:'典藏池',beginner:'启程池',unknown:'未分类'};
 const number=value=>Number(value||0).toLocaleString('zh-CN');
 const percent=value=>`${Math.max(0,Math.min(100,value)).toFixed(1)}%`;
 function category(key,value){
  const hasRecords=Boolean(value.pull_count),limited=['limited','collection'].includes(key);
  return {key,name:value.name||names[key],pullCount:value.pull_count,fiveCount:value.five_count,
   average:value.average_five_interval,fiveRate:value.pull_count?value.five_count/value.pull_count*100:null,upCount:value.up_count,offCount:value.off_count,
   guaranteedCount:value.guaranteed_count,pity:value.since_five,pityExact:value.since_five_exact,
   pityText:hasRecords?`${value.since_five_exact?'':'≥'}${number(value.since_five)}`:'—',
   hardPity:value.hard_pity,progress:hasRecords&&value.hard_pity?percent(value.since_five/value.hard_pity*100):null,
   guaranteeText:value.next_guaranteed===true?'下个五星为大保底':limited&&value.next_guaranteed===null?'大小保底未知':'',
   hasRecords};
 }
 function summary(report,recentLimit=6){
  if(!report?.summary)return {totalPulls:0,fiveCount:0,poolCount:0,categories:[],recentFive:[]};
  const source=report.analysis?.categories||{},categories=[];
  for(const key of [...primary,'beginner','unknown'])if(primary.includes(key)||source[key]?.pull_count)categories.push(category(key,source[key]||{name:names[key]}));
  const recentFive=[];
  for(const pool of report.analysis?.pools||[])for(const event of pool.events||[])recentFive.push({
   name:event.name,itemId:event.item_id,poolId:pool.pool_id,poolName:pool.pool_name,category:pool.category,
   categoryName:pool.category_name,timestamp:event.timestamp,time:event.time_beijing,pulls:event.pulls_since_five,
   exact:event.interval_exact,outcome:event.outcome,guaranteed:event.guaranteed,inPoolPull:event.in_pool_pull,
  });
  recentFive.sort((a,b)=>b.timestamp-a.timestamp||b.poolId-a.poolId||b.inPoolPull-a.inPoolPull);
  return {totalPulls:report.summary.pull_count,fiveCount:report.summary.rarity_counts['5']||0,
   poolCount:Object.keys(report.summary.pool_pull_counts||{}).length,categories,recentFive:recentFive.slice(0,Math.max(0,recentLimit))};
 }
 function pools(report,selected='all',catalog=null){
  return (report?.analysis?.pools||[]).filter(pool=>selected==='all'||pool.category===selected).map(source=>({
   source,poolId:source.pool_id,name:source.pool_name,category:source.category,categoryName:source.category_name,
   featuredIds:catalog?.pool_meta?.[String(source.pool_id)]?.featured_ids||[],featuredNames:source.featured_names||[],firstDate:source.first_date,lastDate:source.last_date,
   pullCount:source.pull_count,fiveCount:source.five_count,pity:source.tail_since_five,pityExact:source.tail_exact,
   pityText:`${source.tail_exact?'':'≥'}${number(source.tail_since_five)}`,hardPity:source.hard_pity,
   progress:source.hard_pity?percent(source.tail_since_five/source.hard_pity*100):null,nextGuaranteed:source.next_guaranteed,
   events:[...(source.events||[])].reverse().map(event=>({...event,exact:event.interval_exact,pulls:event.pulls_since_five})),
   characters:source.characters||[],
  }));
 }
 function records(report,options={}){
  const pageSize=Math.max(1,Math.min(200,Number(options.pageSize)||20));
  if(!report?.records)return {items:[],total:0,page:1,pages:1,pageSize};
  const categories=new Map((report.analysis?.pools||[]).map(pool=>[String(pool.pool_id),pool.category]));
  const ordered=[...report.records].sort((a,b)=>b.timestamp-a.timestamp||b.index-a.index||b.record_id-a.record_id||b.pool_id-a.pool_id);
  const search=String(options.search||'').trim().toLocaleLowerCase(),pool=String(options.pool||''),rarity=String(options.rarity||'');
  const items=[];
  for(const record of ordered)for(const drop of [...record.drops].reverse()){
   const item={recordId:record.record_id,recordIndex:record.index,timestamp:record.timestamp,time:record.time_beijing,
    poolId:record.pool_id,poolName:record.pool_name,category:categories.get(String(record.pool_id))||'unknown',
    itemId:drop.item_id,name:drop.name,rarity:drop.rarity??null,count:drop.count};
   if(pool&&String(item.poolId)!==pool)continue;
   if(options.fiveOnly&&item.rarity!==5)continue;
   if(rarity==='unknown'&&item.rarity!==null)continue;
   if(rarity&&rarity!=='unknown'&&String(item.rarity)!==rarity)continue;
   if(search&&!item.name.toLocaleLowerCase().includes(search))continue;
   items.push(item);
  }
  const total=items.length,pages=Math.max(1,Math.ceil(total/pageSize)),page=Math.min(Math.max(1,Number(options.page)||1),pages);
  return {items:items.slice((page-1)*pageSize,page*pageSize),total,page,pages,pageSize};
 }
 return {summary,pools,records,number,eventGrade:MobileProbabilityApi.eventGrade};
})();

if(typeof module!=='undefined')module.exports=MobileView;
