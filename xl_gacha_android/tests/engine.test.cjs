const {test}=require('node:test'); const assert=require('node:assert/strict');
const fs=require('node:fs'); const engine=require('../web/mobile-engine.js');
const catalog=JSON.parse(fs.readFileSync('../xl_gacha_web/data/catalog.json','utf8'));
const demo=JSON.parse(fs.readFileSync('../xl_gacha_web/static/demo.json','utf8'));

test('80 to 1 wrap retains cross-banner pulls and guaranteed UP; missing 80 still breaks continuity',()=>{
 const meta={1:{category:'limited',pity_group:'limited',featured_ids:[11]},2:{category:'limited',pity_group:'limited',featured_ids:[22]}};
 const r=(pool_id,index,timestamp,drops)=>({pool_id,pool_name:'测试池',index,timestamp,record_id:pool_id,time_beijing:'2026-01-01 00:00:00',drops:drops.map(([item_id,rarity,count])=>({item_id,rarity,count,name:'测试角色'}))});
 const rows=[r(1,79,100,[[99,5,1],[5,3,3]]),r(1,80,200,[[5,3,10]]),r(2,1,300,[[5,3,9],[22,5,1]])];
 const hit=engine.analyze(rows,meta).pools[0].events[0];
 assert.equal(hit.pulls_since_five,23); assert.equal(hit.carried_pulls,13);assert.equal(hit.interval_exact,true);assert.equal(hit.guaranteed,true);
 const gap=engine.analyze([rows[0],rows[2]],meta).pools[0].events[0];
 assert.equal(gap.pulls_since_five,10);assert.equal(gap.interval_exact,false);assert.equal(gap.guaranteed,null);
});

test('non-contiguous index jumps conservatively mark earlier history incomplete',()=>{
 const records=[
  {pool_id:1,pool_name:'测试池',timestamp:100,index:40,record_id:1,time_beijing:'2026-01-01 00:00:00',drops:[{item_id:5,name:'低星',rarity:3,count:9}]},
  {pool_id:1,pool_name:'测试池',timestamp:200,index:50,record_id:2,time_beijing:'2026-01-01 00:00:00',drops:[{item_id:11,name:'五星',rarity:5,count:1}]},
 ];
 const hit=engine.analyze(records,{1:{category:'limited',pity_group:'limited',featured_ids:[11]}}).pools[0].events[0];
 assert.equal(hit.interval_exact,false);
 assert.equal(hit.pulls_since_five,1);
 assert.equal(hit.guaranteed,null);
});

test('average includes every visible pull, first five and current unfinished pulls',()=>{
 const rows=[{pool_id:1,pool_name:'测试池',timestamp:100,index:1,record_id:1,time_beijing:'2026-01-01 00:00:00',drops:[{item_id:1,name:'低星',rarity:3,count:3},{item_id:2,name:'五星',rarity:5,count:1},{item_id:1,name:'低星',rarity:3,count:6}]}];
 assert.equal(engine.analyze(rows,{1:{category:'normal'}}).categories.normal.average_five_interval,10);
 assert.equal(engine.analyze([],{1:{category:'normal'}}).categories.normal.average_five_interval,null);
});

test('ambiguous old CSV banners retain verified shared pity group without inventing UP',async()=>{
 const csv='时间（北京时间）,卡池,记录序号,记录ID,抽次（该记录内）,物品ID,角色/物品,初始星级,数量\n2026-01-01 12:00:00,悲悯仁心,80,1,1,999001,测试五星,5,1\n2026-01-01 12:01:00,救赎之光,1,1,1,999002,测试五星2,5,1\n';
 const source=await engine.parseCSV(csv,catalog),report=engine.buildReport(source.records,catalog,source.pool_metadata),hit=report.analysis.pools[0].events[0];
 assert.equal(hit.interval_exact,true);assert.equal(hit.pulls_since_five,1);assert.equal(hit.outcome,'unknown');
});
test('Android analysis agrees with desktop fixture for every category and five-star event',()=>{
 const result=engine.analyze(demo.records,catalog.pool_meta);
 assert.deepEqual(result.categories,demo.analysis.categories);
 assert.deepEqual(result.pools,demo.analysis.pools);
});
test('record merging deduplicates complete records and retains same-character drops',()=>{
 const r={pool_id:1,timestamp:1700000000,index:1,record_id:1,drops:[{item_id:123,count:1},{item_id:123,count:1}]};
 const report=engine.buildReport([r,r],catalog,{});
 assert.equal(report.summary.pull_count,2); assert.equal(report.records.length,1);
 assert.equal(report.records[0].drops.length,2);
 assert.throws(()=>engine.buildReport([r,{...r,drops:[{item_id:124,count:1}]}],catalog,{}));
});
test('legacy CSV parsing groups multi-pulls and never combines independent positions',async()=>{
 const csv='时间（北京时间）,卡池,记录序号,记录ID,抽次（该记录内）,物品ID,角色/物品,初始星级,数量\n2026-01-01 12:00:00,未知池,1,11,1,999001,测试角色,5,1\n2026-01-01 12:00:00,未知池,1,11,2,999001,测试角色,5,1\n';
 const source=await engine.parseCSV(csv,catalog);const report=engine.buildReport(source.records,catalog,source.pool_metadata);
 assert.equal(report.summary.pull_count,2); assert.equal(report.records.length,1);
 assert.equal(report.analysis.categories.unknown.five_count,2);
});
test('account identity hashes match the desktop algorithm and fields are allowlisted',async()=>{
 const identity=await engine.identity({uid:'555',channel:'sms'});
 const hash=require('node:crypto').createHash('sha256').update('sms:555').digest('hex').slice(0,32);
 assert.equal(identity.id,'account-'+hash);
 const report=engine.buildReport([{...demo.records[0],jwt:'NEVER_STORE'}],catalog,{});
 assert.ok(!JSON.stringify(report).includes('NEVER_STORE'));
});

test('player profile fields preserve account identity and nickname defaults',async()=>{
 const source={uid:'555',channel:'sms',nickname:'合成玩家',game_uid:'9007199254740993',level:80,face_id:12001701,fashion_id:12001701,head_id:0,head_rect_id:21180003,head_rect_stamp:1893456000,head_rect_extra:7,head_rect_item_uid:'0',server_time:1793456000};
 const identity=await engine.identity(source);
 const hash=require('node:crypto').createHash('sha256').update('sms:555').digest('hex').slice(0,32);
 assert.equal(identity.id,'account-'+hash);assert.equal(identity.label,'合成玩家');
 for(const key of Object.keys(source))assert.deepEqual(identity[key],source[key]);
 const named=await engine.identity({...source,label:'我的存档'});assert.equal(named.label,'我的存档');
 await assert.rejects(engine.identity({...source,level:-1}));await assert.rejects(engine.identity({...source,nickname:'bad\nname'}));
});

test('same numeric UID in Bilibili and official login remains in separate archives',async()=>{
 const bili=await engine.identity({uid:'555',channel:'bilibili'}),official=await engine.identity({uid:'555',channel:'sms'});
 assert.notEqual(bili.id,official.id);
 assert.equal(bili.id,'account-'+require('node:crypto').createHash('sha256').update('bilibili:555').digest('hex').slice(0,32));
 const repeated=engine.buildReport([...demo.records,...demo.records],catalog,{}),single=engine.buildReport(demo.records,catalog,{});
 assert.equal(repeated.summary.pull_count,single.summary.pull_count);assert.deepEqual(repeated.analysis,single.analysis);
});

test('new catalog remaps raw IDs and refreshes revision while preserving pulls',()=>{
 const raw=[{pool_id:999901,timestamp:1700000000,index:1,record_id:1,drops:[{item_id:999001,count:1}]}];
 const before=engine.buildReport(raw,catalog,{});
 assert.equal(before.summary.unknown_item_count,1);
 const newer=structuredClone(catalog);newer.version='134359843258232661';newer._revision='test-new-config';
 newer.cards[999001]=['新增五星',5];newer.pools[999901]='新增卡池';newer.pool_meta[999901]={category:'limited',pity_group:'limited',featured_ids:[999001],featured_names:['新增五星'],hard_pity:70};
 const r=engine.buildReport(before.records,newer,before.pool_metadata);
 assert.equal(r.summary.pull_count,1);assert.equal(r.summary.unknown_item_count,0);assert.equal(r.records[0].drops[0].name,'新增五星');
 assert.equal(r.analysis.pools[0].events[0].outcome,'up');assert.equal(r.configuration_revision,'test-new-config');
});
