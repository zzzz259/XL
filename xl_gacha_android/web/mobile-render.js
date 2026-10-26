'use strict';

const $=id=>document.getElementById(id);
const MobileRender=(()=>{
 function el(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
 function icon(name,className=''){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),use=document.createElementNS('http://www.w3.org/2000/svg','use');if(className)svg.setAttribute('class',className);use.setAttribute('href',`#icon-${name}`);svg.append(use);return svg;}
 function channel(value){return {bilibili:'B 服',sms:'官服',taptap:'TapTap',qq:'官服 QQ'}[value]||value||'本机记录';}
 function accountMeta(account){if(!account)return '';return `${channel(account.channel)}${account.uid?` · UID ${account.uid}`:''}`;}
 function time(value){const date=new Date(value);return Number.isNaN(date.getTime())?'':date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});}
 function tabs(state){
  for(const name of ['overview','pools','records','profile'])$(`view-${name}`).hidden=state.tab!==name;
  document.querySelectorAll('.bottom-nav [data-tab]').forEach(button=>{const active=button.dataset.tab===state.tab;button.classList.toggle('active',active);if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');});
 }
 function accounts(state){
  const report=state.report,account=report?.account;
  $('account-button-label').textContent=account?.label||'本机存档';
  $('profile-account-name').textContent=account?.label||'尚未选择';
  $('profile-account-meta').textContent=account?accountMeta(account):'登录或导入后，记录会保存在本机。';
  for(const id of ['rename-account','delete-account'])$(id).disabled=!account||state.demo;
  for(const id of ['profile-export-json','profile-export-csv','profile-image','overview-image'])$(id).disabled=!report;
  $('profile-backup').disabled=!state.accounts.length;
  const list=$('account-list');list.replaceChildren();
  if(!state.accounts.length){const empty=el('div','还没有本机存档。可登录查询或从文件导入。','empty-accounts');list.append(empty);}
  for(const item of state.accounts){
   const button=el('button',undefined,`account-option${item.id===account?.id?' active':''}`);button.type='button';button.dataset.accountId=item.id;button.dataset.uid=item.uid||'';
   const avatar=el('span');avatar.append(icon('user'));const content=el('div');content.append(el('strong',item.label),el('small',`${accountMeta(item)} · ${MobileView.number(item.pull_count)} 抽`));button.append(avatar,content,icon('chevron','chevron'));list.append(button);
  }
  const selected=$('import-target').value,target=$('import-target');target.replaceChildren(new Option('自动识别账号；无身份时新建存档',''));
  for(const item of state.accounts)target.append(new Option(`合并到：${item.label}`,item.id));target.value=selected;
  const receipt=report?.query_receipt;$('query-receipt').hidden=!receipt;
  if(receipt)$('query-receipt-text').textContent=`查询时间：${time(receipt.at)}\n`+receipt.queries.map(query=>`卡池 ${query.selector}：返回码 ${query.ret_code}，${query.record_count} 条记录`).join('\n');
 }
 function categoryCard(value){
  const card=el('article',undefined,`category-card ${value.key}`),top=el('div',undefined,'category-top'),title=el('div',undefined,'category-title');
  title.append(el('span',undefined,'category-dot'),el('h3',value.name));const total=el('div',undefined,'category-total');total.append(el('strong',MobileView.number(value.pullCount)),document.createTextNode('抽'));top.append(title,total);
  const pity=el('div',undefined,'pity-line');pity.append(el('span',value.hasRecords?'距上个五星':'暂无记录'));const count=el('strong',value.pityText);if(value.hasRecords)count.append(el('small','抽'));pity.append(count);
  card.append(top,pity);
  if(value.progress!==null){const track=el('div',undefined,'progress-track'),fill=el('div',undefined,`progress-fill${value.pityExact?'':' uncertain'}`);fill.style.width=value.progress;track.append(fill);card.append(track);}
  else if(value.hasRecords)card.append(el('p','硬保底规则未知，仅显示可见间隔。','progress-unknown'));
  const meta=el('div',undefined,'category-meta'),five=el('span'),average=el('span');five.append(el('strong',MobileView.number(value.fiveCount)),document.createTextNode(' 个五星'));average.append(document.createTextNode('平均 '),el('strong',value.average==null?'—':`${Number(value.average).toFixed(1)} 抽`));meta.append(five,average);
  if(value.key==='normal'&&value.fiveRate!==null){const rate=el('span');rate.append(document.createTextNode('五星率 '),el('strong',`${value.fiveRate.toFixed(2)}%`));meta.append(rate);}
  if(['limited','collection'].includes(value.key)){const outcome=el('span'),guaranteed=el('span');outcome.append(el('strong',`${value.upCount} / ${value.offCount}`),document.createTextNode(' UP / 歪'));guaranteed.append(document.createTextNode('大保底出货 '),el('strong',String(value.guaranteedCount)));meta.append(outcome,guaranteed);}
  card.append(meta);if(value.guaranteeText)card.append(el('p',value.guaranteeText,'guarantee-note'));return card;
 }
 function overview(state){
  const report=state.report;$('overview-empty').hidden=Boolean(report);$('overview-content').hidden=!report;document.body.dataset.demo=String(state.demo);
  if(!report)return;
  $('overview-account-title').textContent=report.account?.label||'招募档案';$('demo-badge').hidden=!state.demo;
  $('overview-updated').textContent=state.demo?'虚构演示数据 · 没有连接游戏账号':`更新于 ${time(report.captured_at)} · 已保存到本机`;
  $('partial-warning').hidden=!report.partial;
  const view=MobileView.summary(report,6);$('summary-pulls').textContent=MobileView.number(view.totalPulls);$('summary-fives').textContent=MobileView.number(view.fiveCount);$('summary-pools').textContent=MobileView.number(view.poolCount);$('summary-records').textContent=MobileView.number(report.summary.record_count);
  const categories=$('category-summary');categories.replaceChildren(...view.categories.map(categoryCard));
  const recent=$('recent-five-list');recent.replaceChildren();
  if(!view.recentFive.length)recent.append(el('p','当前记录中还没有五星角色。','section-description'));
  for(const event of view.recentFive){
   const card=el('article',undefined,'recent-card'),star=el('span',undefined,'recent-star');star.append(icon('star'));
   const info=el('div',undefined,'recent-info');info.append(el('strong',event.name),el('p',`${event.poolName} · ${event.time.slice(0,10)}`));
   const pulls=el('span',`${event.exact?'':'≥'}${event.pulls} 抽`,`recent-pull${event.exact?'':' uncertain'}`);card.append(star,info,pulls);recent.append(card);
  }
  $('overview-coverage').textContent=report.coverage||'展示本机已累计保存的可见历史，不保证覆盖账号全部生涯。';
 }
 function poolEvents(details,pool){
  if(details.dataset.rendered==='true')return;details.dataset.rendered='true';
  const content=details.querySelector('.pool-detail-content');content.replaceChildren();
  const info=el('div',undefined,'pool-progress-info');info.append(el('span',`截至记录末：距五星 ${pool.pityText} 抽`),el('span',pool.hardPity?`硬保底 ${pool.hardPity}`:'硬保底未知'));
  const track=el('div',undefined,'progress-track'),fill=el('div',undefined,`progress-fill${pool.pityExact?'':' uncertain'}`);fill.style.width=pool.progress||'0%';track.append(fill);content.append(info,track);
  const events=el('div',undefined,'five-event-list');
  if(!pool.events.length)events.append(el('p','本池可见记录中尚未出现五星。','section-description'));
  for(const event of pool.events){
   const row=el('article',undefined,'five-event'),top=el('div',undefined,'five-event-top');top.append(icon('star'),el('strong',event.name),el('span',`${event.exact?'':'≥'}${event.pulls} 抽`));
   const meta=el('p',`${event.time_beijing} · 本池第 ${event.in_pool_pull} 抽${event.carried_pulls?` · 跨池累计 ${event.carried_pulls} 抽`:''}`,'five-event-meta');row.append(top,meta);
   if(pool.hardPity){const interval=el('div',undefined,'five-event-track'),fill=el('div',undefined,`five-event-fill ${!event.exact?'uncertain':event.pulls<=30?'early':event.pulls<=60?'middle':'late'}`);fill.style.width=`${Math.max(8,Math.min(100,event.pulls/pool.hardPity*100))}%`;interval.append(fill);row.append(interval);}
   const badges=el('div',undefined,'event-badges');
   if(event.outcome==='up')badges.append(el('span','UP','event-badge up'));
   if(event.outcome==='off')badges.append(el('span','歪','event-badge off'));
   if(event.guaranteed===true)badges.append(el('span','大保底','event-badge guarantee'));
   if(event.guarantee_conflict)badges.append(el('span','记录待核验','event-badge conflict'));
   if(!event.exact)badges.append(el('span','前序不完整','event-badge'));
   if(badges.children.length)row.append(badges);events.append(row);
  }
  content.append(events);
  if(pool.characters.length){const roster=el('div',undefined,'character-roster');for(const character of pool.characters)roster.append(el('span',`${character.name} · ${character.rarity||'?'}★ ×${character.count}`,`character-chip s${character.rarity||0}`));content.append(roster);}
  const tail=el('p',`本池共 ${MobileView.number(pool.pullCount)} 抽 · ${pool.fiveCount} 个五星${pool.nextGuaranteed===true?' · 下个五星为大保底':''}`,'pool-tail');content.append(tail);
 }
 function pools(state){
  document.querySelectorAll('#pool-category-tabs [data-category]').forEach(button=>button.classList.toggle('active',button.dataset.category===state.poolCategory));
  const values=MobileView.pools(state.report,state.poolCategory),list=$('pool-list');list.replaceChildren();$('pool-empty').hidden=Boolean(values.length);list.hidden=!values.length;
  for(const pool of values){
   const details=el('details',undefined,'pool-card pool-details');details.dataset.poolId=String(pool.poolId);details.dataset.category=pool.category;details.open=state.openPools.has(String(pool.poolId));
   const summary=el('summary'),labels=el('div',undefined,'pool-labels');labels.append(el('span',pool.categoryName,`pool-category ${pool.category}`),el('span',`${pool.firstDate} — ${pool.lastDate}`,'pool-date'));
   const name=el('h3',pool.name,'pool-name'),featured=el('p',pool.featuredNames.length?`当期五星 UP · ${pool.featuredNames.join(' / ')}`:pool.category==='normal'?'常驻角色招募 · 不区分 UP / 歪':pool.category==='beginner'?'启程招募 · 独立计数':'UP 配置未确认','pool-featured');
   const numbers=el('div',undefined,'pool-numbers'),pulls=el('div'),five=el('div'),pity=el('div',undefined,'pool-pity');pulls.append(el('strong',MobileView.number(pool.pullCount)),el('span','总抽数'));five.append(el('strong',MobileView.number(pool.fiveCount)),el('span','五星'));pity.append(el('strong',pool.pityText),el('span','可见垫抽'));numbers.append(pulls,five,pity);
   summary.append(labels,name,featured,numbers);const content=el('div',undefined,'pool-detail-content');details.append(summary,content);list.append(details);if(details.open)poolEvents(details,pool);
  }
 }
 function recordPoolOptions(state){
  const select=$('record-pool'),selected=select.value,pools=[...(state.report?.analysis?.pools||[])].sort((a,b)=>a.pool_name.localeCompare(b.pool_name,'zh-CN'));select.replaceChildren(new Option('全部卡池',''));
  for(const pool of pools)select.append(new Option(pool.pool_name,String(pool.pool_id)));select.value=selected;
 }
 function records(state,result){
  const list=$('record-list');list.replaceChildren();$('record-count').textContent=`共 ${MobileView.number(result.total)} 项掉落`;
  $('record-empty').hidden=result.total>0;list.hidden=!result.total;
  for(const item of result.items){
   const card=el('article',undefined,'record-item');card.dataset.rarity=item.rarity===null?'unknown':String(item.rarity);card.dataset.poolId=String(item.poolId);
   const rarity=el('span',item.rarity===null?'?':`${item.rarity}★`,`rarity-badge s${item.rarity||0}`),info=el('div',undefined,'record-item-info');info.append(el('strong',item.name),el('p',`${item.poolName} · 记录 #${item.recordIndex}`));
   const meta=el('div',undefined,'record-item-meta');meta.append(el('strong',`×${item.count}`),el('time',item.time.slice(5,16)));card.append(rarity,info,meta);list.append(card);
  }
  $('record-page').textContent=`${result.page} / ${result.pages}`;$('record-prev').disabled=result.page<=1;$('record-next').disabled=result.page>=result.pages;
  document.querySelector('.pagination').hidden=!result.total;
 }
 function catalog(value){
  if(!value)return;$('catalog-version').textContent=`配置 ${value.version||'未知'} · ${value.origin==='bundled'?'内置':'本机缓存'}`;
  const phases={checking:'正在检查和校验配置，现有记录仍可查看。',unconfigured:'使用本机配置，尚未设置下载地址。',updated:'配置已更新，存档会按新配置重新识别。',current:'已是最新可用配置。',error:'更新暂未成功，继续使用本机配置。'};
  $('catalog-status').textContent=value.updated===true?phases.updated:(phases[value.phase]||'本机配置可用，启动后自动检查。');if(!value.source&&document.activeElement!==$('catalog-source'))$('catalog-source').value='';else if(document.activeElement!==$('catalog-source'))$('catalog-source').value=value.source||'';
  $('catalog-check').disabled=value.phase==='checking';$('catalog-source-save').disabled=value.phase==='checking';
 }
 function login(state){
  const value=state.login||{phase:'idle',profiles:[]},active=['opening','waiting','querying','cancelling'].includes(value.phase),progress=state.loginPanel==='progress';
  $('bili-login').hidden=!state.buildInfo?.bilibili_enabled;$('bili-diagnostics').hidden=!state.buildInfo?.bilibili_enabled;
  $('login-options').hidden=progress;$('login-progress').hidden=!progress;$('login-progress').classList.toggle('error',value.phase==='error');
  const titles={opening:'正在打开授权',waiting:'等待本人授权',querying:'正在查询记录',cancelling:'正在取消',done:'查询完成',error:'这次没有完成'};
  $('login-progress-title').textContent=titles[value.phase]||'正在处理';
  $('login-progress-text').textContent=value.phase==='error'?(value.message||MobileServices.messages[value.error]||'登录或查询未完成，请返回后重试。'):(value.message||'正在处理，请稍候…');
  $('login-cancel').hidden=!active&&value.phase!=='error';$('login-cancel').disabled=['querying','cancelling'].includes(value.phase);$('login-cancel').textContent=value.phase==='error'?'返回登录方式':value.phase==='querying'?'正在查询，请稍候':'取消登录';
  for(const id of ['official-login','tap-login','bili-login','remember-login','manual-submit','other-account-login'])$(id).disabled=active||state.working;
  $('login-profile-options').hidden=!$('remember-login').checked;
  const select=$('login-profile'),selected=select.value;select.replaceChildren(new Option('添加新的登录会话',''));for(const profile of value.profiles||[])select.append(new Option(profile.label,profile.id));select.value=selected;if(!select.value){const profile=(value.profiles||[]).find(item=>item.account_id===state.report?.account?.id);select.value=profile?profile.id:'';}
  $('forget-login').disabled=active||!select.value;
 }
 function profile(state){
  accounts(state);catalog(state.catalog);login(state);
  $('app-version').textContent=`Android ${state.buildInfo?.baseline?.integration_version||'0.4'}`;
 }
 function all(state){tabs(state);overview(state);recordPoolOptions(state);pools(state);records(state,MobileView.records(state.report,{...state.filters,page:state.recordPage}));profile(state);}
 return {el,icon,tabs,accounts,overview,pools,poolEvents,records,recordPoolOptions,catalog,login,profile,all,accountMeta,time};
})();
