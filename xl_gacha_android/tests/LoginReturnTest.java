package cn.starfall.gacha.local;
import android.net.Uri;
public class LoginReturnTest {
 static int n;
 static void ok(boolean b){if(!b)throw new AssertionError("return case "+n);n++;}
 public static void main(String[] args){try{run();System.exit(0);}catch(Throwable e){e.printStackTrace();System.exit(1);}}
 static void run(){
  String callback="https://accounts.taptap.cn/social/callback/qq?code=qaCode&state=qaState";
  ok(LoginReturn.parseShare("QQ授权\n"+callback)!=null);
  ok(LoginReturn.matches(callback,"qaState"));
  ok(!LoginReturn.matches(callback,"otherSession"));
  ok(!LoginReturn.matches(callback,null));
  ok(LoginReturn.parseShare(callback+"&state=other")==null);
  ok(LoginReturn.parseShare(callback+"&redirect_uri=https://evil.example")==null);
  ok(LoginReturn.parseShare(callback.replace("https:","http:"))==null);
  ok(LoginReturn.parseShare(callback.replace("accounts.taptap.cn","accounts.taptap.cn.evil.example"))==null);
  ok(LoginReturn.parseShare(callback.replace("/qq?","/other?"))==null);
  ok(LoginReturn.parseShare(callback+"#fragment")==null);
  ok(LoginReturn.parseShare(callback+"\nhttps://evil.example")==null);
  ok(LoginReturn.parseShare(callback.replace("code=qaCode","code="))==null);
  ok("qaState".equals(LoginReturn.authorizationState(Uri.parse("https://graph.qq.com/oauth2.0/m_authorize?state=qaState"))));
  ok("qaState".equals(LoginReturn.authorizationState(Uri.parse("mqqapi://oauth?url=https%3A%2F%2Fgraph.qq.com%2Foauth2.0%2Fm_authorize%3Fstate%3DqaState"))));
  ok(LoginReturn.authorizationState(Uri.parse("https://evil.example/?state=qaState"))==null);
  System.out.println("Login return checks passed: "+n);
 }
}
