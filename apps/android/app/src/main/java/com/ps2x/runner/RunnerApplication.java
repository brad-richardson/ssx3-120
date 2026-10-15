package com.ps2x.runner;

import android.app.Application;
import android.content.res.AssetManager;
import android.system.Os;
import android.util.Log;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HashSet;
import java.util.Set;

// Runs for launcher aliases AND direct NativeActivity harness launches, before
// native env initialization. Only app-pack is managed; external play data is not.
public final class RunnerApplication extends Application {
    private static final String TAG = "ps2x";

    private static byte[] readAll(InputStream stream) throws IOException {
        try (InputStream in = stream; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[65536];
            int n;
            while ((n = in.read(buffer)) != -1) out.write(buffer, 0, n);
            return out.toByteArray();
        }
    }

    private static String hex(byte[] bytes) {
        StringBuilder out = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) out.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
        return out.toString();
    }

    // Resolve once and refuse symlinks or anything outside the literal private
    // app-pack child. Deletion is limited to that hash directory's direct files.
    private static void retire(File root, String hash) throws IOException {
        if (!hash.matches("[0-9a-f]{64}")) return;
        File old = new File(root, hash).getAbsoluteFile();
        if (!old.equals(old.getCanonicalFile())) throw new IOException("Pack symlink: " + old);
        if (!old.isDirectory()) return;
        File[] files = old.listFiles();
        if (files == null) throw new IOException("Cannot list " + old);
        for (File f : files) {
            if (!f.getAbsoluteFile().equals(f.getCanonicalFile()) || !f.isFile())
                throw new IOException("Unexpected pack entry: " + f);
        }
        for (File f : files) if (!f.delete()) throw new IOException("Cannot remove " + f);
        if (!old.delete()) throw new IOException("Cannot remove " + old);
    }

    @Override
    public void onCreate() {
        super.onCreate();
        // Public adb staging starts this process once before pushing resources.
        // Android 15 denies the app access to shell-created directories in its
        // external tree, so create writable data/card directories as the app.
        File external = getExternalFilesDir(null);
        File ge1Data = external == null ? null : new File(external, "ge1-data");
        File card = external == null ? null : new File(external, "mc0");
        if (external == null || !external.isDirectory()
                || (!ge1Data.mkdirs() && !ge1Data.isDirectory())
                || (!card.mkdirs() && !card.isDirectory())) {
            throw new IllegalStateException("Cannot prepare external app directories");
        }
        final long start = android.os.SystemClock.elapsedRealtime();
        try {
            final AssetManager assets = getAssets();
            final byte[] manifest;
            try {
                manifest = readAll(assets.open("remaster/MANIFEST"));
            } catch (FileNotFoundException absent) {
                // Ordinary upstream builds carry no private pack.
                return;
            }
            String hash = hex(MessageDigest.getInstance("SHA-256").digest(manifest));
            File root = new File(getFilesDir().getCanonicalFile(), "app-pack");
            if (!root.equals(root.getCanonicalFile()))
                throw new IOException("Pack root symlink: " + root);
            File pack = new File(root, hash);
            File complete = new File(pack, "MANIFEST");
            File current = new File(root, "current");
            boolean cached = complete.isFile() && java.util.Arrays.equals(
                manifest, readAll(new FileInputStream(complete)));
            int count = 0;
            Set<String> names = new HashSet<>();
            if (!pack.getAbsoluteFile().equals(pack.getCanonicalFile()))
                throw new IOException("Pack symlink: " + pack);
            if (!cached && !pack.isDirectory() && !pack.mkdirs())
                throw new IOException("Cannot create " + pack);
            for (String line : new String(manifest, StandardCharsets.UTF_8).split("\\n")) {
                if (line.isEmpty() || line.startsWith("#")) continue;
                if (!line.matches("[0-9a-f]{64}  [0-9a-f-]+\\.(png|dds)"))
                    throw new IOException("Invalid pack manifest entry");
                String name = line.substring(66);
                if (!names.add(name)) throw new IOException("Duplicate pack entry: " + name);
                count++;
                if (cached) continue;
                File output = new File(pack, name);
                if (!output.getAbsoluteFile().equals(output.getCanonicalFile()))
                    throw new IOException("Pack symlink: " + output);
                MessageDigest digest = MessageDigest.getInstance("SHA-256");
                try (InputStream in = assets.open("remaster/" + name);
                     FileOutputStream out = new FileOutputStream(output)) {
                    byte[] buffer = new byte[65536];
                    int n;
                    while ((n = in.read(buffer)) != -1) {
                        digest.update(buffer, 0, n);
                        out.write(buffer, 0, n);
                    }
                    out.getFD().sync();
                }
                if (!hex(digest.digest()).equals(line.substring(0, 64)))
                    throw new IOException("Pack SHA mismatch: " + name);
            }
            if (!cached) {
                try (FileOutputStream out = new FileOutputStream(complete)) {
                    out.write(manifest);
                    out.getFD().sync();
                }
            }
            String previous = current.isFile() ? new String(readAll(new FileInputStream(current)),
                StandardCharsets.US_ASCII).trim() : "";
            if (!previous.equals(hash)) {
                if (!previous.isEmpty()) retire(root, previous);
                try (FileOutputStream out = new FileOutputStream(current)) {
                    out.write(hash.getBytes(StandardCharsets.US_ASCII));
                    out.getFD().sync();
                }
            }
            Os.setenv("PS2X_APP_PACK", pack.getAbsolutePath(), true);
            Log.i(TAG, "app-pack: " + (cached ? "cached" : "extracted") + " files=" + count
                + " ms=" + (android.os.SystemClock.elapsedRealtime() - start)
                + " hash=" + hash + " path=" + pack.getAbsolutePath());
        } catch (Exception failure) {
            Log.e(TAG, "app-pack: failed", failure);
            throw new IllegalStateException("Bundled pack preparation failed", failure);
        }
    }
}
