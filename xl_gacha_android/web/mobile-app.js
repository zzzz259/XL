'use strict';

(()=>{
 function readCompact(){try{return localStorage.getItem('starfall-ui-compact')!=='0';}catch{return true;}}
 const state={tab:'query',report:null,accounts:[],demo:false,buildInfo:null,catalog:null,catalogData:null,login:{phase:'idle',profiles:[]},loginPanel:'options',loginResult:'',working:false,poolCategory:'all',poolLimit:20,eventLimits:{},characterLimits:{},filters:{search:'',pool:'',rarity:'',fiveOnly:false},recordPage:1,compact:readCompact()};
 let navigation=MobileNavigation.createNavigation('query'),toastTimer,loginTimer,catalogTimer,confirmHandler=null;
 window.mobileReady=false;
 function applyCompact(){document.body.classList.toggle('compact-ui',state.compact);const button=$('compact-toggle');button.textContent=state.compact?'简洁':'详细';button.setAttribute('aria-pressed',String(state.compact));button.setAttribute('aria-label',state.compact?'当前为简洁模式，点击显示次要信息':'当前为详细模式，点击隐藏次要信息');}
 const active=()=>['opening','waiting','querying','cancelling'].includes(state.login.phase);
 function notify(text,kind=''){clearTimeout(toastTimer);const toast=$('toast');toast.textContent=text;toast.className=`toast ${kind}`;toast.hidden=!text;if(text)toastTimer=setTimeout(()=>{toast.hidden=true;},kind==='error'?6500:4000);}
 function openDialog(id){const dialog=$(id);if(!dialog.open)dialog.showModal();}
 function closeDialog(id){const dialog=$(id);if(dialog?.open&&dialog.dataset.busy!=='true')dialog.close();}
 function applyPage(name,scrollY=0){state.tab=name;MobileRender.tabs(state);requestAnimationFrame(()=>{if(state.tab===name)window.scrollTo(0,scrollY);});}
 function switchTab(name,{replace=false,resetScroll=false}={}){const scrollY=navigation.go(name,{replace,resetScroll,scrollY:window.scrollY});applyPage(name,scrollY);}
 function renderRecords(){const result=MobileView.records(state.report,{...state.filters,page:state.recordPage});state.recordPage=result.page;MobileRender.records(state,result);}
 function renderAll(){MobileRender.tabs(state);MobileRender.recordPage(state);MobileRender.recordPoolOptions(state);renderRecords();MobileRender.profile(state);}
 function showReport(report,demo=false,preserveView=false){
  state.report=report;state.demo=demo;
  if(!preserveView){state.poolCategory='all';state.poolLimit=20;state.eventLimits={};state.characterLimits={};state.filters={search:'',pool:'',rarity:'',fiveOnly:false};state.recordPage=1;$('record-search').value='';$('record-pool').value='';$('record-rarity').value='';$('record-five-only').checked=false;$('record-five-only').disabled=false;$('record-rarity').disabled=false;}
  renderAll();if(!preserveView)switchTab('records',{replace:true,resetScroll:true});
 }
 async function refreshLibrary(restore=false){const info=await MobileServices.api('/library');state.accounts=info.accounts;if(restore&&info.last_account&&info.accounts.some(account=>account.id===info.last_account)){state.report=await MobileServices.api('/library/open',{id:info.last_account});state.demo=false;}MobileRender.profile(state);if(restore)renderAll();}
 async function run(work){if(state.working)return;state.working=true;MobileRender.login(state);try{await work();}catch(error){notify(error.message||'操作未完成，请重试。','error');}finally{state.working=false;MobileRender.login(state);}}
 function confirm(title,message,label,action){$('confirm-title').textContent=title;$('confirm-message').textContent=message;$('confirm-action').textContent=label;confirmHandler=action;openDialog('confirm-dialog');}
 function openAccount(){MobileRender.accounts(state);openDialog('account-dialog');}
 function openImport(){closeDialog('account-dialog');$('import-error').hidden=true;MobileRender.accounts(state);openDialog('import-dialog');}
 function openLogin(){state.loginPanel='options';switchTab('query');MobileRender.login(state);MobileServices.api('/login').then(value=>{handleLogin(value);}).catch(error=>notify(error.message,'error'));}
 function scheduleLogin(delay=800){clearTimeout(loginTimer);loginTimer=setTimeout(pollLogin,delay);}
 async function pollLogin(){try{const value=await MobileServices.api('/login');handleLogin(value);if(active())scheduleLogin();}catch(error){state.login={phase:'error',error:error.code||'QUERY_FAILED',profiles:state.login.profiles||[]};state.loginPanel='progress';MobileRender.login(state);}}
 function handleLogin(value){state.login=value;if(['cancelled','done','idle'].includes(value.phase))state.loginPanel='options';else if(active()||value.phase==='error')state.loginPanel='progress';MobileRender.login(state);if(value.report&&value.id!==state.loginResult){state.loginResult=value.id||String(Date.now());showReport(value.report,false);refreshLibrary(false).catch(error=>notify(error.message,'error'));notify('记录已更新并按账号保存到本机。');}}
 async function startLogin(channel,method='',forceNew=false){
  if(state.working||active())return;state.working=true;state.loginResult='';state.loginPanel='progress';state.login={phase:'opening',profiles:state.login.profiles||[]};MobileRender.login(state);
  try{const value=await MobileServices.api('/login/start',{channel,...(method?{method}:{}),remember:channel!=='bilibili'&&$('remember-login').checked,profile:channel==='official'&&!forceNew&&$('remember-login').checked?($('login-profile').value||null):null});handleLogin(value);scheduleLogin(400);}
  catch(error){state.login={phase:'error',error:error.code||'PORTAL_LOGIN_FAILED',profiles:state.login.profiles||[]};MobileRender.login(state);}
  finally{state.working=false;MobileRender.login(state);}
 }
 async function exportReport(kind){if(!state.report){notify('没有可导出的记录。','error');return;}const file=MobileServices.reportFile(state.report,kind,state.demo);MobileServices.saveBlob(new Blob([file.content],{type:file.mime}),file.filename);}
 async function checkCatalog(force){clearTimeout(catalogTimer);try{const value=await MobileServices.api('/catalog/check',{force});await updateCatalog(value);}catch(error){$('catalog-status').textContent=error.message;}}
 async function refreshCatalog(){try{await updateCatalog(await MobileServices.api('/catalog'));}catch(error){$('catalog-status').textContent=error.message;}}
 async function updateCatalog(value){
  const previous=state.catalog?.revision;value=MobileServices.catalogView(value,previous);state.catalog=value;MobileRender.catalog(value);
  if(value.revision&&value.revision!==previous&&state.report?.storage==='local'&&state.report.configuration_revision!==value.revision){const id=state.report.account.id,report=await MobileServices.api('/library/open',{id});if(state.report?.account?.id===id)showReport(report,false,true);}
  if(value.phase==='checking')catalogTimer=setTimeout(refreshCatalog,700);
 }
 function bind(){
  document.querySelectorAll('.bottom-nav [data-tab]').forEach(button=>button.addEventListener('click',()=>switchTab(button.dataset.tab)));
  $('compact-toggle').addEventListener('click',()=>{state.compact=!state.compact;applyCompact();try{localStorage.setItem('starfall-ui-compact',state.compact?'1':'0');}catch{}});
  document.addEventListener('click',event=>{const close=event.target.closest('[data-close]');if(close)closeDialog(close.dataset.close);});
  document.addEventListener('mobile-status',event=>notify(event.detail.text,event.detail.kind));
  $('account-button').addEventListener('click',openAccount);$('empty-to-accounts').addEventListener('click',openAccount);
  $('account-list').addEventListener('click',event=>{const option=event.target.closest('[data-account-id]');if(!option)return;run(async()=>{const report=await MobileServices.api('/library/open',{id:option.dataset.accountId});showReport(report,false);closeDialog('account-dialog');await refreshLibrary(false);});});
  for(const id of ['account-import','import-empty-button','profile-import'])$(id).addEventListener('click',openImport);
  for(const id of ['empty-to-query','update-record-button','profile-login'])$(id).addEventListener('click',openLogin);
  $('demo-button').addEventListener('click',()=>run(async()=>{const response=await fetch('./demo.json',{cache:'no-store'});if(!response.ok)throw new Error('演示数据暂时无法加载。');showReport(await response.json(),true);}));
  $('official-login').addEventListener('click',()=>startLogin('official'));$('tap-login').addEventListener('click',()=>startLogin('official','taptap_app'));$('bili-login').addEventListener('click',()=>startLogin('bilibili'));
  $('other-account-login').addEventListener('click',()=>{$('login-profile').value='';startLogin('official','',true);});
  $('remember-login').addEventListener('change',()=>MobileRender.login(state));$('login-profile').addEventListener('change',()=>MobileRender.login(state));
  $('forget-login').addEventListener('click',()=>{if(!$('login-profile').value)return;confirm('删除登录会话','只删除记住的官网会话，不会删除抽卡存档。','删除会话',async()=>{await MobileServices.api('/login/forget',{profile:$('login-profile').value});$('login-profile').value='';state.login=await MobileServices.api('/login');MobileRender.login(state);notify('登录会话已删除，抽卡存档未改变。');});});
  $('login-cancel').addEventListener('click',()=>{if(state.login.phase==='error'){state.loginPanel='options';state.login={phase:'idle',profiles:state.login.profiles||[]};MobileRender.login(state);return;}run(async()=>{state.login=await MobileServices.api('/login/cancel');state.loginPanel='options';MobileRender.login(state);});});
  $('manual-form').addEventListener('submit',event=>{event.preventDefault();if(state.working)return;const credential=$('manual-credential').value.trim();if(!credential){notify('请先粘贴完整登录凭据。','error');return;}if(!$('manual-consent').checked){notify('请先确认这是本人账号。','error');return;}run(async()=>{$('manual-credential').value='';$('manual-consent').checked=false;state.loginPanel='progress';state.login={phase:'querying',message:'正在验证凭据并查询记录…',profiles:state.login.profiles||[]};MobileRender.login(state);try{const report=await MobileServices.api('/query',{credential});state.login={phase:'idle',profiles:state.login.profiles||[]};state.loginPanel='options';showReport(report,false);await refreshLibrary(false);notify('查询完成，记录已保存到本机。');}catch(error){state.login={phase:'error',error:error.code||'QUERY_FAILED',profiles:state.login.profiles||[]};MobileRender.login(state);throw error;}});});
  for(const [id,method] of [['tap-diagnostics','copyTapDiagnostics'],['bili-diagnostics','copyBiliDiagnostics']])$(id).addEventListener('click',()=>run(async()=>{await window.AndroidBridge[method]();notify('已复制脱敏诊断，不包含账号凭据。');}));
  $('import-form').addEventListener('submit',event=>{event.preventDefault();const file=$('import-file').files[0];if(!file||state.working)return;state.working=true;$('import-submit').disabled=true;$('import-error').hidden=true;(async()=>{if(file.size>30*1024*1024)throw new Error('文件最多 30 MB。');const content=await file.text(),format=file.name.toLowerCase().endsWith('.csv')?'csv':'json';const result=await MobileServices.api('/library/import',{content,format,target:$('import-target').value||null,label:$('import-label').value.trim()});showReport(result.reports.at(-1),false);await refreshLibrary(false);$('import-form').reset();closeDialog('import-dialog');notify(`已导入并合并 ${result.reports.length} 个账号存档。`);})().catch(error=>{$('import-error').textContent=error.message;$('import-error').hidden=false;}).finally(()=>{state.working=false;$('import-submit').disabled=false;});});
  $('profile-export-json').addEventListener('click',()=>run(()=>exportReport('json')));$('profile-export-csv').addEventListener('click',()=>run(()=>exportReport('csv')));
  $('profile-backup').addEventListener('click',()=>run(async()=>{const backup=await MobileServices.api('/library/backup');MobileServices.saveBlob(new Blob([JSON.stringify(backup,null,2)],{type:'application/json'}),`星落-全部账号备份-${new Date().toISOString().slice(0,10)}.json`);notify(`已生成 ${backup.accounts.length} 个账号的备份。`);}));
  $('rename-account').addEventListener('click',()=>{if(!state.report||state.demo)return;$('rename-input').value=state.report.account.label;openDialog('rename-dialog');});
  $('rename-form').addEventListener('submit',event=>{event.preventDefault();run(async()=>{const report=await MobileServices.api('/library/rename',{id:state.report.account.id,label:$('rename-input').value});showReport(report,false,true);closeDialog('rename-dialog');await refreshLibrary(false);notify('存档名称已保存。');});});
  $('delete-account').addEventListener('click',()=>{if(!state.report||state.demo)return;confirm('删除本机存档',`将删除“${state.report.account.label}”的本机记录，不影响游戏账号。未备份的数据无法恢复。`,'删除存档',async()=>{await MobileServices.api('/library/delete',{id:state.report.account.id});state.report=null;state.demo=false;await refreshLibrary(false);if(state.accounts.length)showReport(await MobileServices.api('/library/open',{id:state.accounts[0].id}),false);else renderAll();notify('本机存档已删除。');});});
  $('confirm-action').addEventListener('click',()=>{const action=confirmHandler;confirmHandler=null;closeDialog('confirm-dialog');if(action)run(action);});
  for(const id of ['overview-image','profile-image'])$(id).addEventListener('click',()=>MobileImage.open(state.report,state.poolCategory));
  $('pool-category-tabs').addEventListener('click',event=>{const button=event.target.closest('[data-category]');if(!button)return;state.poolCategory=button.dataset.category;MobileRender.pools(state);});
  $('pool-list').addEventListener('click',event=>{const more=event.target.closest('[data-pool-more],[data-character-more],[data-pool-list-more]');if(!more)return;const scrollY=window.scrollY;if(more.dataset.poolListMore!==undefined)state.poolLimit+=20;else if(more.dataset.poolMore)state.eventLimits[more.dataset.poolMore]=(state.eventLimits[more.dataset.poolMore]||20)+20;else if(more.dataset.characterMore)state.characterLimits[more.dataset.characterMore]=(state.characterLimits[more.dataset.characterMore]||40)+40;MobileRender.pools(state);window.scrollTo(0,scrollY);});
  $('record-search').addEventListener('input',()=>{state.filters.search=$('record-search').value;state.recordPage=1;renderRecords();});
  $('record-pool').addEventListener('change',()=>{state.filters.pool=$('record-pool').value;state.recordPage=1;renderRecords();});
  $('record-rarity').addEventListener('change',()=>{state.filters.rarity=$('record-rarity').value;state.recordPage=1;renderRecords();});
  $('record-five-only').addEventListener('change',()=>{state.filters.fiveOnly=$('record-five-only').checked;if(state.filters.fiveOnly){state.filters.rarity='';$('record-rarity').value='';}$('record-rarity').disabled=state.filters.fiveOnly;state.recordPage=1;renderRecords();});
  $('record-filter-toggle').addEventListener('click',()=>{const hidden=$('record-filters').hidden;$('record-filters').hidden=!hidden;$('record-filter-toggle').setAttribute('aria-expanded',String(hidden));});
  $('record-prev').addEventListener('click',()=>{state.recordPage--;renderRecords();});$('record-next').addEventListener('click',()=>{state.recordPage++;renderRecords();});
  $('catalog-check').addEventListener('click',()=>checkCatalog(true));$('catalog-source-save').addEventListener('click',()=>run(async()=>{const value=await MobileServices.api('/catalog/source',{url:$('catalog-source').value.trim()});await updateCatalog(value);notify('配置地址已保存，正在检查更新。');}));
  document.querySelector('.info-trigger').addEventListener('click',()=>{switchTab('profile');const details=document.querySelector('.information-section details');details.open=true;details.scrollIntoView({block:'start'});});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)checkCatalog(false);});
 }
 window.mobileBack=()=>{const dialogs=[...document.querySelectorAll('dialog[open]')];if(dialogs.length){const dialog=dialogs.at(-1);if(dialog.dataset.busy==='true')return true;dialog.close();return true;}const previous=navigation.back(window.scrollY);if(previous){applyPage(previous.page,previous.scrollY);return true;}return false;};
 async function init(){
  applyCompact();bind();MobileImage.init({notify});renderAll();
  try{
   state.buildInfo=await MobileServices.buildInfo();state.catalogData=await fetch('./catalog.json',{cache:'no-store'}).then(response=>response.json());await refreshLibrary(true);
   const initial=MobileNavigation.initialPage(state);navigation=MobileNavigation.createNavigation(initial);switchTab(initial,{replace:true,resetScroll:true});
   const login=await MobileServices.api('/login');state.login=login;if(active()||login.pending_result||login.report){switchTab('query');handleLogin(login);if(active())scheduleLogin();}MobileRender.profile(state);await refreshCatalog();checkCatalog(false);setInterval(()=>checkCatalog(false),6*3600*1000);
  }catch(error){notify(error.message||'应用初始化未完成，请重试。','error');}
  finally{MobileRender.profile(state);window.mobileReady=true;}
 }
 init();
})();
