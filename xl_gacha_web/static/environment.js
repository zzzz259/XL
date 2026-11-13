'use strict';
(() => {
 if(typeof AndroidHost!=='undefined'){document.getElementById('browser-environment').hidden=true;return;}
 const names={browser:'登录浏览器',directory:'存档目录权限',loopback:'本机回环端口',official_login:'官网登录连接',official_auth:'游戏认证连接'};
 const codes={BROWSER_FOUND:'已找到',BROWSER_NOT_FOUND:'未找到，请指定浏览器路径',DIRECTORY_WRITABLE:'可读写',DIRECTORY_NOT_WRITABLE:'不可写，请完整解压到可写目录',LOOPBACK_REACHABLE:'可连接',LOOPBACK_BLOCKED:'连接失败，请检查防火墙或安全软件',REACHABLE:'连接正常',HTTP_BLOCKED:'服务已响应，但请求被限制；请打开官网确认',NETWORK_UNREACHABLE:'连接失败，请检查网络、DNS 或官方服务状态'};
 let timer=null,editing=false;
 async function api(action,body={}){
  const r=await fetch(`./api/environment${action?'/'+action:''}`,{method:action?'POST':'GET',...(action?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
  const v=await r.json();if(!r.ok)throw new Error(v.error==='INVALID_BROWSER_PATH'?'路径无效，请选择已经存在的浏览器 exe 文件。':v.error==='PORTAL_BUSY'?'请先完成或取消当前登录，再修改浏览器。':'环境检查未完成，请重试。');return v;
 }
 function show(v){
  if(!editing&&document.activeElement!==$('browser-path'))$('browser-path').value=v.custom_path||'';
  $('diagnostic-path').textContent=v.log_path||'';
  $('environment-status').textContent=v.phase==='running'?'正在检查浏览器、目录、回环端口和官方网络…':v.phase==='done'?'检查完成；网络检查使用直接连接，不提交账号或凭据。':'可运行自检；指定路径留空时恢复自动检测。';
  $('environment-results').replaceChildren();
  for(const c of v.checks||[]){const li=node('li',`${c.status==='ok'?'✓':'!'} ${names[c.name]}：${codes[c.code]||'请重试'}`);if(c.paths?.length)li.append(node('small',c.paths.join('；')));$('environment-results').append(li);}
  clearTimeout(timer);if(v.phase==='running')timer=setTimeout(refresh,700);
 }
 async function refresh(){try{show(await api(''));}catch(e){$('environment-status').textContent=e.message;}}
 $('browser-path').addEventListener('input',()=>{editing=true;});
 $('browser-path-save').addEventListener('click',async()=>{try{const v=await api('browser',{path:$('browser-path').value});editing=false;show(v);}catch(e){$('environment-status').textContent=e.message;}});
 $('environment-check').addEventListener('click',async()=>{try{show(await api('check'));}catch(e){$('environment-status').textContent=e.message;}});
 refresh();
})();
