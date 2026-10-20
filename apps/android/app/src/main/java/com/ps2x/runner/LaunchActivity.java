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

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;

// TG1: launcher trampoline behind the single launcher entry ("SSX 3").
// It runs before the runner library loads: the persisted mode (see
// Set2Relaunch, files/launch-hz, default 120) decides whether
// PS2X_LAUNCH_HZ=120 is set in the process env, and the native env loader
// (ps2_android_runtime.cpp, a static initializer that runs when
// NativeActivity loads the library) then layers files/full120.env over
// files/ps2x.env. The 60 mode sets nothing, so it boots on today's env.
// TKM1: the TKO1 "SSX 3 Tricky" entry is gone; the Tricky layer is always
// on in the base env. Nothing sets PS2X_LAUNCH_LAYER anymore.
// The runtime reads the env once per process: switching needs the game
// closed first (swipe it away in Recents).
public final class LaunchActivity extends Activity {
    private static final String TAG = "ps2x";
    // Target the runtime in this process was started with (null = not yet).
    private static String sStartedTarget;

    static final String HZ_FILE = "launch-hz";
    static final String HZ_120 = "120";
    static final String HZ_60 = "60";

    private static String readAll(InputStream stream) throws IOException {
        try (InputStream in = stream; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[64];
            int n;
            while ((n = in.read(buffer)) != -1) out.write(buffer, 0, n);
            return out.toString("UTF-8");
        }
    }

    static String readLaunchHz(File filesDir) {
        if (filesDir == null) return HZ_120;
        File hz = new File(filesDir, HZ_FILE);
        try {
            if (!hz.isFile()) return HZ_120;
            return HZ_60.equals(readAll(new FileInputStream(hz)).trim()) ? HZ_60 : HZ_120;
        } catch (IOException | SecurityException e) {
            Log.e(TAG, "launch-hz: unreadable, defaulting to 120", e);
            return HZ_120;
        }
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        final String target = readLaunchHz(getExternalFilesDir(null));
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
