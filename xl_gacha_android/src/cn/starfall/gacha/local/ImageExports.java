package cn.starfall.gacha.local;

import android.content.*;
import android.graphics.BitmapFactory;
import android.media.MediaScannerConnection;
import android.net.Uri;
import android.os.*;
import android.provider.MediaStore;
import java.io.*;
import java.util.*;

/** Only generated PNG images enter this cache. No archive or login files are exposed. */
final class ImageExports {
 static final long RETENTION=24*60*60*1000L,MAX_CACHE=128*1024*1024L;
 static File root(Context c){return new File(c.getCacheDir(),"shared-images");}
 static void validate(byte[] bytes)throws IOException{
  if(bytes==null||bytes.length<24||bytes.length>8*1024*1024)throw new IOException("IMAGE_INVALID");
  BitmapFactory.Options options=new BitmapFactory.Options();options.inJustDecodeBounds=true;BitmapFactory.decodeByteArray(bytes,0,bytes.length,options);
  if(!"image/png".equals(options.outMimeType)||options.outWidth<1||options.outHeight<1||options.outWidth>4096||options.outHeight>20000||(long)options.outWidth*options.outHeight>64000000)throw new IOException("IMAGE_INVALID");
 }
 static synchronized void cleanup(Context c){File[] files=root(c).listFiles();if(files!=null)for(File f:files)if(f.isFile()&&f.lastModified()<System.currentTimeMillis()-RETENTION)f.delete();}
 static synchronized Uri stage(Context c,byte[] bytes)throws IOException{
  validate(bytes);cleanup(c);File dir=root(c);if(!dir.isDirectory()&&!dir.mkdirs())throw new IOException("IMAGE_SHARE_FAILED");
  long size=bytes.length;File[] files=dir.listFiles();if(files!=null)for(File f:files)size+=f.length();if(size>MAX_CACHE)throw new IOException("IMAGE_SHARE_TOO_LARGE");
  File target=new File(dir,UUID.randomUUID()+".png");boolean complete=false;
  try{try(OutputStream out=new FileOutputStream(target)){out.write(bytes);}complete=true;}finally{if(!complete)target.delete();}
  return new Uri.Builder().scheme("content").authority(c.getPackageName()+".images").appendPath(target.getName()).build();
 }
 static File file(Context c,Uri uri)throws FileNotFoundException{
  if(uri==null||!"content".equals(uri.getScheme())||!(c.getPackageName()+".images").equals(uri.getAuthority())||uri.getQuery()!=null||uri.getFragment()!=null||uri.getPathSegments().size()!=1||!uri.getLastPathSegment().matches("[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\\.png"))throw new FileNotFoundException("IMAGE_NOT_FOUND");
  File f=new File(root(c),uri.getLastPathSegment());if(!f.isFile())throw new FileNotFoundException("IMAGE_NOT_FOUND");return f;
 }
 static Intent shareIntent(Context c,List<String> ids)throws IOException{
  if(ids==null||ids.isEmpty()||ids.size()>100||new HashSet<>(ids).size()!=ids.size())throw new IOException("IMAGE_SHARE_INVALID");
  ArrayList<Uri> uris=new ArrayList<>();for(String id:ids){Uri u=new Uri.Builder().scheme("content").authority(c.getPackageName()+".images").appendPath(id).build();file(c,u);uris.add(u);}
  Intent intent=new Intent(uris.size()==1?Intent.ACTION_SEND:Intent.ACTION_SEND_MULTIPLE).setType("image/png").addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
  if(uris.size()==1)intent.putExtra(Intent.EXTRA_STREAM,uris.get(0));else intent.putParcelableArrayListExtra(Intent.EXTRA_STREAM,uris);
  ClipData clip=ClipData.newUri(c.getContentResolver(),"抽卡记录",uris.get(0));for(int i=1;i<uris.size();i++)clip.addItem(new ClipData.Item(uris.get(i)));intent.setClipData(clip);return intent;
 }
 static Uri saveGallery(Context c,byte[] bytes,String name)throws IOException{
  validate(bytes);name=name.replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]","_");while(name.getBytes(java.nio.charset.StandardCharsets.UTF_8).length>160)name=name.substring(0,name.offsetByCodePoints(name.length(),-1));if(!name.endsWith(".png"))name+=".png";
  if(Build.VERSION.SDK_INT>=29)return Exports.saveScoped(c,MediaStore.Images.Media.EXTERNAL_CONTENT_URI,Environment.DIRECTORY_PICTURES+"/星落抽卡手记",bytes,name,"image/png");
  File dir=new File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_PICTURES),"星落抽卡手记");if(!dir.isDirectory()&&!dir.mkdirs())throw new IOException("FILE_SAVE_FAILED");
  File target=new File(dir,System.currentTimeMillis()+"-"+UUID.randomUUID()+"-"+name);boolean complete=false;
  try{try(OutputStream out=new FileOutputStream(target)){out.write(bytes);}complete=true;}finally{if(!complete)target.delete();}
  MediaScannerConnection.scanFile(c,new String[]{target.getAbsolutePath()},new String[]{"image/png"},null);return Uri.fromFile(target);
 }
}
