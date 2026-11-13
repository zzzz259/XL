package cn.starfall.gacha.local;

import android.content.Context;
import android.util.AtomicFile;
import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.time.Instant;

/** Optional public text announcements, independent of catalog versions and all account data. */
final class AppNews {
 static final int LIMIT=16384;
 interface Transport {byte[] get(String url)throws Exception;}
 static JSONObject validate(JSONObject input)throws Exception{
  try{
   if(!(input.opt("schema_version") instanceof Integer)||!(input.opt("revision") instanceof String))throw new IOException();
   if(!"starfall-gacha-app-news".equals(input.getString("format"))||input.getInt("schema_version")!=1||!input.getString("revision").matches("[1-9][0-9]{0,19}"))throw new IOException();
   JSONArray rows=input.getJSONArray("entries"),entries=new JSONArray();if(rows.length()>10)throw new IOException();java.util.Set<String> ids=new java.util.HashSet<>();
   for(int i=0;i<rows.length();i++){
    JSONObject row=rows.getJSONObject(i);String id=row.getString("id"),title=row.getString("title"),body=row.getString("body"),version=row.getString("version_name"),published=row.getString("published_at");
    for(String key:new String[]{"id","title","body","version_name","published_at"})if(!(row.opt(key) instanceof String))throw new IOException();
    if(!(row.opt("version_code") instanceof Integer)&&!(row.opt("version_code") instanceof Long))throw new IOException();
    if(!id.matches("[A-Za-z0-9._-]{1,64}")||!ids.add(id)||title.trim().isEmpty()||body.trim().isEmpty()||title.codePointCount(0,title.length())>120||title.codePointCount(0,title.length())+body.codePointCount(0,body.length())>600||!version.matches("[A-Za-z0-9.+_-]{1,32}")||row.getLong("version_code")<1||row.getLong("version_code")>Integer.MAX_VALUE||!published.endsWith("Z"))throw new IOException();
    Instant.parse(published);
    for(char ch:(title+body).toCharArray())if(Character.isISOControl(ch)&&ch!='\n'&&ch!='\t')throw new IOException();
    entries.put(MainActivity.object("id",id,"title",title,"body",body,"version_name",version,"version_code",row.getLong("version_code"),"published_at",published));
   }
   return MainActivity.object("format","starfall-gacha-app-news","schema_version",1,"revision",input.getString("revision"),"entries",entries);
  }catch(Exception e){throw GachaClient.error("NEWS_INVALID");}
 }
 static synchronized JSONObject get(Context context,String source,boolean force)throws Exception{
  return get(context,source,force,url->{
   HttpURLConnection connection=(HttpURLConnection)new URL(url).openConnection(Proxy.NO_PROXY);
   try{connection.setConnectTimeout(10000);connection.setReadTimeout(12000);connection.setInstanceFollowRedirects(false);connection.setUseCaches(false);connection.setRequestProperty("Accept","application/json");connection.setRequestProperty("Accept-Encoding","identity");if(connection.getResponseCode()!=200)throw new IOException();try(InputStream in=connection.getInputStream()){return GachaClient.bounded(in,LIMIT);}}finally{connection.disconnect();}
  });
 }
 static synchronized JSONObject get(Context context,String source,boolean force,Transport transport)throws Exception{
  if(source.isEmpty())return MainActivity.object("phase","unconfigured","entries",new JSONArray());
  CatalogUpdates.validateSource(source);String url=new URI(source).resolve("app-news.json").toString();
  AtomicFile cache=new AtomicFile(new File(context.getFilesDir(),"app-news-"+CatalogUpdates.hash(source.getBytes(StandardCharsets.UTF_8))+".json"));
  JSONObject previous=null;long checked=0;
  try(InputStream in=cache.openRead()){JSONObject saved=new JSONObject(new String(GachaClient.bounded(in,LIMIT+1024),StandardCharsets.UTF_8));previous=validate(saved.getJSONObject("news"));checked=saved.getLong("checked_at");}catch(Exception ignored){}
  long now=System.currentTimeMillis();if(previous!=null&&!force&&now>=checked&&now-checked<6*3600*1000L)return previous.put("phase","cached");
  try{
   byte[] raw=transport.get(url);if(raw.length>LIMIT)throw new IOException();JSONObject news=validate(new JSONObject(new String(raw,StandardCharsets.UTF_8)));
   if(previous!=null){String old=previous.getString("revision"),next=news.getString("revision");int comparison=next.length()==old.length()?next.compareTo(old):Integer.compare(next.length(),old.length());if(comparison<0||(comparison==0&&!previous.toString().equals(news.toString())))throw new IOException();}
   FileOutputStream out=null;try{out=cache.startWrite();out.write(MainActivity.object("checked_at",now,"news",news).toString().getBytes(StandardCharsets.UTF_8));cache.finishWrite(out);out=null;}finally{if(out!=null)cache.failWrite(out);}
   return news.put("phase","current");
  }catch(Exception e){return previous==null?MainActivity.object("phase","unavailable","entries",new JSONArray()):previous.put("phase","cached");}
 }
}
