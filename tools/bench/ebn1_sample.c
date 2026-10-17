// EBN1 sampler: per-thread cumulative CPU + process instructions/cycles, no root.
//
// Usage: ebn1_sample PID INTERVAL_S
// Prints one JSON row per interval to stdout:
//   {"wall_s": 12.0, "ri_instructions": 1, "ri_cycles": 2,
//    "threads": [{"tid": 123, "name": "GameThread", "user_ns": 1, "sys_ns": 2}]}
// user/sys are pth_user/system_time nanoseconds (as in pm1_macthreads.c).
// ri_instructions/ri_cycles are proc_pid_rusage RUSAGE_INFO_V4 counters
// (Apple Silicon; 0 when unavailable). Stops (rc 0) when PID exits.
// libproc only, ships with macOS.
#include <libproc.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/proc_info.h>
#include <sys/resource.h>
#include <sys/time.h>
#include <unistd.h>

static double now_s(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec + tv.tv_usec / 1e6;
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s PID INTERVAL_S\n", argv[0]);
        return 2;
    }
    int pid = atoi(argv[1]);
    double interval = atof(argv[2]);
    if (pid <= 0 || interval <= 0)
        return 2;
    double t0 = now_s();
    for (;;) {
        double w = now_s() - t0;
        uint64_t ins = 0, cyc = 0;
        struct rusage_info_v4 ru;
        if (proc_pid_rusage(pid, RUSAGE_INFO_V4, (rusage_info_t *)&ru) == 0) {
            ins = ru.ri_instructions;
            cyc = ru.ri_cycles;
        }
        uint64_t tids[2048];
        int nbytes = proc_pidinfo(pid, PROC_PIDLISTTHREADS, 0, tids, sizeof(tids));
        if (nbytes <= 0)
            return 0; // PID gone
        int n = nbytes / (int)sizeof(uint64_t);
        printf("{\"wall_s\": %.3f, \"ri_instructions\": %llu, \"ri_cycles\": %llu, \"threads\": [",
               w, (unsigned long long)ins, (unsigned long long)cyc);
        int first = 1;
        for (int i = 0; i < n; i++) {
            struct proc_threadinfo pth;
            if (proc_pidinfo(pid, PROC_PIDTHREADINFO, tids[i], &pth,
                             sizeof(pth)) != (int)sizeof(pth))
                continue;
            char name[65];
            memcpy(name, pth.pth_name, 64);
            name[64] = 0;
            char esc[130];
            int j = 0;
            for (int k = 0; name[k] && j < (int)sizeof(esc) - 2; k++) {
                if (name[k] == '"' || name[k] == '\\')
                    esc[j++] = '\\';
                esc[j++] = name[k];
            }
            esc[j] = 0;
            printf("%s{\"tid\": %llu, \"name\": \"%s\", \"user_ns\": %llu, \"sys_ns\": %llu}",
                   first ? "" : ", ", (unsigned long long)tids[i], esc,
                   (unsigned long long)pth.pth_user_time,
                   (unsigned long long)pth.pth_system_time);
            first = 0;
        }
        printf("]}\n");
        fflush(stdout);
        usleep((useconds_t)(interval * 1e6));
    }
}
