'use strict';

const {test}=require('node:test');
const assert=require('node:assert/strict');
const MobileProbability=require('../web/mobile-probability.js');

test('first-UP distribution covers two pity cycles and preserves the documented boundaries',()=>{
 const rows=MobileProbability.distribution();
 assert.equal(rows.length,140);
 assert.ok(rows.at(-1).cumulative>0.999);
 assert.equal(rows[0].probability,0.01);
 assert.ok(Math.abs(rows[2].cumulative-0.029996)<1e-9);
 assert.ok(Math.abs(rows[69].cumulative-0.72502305)<1e-8);
 assert.equal(MobileProbability.thresholds.luckyMax,6);
 assert.equal(MobileProbability.thresholds.unluckyMin,122);
});

test('event grades require UP endpoints and complete valid UP cycles',()=>{
 const event=(pulls,guaranteed=false,exact=true)=>({pulls,guaranteed,interval_exact:exact,outcome:'up'});
 assert.equal(MobileProbability.eventGrade(event(6),70),'lucky');
 assert.equal(MobileProbability.eventGrade(event(7),70),'normal');
 assert.equal(MobileProbability.eventGrade(event(70),70),'normal');
 assert.equal(MobileProbability.eventGrade(event(121),70),'normal');
 assert.equal(MobileProbability.eventGrade(event(122),70),'normal','a single five-star interval cannot exceed hard pity');
 assert.equal(MobileProbability.eventGrade(event(122,true),70),'normal');
 assert.equal(MobileProbability.eventGrade(event(122,null),70),'normal');
 assert.equal(MobileProbability.eventGrade(event(1,false,false),70),'normal');
 assert.equal(MobileProbability.eventGrade(event(122),null),'normal');
 assert.equal(MobileProbability.eventGrade(event(2),70,'normal'),'normal');
 assert.equal(MobileProbability.eventGrade({...event(2),outcome:'off'},70),'normal');
 assert.equal(MobileProbability.eventGrade({...event(2,true),up_cycle_pulls:64,up_cycle_exact:true},70),'normal');
 assert.equal(MobileProbability.eventGrade({...event(61,true),up_cycle_pulls:122,up_cycle_exact:true},70),'rough');
 assert.equal(MobileProbability.eventGrade({...event(61,true),up_cycle_pulls:122,up_cycle_exact:false},70),'normal');
});

test('real engine events reach both grade thresholds through valid same-stream UP cycles',()=>{
 const engine=require('../web/mobile-engine.js');
 const catalog={version:'test',cards:{1:['低星',3],2:['UP',5],3:['歪',5]},pools:{10:'限定一',11:'限定二',20:'普通',30:'典藏'},pool_meta:{
  10:{category:'limited',pity_group:'limited',featured_ids:[2],hard_pity:70},11:{category:'limited',pity_group:'limited',featured_ids:[2],hard_pity:70},
  20:{category:'normal',pity_group:'normal',featured_ids:[],hard_pity:70},30:{category:'collection',pity_group:'collection',featured_ids:[2],hard_pity:70}}};
 const row=(pool_id,index,timestamp,item_id,count=1)=>({pool_id,index,timestamp,record_id:index,drops:[{item_id,count}]});
 const rows=[row(10,1,1,2),{...row(10,2,2,1,5),drops:[{item_id:1,count:5},{item_id:2,count:1}]},
  {...row(10,3,3,1,60),drops:[{item_id:1,count:60},{item_id:3,count:1}]},row(20,1,4,2),
  {...row(11,4,5,1,60),drops:[{item_id:1,count:60},{item_id:2,count:1}]}];
 const source=engine.buildReport(rows,catalog,{}),snapshot=JSON.stringify(source);
 const cycles=MobileProbability.upCycles(source);
 const events=source.analysis.pools.flatMap(pool=>pool.events.map(event=>({pool,event})));
 const grades=events.map(({pool,event})=>MobileProbability.eventGrade({...event,...cycles.get(event),pulls:event.pulls_since_five},pool.hard_pity,pool.category));
 assert.equal(grades.filter(grade=>grade==='lucky').length,1);
 assert.equal(grades.filter(grade=>grade==='rough').length,1);
 const rough=events.find(({pool})=>pool.pool_id===11).event;
 assert.equal(rough.pulls_since_five,61);assert.equal(rough.guaranteed,true);
 assert.deepEqual(cycles.get(rough),{up_cycle_pulls:122,up_cycle_exact:true});
 assert.equal(MobileProbability.upCycles(source,{poolIds:[11]}).get(rough).up_cycle_exact,false,'excluded predecessor pool separates the cycle');
 assert.equal(JSON.stringify(source),snapshot);
});

test('UP cycles never cross independent categories, unknown outcomes or incomplete intervals',()=>{
 const event=(timestamp,outcome,pulls,exact=true,guaranteed=false)=>({timestamp,outcome,pulls_since_five:pulls,interval_exact:exact,guaranteed});
 for(const blocker of ['category','unknown','gap','conflict']){
  const off=event(1,'off',65),up=event(3,'up',65,true,true);
  const pools=[{pool_id:1,category:'limited',hard_pity:70,events:[off]}];
  if(blocker==='unknown')pools[0].events.push(event(2,'unknown',1));
  if(blocker==='gap')up.interval_exact=false;
  if(blocker==='conflict')up.guarantee_conflict=true;
  pools.push({pool_id:2,category:blocker==='category'?'collection':'limited',hard_pity:70,events:[up]});
  const cycles=MobileProbability.upCycles({analysis:{pools}});
  assert.equal(cycles.get(up).up_cycle_exact,false,blocker);
 }
});
