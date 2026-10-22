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
 }
}
