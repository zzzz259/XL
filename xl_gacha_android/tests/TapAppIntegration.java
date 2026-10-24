package cn.starfall.gacha.local;

import android.app.*;
import android.content.*;
import android.os.*;
import android.view.View;
import android.webkit.*;
import java.io.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** Intercepts synthetic provider responses and App launch; never contacts an account service. */
public final class TapAppIntegration extends Instrumentation {
 int checks;LoginActivity activity;AtomicInteger launches=new AtomicInteger();AtomicReference<Intent> launched=new AtomicReference<>();AtomicBoolean unavailable=new AtomicBoolean();
 void ok(boolean condition,String name){checks++;if(!condition)throw new AssertionError(name);}
 void checkedMain(Runnable action){AtomicReference<Throwable> error=new AtomicReference<>();runOnMainSync(()->{try{action.run();}catch(Throwable e){error.set(e);}});if(error.get()!=null)throw new AssertionError(error.get());}
 @Override public void onCreate(Bundle b){super.onCreate(b);start();}
 @Override public void onStart(){Bundle result=new Bundle();try{
  TapAppLoginTest.main(new String[0]);result.putInt("tap_policy_checks",TapAppLoginTest.checks);
  ActivityMonitor monitor=new ActivityMonitor(){@Override public ActivityResult onStartActivity(Intent intent){if("com.taptap".equals(intent.getPackage())){if(unavailable.get())throw new ActivityNotFoundException();launches.incrementAndGet();launched.set(intent);return new ActivityResult(Activity.RESULT_CANCELED,null);}return null;}};addMonitor(monitor);
  MainActivity.session="tapQaOwner";TapAppDiagnostics.begin();
  activity=(LoginActivity)startActivitySync(new Intent(getTargetContext(),LoginActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).putExtra("reuse",true).putExtra("tap_app",true));
  WebView web=activity.views.get(0);CountDownLatch loaded=new CountDownLatch(1);
  checkedMain(()->{
   activity.handler.removeCallbacks(activity.poll);web.stopLoading();WebViewClient production=web.getWebViewClient();
   web.setWebViewClient(new WebViewClient(){
    @Override public WebResourceResponse shouldInterceptRequest(WebView v,WebResourceRequest request){
     String body=request.getUrl().getPath().equals("/api/device-code/get")?"{\"data\":{\"qrcode_url\":\"https://accounts.taptap.cn/device?qrcode=qaChallenge&user_code=QA123456\",\"expires_in\":120}}":"<!doctype html><button class='login-page__content__login-mode-icon' onclick=\"fetch('/api/device-code/get')\">QR mode</button>";
     return new WebResourceResponse(request.getUrl().getPath().equals("/api/device-code/get")?"application/json":"text/html","UTF-8",new ByteArrayInputStream(body.getBytes()));
    }
    @Override public void onPageStarted(WebView v,String url,android.graphics.Bitmap icon){production.onPageStarted(v,url,icon);}
    @Override public void onPageFinished(WebView v,String url){production.onPageFinished(v,url);if("https://accounts.taptap.cn/login?qa=1".equals(url))loaded.countDown();}
   });MainActivity.phase("waiting","QA only");web.loadUrl("https://accounts.taptap.cn/login?qa=1");
  });
  ok(loaded.await(10,TimeUnit.SECONDS),"official synthetic page loaded");
  for(int i=0;i<50&&activity.tap.challenge==null;i++){checkedMain(()->activity.tap.poll());SystemClock.sleep(100);}
  checkedMain(()->{
   ok(activity.tap.challenge!=null,"production fetch observer captures fresh challenge");
   ok(activity.tap.open.isEnabled(),"native App action enabled");
   activity.tap.open.performClick();ok(launches.get()==1,"one App launch");
   ok("com.taptap".equals(launched.get().getPackage()),"fixed official package");
   ok(activity.tap.opened&&activity.tap.challenge==null,"challenge consumed on handoff");
   activity.tap.poll();ok(launches.get()==1,"poll never reopens App");
   unavailable.set(true);activity.tap.opened=false;activity.tap.challenge=new TapAppLogin.Challenge(web.getUrl(),"https://accounts.taptap.cn/device?qrcode=qaChallenge&user_code=QA123456",activity.ownerSession,activity.tap.generation,SystemClock.uptimeMillis());activity.tap.press();
   ok(activity.tap.info.getText().toString().contains("无法打开 TapTap"),"missing App produces actionable fallback");
   ok("waiting".equals(MainActivity.login.optString("phase"))&&!activity.tap.requested,"web flow survives missing App");
   MainActivity.session="changedOwner";activity.tap.press();ok(launches.get()==1,"cancelled/switched owner cannot launch");MainActivity.session="tapQaOwner";activity.tap.requested=true;
   WebView popup=activity.createWebView();ok(activity.tap.panel.getVisibility()==View.GONE,"toolbar follows active window");activity.views.remove(popup);activity.layout.removeView(popup);popup.destroy();activity.showView(web);ok(activity.tap.panel.getVisibility()==View.VISIBLE,"return restores original official window");
   activity.tap.navigation(web,"https://evil.example/login");ok(activity.tap.panel.getVisibility()==View.GONE,"toolbar hides outside official flow");
   activity.tap.navigation(web,"https://accounts.taptap.cn/authorize");result.putLong("timeout_check_uptime_ms",SystemClock.uptimeMillis());activity.tap.expiresAt=SystemClock.uptimeMillis()-1;
   activity.tap.poll();ok("TAP_AUTH_TIMEOUT".equals(MainActivity.login.optString("error")),"expired authorization cannot remain active");
  });
  waitForIdleSync();ok("error".equals(MainActivity.login.optString("phase")),"destroy preserves timeout error");
  ok(!TapAppDiagnostics.report(getTargetContext()).toString().contains("qaChallenge"),"diagnostic never retains challenge");
  removeMonitor(monitor);result.putString("result","PASS "+checks+" TapTap App WebView/handoff checks");finish(Activity.RESULT_OK,result);
 }catch(Throwable e){result.putString("result","FAIL "+e);finish(Activity.RESULT_CANCELED,result);}finally{if(activity!=null&&!activity.isFinishing())runOnMainSync(()->activity.finish());}}
}
