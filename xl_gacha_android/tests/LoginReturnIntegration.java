package cn.starfall.gacha.local;
import android.app.*;
import android.content.*;
import android.net.Uri;
import android.os.*;
import android.webkit.*;
import android.view.accessibility.AccessibilityNodeInfo;
import java.io.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** Disposable isolated APK. No real credentials, account data or provider requests. */
public class LoginReturnIntegration extends Instrumentation {
 int checks; LoginActivity login; AtomicInteger callbacks=new AtomicInteger();boolean separateTask;
 static final String CALLBACK="https://accounts.taptap.cn/social/callback/qq?code=qaCode&state=qaState";
 void ok(boolean value,String name){if(!value)throw new AssertionError(name);checks++;}
 @Override public void onCreate(Bundle b){super.onCreate(b);separateTask=!"false".equals(b.getString("separateTask"));start();}
 @Override public void onStart(){Bundle result=new Bundle();try{
  MainActivity.session="qaSession";
  login=(LoginActivity)startActivitySync(new Intent(getTargetContext(),LoginActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).putExtra("reuse",true));
  runOnMainSync(()->{
   login.handler.removeCallbacks(login.poll);login.views.get(0).stopLoading();
   login.views.get(0).setWebViewClient(new WebViewClient(){
    @Override public WebResourceResponse shouldInterceptRequest(WebView v,WebResourceRequest r){return new WebResourceResponse("text/html","UTF-8",new ByteArrayInputStream("<p>Isolated callback test</p>".getBytes()));}
    @Override public void onPageStarted(WebView v,String u,android.graphics.Bitmap icon){if(u.startsWith("https://accounts.taptap.cn/social/callback/qq?"))callbacks.incrementAndGet();}
   });
   MainActivity.phase("waiting","QA only");
   login.recordAuthorization(login.views.get(0),Uri.parse("https://graph.qq.com/oauth2.0/m_authorize?state=qaState"));
   WebView popup=login.createWebView();login.recordAuthorization(popup,Uri.parse("https://graph.qq.com/oauth2.0/m_authorize?state=qaState"));
   ok(login.authorizationView==popup,"same state binds active authorization popup");
   login.recordAuthorization(login.views.get(0),Uri.parse("https://graph.qq.com/oauth2.0/m_authorize?state=qaState"));login.views.remove(popup);login.layout.removeView(popup);popup.destroy();
   ok(login.canResumeShare(Uri.parse(CALLBACK)),"pending match");
   ok(!login.canResumeShare(Uri.parse(CALLBACK.replace("qaState","otherState"))),"wrong state");
   MainActivity.session="otherSession";ok(!login.canResumeShare(Uri.parse(CALLBACK)),"changed login session");MainActivity.session="qaSession";
   MainActivity.phase("cancelled","QA only");ok(!login.canResumeShare(Uri.parse(CALLBACK)),"cancelled login");MainActivity.phase("waiting","QA only");
   long saved=login.deadline;login.deadline=0;ok(!login.canResumeShare(Uri.parse(CALLBACK)),"expired login");login.deadline=saved;
   login.accepted=true;ok(!login.canResumeShare(Uri.parse(CALLBACK)),"credential already accepted");login.accepted=false;
   WebView savedView=login.authorizationView;login.authorizationView=null;ok(!login.canResumeShare(Uri.parse(CALLBACK)),"window missing");login.authorizationView=savedView;
  });
  startActivitySync(new Intent(Intent.ACTION_SEND).setClass(getTargetContext(),LoginReturnActivity.class).setType("text/plain").putExtra(Intent.EXTRA_TEXT,CALLBACK).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|(separateTask?Intent.FLAG_ACTIVITY_MULTIPLE_TASK:0)));
  waitForIdleSync();boolean clicked=false;
  for(int i=0;i<30&&!clicked;i++){
   AccessibilityNodeInfo root=getUiAutomation().getRootInActiveWindow();
   if(root!=null)for(AccessibilityNodeInfo node:root.findAccessibilityNodeInfosByText("继续登录"))if(node.isClickable()&&"继续登录".contentEquals(node.getText())){clicked=node.performAction(AccessibilityNodeInfo.ACTION_CLICK);break;}
   if(!clicked)SystemClock.sleep(100);
  }
  ok(clicked,"share confirmation button");
  for(int i=0;i<50&&callbacks.get()==0;i++)SystemClock.sleep(100);
  runOnMainSync(()->{ok(callbacks.get()==1,"exact pending WebView loaded callback once");ok(!login.canResumeShare(Uri.parse(CALLBACK)),"replay rejected");ok(LoginActivity.current==login,"original login Activity restored");});
  result.putString("result","PASS "+checks+" isolated Android share/session/window checks");finish(Activity.RESULT_OK,result);
 }catch(Throwable e){result.putString("result","FAIL "+e.toString());finish(Activity.RESULT_CANCELED,result);}finally{if(login!=null)runOnMainSync(()->login.finish());}}
}
