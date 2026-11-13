package cn.starfall.gacha.local;

import android.content.Intent;
import android.net.Uri;
import java.util.Set;

/** Official QR handoff; challenge values are short-lived and remain in memory. */
final class TapAppLogin {
 static boolean official(Uri uri){return "https".equals(uri.getScheme())&&"accounts.taptap.cn".equals(uri.getHost())&&uri.getUserInfo()==null&&(uri.getPort()==-1||uri.getPort()==443);}
 static boolean loginPage(String url){if(url==null||url.length()>16384)return false;Uri u=Uri.parse(url);return official(u)&&"/login".equals(u.getPath());}
 static boolean authPage(String url){if(url==null||url.length()>16384)return false;Uri u=Uri.parse(url);return official(u)&&("/authorize".equals(u.getPath())||"/login".equals(u.getPath()));}
 static final class Challenge {
  private final String page,owner;private String url;private final long generation,expires;
  Challenge(String page,String url,String owner,long generation,long now){
   if(!loginPage(page)||url==null||url.length()>2048||owner==null||owner.isEmpty())throw new IllegalArgumentException("TAP_CHALLENGE_INVALID");
   Uri uri=Uri.parse(url);Set<String> keys=uri.getQueryParameterNames();
   if(!official(uri)||!"/device".equals(uri.getEncodedPath())||uri.getFragment()!=null||keys.size()!=2||!keys.contains("qrcode")||!keys.contains("user_code"))throw new IllegalArgumentException("TAP_CHALLENGE_INVALID");
   for(String key:keys)if(uri.getQueryParameters(key).size()!=1||!uri.getQueryParameter(key).matches("[A-Za-z0-9._~-]{1,1024}"))throw new IllegalArgumentException("TAP_CHALLENGE_INVALID");
   this.page=page;this.url=url;this.owner=owner;this.generation=generation;this.expires=now+110000;
  }
  boolean available(String page,String owner,long generation,long now){return url!=null&&this.page.equals(page)&&this.owner.equals(owner)&&this.generation==generation&&now<expires&&now>=expires-110000;}
  Intent consume(String page,String owner,long generation,long now){
   if(!available(page,owner,generation,now))return null;
   Uri route=new Uri.Builder().scheme("taptap").authority("taptap.com").path("to").appendQueryParameter("url",url).build();url=null;
   return new Intent(Intent.ACTION_VIEW,route).addCategory(Intent.CATEGORY_BROWSABLE).setPackage("com.taptap");
  }
 }
}
