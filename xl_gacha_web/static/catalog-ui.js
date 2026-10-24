'use strict';
(() => {
 const messages={CATALOG_SOURCE_INVALID:'地址无效，请使用以 /current.json 结尾的 HTTPS 地址，不附带账号密码或参数。',CATALOG_HASH_MISMATCH:'配置下载不完整或校验失败，继续使用原配置。',CATALOG_SCHEMA_UNSUPPORTED:'配置格式暂不受支持，继续使用原配置；可能需要更新应用。',CATALOG_DOWNGRADE_REJECTED:'服务器版本较旧，已保留本机较新配置。',CATALOG_VERSION_CONFLICT:'同一版本内容发生冲突，已保留原配置。'};
 let timer=null,periodic=null,editing=false,opening=false;
 async function api(action,body={}){
  const r=await fetch(`./api/catalog${action?'/'+action:''}`,{method:action?'POST':'GET',cache:'no-store',...(action?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
  const v=await r.json();if(!r.ok)throw new Error(messages[v.error]||'配置操作未完成，原配置仍可使用。');return v;
 }
 async function show(v){
  $('catalog-version').textContent=`· ${v.version} · ${v.origin==='bundled'?'内置':'本机缓存'}`;
  if(!editing&&document.activeElement!==$('catalog-source'))$('catalog-source').value=v.source||'';
  $('catalog-status').textContent=v.phase==='checking'?'正在检查和校验配置，现有记录仍可查看。':v.phase==='unconfigured'?'尚未设置配置下载地址，正在使用本机配置。服务器配置好后可在下方填写地址。':v.phase==='error'?(messages[v.error]||'更新暂未成功，继续使用本机配置，稍后可重试。'):v.phase==='updated'?'角色与卡池配置已更新，已有存档会自动重新解析。':v.phase==='current'?'已是当前可用的最新配置。':'本机配置可用；启动后自动检查，每 6 小时最多检查一次。';
  $('catalog-check').disabled=v.phase==='checking';$('catalog-source-save').disabled=v.phase==='checking';
  clearTimeout(timer);if(v.phase==='checking')timer=setTimeout(refresh,700);
  if(v.revision&&!opening&&!state.busy&&state.report?.storage==='local'&&state.report.configuration_revision!==v.revision){
   opening=true;try{const id=state.report.account.id;const report=await LocalArchive.api('open',{id});if(state.report?.account?.id===id)showReport(report,false,{preserveView:true});}catch{}finally{opening=false;}
  }
 }
 async function refresh(){try{await show(await api(''));}catch(e){$('catalog-status').textContent=e.message;}}
 async function check(force){try{await show(await api('check',{force}));}catch(e){$('catalog-status').textContent=e.message;}}
 $('catalog-source').addEventListener('input',()=>{editing=true;});
 $('catalog-check').addEventListener('click',()=>check(true));
 $('catalog-source-save').addEventListener('click',async()=>{try{const v=await api('source',{url:$('catalog-source').value.trim()});editing=false;await show(v);}catch(e){$('catalog-status').textContent=e.message;}});
 check(false);periodic=setInterval(()=>check(false),6*3600*1000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)check(false);});
})();
