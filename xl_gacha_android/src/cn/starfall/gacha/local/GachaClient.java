package cn.starfall.gacha.local;

import android.content.Context;
import android.util.Base64;
import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

final class GachaClient {
 static class Credential {String token,uid,provider,guid;Credential(String t,String u,String p,String g){token=t;uid=u;provider=p;guid=g;}}
 static IOException error(String code){return new IOException(code);}
 static Credential credential(String raw)throws Exception{
  if(raw==null||raw.length()>65536)throw error("INVALID_CREDENTIAL_INPUT");String token=raw.trim();
  if(token.startsWith("{")){JSONObject j=new JSONObject(token);Object v=j.has("HP_2070021")?j.get("HP_2070021"):j.has("HP_2070023")?j.get("HP_2070023"):j;if(v instanceof String&&((String)v).trim().startsWith("{"))v=new JSONObject((String)v);token=v instanceof JSONObject?((JSONObject)v).getString("jwt"):String.valueOf(v);}
  String[] parts=token.split("\\.");if(parts.length!=3)throw error("INVALID_CREDENTIAL_INPUT");
  JSONObject c=new JSONObject(new String(Base64.decode(parts[1],Base64.URL_SAFE|Base64.NO_WRAP|Base64.NO_PADDING),StandardCharsets.UTF_8));
  String app=c.optString("appid"),uid=c.optString("uid"),provider=c.optString("provider"),guid=c.getString("guid");
  if(!app.equals("2070021")&&!app.equals("2070023"))throw error("WRONG_APP");
  if(c.optDouble("exp",0)<=System.currentTimeMillis()/1000.0)throw error("CREDENTIAL_EXPIRED");
  if(uid.isEmpty()||!provider.matches("[A-Za-z0-9_-]{1,64}"))throw error("INVALID_CREDENTIAL_INPUT");
  guid=UUID.fromString(guid).toString();return new Credential(token,uid,provider,guid);
 }
 static String accountId(String uid,String provider)throws Exception{byte[] hash=MessageDigest.getInstance("SHA-256").digest((provider+":"+uid).getBytes(StandardCharsets.UTF_8));StringBuilder out=new StringBuilder("account-");for(int i=0;i<16;i++)out.append(String.format(Locale.ROOT,"%02x",hash[i]&255));return out.toString();}
 static byte[] bounded(InputStream in,int limit)throws Exception{ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] b=new byte[8192];int n;while((n=in.read(b))!=-1){if(out.size()+n>limit)throw error("RESPONSE_TOO_LARGE");out.write(b,0,n);}return out.toByteArray();}
 static Proxy systemProxy(){return Proxy.NO_PROXY;}
 static String authenticate(Credential c,Proxy proxy)throws Exception{
  HttpURLConnection conn=(HttpURLConnection)new URL("https://elpis-accverify.haoplay.com.cn/v1/account/login").openConnection(proxy);
  conn.setConnectTimeout(15000);conn.setReadTimeout(15000);conn.setInstanceFollowRedirects(false);conn.setRequestMethod("POST");conn.setDoOutput(true);conn.setRequestProperty("Content-Type","application/json");
  JSONObject body=new JSONObject().put("openId",c.uid).put("token",c.token).put("platform",1).put("channel",c.provider).put("buildVersion","").put("authRemote",true);
  try{
   byte[] bytes=body.toString().getBytes(StandardCharsets.UTF_8);conn.setFixedLengthStreamingMode(bytes.length);try(OutputStream out=conn.getOutputStream()){out.write(bytes);}
   if(conn.getResponseCode()!=200)throw error("AUTH_NETWORK_ERROR");
   JSONObject auth;try(InputStream in=conn.getInputStream()){auth=new JSONObject(new String(bounded(in,65536),StandardCharsets.UTF_8));}
   if(auth.optInt("retCode",-1)!=0)throw error("AUTH_REJECTED");JSONObject data=auth.getJSONObject("data");if(data.optInt("maintainState",0)==1)throw error("SERVER_MAINTENANCE");
   JSONArray addresses=data.optJSONArray("addresses");if(addresses!=null)for(int i=0;i<addresses.length();i++){String a=addresses.optString(i);if(a.matches("[A-Za-z0-9.-]+\\.haoplay\\.com\\.cn:8801"))return a.substring(0,a.length()-5);}
   throw error("NO_ALLOWED_GATEWAY");
  }finally{conn.disconnect();}
 }
 static Socket gateway(String host,Proxy proxy)throws Exception{
  if(!host.matches("[A-Za-z0-9.-]+\\.haoplay\\.com\\.cn"))throw error("GATEWAY_NOT_ALLOWED");
  Socket sock=new Socket();try{
   if(proxy.type()==Proxy.Type.HTTP){sock.connect(proxy.address(),12000);sock.setSoTimeout(12000);sock.getOutputStream().write(("CONNECT "+host+":8801 HTTP/1.1\r\nHost: "+host+":8801\r\n\r\n").getBytes(StandardCharsets.US_ASCII));ByteArrayOutputStream h=new ByteArrayOutputStream();String s="";while(!s.endsWith("\r\n\r\n")){if(h.size()>16384)throw error("PROXY_CONNECT_REJECTED");int b=sock.getInputStream().read();if(b<0)throw error("PROXY_CONNECT_REJECTED");h.write(b);s=h.toString("US-ASCII");}if(!s.split("\r\n")[0].matches("HTTP/1\\.[01] 200.*"))throw error("PROXY_CONNECT_REJECTED");}
   else sock.connect(new InetSocketAddress(host,8801),12000);
   sock.setSoTimeout(20000);return sock;
  }catch(Exception e){sock.close();throw e;}
 }
 static JSONArray decode(byte[] body)throws Exception{
  JSONArray records=new JSONArray();for(Wire.Field f:Wire.fields(body))if(f.n==1&&f.w==2){List<Wire.Field> r=Wire.fields(f.bytes);JSONArray drops=new JSONArray();for(Wire.Field d:r)if(d.n==2&&d.w==2){List<Wire.Field> v=Wire.fields(d.bytes);drops.put(new JSONObject().put("item_id",Wire.number(v,2)).put("count",Wire.number(v,3)));}
   records.put(new JSONObject().put("pool_id",Wire.number(r,1)).put("timestamp",Wire.number(r,3)).put("index",Wire.number(r,5)).put("record_id",Wire.number(r,6)).put("drops",drops));}return records;
 }
 static JSONObject query(Context context,String raw)throws Exception{
  Credential c=credential(raw);Proxy proxy=systemProxy();String host=authenticate(c,proxy);
  JSONObject catalog=MainActivity.updates.catalog();
  JSONArray selectors=catalog.getJSONArray("selectors"),records=new JSONArray(),queries=new JSONArray();boolean accepted=false,partial=false;
  try(Socket socket=gateway(host,proxy)){
   byte[] enter=Wire.join(Wire.blob(1,c.token.getBytes(StandardCharsets.UTF_8)),Wire.blob(3,c.guid.getBytes(StandardCharsets.UTF_8)),Wire.blob(4,"haoplay_cn".getBytes(StandardCharsets.UTF_8)),Wire.integer(5,1),Wire.integer(6,1),Wire.blob(7,c.uid.getBytes(StandardCharsets.UTF_8)));
   socket.getOutputStream().write(Wire.frame(1002,enter,1));Wire.Reply reply=Wire.response(socket,101002);
   if(reply.code!=0)throw error("ENTER_REJECTED");if(Wire.number(Wire.fields(reply.body),1)!=1)throw error("ACTOR_NOT_FOUND");
   for(int i=0;i<selectors.length();i++){
    int selector=selectors.getInt(i);socket.getOutputStream().write(Wire.frame(10504,Wire.integer(1,selector),i+2));reply=Wire.response(socket,110504);
    JSONArray batch=reply.code==0?decode(reply.body):new JSONArray();accepted|=reply.code==0;partial|=reply.code!=0;
    for(int k=0;k<batch.length();k++)records.put(batch.get(k));queries.put(new JSONObject().put("selector",selector).put("ret_code",reply.code).put("record_count",batch.length()));
    if(i+1<selectors.length())Thread.sleep(1000);
   }
  }
  if(!accepted)throw error("ALL_HISTORY_QUERIES_REJECTED");return new JSONObject().put("uid",c.uid).put("channel",c.provider).put("records",records).put("queries",queries).put("partial",partial).put("catalog_snapshot",catalog);
 }
}
