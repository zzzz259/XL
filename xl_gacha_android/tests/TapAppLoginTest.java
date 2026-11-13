package cn.starfall.gacha.local;

import android.content.Intent;
import android.net.Uri;

/** Real Android Uri policy and short-lived, session-bound App handoff. */
public final class TapAppLoginTest {
 static int checks;
 static final String PAGE="https://accounts.taptap.cn/login?return_to=qa";
 static final String QR="https://accounts.taptap.cn/device?qrcode=qaChallenge&user_code=QA123456";
 static void ok(boolean value,String name){checks++;if(!value)throw new AssertionError(name);}
 public static void main(String[] args)throws Exception{
  ok(TapAppLogin.loginPage(PAGE),"official login page");
  for(String url:new String[]{"http://accounts.taptap.cn/login","https://accounts.taptap.cn.evil/login","https://other@accounts.taptap.cn/login","https://accounts.taptap.cn:444/login","https://accounts.taptap.cn/device"})ok(!TapAppLogin.loginPage(url),"untrusted page");
  TapAppLogin.Challenge pending=new TapAppLogin.Challenge(PAGE,QR,"owner",4,1000);
  ok(pending.available(PAGE,"owner",4,1001),"current challenge");
  ok(!pending.available(PAGE,"other",4,1001),"switched session");
  ok(!pending.available(PAGE,"owner",5,1001),"navigated window");
  ok(!pending.available(PAGE,"owner",4,111001),"expired challenge");
  Intent intent=pending.consume(PAGE,"owner",4,1001);
  ok("com.taptap".equals(intent.getPackage()),"fixed official package");
  ok("taptap://taptap.com/to?url=https%3A%2F%2Faccounts.taptap.cn%2Fdevice%3Fqrcode%3DqaChallenge%26user_code%3DQA123456".equals(intent.getDataString()),"official nested device URL encoded once");
  ok(!pending.available(PAGE,"owner",4,1002),"cannot replay");
  for(String url:new String[]{QR+"&next=https://evil.example",QR+"&user_code=OTHER",QR+"#fragment",QR.replace("https:","http:"),QR.replace("/device?","/device/../login?"),QR.replace("accounts.taptap.cn","accounts.taptap.cn.evil"),QR.replace("qaChallenge","%0A"),"https://accounts.taptap.cn/device?user_code=QA"}){
   boolean rejected=false;try{new TapAppLogin.Challenge(PAGE,url,"owner",4,1000);}catch(IllegalArgumentException e){rejected=true;}ok(rejected,"unsafe challenge");
  }
  System.out.println("TapTap App policy checks passed: "+checks);
 }
}
