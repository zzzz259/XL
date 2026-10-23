package cn.starfall.gacha.local;
import android.content.Intent;
import android.net.Uri;
import java.util.Locale;
final class LoginLinks {
 static Intent create(Uri uri)throws Exception{
  String requestedPackage=null;
  if("intent".equalsIgnoreCase(uri.getScheme())){
   Intent parsed=Intent.parseUri(uri.toString(),Intent.URI_INTENT_SCHEME);
   requestedPackage=parsed.getPackage();uri=parsed.getData();
  }
  if(uri==null||uri.getScheme()==null)return null;
  String s=uri.getScheme().toLowerCase(Locale.ROOT);
  boolean qq=s.equals("mqq")||s.equals("mqqapi")||s.equals("mqqconnect")||s.equals("mqqopensdkssologin")||s.equals("wtloginmqq");
  boolean tap=s.equals("tap")||s.equals("taptap");
  if(!qq&&!tap)return null;
  if(requestedPackage!=null&&!(qq&&(requestedPackage.equals("com.tencent.mobileqq")||requestedPackage.equals("com.tencent.tim")||requestedPackage.equals("com.tencent.qqlite")))&&!(tap&&(requestedPackage.equals("com.taptap")||requestedPackage.equals("com.taptap.global"))))return null;
  Intent safe=new Intent(Intent.ACTION_VIEW,uri).addCategory(Intent.CATEGORY_BROWSABLE);
  if(qq)safe.setPackage(requestedPackage==null?"com.tencent.mobileqq":requestedPackage);
  else if(requestedPackage!=null)safe.setPackage(requestedPackage);
  return safe;
 }
 static boolean trusted(String url){
  if(url==null)return false;Uri u=Uri.parse(url);String h=u.getHost();if(!"https".equals(u.getScheme())||h==null)return false;
  h=h.toLowerCase(Locale.ROOT);
  for(String domain:new String[]{"haoplay.com.cn","taptap.cn","taptap.com","tapapis.cn","qq.com"})if(h.equals(domain)||h.endsWith("."+domain))return true;
  return false;
 }
 static boolean allowed(String url,boolean gesture,boolean recentTouch){return gesture||(recentTouch&&trusted(url));}
}
