package cn.starfall.gacha.local;

import android.content.Context;
import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Exchanges only a fresh grant returned by the official Bilibili consent activity. */
final class BiliAuthClient {
 static final String DOMAIN="line3-sdk-center-login-sh.biligame.net";
 static JSONObject config(Context context)throws Exception{
  try(InputStream in=context.getAssets().open("bilibili-client.json")){
   JSONObject c=new JSONObject(new String(GachaClient.bounded(in,8192),StandardCharsets.UTF_8));
   for(String k:new String[]{"merchant_id","app_id","server_id"})if(!c.getString(k).matches("[0-9]{1,20}"))throw new IOException();
   if(!c.getString("client_key").matches("[A-Za-z0-9_-]{8,128}"))throw new IOException();return c;
  }catch(Exception e){throw GachaClient.error("BILI_CONFIG_MISSING");}
 }
 static GachaClient.Credential exchange(Context context,String grant)throws Exception{
  if(grant==null||grant.isEmpty()||grant.length()>65536)throw GachaClient.error("BILI_AUTH_RESULT_MISSING");
  JSONObject cfg=config(context);Map<String,String> p=new TreeMap<>();
  BiliDiagnostics.put("stage","sdk_exchange");BiliDiagnostics.put("official_grant_received",true);
  p.put("access_key",grant);p.put("key_type","0");p.put("version","3");p.put("acct_exchange_type","10");p.put("timestamp",Long.toString(System.currentTimeMillis()));p.put("domain",DOMAIN);
  for(String k:new String[]{"merchant_id","app_id","server_id"})p.put(k,cfg.getString(k));p.put("game_id",cfg.getString("app_id"));
  p.put("platform","3");p.put("platform_type","3");p.put("sdk_type","1");p.put("sdk_ver","5.9.0");p.put("sdk_log_type","1");p.put("sdk_name","CGameSdk");p.put("current_env","0");
  byte[] bytes=BiliProtocol.form(p,cfg.getString("client_key")).getBytes(StandardCharsets.UTF_8);p.clear();
  HttpURLConnection conn=(HttpURLConnection)new URL("https://"+DOMAIN+"/api/external/token.exchange/v3").openConnection(Proxy.NO_PROXY);
  try{
   conn.setInstanceFollowRedirects(false);conn.setConnectTimeout(12000);conn.setReadTimeout(12000);conn.setRequestMethod("POST");conn.setDoOutput(true);
   conn.setRequestProperty("Content-Type","application/x-www-form-urlencoded");conn.setRequestProperty("User-Agent","Mozilla/5.0 BSGameSDK");conn.setRequestProperty("Accept","application/json");conn.setRequestProperty("Cookie","");
   conn.setFixedLengthStreamingMode(bytes.length);try(OutputStream out=conn.getOutputStream()){out.write(bytes);}Arrays.fill(bytes,(byte)0);
   int http=conn.getResponseCode();BiliDiagnostics.put("exchange_http_status",http);if(http!=200)throw GachaClient.error("BILI_EXCHANGE_NETWORK_ERROR");
   JSONObject result;try(InputStream in=conn.getInputStream()){result=new JSONObject(new String(GachaClient.bounded(in,65536),StandardCharsets.UTF_8));}
   BiliDiagnostics.put("exchange_code",result.optInt("code",Integer.MIN_VALUE));if(!"0".equals(String.valueOf(result.opt("code"))))throw GachaClient.error("BILI_EXCHANGE_REJECTED");
   String uid=BiliProtocol.uid(result.opt("uid"));Object token=result.opt("access_key");
   if(uid.isEmpty()||!(token instanceof String)||((String)token).isEmpty()||((String)token).length()>65536)throw GachaClient.error("BILI_EXCHANGE_INVALID_RESPONSE");
   BiliDiagnostics.put("automatic_uid_obtained",true);BiliDiagnostics.put("sdk_token_obtained",true);return new GachaClient.Credential((String)token,uid,"bilibili",UUID.randomUUID().toString());
  }catch(SocketTimeoutException e){throw GachaClient.error("BILI_EXCHANGE_NETWORK_ERROR");}
  finally{Arrays.fill(bytes,(byte)0);conn.disconnect();}
 }
}
