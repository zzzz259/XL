'use strict';
(() => {
 const originalFetch=window.fetch.bind(window),pending=new Map();let serial=0;
 function native(action,payload={}){return new Promise((resolve,reject)=>{const id=String(++serial);pending.set(id,{resolve,reject});AndroidHost.request(id,action,JSON.stringify(payload));});}
 window.AndroidBridge={receive(id,status,value){const p=pending.get(id);if(!p)return;pending.delete(id);if(status>=400)p.reject(new Error(value.error||'QUERY_FAILED'));else p.resolve(value);},
  async saveImage(file){return native('imageAlbum',{name:file.name,data:await imageData(file.blob)});},
  async shareImages(files){const images=[];for(const file of files){const v=await native('imageStage',{data:await imageData(file.blob)});images.push(v.image);}return native('imageShare',{images});},
  copyBiliDiagnostics(){return native('copyBiliDiagnostics');},
  copyTapDiagnostics(){return native('copyTapDiagnostics');}
 };
 async function imageData(blob){if(blob.size>8*1024*1024)throw new Error('IMAGE_INVALID');return base64(blob);}
 function base64(blob){return new Promise((resolve,reject)=>{const f=new FileReader();f.onload=()=>resolve(String(f.result).split(',')[1]);f.onerror=()=>reject(new Error('FILE_READ_FAILED'));f.readAsDataURL(blob);});}
 let database;
 function db(){return database||(database=new Promise((resolve,reject)=>{const q=indexedDB.open('starfall-local-archives',1);q.onupgradeneeded=()=>{q.result.createObjectStore('accounts',{keyPath:'id'});q.result.createObjectStore('settings');};q.onsuccess=()=>resolve(q.result);q.onerror=()=>reject(new Error('LOCAL_STORAGE_ERROR'));}));}
 async function all(){const d=await db();const rows=await new Promise((resolve,reject)=>{const q=d.transaction('accounts').objectStore('accounts').getAll();q.onsuccess=()=>resolve(q.result.map(v=>v.report));q.onerror=()=>reject(new Error('LOCAL_STORAGE_ERROR'));});
  const cfg=await catalog(),old=rows.filter(r=>r.analysis?.version!==2||r.configuration_revision!==(cfg._revision||cfg.version));
  if(old.length){for(const r of old){const fresh=MobileEngine.buildReport(r.records,cfg,r.pool_metadata);for(const k of ['analysis','records','summary','pool_metadata','configuration_version','configuration_revision'])r[k]=fresh[k];}await write(old);}return rows;
 }
 async function last(){const d=await db();return new Promise(resolve=>{const q=d.transaction('settings').objectStore('settings').get('last');q.onsuccess=()=>resolve(q.result||null);q.onerror=()=>resolve(null);});}
 async function write(reports,lastId,deleted){const d=await db();return new Promise((resolve,reject)=>{const t=d.transaction(['accounts','settings'],'readwrite');for(const r of reports)t.objectStore('accounts').put({id:r.account.id,report:r});if(deleted)t.objectStore('accounts').delete(deleted);if(lastId!==undefined)t.objectStore('settings').put(lastId,'last');t.oncomplete=()=>resolve();t.onerror=()=>reject(new Error('LOCAL_STORAGE_ERROR'));t.onabort=()=>reject(new Error('LOCAL_STORAGE_ERROR'));});}
 const catalog=()=>originalFetch('./catalog.json',{cache:'no-store'}).then(r=>r.json());
 async function merge(sources,target,label,snapshot){
  const current=await all(),map=new Map(current.map(r=>[r.account.id,r])),cfg=snapshot||await catalog(),results=[];
  for(const source of sources){
   if(!source||!Array.isArray(source.records))MobileEngine.fail();
   let id=await MobileEngine.identity(source.account,label);
   if(target){const old=map.get(target);if(!old)MobileEngine.fail('ACCOUNT_NOT_FOUND');if(source.account&&id.id!==target)MobileEngine.fail('ACCOUNT_ID_MISMATCH');id=old.account;}
   const old=map.get(id.id);if(old)id={...old.account,...id,id:old.account.id,label:old.account.label};
   const r=MobileEngine.buildReport([...(old?.records||[]),...source.records],cfg,{...(old?.pool_metadata||{}),...(source.pool_metadata||{})});
   r.account=id;r.partial=Boolean(source.partial);
   if(source.queries)r.query_receipt={at:new Date().toISOString(),queries:source.queries.map(q=>({selector:q.selector,ret_code:q.ret_code,record_count:q.record_count}))};
   else if(old?.query_receipt)r.query_receipt=old.query_receipt;
   map.set(id.id,r);results.push(r);
  }
  if(map.size>500)MobileEngine.fail('ARCHIVE_TOO_LARGE');
  await write(results,results.at(-1)?.account.id);return results;
 }
 let savedSession='';
 async function route(path,body={}){
  if(path==='/api/catalog')return native('catalogStatus');
  if(path==='/api/catalog/check')return native('catalogCheck',body);
  if(path==='/api/catalog/source')return native('catalogSource',body);
  if(path==='/api/login'){
   const v=await native('loginStatus');
   if(v.phase==='done'&&v.raw){
    if(savedSession!==v.id){const [r]=await merge([{records:v.raw.records,queries:v.raw.queries,partial:v.raw.partial,account:{...v.raw.profile,uid:v.raw.uid,channel:v.raw.channel}}],null,null,v.raw.catalog_snapshot);savedSession=v.id;v.report=r;}
    else{const rows=await all();v.report=rows.find(r=>r.account.uid===v.raw.uid&&r.account.channel===v.raw.channel);}
    delete v.raw;
    await native('loginAck',{id:v.id});
   }return v;
  }
  if(path==='/api/login/start')return native('loginStart',body);
  if(path==='/api/login/cancel')return native('loginCancel');
  if(path==='/api/login/forget')return native('loginForget',body);
  if(path==='/api/query'){const raw=await native('query',body);return (await merge([{records:raw.records,partial:raw.partial,account:{...raw.profile,uid:raw.uid,channel:raw.channel}}],null,null,raw.catalog_snapshot))[0];}
  const rows=await all();
  if(path==='/api/library')return {accounts:rows.map(r=>({...r.account,pull_count:r.summary.pull_count,updated_at:r.captured_at})).sort((a,b)=>b.updated_at.localeCompare(a.updated_at)),last_account:await last(),directory:'此应用的本地存储（更新应用会保留，卸载或清除应用数据会删除）'};
  if(path==='/api/library/backup')return {format:'starfall-local-backup',version:1,exported_at:new Date().toISOString(),accounts:rows};
  if(path==='/api/library/import'){
   if(typeof body.content!=='string'||new TextEncoder().encode(body.content).length>30*1024*1024)MobileEngine.fail('ARCHIVE_TOO_LARGE');
   const source=body.format==='csv'?await MobileEngine.parseCSV(body.content,await catalog()):JSON.parse(body.content.replace(/^\ufeff/,''));
   let sources=[source];if(source.format==='starfall-local-backup'){if(source.version!==1||body.target||!Array.isArray(source.accounts))MobileEngine.fail();sources=source.accounts;}
   if(!sources.length||sources.length>500)MobileEngine.fail();return {reports:await merge(sources,body.target,body.label)};
  }
  const report=rows.find(r=>r.account.id===body.id);if(!report)MobileEngine.fail('ACCOUNT_NOT_FOUND');
  if(path==='/api/library/open'){await write([],report.account.id);return report;}
  if(path==='/api/library/rename'){if(typeof body.label!=='string'||!body.label.trim()||body.label.length>60||/[\x00-\x1f]/.test(body.label))MobileEngine.fail('INVALID_LABEL');report.account.label=body.label.trim();await write([report]);return report;}
  if(path==='/api/library/delete'){await write([],null,body.id);return {deleted:true};}
  MobileEngine.fail('NOT_FOUND');
 }
 let queue=Promise.resolve();
 window.fetch=async(input,options={})=>{
  const url=new URL(typeof input==='string'?input:input.url,location.href);
  if(url.origin===location.origin&&url.pathname.startsWith('/api/')){
   let task;
   try{
    const body=options.body?JSON.parse(options.body):{};
    const op=()=>route(url.pathname,body);
    if(url.pathname==='/api/query'||url.pathname==='/api/login'||url.pathname.startsWith('/api/library/')){task=queue.then(op);queue=task.catch(()=>{});}else task=op();
    return new Response(JSON.stringify(await task),{status:200,headers:{'Content-Type':'application/json'}});
   }catch(e){const code=/^[A-Z_0-9]+$/.test(e.message)?e.message:'INVALID_ARCHIVE';return new Response(JSON.stringify({error:code}),{status:400,headers:{'Content-Type':'application/json'}});}
  }
  return originalFetch(input,options);
 };
 function notify(text,kind=''){document.dispatchEvent(new CustomEvent('mobile-status',{detail:{text,kind}}));}
 async function exportBlob(a){
  const response=await originalFetch(a.href),blob=await response.blob();
  const data=await base64(blob);
  const result=await native('save',{name:a.download||'星落记录',mime:blob.type||'application/octet-stream',data});
  if(result.saved)notify(result.location?`文件已保存到${result.location}。`:'文件已保存。');
 }
 const click=HTMLAnchorElement.prototype.click;
 const exportError=e=>notify(e.message==='SAVE_IN_PROGRESS'?'请先完成当前文件保存。':'文件保存失败，请重试。','error');
 HTMLAnchorElement.prototype.click=function(){if(this.href.startsWith('blob:')){exportBlob(this).catch(exportError);return;}return click.call(this);};
 document.addEventListener('click',e=>{const a=e.target.closest?.('a[download]');if(a?.href.startsWith('blob:')){e.preventDefault();exportBlob(a).catch(exportError);}});
})();
