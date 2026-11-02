package cn.starfall.gacha.local;
import java.util.*;
public class WireTest {
 public static void main(String[] args)throws Exception{
  if(!Arrays.equals(Wire.varint(300),new byte[]{(byte)0xac,2}))throw new AssertionError("varint");
  byte[] b=Wire.join(Wire.integer(1,150),Wire.blob(2,new byte[]{1,2,3}));
  if(!Arrays.equals(b,new byte[]{8,(byte)0x96,1,18,3,1,2,3}))throw new AssertionError("protobuf");
  List<Wire.Field> fields=Wire.fields(b);
  if(Wire.number(fields,1)!=150||!Arrays.equals(Wire.bytes(fields,2),new byte[]{1,2,3}))throw new AssertionError("decode");
  try{Wire.fields(new byte[]{18,10,1});throw new AssertionError("truncated accepted");}catch(java.io.IOException expected){}
  byte[] frame=Wire.frame(10504,Wire.integer(1,1),2);
  if(frame[0]!=0||frame[1]!=0||frame.length!=8+((frame[2]&255)*256+(frame[3]&255)))throw new AssertionError("frame");
  System.out.println("Android protobuf/transport fixture checks passed.");
  byte[] head=Wire.join(Wire.integer(1,7001),Wire.integer(2,8002),Wire.integer(3,9003),Wire.integer(4,1735689600123L),Wire.integer(5,1),Wire.integer(6,8800202610311234L));
  byte[] actor=Wire.join(Wire.blob(1,"openid-x".getBytes(java.nio.charset.StandardCharsets.UTF_8)),Wire.integer(2,9007199254740993L),Wire.integer(3,123456),Wire.integer(4,88),Wire.integer(5,999999),Wire.blob(6,"星见雅".getBytes(java.nio.charset.StandardCharsets.UTF_8)),Wire.integer(8,3008),Wire.blob(18,head));
  Map<String,Object> p=Wire.profile(Wire.join(Wire.integer(1,1),Wire.blob(2,actor),Wire.integer(3,1735689600456L)));
  if(!"星见雅".equals(p.get("nickname")))throw new AssertionError("nickname");
  if(!"9007199254740993".equals(p.get("game_uid")))throw new AssertionError("game_uid");
  if(((Number)p.get("level")).longValue()!=88||((Number)p.get("face_id")).longValue()!=3008)throw new AssertionError("level/face_id");
  if(((Number)p.get("fashion_id")).longValue()!=7001||((Number)p.get("head_id")).longValue()!=8002||((Number)p.get("head_rect_id")).longValue()!=9003)throw new AssertionError("head ids");
  if(((Number)p.get("head_rect_stamp")).longValue()!=1735689600123L||((Number)p.get("head_rect_extra")).longValue()!=1)throw new AssertionError("head rect");
  if(!"8800202610311234".equals(p.get("head_rect_item_uid")))throw new AssertionError("head_rect_item_uid");
  if(((Number)p.get("server_time")).longValue()!=1735689600456L)throw new AssertionError("server_time");
  Map<String,Object> missing=Wire.profile(Wire.join(Wire.integer(1,1),Wire.integer(3,42)));
  if(!"".equals(missing.get("nickname"))||!"0".equals(missing.get("game_uid"))||((Number)missing.get("level")).longValue()!=0||((Number)missing.get("server_time")).longValue()!=42)throw new AssertionError("missing actor defaults");
  Map<String,Object> noHead=Wire.profile(Wire.join(Wire.integer(1,1),Wire.blob(2,Wire.join(Wire.integer(2,123),Wire.integer(4,7),Wire.blob(6,"abc".getBytes(java.nio.charset.StandardCharsets.UTF_8)))),Wire.integer(3,99)));
  if(((Number)noHead.get("fashion_id")).longValue()!=0||((Number)noHead.get("head_id")).longValue()!=0||((Number)noHead.get("head_rect_id")).longValue()!=0||((Number)noHead.get("head_rect_stamp")).longValue()!=0||((Number)noHead.get("head_rect_extra")).longValue()!=0||!"0".equals(noHead.get("head_rect_item_uid")))throw new AssertionError("zero avatar fields");
  if(!"123".equals(noHead.get("game_uid"))||((Number)noHead.get("level")).longValue()!=7||!"abc".equals(noHead.get("nickname"))||((Number)noHead.get("server_time")).longValue()!=99)throw new AssertionError("actor without head");
  System.out.println("EnterGameRsp profile parse fixture checks passed.");
 }
}
