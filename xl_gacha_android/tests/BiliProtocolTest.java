package cn.starfall.gacha.local;
import java.util.*;
public final class BiliProtocolTest {
 private static void check(boolean b,String s){if(!b)throw new AssertionError(s);}
 public static void main(String[] args)throws Exception{
  check("bilibili".equals(BiliProtocol.gameChannel("bilibili")),"B server entered official channel");
  check("haoplay_cn".equals(BiliProtocol.gameChannel("taptap")),"official provider routing changed");
  check("9007199254740993".equals(BiliProtocol.uid(9007199254740993L)),"UID precision lost");
  for(Object bad:new Object[]{null,0,-1,"00123","123x",9007199254740993d,new Object(){public String toString(){throw new AssertionError("unknown UID stringified");}}})check(BiliProtocol.uid(bad).isEmpty(),"invalid UID accepted");
  Map<String,String> p=new HashMap<>();p.put("b","2");p.put("a","1");p.put("sign","ignored");p.put("token","ignored");p.put("item_name","ignored");p.put("item_desc","ignored");p.put("feign_sign","ignored");
  check("cb3f80f72f5483d5dfcc84b2f68641f5".equals(BiliProtocol.sign(p,"DUMMY_CLIENT_KEY")),"SDK signature mismatch");
  p.put("access_key","DUMMY&uid=123");String form=BiliProtocol.form(p,"DUMMY_CLIENT_KEY");check(form.contains("access_key=DUMMY%26uid%3D123"),"unsafe form encoding");check(!form.contains("DUMMY_CLIENT_KEY"),"client key sent in request");
  System.out.println("Bili protocol: channel routing, UID precision, SDK signing and form encoding passed.");
 }
}
