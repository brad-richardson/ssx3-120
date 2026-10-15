package com.ps2x.runner;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;
import android.os.Process;

// SET2: the in-game settings panel's Restart. Fires the matching launcher
// alias (.Launch120/.Launch60) as a fresh task, then kills this process so
// the relaunch boots clean (the runtime reads its env once per process).
// Called from native (set2AndroidRelaunch) after the panel writes its
// settings; on success this method never returns.
public final class Set2Relaunch {
    public static void toHz(Activity activity, String target) {
        final String alias = "120".equals(target) ? ".Launch120" : ".Launch60";
        final Intent game = new Intent();
        game.setComponent(new ComponentName(activity.getPackageName(),
                LaunchActivity.class.getPackage().getName() + alias));
        game.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK);
        activity.startActivity(game);
        Process.killProcess(Process.myPid());
        System.exit(0);
    }
}
