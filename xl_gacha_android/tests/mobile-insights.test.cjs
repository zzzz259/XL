const {test}=require('node:test'); const assert=require('node:assert/strict');
const MobileInsights=require('../web/mobile-insights.js'); const engine=require('../web/mobile-engine.js');

const DAY=86400,NOW=1780000000;
const drop=(item_id,rarity,count=1,name='测试')=>({item_id,name,rarity,count});
const record=(pool_id,timestamp,drops,index=1,record_id=1)=>({pool_id,pool_name:'测试池',timestamp,index,record_id,time_beijing:String(timestamp),drops});
const poolInfo=(pool_id,category,hard_pity,events=[])=>({pool_id,pool_name:'测试池',category,category_name:category,hard_pity,events});
const report=(records,pools)=>({schema_version:1,records,analysis:{version:2,pools}});
const ev=(timestamp,in_pool_pull,outcome,interval_exact=true,pulls_since_five=in_pool_pull)=>({timestamp,in_pool_pull,outcome,interval_exact,pulls_since_five});
const careerOf=(pulls,fives,hard_pity=70)=>MobileInsights.analyze(report(
 [record(1,1000,[...Array.from({length:fives},()=>drop(12,5,1,'金')),drop(11,3,pulls-fives)])],
 [poolInfo(1,'normal',hard_pity)])).career;
const tag=(tags,key)=>tags.find(t=>t.key===key);

test('career covers all visible history while recent keeps the inclusive 45-day floor and drops future records',()=>{
 const ten=()=>[drop(11,3,10)];
 const records=[record(1,NOW-46*DAY,ten()),record(1,NOW-45*DAY,ten()),record(1,NOW-10*DAY,ten()),record(1,NOW+DAY,ten())];
 const {career,recent}=MobileInsights.analyze(report(records,[poolInfo(1,'normal',70)]),{now:NOW});
 assert.equal(career.pulls,40);assert.equal(career.status,'zero');assert.equal(career.fiveCount,0);
 assert.equal(recent.pulls,20);assert.equal(recent.status,'small');assert.equal(recent.days,45);
});

test('career and recent only count normal limited collection pools with hard_pity above zero',()=>{
 const pools=[poolInfo(1,'normal',70),poolInfo(2,'beginner',null),poolInfo(3,'unknown',70),poolInfo(4,'normal',0),poolInfo(5,'limited',70),poolInfo(6,'collection',80)];
 const records=[1,2,3,4,5,6].map(id=>record(id,1000,[drop(11,3,10)]));
 const {career,tags,multi}=MobileInsights.analyze(report(records,pools),{now:2000});
 assert.equal(career.pulls,30);assert.equal(career.status,'zero');
 assert.deepEqual(tags,[]);assert.equal(multi.count,0);
});

test('unknown rarity forces status unknown and recent window can isolate confirmed records',()=>{
 const records=[record(1,NOW-100*DAY,[drop(11,3,13),drop(999,null,2)]),record(1,NOW-10*DAY,[drop(11,3,19),drop(12,5,1,'金')])];
 const {career,recent}=MobileInsights.analyze(report(records,[poolInfo(1,'normal',70)]),{now:NOW});
 assert.equal(career.status,'unknown');assert.equal(career.pulls,33);assert.equal(career.fiveCount,1);
 assert.equal(recent.status,'small');assert.equal(recent.pulls,20);assert.equal(recent.fiveCount,1);
});

test('career reports empty and recent excluded without eligible records',()=>{
 const {career,recent,tags,multi}=MobileInsights.analyze(report([],[poolInfo(1,'normal',70)]),{now:NOW});
 assert.equal(career.status,'empty');assert.equal(recent.status,'excluded');assert.equal(recent.days,45);
 assert.deepEqual(tags,[]);assert.deepEqual(multi,{count:0,counts:{exact:0,unknown:0},events:[],byPool:{}});
});

test('small zero and lowConfidence statuses follow pull and five star counts',()=>{
 const small=careerOf(20,1);assert.equal(small.status,'small');assert.equal(small.lowConfidence,true);
 const zero=careerOf(40,0);assert.equal(zero.status,'zero');assert.equal(zero.ratio,null);assert.equal(zero.lowConfidence,true);
 const weak=careerOf(120,2);assert.equal(weak.status,'rated');assert.equal(weak.lowConfidence,true);assert.equal(weak.label,'略需转运');
 const steady=careerOf(120,5);assert.equal(steady.status,'rated');assert.equal(steady.lowConfidence,false);assert.equal(steady.label,'欧气不错');assert.equal(steady.tone,'good');
 const edge=careerOf(99,5);assert.equal(edge.status,'rated');assert.equal(edge.lowConfidence,true);
});

test('rating labels and tones follow the ratio thresholds exactly',()=>{
 const cases=[[70,70,5,0.2,'绝世欧皇','lucky'],[50,40,2,0.4,'欧气不错','good'],[50,65,2,0.65,'平稳前行','steady'],[50,90,2,0.9,'略需转运','rough'],[50,92,2,0.92,'继续加油','quiet']];
 for(const[hard_pity,pulls,fives,ratio,label,tone]of cases){
  const career=careerOf(pulls,fives,hard_pity);
  assert.equal(career.status,'rated');assert.ok(Math.abs(career.ratio-ratio)<1e-9);assert.equal(career.label,label);assert.equal(career.tone,tone);
 }
});

test('ten pull kinds count by kind with unknown rarity as a lower bound and multi exposes positions',()=>{
 const drops2=()=>[drop(11,3,6),drop(12,5,1,'金A'),drop(13,5,1,'金B'),drop(11,3,2)];
 const records=[
  record(7,1000000,drops2()),record(7,1000010,[drop(11,3,7),drop(12,5,1,'金A'),drop(13,5,1,'金B'),drop(14,5,1,'金C')]),
  record(7,1000020,[drop(11,3,7),drop(12,5,1,'金A'),drop(13,5,1,'金B'),drop(999,null,1)]),
  record(7,1000030,[drop(11,3,9),drop(12,5,1,'金A')]),record(7,1000040,drops2()),
 ];
 const {tags,multi}=MobileInsights.analyze(report(records,[poolInfo(7,'limited',70)]),{now:2000000});
 assert.equal(tag(tags,'ten_2').text,'十连双金');assert.equal(tag(tags,'ten_2').count,2);
 assert.equal(tag(tags,'ten_3').text,'十连三金');assert.equal(tag(tags,'ten_3').count,1);
 assert.equal(tag(tags,'ten_atleast').text,'十连至少双金');assert.equal(tag(tags,'ten_atleast').count,1);
 assert.equal(multi.count,4);assert.deepEqual(multi.counts,{exact:3,unknown:1});
 const[e1,e2,e3,e4]=multi.events;
 assert.equal(e1.five_count,2);assert.equal(e1.exact,true);assert.deepEqual(e1.positions,[7,8]);assert.deepEqual(e1.characters,['金A','金B']);
 assert.deepEqual(e2.positions,[18,19,20]);assert.equal(e2.five_count,3);
 assert.equal(e3.exact,false);assert.deepEqual(e3.positions,[28,29]);
 assert.deepEqual(e4.positions,[47,48]);
 assert.equal(multi.byPool['7'].length,4);
 assert.deepEqual(multi.events.map(e=>e.timestamp),[1000000,1000010,1000020,1000040]);
});

test('non ten pull records never merge into a ten pull multi',()=>{
 const records=[record(8,1000,[drop(11,3,4),drop(12,5,1,'金')]),record(8,2000,[drop(11,3,4),drop(12,5,1,'金')]),record(8,3000,[drop(11,3,9),drop(12,5,1,'金'),drop(13,5,1,'金'),drop(14,5,1,'金')])];
 const {tags,multi}=MobileInsights.analyze(report(records,[poolInfo(8,'limited',70)]),{now:5000});
 assert.equal(tag(tags,'ten_2'),undefined);assert.equal(multi.count,0);
});

test('hard pity hits count per well size only with exact intervals',()=>{
 const pools=[poolInfo(9,'limited',70,[ev(100,70,'up'),ev(200,71,'off'),ev(300,70,'up',false),ev(400,140,'up')]),poolInfo(10,'collection',80,[ev(500,80,'up')])];
 const {tags}=MobileInsights.analyze(report([],pools),{now:1000});
 assert.equal(tag(tags,'pity_70').text,'抽满70井');assert.equal(tag(tags,'pity_70').count,3);
 assert.equal(tag(tags,'pity_80').text,'抽满80井');assert.equal(tag(tags,'pity_80').count,1);
});

test('longest up and off streaks across pools become streak tags only at length two or more',()=>{
 const pools=[poolInfo(11,'limited',70,[ev(1,1,'up'),ev(2,2,'up'),ev(3,3,'off'),ev(4,4,'up'),ev(5,5,'off'),ev(6,6,'off'),ev(7,7,'off')])];
 const {tags}=MobileInsights.analyze(report([],pools),{now:100});
 assert.equal(tag(tags,'up_streak').text,'2连UP');assert.equal(tag(tags,'up_streak').count,1);
 assert.equal(tag(tags,'off_streak').text,'3连歪');assert.equal(tag(tags,'off_streak').count,1);
 const broken=MobileInsights.analyze(report([],[poolInfo(12,'normal',70,[ev(1,1,'up'),ev(2,2,'up'),ev(3,3,'normal'),ev(4,4,'up'),ev(5,5,'up')])]),{now:100});
 assert.equal(tag(broken.tags,'up_streak').text,'2连UP');assert.equal(tag(broken.tags,'off_streak'),undefined);
 const singles=MobileInsights.analyze(report([],[poolInfo(13,'limited',70,[ev(1,1,'up'),ev(2,2,'off'),ev(3,3,'up')])]),{now:100});
 assert.equal(tag(singles.tags,'up_streak'),undefined);assert.equal(tag(singles.tags,'off_streak'),undefined);
 const cross=MobileInsights.analyze(report([],[poolInfo(14,'limited',70,[ev(10,1,'up')]),poolInfo(15,'limited',70,[ev(20,1,'up')])]),{now:100});
 assert.equal(tag(cross.tags,'up_streak').text,'2连UP');
});

test('duplicate records dedupe by pool timestamp index and record id keeping the first copy',()=>{
 const first=record(1,1000,[drop(11,3,6),drop(12,5,1,'金'),drop(13,5,1,'金'),drop(11,3,2)],5,9);
 const rep=report([first,{...first},{...first,drops:[drop(11,3,10)]}],[poolInfo(1,'normal',70)]);
 const {career,multi}=MobileInsights.analyze(rep,{now:2000});
 assert.equal(career.pulls,10);assert.equal(multi.count,1);assert.deepEqual(multi.events[0].positions,[7,8]);
});

test('category option narrows every section to that category',()=>{
 const records=[record(20,1000,[drop(11,3,9),drop(12,5,1,'金')]),record(21,2000,[drop(11,3,9),drop(12,5,1,'金')])];
 const pools=[poolInfo(20,'normal',70),poolInfo(21,'limited',70)];
 const all=MobileInsights.analyze(report(records,pools),{now:3000});
 assert.equal(all.career.pulls,20);assert.equal(all.career.fiveCount,2);
 const limited=MobileInsights.analyze(report(records,pools),{now:3000,category:'limited'});
 assert.equal(limited.career.pulls,10);assert.equal(limited.career.fiveCount,1);
 assert.equal(limited.multi.count,0);assert.equal(tag(limited.tags,'ten_2'),undefined);
});

const catalog={version:'test',_revision:'test',cards:{1001:['低星',3],1002:['普通五星',5],1003:['限定五星',5]},
 pools:{1001:'普通池',1002:'限定池',1003:'启程池'},
 pool_meta:{
  '1001':{category:'normal',pity_group:'normal',featured_ids:[],featured_names:[],hard_pity:70},
  '1002':{category:'limited',pity_group:'limited',featured_ids:[1003],featured_names:['限定五星'],hard_pity:70},
  '1003':{category:'beginner',pity_group:'beginner',featured_ids:[],featured_names:[],hard_pity:null}}};

test('engine built report agrees on career recent tags multi and excludes beginner and unknown pools',()=>{
 const rows=[
  {pool_id:1001,timestamp:NOW-100*DAY,index:1,record_id:1001,drops:[{item_id:1001,count:35}]},
  {pool_id:1001,timestamp:NOW-10*DAY,index:2,record_id:1001,drops:[{item_id:1001,count:62},{item_id:1002,count:1},{item_id:1002,count:1},{item_id:1001,count:6}]},
  {pool_id:1002,timestamp:NOW-5*DAY,index:1,record_id:1002,drops:[{item_id:1001,count:6},{item_id:1003,count:1},{item_id:1003,count:1},{item_id:1001,count:2}]},
  {pool_id:1003,timestamp:NOW-5*DAY,index:1,record_id:1003,drops:[{item_id:1001,count:8},{item_id:1002,count:1},{item_id:1002,count:1}]},
  {pool_id:7777,pool_name:'未知池',timestamp:NOW-5*DAY,index:1,record_id:7777,drops:[{item_id:1001,count:10}]},
 ];
 const source=engine.buildReport(rows,catalog,{});
 const {career,recent,tags,multi}=MobileInsights.analyze(source,{now:NOW});
 assert.equal(career.pulls,115);assert.equal(career.fiveCount,4);assert.equal(career.status,'rated');
 assert.equal(career.label,'平稳前行');assert.equal(career.tone,'steady');assert.equal(career.lowConfidence,false);
 assert.equal(recent.pulls,80);assert.equal(recent.fiveCount,4);assert.equal(recent.lowConfidence,true);
 assert.equal(tag(tags,'ten_2').count,1);assert.equal(tag(tags,'up_streak').text,'2连UP');
 assert.equal(multi.count,1);assert.deepEqual(multi.counts,{exact:1,unknown:0});
 const limited=source.analysis.pools.find(p=>p.pool_id===1002);
 assert.deepEqual(multi.events[0].positions,limited.events.map(e=>e.in_pool_pull));
 assert.deepEqual(multi.events[0].characters,['限定五星','限定五星']);
 assert.equal(multi.byPool['1002'].length,1);
});

test('analyze never mutates the input report or the original analysis schema',()=>{
 const rows=[
  {pool_id:1001,timestamp:NOW-10*DAY,index:1,record_id:1001,drops:[{item_id:1001,count:62},{item_id:1002,count:1},{item_id:1002,count:1},{item_id:1001,count:6}]},
  {pool_id:1002,timestamp:NOW-5*DAY,index:1,record_id:1002,drops:[{item_id:1001,count:6},{item_id:1003,count:1},{item_id:1003,count:1},{item_id:1001,count:2}]},
 ];
 const source=engine.buildReport(rows,catalog,{}),snapshot=JSON.stringify(source);
 MobileInsights.analyze(source,{now:NOW});MobileInsights.analyze(source,{now:NOW,category:'limited'});MobileInsights.analyze(source);
 assert.equal(JSON.stringify(source),snapshot);
 const hand=report([record(1,1000,[drop(11,3,6),drop(12,5,1,'金'),drop(12,5,1,'金'),drop(11,3,2)])],[poolInfo(1,'normal',70,[ev(1,7,'up')])]);
 const handSnapshot=JSON.stringify(hand);MobileInsights.analyze(hand,{now:2000});assert.equal(JSON.stringify(hand),handSnapshot);
});
