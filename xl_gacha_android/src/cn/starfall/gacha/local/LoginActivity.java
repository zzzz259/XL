package cn.starfall.gacha.local;

import android.app.*;
import android.content.*;
import android.net.Uri;
import android.os.*;
import android.webkit.*;
import android.widget.*;
import org.json.*;
import java.util.*;

public class LoginActivity extends Activity {
 static LoginActivity current;
 final Handler handler=new Handler(Looper.getMainLooper());final ArrayList<WebView> views=new ArrayList<>();
 LinearLayout layout;boolean accepted=false,remember=false,pollingReady=false,fresh=false,storageCleared=false,storageClearPending=false;long deadline,externalAuthUntil;
 String authorizationState,ownerSession;WebView authorizationView;boolean returnConsumed;
 TapLoginController tap;
 static final String ORIGIN="https://i.haoplay.com.cn";
 static final String READ="(()=>{if(location.origin!=='https://i.haoplay.com.cn')return null;const v={};for(const k of ['jwt','HP_2070021','HP_2070023','lastjwt'])v[k]=localStorage.getItem(k);return JSON.stringify({provider:localStorage.getItem('provider')||'',values:v});})()";
 @Override public void onCreate(Bundle b){super.onCreate(b);current=this;ownerSession=MainActivity.session;remember=getIntent().getBooleanExtra("remember",false);deadline=System.currentTimeMillis()+600000;
  layout=new LinearLayout(this);layout.setOrientation(LinearLayout.VERTICAL);layout.setBackgroundColor(0xff17212d);layout.setOnApplyWindowInsetsListener((v,i)->{v.setPadding(i.getSystemWindowInsetLeft(),i.getSystemWindowInsetTop(),i.getSystemWindowInsetRight(),i.getSystemWindowInsetBottom());return i;});setContentView(layout);
  TextView title=new TextView(this);title.setText("官网登录 · 登录后自动查询\nhttps://i.haoplay.com.cn\nQQ 授权若打开浏览器：分享页面 → 抽卡手记·继续QQ授权");title.setTextColor(0xffeed4a4);title.setPadding(16,12,16,12);layout.addView(title);
  tap=new TapLoginController(this,getIntent().getBooleanExtra("tap_app",false));
  fresh=!getIntent().getBooleanExtra("reuse",false);
  WebView web=createWebView();
  Runnable load=()->{if(!isFinishing()){web.loadUrl(ORIGIN+"/?appid=2070021&l=cn");MainActivity.phase("waiting",tap.requested?"请在官网选择 TapTap，进入授权页后点击上方“打开 TapTap App”。":"请在官网正常完成短信或 TapTap 登录。");handler.postDelayed(poll,1000);}};
  if(!getIntent().getBooleanExtra("reuse",false)){getSharedPreferences("login",MODE_PRIVATE).edit().clear().apply();clearRemoteSessions(load);}else load.run();
 }
 WebView createWebView(){WebView w=new WebView(this);views.add(w);layout.addView(w,new LinearLayout.LayoutParams(-1,0,1));showView(w);
  w.setOnTouchListener((v,event)->{if(event.getAction()==android.view.MotionEvent.ACTION_UP)externalAuthUntil=SystemClock.uptimeMillis()+30000;return false;});
  WebSettings s=w.getSettings();s.setJavaScriptEnabled(true);s.setDomStorageEnabled(true);s.setSupportMultipleWindows(true);s.setAllowFileAccess(false);s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
  CookieManager.getInstance().setAcceptThirdPartyCookies(w,true);
  w.setWebViewClient(new WebViewClient(){
   @Override public void onPageStarted(WebView v,String url,android.graphics.Bitmap icon){recordAuthorization(v,Uri.parse(url));tap.navigation(v,url);}
   @Override public void onPageFinished(WebView v,String url){
    if(isFinishing()||!ORIGIN.equals(Uri.parse(url).getScheme()+"://"+Uri.parse(url).getHost()))return;
    if(fresh&&!storageCleared){if(storageClearPending)return;storageClearPending=true;v.evaluateJavascript("(()=>{if(location.origin!=='https://i.haoplay.com.cn')return false;localStorage.clear();sessionStorage.clear();return true;})()",value->{if(isFinishing())return;if(!"true".equals(value)){MainActivity.phase("error","无法清理旧登录状态，请重试。");finish();return;}storageCleared=true;v.clearCache(true);v.reload();});}
    else pollingReady=true;
   }
   @Override public boolean shouldOverrideUrlLoading(WebView v,WebResourceRequest r){recordAuthorization(v,r.getUrl());String scheme=r.getUrl().getScheme();if("https".equals(scheme))return false;
   String source=v.getUrl();if((source==null||"about:blank".equals(source))&&v.getTag() instanceof String)source=(String)v.getTag();
   if(LoginLinks.allowed(source,r.hasGesture(),SystemClock.uptimeMillis()<externalAuthUntil)){try{Intent link=LoginLinks.create(r.getUrl());if(link!=null)try{startActivity(link);}catch(ActivityNotFoundException|SecurityException e){Toast.makeText(LoginActivity.this,"无法打开授权应用。请确认 QQ/TapTap 已安装在同一设备和用户空间，或选择其他登录方式。",Toast.LENGTH_LONG).show();}}catch(Exception ignored){}}return true;}});
  w.setWebChromeClient(new WebChromeClient(){@Override public boolean onCreateWindow(WebView v,boolean dialog,boolean gesture,android.os.Message msg){if(!gesture&&!(SystemClock.uptimeMillis()<externalAuthUntil&&LoginLinks.trusted(v.getUrl())))return false;WebView child=createWebView();child.setTag(v.getUrl());((WebView.WebViewTransport)msg.obj).setWebView(child);msg.sendToTarget();return true;}
   @Override public void onCloseWindow(WebView v){if(views.size()>1){views.remove(v);layout.removeView(v);v.destroy();showView(views.get(views.size()-1));}}
  });return w;
 }
 void showView(WebView active){for(WebView view:views)view.setVisibility(view==active?android.view.View.VISIBLE:android.view.View.GONE);if(tap!=null)tap.visible(active);}
 void recordAuthorization(WebView view,Uri uri){String state=LoginReturn.authorizationState(uri);if(state!=null){if(!state.equals(authorizationState))returnConsumed=false;authorizationState=state;authorizationView=view;}}
 boolean canResumeShare(Uri uri){return current==this&&!isFinishing()&&!accepted&&!returnConsumed&&ownerSession.equals(MainActivity.session)&&"waiting".equals(MainActivity.login.optString("phase"))&&System.currentTimeMillis()<deadline&&views.contains(authorizationView)&&LoginReturn.matches(uri.toString(),authorizationState);}
 boolean resumeShare(Uri uri){if(!canResumeShare(uri))return false;returnConsumed=true;authorizationView.loadUrl(uri.toString());return true;}
 final Runnable poll=new Runnable(){public void run(){if(isFinishing()||accepted)return;if(System.currentTimeMillis()>deadline){MainActivity.phase("error","等待登录超时，请重新打开官网。");finish();return;}
  tap.poll();if(isFinishing())return;
  if(!pollingReady){handler.postDelayed(this,1000);return;}
  for(WebView w:new ArrayList<>(views)){String u=w.getUrl();if(u==null)continue;Uri uri=Uri.parse(u);if(!"https".equals(uri.getScheme())||!"i.haoplay.com.cn".equals(uri.getHost())||(uri.getPort()!=-1&&uri.getPort()!=443))continue;
   w.evaluateJavascript(READ,result->{if(accepted||isFinishing()||result==null||"null".equals(result))return;
    try{Object decoded=new JSONTokener(result).nextValue();if(!(decoded instanceof String))return;JSONObject state=new JSONObject((String)decoded),values=state.getJSONObject("values");String provider=state.optString("provider");
     for(String key:new String[]{"jwt","HP_2070021","HP_2070023","lastjwt"}){String raw=values.optString(key,"");if(raw.isEmpty()||raw.equals("null"))continue;
      try{GachaClient.Credential c=GachaClient.credential(raw);if(!provider.isEmpty()&&!c.provider.equals(provider))continue;accepted=true;if(tap.requested)TapAppDiagnostics.finish("PORTAL_CREDENTIAL_READY");MainActivity.acceptCredential(c.token);finish();break;}catch(Exception ignored){}}
    }catch(Exception ignored){}
   });
  }handler.postDelayed(this,1000);
 }};
 static void clearRemoteSessions(Runnable done){
  WebStorage.getInstance().getOrigins(origins->{for(Object value:origins.keySet()){String origin=String.valueOf(value);if(!MainActivity.HOST.equals(Uri.parse(origin).getHost()))WebStorage.getInstance().deleteOrigin(origin);}
   CookieManager.getInstance().removeAllCookies(ok->{CookieManager.getInstance().flush();done.run();});});
 }
 @Override protected void onResume(){super.onResume();if(tap!=null)tap.resume();}
 @Override public void onBackPressed(){if(views.size()>1){WebView last=views.remove(views.size()-1);layout.removeView(last);last.destroy();showView(views.get(views.size()-1));return;}finish();}
 @Override protected void onDestroy(){handler.removeCallbacks(poll);if(!accepted&&!Arrays.asList("cancelled","error","querying","done").contains(MainActivity.login.optString("phase")))MainActivity.phase("cancelled","已取消官网登录。");
  if(tap!=null)tap.close();
  for(WebView w:views)w.destroy();views.clear();if(!remember)clearRemoteSessions(()->{});if(current==this)current=null;super.onDestroy();}
}
