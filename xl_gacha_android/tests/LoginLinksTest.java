package cn.starfall.gacha.local;
import android.content.Intent;
import android.net.Uri;
public class LoginLinksTest {
 static int n;
 static void ok(boolean b){if(!b)throw new AssertionError("case "+n);n++;}
 public static void main(String[] args){try{run();System.exit(0);}catch(Throwable e){e.printStackTrace();System.exit(1);}}
 static void run()throws Exception{
  ok(LoginLinks.create(Uri.parse("mqqapi://oauth/login?qa=1")).getPackage().equals("com.tencent.mobileqq"));
  ok(LoginLinks.create(Uri.parse("mqqOpensdkSSoLogin://SSoLogin/qq?qa=1"))!=null);
  ok(LoginLinks.create(Uri.parse("intent://oauth/login#Intent;scheme=mqqapi;package=com.tencent.mobileqq;end")).getComponent()==null);
  ok(LoginLinks.create(Uri.parse("intent://oauth/login#Intent;scheme=mqqapi;package=evil.app;end"))==null);
  ok(LoginLinks.create(Uri.parse("file:///private"))==null);
  ok(LoginLinks.create(Uri.parse("javascript:alert(1)"))==null);
  ok(LoginLinks.create(Uri.parse("unknownapp://test"))==null);
  ok(LoginLinks.create(Uri.parse("taptap://oauth/login"))!=null);
  ok(LoginLinks.trusted("https://accounts.taptap.cn/login"));
  ok(LoginLinks.trusted("https://graph.qq.com/oauth2"));
  ok(!LoginLinks.trusted("https://taptap.cn.evil.example/login"));
  ok(!LoginLinks.trusted("http://graph.qq.com/oauth2"));
  ok(!LoginLinks.trusted("https://evil.example/login"));
  ok(LoginLinks.allowed("https://accounts.taptap.cn/login",false,true));
  ok(!LoginLinks.allowed("https://evil.example/login",false,true));
  ok(!LoginLinks.allowed("https://graph.qq.com/login",false,false));
  System.out.println("Login link policy checks passed: "+n);
 }
}
