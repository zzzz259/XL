'use strict';

const MobileMascot=(()=>{
 const QUERY='(prefers-reduced-motion: reduce)';
 const ELEMENT_IDS={button:'gugu-button',idle:'gugu-idle',reaction:'gugu-reaction',poster:'gugu-poster'};

 function create(options={}){
  const opts=options||{};
  const provided=opts.elements||{};
  const doc=opts.document||(typeof document!=='undefined'?document:null);
  const assets=opts.assets||(typeof MobileAssets!=='undefined'?MobileAssets:null);
  const random=opts.random||Math.random;
  const setTimeoutFn=opts.setTimeout||globalThis.setTimeout;
  const clearTimeoutFn=opts.clearTimeout||globalThis.clearTimeout;
  const matchMediaFn=opts.matchMedia||(typeof window!=='undefined'&&typeof window.matchMedia==='function'?window.matchMedia.bind(window):()=>({matches:false,addEventListener(){},removeEventListener(){},addListener(){}}));

  let elements=null,manifest=null,reactions=[],bag=[],lastPick=-1,timer=null,runSeq=0,state='idle',media=null,bound=false;

  function resolveElements(){
   if(elements)return elements;
   elements={};
   for(const key of Object.keys(ELEMENT_IDS)){
    const id=ELEMENT_IDS[key];
    elements[key]=provided[key]||(doc&&typeof doc.getElementById==='function'?doc.getElementById(id):null);
   }
   const missing=Object.keys(ELEMENT_IDS).filter((key)=>!elements[key]);
   if(missing.length)throw new Error('MobileMascot: missing elements '+missing.map((key)=>ELEMENT_IDS[key]).join(', '));
   return elements;
  }
  function reduced(){return !!media&&!!media.matches;}
  function render(next){
   state=next;
   elements.idle.hidden=next!=='idle';
   elements.reaction.hidden=next!=='reacting';
   elements.poster.hidden=next!=='poster';
   elements.button.dataset.state=next;
  }
  function cancelTimer(){
   if(timer!==null){clearTimeoutFn(timer);timer=null;}
  }
  function backToIdle(){
   if(reduced()){render('poster');return;}
   render('idle');
  }
  function failToPoster(){
   cancelTimer();
   render('poster');
  }
  function shuffled(indices){
   const list=indices.slice();
   for(let i=list.length-1;i>0;i--){
    const j=Math.floor(random()*(i+1));
    const swap=list[i];list[i]=list[j];list[j]=swap;
   }
   return list;
  }
  function nextReaction(){
   if(!bag.length){
    bag=shuffled(reactions.map((_,index)=>index));
    if(lastPick>=0&&bag.length>1&&bag[bag.length-1]===lastPick){
     const k=Math.floor(random()*(bag.length-1));
     const swap=bag[k];bag[k]=bag[bag.length-1];bag[bag.length-1]=swap;
    }
   }
   lastPick=bag.pop();
   return reactions[lastPick];
  }
  function guardDecode(img){
   if(img&&typeof img.decode==='function'){
    const run=runSeq;
    Promise.resolve().then(()=>img.decode()).catch(()=>{if(run===runSeq)failToPoster();});
   }
  }
  function react(){
   if(!bound||state==='poster'||reduced()||!reactions.length)return;
   cancelTimer();
   const picked=nextReaction();
   runSeq+=1;
   elements.reaction.src=picked.path+'?run='+runSeq;
   guardDecode(elements.reaction);
   render('reacting');
   timer=setTimeoutFn(()=>{timer=null;backToIdle();},picked.durationMs);
  }
  function reset(){
   cancelTimer();
   if(reduced())render('poster');
   else render('idle');
  }
  function onMediaChange(){reset();}
  function init(){
   if(bound)return api;
   resolveElements();
   if(!assets||typeof assets.mascot!=='function')throw new Error('MobileMascot: MobileAssets.mascot() is unavailable');
   manifest=assets.mascot();
   if(!manifest||!manifest.idle||!Array.isArray(manifest.reactions))throw new Error('MobileMascot: mascot manifest is missing idle or reactions');
   reactions=manifest.reactions;
   if(manifest.idle.path)elements.idle.src=manifest.idle.path;
   if(manifest.idle.poster)elements.poster.src=manifest.idle.poster;
   elements.idle.onerror=failToPoster;
   elements.reaction.onerror=failToPoster;
   guardDecode(elements.idle);
   media=matchMediaFn(QUERY)||null;
   if(media){
    if(typeof media.addEventListener==='function')media.addEventListener('change',onMediaChange);
    else if(typeof media.addListener==='function')media.addListener(onMediaChange);
   }
   elements.button.addEventListener('click',react);
   bound=true;
   if(reduced())render('poster');
   else render('idle');
   return api;
  }
  const api={init,reset,react,get state(){return state;}};
  return api;
 }

 return {create};
})();

if(typeof module!=='undefined')module.exports=MobileMascot;
