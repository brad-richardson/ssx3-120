// SPDX-License-Identifier: GPL-3.0-or-later OR MIT
// PM1 mac thread sampler: per-thread cumulative CPU + pthread names, no root.
//
// Usage: pm1_macthreads PID INTERVAL_S
// Prints one JSON row per interval to stdout:
//   {"wall_s": 12.0, "threads": [{"tid": 123, "name": "GameThread",
//                                 "user_ns": 1, "sys_ns": 2}]}
// pth_user/sys_time are NANOSECONDS (verified: a GIL-shared busy thread
// accumulates ~0.25e9 units per 0.5 s wall).
// Stops (rc 0) when PID exits. libproc only, ships with macOS.
#include <libproc.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/proc_info.h>
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
        uint64_t tids[1024];
        int nbytes = proc_pidinfo(pid, PROC_PIDLISTTHREADS, 0, tids, sizeof(tids));
        if (nbytes <= 0)
            return 0; // PID gone
        int n = nbytes / (int)sizeof(uint64_t);
        printf("{\"wall_s\": %.3f, \"threads\": [", w);
        int first = 1;
        for (int i = 0; i < n; i++) {
            struct proc_threadinfo pth;
            if (proc_pidinfo(pid, PROC_PIDTHREADINFO, tids[i], &pth,
                             sizeof(pth)) != (int)sizeof(pth))
                continue;
            char name[65];
            memcpy(name, pth.pth_name, 64);
            name[64] = 0;
            // JSON-escape the name (pthread names are simple, but be safe).
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
