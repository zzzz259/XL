package cn.starfall.gacha.local;

import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Environment;
import android.provider.MediaStore;
import java.io.IOException;
import java.io.OutputStream;

/** Save an app-owned scoped-storage download; never requests broad file permissions. */
final class Exports {
 static void saveDownloads(Context context,byte[] bytes,String name,String mime)throws IOException {
  saveScoped(context,MediaStore.Downloads.EXTERNAL_CONTENT_URI,Environment.DIRECTORY_DOWNLOADS+"/Starfall",bytes,name,mime);
 }
 static Uri saveScoped(Context context,Uri collection,String directory,byte[] bytes,String name,String mime)throws IOException {
  ContentValues values=new ContentValues();
  values.put(MediaStore.MediaColumns.DISPLAY_NAME,name);
  values.put(MediaStore.MediaColumns.MIME_TYPE,mime);
  values.put(MediaStore.MediaColumns.RELATIVE_PATH,directory);
  values.put(MediaStore.MediaColumns.IS_PENDING,1);
  Uri target=context.getContentResolver().insert(collection,values);
  if(target==null)throw new IOException("FILE_SAVE_FAILED");
  boolean complete=false;
  try {
   try(OutputStream out=context.getContentResolver().openOutputStream(target,"w")) {
    if(out==null)throw new IOException("FILE_SAVE_FAILED");
    out.write(bytes);
   }
   ContentValues ready=new ContentValues();ready.put(MediaStore.MediaColumns.IS_PENDING,0);
   if(context.getContentResolver().update(target,ready,null,null)!=1)throw new IOException("FILE_SAVE_FAILED");
   complete=true;
  } finally {
   if(!complete)try{context.getContentResolver().delete(target,null,null);}catch(RuntimeException ignored){}
  }
  return target;
 }
}
