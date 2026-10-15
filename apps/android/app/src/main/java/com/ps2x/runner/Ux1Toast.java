package com.ps2x.runner;

import android.app.Activity;
import android.widget.Toast;

// UX1: quick-save/load feedback (DS1 gap §2). Called from native
// (ps2_android_toast.cpp) on a worker thread; hops to the UI thread, so the
// caller never blocks. Anonymous Runnable: no desugar dependency.
public final class Ux1Toast {
    private Ux1Toast() {}

    public static void show(final Activity activity, final String text) {
        if (activity == null) {
            return;
        }
        final String msg = text == null ? "" : text;
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                Toast.makeText(activity, msg, Toast.LENGTH_SHORT).show();
            }
        });
    }
}
