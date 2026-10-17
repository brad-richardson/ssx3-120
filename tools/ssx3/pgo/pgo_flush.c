/* Optional Android local PGO training: periodic flush before process kill.
 *
 * Staged via -Pps2xPgoHelperDir=<this dir>; compile only with
 * -fprofile-generate. Profiles stay private (regenerate after guest-code
 * changes).
 *
 * Why a helper thread (PG2 GEN lineage): device runs end with force-stop
 * (SIGKILL, no atexit flush), and continuous-mode (%c) profiles came back
 * unmergeable (0-byte / "truncated profile data"). So GEN uses a PLAIN
 * LLVM_PROFILE_FILE (no %c) and flushes the standard buffered profile every
 * 30 s from a helper thread; the last flush before force-stop wins (loses
 * <= 30 s of counts). No guest interaction.
 */
#include <pthread.h>
#include <unistd.h>

/* Provided by libclang_rt.profile (linked via -fprofile-generate). */
extern int __llvm_profile_write_file(void);

static void *pg2_pgo_flusher(void *arg)
{
    (void)arg;
    for (;;)
    {
        sleep(30);
        __llvm_profile_write_file();
    }
    return 0;
}

__attribute__((constructor)) static void pg2_pgo_start_flusher(void)
{
    pthread_t t;
    if (pthread_create(&t, 0, pg2_pgo_flusher, 0) == 0)
        pthread_detach(t);
}
