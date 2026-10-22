'use strict';
(() => {
  const messages={
    PORTAL_BROWSER_NOT_FOUND:'未找到可用于自动登录的浏览器。请打开“浏览器与环境检查”，指定 Edge、Chrome 或兼容 Chromium 的浏览器 exe 路径。',
    PORTAL_BROWSER_START_FAILED:'已尝试可用浏览器，仍未能建立登录连接。请在“浏览器与环境检查”运行自检并查看诊断日志。',
    PORTAL_BROWSER_CLOSED:'登录浏览器已退出，或其连接持续不可达。请重新打开登录；若窗口仍在，请运行环境自检。',
    PORTAL_DEBUG_UNAVAILABLE:'浏览器进程仍在运行，但登录连接持续不可用。请运行环境自检，或指定另一种浏览器后重试。',
    PORTAL_TIMEOUT:'等待登录超时，请重新打开官网完成登录。',
    PORTAL_BUSY:'已有一个登录流程正在进行，请先完成或取消。',
    PORTAL_PROFILE_INVALID:'这个登录会话无法使用，请选择“添加新的登录会话”。',
    PORTAL_PROFILE_IN_USE:'会话文件仍被浏览器占用，请关闭专用登录窗口后再试。',
    PORTAL_AUTH_REJECTED:'官网登录状态未被游戏服务接受，请在官网退出后重新登录。',
    PORTAL_QUERY_IN_PROGRESS:'已开始查询，请等待本次查询完成。',
    PORTAL_LOGIN_FAILED:'自动登录查询未完成，请重试或使用手动粘贴方式。',
  };
  let timer=null,active=false,starting=false;
  async function api(action,body={}) {
    const response=await fetch(`./api/login${action?'/'+action:''}`,{method:action?'POST':'GET',cache:'no-store',
      ...(action?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
    const value=await response.json();
    if(!response.ok)throw new Error(messages[value.error]||errors[value.error]||'无法连接本机登录服务。');
    return value;
  }
  function controls(value) {
    active=['opening','waiting','querying','cancelling'].includes(value.phase);
    state.busy=active;
    for(const id of ['portal-start','remember-login','login-profile','submit','demo'])$(id).disabled=active;
    $('account-select').disabled=active;
    $('portal-cancel').hidden=!active;
    $('portal-cancel').disabled=value.phase==='querying';
    $('portal-cancel').textContent=value.phase==='querying'?'正在查询，请稍候':'取消登录';
    $('forget-login').disabled=active||!$('login-profile').value;
    if(active){document.body.classList.add('show-query'); $('toggle-query').setAttribute('aria-expanded','true');}
  }
  function show(value) {
    controls(value);
    if(value.phase==='idle')return;
    $('portal-progress').hidden=false;
    $('portal-progress').className=`status ${value.phase==='error'?'error':active?'loading':''}`;
    $('portal-progress').textContent=value.phase==='error'?(messages[value.error]||errors[value.error]||messages.PORTAL_LOGIN_FAILED):(value.message||'正在处理…');
    if(value.cleanup_pending)$('portal-progress').textContent+=' 部分会话文件仍被浏览器占用，请关闭专用窗口后清理临时登录目录。';
    if(value.phase==='done'&&value.report){
      showReport(value.report); LocalArchive.refresh().catch(()=>{});
      document.body.classList.remove('show-query');
      status('已通过官网登录并完成查询，记录已按账号身份保存到本机。');
    }
  }
  async function poll() {
    try {
      const value=await api(''); show(value);
      if(active)timer=setTimeout(poll,1000);
      else refreshProfiles(value.profiles||[]);
    }catch(e){
      controls({phase:'error'}); $('portal-progress').hidden=false; $('portal-progress').textContent=e.message;
      $('portal-progress').className='status error';
    }
  }
  function refreshProfiles(profiles) {
    const chosen=$('login-profile').value;
    $('login-profile').replaceChildren(new Option('添加新的登录会话',''));
    for(const p of profiles)$('login-profile').append(new Option(p.label,p.id));
    if(profiles.some(p=>p.id===chosen))$('login-profile').value=chosen;
    else if(state.report?.account){const p=profiles.find(p=>p.account_id===state.report.account.id); if(p)$('login-profile').value=p.id;}
    $('forget-login').disabled=active||!$('login-profile').value;
  }
  $('remember-login').addEventListener('change',()=>{$('login-profile-options').hidden=!$('remember-login').checked;});
  $('login-profile').addEventListener('change',()=>{$('forget-login').disabled=active||!$('login-profile').value;});
  $('portal-start').addEventListener('click',async()=>{
    if(state.busy||starting)return;
    starting=true;
    try{
      clearTimeout(timer);
      controls({phase:'opening'}); $('portal-progress').hidden=false; $('portal-progress').textContent='正在打开专用官网登录窗口…';
      const value=await api('start',{remember:$('remember-login').checked,profile:$('remember-login').checked?($('login-profile').value||null):null});
      show(value); timer=setTimeout(poll,500);
    }catch(e){controls({phase:'error'}); status(e.message,'error');}
    finally{starting=false;}
  });
  $('portal-cancel').addEventListener('click',async()=>{
    try{show(await api('cancel')); clearTimeout(timer);timer=setTimeout(poll,300);}
    catch(e){status(e.message,'error');}
  });
  $('forget-login').addEventListener('click',async()=>{
    const profile=$('login-profile').value;
    if(!profile||active||!confirm('删除所选官网登录会话？抽卡存档会保留。'))return;
    try{await api('forget',{profile});refreshProfiles((await api('')).profiles||[]);status('已删除官网登录会话，抽卡存档不受影响。');}
    catch(e){status(e.message,'error');}
  });
  // Restore active login progress after a browser refresh; do not replace an archive with an old completed query.
  api('').then(value=>{refreshProfiles(value.profiles||[]);if(['opening','waiting','querying'].includes(value.phase)){show(value);timer=setTimeout(poll,500);}})
    .catch(()=>{});
})();
