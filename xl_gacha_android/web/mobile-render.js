'use strict';

const $=id=>document.getElementById(id);
const MobileRender=(()=>{
 function el(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
 function icon(name,className=''){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),use=document.createElementNS('http://www.w3.org/2000/svg','use');if(className)svg.setAttribute('class',className);use.setAttribute('href',`#icon-${name}`);svg.append(use);return svg;}
 function avatar(itemId,name,rarity=5,className=''){
  const node=el('span',String(name||'?').trim().slice(0,1)||'?',`item-avatar s${rarity||0} ${className}`);node.dataset.itemId=String(itemId||'');node.dataset.rarity=String(rarity??'unknown');node.title=String(name||'未知角色');node.setAttribute('aria-label',node.title);return node;
 }
 function channel(value){return {bilibili:'B 服',sms:'官服',taptap:'TapTap',qq:'官服 QQ'}[value]||value||'本机记录';}
 function accountMeta(account,hideUid=true){if(!account)return '';const uid=String(account.uid||''),shown=hideUid&&uid.length>5?`${uid.slice(0,2)}•••${uid.slice(-3)}`:uid;return `${channel(account.channel)}${shown?` · UID ${shown}`:''}`;}
 function time(value){const date=new Date(value);return Number.isNaN(date.getTime())?'':date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});}
 function tabs(state){
  for(const name of ['records','query','profile'])$(`view-${name}`).hidden=state.tab!==name;
  document.querySelectorAll('.bottom-nav [data-tab]').forEach(button=>{const active=button.dataset.tab===state.tab;button.classList.toggle('active',active);if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');});
 }
 function accounts(state){
  const report=state.report,account=report?.account;
  $('account-button-label').textContent=account?.label||'本机存档';
  $('profile-account-name').textContent=account?.label||'尚未选择';
  $('profile-account-meta').textContent=account?accountMeta(account,false):'登录或导入后，记录会保存在本机。';
  for(const id of ['rename-account','delete-account'])$(id).disabled=!account||state.demo;
  for(const id of ['profile-export-json','profile-export-csv','profile-image','overview-image'])$(id).disabled=!report;
  $('profile-backup').disabled=!state.accounts.length;
  const list=$('account-list');list.replaceChildren();
  if(!state.accounts.length)list.append(el('div','还没有本机存档。可登录查询或从文件导入。','empty-accounts'));
  for(const item of state.accounts){
   const button=el('button',undefined,`account-option${item.id===account?.id?' active':''}`);button.type='button';button.dataset.accountId=item.id;button.dataset.uid=item.uid||'';
   const avatarNode=el('span');avatarNode.append(icon('user'));const content=el('div');content.append(el('strong',item.label),el('small',`${accountMeta(item)} · ${MobileView.number(item.pull_count)} 抽`));button.append(avatarNode,content,icon('chevron','chevron'));list.append(button);
  }
  const selected=$('import-target').value,target=$('import-target');target.replaceChildren(new Option('自动识别账号；无身份时新建存档',''));
  for(const item of state.accounts)target.append(new Option(`合并到：${item.label}`,item.id));target.value=selected;
  const receipt=report?.query_receipt;$('query-receipt').hidden=!receipt;
  if(receipt)$('query-receipt-text').textContent=`查询时间：${time(receipt.at)}\n`+receipt.queries.map(query=>`卡池 ${query.selector}：返回码 ${query.ret_code}，${query.record_count} 条记录`).join('\n');
 }
 function career(state){
  const source=state.report?.analysis?.categories||{},list=$('career-categories');list.replaceChildren();
  for(const key of ['normal','limited','collection']){
   const value=source[key];if(!value)continue;
   const card=el('article',undefined,`career-category ${key}`);card.dataset.category=key;
   card.append(el('h3',value.name));const pulls=el('div',undefined,'career-pulls');pulls.append(el('strong',MobileView.number(value.pull_count)),el('span','抽'));
   const metrics=el('div',undefined,'career-metrics'),count=el('div'),average=el('div');
   count.append(el('strong',key==='normal'?String(value.five_count):`${value.five_count} / ${value.off_count}`),el('span',key==='normal'?'出卡数':'出卡数 / 歪'));
   const avg=value.average_five_interval===null?'—':Number(value.average_five_interval).toFixed(1);average.append(el('strong',avg),el('span','五星平均'));
   metrics.append(count,average);card.append(pulls,metrics);list.append(card);
  }
  const extra=$('career-extra');extra.replaceChildren();
  for(const key of ['beginner','unknown']){const value=source[key];if(value?.pull_count)extra.append(el('span',`${value.name}另计 ${MobileView.number(value.pull_count)} 抽 · ${value.five_count} 个五星`));}
  extra.hidden=!extra.children.length;
 }
 function pity(state){
  const source=state.report?.analysis?.categories||{},list=$('pity-categories');list.replaceChildren();
  for(const key of ['normal','limited','collection']){
   const value=source[key];if(!value)continue;
   const card=el('article',undefined,`pity-category ${key}`);card.dataset.category=key;
   card.append(el('span',`${value.name}已垫`));
   const count=value.pull_count?`${value.since_five_exact?'':'≥'}${MobileView.number(value.since_five)}`:'—',limit=value.hard_pity||'—';
   card.append(el('strong',`${count} / ${limit}`));
   if(value.pull_count&&value.hard_pity){const track=el('div',undefined,'pity-track'),fill=el('div',undefined,`pity-fill${value.since_five_exact?'':' uncertain'}`);fill.style.width=`${Math.min(100,value.since_five/value.hard_pity*100)}%`;track.append(fill);card.append(track);}
   else card.append(el('small',value.pull_count?'保底规则未确认':'暂无记录'));
   list.append(card);
  }
 }
 function poolCard(pool,state){
  const card=el('article',undefined,`pool-card ${pool.category}`);card.dataset.poolId=String(pool.poolId);card.dataset.category=pool.category;
  const heading=el('div',undefined,'pool-heading'),title=el('div',undefined,'pool-title');title.append(el('h3',pool.name),el('p',`${pool.firstDate} — ${pool.lastDate}`));
  const total=el('div',undefined,'pool-total');total.append(el('strong',MobileView.number(pool.pullCount)),el('span','抽'));heading.append(title,total);card.append(heading);
  const featured=el('div',undefined,'pool-featured');
  if(pool.featuredIds.length||pool.featuredNames.length){const avatars=el('div',undefined,'pool-up-avatars'),ids=pool.featuredNames.length?pool.featuredNames:pool.featuredIds;for(const [index,id] of ids.entries())avatars.append(avatar(pool.featuredIds[index]||'',pool.featuredNames[index]||'UP 角色',5,'up-avatar'));featured.append(avatars);}
  else featured.append(el('p',pool.category==='normal'?'常驻角色招募 · 不区分 UP / 歪':pool.category==='beginner'?'启程招募 · 独立计数':'UP 配置未确认'));
  card.append(featured);
  const events=el('div',undefined,'five-event-list'),limit=state.eventLimits[pool.poolId]||20;
  if(!pool.events.length)events.append(el('p','可见记录中尚未出现五星角色。','no-five'));
  for(const event of pool.events.slice(0,limit)){
   const row=el('article',undefined,'five-event'),portrait=avatar(event.item_id,event.name,5,'event-avatar');if(event.outcome==='off')portrait.append(el('span','歪','avatar-flag'));
   const body=el('div',undefined,'event-body'),name=el('div',undefined,'event-name');name.append(el('strong',event.name));
   if(event.outcome==='up')name.append(el('span','UP','event-badge up'));
   if(event.guaranteed===true)name.append(el('span','大保底','event-badge guarantee'));
   if(event.guarantee_conflict)name.append(el('span','记录待核验','event-badge conflict'));
   if(!event.exact)name.append(el('span','前序不完整','event-badge uncertain'));
   if(event.exact&&event.pulls<=30)name.append(el('span','超欧','event-badge lucky'));
   if(event.exact&&pool.hardPity&&event.pulls>60)name.append(el('span','超非','event-badge rough'));
   const track=el('div',undefined,'five-event-track'),fill=el('div',undefined,`five-event-fill ${!event.exact?'uncertain':event.pulls<=30?'early':event.pulls<=60?'middle':'late'}`);
   if(pool.hardPity)fill.style.width=`${Math.max(12,Math.min(100,event.pulls/pool.hardPity*100))}%`;else fill.classList.add('no-rule');
   fill.append(el('span',`${event.exact?'':'≥'}${event.pulls} 抽`));track.append(fill);
   const meta=el('p',`${event.time_beijing} · 本池第 ${event.in_pool_pull} 抽${event.carried_pulls?` · 跨池累计 ${event.carried_pulls} 抽`:''}`,'five-event-meta');body.append(name,track,meta);row.append(portrait,body);events.append(row);
  }
  if(pool.events.length>limit){const more=el('button',`继续显示五星记录 · 还有 ${pool.events.length-limit} 条`,'load-more');more.type='button';more.dataset.poolMore=pool.poolId;events.append(more);}
  card.append(events);
  const tail=el('p',`截至本池记录末 · 已垫 ${pool.pityText} 抽${pool.nextGuaranteed===true?' · 下个五星为大保底':''}`,'pool-tail');card.append(tail);
  if(pool.characters.length){
   const details=el('details',undefined,'characters-detail'),summary=el('summary',`全部获得角色 · ${pool.characters.length} 种`),list=el('div',undefined,'character-roster'),limitCharacters=state.characterLimits[pool.poolId]||40;
   for(const character of pool.characters.slice(0,limitCharacters))list.append(el('span',`${character.name} · ${character.rarity||'?'}★ ×${character.count}`,`roster-chip s${character.rarity||0}`));
   if(pool.characters.length>limitCharacters){const more=el('button',`继续显示角色 · 还有 ${pool.characters.length-limitCharacters} 种`,'load-more');more.type='button';more.dataset.characterMore=pool.poolId;list.append(more);}
   details.append(summary,list);card.append(details);
  }
  return card;
 }
 function pools(state){
  document.querySelectorAll('#pool-category-tabs [data-category]').forEach(button=>button.classList.toggle('active',button.dataset.category===state.poolCategory));
  const values=MobileView.pools(state.report,state.poolCategory,state.catalogData),list=$('pool-list'),limit=state.poolLimit;list.replaceChildren(...values.slice(0,limit).map(pool=>poolCard(pool,state)));
  if(values.length>limit){const more=el('button',`继续显示卡池 · 还有 ${values.length-limit} 个`,'load-more');more.type='button';more.dataset.poolListMore='';list.append(more);}
  $('pool-empty').hidden=Boolean(values.length);list.hidden=!values.length;
 }
 function distribution(state){
  const summary=state.report?.summary;if(!summary)return;
  const rarity=$('rarity-bars'),pools=$('pool-bars');rarity.replaceChildren();pools.replaceChildren();
  function bar(container,label,count,total,className=''){
   const row=el('div',undefined,'distribution-row'),track=el('div',undefined,'distribution-track'),fill=el('div',undefined,`distribution-fill ${className}`);fill.style.width=`${total?Math.max(count>0?2:0,count/total*100):0}%`;track.append(fill);row.append(el('span',label),track,el('strong',String(count)));container.append(row);
  }
  for(let star=5;star>=1;star--)bar(rarity,`${star} 星`,summary.rarity_counts[String(star)]||0,summary.pull_count,`rarity-${star}`);
  if(summary.rarity_counts.unknown)bar(rarity,'未映射',summary.rarity_counts.unknown,summary.pull_count);
  for(const [name,count] of Object.entries(summary.pool_pull_counts||{}).sort((a,b)=>b[1]-a[1]))bar(pools,name,count,summary.pull_count);
 }
 function recordPage(state){
  const report=state.report;$('records-empty').hidden=Boolean(report);$('records-content').hidden=!report;$('record-floating-actions').hidden=!report;document.body.dataset.demo=String(state.demo);
  if(!report)return;
  $('record-account-name').textContent=report.account?.label||'招募档案';$('demo-badge').hidden=!state.demo;
  $('record-account-avatar').replaceChildren(avatar('',report.account?.label||'星',null,'account-avatar-placeholder'));
  $('record-account-meta').textContent=accountMeta(report.account);$('record-account-updated').textContent=state.demo?'虚构演示数据 · 没有连接游戏账号':`更新于 ${time(report.captured_at)}`;
  $('partial-warning').hidden=!report.partial;
  $('summary-pulls').textContent=MobileView.number(report.summary.pull_count);$('summary-fives').textContent=MobileView.number(report.summary.rarity_counts['5']||0);$('summary-pools').textContent=MobileView.number(Object.keys(report.summary.pool_pull_counts||{}).length);$('summary-records').textContent=MobileView.number(report.summary.record_count);
  career(state);pity(state);pools(state);distribution(state);$('record-coverage').textContent=report.coverage||'展示本机已累计保存的可见历史，不保证覆盖账号全部生涯。';
 }
 function recordPoolOptions(state){
  const select=$('record-pool'),selected=select.value,pools=[...(state.report?.analysis?.pools||[])].sort((a,b)=>a.pool_name.localeCompare(b.pool_name,'zh-CN'));select.replaceChildren(new Option('全部卡池',''));
  for(const pool of pools)select.append(new Option(pool.pool_name,String(pool.pool_id)));select.value=selected;
 }
 function records(state,result){
  const list=$('record-list');list.replaceChildren();$('record-count').textContent=`共 ${MobileView.number(result.total)} 项掉落`;$('record-empty').hidden=result.total>0;list.hidden=!result.total;
  for(const item of result.items){
   const card=el('article',undefined,'record-item');card.dataset.rarity=item.rarity===null?'unknown':String(item.rarity);card.dataset.poolId=String(item.poolId);
   const rarity=el('span',item.rarity===null?'?':`${item.rarity}★`,`rarity-badge s${item.rarity||0}`),info=el('div',undefined,'record-item-info');info.append(el('strong',item.name),el('p',`${item.poolName} · 记录 #${item.recordIndex}`));
   const meta=el('div',undefined,'record-item-meta');meta.append(el('strong',`×${item.count}`),el('time',item.time.slice(5,16)));card.append(rarity,info,meta);list.append(card);
  }
  $('record-page').textContent=`${result.page} / ${result.pages}`;$('record-prev').disabled=result.page<=1;$('record-next').disabled=result.page>=result.pages;document.querySelector('.pagination').hidden=!result.total||result.pages<=1;
 }
 function catalog(value){
  if(!value)return;$('catalog-version').textContent=`配置 ${value.version||'未知'} · ${value.origin==='bundled'?'内置':'本机缓存'}`;
  const phases={checking:'正在检查和校验配置，现有记录仍可查看。',unconfigured:'使用本机配置，尚未设置下载地址。',updated:'配置已更新，存档会按新配置重新识别。',current:'已是最新可用配置。',error:'更新暂未成功，继续使用本机配置。'};
  $('catalog-status').textContent=value.updated===true?phases.updated:(phases[value.phase]||'本机配置可用，启动后自动检查。');if(document.activeElement!==$('catalog-source'))$('catalog-source').value=value.source||'';$('catalog-check').disabled=value.phase==='checking';$('catalog-source-save').disabled=value.phase==='checking';
 }
 function login(state){
  const value=state.login||{phase:'idle',profiles:[]},active=['opening','waiting','querying','cancelling'].includes(value.phase),progress=state.loginPanel==='progress';
  $('bili-login').hidden=!state.buildInfo?.bilibili_enabled;$('bili-diagnostics').hidden=!state.buildInfo?.bilibili_enabled;$('login-options').hidden=progress;$('login-progress').hidden=!progress;$('login-progress').classList.toggle('error',value.phase==='error');
  const titles={opening:'正在打开授权',waiting:'等待本人授权',querying:'正在查询记录',cancelling:'正在取消',done:'查询完成',error:'这次没有完成'};$('login-progress-title').textContent=titles[value.phase]||'正在处理';
  $('login-progress-text').textContent=value.phase==='error'?(value.message||MobileServices.messages[value.error]||'登录或查询未完成，请返回后重试。'):(value.message||'正在处理，请稍候…');
  $('login-cancel').hidden=!active&&value.phase!=='error';$('login-cancel').disabled=['querying','cancelling'].includes(value.phase);$('login-cancel').textContent=value.phase==='error'?'返回登录方式':value.phase==='querying'?'正在查询，请稍候':'取消登录';
  for(const id of ['official-login','tap-login','bili-login','remember-login','manual-submit','other-account-login'])$(id).disabled=active||state.working;
  $('login-profile-options').hidden=!$('remember-login').checked;
  const select=$('login-profile'),selected=select.value;select.replaceChildren(new Option('添加新的登录会话',''));for(const profile of value.profiles||[])select.append(new Option(profile.label,profile.id));select.value=selected;if(!select.value){const profile=(value.profiles||[]).find(item=>item.account_id===state.report?.account?.id);select.value=profile?profile.id:'';}$('forget-login').disabled=active||!select.value;
 }
 function profile(state){accounts(state);catalog(state.catalog);login(state);$('app-version').textContent=`Android ${state.buildInfo?.baseline?.integration_version||'0.4'}`;}
 function all(state){tabs(state);recordPage(state);recordPoolOptions(state);records(state,MobileView.records(state.report,{...state.filters,page:state.recordPage}));profile(state);}
 return {el,icon,avatar,tabs,accounts,recordPage,pools,poolCard,records,recordPoolOptions,catalog,login,profile,all,accountMeta,time};
})();
