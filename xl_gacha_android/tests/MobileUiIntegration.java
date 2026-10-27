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
  check("(()=>{window.__qaDemo='';(async()=>{const source=await fetch('./demo.json').then(r=>r.json());source.account={uid:'90010001',channel:'qa',label:'合成演示账号'};const response=await fetch('./api/library/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:JSON.stringify(source),format:'json'})});if(!response.ok)throw new Error(await response.text());window.__qaDemo=true;})().catch(e=>window.__qaDemo='FAIL '+e);return true;})()","import synthetic report");
  await("window.__qaDemo===true");evaluate("window.mobileReady=false");runOnMainSync(()->web.reload());await("window.mobileReady===true");
  await("document.body.dataset.demo==='false'&&!document.getElementById('view-records').hidden");
  check("document.getElementById('summary-pulls').textContent.trim()==='464'","demo summary total");
  check("document.getElementById('summary-fives').textContent.trim()==='9'","demo five-star total");
  check("document.getElementById('summary-pools').textContent.trim()==='7'","demo pool total");
  check("document.getElementById('record-account-name').textContent.trim()==='招募档案'&&document.getElementById('account-quick-stats')===null","account name is not duplicated");
  for(final String id : new String[]{"account-summary","career-summary","pool-list","record-floating-actions","career-evaluation","recent-evaluation","career-tags"})check("!document.getElementById('"+id+"').hidden",id+" is visible");
  check("document.querySelectorAll('.career-pity').length===3&&document.querySelectorAll('.career-pity-fill').length===3","career cards include overlay pity bars");
  check("document.querySelector('.career-category.limited .career-metrics').textContent.includes('UP/歪')&&document.querySelector('.career-category.limited .career-metrics').textContent.includes('平均UP')","limited labels use UP terminology");
  check("document.querySelector('.career-category.limited .career-metrics>div:first-child strong').textContent==='3 / 2'","limited UP/off values use actual counts");
  check("document.querySelector('.career-category.normal .career-metrics').textContent.includes('五星平均')","normal keeps five-star average");
  check("document.getElementById('career-extra').hidden","beginner banner is excluded from career extras");
  check("document.getElementById('career-evaluation').textContent.length>0&&document.getElementById('recent-evaluation').textContent.length>0&&document.getElementById('career-tags').children.length>0","career and recent evaluations and tags render");
  check("document.querySelectorAll('#pool-list .pool-card').length>0","pool cards render in records");
  check("document.querySelector('#pool-list .five-event')!==null","five-star events are expanded");
  check("document.querySelector('#pool-list .five-event-fill').getBoundingClientRect().height>=20","pull interval bars are prominent");
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
  check("(()=>{document.getElementById('overview-image').click();return true;})()","open floating image export");
  await("document.querySelector('#image-previews .image-preview img')?.complete&&document.querySelector('#image-previews .image-preview img').naturalWidth===1080");
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
  runOnMainSync(()->web.getSettings().setTextZoom(140));SystemClock.sleep(250);
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
  result.putString("result","PASS "+checks+" mobile UI checks");finish(Activity.RESULT_OK,result);
 }catch(Throwable e){result.putString("result","FAIL "+e);finish(Activity.RESULT_CANCELED,result);}finally{if(activity!=null)runOnMainSync(()->activity.finish());}}
}
