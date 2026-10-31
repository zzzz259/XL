'use strict';

const {test}=require('node:test');
const assert=require('node:assert/strict');
const MobileMascot=require('../web/mobile-mascot.js');

const IDLE={path:'assets/mascot/idle.webp',poster:'assets/mascot/idle-poster.png',durationMs:1366};
const REACTIONS=[
 {name:'happy',path:'assets/mascot/happy.webp',durationMs:800},
 {name:'shy',path:'assets/mascot/shy.webp',durationMs:900},
 {name:'dizzy',path:'assets/mascot/dizzy.webp',durationMs:1000},
];

function createElementStub(){
 return {
  src:'',hidden:false,dataset:{},
  listeners:{},
  addEventListener(type,handler){(this.listeners[type]||(this.listeners[type]=[])).push(handler);},
  removeEventListener(){},
  click(){(this.listeners.click||[]).slice().forEach((handler)=>handler());},
 };
}

function createFakeClock(){
 let now=0,nextId=1;const pending=new Map();
 return {
  setTimeout(handler,delay){const id=nextId++;pending.set(id,{at:now+delay,delay,handler});return id;},
  clearTimeout(id){pending.delete(id);},
  pending(){return [...pending.values()];},
  advance(ms){
   now+=ms;
   const due=[...pending.entries()].filter(([,task])=>task.at<=now).sort((a,b)=>a[0]-b[0]);
   for(const [id,task] of due){pending.delete(id);task.handler();}
  },
 };
}

function createMediaQuery(initial=false){
 return {
  matches:initial,listeners:[],
  addEventListener(type,handler){this.listeners.push(handler);},
  removeEventListener(){},
  change(value){this.matches=value;this.listeners.slice().forEach((handler)=>handler());},
 };
}

function createHarness(overrides={}){
 const elements={button:createElementStub(),idle:createElementStub(),reaction:createElementStub(),poster:createElementStub()};
 const clock=createFakeClock();
 const media=createMediaQuery(false);
 const manifest={idle:IDLE,reactions:REACTIONS.map((reaction)=>({...reaction}))};
 const assets={init(){},mascot(){return manifest;}};
 const mascot=MobileMascot.create({elements,assets,setTimeout:clock.setTimeout,clearTimeout:clock.clearTimeout,matchMedia:()=>media,...overrides});
 return {mascot,elements,clock,media,assets,manifest};
}

const pickName=(src)=>src.replace(/^assets\/mascot\//,'').replace(/\.webp\?run=\d+$/,'');
const durationOf=(name)=>REACTIONS.find((reaction)=>reaction.name===name).durationMs;
const flush=()=>new Promise((resolve)=>setImmediate(resolve));

test('init wires idle and poster sources, binds the click once, and starts on idle',()=>{
 const {mascot,elements}=createHarness();
 mascot.init();mascot.init();
 assert.equal(elements.idle.src,IDLE.path);
 assert.equal(elements.poster.src,IDLE.poster);
 assert.equal(elements.button.listeners.click.length,1);
 assert.equal(mascot.state,'idle');
 assert.equal(elements.button.dataset.state,'idle');
 assert.equal(elements.idle.hidden,false);
 assert.equal(elements.reaction.hidden,true);
 assert.equal(elements.poster.hidden,true);
});

test('init fails fast when required elements are missing',()=>{
 const mascot=MobileMascot.create({assets:{init(){},mascot(){return {idle:IDLE,reactions:REACTIONS};}}});
 assert.throws(()=>mascot.init(),/missing elements/);
});

test('shuffle bag deals every reaction once per bag without adjacent repeats across refills',()=>{
 const {mascot,elements,clock}=createHarness();
 mascot.init();
 const names=[];
 for(let i=0;i<REACTIONS.length*2;i++){
  elements.button.click();
  names.push(pickName(elements.reaction.src));
  clock.advance(5000);
 }
 assert.equal(new Set(names.slice(0,REACTIONS.length)).size,REACTIONS.length);
 assert.equal(new Set(names.slice(REACTIONS.length)).size,REACTIONS.length);
 for(let i=1;i<names.length;i++)assert.notEqual(names[i],names[i-1]);
});

test('two-reaction bag strictly alternates across refills',()=>{
 const two=[REACTIONS[0],REACTIONS[2]];
 const {mascot,elements,clock}=createHarness({assets:{init(){},mascot(){return {idle:IDLE,reactions:two};}}});
 mascot.init();
 const names=[];
 for(let i=0;i<4;i++){
  elements.button.click();
  names.push(pickName(elements.reaction.src));
  clock.advance(5000);
 }
 for(let i=1;i<names.length;i++)assert.notEqual(names[i],names[i-1]);
});

test('click tags the reaction src with an incrementing run sequence and marks the button reacting',()=>{
 const {mascot,elements}=createHarness();
 mascot.init();
 elements.button.click();
 assert.match(elements.reaction.src,/\?run=1$/);
 assert.equal(mascot.state,'reacting');
 assert.equal(elements.button.dataset.state,'reacting');
 assert.equal(elements.reaction.hidden,false);
 assert.equal(elements.idle.hidden,true);
 elements.button.click();
 assert.match(elements.reaction.src,/\?run=2$/);
 assert.equal(mascot.state,'reacting');
});

test('reaction timer uses the manifest durationMs then returns to idle',()=>{
 const {mascot,elements,clock}=createHarness();
 mascot.init();
 elements.button.click();
 const name=pickName(elements.reaction.src);
 const [task]=clock.pending();
 assert.equal(task.delay,durationOf(name));
 clock.advance(durationOf(name)-1);
 assert.equal(mascot.state,'reacting');
 clock.advance(1);
 assert.equal(mascot.state,'idle');
 assert.equal(elements.idle.hidden,false);
 assert.equal(elements.reaction.hidden,true);
 assert.equal(clock.pending().length,0);
});

test('rapid clicks reset the pending timer and advance to the next reaction',()=>{
 const {mascot,elements,clock}=createHarness();
 mascot.init();
 elements.button.click();
 const first=clock.pending()[0];
 clock.advance(500);
 elements.button.click();
 const remaining=clock.pending();
 assert.equal(remaining.length,1);
 const name=pickName(elements.reaction.src);
 assert.equal(remaining[0].delay,durationOf(name));
 assert.match(elements.reaction.src,/\?run=2$/);
 clock.advance(remaining[0].delay);
 assert.equal(mascot.state,'idle');
 clock.advance(first.delay);
 assert.equal(mascot.state,'idle');
});

test('reset clears the timer and returns to idle for page switches or backgrounding',()=>{
 const {mascot,elements,clock}=createHarness();
 mascot.init();
 elements.button.click();
 assert.equal(mascot.state,'reacting');
 mascot.reset();
 assert.equal(clock.pending().length,0);
 assert.equal(mascot.state,'idle');
 assert.equal(elements.button.dataset.state,'idle');
 clock.advance(10000);
 assert.equal(mascot.state,'idle');
});

test('reduced motion shows the poster, ignores clicks, and media changes reset the state',()=>{
 const {mascot,elements,clock,media}=createHarness();
 media.matches=true;
 mascot.init();
 assert.equal(mascot.state,'poster');
 assert.equal(elements.poster.hidden,false);
 assert.equal(elements.idle.hidden,true);
 assert.equal(elements.reaction.src,'');
 elements.button.click();
 assert.equal(mascot.state,'poster');
 assert.equal(elements.reaction.src,'');
 media.change(false);
 assert.equal(mascot.state,'idle');
 assert.equal(elements.idle.hidden,false);
 elements.button.click();
 assert.equal(mascot.state,'reacting');
 media.change(true);
 assert.equal(mascot.state,'poster');
 assert.equal(clock.pending().length,0);
 clock.advance(10000);
 assert.equal(mascot.state,'poster');
});

test('reaction decode failure falls back to the poster and disables interactions',async()=>{
 const {mascot,elements,clock}=createHarness();
 elements.reaction.decode=()=>Promise.reject(new Error('decode failed'));
 mascot.init();
 elements.button.click();
 assert.equal(mascot.state,'reacting');
 await flush();
 assert.equal(mascot.state,'poster');
 assert.equal(elements.poster.hidden,false);
 assert.equal(clock.pending().length,0);
 elements.button.click();
 assert.equal(mascot.state,'poster');
 assert.match(elements.reaction.src,/\?run=1$/);
});

test('idle load error shows the poster',()=>{
 const {mascot,elements}=createHarness();
 mascot.init();
 elements.idle.onerror();
 assert.equal(mascot.state,'poster');
 assert.equal(elements.button.dataset.state,'poster');
 assert.equal(elements.poster.hidden,false);
 assert.equal(elements.idle.hidden,true);
});
