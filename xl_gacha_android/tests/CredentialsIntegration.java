package cn.starfall.gacha.local;

import android.app.*;
import android.content.*;
import android.os.Bundle;
import android.util.Base64;
import org.json.*;
import java.nio.charset.StandardCharsets;
import java.lang.reflect.*;

/** Synthetic native secrets only; never contacts account services. */
public final class CredentialsIntegration extends Instrumentation {
 int checks;Class<?> store;
 void ok(boolean value,String message){if(!value)throw new AssertionError(message);checks++;}
 Object invoke(String method,Class<?>[] types,Object...args)throws Exception{
  try{return store.getDeclaredMethod(method,types).invoke(null,args);}catch(InvocationTargetException e){throw (Exception)e.getCause();}
 }
 Object account(String method,String id)throws Exception{return invoke(method,new Class[]{Context.class,String.class},getTargetContext(),id);}
 JSONObject status(String id)throws Exception{return (JSONObject)account("status",id);}
 static String token(long now,long exp,String uid)throws Exception{
  JSONObject c=MainActivity.object("appid","2070021","uid",uid,"provider","sms","guid","00000000-0000-4000-8000-000000000001","iat",now,"vfp",now+10,"exp",exp);
  return "e30."+Base64.encodeToString(c.toString().getBytes(StandardCharsets.UTF_8),Base64.URL_SAFE|Base64.NO_WRAP|Base64.NO_PADDING)+".cWE";
 }
 @Override public void onCreate(Bundle b){super.onCreate(b);start();}
 @Override public void onStart(){Bundle result=new Bundle();String id="";try{
  try{store=Class.forName("cn.starfall.gacha.local.CredentialStore");}catch(ClassNotFoundException e){throw new AssertionError("missing encrypted credential store");}
  Context context=getTargetContext();long now=System.currentTimeMillis()/1000;String jwt=token(now,now+3600,"credential-qa");
  GachaClient.Credential c=GachaClient.credential(jwt);id=GachaClient.accountId(c.uid,c.provider);account("delete",id);
  ok(!status(id).getBoolean("available"),"no credentials for an uninitialized account");
  ok(!status("local-00000000000000000000000000000000").getBoolean("available"),"anonymous imported archive has no credential");
  invoke("save",new Class[]{Context.class,GachaClient.Credential.class},context,c);
  ok(status(id).getBoolean("available"),"saved credential is available");
  ok(!status(id).getBoolean("auto_renew"),"renewal defaults off");
  GachaClient.Credential read=(GachaClient.Credential)account("read",id);
  ok(jwt.equals(read.token),"Keystore encrypted credential reads back unchanged");
  java.io.File file=new java.io.File(context.getNoBackupFilesDir(),"credentials/"+id+".session");
  byte[] bytes;try(java.io.InputStream in=new java.io.FileInputStream(file)){bytes=GachaClient.bounded(in,200000);}
  ok(!new String(bytes,StandardCharsets.UTF_8).contains(jwt),"disk never stores JWT as plaintext");
  ok(!status(id).toString().contains(jwt),"status never returns JWT");
  String other=GachaClient.accountId(c.uid,"bilibili");ok(!status(other).getBoolean("available"),"channel isolation");
  try{invoke("configure",new Class[]{Context.class,String.class,boolean.class},context,id,true);throw new AssertionError("enabled without SDK context");}catch(Exception e){ok("RENEWAL_PROFILE_REQUIRED".equals(e.getMessage()),"missing SDK state cannot enable renewal");}
  JSONObject params=MainActivity.object("appid","2070021","channel","sms","guid",c.guid,"l","cn","tapdbid","qa","bundleid","cn.haoplay.game.and.elpis","deviceid","","oaid","","fcmToken","","hashkey","qa","oneToken","qa","new","0","term","qa");
  JSONObject profile=MainActivity.object("appkey","synthetic-key","signing_input","synthetic-input","params",params);
  JSONObject state=MainActivity.object("version",1,"profile",profile,"credential",MainActivity.object("open_id",c.uid,"channel",c.provider,"token",jwt));
  invoke("importState",new Class[]{Context.class,String.class,String.class},context,id,state.toString());
  ok(status(id).getBoolean("renewal_ready")&&!status(id).getBoolean("auto_renew"),"imported profile stays disabled");
  invoke("configure",new Class[]{Context.class,String.class,boolean.class},context,id,true);
  ok(status(id).getBoolean("auto_renew"),"explicit opt in enables renewal");
  invoke("configure",new Class[]{Context.class,String.class,boolean.class},context,id,false);
  ok(!status(id).getBoolean("auto_renew"),"opt out disables renewal");
  String signed=new String(CredentialRenewal.form(profile,1000),StandardCharsets.UTF_8);
  ok(signed.endsWith("&sig=lngT%2BOl7JAsLh4JE6st5iNUSCSI%3D"),"SDK signing matches the independently verified Python oracle");
  invoke("configure",new Class[]{Context.class,String.class,boolean.class},context,id,true);
  final int[] calls={0,0};final long clock=now;final String owner=c.uid;
  CredentialRenewal.Transport fake=new CredentialRenewal.Transport(){
   public JSONObject auth(String old,byte[] body)throws Exception{calls[0]++;long issued=clock+(calls[0]==1?100:1100);return MainActivity.object("code",0,"jwt",token(issued,clock+10000,owner));}
   public void verify(GachaClient.Credential candidate){calls[1]++;}
  };
  ok("NOT_DUE".equals(CredentialRenewal.renew(context,id,fake,now+69))&&calls[0]==0,"no renewal before vfp plus 60 seconds");
  ok("UPDATED".equals(CredentialRenewal.renew(context,id,fake,now+100))&&calls[1]==1,"candidate verified before saving");
  ok("COOLDOWN".equals(CredentialRenewal.renew(context,id,fake,now+200))&&calls[0]==1,"persistent retry cooldown");
  ok("UPDATED".equals(CredentialRenewal.renew(context,id,fake,now+1100))&&calls[1]==2,"second simulated natural cycle uses the latest credential");
  String latest=((GachaClient.Credential)account("read",id)).token;
  CredentialRenewal.Transport rejected=new CredentialRenewal.Transport(){public JSONObject auth(String old,byte[] body)throws Exception{return MainActivity.object("code",0,"jwt",token(clock+2100,clock+15000,owner));}public void verify(GachaClient.Credential candidate)throws Exception{throw new java.io.IOException("AUTH_REJECTED");}};
  ok("RENEWAL_FAILED".equals(CredentialRenewal.renew(context,id,rejected,now+2100)),"game rejects candidate");
  ok(latest.equals(((GachaClient.Credential)account("read",id)).token),"rejected candidate leaves latest accepted token intact");
  invoke("configure",new Class[]{Context.class,String.class,boolean.class},context,id,false);
  JSONObject wrong=new JSONObject(state.toString());wrong.getJSONObject("credential").put("open_id","other");
  try{invoke("importState",new Class[]{Context.class,String.class,String.class},context,id,wrong.toString());throw new AssertionError("wrong account accepted");}catch(Exception e){ok("ACCOUNT_ID_MISMATCH".equals(e.getMessage()),"reject wrong imported account");}
  ok(latest.equals(((GachaClient.Credential)account("read",id)).token),"failed import retains working token");
  GachaClient.Credential expired=GachaClient.credential(token(now+5000,now-1,c.uid),false);
  invoke("save",new Class[]{Context.class,GachaClient.Credential.class},context,expired);
  try{account("read",id);throw new AssertionError("expired credential returned");}catch(Exception e){ok("CREDENTIAL_EXPIRED".equals(e.getMessage()),"expired saved credential requires login");}
  account("delete",id);ok(!status(id).getBoolean("available"),"delete clears only this credential");
  Class<?> news;
  try{news=Class.forName("cn.starfall.gacha.local.AppNews");}catch(ClassNotFoundException e){throw new AssertionError("missing public update news reader");}
  JSONObject announcement=MainActivity.object("format","starfall-gacha-app-news","schema_version",1,"revision","1","entries",new JSONArray().put(MainActivity.object("id","v041","version_name","0.4.1","version_code",22,"title","更新内容","body","合成公告，不含个人信息。","published_at","2026-11-03T00:00:00Z")));
  JSONObject parsed=(JSONObject)news.getDeclaredMethod("validate",JSONObject.class).invoke(null,announcement);
  ok(parsed.getJSONArray("entries").length()==1,"valid public update news accepted");
  announcement.getJSONArray("entries").getJSONObject(0).put("body",new String(new char[601]).replace('\0','长'));
  try{news.getDeclaredMethod("validate",JSONObject.class).invoke(null,announcement);throw new AssertionError("oversize news accepted");}catch(InvocationTargetException e){ok("NEWS_INVALID".equals(e.getCause().getMessage()),"oversize announcement rejected");}
  announcement.getJSONArray("entries").getJSONObject(0).put("body","合成公告。");
  final JSONObject fixture=announcement;final int[] downloads={0};String source="https://news.example/gacha-config/current.json";
  java.io.File newsCache=new java.io.File(context.getFilesDir(),"app-news-"+CatalogUpdates.hash(source.getBytes(StandardCharsets.UTF_8))+".json");new android.util.AtomicFile(newsCache).delete();
  try{
   AppNews.Transport transport=url->{if(!url.equals("https://news.example/gacha-config/app-news.json"))throw new AssertionError("news URL");downloads[0]++;return fixture.toString().getBytes(StandardCharsets.UTF_8);};
   ok(AppNews.get(context,source,true,transport).getString("phase").equals("current"),"public news commits independently of catalog");
   ok(AppNews.get(context,source,false,transport).getString("phase").equals("cached")&&downloads[0]==1,"successful news cache skips repeated requests");
   fixture.getJSONArray("entries").getJSONObject(0).put("body","同版本不同内容");
   ok(AppNews.get(context,source,true,transport).getJSONArray("entries").getJSONObject(0).getString("body").equals("合成公告。"),"same revision changed content preserves cache");
   ok(AppNews.get(context,source,true,url->{throw new java.io.IOException();}).getString("phase").equals("cached"),"network failure preserves cached news");
  }finally{new android.util.AtomicFile(newsCache).delete();}
  result.putString("result","PASS "+checks+" credential storage checks");finish(Activity.RESULT_OK,result);
 }catch(Throwable e){result.putString("result","FAIL "+e);finish(Activity.RESULT_CANCELED,result);}finally{try{if(store!=null&&!id.isEmpty())account("delete",id);}catch(Exception ignored){}}}
}
