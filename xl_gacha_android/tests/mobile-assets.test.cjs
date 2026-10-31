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
 assert.equal(manifest.resource_version,'134359843258232660');
 assert.equal(Object.keys(manifest.characters).length,108);
 assert.equal(Object.keys(manifest.fashions).length,312);
 for(const section of ['characters','fashions','account_items']){
  for(const entry of Object.values(manifest[section])){
   assert.ok(entry.path.startsWith('assets/'),`${section} path must be relative: ${entry.path}`);
   assert.ok(!entry.path.includes('..')&&!entry.path.includes('://'),`unsafe path: ${entry.path}`);
   assert.ok(manifest.files[entry.path],`missing sha256 for ${entry.path}`);
  }
 }
 for(const value of Object.values(manifest.brand))assert.ok(manifest.files[value]);
 for(const reaction of manifest.mascot.reactions)assert.ok(manifest.files[reaction.path]);
 assert.equal(manifest.mascot.idle.durationMs,1366);
 assert.equal(manifest.mascot.reactions.map(r=>r.durationMs).join(','),'1699,1366,1333');
});

test('default character resolves account rect to controlled fashion asset',()=>{
 const view=MobileAssets.character('10000101');
 assert.deepEqual(view,{src:'assets/fashions/HeadRect_10001_1.png',name:'菲尼斯',rarity:1,crop:'left'});
 for(const id of Object.keys(manifest.characters)){
  const entry=MobileAssets.character(id);
  assert.ok(entry,`character ${id} must resolve`);
  assert.equal(entry.crop,'left');
  assert.equal(entry.src,manifest.characters[id].path);
  assert.ok(fs.existsSync(path.join(__dirname,'..','web',entry.src)));
 }
});

test('unknown character id resolves to null without fabricating data',()=>{
 assert.equal(MobileAssets.character('99999999'),null);
 assert.equal(MobileAssets.character(undefined),null);
});

test('player head follows fashion_id then head_id then face_id priority',()=>{
 const fashionSrc=manifest.fashions[anyFashionId].path;
 const headSrc=manifest.account_items[headItemId].path;
 const faceSrc=manifest.fashions[otherFashionId].path;
 const withFashion=MobileAssets.player({fashion_id:Number(anyFashionId),head_id:Number(headItemId),face_id:Number(otherFashionId)});
 assert.deepEqual(withFashion.head,{src:fashionSrc,crop:'left'});
 const withHead=MobileAssets.player({fashion_id:0,head_id:Number(headItemId),face_id:Number(otherFashionId)});
 assert.deepEqual(withHead.head,{src:headSrc,crop:'left'});
 const withFace=MobileAssets.player({fashion_id:0,head_id:0,face_id:Number(otherFashionId)});
 assert.deepEqual(withFace.head,{src:faceSrc,crop:'left'});
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
 assert.deepEqual(MobileAssets.player({head_rect_id:rect,head_rect_stamp:2000,server_time:1000}).frame,{src});
 assert.equal(MobileAssets.player({head_rect_id:rect,head_rect_stamp:1000,server_time:2000}).frame,null);
 assert.equal(MobileAssets.player({head_rect_id:rect,head_rect_stamp:1000,server_time:1000}).frame,null);
 assert.equal(MobileAssets.player({head_rect_id:rect}).frame,null);
 assert.equal(MobileAssets.player({head_rect_id:rect,head_rect_stamp:2000}).frame,null);
});

test('player rank only comes from anonymous uid zero with positive extra',()=>{
 assert.equal(MobileAssets.player({head_rect_item_uid:'0',head_rect_extra:3}).rank,3);
 assert.equal(MobileAssets.player({head_rect_item_uid:0,head_rect_extra:'5'}).rank,5);
 assert.equal(MobileAssets.player({head_rect_item_uid:'0',head_rect_extra:0}).rank,null);
 assert.equal(MobileAssets.player({head_rect_item_uid:'0',head_rect_extra:'0'}).rank,null);
 assert.equal(MobileAssets.player({head_rect_item_uid:'12345',head_rect_extra:3}).rank,null);
 assert.equal(MobileAssets.player({head_rect_extra:3}).rank,null);
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
 const known='assets/fashions/HeadRect_10001_1.png';
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
 await assert.rejects(MobileAssets.loadImage('/assets/fashions/HeadRect_10001_1.png'));
 await assert.rejects(MobileAssets.loadImage('assets/fashions/HeadRect_10001_1.png?next=..%2f..'));
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
 await assert.rejects(MobileAssets.loadImage('assets/fashions/HeadRect_10001_1.png'));
 MobileAssets._inject({Image:FakeImage});
 const recovered=await MobileAssets.loadImage('assets/fashions/HeadRect_10001_1.png');
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
