package cn.starfall.gacha.local;
import android.app.*;
import android.content.*;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Bundle;
import android.provider.MediaStore;
import java.io.*;
import java.util.*;

/** Synthetic images only; removes only its own album contribution. */
public class ImageExportIntegration extends Instrumentation {
 int checks;Uri album,download;final ArrayList<Uri> staged=new ArrayList<>();
 void ok(boolean value,String name){if(!value)throw new AssertionError(name);checks++;}
 interface Attempt{void run()throws Exception;}
 void rejects(Attempt work)throws Exception{boolean rejected=false;try{work.run();}catch(IOException|IllegalArgumentException|UnsupportedOperationException e){rejected=true;}ok(rejected,"unsafe image operation rejected");}
 @Override public void onCreate(Bundle b){super.onCreate(b);start();}
 @Override public void onStart(){Bundle result=new Bundle();int outcome=Activity.RESULT_OK;Context c=getTargetContext();try{
  ByteArrayOutputStream out=new ByteArrayOutputStream();Bitmap bitmap=Bitmap.createBitmap(2,2,Bitmap.Config.ARGB_8888);bitmap.compress(Bitmap.CompressFormat.PNG,100,out);bitmap.recycle();byte[] png=out.toByteArray();
  rejects(()->ImageExports.stage(c,"not an image".getBytes()));
  Uri first=ImageExports.stage(c,png);staged.add(first);Uri second=ImageExports.stage(c,png);staged.add(second);
  ok(!first.equals(second),"unique private image IDs");
  ok("image/png".equals(c.getContentResolver().getType(first)),"provider MIME");
  try(InputStream in=c.getContentResolver().openInputStream(first)){ok(Arrays.equals(png,GachaClient.bounded(in,1024*1024)),"provider bytes readable");}
  try(Cursor row=c.getContentResolver().query(first,null,null,null,null)){ok(row!=null&&row.moveToFirst()&&row.getLong(row.getColumnIndexOrThrow("_size"))==png.length,"provider metadata");}
  rejects(()->{try(OutputStream ignored=c.getContentResolver().openOutputStream(first,"w")) {}});
  rejects(()->ImageExports.file(c,Uri.parse(first.toString()+"/other")));
  rejects(()->ImageExports.file(c,Uri.parse(first.toString()+"?path=../files")));
  rejects(()->ImageExports.file(c,Uri.parse("content://evil.example/"+first.getLastPathSegment())));
  Intent one=ImageExports.shareIntent(c,Collections.singletonList(first.getLastPathSegment()));
  ok(Intent.ACTION_SEND.equals(one.getAction())&&first.equals(one.getParcelableExtra(Intent.EXTRA_STREAM)),"single image share");
  Intent multi=ImageExports.shareIntent(c,Arrays.asList(second.getLastPathSegment(),first.getLastPathSegment()));
  ok(Intent.ACTION_SEND_MULTIPLE.equals(multi.getAction())&&multi.getParcelableArrayListExtra(Intent.EXTRA_STREAM).equals(Arrays.asList(second,first)),"multiple image order");
  ok(multi.getClipData().getItemCount()==2&&(multi.getFlags()&Intent.FLAG_GRANT_READ_URI_PERMISSION)!=0&&(multi.getFlags()&Intent.FLAG_GRANT_WRITE_URI_PERMISSION)==0,"temporary read-only grants");
  rejects(()->ImageExports.shareIntent(c,Collections.emptyList()));
  rejects(()->ImageExports.shareIntent(c,Arrays.asList(first.getLastPathSegment(),first.getLastPathSegment())));
  album=ImageExports.saveGallery(c,png,"QA-相册测试.png");
  try(InputStream in=c.getContentResolver().openInputStream(album)){ok(Arrays.equals(png,GachaClient.bounded(in,1024*1024)),"album bytes persisted");}
  try(Cursor row=c.getContentResolver().query(album,new String[]{MediaStore.MediaColumns.RELATIVE_PATH,MediaStore.MediaColumns.IS_PENDING},null,null,null)){ok(row!=null&&row.moveToFirst()&&row.getString(0).equals("Pictures/星落抽卡手记/")&&row.getInt(1)==0,"published Pictures album");}
  String name="QA-"+UUID.randomUUID()+".json";byte[] json="{\"qa\":true}".getBytes("UTF-8");Exports.saveDownloads(c,json,name,"application/json");
  try(Cursor row=c.getContentResolver().query(MediaStore.Downloads.EXTERNAL_CONTENT_URI,new String[]{"_id",MediaStore.MediaColumns.RELATIVE_PATH},MediaStore.MediaColumns.DISPLAY_NAME+"=?",new String[]{name},null)){ok(row!=null&&row.moveToFirst()&&row.getString(1).equals("Download/Starfall/"),"document export remains in Downloads");download=ContentUris.withAppendedId(MediaStore.Downloads.EXTERNAL_CONTENT_URI,row.getLong(0));}
  try(InputStream in=c.getContentResolver().openInputStream(download)){ok(Arrays.equals(json,GachaClient.bounded(in,1024)),"document export bytes preserved");}
  File expired=ImageExports.file(c,first);ok(expired.setLastModified(System.currentTimeMillis()-2*ImageExports.RETENTION),"expire synthetic cache");ImageExports.cleanup(c);
  ok(!expired.exists()&&ImageExports.file(c,second).exists(),"cleanup preserves fresh shared image");
  result.putString("result","PASS "+checks+" image album/share checks");
 }catch(Throwable e){result.putString("result","FAIL "+e);outcome=Activity.RESULT_CANCELED;}finally{if(album!=null)c.getContentResolver().delete(album,null,null);if(download!=null)c.getContentResolver().delete(download,null,null);for(Uri uri:staged)try{ImageExports.file(c,uri).delete();}catch(IOException ignored){}}finish(outcome,result);}
}
