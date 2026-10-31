'use strict';

const MobileServices=(()=>{
 const messages={
  INVALID_INPUT:'请粘贴完整的 JWT 字符串或官网 HP 对象。',INVALID_CREDENTIAL_INPUT:'没有识别到有效凭据，请复制完整值。',
  CREDENTIAL_EXPIRED:'登录凭据已过期，请重新登录。',WRONG_APP:'当前凭据不属于支持的星落应用。',
  APP_ID_MISMATCH:'凭据包装键与应用 ID 不一致。',AMBIGUOUS_CREDENTIAL_INPUT:'内容包含多个登录会话，请只保留一个。',
  DEVICE_MISMATCH:'凭据中的设备字段不一致。',PROVIDER_MISMATCH:'凭据中的登录渠道不一致。',
  ACTOR_NOT_FOUND:'认证成功，但没有找到对应游戏角色。',GATEWAY_NETWORK_ERROR:'无法连接游戏网关，请检查网络后重试。',
  AUTH_NETWORK_ERROR:'暂时无法连接游戏认证服务。',SERVER_MAINTENANCE:'服务端正在维护，请稍后查询。',
  BUSY:'当前查询较多，请稍后重试。',RATE_LIMITED:'操作较频繁，请一分钟后重试。',INPUT_TOO_LARGE:'提交内容过长。',
  ALL_HISTORY_QUERIES_REJECTED:'服务端没有接受抽卡历史查询。',QUERY_FAILED:'查询未完成，请稍后重试。',
  INVALID_ARCHIVE:'记录格式不正确，未保存任何数据。',INVALID_CSV:'CSV 格式无法识别。',
  ACCOUNT_ID_MISMATCH:'文件与目标存档属于不同账号。',ACCOUNT_NOT_FOUND:'这个存档已不存在，请刷新账号列表。',
  RECORD_CONFLICT:'同一条记录出现不同内容，本次未合并。',ARCHIVE_TOO_LARGE:'文件或存档超出容量限制。',
  LOCAL_STORAGE_ERROR:'无法写入本机存档，请检查应用存储空间。',INVALID_LABEL:'请输入 1–60 个字符的存档名称。',
  PORTAL_BROWSER_CLOSED:'登录窗口已退出，请重新打开。',PORTAL_TIMEOUT:'等待登录超时，请重试。',
  PORTAL_BUSY:'已有一个登录流程正在进行。',PORTAL_PROFILE_INVALID:'这个登录会话无法使用，请新建会话。',
  PORTAL_AUTH_REJECTED:'官网登录状态未被游戏服务接受，请重新登录。',PORTAL_QUERY_IN_PROGRESS:'正在查询，请等待完成。',
  PORTAL_LOGIN_FAILED:'自动登录查询未完成，请重试或使用手动凭据。',BILI_APP_NOT_FOUND:'请先安装并登录官方哔哩哔哩 App。',
  BILI_AUTH_UNAVAILABLE:'当前 B 站版本无法打开授权页面。',BILI_AUTH_RESULT_MISSING:'B 站未返回有效授权。',
  BILI_CONFIG_MISSING:'这个安装包未配置 B 服接入参数。',BILI_EXCHANGE_NETWORK_ERROR:'B 服账号交换服务连接失败。',
  BILI_EXCHANGE_REJECTED:'B 服账号交换未通过，请重新授权。',BILI_EXCHANGE_INVALID_RESPONSE:'B 服返回内容不完整。',
  TAP_AUTH_TIMEOUT:'本次 TapTap 授权已过期，请重新打开。',CATALOG_SOURCE_INVALID:'请输入有效的 HTTPS 配置清单地址。',
  CATALOG_HASH_MISMATCH:'配置下载不完整或校验失败，已保留原配置。',CATALOG_SCHEMA_UNSUPPORTED:'配置格式不受支持，请更新应用。',
  CATALOG_DOWNGRADE_REJECTED:'服务器配置较旧，已保留本机较新配置。',CATALOG_VERSION_CONFLICT:'配置版本冲突，已保留原配置。',
  STORAGE_PERMISSION_DENIED:'未获得保存图片权限，可允许后重试或直接分享。',IMAGE_SHARE_TOO_LARGE:'图片较多，请分批分享。',
  IMAGE_SHARE_FAILED:'无法打开系统分享，请重试。',IMAGE_INVALID:'图片过大或格式异常。',FILE_SAVE_FAILED:'保存失败，请检查存储空间。',
  SAVE_IN_PROGRESS:'请先完成当前文件保存。',
 };
 async function api(path,body){
  const response=await fetch(`./api${path}`,{method:body===undefined?'GET':'POST',cache:'no-store',
   ...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
  const result=await response.json();
  if(!response.ok){const code=result.error||'QUERY_FAILED',error=new Error(messages[code]||'操作未完成，请稍后重试。');error.code=code;throw error;}
  return result;
 }
 function csv(report){
  const rows=[['时间（北京时间）','卡池','记录序号','记录ID','抽次（该记录内）','物品ID','角色/物品','初始星级','数量','卡池ID','存档ID','账号名称','账号UID','登录渠道','卡池分类']];
  const account=report.account||{},categories=Object.fromEntries((report.analysis?.pools||[]).map(pool=>[pool.pool_id,pool.category]));
  for(const record of report.records)record.drops.forEach((drop,index)=>rows.push([record.time_beijing,record.pool_name,record.index,record.record_id,index+1,drop.item_id,drop.name,drop.rarity??'',drop.count,record.pool_id,account.id||'',account.label||'',account.uid||'',account.channel||'',categories[record.pool_id]||'unknown']));
  const escape=value=>{let text=String(value);if(typeof value==='string'&&/^[=+@\-\t\r]/.test(text))text="'"+text;return `"${text.replaceAll('"','""')}"`;};
  return '\ufeff'+rows.map(row=>row.map(escape).join(',')).join('\r\n');
 }
 function reportFile(report,kind,isDemo=false,now=new Date()){
  if(!report||!Array.isArray(report.records))throw new Error('没有可导出的记录。');
  if(!['json','csv'].includes(kind))throw new Error('不支持的导出格式。');
  const label=(report.account?.label||'记录').replace(/[<>:"/\\|?*\x00-\x1f]/g,'_');
  const base=`${isDemo?'demo-':''}starfall-gacha-${label}-${now.toISOString().slice(0,10)}`;
  return kind==='json'?{content:JSON.stringify(report,null,2),mime:'application/json',filename:`${base}.json`}:{content:csv(report),mime:'text/csv;charset=utf-8',filename:`${base}.csv`};
 }
 function saveBlob(blob,filename){
  const url=URL.createObjectURL(blob),anchor=document.createElement('a');anchor.href=url;anchor.download=filename;
  document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
 }
 async function buildInfo(){
  try{const response=await fetch('./build-info.json',{cache:'no-store'});if(!response.ok)throw new Error();return await response.json();}
  catch{return {bilibili_enabled:false,baseline:null};}
 }
 function catalogView(value,previousRevision){return {...value,updated:value.updated===true||Boolean(previousRevision&&value.revision&&value.revision!==previousRevision)};}
 return {api,buildInfo,catalogView,messages,reportFile,saveBlob};
})();

if(typeof module!=='undefined')module.exports=MobileServices;
