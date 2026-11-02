package cn.starfall.gacha.local;

import android.app.*;
import android.content.*;
import android.graphics.Insets;
import android.graphics.Rect;
import android.os.*;
import android.view.*;
import android.widget.FrameLayout;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;

/** Exercises the real main-window inset listener in the isolated QA application. */
public final class NativeInsetsIntegration extends Instrumentation {
 int checks;MainActivity activity;FrameLayout content;View probe;WindowInsets source;
 final AtomicReference<WindowInsets> childInsets=new AtomicReference<>();
 void ok(boolean value,String name){if(!value)throw new AssertionError(name);checks++;}
 @Override public void onCreate(Bundle b){super.onCreate(b);start();}
 void fixture(String name,WindowInsets insets,int left,int top,int right,int bottom){
  int[] padding=new int[8];
  runOnMainSync(()->{
   childInsets.set(null);content.dispatchApplyWindowInsets(insets);
   padding[0]=content.getPaddingLeft();padding[1]=content.getPaddingTop();padding[2]=content.getPaddingRight();padding[3]=content.getPaddingBottom();
   padding[4]=activity.web.getPaddingLeft();padding[5]=activity.web.getPaddingTop();padding[6]=activity.web.getPaddingRight();padding[7]=activity.web.getPaddingBottom();
  });
  ok(padding[0]==left&&padding[1]==top&&padding[2]==right&&padding[3]==bottom,name+": parent reserves each handled edge exactly once");
  WindowInsets child=childInsets.get();
  ok(child==null||!child.hasSystemWindowInsets(),name+": already-reserved system-window space is not dispatched to WebView children");
  DisplayCutout cutout=child==null?null:child.getDisplayCutout();
  ok(cutout==null||(cutout.getSafeInsetLeft()==0&&cutout.getSafeInsetTop()==0&&cutout.getSafeInsetRight()==0&&cutout.getSafeInsetBottom()==0),name+": already-reserved display-cutout space is not dispatched to WebView children");
  ok(padding[4]==0&&padding[5]==0&&padding[6]==0&&padding[7]==0,name+": parent remains the sole native padding owner");
 }
 @Override public void onStart(){Bundle result=new Bundle();int outcome=Activity.RESULT_OK;try{
  ok("cn.starfall.gacha.returnqa".equals(getTargetContext().getPackageName()),"native inset test runs only in the isolated returnqa package");
  activity=(MainActivity)startActivitySync(new Intent(getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
  waitForIdleSync();
  for(int i=0;i<30;i++){
   AtomicReference<WindowInsets> attached=new AtomicReference<>();runOnMainSync(()->attached.set(activity.web.getRootWindowInsets()));
   if(attached.get()!=null)break;SystemClock.sleep(100);
  }
  runOnMainSync(()->{content=(FrameLayout)activity.web.getParent();source=content.getRootWindowInsets();});
  ok(source!=null,"main WebView is attached to a window with real insets");
  runOnMainSync(()->{
   int[] location=new int[2];activity.web.getLocationOnScreen(location);
   result.putString("native_window",MainActivity.object("android_api",Build.VERSION.SDK_INT,"web_x",location[0],"web_y",location[1],"web_width",activity.web.getWidth(),"web_height",activity.web.getHeight(),"parent_left",content.getPaddingLeft(),"parent_top",content.getPaddingTop(),"parent_right",content.getPaddingRight(),"parent_bottom",content.getPaddingBottom(),"window_left",source.getSystemWindowInsetLeft(),"window_top",source.getSystemWindowInsetTop(),"window_right",source.getSystemWindowInsetRight(),"window_bottom",source.getSystemWindowInsetBottom()).toString());
   probe=new View(activity);probe.setOnApplyWindowInsetsListener((v,insets)->{childInsets.set(insets);return insets;});
   // Observe before WebView, including on Android versions that dispatch to siblings sequentially.
   content.addView(probe,0,new FrameLayout.LayoutParams(1,1));
  });
  WindowInsets bars=source.consumeDisplayCutout().replaceSystemWindowInsets(7,29,11,48);
  fixture("four-sided system bars",bars,7,29,11,48);
  fixture("repeated bars do not accumulate",bars,7,29,11,48);
  // Legacy system-window bottom insets include the keyboard; retain existing resize behavior.
  fixture("keyboard-sized bottom inset",bars.replaceSystemWindowInsets(7,29,11,320),7,29,11,320);
  if(Build.VERSION.SDK_INT>=29){
   DisplayCutout cutout=new DisplayCutout(new Rect(17,39,19,58),Arrays.asList(new Rect(0,700,17,800),new Rect(450,0,550,39),new Rect(981,700,1000,800),new Rect(450,1442,550,1500)));
   WindowInsets notched=new WindowInsets.Builder().setSystemWindowInsets(Insets.of(7,29,11,48)).setDisplayCutout(cutout).build();
   fixture("cutout extends beyond bars",notched,17,39,19,58);
   fixture("bars extend beyond cutout",new WindowInsets.Builder().setSystemWindowInsets(Insets.of(27,49,31,68)).setDisplayCutout(cutout).build(),27,49,31,68);
  }else result.putString("synthetic_cutout","SKIP: WindowInsets.Builder requires API29; real API28 insets still exercised");
  fixture("removed bars reset old padding",bars.consumeSystemWindowInsets().consumeDisplayCutout(),0,0,0,0);
  result.putString("result","PASS "+checks+" native inset ownership checks");
 }catch(Throwable e){result.putString("result","FAIL "+e);outcome=Activity.RESULT_CANCELED;}
 finally{if(activity!=null)runOnMainSync(()->{if(content!=null){if(probe!=null)content.removeView(probe);content.requestApplyInsets();}activity.finish();});}
 finish(outcome,result);}
}
