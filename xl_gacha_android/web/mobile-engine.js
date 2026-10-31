'use strict';
const MobileEngine=(()=>{
 const names={normal:'普通池',limited:'限定池',collection:'典藏池',beginner:'启程池',unknown:'未分类'};
 const fail=(code='INVALID_ARCHIVE')=>{throw new Error(code);};
 const num=(v,min=0,max=Number.MAX_SAFE_INTEGER)=>{const n=Number(v);if(typeof v==='boolean'||v===null||v===''||!Number.isSafeInteger(n)||n<min||n>max)fail();return n;};
 const txt=(v,max=120)=>{if(typeof v!=='string'||v.length>max||/[\x00-\x1f]/.test(v))fail();return v.trim();};
 const date=stamp=>new Date((stamp+28800)*1000).toISOString().slice(0,19).replace('T',' ');
 const sort=(a,b)=>a.timestamp-b.timestamp||a.index-b.index||a.record_id-b.record_id||a.pool_id-b.pool_id;
 function analyze(records,metadata){
  const categories={},groups={},pools={};
  for(const [k,name]of Object.entries(names))categories[k]={name,pull_count:0,five_count:0,up_count:0,off_count:0,guaranteed_count:0,average_five_interval:null,since_five:0,since_five_exact:false,next_guaranteed:null,hard_pity:null};
  for(const r of [...records].sort(sort)){
   const id=r.pool_id,m=metadata[id]||{},category=names[m.category]?m.category:'unknown',group=m.pity_group||`unknown:${id}`;
   const s=groups[group]||(groups[group]={since:0,exact:false,guarantee:null,last_index:null,pool:null,carried:0});
   const continuous=s.last_index===null||r.index===s.last_index+1||(s.last_index===80&&r.index===1);
   if(s.last_index!==null&&(r.index<=0||!continuous))Object.assign(s,{since:0,exact:false,guarantee:null,carried:0});
   s.last_index=r.index;if(s.pool!==id){s.pool=id;s.carried=s.since;}
   const stats=categories[category];stats.hard_pity=m.hard_pity??null;
   const p=pools[id]||(pools[id]={pool_id:id,pool_name:r.pool_name,category,category_name:names[category],hard_pity:m.hard_pity??null,
    featured_names:m.featured_names||[],pull_count:0,five_count:0,record_count:0,first_date:r.time_beijing.slice(0,10),last_date:r.time_beijing.slice(0,10),latest_timestamp:r.timestamp,
    events:[],characters:{},tail_since_five:0,tail_exact:false,next_guaranteed:null});
   p.record_count++;p.last_date=r.time_beijing.slice(0,10);p.latest_timestamp=r.timestamp;
   for(const d of r.drops){
    const n=d.count;stats.pull_count+=n;
    const char=p.characters[d.item_id]||(p.characters[d.item_id]={item_id:d.item_id,name:d.name,rarity:d.rarity,count:0});char.count+=n;
    if(d.rarity!==5){s.since+=n;p.pull_count+=n;if(d.rarity===null)Object.assign(s,{since:0,exact:false,guarantee:null,carried:0});continue;}
    for(let i=0;i<n;i++){
     s.since++;p.pull_count++;
     const featured=m.featured_ids||[];
     const outcome=['limited','collection'].includes(category)&&featured.length?(featured.includes(d.item_id)?'up':'off'):['normal','beginner'].includes(category)?'normal':'unknown';
     const guaranteed=outcome==='up'?s.guarantee:outcome==='off'?false:null;
     p.events.push({name:d.name,item_id:d.item_id,pulls_since_five:s.since,interval_exact:s.exact,outcome,guaranteed,
      guarantee_conflict:s.guarantee===true&&outcome==='off',carried_pulls:s.carried,in_pool_pull:p.pull_count,timestamp:r.timestamp,time_beijing:r.time_beijing});
     p.five_count++;stats.five_count++;stats.up_count+=outcome==='up'?1:0;stats.off_count+=outcome==='off'?1:0;stats.guaranteed_count+=guaranteed===true?1:0;
     Object.assign(s,{since:0,exact:true,guarantee:outcome==='off'?true:outcome==='up'?false:null,carried:0});
    }
   }
   Object.assign(p,{tail_since_five:s.since,tail_exact:s.exact,next_guaranteed:s.guarantee});
   Object.assign(stats,{since_five:s.since,since_five_exact:s.exact,next_guaranteed:s.guarantee});
  }
  for(const s of Object.values(categories))s.average_five_interval=s.five_count?Math.floor((s.pull_count*200+s.five_count)/(2*s.five_count))/100:null;
  for(const p of Object.values(pools))p.characters=Object.values(p.characters).sort((a,b)=>(b.rarity||0)-(a.rarity||0)||b.count-a.count||a.item_id-b.item_id);
  return {version:2,categories,pools:Object.values(pools).sort((a,b)=>b.latest_timestamp-a.latest_timestamp||b.pool_id-a.pool_id)};
 }
 function buildReport(input,catalog,rawMeta={}){
  if(!Array.isArray(input)||input.length>200000||!rawMeta||typeof rawMeta!=='object')fail();
  const unique=new Map(),meta={};let quantity=0;
  for(const raw of input){
   if(!raw||!Array.isArray(raw.drops)||!raw.drops.length||raw.drops.length>1000)fail();
   const id=num(raw.pool_id,-Math.pow(2,52)),stamp=num(raw.timestamp,0,4102444800);
   const r={pool_id:id,pool_name:txt(catalog.pools[id]||raw.pool_name||`未知卡池(${id})`),timestamp:stamp,time_beijing:date(stamp),index:num(raw.index??0),record_id:num(raw.record_id??0),drops:[]};
   for(const d of raw.drops){const item=num(d.item_id),known=catalog.cards[item];const star=known?known[1]:d.rarity;
    r.drops.push({item_id:item,name:txt(known?known[0]:(d.name||`未知物品(${item})`)),rarity:star===null||star===undefined||star===''?null:num(star,1,5),count:num(d.count,1,10000)});}
   let key=JSON.stringify([id,stamp,r.index,r.record_id]);if(!r.index&&!r.record_id)key+=JSON.stringify(r.drops);
   if(unique.has(key)&&JSON.stringify(unique.get(key).drops)!==JSON.stringify(r.drops))fail('RECORD_CONFLICT');
   unique.set(key,r);
   if(!catalog.pool_meta[id]){const m=rawMeta[id]||{},category=m.category||'unknown';if(!names[category])fail();
    const inferred=Object.entries(catalog.pools).filter(([k,v])=>v===r.pool_name).map(([k])=>catalog.pool_meta[k]||{}),groups=[...new Set(inferred.map(v=>v.pity_group))];
    const verified=inferred.length&&groups.length===1&&groups[0]&&inferred.every(v=>v.category===category);
    meta[id]={category,pity_group:verified?groups[0]:`import:${id}`,featured_ids:[],featured_names:[],hard_pity:category==='unknown'?null:70};}
  }
  const records=[...unique.values()].sort(sort),stars={},pools={};
  for(const r of records)for(const d of r.drops){quantity+=d.count;const key=d.rarity===null?'unknown':String(d.rarity);stars[key]=(stars[key]||0)+d.count;pools[r.pool_name]=(pools[r.pool_name]||0)+d.count;}
  if(records.length>100000||quantity>1000000)fail('ARCHIVE_TOO_LARGE');
  return {schema_version:1,captured_at:new Date().toISOString(),configuration_version:catalog.version,configuration_revision:catalog._revision||catalog.version,storage:'local',coverage:'本机累计保存的可见历史，不保证覆盖全部生涯。',partial:false,
   records,queries:[],pool_metadata:meta,summary:{record_count:records.length,pull_count:quantity,rarity_counts:stars,pool_pull_counts:pools,unknown_item_count:stars.unknown||0},analysis:analyze(records,{...catalog.pool_meta,...meta})};
 }
 async function digest(value){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(b=>b.toString(16).padStart(2,'0')).join('');}
 async function identity(value={},label=''){
  const uid=txt(String(value.uid||''),64),channel=txt(value.channel||'',64);let id;
  if(uid||channel){if(!uid||!/^[-\w]{1,64}$/.test(channel))fail();id='account-'+(await digest(channel+':'+uid)).slice(0,32);if(value.id&&value.id!==id)fail('ACCOUNT_ID_MISMATCH');}
  else{id=value.id||'local-'+crypto.randomUUID().replaceAll('-','');if(!/^local-[a-f0-9]{32}$/.test(id))fail();}
  const result={id,uid,channel,label:txt(label||value.label||value.nickname||(uid?`${channel} · UID ${uid}`:'导入的账号'),60)};
  for(const [key,max] of [['nickname',60],['game_uid',64],['head_rect_item_uid',64]])if(value[key]!==undefined&&value[key]!==null&&value[key]!=='')result[key]=txt(String(value[key]),max);
  for(const key of ['level','face_id','fashion_id','head_id','head_rect_id','head_rect_stamp','head_rect_extra','server_time'])if(value[key]!==undefined&&value[key]!==null&&value[key]!=='')result[key]=num(value[key]);
  return result;
 }
 function csvRows(content){
  const rows=[];let row=[],cell='',quote=false;
  for(let i=0;i<content.length;i++){const c=content[i];
   if(quote){if(c==='"'&&content[i+1]==='"'){cell+='"';i++;}else if(c==='"')quote=false;else cell+=c;}
   else if(c==='"'&&!cell)quote=true;else if(c===','){row.push(cell);cell='';}else if(c==='\n'){row.push(cell.replace(/\r$/,''));rows.push(row);row=[];cell='';}else cell+=c;
  }
  if(quote)fail('INVALID_CSV');if(cell||row.length){row.push(cell.replace(/\r$/,''));rows.push(row);}return rows;
 }
 async function parseCSV(content,catalog){
  const rows=csvRows(content.replace(/^\ufeff/,'')),header=rows.shift()||[],required=['时间（北京时间）','卡池','记录序号','记录ID','抽次（该记录内）','物品ID','角色/物品','初始星级','数量'];
  if(required.some(h=>!header.includes(h)))fail('INVALID_CSV');
  const groups=new Map(),identities=new Map(),meta={},poolIds={};
  for(const row of rows){if(row.every(x=>!x))continue;const d=Object.fromEntries(header.map((h,i)=>[h,row[i]||'']));
   const name=txt(d['卡池']);let pool;
   if(d['卡池ID']){pool=num(d['卡池ID'],-Math.pow(2,52));if(d['卡池分类'])meta[pool]={category:d['卡池分类']};}
   else if(poolIds[name]!==undefined)pool=poolIds[name];
   else{const matches=Object.entries(catalog.pools).filter(([k,v])=>v===name).map(([k])=>Number(k));pool=matches.length===1?matches[0]:-parseInt((await digest(name)).slice(0,12),16);poolIds[name]=pool;
    if(matches.length>1){const types=[...new Set(matches.map(id=>catalog.pool_meta[id]?.category||'unknown'))];if(types.length===1)meta[pool]={category:types[0]};}}
   if(!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(d['时间（北京时间）']))fail('INVALID_CSV');
   const stamp=Date.parse(d['时间（北京时间）'].replace(' ','T')+'+08:00')/1000,index=num(d['记录序号']),rid=num(d['记录ID']);
   const key=JSON.stringify([pool,stamp,index,rid]);let r=groups.get(key);if(!r){r={pool_id:pool,pool_name:name,timestamp:stamp,index,record_id:rid,drops:{}};groups.set(key,r);}
   const pos=num(d['抽次（该记录内）'],1,1000),drop={item_id:num(d['物品ID']),name:txt(d['角色/物品']),rarity:d['初始星级']?num(d['初始星级'],1,5):null,count:num(d['数量'],1,10000)};
   if(r.drops[pos]&&JSON.stringify(r.drops[pos])!==JSON.stringify(drop))fail('RECORD_CONFLICT');r.drops[pos]=drop;
   if(d['存档ID'])identities.set(d['存档ID'],{id:d['存档ID'],label:d['账号名称'],uid:d['账号UID'],channel:d['登录渠道']});
  }
  if(!groups.size||identities.size>1)fail('INVALID_CSV');
  return {records:[...groups.values()].map(r=>{const positions=Object.keys(r.drops).map(Number).sort((a,b)=>a-b);if(positions.some((p,i)=>p!==i+1))fail('INVALID_CSV');return {...r,drops:positions.map(p=>r.drops[p])};}),pool_metadata:meta,...(identities.size?{account:[...identities.values()][0]}:{})};
 }
 return {analyze,buildReport,parseCSV,identity,fail};
})();
if(typeof module!=='undefined')module.exports=MobileEngine;
