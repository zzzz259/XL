'use strict';

const MobileAssets=(()=>{
 const MANIFEST_URL='./assets/manifest.json';
 const ASSET_PREFIX='assets/';
 const deps={fetch:null,Image:null};
 let manifestPromise=null;
 let refreshPromise=null;
 let manifest=null;
 const imageCache=new Map();

 const resolveManifest=()=>{
  if(manifest)return manifest;
  throw new Error('MobileAssets: 请先调用并等待 init()');
 };
 // 仅放行受控的 assets/ 相对路径：manifest 中的路径全部满足该约束。
 const isAllowedSrc=src=>{
  if(typeof src!=='string'||src.length===0||/\s/.test(src))return false;
  if(src.includes('?')||src.includes('#')||src.includes('%'))return false;
  if(src.startsWith('/')||src.startsWith('\\')||src.startsWith('.'))return false;
  if(src.startsWith('//')||src.includes('://'))return false;
  if(/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(src))return false;
  if(src.includes('..'))return false;
  return src.startsWith(ASSET_PREFIX);
 };

 function fetchManifest(){
   const fetchImpl=deps.fetch||(typeof fetch==='function'?fetch:null);
   if(!fetchImpl)return Promise.reject(new Error('MobileAssets: 当前环境没有 fetch，无法加载 manifest'));
   return Promise.resolve()
     .then(()=>fetchImpl(MANIFEST_URL,{cache:'no-store'}))
     .then(response=>{
      if(!response||response.ok===false)throw new Error(`MobileAssets: manifest 请求失败 (${response&&response.status})`);
      return response.json();
     })
     .then(data=>{
      if(!data||typeof data!=='object'||Array.isArray(data))throw new Error('MobileAssets: manifest 格式无效');
      return data;
     });
 }
 function init(){
  if(!manifestPromise)manifestPromise=fetchManifest().then(data=>{manifest=data;return data;});
  return manifestPromise;
 }
 function refresh(){
  if(!refreshPromise){
   refreshPromise=fetchManifest().then(data=>{
    manifest=data;
    manifestPromise=Promise.resolve(data);
    imageCache.clear();
    return data;
   }).finally(()=>{refreshPromise=null;});
  }
  return refreshPromise;
 }
 function revision(){
  const source=resolveManifest();
  return source._revision??source.visual_revision??source.resource_version??null;
 }

 // CommonResource.HeadBg: 88px 圆形遮罩，320×140 的 PicLoader 位于 (-91,-23)。
 const HEAD_CROP=[91,23,88,88],HEAD_SIZE=[320,140];
 const PORTRAIT_CROP=[38,0,180,180],PORTRAIT_SIZE=[256,256];
 function geometry(entry,fallbackCrop,fallbackSize){
  const crop=entry.crop_rect,size=entry.source_size;
  const valid=Array.isArray(crop)&&crop.length===4&&Array.isArray(size)&&size.length===2
   &&[...crop,...size].every(Number.isSafeInteger)
   &&size.every(value=>value>0&&value<=4096)&&crop[0]>=0&&crop[1]>=0&&crop[2]>0&&crop[3]>0
   &&crop[0]+crop[2]<=size[0]&&crop[1]+crop[3]<=size[1];
  return {cropRect:[...(valid?crop:fallbackCrop)],sourceSize:[...(valid?size:fallbackSize)]};
 }

 function character(itemId){
  const entry=resolveManifest().characters?.[String(itemId)];
  if(!entry||!entry.path)return null;
  return {src:entry.path,name:entry.name??null,rarity:entry.rarity??null,crop:'center',...geometry(entry,PORTRAIT_CROP,PORTRAIT_SIZE)};
 }

 const fashionHead=id=>{
  const entry=resolveManifest().fashions?.[String(id)];
  return entry&&entry.rect_path?{src:entry.rect_path,crop:'game-head',...geometry(entry,HEAD_CROP,HEAD_SIZE)}:null;
 };
 const accountHead=id=>{
  const entry=resolveManifest().account_items?.[String(id)];
  return entry&&entry.type===108&&entry.path?{src:entry.path,crop:'game-head',...geometry(entry,HEAD_CROP,HEAD_SIZE)}:null;
 };
 function headFor(account){
  const fashionId=Number(account.fashion_id??0);
  if(Number.isFinite(fashionId)&&fashionId>0){
   return fashionHead(fashionId);
  }
  const headId=Number(account.head_id??0);
  if(Number.isFinite(headId)&&headId>0){
   return accountHead(headId);
  }
  const faceId=Number(account.face_id);
  if(fashionId===0&&headId===0&&Number.isFinite(faceId)&&faceId>0){
   const head=fashionHead(faceId);
   if(head)return head;
  }
  return null;
 }
 function frameFor(account,nowMs){
  const source=resolveManifest();
  const rectId=Number(account.head_rect_id);
  if(!Number.isFinite(rectId)||rectId<=0)return null;
  const entry=source.account_items?.[String(rectId)];
  if(!entry||entry.type!==109||!entry.path)return null;
  if(String(entry.effect_value)==='1'){
   const stamp=Number(account.head_rect_stamp),serverTime=Number(account.server_time);
   if(!Number.isFinite(stamp)||!Number.isFinite(serverTime))return null;
   const receivedAt=Number(account.server_time_received_at);
   // 新存档沿用服务器时钟偏差；旧存档用当前时间避免冻结已过期的头像框。
   const currentServerTime=Number.isFinite(receivedAt)&&receivedAt>0
    ?serverTime+Math.max(0,nowMs-receivedAt)/1000
    :Math.max(serverTime,Math.floor(nowMs/1000));
   if(stamp<=currentServerTime)return null;
  }
  return {src:entry.path};
 }
 function rankFor(account){
  if(String(account.head_rect_item_uid??'')!=='0')return null;
  const extra=Number(account.head_rect_extra);
  return Number.isFinite(extra)&&extra>0?extra:null;
 }
 function player(account,options={}){
  const empty={head:null,frame:null,rank:null};
  if(!account||typeof account!=='object')return empty;
  resolveManifest();
  const nowMs=Number.isFinite(options.nowMs)?options.nowMs:Date.now();
  const frame=frameFor(account,nowMs);
  return {head:headFor(account),frame,rank:frame?rankFor(account):null};
 }

 function brand(){
  return resolveManifest().brand||null;
 }
 function mascot(){
  return resolveManifest().mascot||null;
 }

 function loadImage(src){
  if(!isAllowedSrc(src))return Promise.reject(new Error(`MobileAssets: 拒绝加载非受控资源路径 ${src}`));
  const cached=imageCache.get(src);
  if(cached)return cached;
  const ImageCtor=deps.Image||(typeof Image==='function'?Image:null);
  if(typeof ImageCtor!=='function'){
   return Promise.reject(new Error('MobileAssets: 当前环境没有 Image，无法加载图片'));
  }
  const promise=new Promise((resolve,reject)=>{
   const img=new ImageCtor();
   let settled=false;
   const once=callback=>()=>{if(!settled){settled=true;callback();}};
   img.onload=once(()=>{
    if(typeof img.decode==='function'){
     Promise.resolve(img.decode()).then(()=>resolve(img),()=>resolve(img));
    }else{
     resolve(img);
    }
   });
   img.onerror=once(()=>reject(new Error(`MobileAssets: 图片加载失败 ${src}`)));
   img.src=src;
  });
  imageCache.set(src,promise);
  promise.catch(()=>{if(imageCache.get(src)===promise)imageCache.delete(src);});
  return promise;
 }

 // 测试注入入口：仅用于单元测试替换 fetch/Image，生产代码不调用。
 function _inject(injection){
  if(injection&&typeof injection==='object'){
   if(typeof injection.fetch==='function')deps.fetch=injection.fetch;
   if(typeof injection.Image==='function')deps.Image=injection.Image;
  }
  return api;
 }
 function _reset(){
  manifestPromise=null;
  refreshPromise=null;
  manifest=null;
  imageCache.clear();
  return api;
 }

 const api={init,refresh,revision,character,player,brand,mascot,loadImage,_inject,_reset};
 return api;
})();

if(typeof module!=='undefined')module.exports=MobileAssets;
