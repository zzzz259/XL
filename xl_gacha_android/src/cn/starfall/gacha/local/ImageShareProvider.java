package cn.starfall.gacha.local;

import android.content.*;
import android.database.*;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.*;

/** A private, read-only provider with per-share URI grants. */
public class ImageShareProvider extends ContentProvider {
 @Override public boolean onCreate(){return true;}
 @Override public String getType(Uri uri){try{ImageExports.file(getContext(),uri);return "image/png";}catch(IOException e){return null;}}
 @Override public ParcelFileDescriptor openFile(Uri uri,String mode)throws FileNotFoundException{if(!"r".equals(mode))throw new FileNotFoundException("READ_ONLY_IMAGE");return ParcelFileDescriptor.open(ImageExports.file(getContext(),uri),ParcelFileDescriptor.MODE_READ_ONLY);}
 @Override public Cursor query(Uri uri,String[] projection,String selection,String[] args,String order){
  try{File f=ImageExports.file(getContext(),uri);String[] columns=projection==null?new String[]{OpenableColumns.DISPLAY_NAME,OpenableColumns.SIZE}:projection;MatrixCursor cursor=new MatrixCursor(columns);Object[] values=new Object[columns.length];
   for(int i=0;i<columns.length;i++){if(OpenableColumns.DISPLAY_NAME.equals(columns[i]))values[i]="星落抽卡记录-"+f.getName();else if(OpenableColumns.SIZE.equals(columns[i]))values[i]=f.length();}cursor.addRow(values);return cursor;
  }catch(IOException e){throw new IllegalArgumentException("IMAGE_NOT_FOUND");}
 }
 @Override public Uri insert(Uri u,ContentValues v){throw new UnsupportedOperationException("READ_ONLY_IMAGE");}
 @Override public int update(Uri u,ContentValues v,String s,String[] a){throw new UnsupportedOperationException("READ_ONLY_IMAGE");}
 @Override public int delete(Uri u,String s,String[] a){throw new UnsupportedOperationException("READ_ONLY_IMAGE");}
}
