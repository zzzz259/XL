package cn.starfall.gacha.local;

import android.content.Context;
import android.util.Base64;
import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/** SDK renewal for explicitly imported matching SDK state. Never reconstructs missing device identities. */
final class CredentialRenewal {
 interface Transport {JSONObject auth(String token,byte[] body)throws Exception;void verify(GachaClient.Credential c)throws Exception;}
 static final Transport NETWORK=new Transport(){
  public JSONObject auth(String token,byte[] body)throws Exception{
   HttpURLConnection conn=(HttpURLConnection)new URL("https://p4cn.17995api.net/api/auth").openConnection(Proxy.NO_PROXY);
   try{conn.setConnectTimeout(15000);conn.setReadTimeout(15000);conn.setInstanceFollowRedirects(false);conn.setRequestMethod("POST");conn.setDoOutput(true);conn.setRequestProperty("Authorization",token);conn.setRequestProperty("Content-Type","application/x-www-form-urlencoded");conn.setRequestProperty("Accept","application/json");conn.setFixedLengthStreamingMode(body.length);try(OutputStream out=conn.getOutputStream()){out.write(body);}if(conn.getResponseCode()!=200)throw GachaClient.error("RENEWAL_NETWORK_ERROR");try(InputStream in=conn.getInputStream()){return new JSONObject(new String(GachaClient.bounded(in,131072),StandardCharsets.UTF_8));}}finally{conn.disconnect();}
  }
  public void verify(GachaClient.Credential c)throws Exception{try{GachaClient.authenticate(c,Proxy.NO_PROXY);}catch(IOException e){if(!"SERVER_MAINTENANCE".equals(e.getMessage()))throw e;}}
 };
 static void validateProfile(JSONObject p,GachaClient.Credential c)throws Exception{
  try{
   JSONObject q=p.getJSONObject("params");
   if(c.appId.isEmpty()||c.refreshAfter<=c.issuedAt||c.expiresAt<=c.refreshAfter||p.getString("appkey").isEmpty()||p.getString("signing_input").isEmpty())throw new IOException();
   if(!q.getString("appid").equals(c.appId)||!q.getString("channel").equals(c.provider))throw GachaClient.error("ACCOUNT_ID_MISMATCH");
   for(String k:new String[]{"appid","l","channel","guid","tapdbid","bundleid","deviceid","oaid","fcmToken","hashkey","oneToken","new","term"})if(!(q.get(k) instanceof String))throw new IOException();
   if(!"cn.haoplay.game.and.elpis".equals(q.getString("bundleid")))throw new IOException();
   for(Iterator<String> it=q.keys();it.hasNext();)if(!(q.get(it.next()) instanceof String))throw new IOException();
  }catch(Exception e){throw GachaClient.error("ACCOUNT_ID_MISMATCH".equals(e.getMessage())?"ACCOUNT_ID_MISMATCH":"INVALID_RENEWAL_PROFILE");}
 }
 static String encode(String s)throws Exception{return URLEncoder.encode(s,"UTF-8").replace("+","%20").replace("%7E","~").replace("%21","!").replace("%27","'").replace("%28","(").replace("%29",")");}
 static String md5(String s)throws Exception{byte[] bytes=MessageDigest.getInstance("MD5").digest(s.getBytes(StandardCharsets.UTF_8));StringBuilder out=new StringBuilder();for(byte b:bytes)out.append(String.format(Locale.ROOT,"%02x",b&255));return out.toString();}
 static byte[] form(JSONObject p,long now)throws Exception{
  JSONObject q=p.getJSONObject("params");StringBuilder raw=new StringBuilder();
  for(Iterator<String> it=q.keys();it.hasNext();){String k=it.next();if(k.equals("appkey")||k.equals("config")||k.toLowerCase(Locale.ROOT).startsWith("private"))continue;if(raw.length()>0)raw.append('&');raw.append(encode(k)).append('=').append(encode(q.getString(k)));}
  if(raw.length()>0)raw.append('&');raw.append("time=").append(now).append("&sys=android&tz=-480");
  String secret=md5(md5(p.getString("appkey"))+p.getString("signing_input"));Mac mac=Mac.getInstance("HmacSHA1");mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8),"HmacSHA1"));String sig=Base64.encodeToString(mac.doFinal(raw.toString().getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP);
  return (raw+"&sig="+encode(sig)).getBytes(StandardCharsets.UTF_8);
 }
 static String renew(Context context,String id)throws Exception{return renew(context,id,NETWORK,System.currentTimeMillis()/1000);}
 static String renew(Context context,String id,Transport transport,long now)throws Exception{
  synchronized(CredentialStore.class){
   JSONObject value=CredentialStore.load(context,id);if(value==null||!value.optBoolean("auto_renew")||!value.has("profile"))return "DISABLED";
   GachaClient.Credential old=CredentialStore.decode(value);validateProfile(value.getJSONObject("profile"),old);
   if(old.expiresAt<=now)return "CREDENTIAL_EXPIRED";
   if(value.optBoolean("renewal_blocked"))return "AUTH_REQUIRED";
   if(now<old.refreshAfter+60)return "NOT_DUE";
   if(now-value.optLong("last_attempt")<900)return "COOLDOWN";
   value.put("last_attempt",now);CredentialStore.write(context,id,value);
   String outcome="RENEWAL_FAILED";
   try{
    JSONObject response=transport.auth(old.token,form(value.getJSONObject("profile"),now));
    if(response.optInt("code",-1)==401){value.put("renewal_blocked",true);outcome="AUTH_REQUIRED";}
    else if(response.optInt("code",-1)==0){
     GachaClient.Credential next=GachaClient.credential(response.getString("jwt"),false);
     if(!next.uid.equals(old.uid)||!next.provider.equals(old.provider)||!next.appId.equals(old.appId)||next.issuedAt<=old.issuedAt||next.issuedAt>now+300||next.refreshAfter<=now||next.expiresAt<=next.refreshAfter)throw GachaClient.error("RENEWAL_CANDIDATE_REJECTED");
     transport.verify(next);value.put("credential",CredentialStore.encoded(next));value.put("renewal_blocked",false);outcome="UPDATED";
    }
   }catch(Exception e){outcome="RENEWAL_NETWORK_ERROR".equals(e.getMessage())?"RENEWAL_NETWORK_ERROR":"RENEWAL_FAILED";}
   value.put("last_error",outcome.equals("UPDATED")?"":outcome);CredentialStore.write(context,id,value);return outcome;
  }
 }
}
