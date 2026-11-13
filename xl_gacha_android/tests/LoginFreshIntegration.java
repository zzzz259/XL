package cn.starfall.gacha.local;

import android.app.*;
import android.content.*;
import android.os.*;
import android.webkit.*;
import java.io.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** Isolated WebView regression: no real credentials or external network requests. */
public final class LoginFreshIntegration extends Instrumentation {
 int checks;LoginActivity activity;
 void ok(boolean value,String name){if(!value)throw new AssertionError(name);checks++;}
 String evaluate(WebView web,String script)throws Exception{
  CountDownLatch done=new CountDownLatch(1);AtomicReference<String> value=new AtomicReference<>();
  runOnMainSync(()->web.evaluateJavascript(script,v->{value.set(v);done.countDown();}));
  if(!done.await(10,TimeUnit.SECONDS))throw new AssertionError("WebView evaluation timeout");return value.get();
 }
 @Override public void onCreate(Bundle b){super.onCreate(b);start();}
 @Override public void onStart(){Bundle result=new Bundle();try{
  MainActivity.session="fresh-login-regression";
  activity=(LoginActivity)startActivitySync(new Intent(getTargetContext(),LoginActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).putExtra("reuse",true));
  final WebView web=activity.views.get(0);AtomicInteger loads=new AtomicInteger();AtomicBoolean delegate=new AtomicBoolean();CountDownLatch seeded=new CountDownLatch(1);
  runOnMainSync(()->{
   activity.handler.removeCallbacks(activity.poll);web.stopLoading();WebViewClient product=web.getWebViewClient();
   activity.fresh=true;activity.pollingReady=false;activity.storageCleared=false;activity.storageClearPending=false;
   web.setWebViewClient(new WebViewClient(){
    @Override public WebResourceResponse shouldInterceptRequest(WebView v,WebResourceRequest request){return new WebResourceResponse("text/html","UTF-8",new ByteArrayInputStream("<!doctype html><title>Isolated login fixture</title>".getBytes()));}
    @Override public void onPageFinished(WebView v,String url){if(!(LoginActivity.ORIGIN+"/fresh-login-fixture").equals(url))return;loads.incrementAndGet();if(delegate.get())product.onPageFinished(v,url);else seeded.countDown();}
   });
   web.setTag(product);web.loadUrl(LoginActivity.ORIGIN+"/fresh-login-fixture");
  });
  ok(seeded.await(10,TimeUnit.SECONDS),"fixture loaded without provider network");
  ok("true".equals(evaluate(web,"(()=>{for(const k of ['jwt','lastjwt','HP_2070021','HP_2070023','provider'])localStorage.setItem(k,'DUMMY_OLD_ACCOUNT');sessionStorage.setItem('old','DUMMY');return localStorage.length>=5;})()")),"old login storage seeded");
  runOnMainSync(()->{ok(!activity.pollingReady,"credential polling blocked before cleanup");delegate.set(true);((WebViewClient)web.getTag()).onPageFinished(web,web.getUrl());});
  for(int i=0;i<100&&!activity.pollingReady;i++)SystemClock.sleep(100);
  runOnMainSync(()->{ok(activity.storageCleared,"actual origin storage cleared");ok(activity.pollingReady,"polling enabled after reload");ok(loads.get()>=2,"clean origin reloaded before detection");ok(!activity.accepted,"old credential never accepted");});
  ok("true".equals(evaluate(web,"localStorage.length===0&&sessionStorage.length===0")),"JWT wrappers/provider/session data removed");
  result.putString("result","PASS "+checks+" fresh-login WebView checks");finish(Activity.RESULT_OK,result);
 }catch(Throwable e){result.putString("result","FAIL "+e);finish(Activity.RESULT_CANCELED,result);}finally{if(activity!=null)runOnMainSync(()->activity.finish());}}
}
