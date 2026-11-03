package cn.starfall.gacha.local;

import android.app.job.*;
import android.content.*;
import java.util.concurrent.Future;

/** Best-effort OS scheduling; foreground refresh also checks the renewal window. */
public final class CredentialRenewalJob extends JobService {
 private static final int ID=157001;
 private Future<?> task;
 static void schedule(Context context){
  JobScheduler jobs=(JobScheduler)context.getSystemService(Context.JOB_SCHEDULER_SERVICE);boolean enabled=false;
  for(String id:CredentialStore.accounts(context))try{enabled|=CredentialStore.status(context,id).optBoolean("auto_renew");}catch(Exception ignored){}
  if(enabled)jobs.schedule(new JobInfo.Builder(ID,new ComponentName(context,CredentialRenewalJob.class)).setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).setPersisted(true).setPeriodic(6*3600*1000L).build());else jobs.cancel(ID);
 }
 @Override public boolean onStartJob(JobParameters params){task=MainActivity.IO.submit(()->{for(String id:CredentialStore.accounts(this)){if(Thread.currentThread().isInterrupted())break;try{CredentialRenewal.renew(this,id);}catch(Exception ignored){}}jobFinished(params,false);});return true;}
 @Override public boolean onStopJob(JobParameters params){if(task!=null)task.cancel(true);return false;}
}
