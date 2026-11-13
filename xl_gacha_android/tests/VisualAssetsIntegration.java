package cn.starfall.gacha.local;

import android.app.Activity;
import android.app.Instrumentation;
import android.os.Bundle;
import java.io.File;

/** Runs the offline visual updater fixtures inside Android's normal app runtime. */
public final class VisualAssetsIntegration extends Instrumentation {
    @Override public void onCreate(Bundle arguments) { super.onCreate(arguments); start(); }
    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            File cache = new File(getTargetContext().getCacheDir(), "visual-assets-test-" + System.nanoTime());
            VisualAssetsTest.main(new String[]{cache.getAbsolutePath()});
            result.putString("result", "PASS " + VisualAssetsTest.assertions + " offline visual asset checks");
            finish(Activity.RESULT_OK, result);
        } catch (Throwable error) {
            result.putString("result", "FAIL " + error.toString());
            finish(Activity.RESULT_CANCELED, result);
        }
    }
}
