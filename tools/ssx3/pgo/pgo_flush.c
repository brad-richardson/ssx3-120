/* Optional Android local PGO training: flush every 30 s before process kill.
 * Compile only with -fprofile-generate; outputs remain private. */
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
