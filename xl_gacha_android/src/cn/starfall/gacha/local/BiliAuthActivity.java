package cn.starfall.gacha.local;

import android.app.Activity;
import android.content.*;
import android.os.Bundle;
import android.widget.*;

/** Own application identity is always shown in the official Bilibili consent UI. */
public final class BiliAuthActivity extends Activity {
 static BiliAuthActivity current;
 private static final int AUTH=7041;
 @Override public void onCreate(Bundle saved){
  super.onCreate(saved);current=this;
  LinearLayout layout=new LinearLayout(this);layout.setOrientation(1);int pad=(int)(24*getResources().getDisplayMetrics().density);layout.setPadding(pad,pad,pad,pad);
  layout.setOnApplyWindowInsetsListener((v,i)->{v.setPadding(pad+i.getSystemWindowInsetLeft(),pad+i.getSystemWindowInsetTop(),pad+i.getSystemWindowInsetRight(),pad+i.getSystemWindowInsetBottom());return i;});
  TextView text=new TextView(this);text.setTextSize(18);text.setText("请在哔哩哔哩的官方页面确认授权。\n\n返回后自动识别账号并查询全部已知卡池，不需要输入 UID。\n\n若 B 站启动广告遮住授权页面，请关闭广告，或从最近任务返回此页面。账号凭据仅用于本次查询，不写入存档。");layout.addView(text);
  Button cancel=new Button(this);cancel.setText("取消并返回手记");cancel.setOnClickListener(v->{MainActivity.phase("cancelled","已取消 B 服授权。");back();});layout.addView(cancel);setContentView(layout);
  if(saved==null){
   try{BiliAuthClient.config(this);MainActivity.phase("waiting","请在 B 站官方页面确认授权…");Intent intent=new Intent("tv.danmaku.bili.action.AUTHORIZE").setComponent(new ComponentName("tv.danmaku.bili","tv.danmaku.bili.activities.login.SSOActivity")).putExtra("package_name",getPackageName());startActivityForResult(intent,AUTH);}
   catch(ActivityNotFoundException e){MainActivity.failLogin("BILI_APP_NOT_FOUND");back();}
   catch(SecurityException e){MainActivity.failLogin("BILI_AUTH_UNAVAILABLE");back();}
   catch(Exception e){MainActivity.failLogin("BILI_CONFIG_MISSING");back();}
  }
 }
 private void back(){startActivity(new Intent(this,MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP|Intent.FLAG_ACTIVITY_SINGLE_TOP));finish();}
 @Override protected void onActivityResult(int request,int result,Intent data){super.onActivityResult(request,result,data);if(request!=AUTH)return;
  try{
   if(result!=RESULT_OK){MainActivity.phase("cancelled","B 站授权已取消，未开始查询。");BiliDiagnostics.finish(this,"CONSENT_CANCELLED");}
   else{Object value=data==null||data.getExtras()==null?null:data.getExtras().get("access_key");if(!(value instanceof String)||((String)value).isEmpty())MainActivity.failLogin("BILI_AUTH_RESULT_MISSING");else MainActivity.acceptBiliGrant(getApplicationContext(),(String)value);}
  }catch(RuntimeException e){MainActivity.failLogin("BILI_AUTH_RESULT_MISSING");}
  finally{if(data!=null)data.replaceExtras((Bundle)null);back();}
 }
 @Override public void onBackPressed(){MainActivity.phase("cancelled","已取消 B 服授权。");back();}
 @Override protected void onDestroy(){if(current==this)current=null;super.onDestroy();}
}
