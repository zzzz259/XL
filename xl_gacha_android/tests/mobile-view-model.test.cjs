'use strict';

const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const MobileView=require('../web/mobile-view-model.js');
const demo=JSON.parse(fs.readFileSync('../xl_gacha_web/static/demo.json','utf8'));
const catalog=JSON.parse(fs.readFileSync('../xl_gacha_web/data/catalog.json','utf8'));

test('overview preserves totals and derives primary and non-empty extra categories',()=>{
 const original=JSON.stringify(demo),view=MobileView.summary(demo,100);
 assert.equal(view.totalPulls,demo.summary.pull_count);
 assert.equal(view.fiveCount,demo.summary.rarity_counts['5']);
 assert.equal(view.poolCount,Object.keys(demo.summary.pool_pull_counts).length);
 const categories=Object.fromEntries(view.categories.map(item=>[item.key,item]));
 for(const key of ['normal','limited','collection','beginner']){
  assert.equal(categories[key].pullCount,demo.analysis.categories[key].pull_count);
  assert.equal(categories[key].pityExact,demo.analysis.categories[key].since_five_exact);
 }
 assert.equal(categories.normal.fiveRate,demo.analysis.categories.normal.five_count/demo.analysis.categories.normal.pull_count*100);
 assert.equal(categories.limited.guaranteedCount,demo.analysis.categories.limited.guaranteed_count);
 assert.equal(categories.unknown,undefined);
 assert.equal(JSON.stringify(demo),original);
});

test('recent five-star events are newest first and retain pool and uncertainty context',()=>{
 const view=MobileView.summary(demo,100);
 assert.equal(view.recentFive.length,demo.summary.rarity_counts['5']);
 for(let i=1;i<view.recentFive.length;i++)assert.ok(view.recentFive[i-1].timestamp>=view.recentFive[i].timestamp);
 for(const event of view.recentFive){
  assert.equal(typeof event.name,'string');
  assert.equal(typeof event.poolName,'string');
  assert.equal(typeof event.exact,'boolean');
  assert.ok(Number.isSafeInteger(event.pulls));
 }
 assert.ok(MobileView.summary(demo,3).recentFive.length<=3);
});

test('pool views filter categories and present events newest first without mutating analysis',()=>{
 const original=JSON.stringify(demo),pools=MobileView.pools(demo,'limited');
 assert.ok(pools.length>0);
 assert.ok(pools.every(pool=>pool.category==='limited'));
 for(const pool of pools){
  for(let i=1;i<pool.events.length;i++)assert.ok(pool.events[i-1].timestamp>=pool.events[i].timestamp);
  assert.equal(pool.pullCount,pool.source.pull_count);
  assert.equal(pool.pityExact,pool.source.tail_exact);
 }
 assert.equal(JSON.stringify(demo),original);
});

test('pool views retain hard-pity limits and map UP IDs from the local catalog',()=>{
 const pools=MobileView.pools(demo,'all',catalog);
 assert.ok(pools.some(pool=>pool.featuredIds.length>0));
 for(const pool of pools){
  assert.equal(pool.hardPity,pool.source.hard_pity);
  const meta=catalog.pool_meta[String(pool.poolId)];
  if(meta)assert.deepEqual(pool.featuredIds,meta.featured_ids);
 }
});

test('record cards flatten newest drops and paginate after filtering',()=>{
 const result=MobileView.records(demo,{page:2,pageSize:7});
 assert.ok(result.items.length<=7);
 assert.equal(result.page,2);
 assert.equal(result.pages,Math.ceil(result.total/7));
 for(let i=1;i<result.items.length;i++)assert.ok(result.items[i-1].timestamp>=result.items[i].timestamp);
 const five=MobileView.records(demo,{fiveOnly:true,pageSize:1000});
 assert.ok(five.total>0);
 assert.ok(five.items.every(item=>item.rarity===5));
 const quickFive=MobileView.records(demo,{rarity:'5',pageSize:1000});
 assert.equal(quickFive.total,five.total);
});

test('record search, pool filter and empty states are mobile friendly',()=>{
 const first=demo.records[0],pool=String(first.pool_id);
 const filtered=MobileView.records(demo,{pool,pageSize:1000});
 assert.ok(filtered.total>0);
 assert.ok(filtered.items.every(item=>String(item.poolId)===pool));
 const missing=MobileView.records(demo,{search:'__不存在的角色__',page:9});
 assert.equal(missing.total,0);
 assert.equal(missing.page,1);
 assert.equal(missing.pages,1);
 assert.deepEqual(missing.items,[]);
});

test('empty and unknown reports never invent live pity or statistics',()=>{
 assert.deepEqual(MobileView.records(null),{items:[],total:0,page:1,pages:1,pageSize:20});
 const empty=MobileView.summary(null);
 assert.equal(empty.totalPulls,0);
 assert.equal(empty.fiveCount,0);
 assert.deepEqual(empty.recentFive,[]);
 const report=structuredClone(demo),limited=report.analysis.categories.limited;
 limited.since_five=12;limited.since_five_exact=false;limited.next_guaranteed=null;
 const view=MobileView.summary(report).categories.find(item=>item.key==='limited');
 assert.equal(view.pityText,'≥12');
 assert.equal(view.guaranteeText,'大小保底未知');
});

test('event luck grades use the first-UP distribution and never punish guaranteed pulls',()=>{
 const event=(pulls,guaranteed=false,exact=true)=>({pulls,guaranteed,interval_exact:exact});
 assert.equal(MobileView.eventGrade(event(6),70),'lucky');
 assert.equal(MobileView.eventGrade(event(7),70),'normal');
 assert.equal(MobileView.eventGrade(event(70),70),'normal');
 assert.equal(MobileView.eventGrade(event(121),70),'normal');
 assert.equal(MobileView.eventGrade(event(122),70),'rough');
 assert.equal(MobileView.eventGrade(event(122,true),70),'normal');
 assert.equal(MobileView.eventGrade(event(122,null),70),'normal');
 assert.equal(MobileView.eventGrade(event(1,false,false),70),'normal');
 assert.equal(MobileView.eventGrade(event(122),null),'normal');
});
