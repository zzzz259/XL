package cn.starfall.gacha.local;

import java.io.*;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.util.*;

final class Wire {
 static class Field {int n,w;long value;byte[] bytes;Field(int n,int w){this.n=n;this.w=w;}}
 static byte[] varint(long value){ByteArrayOutputStream out=new ByteArrayOutputStream();while((value&~127L)!=0){out.write((int)(value&127)|128);value>>>=7;}out.write((int)value);return out.toByteArray();}
 static byte[] join(byte[]...parts){ByteArrayOutputStream out=new ByteArrayOutputStream();for(byte[] p:parts)out.write(p,0,p.length);return out.toByteArray();}
 static byte[] integer(int n,long value){return join(varint(n<<3),varint(value));}
 static byte[] blob(int n,byte[] value){return join(varint((n<<3)|2),varint(value.length),value);}
 static byte[] frame(int command,byte[] body,int sequence){byte[] msg=join(blob(1,join(integer(1,command),integer(3,sequence))),blob(2,body));if(msg.length>65535)throw new IllegalArgumentException("REQUEST_TOO_LARGE");return join(ByteBuffer.allocate(8).putShort((short)0).putShort((short)msg.length).putShort((short)0).putShort((short)command).array(),msg);}
 static long readVar(byte[] data,int[] p)throws IOException{long v=0;for(int shift=0;shift<70;shift+=7){if(p[0]>=data.length)break;int b=data[p[0]++]&255;v|=(long)(b&127)<<shift;if((b&128)==0)return v;}throw new IOException("INVALID_PROTOBUF");}
 static List<Field> fields(byte[] data)throws IOException{List<Field> result=new ArrayList<>();int[] p={0};while(p[0]<data.length){long tag=readVar(data,p);int n=(int)(tag>>>3),w=(int)(tag&7);if(n<1)throw new IOException("INVALID_PROTOBUF");Field f=new Field(n,w);
  if(w==0)f.value=readVar(data,p);else{long size=w==2?readVar(data,p):w==1?8:w==5?4:-1;if(size<0||size>data.length-p[0])throw new IOException("TRUNCATED_PROTOBUF");f.bytes=Arrays.copyOfRange(data,p[0],p[0]+(int)size);p[0]+=(int)size;}result.add(f);}return result;}
 static long number(List<Field> fields,int n){for(Field f:fields)if(f.n==n&&f.w==0)return f.value;return 0;}
 static byte[] bytes(List<Field> fields,int n){for(Field f:fields)if(f.n==n&&f.w==2)return f.bytes;return new byte[0];}
 static Map<String,Object> profile(byte[] body)throws IOException{
  List<Field> rsp=fields(body),actor=fields(bytes(rsp,2)),head=fields(bytes(actor,18));Map<String,Object> out=new HashMap<>();
  out.put("nickname",new String(bytes(actor,6),java.nio.charset.StandardCharsets.UTF_8));out.put("game_uid",String.valueOf(number(actor,2)));out.put("level",number(actor,4));out.put("face_id",number(actor,8));
  out.put("fashion_id",number(head,1));out.put("head_id",number(head,2));out.put("head_rect_id",number(head,3));out.put("head_rect_stamp",number(head,4));out.put("head_rect_extra",number(head,5));out.put("head_rect_item_uid",String.valueOf(number(head,6)));out.put("server_time",number(rsp,3));
  return out;
 }
 static byte[] exact(InputStream in,int n)throws IOException{byte[] out=new byte[n];int p=0;while(p<n){int got=in.read(out,p,n-p);if(got<0)throw new IOException("CONNECTION_CLOSED");p+=got;}return out;}
 static class Reply {int code;byte[] body;Reply(int code,byte[] body){this.code=code;this.body=body;}}
 static Reply response(Socket socket,int expected)throws IOException{long end=System.currentTimeMillis()+20000;for(int i=0;i<128;i++){long remaining=end-System.currentTimeMillis();if(remaining<=0)throw new IOException("RESPONSE_TIMEOUT");socket.setSoTimeout((int)remaining);ByteBuffer h=ByteBuffer.wrap(exact(socket.getInputStream(),8));int magic=h.getShort()&65535,n=h.getShort()&65535;if(magic!=0||n==0)throw new IOException("INVALID_FRAME");List<Field> msg=fields(exact(socket.getInputStream(),n));List<Field> head=fields(bytes(msg,1));if(number(head,1)==expected)return new Reply((int)number(head,4),bytes(msg,2));}throw new IOException("RESPONSE_TIMEOUT");}
}
