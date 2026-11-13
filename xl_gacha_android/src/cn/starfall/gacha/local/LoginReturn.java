package cn.starfall.gacha.local;
import android.net.Uri;
import java.util.*;
import java.util.regex.*;
final class LoginReturn {
 static final Pattern URL=Pattern.compile("https?://[^\\s<>\"\\[\\]]+");
 static boolean token(String v,int max){return v!=null&&v.length()<=max&&v.matches("[A-Za-z0-9._~-]+");}
 static Uri parseShare(String text){
  if(text==null||text.length()>32768)return null;
  Matcher m=URL.matcher(text);if(!m.find())return null;String raw=m.group();if(m.find())return null;
  try{Uri u=Uri.parse(raw);
   if(!"https".equals(u.getScheme())||!"accounts.taptap.cn".equalsIgnoreCase(u.getHost())||!"/social/callback/qq".equals(u.getPath())||(u.getPort()!=-1&&u.getPort()!=443)||u.getUserInfo()!=null||u.getFragment()!=null)return null;
   if(!u.getQueryParameterNames().equals(new HashSet<>(Arrays.asList("code","state")))||u.getQueryParameters("code").size()!=1||u.getQueryParameters("state").size()!=1)return null;
   return token(u.getQueryParameter("code"),4096)&&token(u.getQueryParameter("state"),512)?u:null;
  }catch(RuntimeException e){return null;}
 }
 static boolean matches(String text,String expected){Uri u=parseShare(text);return u!=null&&token(expected,512)&&expected.equals(u.getQueryParameter("state"));}
 static String authorizationState(Uri uri){return authorizationState(uri,0);}
 static String authorizationState(Uri uri,int depth){
  if(uri==null||depth>3||uri.toString().length()>32768)return null;
  try{
   boolean web=LoginLinks.trusted(uri.toString());boolean app=LoginLinks.create(uri)!=null;
   if(!web&&!app||"/social/callback/qq".equals(uri.getPath()))return null;
   for(String key:new String[]{"url","u1","redirect_uri","authorize_url","auth_url"}){
    String nested=uri.getQueryParameter(key);if(nested!=null){String state=authorizationState(Uri.parse(nested),depth+1);if(state!=null)return state;}
   }
   String state=uri.getQueryParameter("state");return uri.getQueryParameters("state").size()==1&&token(state,512)?state:null;
  }catch(Exception e){return null;}
 }
}
