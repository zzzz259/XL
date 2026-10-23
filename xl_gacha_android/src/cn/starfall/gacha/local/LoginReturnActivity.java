package cn.starfall.gacha.local;

import android.app.*;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;

/** Explicit browser share only; never claims another provider's HTTPS links. */
public class LoginReturnActivity extends Activity {
 @Override public void onCreate(Bundle state){super.onCreate(state);
  Intent input=getIntent();String text=null;
  try{if(input!=null&&Intent.ACTION_SEND.equals(input.getAction())&&"text/plain".equals(input.getType())){CharSequence value=input.getCharSequenceExtra(Intent.EXTRA_TEXT);if(value!=null&&value.length()<=32768)text=value.toString();}}catch(RuntimeException ignored){}
  Uri callback=LoginReturn.parseShare(text);if(input!=null)input.replaceExtras((Bundle)null);
  LoginActivity pending=LoginActivity.current;
  if(callback==null){message("请分享浏览器中 QQ 授权后返回的 TapTap 页面。此入口不接受其他链接。");return;}
  if(pending==null||!pending.canResumeShare(callback)){message("没有匹配的待完成 QQ 登录，或本次登录已过期。请回到工具重新开始登录，再分享最新的回调页面。");return;}
  new AlertDialog.Builder(this).setTitle("继续 QQ 授权登录")
   .setMessage("将当前回调交给本次登录窗口的 TapTap 页面继续处理？授权链接不会写入抽卡存档或日志。")
   .setPositiveButton("继续登录",(d,w)->{if(pending.resumeShare(callback)){pending.startActivity(new Intent(pending,LoginActivity.class).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));finish();}else message("登录状态已变化，请重新开始登录。");})
   .setNegativeButton("取消",(d,w)->finish()).setOnCancelListener(d->finish()).show();
 }
 void message(String text){new AlertDialog.Builder(this).setTitle("无法继续登录").setMessage(text).setPositiveButton("知道了",(d,w)->finish()).setOnCancelListener(d->finish()).show();}
}
