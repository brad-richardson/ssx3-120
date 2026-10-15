#!/system/bin/sh
# PG5 GEN builds only (gradle adds this dir when -Pps2xPgoGenerate=ON): PGO
# profile path for the instrumented APK. PLAIN path (no %c: NDK r28's
# continuous mode produced unmergeable files, PG1); the pgo_flush helper
# thread flushes the buffered profile every 30 s (the host ends runs with
# force-stop/SIGKILL, no atexit flush). Needs android:debuggable (GEN only).
export LLVM_PROFILE_FILE=/data/user/0/com.ps2x.runner/files/pg2.profraw
exec "$@"
