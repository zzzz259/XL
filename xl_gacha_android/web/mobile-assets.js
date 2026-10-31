'use strict';

const MobileAssets=(()=>{
 const MANIFEST_URL='./assets/manifest.json';
 const ASSET_PREFIX='assets/';
 const deps={fetch:null,Image:null};
 let manifestPromise=null;
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

 function init(){
  if(!manifestPromise){
   const fetchImpl=deps.fetch||(typeof fetch==='function'?fetch:null);
   if(!fetchImpl){
    manifestPromise=Promise.reject(new Error('MobileAssets: 当前环境没有 fetch，无法加载 manifest'));
   }else{
    manifestPromise=Promise.resolve()
     .then(()=>fetchImpl(MANIFEST_URL,{cache:'no-store'}))
     .then(response=>{
      if(!response||response.ok===false)throw new Error(`MobileAssets: manifest 请求失败 (${response&&response.status})`);
      return response.json();
     })
     .then(data=>{manifest=data;return data;});
   }
  }
  return manifestPromise;
 }

 function character(itemId){
  const entry=resolveManifest().characters?.[String(itemId)];
  if(!entry||!entry.path)return null;
  return {src:entry.path,name:entry.name??null,rarity:entry.rarity??null,crop:'left'};
 }

 const fashionHead=id=>{
  const entry=resolveManifest().fashions?.[String(id)];
  return entry&&entry.path?{src:entry.path,crop:'left'}:null;
 };
 const accountHead=id=>{
  const entry=resolveManifest().account_items?.[String(id)];
  return entry&&entry.type===108&&entry.path?{src:entry.path,crop:'left'}:null;
 };
 function headFor(account){
  const fashionId=Number(account.fashion_id);
  if(Number.isFinite(fashionId)&&fashionId>0){
   const head=fashionHead(fashionId);
   if(head)return head;
  }
  const headId=Number(account.head_id);
  if(Number.isFinite(headId)&&headId>0){
   const head=accountHead(headId);
   if(head)return head;
  }
  const faceId=Number(account.face_id);
  if(Number.isFinite(faceId)&&faceId>0){
   const head=fashionHead(faceId);
   if(head)return head;
  }
  return null;
 }
 function frameFor(account){
  const source=resolveManifest();
  const rectId=Number(account.head_rect_id);
  if(!Number.isFinite(rectId)||rectId<=0)return null;
  const entry=source.account_items?.[String(rectId)];
  if(!entry||entry.type!==109||!entry.path)return null;
  if(String(entry.effect_value)==='1'){
   const stamp=Number(account.head_rect_stamp),serverTime=Number(account.server_time);
   if(!Number.isFinite(stamp)||!Number.isFinite(serverTime)||stamp<=serverTime)return null;
  }
  return {src:entry.path};
 }
 function rankFor(account){
  if(String(account.head_rect_item_uid??'')!=='0')return null;
  const extra=Number(account.head_rect_extra);
  return Number.isFinite(extra)&&extra>0?extra:null;
 }
 function player(account){
  const empty={head:null,frame:null,rank:null};
  if(!account||typeof account!=='object')return empty;
  resolveManifest();
  return {head:headFor(account),frame:frameFor(account),rank:rankFor(account)};
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
  manifest=null;
  imageCache.clear();
  return api;
 }

 const api={init,character,player,brand,mascot,loadImage,_inject,_reset};
 return api;
})();

if(typeof module!=='undefined')module.exports=MobileAssets;
