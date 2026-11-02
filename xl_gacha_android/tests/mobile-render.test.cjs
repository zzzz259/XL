'use strict';

const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function harness(){
 class Element{
  constructor(tag='div',text=''){this.tagName=tag;this.children=[];this.dataset={};this.style={};this.className='';this._text=text;this.hidden=false;}
  get childNodes(){return this.children;}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
  set textContent(text){this._text=String(text);this.children=[];}
  append(...children){this.children.push(...children);}
  prepend(...children){this.children.unshift(...children);}
  replaceChildren(...children){this._text='';this.children=[...children];}
  setAttribute(){}
  addEventListener(){}
  querySelectorAll(selector){const result=[];const visit=node=>{for(const child of node.children){if(child.className.split(/\s+/).includes(selector.slice(1)))result.push(child);visit(child);}};visit(this);return result;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 }
 const ids=new Map();
 const document={body:new Element(),createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),createTextNode:text=>new Element('#text',text),getElementById:id=>{if(!ids.has(id))ids.set(id,new Element());return ids.get(id);},querySelector:()=>null,querySelectorAll:()=>[]};
 const insights={tags:[{key:'up_140',text:'旧文案',count:2}],career:null,recent:null,multi:{byPool:{}}};
 const context=vm.createContext({document,MobileAssets:{character:()=>null,player:()=>({head:null,frame:null,rank:null})},MobileView:{number:String,eventGrade:()=>null,pools:()=>[]},MobileServices:{accountName:()=> '合成玩家'},MobileInsights:{analyze:()=>insights,tagText:()=> '吃满140*2'}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/mobile-render.js'),'utf8')+'\nglobalThis.render=MobileRender;',context);
 return {render:context.render,document,insights};
}

function fixture(){
 const positions=[34,33,27,23,22,21,17,12,11];
 const events=positions.map(position=>({in_pool_pull:position,item_id:10000101,name:`角色${position}`,outcome:position===33?'off':'up',exact:true,pulls:position,guaranteed:position===22,time_beijing:'2026-11-02 12:00:00'}));
 const pool={poolId:9,category:'limited',name:'合成限定池',firstDate:'2026-11-01',lastDate:'2026-11-02',pullCount:34,featuredIds:[],featuredNames:[],events,characters:[],hardPity:70,pityText:'0'};
 const bursts=[{timestamp:3,record_id:3,positions:[33,34],five_count:2,exact:true},{timestamp:2,record_id:2,positions:[21,22,23],five_count:3,exact:true},{timestamp:1,record_id:1,positions:[11,12],five_count:2,exact:false}];
 return {pool,state:{eventLimits:{},characterLimits:{},showCharacters:false,insights:{multi:{byPool:{9:bursts}}}}};
}

test('actual ten-pull members share one heading and unrelated golds stay outside each group',()=>{
 const {render}=harness(),{pool,state}=fixture(),card=render.poolCard(pool,state);
 const groups=card.querySelectorAll('.multi-pull-group');
 assert.equal(groups.length,3);
 assert.deepEqual(groups.map(group=>group.querySelectorAll('.five-event').length),[2,3,2]);
 assert.deepEqual(groups.map(group=>group.querySelector('.multi-pull-heading').textContent),['十连双金','十连三金','十连至少双金']);
 const list=card.querySelector('.five-event-list');
 assert.deepEqual(list.children.filter(child=>child.className==='five-event').map(row=>row.querySelector('.event-name').textContent),['角色27UP','角色17UP']);
 assert.equal(card.querySelectorAll('.multi').length,0,'group label appears once rather than on each row');
 assert.equal(groups[0].querySelector('.avatar-flag').textContent,'歪');
 assert.ok(groups[1].textContent.includes('大保底'),'other event metadata is retained');
});

test('truncation and load more rebuild only visible groups and never merge separate bursts',()=>{
 const {render}=harness(),{pool,state}=fixture();state.eventLimits[9]=3;
 const first=render.poolCard(pool,state);
 assert.equal(first.querySelectorAll('.five-event').length,3);
 assert.equal(first.querySelectorAll('.multi-pull-group').length,1);
 assert.equal(first.querySelector('.load-more').textContent,'继续显示五星记录 · 还有 6 条');
 state.eventLimits[9]=6;
 const second=render.poolCard(pool,state);
 assert.deepEqual(second.querySelectorAll('.multi-pull-group').map(group=>group.querySelectorAll('.five-event').length),[2,3]);
 state.eventLimits[9]=20;
 assert.equal(render.poolCard(pool,state).querySelectorAll('.multi-pull-group').length,3);
});

test('career tags use the shared insights formatter',()=>{
 const {render,document}=harness();
 render.recordPage({report:{account:{},summary:{pull_count:0,rarity_counts:{},pool_pull_counts:{}},analysis:{categories:{}}},poolCategory:'all',eventLimits:{},characterLimits:{},poolLimit:20});
 assert.equal(document.getElementById('career-tags').textContent,'吃满140*2');
});
