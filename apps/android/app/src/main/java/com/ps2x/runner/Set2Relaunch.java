// SPDX-License-Identifier: GPL-3.0-or-later OR MIT
package com.ps2x.runner;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;
import android.os.Process;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;

// SET2: the in-game settings panel's Restart. Persists the chosen mode to
// files/launch-hz (default 120 when absent), fires the single launcher
// alias (.Launch120) as a fresh task, then kills this process so the
// relaunch boots clean (the runtime reads its env once per process).
// Called from native (set2AndroidRelaunch) after the panel writes its
// settings; on success this method never returns.
public final class Set2Relaunch {
    private static final String TAG = "ps2x";

    static void writeLaunchHz(Activity activity, String target) {
        String hz = LaunchActivity.HZ_60.equals(target)
                ? LaunchActivity.HZ_60 : LaunchActivity.HZ_120;
        File dir = activity.getExternalFilesDir(null);
        if (dir == null) {
            Log.e(TAG, "launch-hz: no files dir, mode not persisted");
            return;
        }
        try (FileOutputStream out = new FileOutputStream(new File(dir, LaunchActivity.HZ_FILE))) {
            out.write(hz.getBytes(StandardCharsets.UTF_8));
            out.getFD().sync();
        } catch (IOException | SecurityException e) {
            Log.e(TAG, "launch-hz: write failed", e);
        }
    }

    public static void toHz(Activity activity, String target) {
        writeLaunchHz(activity, target);
        final Intent game = new Intent();
        game.setComponent(new ComponentName(activity.getPackageName(),
                LaunchActivity.class.getPackage().getName() + ".Launch120"));
        game.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK);
        activity.startActivity(game);
        Process.killProcess(Process.myPid());
        System.exit(0);
    }
}
