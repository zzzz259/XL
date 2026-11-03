package cn.starfall.gacha.local;

import android.app.*;
import android.content.*;
import android.os.*;
import android.webkit.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

public final class MobileUiIntegration extends Instrumentation {
 int checks;MainActivity activity;WebView web;
 void ok(boolean value,String name){if(!value)throw new AssertionError(name);checks++;}
 String evaluate(String script)throws Exception{
  CountDownLatch done=new CountDownLatch(1);AtomicReference<String> value=new AtomicReference<>();
  runOnMainSync(()->web.evaluateJavascript(script,v->{value.set(v);done.countDown();}));
  if(!done.await(10,TimeUnit.SECONDS))throw new AssertionError("WebView evaluation timeout");return value.get();
 }
 void await(String script)throws Exception{
  for(int i=0;i<100;i++){if("true".equals(evaluate(script)))return;SystemClock.sleep(100);}
  throw new AssertionError("WebView condition timeout: "+script);
 }
 void check(String script,String name)throws Exception{ok("true".equals(evaluate(script)),name);}
 void averageUpUi()throws Exception{
  check("(()=>{window.__qaAverageReady=false;(async()=>{const library=await MobileServices.api('/library'),report=await MobileServices.api('/library/open',{id:library.last_account});for(const key of ['normal','limited','collection'])Object.assign(report.analysis.categories[key],{pull_count:510,five_count:12,up_count:7,off_count:5,average_five_interval:42.5,since_five:7,since_five_exact:true});window.__qaAverageReport=report;MobileRender.recordPage({report,demo:false,poolCategory:'all',eventLimits:{},characterLimits:{},poolLimit:20,showCharacters:false});window.dispatchEvent(new Event('resize'));window.__qaAverageReady=true;})().catch(e=>window.__qaAverageReady=String(e));return true;})()","render reported 510 pull 7 UP 5 off fixture from an existing archive");
  await("window.__qaAverageReady===true");
  check("(()=>{const average=key=>document.querySelector('.career-category.'+key+' .career-metrics>div:last-child>strong').textContent;return average('normal')==='42.5'&&average('limited')==='72.9'&&average('collection')==='72.9';})()","UP averages use UP count while normal average retains five-star count");
  check("(()=>{const report=window.__qaAverageReport;report.analysis.categories.collection.up_count=0;MobileRender.recordPage({report,demo:false,poolCategory:'all',eventLimits:{},characterLimits:{},poolLimit:20,showCharacters:false});delete window.__qaAverageReport;return document.querySelector('.collection .career-metrics>div:last-child>strong').textContent==='—'&&document.getElementById('credential-renew')===null&&document.getElementById('credential-import')===null;})()","no UP renders a dash and experimental renewal controls stay absent");
 }
 void nicknameCompatibility()throws Exception{
  org.json.JSONObject previousLogin=MainActivity.login;String previousSession=MainActivity.session;
  try{
   byte[] actor=Wire.join(Wire.integer(2,456),Wire.integer(4,77),Wire.blob(6,"\u0016合成🌟玩家".getBytes(java.nio.charset.StandardCharsets.UTF_8)));
   org.json.JSONObject profile=new org.json.JSONObject(Wire.profile(Wire.blob(2,actor))),demo;
   try(java.io.InputStream in=getTargetContext().getAssets().open("demo.json")){demo=new org.json.JSONObject(new String(GachaClient.bounded(in,1024*1024),java.nio.charset.StandardCharsets.UTF_8));}
   MainActivity.session="nickname-control-qa";
   MainActivity.login=MainActivity.object("phase","done","id",MainActivity.session,"pending_result",true,"raw",MainActivity.object("uid","nickname-control-qa","channel","qa","profile",profile,"records",demo.getJSONArray("records"),"queries",new org.json.JSONArray(),"catalog_snapshot",MainActivity.updates.catalog()));
   check("(()=>{window.__qaNicknameDone=false;(async()=>{window.__qaNicknamePrevious=(await MobileServices.api('/library')).last_account;const value=await MobileServices.api('/login');window.__qaNicknameReport=value.report;MobileRender.recordPage({report:value.report,demo:false,poolCategory:'all',eventLimits:{},characterLimits:{},poolLimit:20,showCharacters:false});window.__qaNicknameDone=true;})().catch(e=>window.__qaNicknameDone=String(e));return true;})()","pass live Java profile through native login bridge and local save");
   await("window.__qaNicknameDone===true");
   check("window.__qaNicknameReport.account.nickname==='合成🌟玩家'&&window.__qaNicknameReport.account.game_uid==='456'&&window.__qaNicknameReport.account.level===77&&window.__qaNicknameReport.summary.pull_count===464&&document.getElementById('record-account-name').textContent==='合成🌟玩家'","leading U+0016 is removed and complete records and Unicode display survive");
   check("(()=>{window.__qaNicknameReopened=null;MobileServices.api('/library/open',{id:window.__qaNicknameReport.account.id}).then(report=>window.__qaNicknameReopened=report);return true;})()","reopen normalized local archive");
   await("window.__qaNicknameReopened?.account.nickname==='合成🌟玩家'&&window.__qaNicknameReopened.summary.pull_count===464");
   check("(()=>{window.__qaNicknameImport='';const report=window.__qaNicknameReport;MobileServices.api('/library/import',{format:'json',content:JSON.stringify({account:{...report.account,nickname:'\\u0016合成玩家'},records:report.records})}).then(()=>window.__qaNicknameImport='unexpected success').catch(error=>window.__qaNicknameImport=error.code);return true;})()","imported files retain strict profile validation");
   await("window.__qaNicknameImport!==''");
   check("window.__qaNicknameImport==='INVALID_ARCHIVE'","reject control characters in imported profile instead of relaxing archive checks");
   check("(()=>{window.__qaNicknameClean=false;(async()=>{await MobileServices.api('/library/delete',{id:window.__qaNicknameReport.account.id});if(window.__qaNicknamePrevious)await MobileServices.api('/library/open',{id:window.__qaNicknamePrevious});window.__qaNicknameClean=true;})().catch(e=>window.__qaNicknameClean=String(e));return true;})()","remove only the synthetic nickname account");
   await("window.__qaNicknameClean===true");
  }finally{MainActivity.login=previousLogin;MainActivity.session=previousSession;}
 }
 void credentialUi()throws Exception{
  check("(()=>{window.__qaCredentialApi=MobileServices.api;window.__qaCredentialCall=null;MobileServices.api=async(path,body)=>{if(path==='/library/refresh'){window.__qaCredentialCall=body.id;if(window.__qaCredentialError){const e=new Error('合成网络错误');e.code=window.__qaCredentialError;throw e;}const rows=await window.__qaCredentialApi('/library');return window.__qaCredentialApi('/library/open',{id:rows.last_account});}return window.__qaCredentialApi(path,body);};document.getElementById('update-record-button').click();return true;})()","refresh current account with isolated transport");
  await("window.__qaCredentialCall!==null&&!document.getElementById('update-record-button').disabled");
  check("!document.getElementById('view-records').hidden&&document.getElementById('credential-renew')===null&&document.getElementById('credential-import')===null","refresh stays on records and experimental renewal controls are absent");
  check("(()=>{window.__qaCredentialError='AUTH_NETWORK_ERROR';document.getElementById('update-record-button').click();return true;})()","simulate network failure");
  await("document.getElementById('toast').textContent==='合成网络错误'");
  check("!document.getElementById('view-records').hidden","network failure retains record page");
  check("(()=>{window.__qaCredentialError='CREDENTIAL_EXPIRED';document.getElementById('update-record-button').click();return true;})()","simulate expired credential");
  await("!document.getElementById('view-query').hidden");
  check("document.getElementById('toast').textContent.includes('重新登录')","expired credential opens login choices");
  check("(()=>{MobileServices.api=window.__qaCredentialApi;delete window.__qaCredentialApi;delete window.__qaCredentialError;delete window.__qaCredentialCall;return true;})()","restore native credential API");
  tab("records");
 }
 void sheets()throws Exception{
  for(String id:new String[]{"account-dialog","import-dialog","rename-dialog","confirm-dialog","manual-help-dialog","mobile-image-dialog"}){
   check("(()=>{const d=document.getElementById('"+id+"');d.showModal();d.dispatchEvent(new PointerEvent('pointerdown',{clientX:0,clientY:0,bubbles:true}));d.dispatchEvent(new MouseEvent('click',{clientX:0,clientY:0,bubbles:true}));return !d.open;})()","outside tap dismisses "+id);
  }
  check("(()=>{const d=document.getElementById('mobile-image-dialog');d.dataset.busy='true';d.showModal();d.dispatchEvent(new PointerEvent('pointerdown',{clientX:0,clientY:0}));d.dispatchEvent(new MouseEvent('click',{clientX:0,clientY:0}));const kept=d.open;delete d.dataset.busy;d.close();return kept;})()","busy image delivery cannot be dismissed");
 }
 void polishFixture(String zoom)throws Exception{
  check("(()=>{window.__qaPolishScroll=window.scrollY;const catalog={version:'qa',cards:{1:['低星',3],10000170:['合成UP',5],10000117:['合成歪',5]},pools:{991:'十连分组合成池'},pool_meta:{991:{category:'limited',pity_group:'limited',featured_ids:[10000170],featured_names:['合成UP'],hard_pity:70}}};const row=(index,timestamp,drops)=>({pool_id:991,index,timestamp,record_id:index,drops});const rows=[row(1,100,[{item_id:1,count:8},{item_id:10000170,count:1},{item_id:10000117,count:1}]),row(2,200,[{item_id:1,count:9},{item_id:10000170,count:1}]),row(3,300,[{item_id:1,count:7},{item_id:10000170,count:1},{item_id:10000117,count:1},{item_id:10000170,count:1}]),row(4,400,[{item_id:10000170,count:1}]),row(5,500,[{item_id:1,count:8},{item_id:10000170,count:1},{item_id:10000117,count:1}])];for(let i=6;i<=19;i++)rows.push(row(i,i*100,[{item_id:10000170,count:1}]));const report=MobileEngine.buildReport(rows,catalog,{});window.__qaPolishPool=MobileView.pools(report,'all',catalog)[0];window.__qaPolishState={eventLimits:{},characterLimits:{},showCharacters:false,insights:MobileInsights.analyze(report)};const fixture=document.createElement('div');fixture.id='qa-polish-fixture';window.__qaPolishFixture=fixture;fixture.append(MobileRender.poolCard(window.__qaPolishPool,window.__qaPolishState));document.getElementById('pool-list').prepend(fixture);window.__qaPolishOutcomes=[...document.querySelectorAll('.career-category.limited .career-outcomes .up,.career-category.limited .career-outcomes .off,.career-category.collection .career-outcomes .up,.career-category.collection .career-outcomes .off')].map(node=>({node,text:node.textContent}));window.__qaPolishOutcomes.forEach(({node})=>node.textContent=node.classList.contains('up')?'11':'3');window.__qaPolishAverages=[...document.querySelectorAll('.career-category.limited .career-metrics>div:last-child>strong,.career-category.collection .career-metrics>div:last-child>strong')].map(node=>({node,text:node.textContent}));window.__qaPolishAverages.forEach(({node},i)=>node.textContent=i?'40.1':'48.1');window.dispatchEvent(new Event('resize'));return true;})()","mount real-engine double/triple gold groups and large inline UP/off metrics");
  check("(()=>{const f=window.__qaPolishFixture,g=[...f.querySelectorAll('.multi-pull-group')];return f.querySelectorAll('.five-event').length===20&&g.length===2&&g.map(n=>n.querySelector('.multi-pull-heading').textContent).join('|')==='十连双金|十连三金'&&g.map(n=>n.querySelectorAll('.five-event').length).join(',')==='2,3'&&f.querySelector('.load-more').parentElement===f.querySelector('.five-event-list');})()","visible event limit keeps separate actual ten-pull groups and load-more outside");
  check("(()=>{window.__qaPolishState.eventLimits[991]=40;window.__qaPolishFixture.replaceChildren(MobileRender.poolCard(window.__qaPolishPool,window.__qaPolishState));const f=window.__qaPolishFixture,g=[...f.querySelectorAll('.multi-pull-group')];return g.length===3&&g.map(n=>[...n.querySelectorAll('.five-event')].map(r=>r.dataset.inPoolPull).join(',')).join('|')==='41,40|30,29,28|10,9'&&f.querySelectorAll('.five-event-list>.five-event').length===16&&f.querySelectorAll('.event-badge.multi').length===0;})()","load more preserves double/triple membership and leaves unrelated single golds ungrouped");
  check("(()=>{const f=window.__qaPolishFixture,reference=f.querySelector('.five-event-list>.five-event'),selectors=['.item-avatar','.event-name','.five-event-track'];return [...f.querySelectorAll('.multi-pull-group .five-event')].every(row=>selectors.every(selector=>{const a=reference.querySelector(selector).getBoundingClientRect(),b=row.querySelector(selector).getBoundingClientRect();return Math.abs(a.left-b.left)<.5&&Math.abs(a.width-b.width)<.5;}));})()","grouped portraits names and bars align with ordinary rows without horizontal indentation");
  check("(()=>{const f=window.__qaPolishFixture,badge=f.querySelector('.avatar-flag'),a=badge.parentElement.getBoundingClientRect(),b=badge.getBoundingClientRect();return badge.textContent==='歪'&&b.left>=a.left&&b.top>=a.top&&b.right<=a.right&&b.bottom<=a.bottom&&getComputedStyle(badge).zIndex==='2'&&getComputedStyle(badge.parentElement).overflow==='hidden';})()","off-banner badge stays entirely visible inside the unchanged portrait clipping viewport");
  check("(()=>{return [...document.querySelectorAll('.career-category.limited .career-outcomes,.career-category.collection .career-outcomes')].every(outcome=>{const up=outcome.querySelector('.up'),off=outcome.querySelector('.off'),a=up.getBoundingClientRect(),b=off.getBoundingClientRect(),box=outcome.parentElement.getBoundingClientRect();const cells=[...outcome.closest('.career-metrics').children].map(n=>n.getBoundingClientRect());return Math.abs(parseFloat(getComputedStyle(up).fontSize)-parseFloat(getComputedStyle(outcome.closest('.career-metrics').children[1].querySelector('strong')).fontSize))<.1&&Math.abs(parseFloat(getComputedStyle(up).fontSize)-parseFloat(getComputedStyle(off).fontSize))<.1&&getComputedStyle(outcome).whiteSpace==='nowrap'&&up.getClientRects().length===1&&off.getClientRects().length===1&&Math.abs(a.top-b.top)<1&&a.left>=box.left-1&&b.right<=box.right+1&&Math.abs(cells[0].top-cells[1].top)<1&&cells[1].left>=cells[0].right-1;});})()","UP/off and average use equal font sizes and remain beside each other on one row "+evaluate("JSON.stringify([...document.querySelectorAll('.career-category.limited .career-outcomes,.career-category.collection .career-outcomes')].map(o=>{const rect=n=>{const r=n.getBoundingClientRect();return [r.left,r.top,r.right,r.bottom];};return {zoom:document.body.dataset.textZoom,width:innerWidth,upSize:getComputedStyle(o.querySelector('.up')).fontSize,offSize:getComputedStyle(o.querySelector('.off')).fontSize,nowrap:getComputedStyle(o).whiteSpace,up:rect(o.querySelector('.up')),off:rect(o.querySelector('.off')),box:rect(o.parentElement),cells:[...o.closest('.career-metrics').children].map(rect)};}))"));
  check("(()=>{const tags=[{key:'up_140',text:'吃满140',count:1},{key:'up_140',text:'吃满140',count:2}];return tags.map(MobileInsights.tagText).join('|')==='吃满140|吃满140*2';})()","shared tag formatter shows a single completed cycle without a multiplier and repeats with an asterisk");
  check("(()=>{document.getElementById('career-summary').scrollIntoView({block:'start'});return true;})()","position enlarged career metrics for screenshot");
  check("(()=>{const text=document.querySelector('.limited .career-pity-text'),badge=MobileRender.el('span','保','career-guarantee');badge.id='qa-pity-badge';text.append(badge);window.dispatchEvent(new Event('resize'));return true;})()","add confirmed guarantee badge layout fixture");
  await("(()=>{const text=document.querySelector('.limited .career-pity-text'),range=document.createRange();range.selectNodeContents(text);const a=range.getBoundingClientRect(),b=text.getBoundingClientRect();return a.left>=b.left-1&&a.right<=b.right+1;})()");
  check("(()=>{document.getElementById('qa-pity-badge').remove();window.dispatchEvent(new Event('resize'));return true;})()","guarantee badge fits current width and text zoom "+zoom);
  capturePolish("polish-metrics-"+zoom+".png");
  check("(()=>{window.__qaPolishOutcomes.forEach(({node})=>node.textContent=node.classList.contains('up')?'123':'456');window.__qaPolishAverages.forEach(({node})=>node.textContent='999.9');window.dispatchEvent(new Event('resize'));return true;})()","inject long UP off and average values without changing records");
  await("(()=>{return [...document.querySelectorAll('.career-category.limited .career-metrics,.career-category.collection .career-metrics')].every(row=>[...row.children].every(cell=>[...cell.children].every(node=>{const range=document.createRange();range.selectNodeContents(node);const r=range.getBoundingClientRect(),box=cell.getBoundingClientRect();return r.left>=box.left-1&&r.right<=box.right+1;})));})()");
  check("window.__qaPolishOutcomes.every(({node})=>node.textContent===(node.classList.contains('up')?'123':'456'))&&window.__qaPolishAverages.every(({node})=>node.textContent==='999.9')","adaptive fitting retains exact numbers without ellipsis or a second metric row");
  check("(()=>{window.__qaPolishOutcomes.forEach(({node})=>node.textContent=node.classList.contains('up')?'11':'3');window.__qaPolishAverages.forEach(({node},i)=>node.textContent=i?'40.1':'48.1');window.dispatchEvent(new Event('resize'));return true;})()","restore the ordinary enlarged inline sample");

  check("(()=>{window.__qaPolishPool.events=window.__qaPolishPool.events.filter(event=>event.in_pool_pull<=41);window.__qaPolishFixture.replaceChildren(MobileRender.poolCard(window.__qaPolishPool,window.__qaPolishState));for(const image of window.__qaPolishFixture.querySelectorAll('img'))image.loading='eager';window.__qaPolishFixture.scrollIntoView({block:'start'});return [...window.__qaPolishFixture.querySelectorAll('.multi-pull-group')].every(group=>getComputedStyle(group,'::before').backgroundImage.includes('linear-gradient'));})()","position translucent green groups for screenshot");
  await("[...window.__qaPolishFixture.querySelectorAll('img')].every(image=>image.complete&&image.naturalWidth>0)");
  capturePolish("polish-groups-"+zoom+".png");
  check("(()=>{window.__qaPolishFixture.remove();for(const item of [...window.__qaPolishOutcomes,...window.__qaPolishAverages])item.node.textContent=item.text;window.scrollTo(0,window.__qaPolishScroll);for(const key of ['__qaPolishFixture','__qaPolishPool','__qaPolishState','__qaPolishOutcomes','__qaPolishAverages','__qaPolishScroll'])delete window[key];return true;})()","restore original report rows and metric counts after synthetic visual checks");
 }
 void navigationChrome()throws Exception{
  check("(()=>{window.__qaChromePadding=document.querySelector('.bottom-nav').style.paddingBottom;return true;})()","save navigation padding before inset simulation");
  for(int inset:new int[]{0,48,96}){
   check("(()=>{document.querySelector('.bottom-nav').style.paddingBottom='"+(inset+8)+"px';return true;})()","simulate bottom safe-area padding "+inset);
   await("(()=>{const nav=document.querySelector('.bottom-nav').getBoundingClientRect(),share=document.getElementById('overview-image').getBoundingClientRect();return share.bottom<=nav.top-8&&document.elementFromPoint(share.x+share.width/2,share.y+share.height/2)?.closest('button')?.id==='overview-image';})()");
   check("(()=>{const nav=document.querySelector('.bottom-nav').getBoundingClientRect();return Math.abs(parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--bottom-nav-height'))-nav.height)<=1;})()","floating share follows the full measured bottom navigation height "+inset);
  }
  check("(()=>{document.querySelector('.bottom-nav').style.paddingBottom=window.__qaChromePadding;delete window.__qaChromePadding;return true;})()","restore navigation padding");
  await("(()=>{const nav=document.querySelector('.bottom-nav').getBoundingClientRect(),share=document.getElementById('overview-image').getBoundingClientRect();return share.bottom<=nav.top-8;})()");
 }
 void capturePolish(String name)throws Exception{waitForIdleSync();SystemClock.sleep(250);android.graphics.Bitmap image=getUiAutomation().takeScreenshot();if(image!=null){try(java.io.FileOutputStream stream=new java.io.FileOutputStream(new java.io.File(getTargetContext().getExternalFilesDir(null),name))){image.compress(android.graphics.Bitmap.CompressFormat.PNG,100,stream);}finally{image.recycle();}}}
 int textZoom()throws Exception{AtomicInteger value=new AtomicInteger();runOnMainSync(()->value.set(web.getSettings().getTextZoom()));return value.get();}
 void tab(String name)throws Exception{
  check("(()=>{document.querySelector('.bottom-nav [data-tab=\""+name+"\"]').click();return true;})()","open "+name);
  await("!document.getElementById('view-"+name+"').hidden");
  check("document.documentElement.scrollWidth<=window.innerWidth+1",name+" has no horizontal overflow");
 }
 @Override public void onCreate(Bundle b){super.onCreate(b);start();}
 @Override public void onStart(){Bundle result=new Bundle();try{
  MainActivity.login=MainActivity.object("phase","idle");MainActivity.session="";
  activity=(MainActivity)startActivitySync(new Intent(getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));web=activity.web;
  await("window.mobileReady===true");
  check("(()=>{window.__qaReset='';(async()=>{const d=await new Promise((resolve,reject)=>{const q=indexedDB.open('starfall-local-archives',1);q.onsuccess=()=>resolve(q.result);q.onerror=reject;});await new Promise((resolve,reject)=>{const t=d.transaction(['accounts','settings'],'readwrite');t.objectStore('accounts').clear();t.objectStore('settings').clear();t.oncomplete=resolve;t.onerror=reject;});d.close();window.__qaReset=true;})().catch(e=>window.__qaReset='FAIL '+e);return true;})()","clear isolated archives");
  await("window.__qaReset===true");evaluate("window.mobileReady=false");runOnMainSync(()->web.reload());await("window.mobileReady===true");
  check("!document.getElementById('view-query').hidden","empty library opens query page");
  check("document.querySelectorAll('.bottom-nav [data-tab]').length===3","three mobile destinations");
  check("document.getElementById('login-dialog')===null","login is inline instead of a dialog");
  check("document.getElementById('remember-login')===null&&document.getElementById('demo-button')===null&&document.getElementById('compact-toggle')===null&&document.getElementById('tap-diagnostics')===null","removed controls are absent");
  check("document.querySelector('.query-heading p')===null","query page has no promotional copy");
  check("document.getElementById('manual-open')!==null&&document.getElementById('manual-dialog')===null&&document.getElementById('manual-panel')!==null","manual credential uses the login card back");
  check("!document.getElementById('query-flip').classList.contains('flipped')&&document.getElementById('manual-panel').getAttribute('aria-hidden')==='true'&&document.getElementById('manual-credential').disabled","manual face starts inactive");
  await("document.getElementById('gugu-idle').complete&&document.getElementById('gugu-idle').naturalWidth>0");
  check("document.querySelector('.query-secondary').nextElementSibling.id==='gugu-button'&&document.getElementById('gugu-button').dataset.state===(matchMedia('(prefers-reduced-motion: reduce)').matches?'poster':'idle')","gugu sits below query actions and respects motion preference");
  await("document.getElementById('gugu-idle').naturalWidth===864&&document.getElementById('gugu-idle').naturalHeight===864&&document.getElementById('gugu-poster').naturalWidth===864&&document.getElementById('gugu-poster').naturalHeight===864");
  check("(()=>{window.__qaReducedMotion=matchMedia('(prefers-reduced-motion: reduce)').matches;document.getElementById('gugu-button').click();return true;})()","click gugu for a random reaction or reduced-motion poster");
  await("window.__qaReducedMotion?document.getElementById('gugu-button').dataset.state==='poster':document.getElementById('gugu-button').dataset.state==='reacting'&&document.getElementById('gugu-reaction').naturalWidth===864&&document.getElementById('gugu-reaction').naturalHeight===864");
  await("window.__qaReducedMotion||document.getElementById('gugu-button').dataset.state==='idle'");
  check("(()=>{document.getElementById('manual-open').click();return true;})()","flip to manual credentials");
  await("document.getElementById('query-flip').classList.contains('flipped')&&document.getElementById('manual-panel').getAttribute('aria-hidden')==='false'&&!document.getElementById('manual-credential').disabled");
  check("document.getElementById('manual-open-label').textContent==='返回登录方式'&&document.getElementById('query-front').getAttribute('aria-hidden')==='true'&&document.getElementById('official-login').disabled","manual flip exposes only the active face");
  check("(()=>{document.getElementById('manual-help').click();return true;})()","open manual credential guide");
  await("document.getElementById('manual-help-dialog').open");
  check("document.getElementById('manual-help-dialog').textContent.includes('Local Storage')&&document.getElementById('manual-help-dialog').textContent.includes('HP_2070023')&&!document.getElementById('manual-help-dialog').contains(document.getElementById('manual-form'))","guide is separate from the credential form");
  check("window.mobileBack()===true&&!document.getElementById('manual-help-dialog').open&&document.getElementById('query-flip').classList.contains('flipped')","back closes guide before flipping the card");
  check("(()=>{document.getElementById('manual-credential').value='QA_INVALID';document.getElementById('manual-consent').checked=false;document.getElementById('manual-submit').click();return true;})()","reject unconfirmed manual credential");
  await("!document.getElementById('manual-error').hidden");
  check("document.getElementById('manual-credential').value==='QA_INVALID'","manual credential is retained after validation failure");
  check("(()=>{document.getElementById('manual-open').click();return true;})()","same button returns to login methods");
  await("!document.getElementById('query-flip').classList.contains('flipped')&&document.getElementById('manual-open-label').textContent==='手动凭据'");
  check("document.getElementById('manual-credential').value==='QA_INVALID'","flip preserves unsubmitted input");
  check("(()=>{document.getElementById('manual-open').click();return true;})()","reopen manual credentials");
  await("document.getElementById('query-flip').classList.contains('flipped')&&!document.getElementById('manual-credential').disabled");
  check("(()=>{window.__qaApi=MobileServices.api;MobileServices.api=async path=>{if(path==='/query')throw new Error('QA_QUERY_FAILED');return window.__qaApi(path);};document.getElementById('manual-consent').checked=true;document.getElementById('manual-submit').click();return true;})()","submit synthetic failing credential");
  await("document.getElementById('manual-error').textContent.includes('QA_QUERY_FAILED')&&!document.getElementById('manual-submit').disabled");
  check("document.getElementById('query-flip').classList.contains('flipped')&&document.getElementById('manual-credential').value==='QA_INVALID'&&document.getElementById('manual-consent').checked","query failure retains input and stays on the manual face");
  check("(()=>{MobileServices.api=window.__qaApi;delete window.__qaApi;return true;})()","restore native API bridge");
  check("window.mobileBack()===true&&!document.getElementById('query-flip').classList.contains('flipped')","back returns from manual face to login methods");
  check("(()=>{window.__qaBiliEnabled=undefined;fetch('./build-info.json').then(r=>r.json()).then(info=>window.__qaBiliEnabled=info.bilibili_enabled).catch(()=>window.__qaBiliEnabled=false);return true;})()","load Bili build config");
  await("window.__qaBiliEnabled!==undefined");
  check("window.__qaBiliEnabled?document.getElementById('bili-login').disabled===false&&document.getElementById('bili-login').getAttribute('aria-disabled')==='false':document.getElementById('bili-login').disabled&&document.getElementById('bili-login').getAttribute('aria-disabled')==='true'","bilibili UI matches build config");
  check("document.getElementById('bili-login').textContent.includes(window.__qaBiliEnabled?'在官方哔哩哔哩 App 中确认':'待接入')","bilibili note matches build config");
  check("(()=>{window.__qaApi=MobileServices.api;window.__qaManualReport=fetch('./demo.json').then(r=>r.json()).then(source=>{source.account={uid:'90010001',channel:'qa',label:'手动查询合成账号'};return source;});MobileServices.api=async(path,body)=>{if(path==='/query')return window.__qaManualReport;return window.__qaApi(path,body);};document.getElementById('manual-open').click();document.getElementById('manual-credential').value='QA_SUCCESS';document.getElementById('manual-consent').checked=true;document.getElementById('manual-submit').click();return true;})()","submit synthetic successful credential");
  await("!document.getElementById('view-records').hidden&&!document.getElementById('query-flip').classList.contains('flipped')&&document.getElementById('manual-credential').value===''&&!document.getElementById('manual-consent').checked");
  check("document.getElementById('summary-pulls').textContent.trim()==='464'&&document.getElementById('account-button').textContent.includes('手动查询合成账号')","manual success renders the returned report");
  check("(()=>{MobileServices.api=window.__qaApi;delete window.__qaApi;delete window.__qaManualReport;return true;})()","restore native API after manual success");
  check("(()=>{window.__qaDemo='';(async()=>{const source=await fetch('./demo.json').then(r=>r.json());source.account={uid:'90010001',channel:'qa',label:'合成演示账号',nickname:'合成玩家',game_uid:'90010001',level:80,face_id:12001701,fashion_id:12001701,head_id:0,head_rect_id:21180003,head_rect_stamp:1893456000,head_rect_extra:7,head_rect_item_uid:'0',server_time:1793456000};const response=await fetch('./api/library/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:JSON.stringify(source),format:'json'})});if(!response.ok)throw new Error(await response.text());window.__qaDemo=true;})().catch(e=>window.__qaDemo='FAIL '+e);return true;})()","import synthetic report");
  await("window.__qaDemo===true");evaluate("window.mobileReady=false");runOnMainSync(()->web.reload());await("window.mobileReady===true");
  await("document.body.dataset.demo==='false'&&!document.getElementById('view-records').hidden");
  check("document.getElementById('summary-pulls').textContent.trim()==='464'","demo summary total");
  check("document.getElementById('summary-fives').textContent.trim()==='9'","demo five-star total");
  check("document.getElementById('summary-pools').textContent.trim()==='7'","demo pool total");
  credentialUi();sheets();
  check("document.getElementById('account-button-label').textContent==='合成玩家'&&document.getElementById('profile-account-name').textContent==='合成玩家'&&document.querySelector('.account-option.active strong').textContent==='合成玩家'&&document.getElementById('record-account-name').textContent.trim()==='合成玩家'&&document.getElementById('record-account-uid').textContent==='UID 90010001'&&!document.getElementById('record-account-meta').textContent.includes('UID')&&document.querySelector('.player-level').textContent==='Lv.80'&&document.getElementById('account-quick-stats')===null","real player profile uses separate nickname and UID text");
  check("document.getElementById('record-account-uid').parentElement===document.querySelector('.account-identity-line')&&document.querySelector('.player-level').parentElement===document.querySelector('.account-identity-line')&&getComputedStyle(document.getElementById('record-account-uid')).fontSize===getComputedStyle(document.querySelector('.player-level')).fontSize","UID and level share a dedicated second line");
  check("(()=>{const name=document.getElementById('record-account-name').getBoundingClientRect(),uid=document.getElementById('record-account-uid').getBoundingClientRect(),meta=document.getElementById('record-account-meta').getBoundingClientRect(),avatar=document.getElementById('record-account-avatar').getBoundingClientRect();return uid.top>=name.bottom&&meta.top>=avatar.bottom&&meta.left<=avatar.left+1;})()","nickname, identity and full-width summary have distinct rows");
  check("[...document.querySelectorAll('.pool-card:has(.pool-up-avatars)')].every(card=>card.querySelector('.pool-title-line').contains(card.querySelector('.pool-up-avatars')))","featured UP portraits sit beside pool titles");
  await("document.querySelector('#record-account-avatar .player-head')?.naturalWidth===320&&document.querySelector('#record-account-avatar .player-frame')?.naturalWidth===120");
  check("document.querySelector('#record-account-avatar .player-avatar').dataset.frameId==='21180003'&&document.querySelector('#record-account-avatar .player-rank').textContent==='7'","player head frame and rank render independently");
  check("(()=>{const clip=document.querySelector('#record-account-avatar .player-head-clip'),frame=document.querySelector('#record-account-avatar .player-frame'),head=document.querySelector('#record-account-avatar .player-head'),a=clip.getBoundingClientRect(),b=frame.getBoundingClientRect();return head.dataset.cropRect==='91,23,88,88'&&Math.abs((a.left+a.width/2)-(b.left+b.width/2))<1&&Math.abs((a.top+a.height/2)-(b.top+b.height/2))<1&&Math.abs(b.width/a.width-120/88)<0.02;})()","player crop and frame share the original FGUI center and scale");
  check("(()=>{const a=MobileRender.playerAvatar({fashion_id:12001701});a.querySelector('.player-head').dispatchEvent(new Event('error'));return !a.querySelector('.player-head')&&a.querySelector('.player-fallback')!==null;})()","player head failure restores the neutral avatar");
  await("document.querySelector('#pool-list .five-event .avatar-image')?.naturalWidth===256");
  check("document.querySelector('#pool-list .five-event .item-avatar').dataset.itemId==='10000170'&&document.querySelector('#pool-list .five-event .avatar-image').dataset.cropRect==='38,0,180,180'&&parseFloat(document.querySelector('#pool-list .five-event .avatar-image').style.width)>140","event avatar fills the square with its calibrated head crop");
  check("(()=>{const a=MobileRender.avatar(999999,'未知',5);return !a.querySelector('img')&&a.textContent==='未';})()","unknown character keeps the initial fallback");
  check("(()=>{window.__qaMerge='';(async()=>{const source=await fetch('./demo.json').then(r=>r.json());source.account={uid:'90010001',channel:'qa',label:'不应覆盖旧别名',nickname:'更新玩家',game_uid:'90010002',level:81,face_id:12001701,fashion_id:12001701,head_id:0,head_rect_id:0,head_rect_stamp:0,head_rect_extra:0,head_rect_item_uid:'0',server_time:1793456001};const response=await fetch('./api/library/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:JSON.stringify(source),format:'json'})});if(!response.ok)throw new Error(await response.text());window.__qaMerge=true;})().catch(e=>window.__qaMerge='FAIL '+e);return true;})()","merge updated player profile into the existing archive");
  await("window.__qaMerge===true");evaluate("window.mobileReady=false");runOnMainSync(()->web.reload());await("window.mobileReady===true");
  check("document.getElementById('account-button-label').textContent==='更新玩家'&&document.querySelector('.account-option.active small').textContent.includes('存档 合成演示账号')&&document.getElementById('record-account-name').textContent==='更新玩家'&&document.getElementById('record-account-uid').textContent==='UID 90010002'&&!document.getElementById('record-account-meta').textContent.includes('UID')&&document.querySelector('.player-level').textContent==='Lv.81'&&document.querySelector('#record-account-avatar .player-frame')===null","merge preserves custom alias and updates real profile fields");
  for(final String id : new String[]{"account-summary","career-summary","pool-list","record-floating-actions","career-evaluation","recent-evaluation","career-tags"})check("!document.getElementById('"+id+"').hidden",id+" is visible");
  check("document.querySelectorAll('.career-pity').length===3&&document.querySelectorAll('.career-pity-fill').length===3","career cards include overlay pity bars");
  check("document.querySelector('.career-category.limited .career-metrics').textContent.includes('UP/歪')&&document.querySelector('.career-category.limited .career-metrics').textContent.includes('平均UP')","limited labels use UP terminology");
  check("document.querySelector('.career-category.limited .career-metrics>div:first-child strong').textContent==='3/2'","limited UP/off values use actual counts");
  check("getComputedStyle(document.querySelector('.career-outcomes .up')).color==='rgb(84, 224, 185)'&&getComputedStyle(document.querySelector('.career-outcomes .off')).color==='rgb(255, 156, 145)'","UP and off counts use green and red");
  check("[...document.querySelectorAll('.career-category')].every(e=>getComputedStyle(e).backgroundColor!==getComputedStyle(document.getElementById('career-summary')).backgroundColor)","career pools retain distinct background cards");
  check("document.querySelector('.career-category.normal .career-metrics').textContent.includes('五星平均')","normal keeps five-star average");
  check("document.getElementById('career-extra').hidden","beginner banner is excluded from career extras");
  check("document.getElementById('career-evaluation').textContent.length>0&&document.getElementById('recent-evaluation').textContent.length>0&&document.getElementById('career-tags').children.length>0","career and recent evaluations and tags render");
  check("document.querySelectorAll('#pool-list .pool-card').length>0","pool cards render in records");
  navigationChrome();
  polishFixture("100");
  android.graphics.Bitmap screenshot=getUiAutomation().takeScreenshot();
  if(screenshot!=null){try(java.io.FileOutputStream stream=new java.io.FileOutputStream(new java.io.File(getTargetContext().getExternalFilesDir(null),"records-qa.png"))){screenshot.compress(android.graphics.Bitmap.CompressFormat.PNG,100,stream);}finally{screenshot.recycle();}}
  check("document.querySelector('#pool-list .five-event')!==null","five-star events are expanded");
  check("(()=>{const catalog={version:'qa',cards:{1:['低星',3],2:['合成UP',5],3:['合成歪',5]},pools:{10:'合成限定A',11:'合成限定B',20:'合成普通'},pool_meta:{10:{category:'limited',pity_group:'limited',featured_ids:[2],featured_names:['合成UP'],hard_pity:70},11:{category:'limited',pity_group:'limited',featured_ids:[2],featured_names:['合成UP'],hard_pity:70},20:{category:'normal',pity_group:'normal',featured_ids:[],hard_pity:70}}};const row=(pool_id,index,timestamp,drops)=>({pool_id,index,timestamp,record_id:index,drops});const report=MobileEngine.buildReport([row(10,1,1,[{item_id:2,count:1}]),row(10,2,2,[{item_id:1,count:5},{item_id:2,count:1}]),row(10,3,3,[{item_id:1,count:60},{item_id:3,count:1}]),row(20,1,4,[{item_id:2,count:1}]),row(11,4,5,[{item_id:1,count:60},{item_id:2,count:1}])],catalog,{});const state={eventLimits:{},characterLimits:{},showCharacters:false,insights:MobileInsights.analyze(report)};const cards=MobileView.pools(report,'all',catalog).map(p=>MobileRender.poolCard(p,state));window.__qaGradeText=cards.map(c=>c.textContent).join('|');return cards.filter(c=>c.textContent.includes('超欧')).length===1&&cards.filter(c=>c.textContent.includes('超非')).length===1;})()","real engine history renders one lucky UP and one complete-cycle unlucky UP badge");
  check("document.querySelector('#pool-list .five-event-fill').getBoundingClientRect().height>=20","pull interval bars are prominent");
  check("document.querySelector('.five-event-track').getBoundingClientRect().right<=document.getElementById('overview-image').getBoundingClientRect().left","interval bars leave room for floating share actions");
  check("document.body.classList.contains('compact-ui')","unified compact mode");
  check("getComputedStyle(document.querySelector('.five-event-meta')).display==='none'","event metadata stays hidden");
  check("getComputedStyle(document.querySelector('.character-roster')).display==='none'","character rosters start collapsed");
  check("(()=>{document.getElementById('characters-toggle').click();return true;})()","show all character rosters");
  await("getComputedStyle(document.querySelector('.character-roster')).display!=='none'");
  check("(()=>{document.getElementById('characters-toggle').click();return true;})()","hide all character rosters");
  await("getComputedStyle(document.querySelector('.character-roster')).display==='none'");
  check("(()=>{document.querySelector('#pool-category-tabs [data-category=\"limited\"]').click();return true;})()","filter limited pools");
  await("document.querySelectorAll('#pool-list .pool-card').length>0&&[...document.querySelectorAll('#pool-list .pool-card')].every(e=>e.dataset.category==='limited')");
  check("(()=>{document.querySelector('#pool-category-tabs [data-category=\"all\"]').click();return true;})()","restore all pools");
  check("(()=>{const d=document.getElementById('raw-records-details');d.open=true;d.dispatchEvent(new Event('toggle'));return true;})()","open raw records");
  check("document.querySelectorAll('#record-list .record-item').length>0","mobile raw record cards");
  check("(()=>{const e=document.getElementById('record-pool');e.value=e.options[1].value;e.dispatchEvent(new Event('change',{bubbles:true}));return e.value!=='';})()","filter records by pool");
  await("document.querySelectorAll('#record-list .record-item').length>0&&[...document.querySelectorAll('#record-list .record-item')].every(e=>e.dataset.poolId===document.getElementById('record-pool').value)");
  check("(()=>{const e=document.getElementById('record-pool');e.value='';e.dispatchEvent(new Event('change',{bubbles:true}));const r=document.getElementById('record-rarity');r.value='4';r.dispatchEvent(new Event('change',{bubbles:true}));return true;})()","filter four-star records");
  await("document.querySelectorAll('#record-list .record-item').length>0&&[...document.querySelectorAll('#record-list .record-item')].every(e=>e.dataset.rarity==='4')");
  check("(()=>{const e=document.getElementById('record-rarity');e.value='';e.dispatchEvent(new Event('change',{bubbles:true}));const f=document.getElementById('record-five-only');f.checked=true;f.dispatchEvent(new Event('change',{bubbles:true}));return true;})()","enable five-star filter");
  await("[...document.querySelectorAll('#record-list .record-item')].every(e=>e.dataset.rarity==='5')");
  check("(()=>{const e=document.getElementById('record-search');e.value='__没有这个角色__';e.dispatchEvent(new Event('input',{bubbles:true}));return true;})()","search without results");
  await("!document.getElementById('record-empty').hidden");
  check("(()=>{const e=document.getElementById('record-search');e.value='';e.dispatchEvent(new Event('input',{bubbles:true}));const f=document.getElementById('record-five-only');f.checked=false;f.dispatchEvent(new Event('change',{bubbles:true}));return true;})()","reset record filters");
  check("(()=>{window.__qaDrawCalls=0;window.__qaOriginalDrawImage=CanvasRenderingContext2D.prototype.drawImage;CanvasRenderingContext2D.prototype.drawImage=function(...args){window.__qaDrawCalls++;return window.__qaOriginalDrawImage.apply(this,args);};document.getElementById('overview-image').click();return true;})()","open floating image export with draw instrumentation");
  await("document.querySelector('#image-previews .image-preview img')?.complete&&document.querySelector('#image-previews .image-preview img').naturalWidth===1080");
  check("window.__qaDrawCalls>=2","long image draws the logo and character portraits");
  check("(()=>{window.__qaPng=null;fetch(document.querySelector('#image-previews img').src).then(r=>r.blob()).then(blob=>{const reader=new FileReader();reader.onload=()=>window.__qaPng=reader.result.split(',')[1];reader.readAsDataURL(blob);});return true;})()","capture the actual exported PNG");
  await("typeof window.__qaPng==='string'");
  String png=(String)new org.json.JSONTokener(evaluate("window.__qaPng")).nextValue();
  try(java.io.FileOutputStream stream=new java.io.FileOutputStream(new java.io.File(getTargetContext().getExternalFilesDir(null),"long-image-qa.png"))){stream.write(android.util.Base64.decode(png,android.util.Base64.DEFAULT));}
  check("getComputedStyle(document.getElementById('image-pool-list')).display==='grid'&&getComputedStyle(document.querySelector('#image-pool-list label')).display==='flex'&&parseFloat(getComputedStyle(document.querySelector('#image-pool-list label')).paddingLeft)>0","pool choices use a spaced selectable list");
  check("(()=>{CanvasRenderingContext2D.prototype.drawImage=window.__qaOriginalDrawImage;delete window.__qaOriginalDrawImage;delete window.__qaDrawCalls;return true;})()","restore canvas draw instrumentation");
  check("(()=>{const choices=[...document.querySelectorAll('#image-pool-list input[type=checkbox]')];choices.forEach((choice,index)=>{choice.checked=index===0;choice.dispatchEvent(new Event('change',{bubbles:true}));});return choices.length>1&&choices.filter(choice=>choice.checked).length===1;})()","select one specific pool for long image");
  await("document.getElementById('image-pool-count').textContent==='已选 1/7 个卡池'&&document.getElementById('image-status').textContent.includes('已生成 1 张图片')");
  check("document.querySelector('#image-previews .image-preview img')?.naturalWidth===1080","single pool export generates a preview");
  check("(()=>{document.getElementById('image-pool-none').click();return true;})()","clear all pool selections");
  await("document.getElementById('image-status').textContent.includes('请先在下方勾选一个具体卡池')");
  check("document.querySelector('#image-previews .image-preview')===null","empty pool selection does not generate an image");
  check("window.mobileBack()===true&&!document.getElementById('mobile-image-dialog').open","back closes image export");
  MainActivity.phase("done","查询完成");
  check("(()=>{document.getElementById('update-record-button').click();return true;})()","open query page for update");
  await("!document.getElementById('view-query').hidden&&!document.getElementById('login-options').hidden");
  tab("profile");
  check("window.mobileBack()===true&&!document.getElementById('view-query').hidden","back restores previous page");
  check("(()=>{window.__qaImport='';(async()=>{const catalog=await fetch('./catalog.json').then(r=>r.json());for(const [uid,label,stamp] of [['QA-1','QA一号',1700000000],['QA-2','QA二号',1700000010]]){const report=MobileEngine.buildReport([{pool_id:999901,pool_name:'合成卡池',timestamp:stamp,index:1,record_id:stamp,drops:[{item_id:999001,name:'合成五星',rarity:5,count:1}]}],catalog,{});report.account={uid,channel:'qa',label};const response=await fetch('./api/library/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:JSON.stringify(report),format:'json'})});if(!response.ok)throw new Error(await response.text());}window.__qaImport='ok';})().catch(e=>window.__qaImport='FAIL '+e);return true;})()","import synthetic accounts");
  await("window.__qaImport==='ok'");evaluate("window.mobileReady=false");runOnMainSync(()->web.reload());await("window.mobileReady===true");
  check("document.getElementById('account-button').textContent.includes('QA二号')","restore last synthetic account");
  check("(()=>{document.getElementById('account-button').click();return true;})()","open account switcher");
  await("document.getElementById('account-dialog').open");
  check("document.querySelectorAll('.account-option').length===3","three archived accounts listed");
  check("(()=>{document.querySelector('.account-option[data-uid=\"QA-1\"]').click();return true;})()","switch synthetic account");
  await("document.getElementById('account-button').textContent.includes('QA一号')&&!document.getElementById('account-dialog').open");
  tab("profile");
  check("(()=>{document.querySelector('#text-zoom-options [data-zoom=\"125\"]').click();return true;})()","select 125 percent text zoom");
  await("document.body.dataset.textZoom==='125'&&document.querySelector('#text-zoom-options [data-zoom=\"125\"]').getAttribute('aria-pressed')==='true'");
  ok(textZoom()==125,"native text zoom is applied");
  evaluate("window.mobileReady=false");runOnMainSync(()->web.reload());await("window.mobileReady===true");
  tab("profile");
  check("document.body.dataset.textZoom==='125'&&document.querySelector('#text-zoom-options [data-zoom=\"125\"]').getAttribute('aria-pressed')==='true'","text zoom persists across reload");
  ok(textZoom()==125,"native text zoom also persists across reload");
  check("(()=>{const b=document.querySelector('#text-zoom-options [data-zoom=\"85\"]');if(b)b.click();return b!==null;})()","select smaller 85 percent text zoom");
  await("document.body.dataset.textZoom==='85'");
  ok(textZoom()==85,"smaller native text zoom is applied");
  check("(()=>{document.querySelector('#text-zoom-options [data-zoom=\"100\"]').click();return true;})()","restore standard text zoom");
  await("document.body.dataset.textZoom==='100'");
  tab("records");
  runOnMainSync(()->web.getSettings().setTextZoom(140));SystemClock.sleep(250);
  navigationChrome();
  polishFixture("140");
  check("(()=>{for(const e of document.querySelectorAll('.career-pulls strong'))e.textContent='123,456';return true;})()","inject large metrics");
  check("document.documentElement.scrollWidth<=window.innerWidth+1","large text has no horizontal overflow");
  check("[...document.querySelectorAll('.career-category')].every(e=>e.scrollWidth<=e.clientWidth+1)","summary metrics fit large text");
  tab("query");
  check("(()=>{document.getElementById('manual-open').click();return true;})()","open manual face at large text");
  await("document.getElementById('query-flip').classList.contains('flipped')");SystemClock.sleep(400);
  check("document.documentElement.scrollWidth<=window.innerWidth+1","manual face fits large text");
  check("(()=>{document.getElementById('manual-help').click();return true;})()","open guide at large text");
  await("document.getElementById('manual-help-dialog').open");
  check("document.documentElement.scrollWidth<=window.innerWidth+1","manual guide fits large text");
  check("window.mobileBack()===true&&!document.getElementById('manual-help-dialog').open","close large text guide");
  tab("records");
  VisualAssets originalVisuals=MainActivity.visuals;
  try {
   VisualAssetsTest.Fake fake=new VisualAssetsTest.Fake();fake.publish("2026110201");
   org.json.JSONObject mapping=new org.json.JSONObject(new String(fake.mapping,java.nio.charset.StandardCharsets.UTF_8));
   mapping.getJSONObject("characters").put("999001",new org.json.JSONObject().put("path","assets/portraits/12.png").put("name","合成更新头像").put("rarity",5).put("source_size",new org.json.JSONArray("[1,1]")).put("crop_rect",new org.json.JSONArray("[0,0,1,1]")));
   fake.publish(mapping);
   MainActivity.visuals=new VisualAssets(new java.io.File(getTargetContext().getCacheDir(),"visual-ui-"+System.nanoTime()),originalVisuals.manifest(),fake);
   ok(MainActivity.visuals.check(fake.catalog(),VisualAssetsTest.SOURCE,true).getBoolean("updated"),"activate a synthetic visual update without changing the APK");
   check("(()=>{window.scrollTo(0,120);document.dispatchEvent(new Event('visibilitychange'));return true;})()","poll update status after resuming");
   await("MobileAssets.character(999001)?.src.includes('assets/remote/')&&document.querySelector('#pool-list .avatar-image')?.src.includes('assets/remote/')");
   await("document.querySelector('#pool-list .avatar-image')?.naturalWidth===1");
   check("MobileAssets.character(999001).cropRect.join(',')==='0,0,1,1'","live update refreshes mapping, crop metadata and decoded images");
   check("document.getElementById('account-button').textContent.includes('QA一号')&&!document.getElementById('view-records').hidden","live visual update preserves selected account and tab");
  } finally { MainActivity.visuals=originalVisuals; }
  averageUpUi();nicknameCompatibility();
  result.putString("result","PASS "+checks+" mobile UI checks");finish(Activity.RESULT_OK,result);
 }catch(Throwable e){result.putString("result","FAIL "+e);finish(Activity.RESULT_CANCELED,result);}finally{if(activity!=null)runOnMainSync(()->activity.finish());}}
}
