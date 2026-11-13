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
    BILI_APP_NOT_FOUND:'请先安装并登录官方哔哩哔哩 App，再点击 B 服授权。',
    BILI_AUTH_UNAVAILABLE:'当前 B 站版本无法打开授权页面，请更新官方哔哩哔哩 App 后重试。',
    BILI_AUTH_RESULT_MISSING:'B 站未返回有效授权，请重新确认授权。',
    BILI_CONFIG_MISSING:'这个安装包未配置 B 服接入参数，请使用提供的 B 服测试包。',
    BILI_EXCHANGE_NETWORK_ERROR:'B 服账号交换服务连接失败，请检查网络后重新授权。',
    BILI_EXCHANGE_REJECTED:'B 服账号交换未通过，请在官方 B 站重新登录并授权。',
    BILI_EXCHANGE_INVALID_RESPONSE:'B 服账号交换返回内容不完整，请重试并反馈测试结果。',
    TAP_AUTH_TIMEOUT:'本次 TapTap 授权已过期，请重新打开登录。仍可使用原网页登录方式；可复制 TapTap 脱敏诊断反馈。',
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
    for(const id of ['portal-start','tap-start','bili-start','remember-login','login-profile','submit','demo']){const el=$(id);if(el)el.disabled=active;}
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
      if(value.report.account?.channel==='bilibili'){
        const receipt=value.report.query_receipt,qs=receipt?.queries||[],ok=qs.filter(q=>q.ret_code===0).length;
        status(`B 服查询完成${qs.length?`：${ok}/${qs.length} 个卡池查询成功`:'。'}，记录已存到本机。${value.report.summary.pull_count===0?'当前返回 0 条抽卡记录。':''}`,value.report.partial?'error':undefined);
      }else status('已通过官网登录并完成查询，记录已按账号身份保存到本机。');
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
  async function startLogin(channel,method){
    if(state.busy||starting)return;
    starting=true;
    try{
      clearTimeout(timer);
      controls({phase:'opening'}); $('portal-progress').hidden=false; $('portal-progress').textContent=channel==='bilibili'?'正在打开 B 站官方授权页面…':'正在打开专用官网登录窗口…';
      const value=await api('start',{...(channel==='bilibili'?{channel}:{}),...(method?{method}:{}),remember:channel!=='bilibili'&&$('remember-login').checked,profile:channel!=='bilibili'&&$('remember-login').checked?($('login-profile').value||null):null});
      show(value); timer=setTimeout(poll,500);
    }catch(e){controls({phase:'error'}); status(e.message,'error');}
    finally{starting=false;}
  }
  $('portal-start').addEventListener('click',()=>startLogin('official'));
  $('bili-start')?.addEventListener('click',()=>startLogin('bilibili'));
  $('tap-start')?.addEventListener('click',()=>startLogin('official','taptap_app'));
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
  api('').then(value=>{refreshProfiles(value.profiles||[]);if(['opening','waiting','querying'].includes(value.phase)||value.pending_result){show(value);if(active)timer=setTimeout(poll,500);}})
    .catch(()=>{});
})();
