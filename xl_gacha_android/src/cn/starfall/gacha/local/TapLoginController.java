package cn.starfall.gacha.local;

import android.content.*;
import android.os.SystemClock;
import android.view.View;
import android.webkit.WebView;
import android.widget.*;
import org.json.*;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/** Keeps the official portal WebView alive while its QR challenge is confirmed in TapTap. */
final class TapLoginController {
 final LoginActivity activity;final TextView info;final Button open,web;final LinearLayout panel;
 WebView view;long generation,expiresAt;boolean requested,opened,reading,closed;String script;
 TapAppLogin.Challenge challenge;
 TapLoginController(LoginActivity activity,boolean requested){
  this.activity=activity;this.requested=requested;
  panel=new LinearLayout(activity);panel.setOrientation(LinearLayout.VERTICAL);panel.setPadding(12,0,12,4);panel.setVisibility(View.GONE);
  info=new TextView(activity);info.setTextColor(0xffeed4a4);panel.addView(info);
  LinearLayout row=new LinearLayout(activity);panel.addView(row);
  open=new Button(activity);open.setText("使用 TapTap App 授权");row.addView(open,new LinearLayout.LayoutParams(0,-2,1));open.setOnClickListener(v->press());
  web=new Button(activity);web.setText("继续网页登录");row.addView(web,new LinearLayout.LayoutParams(0,-2,1));web.setOnClickListener(v->{this.requested=false;challenge=null;open.setText("使用 TapTap App 授权");open.setEnabled(TapAppLogin.loginPage(view==null?null:view.getUrl()));info.setText("可在下方继续网页登录。需要重试 App 授权时，再点左侧按钮。");TapAppDiagnostics.event("WEB_FALLBACK");});
  Button copy=new Button(activity);copy.setText("复制诊断");row.addView(copy,new LinearLayout.LayoutParams(0,-2,1));copy.setOnClickListener(v->copyDiagnostic(activity));
  activity.layout.addView(panel);
  try(InputStream in=activity.getAssets().open("tap-app-login.js")){script=new String(GachaClient.bounded(in,32768),StandardCharsets.UTF_8);}catch(Exception e){script="";}
 }
 static void copyDiagnostic(Context c){try{String report="星落手记 TapTap App 脱敏诊断（无账号与凭据）\n"+TapAppDiagnostics.report(c).toString(2);((ClipboardManager)c.getSystemService(Context.CLIPBOARD_SERVICE)).setPrimaryClip(ClipData.newPlainText("TapTap 授权诊断",report));Toast.makeText(c,"脱敏诊断已复制",Toast.LENGTH_SHORT).show();}catch(Exception ignored){}}
 boolean current(){return !closed&&!activity.isFinishing()&&!activity.accepted&&activity.ownerSession.equals(MainActivity.session)&&"waiting".equals(MainActivity.login.optString("phase"))&&activity.views.contains(view);}
 void visible(WebView active){if(view==active)return;generation++;challenge=null;reading=false;view=null;panel.setVisibility(View.GONE);if(TapAppLogin.authPage(active.getUrl()))navigation(active,active.getUrl());}
 void navigation(WebView changed,String url){
  if(changed==view){generation++;challenge=null;reading=false;}
  if(!TapAppLogin.authPage(url)){if(changed==view){view=null;panel.setVisibility(View.GONE);}return;}
  if(view!=changed){view=changed;generation++;challenge=null;reading=false;}
  if(expiresAt==0)expiresAt=SystemClock.uptimeMillis()+120000;
  panel.setVisibility(View.VISIBLE);
  if(requested)TapAppDiagnostics.event(TapAppLogin.loginPage(url)?"OFFICIAL_LOGIN_PAGE":"OFFICIAL_AUTHORIZE_PAGE");
  update();
 }
 void update(){
  boolean login=view!=null&&TapAppLogin.loginPage(view.getUrl());
  open.setEnabled(login&&(!requested||opened||challenge!=null));
  open.setText(opened?"重新准备 App 授权":challenge==null&&requested?"正在准备授权…":"打开 TapTap App");
  info.setText(!login?"请在下方完成官网授权确认。":opened?"已打开 TapTap。确认后请手动返回手记，继续下方官网授权；若取消，可重新准备。":requested?"正在准备本次授权，准备好后点击“打开 TapTap App”。":"可使用已登录的 TapTap App 确认，也可继续网页登录。");
 }
 void press(){
  if(!current())return;
  if(!requested){requested=true;TapAppDiagnostics.begin();}
  if(opened){opened=false;challenge=null;view.evaluateJavascript("if(window.__starfallTapApp)window.__starfallTapApp.reset()",null);TapAppDiagnostics.event("RETRY_REQUESTED");}
  if(challenge==null){update();poll();return;}
  Intent intent=challenge.consume(view.getUrl(),activity.ownerSession,generation,SystemClock.uptimeMillis());challenge=null;
  if(intent==null){update();return;}
  try{activity.startActivity(intent);opened=true;TapAppDiagnostics.event("APP_OPENED");update();}
  catch(ActivityNotFoundException|SecurityException e){requested=false;TapAppDiagnostics.finish("APP_UNAVAILABLE");open.setEnabled(true);open.setText("重试 TapTap App");info.setText("无法打开 TapTap。请安装并登录官方 TapTap App，或点“继续网页登录”。");}
 }
 void poll(){
  if(!current())return;
  if(requested&&expiresAt>0&&SystemClock.uptimeMillis()>=expiresAt){TapAppDiagnostics.finish("AUTH_TIMEOUT");MainActivity.failLogin("TAP_AUTH_TIMEOUT");activity.finish();return;}
  if(!TapAppLogin.loginPage(view.getUrl()))return;
  if(reading||script.isEmpty())return;
  reading=true;final WebView target=view;final long epoch=generation;final String page=view.getUrl();
  target.evaluateJavascript(script+";window.__starfallTapApp?window.__starfallTapApp."+(requested?"request()":"read()")+":null",result->{
   if(epoch!=generation||target!=view)return;reading=false;
   if(!current()||!page.equals(view.getUrl())||opened)return;
   try{JSONObject data=new JSONObject(result);String url=data.optString("qrcode_url","");
    if(requested&&!url.isEmpty()&&challenge==null){try{challenge=new TapAppLogin.Challenge(page,url,activity.ownerSession,epoch,SystemClock.uptimeMillis());TapAppDiagnostics.event("APP_HANDOFF_READY");update();}catch(IllegalArgumentException e){requested=false;TapAppDiagnostics.finish("CHALLENGE_INVALID");open.setEnabled(true);info.setText("暂时无法识别本次授权，请继续网页登录，并复制诊断反馈。");}}
    if(requested&&"expired".equals(data.optString("phase"))){TapAppDiagnostics.finish("CHALLENGE_EXPIRED");MainActivity.failLogin("TAP_AUTH_TIMEOUT");activity.finish();}
   }catch(Exception ignored){}
  });
 }
 void resume(){if(opened&&current()){TapAppDiagnostics.event("RETURNED_TO_HANDBOOK");update();}}
 void close(){closed=true;challenge=null;view=null;if(requested&&!activity.accepted&&"cancelled".equals(MainActivity.login.optString("phase")))TapAppDiagnostics.finish("CANCELLED");}
}
