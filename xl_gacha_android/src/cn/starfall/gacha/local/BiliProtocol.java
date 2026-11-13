package cn.starfall.gacha.local;
import java.util.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.net.URLEncoder;
final class BiliProtocol {
 static String gameChannel(String provider){return "bilibili".equals(provider)?"bilibili":"haoplay_cn";}
 static String uid(Object v){
  if(!(v instanceof String||v instanceof Long||v instanceof Integer))return "";
  String s=v.toString();return s.matches("[1-9][0-9]{0,19}")?s:"";
 }
 static String sign(Map<String,String> p,String key)throws Exception{
  StringBuilder b=new StringBuilder();for(Map.Entry<String,String> e:new TreeMap<>(p).entrySet()){
   if(!Arrays.asList("sign","token","item_name","item_desc","feign_sign").contains(e.getKey().toLowerCase(Locale.ROOT)))b.append(e.getValue());
  }
  byte[] hash=MessageDigest.getInstance("MD5").digest(b.append(key).toString().getBytes(StandardCharsets.UTF_8));
  StringBuilder hex=new StringBuilder();for(byte v:hash)hex.append(String.format(Locale.ROOT,"%02x",v&255));return hex.toString();
 }
 static String form(Map<String,String> p,String key)throws Exception{
  Map<String,String> values=new TreeMap<>(p);values.put("sign",sign(p,key));StringBuilder b=new StringBuilder();
  for(Map.Entry<String,String> e:values.entrySet()){if(b.length()>0)b.append('&');b.append(URLEncoder.encode(e.getKey(),"UTF-8")).append('=').append(URLEncoder.encode(e.getValue(),"UTF-8"));}return b.toString();
 }
}
