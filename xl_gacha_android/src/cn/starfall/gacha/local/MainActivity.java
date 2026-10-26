package cn.starfall.gacha.local;

import android.app.*;
import android.content.*;
import android.net.Uri;
import android.os.Bundle;
import android.util.Base64;
import android.webkit.*;
import android.widget.*;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;

public class MainActivity extends Activity {
 static final String HOST="appassets.androidplatform.net",ORIGIN="https://"+HOST;
 static MainActivity current;
 static CatalogUpdates updates;
 static final ExecutorService IO=Executors.newCachedThreadPool();
 static JSONObject login=new JSONObject();
 static boolean remember=false;
 static String session="";
 WebView web;ValueCallback<Uri[]> picker;byte[] exportBytes;String exportRequest;
 byte[] albumBytes;String albumName,albumRequest;
 static JSONObject object(Object... kv){JSONObject j=new JSONObject();try{for(int i=0;i<kv.length;i+=2)j.put((String)kv[i],kv[i+1]);}catch(JSONException ignored){}return j;}
 static synchronized void phase(String name,String message){login=object("phase",name,"message",message,"id",session);}
 static synchronized void failLogin(String code){if(code==null||!code.matches("[A-Z_0-9]{1,70}"))code="QUERY_FAILED";login=object("phase","error","error",code,"id",session);}
 static synchronized void ensureCatalog(Context context)throws Exception{
  if(updates==null){JSONObject base;JSONObject source;
   try(InputStream in=context.getAssets().open("catalog.json")){base=new JSONObject(new String(GachaClient.bounded(in,4*1024*1024),StandardCharsets.UTF_8));}
   try(InputStream in=context.getAssets().open("update-source.json")){source=new JSONObject(new String(GachaClient.bounded(in,16384),StandardCharsets.UTF_8));}
   updates=new CatalogUpdates(new File(context.getFilesDir(),"catalog-cache"),base,source.optString("url",""));
  }
 }
 static synchronized JSONObject loginStatus(Context c)throws Exception{
  JSONObject out=new JSONObject(login.toString());if(!out.has("phase"))out.put("phase","idle");
  JSONArray profiles=new JSONArray();android.content.SharedPreferences prefs=c.getSharedPreferences("login",MODE_PRIVATE);
  String id=prefs.getString("profile","");if(!id.isEmpty())profiles.put(object("id",id,"label",prefs.getString("label","最近一次登录"),"account_id",prefs.getString("account","")));
  return out.put("profiles",profiles);
 }
 @Override public void onCreate(Bundle b){super.onCreate(b);current=this;getWindow().setStatusBarColor(0xff0b1020);getWindow().setNavigationBarColor(0xff0b1020);
  IO.execute(()->ImageExports.cleanup(getApplicationContext()));
  try{ensureCatalog(this);updates.start(false);
  }catch(Exception e){throw new IllegalStateException("CATALOG_INIT_FAILED");}
  FrameLayout content=new FrameLayout(this);content.setBackgroundColor(0xff0b1020);
  web=new WebView(this);web.setBackgroundColor(0xff0b1020);content.addView(web,new FrameLayout.LayoutParams(-1,-1));setContentView(content);
  // WebView does not inset its rendered document when its own padding changes.
  content.setOnApplyWindowInsetsListener((v,insets)->{v.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());return insets;});
  WebSettings settings=web.getSettings();settings.setJavaScriptEnabled(true);settings.setDomStorageEnabled(true);settings.setAllowFileAccess(false);settings.setAllowContentAccess(true);settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
  if((getApplicationInfo().flags&android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE)!=0)WebView.setWebContentsDebuggingEnabled(true);
  web.addJavascriptInterface(new Host(),"AndroidHost");
  web.setWebViewClient(new WebViewClient(){
   @Override public WebResourceResponse shouldInterceptRequest(WebView v,WebResourceRequest r){Uri u=r.getUrl();if(!"https".equals(u.getScheme())||!HOST.equals(u.getHost()))return denied();
    String path=u.getPath();if("/".equals(path))path="/index.html";if(path==null||path.equals("/bilibili-client.json")||path.contains("..")||!path.matches("/[A-Za-z0-9._-]+"))return denied();
    try{if("/catalog.json".equals(path))return new WebResourceResponse("application/json","UTF-8",200,"OK",Collections.singletonMap("Cache-Control","no-store"),new ByteArrayInputStream(updates.catalog().toString().getBytes(StandardCharsets.UTF_8)));
     String mime=path.endsWith(".js")?"text/javascript":path.endsWith(".css")?"text/css":path.endsWith(".json")?"application/json":path.endsWith(".svg")?"image/svg+xml":"text/html";
     return new WebResourceResponse(mime,"UTF-8",200,"OK",Collections.singletonMap("Cache-Control","no-store"),getAssets().open(path.substring(1)));
    }catch(IOException e){return denied();}}
   @Override public boolean shouldOverrideUrlLoading(WebView v,WebResourceRequest r){if(HOST.equals(r.getUrl().getHost())&&"https".equals(r.getUrl().getScheme()))return false;
    if(r.isForMainFrame()&&r.hasGesture()&&"https".equals(r.getUrl().getScheme())){try{startActivity(new Intent(Intent.ACTION_VIEW,r.getUrl()));}catch(Exception ignored){}}return true;}
  });
  web.setWebChromeClient(new WebChromeClient(){
   @Override public boolean onJsAlert(WebView v,String url,String msg,JsResult r){new AlertDialog.Builder(MainActivity.this).setMessage(msg).setPositiveButton("确定",(d,w)->r.confirm()).setOnCancelListener(d->r.cancel()).show();return true;}
   @Override public boolean onJsConfirm(WebView v,String url,String msg,JsResult r){new AlertDialog.Builder(MainActivity.this).setMessage(msg).setPositiveButton("确定",(d,w)->r.confirm()).setNegativeButton("取消",(d,w)->r.cancel()).setOnCancelListener(d->r.cancel()).show();return true;}
   @Override public boolean onJsPrompt(WebView v,String url,String msg,String value,JsPromptResult r){EditText input=new EditText(MainActivity.this);input.setText(value);new AlertDialog.Builder(MainActivity.this).setMessage(msg).setView(input).setPositiveButton("保存",(d,w)->r.confirm(input.getText().toString())).setNegativeButton("取消",(d,w)->r.cancel()).setOnCancelListener(d->r.cancel()).show();return true;}
   @Override public boolean onShowFileChooser(WebView v,ValueCallback<Uri[]> callback,FileChooserParams params){if(picker!=null)picker.onReceiveValue(null);picker=callback;
    try{Intent i=new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*");i.putExtra(Intent.EXTRA_MIME_TYPES,new String[]{"application/json","text/csv","text/comma-separated-values","text/plain"});startActivityForResult(i,101);}catch(Exception e){picker.onReceiveValue(null);picker=null;}return true;}
  });
  web.loadUrl(ORIGIN+"/index.html");
 }
 static WebResourceResponse denied(){return new WebResourceResponse("text/plain","UTF-8",404,"Not Found",Collections.emptyMap(),new ByteArrayInputStream(new byte[0]));}
 void reply(String id,int status,JSONObject data){runOnUiThread(()->{if(!isFinishing()&&web!=null)web.evaluateJavascript("window.AndroidBridge&&window.AndroidBridge.receive("+JSONObject.quote(id)+","+status+","+data.toString()+")",null);});}
 final class Host {
  @JavascriptInterface public void request(String id,String action,String raw){if(id==null||!id.matches("[0-9]{1,12}")||raw==null||raw.length()>50*1024*1024)return;
   IO.execute(()->{try{JSONObject body=new JSONObject(raw),result;
    switch(action){
     case "catalogStatus":result=updates.status();break;
     case "catalogCheck":updates.start(body.optBoolean("force",true));result=updates.status();break;
     case "catalogSource":updates.setSource(body.getString("url"));updates.start(true);result=updates.status();break;
     case "query":result=GachaClient.query(getApplicationContext(),body.getString("credential"));break;
     case "loginStatus":result=loginStatus(MainActivity.this);break;
     case "copyBiliDiagnostics":String diagnostic="星落手记 B 服脱敏诊断（无账号 UID、令牌）\n"+BiliDiagnostics.report(MainActivity.this).toString(2);runOnUiThread(()->{((ClipboardManager)getSystemService(CLIPBOARD_SERVICE)).setPrimaryClip(ClipData.newPlainText("B 服诊断",diagnostic));Toast.makeText(MainActivity.this,"脱敏诊断已复制",Toast.LENGTH_SHORT).show();});result=object("copied",true);break;
     case "copyTapDiagnostics":runOnUiThread(()->TapLoginController.copyDiagnostic(MainActivity.this));result=object("copied",true);break;
     case "loginAck":synchronized(MainActivity.class){if(session.equals(body.optString("id"))&&"done".equals(login.optString("phase"))){login.remove("raw");login.put("pending_result",false);}}result=object("saved",true);break;
     case "loginStart":synchronized(MainActivity.class){String p=login.optString("phase");if(Arrays.asList("opening","waiting","querying").contains(p))throw GachaClient.error("PORTAL_BUSY");
      if(!Arrays.asList("official","bilibili").contains(body.optString("channel","official")))throw GachaClient.error("INVALID_CHANNEL");
      remember=!"bilibili".equals(body.optString("channel"))&&body.optBoolean("remember",false);session=UUID.randomUUID().toString();phase("opening","正在打开官方授权页面…");}
      boolean bili="bilibili".equals(body.optString("channel")),tapApp=!bili&&"taptap_app".equals(body.optString("method")),reuse=remember&&!tapApp&&!body.optString("profile","").isEmpty();if(bili)BiliDiagnostics.begin(MainActivity.this);if(tapApp)TapAppDiagnostics.begin();runOnUiThread(()->startActivity(new Intent(MainActivity.this,bili?BiliAuthActivity.class:LoginActivity.class).putExtra("reuse",reuse).putExtra("remember",remember).putExtra("tap_app",tapApp)));result=loginStatus(MainActivity.this);break;
     case "loginCancel":synchronized(MainActivity.class){if("querying".equals(login.optString("phase")))throw GachaClient.error("PORTAL_QUERY_IN_PROGRESS");phase("cancelled","已取消登录。");}runOnUiThread(()->{if(LoginActivity.current!=null)LoginActivity.current.finish();if(BiliAuthActivity.current!=null)BiliAuthActivity.current.finish();});result=loginStatus(MainActivity.this);break;
     case "loginForget":getSharedPreferences("login",MODE_PRIVATE).edit().clear().apply();runOnUiThread(()->LoginActivity.clearRemoteSessions(()->{}));result=object("deleted",true);break;
     case "imageStage":result=object("image",ImageExports.stage(MainActivity.this,Base64.decode(body.getString("data"),Base64.DEFAULT)).getLastPathSegment());break;
     case "imageShare":{
      JSONArray list=body.getJSONArray("images");ArrayList<String> ids=new ArrayList<>();for(int i=0;i<list.length();i++)ids.add(list.getString(i));Intent intent=ImageExports.shareIntent(MainActivity.this,ids);
      runOnUiThread(()->{try{startActivity(Intent.createChooser(intent,"分享抽卡记录"));reply(id,200,object("opened",true));}catch(Exception e){reply(id,400,object("error","IMAGE_SHARE_FAILED"));}});return;
     }
     case "imageAlbum":{
      byte[] png=Base64.decode(body.getString("data"),Base64.DEFAULT);ImageExports.validate(png);String name=body.optString("name","星落抽卡记录.png");
      if(android.os.Build.VERSION.SDK_INT<29&&checkSelfPermission(android.Manifest.permission.WRITE_EXTERNAL_STORAGE)!=android.content.pm.PackageManager.PERMISSION_GRANTED){
       runOnUiThread(()->{if(albumRequest!=null){reply(id,409,object("error","SAVE_IN_PROGRESS"));return;}albumBytes=png;albumName=name;albumRequest=id;requestPermissions(new String[]{android.Manifest.permission.WRITE_EXTERNAL_STORAGE},103);});
      }else saveAlbum(id,png,name);return;
     }
     case "save":
      if(body.getString("data").length()>45*1024*1024)throw GachaClient.error("INPUT_TOO_LARGE");byte[] bytes=Base64.decode(body.getString("data"),Base64.DEFAULT);
      String name=body.optString("name","星落记录").replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]","_");String mime=body.optString("mime","application/octet-stream").split(";")[0];
      if(android.os.Build.VERSION.SDK_INT>=29){try{Exports.saveDownloads(getApplicationContext(),bytes,name,mime);reply(id,200,object("saved",true));runOnUiThread(()->Toast.makeText(MainActivity.this,"已保存到 下载/Starfall",Toast.LENGTH_LONG).show());}catch(Exception e){reply(id,400,object("error","FILE_SAVE_FAILED"));}return;}
      runOnUiThread(()->{if(exportBytes!=null){reply(id,409,object("error","SAVE_IN_PROGRESS"));return;}exportBytes=bytes;exportRequest=id;Intent i=new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType(mime).putExtra(Intent.EXTRA_TITLE,name);try{startActivityForResult(i,102);}catch(Exception e){exportBytes=null;exportRequest=null;reply(id,400,object("error","FILE_SAVE_FAILED"));}});return;
     default:throw GachaClient.error("NOT_FOUND");
    }
    reply(id,200,result);
   }catch(Exception e){String code=e.getMessage();if(code==null||!code.matches("[A-Z_0-9]{1,70}"))code="QUERY_FAILED";reply(id,400,object("error",code));}});
  }
 }
 void saveAlbum(String id,byte[] png,String name){IO.execute(()->{try{ImageExports.saveGallery(this,png,name);reply(id,200,object("saved",true,"location","相册/星落抽卡手记"));}catch(Exception e){reply(id,400,object("error","FILE_SAVE_FAILED"));}});}
 @Override public void onRequestPermissionsResult(int request,String[] permissions,int[] granted){super.onRequestPermissionsResult(request,permissions,granted);if(request==103){String id=albumRequest;byte[] png=albumBytes;String name=albumName;albumRequest=null;albumBytes=null;albumName=null;if(id==null)return;if(granted.length>0&&granted[0]==android.content.pm.PackageManager.PERMISSION_GRANTED)saveAlbum(id,png,name);else reply(id,400,object("error","STORAGE_PERMISSION_DENIED"));}}
 static void acceptCredential(String token){MainActivity a=current;if(a==null)return;phase("querying","已获取官网状态，正在查询抽卡记录…");
  IO.execute(()->{try{JSONObject result=GachaClient.query(a.getApplicationContext(),token);
   synchronized(MainActivity.class){login=object("phase","done","message","查询完成，正在保存到本机。","id",session,"raw",result,"pending_result",true);}TapAppDiagnostics.queryFinished(true);
   if(remember)a.getSharedPreferences("login",MODE_PRIVATE).edit().putString("profile",UUID.randomUUID().toString().replace("-","")).putString("label",result.getString("channel")+" · UID "+result.getString("uid")).putString("account",GachaClient.accountId(result.getString("uid"),result.getString("channel"))).apply();
  }catch(Exception e){String code=e.getMessage();if(code==null||!code.matches("[A-Z_0-9]{1,70}"))code="QUERY_FAILED";synchronized(MainActivity.class){login=object("phase","error","error",code,"id",session);}TapAppDiagnostics.queryFinished(false);}});
 }
 static void acceptBiliGrant(Context context,String grant){
  synchronized(MainActivity.class){if(session.isEmpty())session=UUID.randomUUID().toString();}
  phase("querying","已获 B 站授权，正在识别账号并查询全部已知卡池…");
  IO.execute(()->{GachaClient.Credential credential=null;try{
   ensureCatalog(context);credential=BiliAuthClient.exchange(context,grant);JSONObject result=GachaClient.query(context,credential);
   BiliDiagnostics.finish(context,result.optBoolean("partial")?"HISTORY_PARTIAL":"HISTORY_ALL_ACCEPTED");
   synchronized(MainActivity.class){login=object("phase","done","message","B 服查询完成，正在保存到本机。","id",session,"raw",result,"pending_result",true);}
  }catch(Exception e){failLogin(e.getMessage());BiliDiagnostics.put("exception_class",e.getClass().getSimpleName());BiliDiagnostics.finish(context,login.optString("error","QUERY_FAILED"));}finally{if(credential!=null){credential.token=null;credential.guid=null;}}});
 }
 @Override protected void onActivityResult(int request,int result,Intent data){super.onActivityResult(request,result,data);
  if(request==101&&picker!=null){picker.onReceiveValue(result==RESULT_OK&&data!=null&&data.getData()!=null?new Uri[]{data.getData()}:null);picker=null;}
  if(request==102){byte[] bytes=exportBytes;String id=exportRequest;exportBytes=null;exportRequest=null;if(id==null)return;
   if(result==RESULT_OK&&data!=null&&data.getData()!=null&&bytes!=null){Uri target=data.getData();IO.execute(()->{try(OutputStream out=getContentResolver().openOutputStream(target)){if(out==null)throw new IOException();out.write(bytes);reply(id,200,object("saved",true));runOnUiThread(()->Toast.makeText(this,"文件已保存",Toast.LENGTH_SHORT).show());}catch(Exception e){reply(id,400,object("error","FILE_SAVE_FAILED"));}});}
   else reply(id,200,object("cancelled",true));}
 }
 @Override public void onBackPressed(){web.evaluateJavascript("(()=>{if(typeof window.mobileBack==='function')return window.mobileBack()===true;const d=document.querySelector('dialog[open]');if(d){if(d.dataset.busy!=='true')d.close();return true;}return false;})()",value->{if(!"true".equals(value))super.onBackPressed();});}
 @Override protected void onDestroy(){if(current==this)current=null;albumBytes=null;albumRequest=null;albumName=null;if(web!=null){web.removeJavascriptInterface("AndroidHost");web.destroy();web=null;}super.onDestroy();}
}
