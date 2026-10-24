package cn.starfall.gacha.local;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.os.Build;
import android.webkit.WebView;
import org.json.*;

/** Only fixed stage codes, environment versions and aggregate query counts. */
final class TapAppDiagnostics {
 private static JSONArray events=new JSONArray();private static long started;private static String outcome="NOT_STARTED";
 static synchronized void begin(){events=new JSONArray();started=System.currentTimeMillis();outcome="IN_PROGRESS";event("STARTED");}
 static synchronized void event(String stage){if(stage.matches("[A-Z_]{1,60}")&&events.length()<60)events.put(MainActivity.object("stage",stage,"elapsed_ms",started==0?0:System.currentTimeMillis()-started));}
 static synchronized void finish(String result){outcome=result;event(result);}
 static synchronized void queryFinished(boolean success){if("PORTAL_CREDENTIAL_READY".equals(outcome))finish(success?"QUERY_SUCCEEDED":"QUERY_FAILED");}
 static synchronized JSONObject report(Context c){
  JSONObject result=MainActivity.object("format","starfall-tap-app/1","started_at_ms",started,"outcome",outcome,"events",events,"credential_values_retained",false,"android_api",Build.VERSION.SDK_INT,"manufacturer",Build.MANUFACTURER,"model",Build.MODEL);
  try{PackageInfo p=c.getPackageManager().getPackageInfo(c.getPackageName(),0);result.put("app_version",p.versionName);result.put("app_version_code",p.getLongVersionCode());}catch(Exception ignored){}
  try{PackageInfo p=c.getPackageManager().getPackageInfo("com.taptap",0);result.put("taptap",MainActivity.object("installed",true,"version",p.versionName,"enabled",p.applicationInfo.enabled));}catch(Exception e){try{result.put("taptap",MainActivity.object("installed",false));}catch(JSONException ignored){}}
  try{PackageInfo p=WebView.getCurrentWebViewPackage();result.put("webview_version",p==null?"unknown":p.versionName);}catch(Exception ignored){}
  return result;
 }
}
