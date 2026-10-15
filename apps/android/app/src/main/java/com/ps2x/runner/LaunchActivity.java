package com.ps2x.runner;

import android.app.Activity;
import android.app.ActivityManager;
import android.app.NativeActivity;
import android.content.Intent;
import android.graphics.Point;
import android.os.Bundle;
import android.system.ErrnoException;
import android.system.Os;
import android.util.Log;
import android.widget.Toast;

// TG1: launcher trampoline behind the launcher entries ("SSX 3 · 60",
// "SSX 3 · 120" = the Launch120 alias). It runs before the runner library
// loads: for 120 it sets PS2X_LAUNCH_HZ=120 in the process env, and the
// native env loader (ps2_android_runtime.cpp, a static initializer that runs
// when NativeActivity loads the library) then layers files/full120.env over
// files/ps2x.env. The 60 entry sets nothing, so it boots on today's env.
// TKM1: the TKO1 "SSX 3 · Tricky" entry is gone; the Tricky layer is always
// on in the base env. Nothing sets PS2X_LAUNCH_LAYER anymore.
// The runtime reads the env once per process: switching needs the game
// closed first (swipe it away in Recents).
public final class LaunchActivity extends Activity {
    private static final String TAG = "ps2x";
    // Target the runtime in this process was started with (null = not yet).
    private static String sStartedTarget;

    private static String targetFor(String className) {
        if (className.endsWith(".Launch120")) {
            return "120";
        }
        return "60";
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        final String target = targetFor(getComponentName().getClassName());
        if (sStartedTarget == null) {
            try {
                if ("120".equals(target)) {
                    Os.setenv("PS2X_LAUNCH_HZ", "120", true);
                } else {
                    Os.unsetenv("PS2X_LAUNCH_HZ");
                }
                // The public package asks the native GS for one device-tier decision.
                // Play builds keep their explicit env and compiled defaults.
                if (!"com.ps2x.runner".equals(getPackageName())) {
                    final ActivityManager manager = (ActivityManager) getSystemService(ACTIVITY_SERVICE);
                    final ActivityManager.MemoryInfo memory = new ActivityManager.MemoryInfo();
                    manager.getMemoryInfo(memory);
                    final Point panel = new Point();
                    getWindowManager().getDefaultDisplay().getRealSize(panel);
                    Os.setenv("PS2X_PUBLIC_APP", "1", true);
                    Os.setenv("PS2X_PUBLIC_RAM_BYTES", Long.toString(memory.totalMem), true);
                    Os.setenv("PS2X_PUBLIC_PANEL", panel.x + "x" + panel.y, true);
                }
                sStartedTarget = target;
            } catch (ErrnoException e) {
                Log.e(TAG, "TG1 launch: setenv failed", e);
            }
            Log.i(TAG, "TG1 launch: target " + target);
        } else if (!sStartedTarget.equals(target)) {
            Log.i(TAG, "TG1 launch: asked for " + target + ", already running at " + sStartedTarget);
            Toast.makeText(getApplicationContext(),
                    "SSX 3 is running at " + sStartedTarget + ". Close it in Recents to switch to " + target + ".",
                    Toast.LENGTH_LONG).show();
        }
        // singleTask: brings a running game forward instead of starting a
        // second runtime in this process.
        final Intent game = new Intent(this, NativeActivity.class);
        game.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(game);
        finish();
    }
}
