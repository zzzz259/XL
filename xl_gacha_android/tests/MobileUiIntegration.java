package cn.starfall.gacha.local;

import android.app.*;
import android.content.*;
import android.content.pm.ActivityInfo;
import android.graphics.Bitmap;
import android.os.*;
import android.webkit.*;
import android.widget.FrameLayout;
import java.io.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** Real WebView/UI navigation using only synthetic local archives in a QA package. */
public final class MobileUiIntegration extends Instrumentation {
 int checks;boolean phone;MainActivity activity;WebView web;
 void ok(boolean condition,String name){if(!condition)throw new AssertionError(name);checks++;}
 String eval(String script)throws Exception{
  CountDownLatch done=new CountDownLatch(1);AtomicReference<String> result=new AtomicReference<>();
  runOnMainSync(()->web.evaluateJavascript(script,v->{result.set(v);done.countDown();}));
  if(!done.await(10,TimeUnit.SECONDS))throw new AssertionError("JavaScript timed out");return result.get();
 }
 void waitFor(String script,String name)throws Exception{
  for(int i=0;i<150;i++){if("true".equals(eval(script))){ok(true,name);return;}SystemClock.sleep(100);}
  throw new AssertionError(name+": "+eval("JSON.stringify({page:document.body.dataset.page,status:document.getElementById('global-status')?.textContent})"));
 }
 void screenshot(String name)throws Exception{
  SystemClock.sleep(250);Bitmap image=getUiAutomation().takeScreenshot();
  if(image==null)throw new AssertionError("Screenshot unavailable");
  if(phone){int[] location=new int[2];runOnMainSync(()->web.getLocationOnScreen(location));Bitmap cropped=Bitmap.createBitmap(image,location[0],location[1],web.getWidth(),web.getHeight());image.recycle();image=cropped;}
  File dir=new File(getTargetContext().getExternalFilesDir(null),phone?"mobile-ui-phone":"mobile-ui-qa");dir.mkdirs();
  try(OutputStream stream=new FileOutputStream(new File(dir,name+".png"))){image.compress(Bitmap.CompressFormat.PNG,100,stream);}finally{image.recycle();}
 }
 @Override public void onCreate(Bundle args){super.onCreate(args);phone="true".equals(args.getString("phone"));start();}
 @Override public void onStart(){Bundle result=new Bundle();try{
  String pkg=getTargetContext().getPackageName();
  if(!pkg.endsWith(".returnqa")&&!pkg.endsWith(".uiqa"))throw new AssertionError("QA package required");
  MainActivity.login=MainActivity.object("phase","idle");MainActivity.session="";
  activity=(MainActivity)startActivitySync(new Intent(getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));web=activity.web;
  if(phone){runOnMainSync(()->activity.setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT));SystemClock.sleep(600);activity=MainActivity.current;web=activity.web;runOnMainSync(()->{FrameLayout.LayoutParams layout=new FrameLayout.LayoutParams((int)(393*activity.getResources().getDisplayMetrics().density),-1);layout.gravity=android.view.Gravity.CENTER_HORIZONTAL;web.setLayoutParams(layout);});}
  waitFor("Boolean(window.MobileUI&&window.MobileUI.navigate&&window.mobileReady&&window.LocalArchive)","mobile entry and native bridge loaded");
  eval("window.qaSeeded=false;(async()=>{const d=await new Promise((resolve,reject)=>{const q=indexedDB.open('starfall-local-archives',1);q.onsuccess=()=>resolve(q.result);q.onerror=reject;});await new Promise((resolve,reject)=>{const t=d.transaction(['accounts','settings'],'readwrite');t.objectStore('accounts').clear();t.objectStore('settings').clear();t.oncomplete=resolve;t.onerror=reject;});d.close();clearResults();await LocalArchive.refresh();MobileUI.navigate('query');window.qaSeeded=true;})()");
  waitFor("window.qaSeeded===true&&document.body.dataset.page==='query'","empty library shows query");screenshot("01-query");
  eval("document.getElementById('demo').click()");
  waitFor("document.body.dataset.page==='records'&&document.querySelectorAll('.mobile-event').length>0","expanded pools rendered");screenshot("02-records");
  ok("true".equals(eval("document.querySelectorAll('.mobile-pool>summary').length===0")),"pool cards are not collapsed");
  eval("document.querySelector('#category-tabs [data-category=limited]').click()");
  waitFor("state.category==='limited'&&document.querySelector('.career-category.normal').hidden","category binds existing state");screenshot("03-category");
  eval("MobileUI.navigate('settings');showReport(state.report,state.demo,{preserveView:true})");
  waitFor("document.body.dataset.page==='settings'&&state.category==='limited'","background refresh keeps page and category");eval("MobileUI.navigate('records')");
  eval("document.querySelector('.pool-history').scrollIntoView()");screenshot("04-pools");
  eval("window.qaSeeded=false;(async()=>{const f=await fetch('./demo.json').then(r=>r.json());for(const channel of ['sms','bilibili'])await LocalArchive.api('import',{format:'json',content:JSON.stringify({...f,account:{uid:'90010001',channel,label:channel==='sms'?'旅行者 · 官服':'旅行者 · B服'}})});await LocalArchive.refresh(true);window.qaSeeded=true;})()");
  waitFor("window.qaSeeded===true&&document.querySelectorAll('#account-select option').length===3","channel-separated local archives");
  waitFor("window.scrollY<1","new report starts at the account header");
  eval("document.getElementById('mobile-account').click()");waitFor("document.getElementById('accounts-dialog').open","account sheet opens");screenshot("05-accounts");
  runOnMainSync(()->activity.onBackPressed());waitFor("!document.getElementById('accounts-dialog').open","native back closes sheet");
  eval("MobileUI.navigate('query');MobileUI.navigate('settings')");runOnMainSync(()->activity.onBackPressed());
  waitFor("document.body.dataset.page==='query'","native back restores previous page");
  eval("MobileUI.navigate('records');document.getElementById('export-image').click()");
  waitFor("document.querySelectorAll('#image-previews .image-preview').length>0&&!document.querySelector('#image-previews button').disabled","real canvas image generated");screenshot("06-image");
  runOnMainSync(()->activity.onBackPressed());waitFor("!document.getElementById('image-dialog').open","native back closes image preview");
  runOnMainSync(()->web.getSettings().setTextZoom(140));
  SystemClock.sleep(250);
  screenshot("08-records-large-text");
  ok("true".equals(eval("document.documentElement.scrollWidth<=innerWidth+1")),"large system text has no horizontal overflow");
  ok("true".equals(eval("[...document.querySelectorAll('.career-category:not([hidden])')].every(x=>x.scrollWidth<=x.clientWidth+1)")),"career metrics fit with large text");
  eval("MobileUI.navigate('settings')");screenshot("07-settings-large-text");
  result.putString("result","PASS "+checks+" mobile UI WebView checks");finish(Activity.RESULT_OK,result);
 }catch(Throwable e){result.putString("result","FAIL "+e);finish(Activity.RESULT_CANCELED,result);}finally{if(activity!=null)runOnMainSync(()->activity.finish());}}
}
