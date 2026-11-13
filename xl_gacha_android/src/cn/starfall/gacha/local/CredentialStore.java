package cn.starfall.gacha.local;

import android.content.Context;
import android.security.keystore.*;
import android.util.AtomicFile;
import android.util.Base64;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.*;
import javax.crypto.*;
import javax.crypto.spec.GCMParameterSpec;

/** Native-only account secrets. Reports, WebView storage and backups never receive these values. */
final class CredentialStore {
 private static final String ALIAS="starfall-account-credentials-v1";
 static File file(Context context,String id)throws Exception{
  if(id==null||!id.matches("(?:account|local)-[0-9a-f]{32}"))throw GachaClient.error("INVALID_INPUT");
  File directory=new File(context.getNoBackupFilesDir(),"credentials");
  if(!directory.isDirectory()&&!directory.mkdirs())throw GachaClient.error("CREDENTIAL_STORAGE_ERROR");
  return new File(directory,id+".session");
 }
 private static javax.crypto.SecretKey key()throws Exception{
  KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);
  if(!store.containsAlias(ALIAS)){
   KeyGenerator generator=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");
   generator.init(new KeyGenParameterSpec.Builder(ALIAS,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());generator.generateKey();
  }
  return (javax.crypto.SecretKey)store.getKey(ALIAS,null);
 }
 static synchronized JSONObject load(Context context,String id)throws Exception{
  AtomicFile file=new AtomicFile(file(context,id));
  try(InputStream in=file.openRead()){
   JSONObject envelope=new JSONObject(new String(GachaClient.bounded(in,200000),StandardCharsets.UTF_8));
   if(envelope.getInt("version")!=1)throw new IOException();
   Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Base64.decode(envelope.getString("iv"),Base64.NO_WRAP)));cipher.updateAAD(id.getBytes(StandardCharsets.UTF_8));
   JSONObject value=new JSONObject(new String(cipher.doFinal(Base64.decode(envelope.getString("data"),Base64.NO_WRAP)),StandardCharsets.UTF_8));
   GachaClient.Credential c=decode(value);if(!id.equals(GachaClient.accountId(c.uid,c.provider)))throw new IOException();return value;
  }catch(FileNotFoundException e){return null;}catch(Exception e){throw GachaClient.error("CREDENTIAL_STORAGE_ERROR");}
 }
 static synchronized void write(Context context,String id,JSONObject value)throws Exception{
  AtomicFile file=new AtomicFile(file(context,id));FileOutputStream out=null;
  try{
   Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key());cipher.updateAAD(id.getBytes(StandardCharsets.UTF_8));
   JSONObject envelope=MainActivity.object("version",1,"iv",Base64.encodeToString(cipher.getIV(),Base64.NO_WRAP),"data",Base64.encodeToString(cipher.doFinal(value.toString().getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP));
   out=file.startWrite();out.write(envelope.toString().getBytes(StandardCharsets.UTF_8));file.finishWrite(out);out=null;
  }catch(Exception e){if(out!=null)file.failWrite(out);throw GachaClient.error("CREDENTIAL_STORAGE_ERROR");}
 }
 static GachaClient.Credential decode(JSONObject value)throws Exception{
  JSONObject c=value.getJSONObject("credential");String provider=c.getString("channel"),uid=c.getString("open_id");
  if("bilibili".equals(provider))return new GachaClient.Credential(c.getString("token"),uid,provider,c.getString("guid"));
  GachaClient.Credential parsed=GachaClient.credential(c.getString("token"),false);
  if(!parsed.uid.equals(uid)||!parsed.provider.equals(provider))throw GachaClient.error("ACCOUNT_ID_MISMATCH");return parsed;
 }
 static JSONObject encoded(GachaClient.Credential c){return MainActivity.object("token",c.token,"open_id",c.uid,"channel",c.provider,"guid",c.guid);}
 static synchronized void save(Context context,GachaClient.Credential c)throws Exception{
  String id=GachaClient.accountId(c.uid,c.provider);JSONObject value=load(context,id);
  if(value==null)value=MainActivity.object("auto_renew",false);
  else {GachaClient.Credential old=decode(value);if(old.issuedAt>c.issuedAt&&old.expiresAt>System.currentTimeMillis()/1000)return;}
  if(!value.has("credential")||!decode(value).token.equals(c.token)){value.remove("renewal_blocked");value.remove("last_error");}
  value.put("credential",encoded(c));write(context,id,value);
 }
 static synchronized GachaClient.Credential read(Context context,String id)throws Exception{
  JSONObject value=load(context,id);if(value==null)throw GachaClient.error("CREDENTIAL_MISSING");
  GachaClient.Credential c=decode(value);if(c.expiresAt>0&&c.expiresAt<=System.currentTimeMillis()/1000)throw GachaClient.error("CREDENTIAL_EXPIRED");return c;
 }
 static synchronized JSONObject status(Context context,String id)throws Exception{
  JSONObject value=load(context,id);if(value==null)return MainActivity.object("available",false,"auto_renew",false,"renewal_ready",false);
  GachaClient.Credential c=decode(value);return MainActivity.object("available",true,"expires_at",c.expiresAt,"expired",c.expiresAt>0&&c.expiresAt<=System.currentTimeMillis()/1000,"auto_renew",value.optBoolean("auto_renew"),"renewal_ready",value.has("profile"),"last_error",value.optString("last_error"));
 }
 static synchronized void importState(Context context,String id,String raw)throws Exception{
  if(raw==null||raw.getBytes(StandardCharsets.UTF_8).length>131072)throw GachaClient.error("INVALID_RENEWAL_PROFILE");
  JSONObject state;GachaClient.Credential candidate;
  try{state=new JSONObject(raw);candidate=GachaClient.credential(state.getJSONObject("credential").getString("token"));}catch(Exception e){throw GachaClient.error("INVALID_RENEWAL_PROFILE");}
  if(candidate.issuedAt<=0||candidate.issuedAt>System.currentTimeMillis()/1000+300)throw GachaClient.error("INVALID_RENEWAL_PROFILE");
  JSONObject c=state.getJSONObject("credential");
  if(!id.equals(GachaClient.accountId(candidate.uid,candidate.provider))||!candidate.uid.equals(c.optString("open_id"))||!candidate.provider.equals(c.optString("channel")))throw GachaClient.error("ACCOUNT_ID_MISMATCH");
  CredentialRenewal.validateProfile(state.getJSONObject("profile"),candidate);
  JSONObject current=load(context,id),value=MainActivity.object("auto_renew",false,"profile",state.getJSONObject("profile"),"credential",encoded(candidate));
  if(state.optInt("version")!=1)throw GachaClient.error("INVALID_RENEWAL_PROFILE");
  if(current!=null&&decode(current).issuedAt>candidate.issuedAt)value.put("credential",current.getJSONObject("credential"));
  write(context,id,value);CredentialRenewalJob.schedule(context);
 }
 static synchronized void configure(Context context,String id,boolean enabled)throws Exception{
  JSONObject value=load(context,id);if(value==null)throw GachaClient.error("CREDENTIAL_MISSING");
  if(enabled&&!value.has("profile"))throw GachaClient.error("RENEWAL_PROFILE_REQUIRED");
  if(enabled)read(context,id);
  value.put("auto_renew",enabled);write(context,id,value);CredentialRenewalJob.schedule(context);
 }
 static synchronized void delete(Context context,String id)throws Exception{new AtomicFile(file(context,id)).delete();CredentialRenewalJob.schedule(context);}
 static synchronized List<String> accounts(Context context){
  List<String> ids=new ArrayList<>();File[] files=new File(context.getNoBackupFilesDir(),"credentials").listFiles();
  if(files!=null)for(File file:files)if(file.getName().matches("account-[0-9a-f]{32}\\.session"))ids.add(file.getName().replace(".session",""));return ids;
 }
}
