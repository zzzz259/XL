'use strict';

const {test,beforeEach}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const MobileAssets=require('../web/mobile-assets.js');

const manifestPath=path.join(__dirname,'..','web','assets','manifest.json');
const manifestText=fs.readFileSync(manifestPath,'utf8');
const manifest=JSON.parse(manifestText);

class FakeImage{
 constructor(){
  this.onload=null;
  this.onerror=null;
  this._src=null;
 }
 set src(value){
  this._src=value;
  queueMicrotask(()=>{if(typeof this.onload==='function')this.onload();});
 }
 get src(){return this._src;}
 decode(){return Promise.resolve();}
}

const anyFashionId=Object.keys(manifest.fashions)[0];
const otherFashionId=Object.keys(manifest.fashions)[1];
const headItemId=Object.keys(manifest.account_items).find(id=>manifest.account_items[id].type===108);
const permanentFrameId=Object.keys(manifest.account_items).find(id=>manifest.account_items[id].type===109&&String(manifest.account_items[id].effect_value)!=='1');
const timedFrameId=Object.keys(manifest.account_items).find(id=>String(manifest.account_items[id].effect_value)==='1');

beforeEach(async()=>{
 MobileAssets._reset()._inject({
  fetch:()=>Promise.resolve({ok:true,status:200,json:()=>Promise.resolve(JSON.parse(manifestText))}),
  Image:FakeImage,
 });
 await MobileAssets.init();
});

test('manifest keeps resource version, mappings and sha256 for controlled files',()=>{
 assert.equal(manifest.resource_version,'134377281279792758');
 assert.equal(Object.keys(manifest.characters).length,109);
 assert.equal(Object.keys(manifest.fashions).length,316);
 for(const section of ['characters','fashions','account_items']){
  for(const entry of Object.values(manifest[section])){
   assert.ok(entry.path.startsWith('assets/'),`${section} path must be relative: ${entry.path}`);
   assert.ok(!entry.path.includes('..')&&!entry.path.includes('://'),`unsafe path: ${entry.path}`);
   assert.ok(manifest.files[entry.path],`missing sha256 for ${entry.path}`);
  }
 }
 for(const entry of Object.values(manifest.fashions)){
  assert.ok(entry.rect_path.startsWith('assets/account-rect/'));
  assert.ok(manifest.files[entry.rect_path],`missing account rect sha256 for ${entry.rect_path}`);
 }
 for(const value of Object.values(manifest.brand))assert.ok(manifest.files[value]);
 for(const reaction of manifest.mascot.reactions)assert.ok(manifest.files[reaction.path]);
 assert.equal(manifest.mascot.idle.durationMs,1366);
 assert.equal(manifest.mascot.reactions.map(r=>r.durationMs).join(','),'1699,1366,1333');
});

test('default character resolves the initial square portrait to a controlled asset',()=>{
 const view=MobileAssets.character('10000101');
 assert.deepEqual(view,{src:'assets/portraits/10000101.webp',name:'菲尼斯',rarity:1,crop:'center',cropRect:[38,0,180,180],sourceSize:[256,256]});
 for(const id of Object.keys(manifest.characters)){
  const entry=MobileAssets.character(id);
  assert.ok(entry,`character ${id} must resolve`);
  assert.equal(entry.crop,'center');
  assert.equal(entry.src,manifest.characters[id].path);
  assert.ok(fs.existsSync(path.join(__dirname,'..','web',entry.src)));
 }
});

test('unknown character id resolves to null without fabricating data',()=>{
 assert.equal(MobileAssets.character('99999999'),null);
 assert.equal(MobileAssets.character(undefined),null);
});

test('player head uses account CardHeadRect assets and only falls back to face_id when both ids are zero',()=>{
 const fashionSrc=manifest.fashions[anyFashionId].rect_path;
 const headSrc=manifest.account_items[headItemId].path;
 const faceSrc=manifest.fashions[otherFashionId].rect_path;
 const withFashion=MobileAssets.player({fashion_id:Number(anyFashionId),head_id:Number(headItemId),face_id:Number(otherFashionId)});
 assert.deepEqual(withFashion.head,{src:fashionSrc,crop:'game-head',cropRect:[91,23,88,88],sourceSize:[320,140]});
 const withHead=MobileAssets.player({fashion_id:0,head_id:Number(headItemId),face_id:Number(otherFashionId)});
 assert.deepEqual(withHead.head,{src:headSrc,crop:'game-head',cropRect:[91,23,88,88],sourceSize:[320,140]});
 const withFace=MobileAssets.player({fashion_id:0,head_id:0,face_id:Number(otherFashionId)});
 assert.deepEqual(withFace.head,{src:faceSrc,crop:'game-head',cropRect:[91,23,88,88],sourceSize:[320,140]});
 assert.deepEqual(MobileAssets.player({face_id:Number(otherFashionId)}).head,{src:faceSrc,crop:'game-head',cropRect:[91,23,88,88],sourceSize:[320,140]},'missing legacy ids are equivalent to zero');
 assert.equal(MobileAssets.player({fashion_id:99999999,head_id:Number(headItemId),face_id:Number(otherFashionId)}).head,null,'unknown selected fashion must not fall through to a different head');
 assert.equal(MobileAssets.player({fashion_id:0,head_id:99999999,face_id:Number(otherFashionId)}).head,null,'unknown nonzero head id must not fall through to face id');
 assert.equal(MobileAssets.player({fashion_id:0,head_id:Number(permanentFrameId),face_id:0}).head,null,'frame item must not be used as a player head');
 assert.equal(MobileAssets.player({fashion_id:0,head_id:0,face_id:0}).head,null);
 assert.equal(MobileAssets.player({}).head,null);
});

test('player frame is null when no valid head_rect_id is set',()=>{
 assert.equal(MobileAssets.player({head_rect_id:0}).frame,null);
 assert.equal(MobileAssets.player({}).frame,null);
 assert.equal(MobileAssets.player({head_rect_id:99999999}).frame,null);
 assert.equal(MobileAssets.player({head_rect_id:Number(headItemId)}).frame,null,'type 108 item must not be used as frame');
});

test('player frame without effect_value is permanent',()=>{
 const view=MobileAssets.player({head_rect_id:Number(permanentFrameId)});
 assert.deepEqual(view.frame,{src:manifest.account_items[permanentFrameId].path});
});

test('player frame with effect_value 1 requires stamp newer than server time',()=>{
 const rect=Number(timedFrameId);
 const src=manifest.account_items[timedFrameId].path;
 assert.deepEqual(MobileAssets.player({head_rect_id:rect,head_rect_stamp:2000,server_time:1000},{nowMs:1000000}).frame,{src});
 assert.equal(MobileAssets.player({head_rect_id:rect,head_rect_stamp:1000,server_time:2000}).frame,null);
 assert.equal(MobileAssets.player({head_rect_id:rect,head_rect_stamp:1000,server_time:1000}).frame,null);
 assert.equal(MobileAssets.player({head_rect_id:rect}).frame,null);
 assert.equal(MobileAssets.player({head_rect_id:rect,head_rect_stamp:2000}).frame,null);
});

test('player rank only comes from a visible frame with anonymous uid zero and positive extra',()=>{
 const rank=account=>MobileAssets.player({head_rect_id:Number(permanentFrameId),...account}).rank;
 assert.equal(rank({head_rect_item_uid:'0',head_rect_extra:3}),3);
 assert.equal(rank({head_rect_item_uid:0,head_rect_extra:'5'}),5);
 assert.equal(rank({head_rect_item_uid:'0',head_rect_extra:0}),null);
 assert.equal(rank({head_rect_item_uid:'0',head_rect_extra:'0'}),null);
 assert.equal(rank({head_rect_item_uid:'12345',head_rect_extra:3}),null);
 assert.equal(rank({head_rect_extra:3}),null);
 assert.equal(MobileAssets.player({head_rect_id:0,head_rect_item_uid:'0',head_rect_extra:3}).rank,null);
 assert.equal(MobileAssets.player({head_rect_id:Number(timedFrameId),head_rect_stamp:10,server_time:20,head_rect_item_uid:'0',head_rect_extra:3}).rank,null);
});

test('player returns nulls for a missing or non-object account',()=>{
 assert.deepEqual(MobileAssets.player(null),{head:null,frame:null,rank:null});
 assert.deepEqual(MobileAssets.player('bad'),{head:null,frame:null,rank:null});
});

test('player leaves rank and frame independent of head resolution',()=>{
 const view=MobileAssets.player({fashion_id:0,head_id:0,face_id:0,head_rect_item_uid:'0',head_rect_extra:9,head_rect_id:Number(permanentFrameId)});
 assert.equal(view.head,null);
 assert.equal(view.rank,9);
 assert.ok(view.frame);
});

test('loadImage resolves manifest and assets-relative paths with caching',async()=>{
 const known='assets/portraits/10000101.webp';
 const first=MobileAssets.loadImage(known);
 const second=MobileAssets.loadImage(known);
 assert.equal(first,second,'loadImage must cache by src');
 const img=await first;
 assert.ok(img instanceof FakeImage);
 assert.equal(img.src,known);
 const ui=await MobileAssets.loadImage('assets/ui/perlinNoise.png');
 assert.ok(ui instanceof FakeImage);
});

test('loadImage rejects urls, parent traversal and absolute paths',async()=>{
 await assert.rejects(MobileAssets.loadImage('https://example.com/x.png'));
 await assert.rejects(MobileAssets.loadImage('http://example.com/x.png'));
 await assert.rejects(MobileAssets.loadImage('//example.com/x.png'));
 await assert.rejects(MobileAssets.loadImage('data:image/png;base64,AAAA'));
 await assert.rejects(MobileAssets.loadImage('assets/../../secrets.txt'));
 await assert.rejects(MobileAssets.loadImage('../outside.png'));
 await assert.rejects(MobileAssets.loadImage('/assets/portraits/10000101.webp'));
 await assert.rejects(MobileAssets.loadImage('assets/portraits/10000101.webp?next=..%2f..'));
 await assert.rejects(MobileAssets.loadImage('assets/%2e%2e/outside.png'));
 await assert.rejects(MobileAssets.loadImage(''));
 await assert.rejects(MobileAssets.loadImage(null));
});

test('loadImage rejects when the image element errors',async()=>{
 class BrokenImage extends FakeImage{
  set src(value){this._src=value;queueMicrotask(()=>{if(typeof this.onerror==='function')this.onerror();});}
 }
 MobileAssets._reset()._inject({
  fetch:()=>Promise.resolve({ok:true,status:200,json:()=>Promise.resolve(JSON.parse(manifestText))}),
  Image:BrokenImage,
 });
 await MobileAssets.init();
 await assert.rejects(MobileAssets.loadImage('assets/portraits/10000101.webp'));
 MobileAssets._inject({Image:FakeImage});
 const recovered=await MobileAssets.loadImage('assets/portraits/10000101.webp');
 assert.ok(recovered instanceof FakeImage,'a transient image failure must allow a later retry');
});

test('brand and mascot expose controlled ui and mascot bundles',()=>{
 const brand=MobileAssets.brand();
 assert.deepEqual(brand,{logo:'assets/brand/starfall-logo.png',noise:'assets/ui/perlinNoise.png',primary_button:'assets/ui/primary-button.png'});
 const mascot=MobileAssets.mascot();
 assert.equal(mascot.idle.path,'assets/mascot/minigame017_gugu-idle.webp');
 assert.equal(mascot.idle.poster,'assets/mascot/minigame017_gugu-idle.png');
 assert.equal(mascot.idle.durationMs,1366);
 assert.deepEqual(mascot.reactions.map(r=>r.name),['jump_all','relive','die']);
});


test('asset geometry uses validated per-entry metadata without exposing mutable manifest arrays',async()=>{
 const custom=JSON.parse(manifestText);
 custom.characters['10000101'].crop_rect=[30,10,180,180];
 custom.characters['10000101'].source_size=[256,256];
 custom.fashions[anyFashionId].crop_rect=[80,20,90,90];
 custom.fashions[anyFashionId].source_size=[320,140];
 MobileAssets._reset()._inject({fetch:async()=>({ok:true,json:async()=>custom})});
 await MobileAssets.init();
 assert.deepEqual(MobileAssets.character('10000101').cropRect,[30,10,180,180]);
 const head=MobileAssets.player({fashion_id:anyFashionId}).head;
 assert.deepEqual(head.cropRect,[80,20,90,90]);
 head.cropRect[0]=999;
 assert.equal(MobileAssets.player({fashion_id:anyFashionId}).head.cropRect[0],80);
 custom.characters['10000101'].crop_rect=[200,10,180,180];
 assert.deepEqual(MobileAssets.character('10000101').cropRect,[38,0,180,180],'out-of-image crop uses the known bundled geometry');
 custom.fashions[anyFashionId].source_size=[-1,140];
 assert.deepEqual(MobileAssets.player({fashion_id:anyFashionId}).head.sourceSize,[320,140]);
});

test('refresh replaces mapping and image cache and exposes native manifest revision',async()=>{
 const before=await MobileAssets.loadImage('assets/portraits/10000101.webp');
 const fresh=JSON.parse(manifestText);
 fresh._revision='native-visual-r2';
 fresh.characters['10000101'].name='更新角色';
 fresh.characters['10000101'].crop_rect=[20,0,200,200];
 fresh.characters['10000101'].source_size=[256,256];
 let calls=0;
 MobileAssets._inject({fetch:async(url,options)=>{
  calls++;assert.equal(url,'./assets/manifest.json');assert.equal(options.cache,'no-store');
  return {ok:true,json:async()=>fresh};
 }});
 const [left,right]=await Promise.all([MobileAssets.refresh(),MobileAssets.refresh()]);
 assert.equal(calls,1,'simultaneous refreshes share one request');
 assert.equal(left,right);
 assert.equal(MobileAssets.revision(),'native-visual-r2');
 assert.equal(MobileAssets.character('10000101').name,'更新角色');
 assert.deepEqual(MobileAssets.character('10000101').cropRect,[20,0,200,200]);
 assert.notEqual(await MobileAssets.loadImage('assets/portraits/10000101.webp'),before,'same asset URL must reload after a native visual update');
 await MobileAssets.init();
 assert.equal(calls,1,'init uses the refreshed manifest');
});

test('a failed refresh preserves working mappings and can be retried',async()=>{
 const original=MobileAssets.character('10000101');
 MobileAssets._inject({fetch:async()=>({ok:false,status:503})});
 await assert.rejects(MobileAssets.refresh(),/503/);
 assert.deepEqual(MobileAssets.character('10000101'),original);
 assert.equal(MobileAssets.revision(),'134377281279792758');
 MobileAssets._inject({fetch:async()=>({ok:true,json:async()=>({...JSON.parse(manifestText),_revision:'recovered'})})});
 await MobileAssets.refresh();
 assert.equal(MobileAssets.revision(),'recovered');
});

test('timed frame advances from its server receipt anchor and expires frame and rank together',()=>{
 const account={head_rect_id:Number(timedFrameId),head_rect_stamp:1100,server_time:1000,server_time_received_at:10000,head_rect_item_uid:'0',head_rect_extra:3};
 const saved=JSON.stringify(account),src=manifest.account_items[timedFrameId].path;
 assert.deepEqual(MobileAssets.player(account,{nowMs:109999}).frame,{src});
 assert.equal(MobileAssets.player(account,{nowMs:109999}).rank,3);
 assert.equal(MobileAssets.player(account,{nowMs:110000}).frame,null,'exact expiry boundary is no longer visible');
 assert.equal(MobileAssets.player(account,{nowMs:110000}).rank,null);
 assert.equal(MobileAssets.player(account,{nowMs:250000}).frame,null,'saved server time must not freeze frame validity');
 assert.equal(JSON.stringify(account),saved,'clock advancement must not rewrite the archived profile');
});

test('receipt anchor preserves server clock offset and clamps a backward local clock',()=>{
 const account={head_rect_id:Number(timedFrameId),head_rect_stamp:1100,server_time:1000,server_time_received_at:2000000};
 assert.ok(MobileAssets.player(account,{nowMs:2050000}).frame,'local wall clock ahead of server must not expire a server-aligned frame early');
 assert.equal(MobileAssets.player(account,{nowMs:2100000}).frame,null);
 assert.ok(MobileAssets.player(account,{nowMs:1900000}).frame,'backward device clock must not subtract time from the server sample');
 assert.equal(MobileAssets.player({...account,head_rect_stamp:1000},{nowMs:1900000}).frame,null);
});

test('legacy timed frames expire using current wall time while permanent frames remain visible',()=>{
 const legacy={head_rect_id:Number(timedFrameId),head_rect_stamp:1100,server_time:1000};
 assert.ok(MobileAssets.player(legacy,{nowMs:1099999}).frame);
 assert.equal(MobileAssets.player(legacy,{nowMs:1100000}).frame,null);
 assert.equal(MobileAssets.player(legacy,{nowMs:1200000}).frame,null);
 assert.equal(MobileAssets.player({...legacy,server_time:1200},{nowMs:900000}).frame,null,'legacy server sample remains a lower time bound');
 assert.equal(MobileAssets.player({...legacy,server_time_received_at:0},{nowMs:1200000}).frame,null,'zero anchor is treated as a legacy profile');
 assert.ok(MobileAssets.player({...legacy,head_rect_id:Number(permanentFrameId)},{nowMs:1200000}).frame);
 assert.equal(MobileAssets.player({...legacy,head_rect_stamp:1}).frame,null,'default player clock uses the real current time');
});


test('latest exported portraits and frames resolve while genuinely absent game references stay unavailable',()=>{
 assert.equal(MobileAssets.character('10000227').name,'菲欧');
 assert.equal(MobileAssets.character('10000227').src,'assets/portraits/10000227.webp');
 assert.equal(MobileAssets.player({fashion_id:12002271}).head.src,'assets/account-rect/HeadRect_10127_1.png');
 assert.equal(MobileAssets.player({head_rect_id:21181240}).frame.src,'assets/account/Frame_3040.png');
 assert.equal(MobileAssets.player({fashion_id:12002251}).head,null);
 assert.equal(MobileAssets.player({head_rect_id:21181283}).frame,null);
 assert.deepEqual(manifest.source_counts,{characters:109,fashions:322,account_items:101});
 assert.deepEqual(manifest.unavailable_assets.map(entry=>entry.id).sort(),['12002251','12002252','12002253','12002261','12002262','12002263','21181283']);
});
