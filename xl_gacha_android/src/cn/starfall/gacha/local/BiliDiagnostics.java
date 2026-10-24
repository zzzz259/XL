package cn.starfall.gacha.local;
import android.content.Context;
import android.content.pm.PackageInfo;
import android.os.Build;
import org.json.*;

/** Only explicitly constructed diagnostic fields; never receives an account or credential object. */
final class BiliDiagnostics {
 private static JSONObject value;
 static synchronized void begin(Context c){
  value=MainActivity.object("format","starfall-handbook-bili/1","android_api",Build.VERSION.SDK_INT,"android_release",Build.VERSION.RELEASE,"manufacturer",Build.MANUFACTURER,"model",Build.MODEL,"network_policy","direct","credential_values_persisted",false,"queries",new JSONArray());
  try{PackageInfo p=c.getPackageManager().getPackageInfo(c.getPackageName(),0);put("app_version",p.versionName);p=c.getPackageManager().getPackageInfo("tv.danmaku.bili",0);put("bilibili_version",p.versionName);}catch(Exception e){put("bilibili_version","unavailable");}
  put("started_at_ms",System.currentTimeMillis());put("stage","official_consent");save(c);
 }
 static synchronized void put(String key,Object v){if(value!=null)try{value.put(key,v);}catch(JSONException ignored){}}
 static synchronized void query(int selector,int code,int count){if(value!=null){value.optJSONArray("queries").put(MainActivity.object("selector",selector,"ret_code",code,"record_count",count));put("stage","history");}}
 static synchronized void finish(Context c,String outcome){put("outcome",outcome);put("finished_at_ms",System.currentTimeMillis());save(c);}
 private static void save(Context c){if(value!=null)c.getSharedPreferences("bili-diagnostics",0).edit().putString("last",value.toString()).apply();}
 static synchronized JSONObject report(Context c)throws Exception{return value!=null?new JSONObject(value.toString()):new JSONObject(c.getSharedPreferences("bili-diagnostics",0).getString("last","{\"format\":\"starfall-handbook-bili/1\",\"outcome\":\"NOT_STARTED\"}"));}
}
