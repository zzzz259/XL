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
  activity=(MainActivity)startActivitySync(new Intent(getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));web=activity.web;
  await("window.mobileReady===true");
  check("document.querySelectorAll('.bottom-nav [data-tab]').length===4","four mobile destinations");
  check("!!document.getElementById('demo-button')","empty archive offers demo");
  check("(()=>{document.getElementById('demo-button').click();return true;})()","start synthetic demo");
  await("document.body.dataset.demo==='true'");
  check("document.getElementById('summary-pulls').textContent.trim()==='464'","demo overview total");
  check("document.getElementById('summary-fives').textContent.trim()==='9'","demo five-star total");
  check("document.getElementById('recent-five-list').children.length>0","recent five-star cards");
  tab("pools");
  check("document.getElementById('pool-list').children.length>0","pool cards rendered");
  check("(()=>{document.querySelector('#pool-category-tabs [data-category=\"limited\"]').click();return true;})()","filter limited pools");
  await("document.querySelectorAll('#pool-list .pool-details').length>0&&[...document.querySelectorAll('#pool-list .pool-details')].every(e=>e.dataset.category==='limited')");
  check("(()=>{document.querySelector('#pool-category-tabs [data-category=\"all\"]').click();return true;})()","restore all pools");
  check("(()=>{for(const d of document.querySelectorAll('.pool-details')){d.open=true;d.dispatchEvent(new Event('toggle'));if(d.querySelector('.five-event'))return true;}return false;})()","expand a pool with five-star events");
  tab("records");
  check("document.querySelectorAll('#record-list .record-item').length>0","mobile record cards");
  check("(()=>{const e=document.getElementById('record-pool');e.value=e.options[1].value;e.dispatchEvent(new Event('change',{bubbles:true}));return e.value!=='';})()","filter records by pool");
  await("document.querySelectorAll('#record-list .record-item').length>0&&[...document.querySelectorAll('#record-list .record-item')].every(e=>e.dataset.poolId===document.getElementById('record-pool').value)");
  check("(()=>{const e=document.getElementById('record-pool');e.value='';e.dispatchEvent(new Event('change',{bubbles:true}));const r=document.getElementById('record-rarity');r.value='4';r.dispatchEvent(new Event('change',{bubbles:true}));return true;})()","filter four-star records");
  await("document.querySelectorAll('#record-list .record-item').length>0&&[...document.querySelectorAll('#record-list .record-item')].every(e=>e.dataset.rarity==='4')");
  check("(()=>{const e=document.getElementById('record-rarity');e.value='';e.dispatchEvent(new Event('change',{bubbles:true}));const f=document.getElementById('record-five-only');f.checked=true;f.dispatchEvent(new Event('change',{bubbles:true}));return true;})()","enable five-star filter");
  await("[...document.querySelectorAll('#record-list .record-item')].every(e=>e.dataset.rarity==='5')");
  check("(()=>{const e=document.getElementById('record-search');e.value='__没有这个角色__';e.dispatchEvent(new Event('input',{bubbles:true}));return true;})()","search without results");
  await("!document.getElementById('record-empty').hidden");
  check("(()=>{const e=document.getElementById('record-search');e.value='';e.dispatchEvent(new Event('input',{bubbles:true}));const f=document.getElementById('record-five-only');f.checked=false;f.dispatchEvent(new Event('change',{bubbles:true}));return true;})()","reset record filters");
  tab("overview");
  check("(()=>{document.getElementById('overview-image').click();return true;})()","open mobile image export");
  await("document.querySelector('#image-previews .image-preview img')?.complete&&document.querySelector('#image-previews .image-preview img').naturalWidth===1080");
  check("window.mobileBack()===true&&!document.getElementById('mobile-image-dialog').open","back closes image export");
  check("(()=>{document.getElementById('update-record-button').click();return true;})()","open login sheet");
  await("document.getElementById('login-dialog').open");
  check("window.mobileBack()===true&&!document.getElementById('login-dialog').open","back closes dialog");
  tab("profile");
  check("window.mobileBack()===true&&!document.getElementById('view-overview').hidden","back returns overview");
  check("(()=>{window.__qaImport='';(async()=>{const catalog=await fetch('./catalog.json').then(r=>r.json());for(const [uid,label,stamp] of [['QA-1','QA一号',1700000000],['QA-2','QA二号',1700000010]]){const report=MobileEngine.buildReport([{pool_id:999901,pool_name:'合成卡池',timestamp:stamp,index:1,record_id:stamp,drops:[{item_id:999001,name:'合成五星',rarity:5,count:1}]}],catalog,{});report.account={uid,channel:'qa',label};const response=await fetch('./api/library/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:JSON.stringify(report),format:'json'})});if(!response.ok)throw new Error(await response.text());}window.__qaImport='ok';})().catch(e=>window.__qaImport='FAIL '+e);return true;})()","import synthetic accounts");
  await("window.__qaImport==='ok'");
  evaluate("window.mobileReady=false");runOnMainSync(()->web.reload());await("window.mobileReady===true");
  check("document.getElementById('account-button').textContent.includes('QA二号')","restore last synthetic account");
  check("(()=>{document.getElementById('account-button').click();return true;})()","open account switcher");
  await("document.getElementById('account-dialog').open");
  check("document.querySelectorAll('.account-option').length===2","two archived accounts listed");
  check("(()=>{document.querySelector('.account-option[data-uid=\"QA-1\"]').click();return true;})()","switch synthetic account");
  await("document.getElementById('account-button').textContent.includes('QA一号')&&!document.getElementById('account-dialog').open");
  check("document.documentElement.scrollWidth<=window.innerWidth+1","saved account has no horizontal overflow");
  result.putString("result","PASS "+checks+" mobile UI checks");finish(Activity.RESULT_OK,result);
 }catch(Throwable e){result.putString("result","FAIL "+e);finish(Activity.RESULT_CANCELED,result);}finally{if(activity!=null)runOnMainSync(()->activity.finish());}}
}
