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

function mockImage(w=256,h=256){return {naturalWidth:w,naturalHeight:h,width:w,height:h};}

function mockCanvas(){
 const ops=[];
 return {width:0,height:0,ops,
  getContext:()=>({fillStyle:'',font:'',
   fillRect:(...a)=>ops.push(['fillRect',...a]),
   fillText(text,x,y,max){ops.push(['fillText',text,x,y,max,this.font,this.textAlign]);},
   measureText:s=>({width:String(s).length*10}),
   drawImage:(...a)=>ops.push(['drawImage',...a])})};
}

function assetBridge({failIds=new Set(),missingIds=new Set(),failBrand=false,crop='center'}={}){
 const calls={init:0,character:[],load:[]};
 globalThis.MobileAssets={
  init:async()=>{calls.init++;},
  character:async id=>{calls.character.push(id);
   if(failIds.has(id))throw new Error('bad character');
   return missingIds.has(id)?null:{src:`assets/char/${id}.png`,name:`角色${id}`,rarity:5,crop};},
  loadImage:async src=>{calls.load.push(src);
   if(failBrand&&src.includes('brand'))throw new Error('logo decode failed');
   return src.includes('brand')?mockImage(280,140):mockImage();},
  brand:async()=>({logo:'assets/brand/starfall-logo.png',name:'星落'}),
 };
 return calls;
}

function imageReport(){
 const events=[
  {name:'角色甲',item_id:11,pulls_since_five:20,interval_exact:true,outcome:'up',in_pool_pull:20,time_beijing:'2026-01-02 03:04:05'},
  {name:'角色乙',item_id:22,pulls_since_five:40,interval_exact:true,outcome:'off',guaranteed:true,in_pool_pull:60,time_beijing:'2026-01-03 03:04:05'},
  {name:'角色甲',item_id:11,pulls_since_five:10,interval_exact:true,outcome:'up',in_pool_pull:70,time_beijing:'2026-01-04 03:04:05'},
 ];
 return {account:{label:'主号'},preview_source:false,
  summary:{pull_count:200,rarity_counts:{'5':3},pool_pull_counts:{池:200}},
  analysis:{categories:{normal:{name:'普通池',pull_count:200,five_count:3,up_count:0,off_count:0,average_five_interval:66.67,since_five:5,since_five_exact:true,hard_pity:70}},
   pools:[{pool_id:1,pool_name:'池',category:'normal',category_name:'普通池',first_date:'2026-01-01',
    last_date:'2026-01-03',pull_count:200,five_count:3,hard_pity:70,tail_since_five:5,tail_exact:true,
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

test('planPages filters specific pool ids and keeps legacy behavior without them',()=>{
 const LongImage=freshLongImage();
 const mk=(pool_id,category,tag,n)=>({pool_id,pool_name:`池${tag}`,category,category_name:category,first_date:'2026-01-01',last_date:'2026-01-02',pull_count:n,five_count:n,hard_pity:70,tail_since_five:1,tail_exact:true,next_guaranteed:false,events:Array.from({length:n},(_,i)=>({name:`${tag}${i}`,item_id:i+1,pulls_since_five:10,interval_exact:true,outcome:'up'}))});
 const report={analysis:{pools:[mk(1,'limited','甲',3),mk(2,'limited','乙',2),mk(3,'normal','丙',1)]}};
 const names=pages=>pages.flatMap(p=>p.chunks.flatMap(c=>c.events.map(e=>e.name)));
 assert.deepEqual(names(LongImage.planPages(report,'all',10000,new Set([1,3]))),['甲2','甲1','甲0','丙0']);
 assert.deepEqual(names(LongImage.planPages(report,'limited',10000,[2])),['乙1','乙0']);
 const baseline=LongImage.planPages(report,'all');
 assert.deepEqual(names(LongImage.planPages(report,'all',10000,null)),names(baseline));
 assert.deepEqual(LongImage.planPages(report,'all',10000,[1,2,3]).map(p=>p.height),baseline.map(p=>p.height));
 const empty=LongImage.planPages(report,'all',10000,[]);
 assert.equal(empty.length,1);
 assert.equal(empty[0].chunks.length,0);
});

test('headRect uses the full square for center crops and keeps the left crop for banners',()=>{
 const LongImage=freshLongImage();
 assert.deepEqual(LongImage.headRect(mockImage(256,256),'center'),[0,0,256,256]);
 assert.deepEqual(LongImage.headRect(mockImage(280,140),'left'),[0,0,140,140]);
 assert.deepEqual(LongImage.headRect(mockImage(280,140)),[70,0,140,140]);
});

test('draw keeps the legacy layout without assets and renders the new interface with assets',async()=>{
 const LongImage=freshLongImage();
 globalThis.document={createElement:()=>mockCanvas(),getElementById:()=>null};
 const report=imageReport(),page=LongImage.planPages(report,'all')[0];
 const legacy=LongImage.draw(report,page,0,1,'all');
 assert.equal(legacy.ops.filter(op=>op[0]==='drawImage').length,0);
 assert.equal(legacy.ops.find(op=>op[0]==='fillText'&&op[1]==='角色甲')[2],60);
 assetBridge();
 const mobilePage=LongImage.planPages(report,'all')[0];
 const assets=await LongImage.prepare(report,[mobilePage]);
 const canvas=LongImage.draw(report,mobilePage,0,1,'all',assets);
 const draws=canvas.ops.filter(op=>op[0]==='drawImage');
 assert.equal(draws.length,4);
 // The complete logo scales proportionally; avatars still use full squares.
 assert.equal(draws[0][2]+draws[0][4]/2,540);
 assert.equal(draws[0][4]/draws[0][5],2);
 assert.ok(draws[0][5]>=120);
 for(const avatar of draws.slice(1)){
  assert.deepEqual(avatar.slice(2,6),[0,0,256,256]);
  assert.equal(avatar[6],72);
  assert.ok(avatar[8]>=88);
 }
 assert.ok(canvas.ops.find(op=>op[0]==='fillText'&&op[1]==='角色甲')[2]>=184);
 // left-crop banners still crop 140x140 from the left
 const BannerLongImage=freshLongImage();
 assetBridge({crop:'left'});
 const bannerReport=imageReport();
 const bannerPage=BannerLongImage.planPages(bannerReport,'all')[0];
 const bannerAssets=await BannerLongImage.prepare(bannerReport,[bannerPage]);
 const bannerCanvas=BannerLongImage.draw(bannerReport,bannerPage,0,1,'all',bannerAssets);
 for(const avatar of bannerCanvas.ops.filter(op=>op[0]==='drawImage').slice(1))
  assert.deepEqual(avatar.slice(2,6),[0,0,140,140]);
 delete globalThis.document;
 delete globalThis.MobileAssets;
});

test('draw degrades gracefully when the brand logo or insights are missing',async()=>{
 const LongImage=freshLongImage();
 globalThis.document={createElement:()=>mockCanvas(),getElementById:()=>null};
 assetBridge({failBrand:true});
 const report=imageReport(),page=LongImage.planPages(report,'all')[0];
 const assets=await LongImage.prepare(report,[page]);
 const canvas=LongImage.draw(report,page,0,1,'all',assets);
 const draws=canvas.ops.filter(op=>op[0]==='drawImage');
 assert.equal(draws.length,3);
 assert.ok(draws.every(op=>op[1]!==null));
 assert.ok(canvas.ops.some(op=>op[0]==='fillText'&&op[1]==='招募档案'));
 assert.equal(canvas.ops.some(op=>op[0]==='fillText'&&String(op[1]).startsWith('生涯 ·')),false);
 assert.equal(assets.insights,null);
 delete globalThis.document;
 delete globalThis.MobileAssets;
});

test('catalog feedback recognizes native updates and actual revision changes',()=>{
 assert.equal(MobileServices.catalogView({phase:'current',revision:'new',updated:false},'old').updated,true);
 assert.equal(MobileServices.catalogView({phase:'current',revision:'same',updated:false},'same').updated,false);
 assert.equal(MobileServices.catalogView({phase:'current',revision:'cached',updated:false},undefined).updated,false);
 assert.equal(MobileServices.catalogView({phase:'current',revision:'new',updated:true},'old').updated,true);
});

function mobileImageHarness(){
 const fs=require('node:fs'),vm=require('node:vm'),elements=new Map();
 class Element{
  constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.attributes={};this.listeners={};this.textContent='';this.disabled=false;this.hidden=false;this.checked=false;this.open=false;this.value='all';}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=[...children];}
  addEventListener(type,listener){(this.listeners[type]??=[]).push(listener);}
  dispatchEvent(event){event.target??=this;for(const listener of this.listeners[event.type]||[])listener(event);}
  setAttribute(name,value){this.attributes[name]=String(value);}
  querySelectorAll(selector){
   const matches=[];const visit=node=>{for(const child of node.children||[]){if(selector==='input[type=checkbox]'&&child.tagName==='input'&&child.type==='checkbox')matches.push(child);visit(child);}};visit(this);return matches;
  }
  showModal(){this.open=true;}
  close(){this.open=false;this.dispatchEvent({type:'close'});}
 }
 for(const id of ['image-pool-list','image-pool-count','image-selected','image-save','image-share','image-select-all','image-clear','image-category','image-zip','image-previews','mobile-image-dialog','image-status','image-pool-picker','image-pool-all','image-pool-none','image-close'])elements.set(id,new Element());
 const document={fonts:{ready:Promise.resolve()},createElement:tag=>new Element(tag),createTextNode:text=>({textContent:text})};
 let deliveryResolve=null;
 const LongImage={
  planPages:(report,category,maximum,poolIds)=>[{poolIds}],
  prepare:async()=>null,
  draw:()=>({width:1080,height:1000,toBlob:callback=>callback(new Blob(['png'],{type:'image/png'}))}),
  deliver:()=>new Promise(resolve=>{deliveryResolve=resolve;}),makeZip:async()=>new Blob(),
 };
 const context={document,LongImage,MobileRender:{el:(tag,text,className)=>{const element=new Element(tag);element.textContent=text??'';element.className=className??'';return element;}},MobileServices:{messages:{},accountName:MobileServices.accountName},window:{},URL:{createObjectURL:()=>`blob:${Math.random()}`,revokeObjectURL:()=>{}},requestAnimationFrame:callback=>setImmediate(callback),setImmediate,Blob,console};
 context.$=id=>elements.get(id);
 vm.runInNewContext(`${fs.readFileSync(require.resolve('../web/mobile-image.js'),'utf8')}\nglobalThis.controller=MobileImage;`,context);
 context.controller.init({notify:()=>{}});
 return {elements,open:report=>context.controller.open(report),completeDelivery:()=>deliveryResolve?.({opened:true,count:1}),settle:async()=>{for(let i=0;i<12;i++)await new Promise(setImmediate);}};
}

function poolReport(id,poolIds){
 return {account:{id,label:id},analysis:{pools:poolIds.map((poolId,index)=>({pool_id:poolId,pool_name:`池${poolId}`,category:index?'normal':'limited',pull_count:10,index}))}};
}

test('long image pool choices persist for one report and reset to all for another report',async()=>{
 const harness=mobileImageHarness(),first=poolReport('甲',[1,2]),second=poolReport('乙',[3,4]);
 harness.open(first);await harness.settle();
 let choices=harness.elements.get('image-pool-list').querySelectorAll('input[type=checkbox]');
 assert.deepEqual(choices.map(choice=>choice.checked),[true,true]);
 choices[1].checked=false;choices[1].dispatchEvent({type:'change'});await harness.settle();
 harness.elements.get('mobile-image-dialog').close();
 harness.open(first);await harness.settle();
 choices=harness.elements.get('image-pool-list').querySelectorAll('input[type=checkbox]');
 assert.deepEqual(choices.map(choice=>choice.checked),[true,false]);
 harness.elements.get('mobile-image-dialog').close();
 harness.open(second);await harness.settle();
 choices=harness.elements.get('image-pool-list').querySelectorAll('input[type=checkbox]');
 assert.deepEqual(choices.map(choice=>choice.dataset.poolId),['3','4']);
 assert.deepEqual(choices.map(choice=>choice.checked),[true,true]);
});

test('long image pool changes stay locked during system delivery',async()=>{
 const harness=mobileImageHarness();harness.open(poolReport('甲',[1,2]));await harness.settle();
 const elements=harness.elements,choices=elements.get('image-pool-list').querySelectorAll('input[type=checkbox]');
 elements.get('image-share').dispatchEvent({type:'click'});
 assert.equal(elements.get('image-status').textContent,'正在准备系统分享…');
 choices[1].checked=false;choices[1].dispatchEvent({type:'change'});await harness.settle();
 assert.equal(choices[1].checked,true);
 assert.equal(elements.get('image-pool-count').textContent,'已选 2/2 个卡池');
 assert.equal(elements.get('image-status').textContent,'正在准备系统分享…');
 assert.equal(elements.get('image-previews').children.length,1);
 const category=elements.get('image-category');category.value='normal';category.dispatchEvent({type:'change'});await harness.settle();
 assert.equal(category.value,'all');
 assert.equal(elements.get('image-status').textContent,'正在准备系统分享…');
 assert.equal(elements.get('image-previews').children.length,1);
 harness.completeDelivery();await harness.settle();
 assert.equal(elements.get('image-status').textContent,'已打开系统分享，请在选择器中确认。');
 assert.ok(choices.every(choice=>!choice.disabled));
 choices[1].checked=false;choices[1].dispatchEvent({type:'change'});await harness.settle();
 assert.equal(elements.get('image-pool-count').textContent,'已选 1/2 个卡池');
});


test('account headings prefer real nicknames and suppress automatically generated channel names',()=>{
 for(const label of ['tap','bili','sms','taptap · UID 12345','bilibili · UID 12345']){
  const account={nickname:'玩家昵称',channel:'sms',uid:'12345',label},before=JSON.stringify(account);
  assert.equal(MobileServices.accountName(account),'玩家昵称');
  assert.equal(JSON.stringify(account),before);
  assert.equal(MobileServices.accountName({...account,nickname:''}),'招募档案');
 }
 assert.equal(MobileServices.accountName({label:'自己起的存档名'}),'自己起的存档名');
 assert.equal(MobileServices.accountName(null,'本机存档'),'本机存档');
 assert.equal(MobileServices.accountName({nickname:'tap',label:'自动名称'}),'tap');
 const report=fixture();report.account.nickname='玩家昵称';
 assert.ok(MobileServices.reportFile(report,'json',false,new Date('2026-11-02')).filename.includes('玩家昵称'));
});
