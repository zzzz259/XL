'use strict';

const {test}=require('node:test');
const assert=require('node:assert/strict');
const MobileServices=require('../web/mobile-services.js');

function fixture(){
 return {account:{id:'account-test',label:'主号/测试',uid:'7',channel:'sms'},records:[{time_beijing:'2026-01-02 03:04:05',pool_name:'=危险卡池',index:1,record_id:99,pool_id:1,drops:[{item_id:2,name:'+角色',rarity:5,count:1}]}],analysis:{pools:[{pool_id:1,category:'limited'}]}};
}

test('JSON export preserves the complete report and a safe deterministic filename',()=>{
 const report=fixture(),file=MobileServices.reportFile(report,'json',true,new Date('2026-10-26T01:02:03Z'));
 assert.deepEqual(JSON.parse(file.content),report);
 assert.equal(file.mime,'application/json');
 assert.equal(file.filename,'demo-starfall-gacha-主号_测试-2026-10-26.json');
});

test('CSV export keeps identities and neutralizes spreadsheet formulas',()=>{
 const file=MobileServices.reportFile(fixture(),'csv',false,new Date('2026-10-26T01:02:03Z'));
 assert.ok(file.content.startsWith('\ufeff'));
 assert.ok(file.content.includes('"=危险卡池"')===false);
 assert.ok(file.content.includes('"\'=危险卡池"'));
 assert.ok(file.content.includes('"\'+角色"'));
 assert.ok(file.content.includes('"account-test"'));
 assert.ok(file.content.includes('"limited"'));
 assert.equal(file.mime,'text/csv;charset=utf-8');
});

test('unsupported export types and missing reports fail before creating a file',()=>{
 assert.throws(()=>MobileServices.reportFile(fixture(),'xml'),/不支持的导出格式/);
 assert.throws(()=>MobileServices.reportFile(null,'json'),/没有可导出的记录/);
});

test('shared long-image engine loads without the legacy desktop dialog',()=>{
 const fs=require('node:fs'),vm=require('node:vm');
 const code=fs.readFileSync('../xl_gacha_web/static/image-export.js','utf8');
 const context={module:{exports:{}},exports:{},document:{getElementById:()=>null},window:{},TextEncoder};
 vm.runInNewContext(code,context);
 assert.equal(typeof context.module.exports.planPages,'function');
 assert.equal(typeof context.module.exports.deliver,'function');
 assert.equal(typeof context.module.exports.prepare,'function');
});

function freshLongImage(){
 delete require.cache[require.resolve('../../xl_gacha_web/static/image-export.js')];
 return require('../../xl_gacha_web/static/image-export.js');
}

function mockImage(w=280,h=140){return {naturalWidth:w,naturalHeight:h,width:w,height:h};}

function mockCanvas(){
 const ops=[];
 return {width:0,height:0,ops,
  getContext:()=>({fillStyle:'',font:'',
   fillRect:(...a)=>ops.push(['fillRect',...a]),
   fillText:(...a)=>ops.push(['fillText',...a]),
   measureText:s=>({width:String(s).length*10}),
   drawImage:(...a)=>ops.push(['drawImage',...a])})};
}

function assetBridge({failIds=new Set(),missingIds=new Set(),failBrand=false}={}){
 const calls={init:0,character:[],load:[]};
 globalThis.MobileAssets={
  init:async()=>{calls.init++;},
  character:async id=>{calls.character.push(id);
   if(failIds.has(id))throw new Error('bad character');
   return missingIds.has(id)?null:{src:`assets/char/${id}.png`,name:`角色${id}`,rarity:5,crop:'left'};},
  loadImage:async src=>{calls.load.push(src);
   if(failBrand&&src.includes('brand'))throw new Error('logo decode failed');
   return mockImage();},
  brand:async()=>({logo:'assets/brand/starfall-logo.png',name:'星落'}),
 };
 return calls;
}

function imageReport(){
 const events=[
  {name:'角色甲',item_id:11,pulls_since_five:20,interval_exact:true,outcome:'up'},
  {name:'角色乙',item_id:22,pulls_since_five:40,interval_exact:true,outcome:'off',guaranteed:true},
  {name:'角色甲',item_id:11,pulls_since_five:10,interval_exact:true,outcome:'up'},
 ];
 return {account:{label:'主号'},preview_source:false,
  summary:{pull_count:200,rarity_counts:{'5':3},pool_pull_counts:{池:200}},
  analysis:{categories:{normal:{name:'普通池',pull_count:200,five_count:3}},
   pools:[{pool_name:'池',category:'normal',category_name:'普通池',first_date:'2026-01-01',
    last_date:'2026-01-03',pull_count:200,hard_pity:70,tail_since_five:5,tail_exact:true,
    next_guaranteed:false,events}]}};
}

test('prepare resolves null without MobileAssets and never blocks export',async()=>{
 delete globalThis.MobileAssets;
 assert.equal(await freshLongImage().prepare(imageReport(),[]),null);
});

test('prepare dedups unique characters across pages and caches the results',async()=>{
 const calls=assetBridge({failBrand:true});
 const LongImage=freshLongImage(),report=imageReport(),pages=LongImage.planPages(report,'all');
 const assets=await LongImage.prepare(report,pages);
 assert.equal(calls.init,1);
 assert.deepEqual([...calls.character].sort((a,b)=>a-b),[11,22]);
 assert.equal(calls.load.filter(src=>src.includes('char')).length,2);
 assert.equal(assets.brand,null);
 assert.equal(assets.characters.get(22).info.rarity,5);
 await LongImage.prepare(report,pages);
 assert.equal(calls.character.length,2);
 delete globalThis.MobileAssets;
});

test('prepare drops failed character lookups without rejecting',async()=>{
 assetBridge({failIds:new Set([22]),missingIds:new Set([33])});
 const LongImage=freshLongImage(),report=imageReport();
 report.analysis.pools[0].events.push({name:'角色丙',item_id:33,pulls_since_five:5,interval_exact:true,outcome:'up'});
 const assets=await LongImage.prepare(report,LongImage.planPages(report,'all'));
 assert.deepEqual([...assets.characters.keys()],[11]);
 delete globalThis.MobileAssets;
});

test('draw keeps the legacy layout without assets and renders avatars plus logo with assets',async()=>{
 const LongImage=freshLongImage();
 globalThis.document={createElement:()=>mockCanvas()};
 const report=imageReport(),page=LongImage.planPages(report,'all')[0];
 const legacy=LongImage.draw(report,page,0,1,'all');
 assert.equal(legacy.ops.filter(op=>op[0]==='drawImage').length,0);
 assert.equal(legacy.ops.find(op=>op[0]==='fillText'&&op[1]==='角色甲')[2],60);
 assetBridge();
 const assets=await LongImage.prepare(report,LongImage.planPages(report,'all'));
 const canvas=LongImage.draw(report,page,0,1,'all',assets);
 const draws=canvas.ops.filter(op=>op[0]==='drawImage');
 assert.equal(draws.length,4);
 assert.deepEqual(draws[0].slice(2),[1080-40-56,51,56,34]);
 for(const avatar of draws.slice(1))assert.deepEqual(avatar.slice(2,6),[0,0,140,140]);
 assert.equal(canvas.ops.find(op=>op[0]==='fillText'&&op[1]==='角色甲')[2],132);
 delete globalThis.document;
 delete globalThis.MobileAssets;
});

test('catalog feedback recognizes native updates and actual revision changes',()=>{
 assert.equal(MobileServices.catalogView({phase:'current',revision:'new',updated:false},'old').updated,true);
 assert.equal(MobileServices.catalogView({phase:'current',revision:'same',updated:false},'same').updated,false);
 assert.equal(MobileServices.catalogView({phase:'current',revision:'cached',updated:false},undefined).updated,false);
 assert.equal(MobileServices.catalogView({phase:'current',revision:'new',updated:true},'old').updated,true);
});
