// SPDX-License-Identifier: GPL-3.0-or-later
// OM1 Part 4: offline microVU core API (runtime-owned; the bridge calls it).
// Uppercase markers: OM1P4_CORE.
#pragma once

#include <cstdint>
#include <string>

enum OlcDisposition
{
    OLC_SERVED = 1, // job fully executed; bridge exports state
    OLC_MISS = 0,   // lookup/resolve/trap miss; live VU canonicalized,
                    // caller must restart the job in the static engine
};

// No-JIT bring-up (data mappings, mVUinit state, table install, trap/TEXT
// wiring). Arms the codegen-abort guards first; any compile attempt aborts.
bool olc_init(std::string& err);
void olc_shutdown();

// Execute-mirror core. TPC arrives as an INDEX (bridge/seed set it, as the
// bench seed did). On MISS the live VU is canonicalized (fresh-init state)
// so the next offline job starts clean; the caller restarts this job static.
// OBE1: `backup` (may be null) asks the core to copy `size` bytes from `src` to `dst` after the entry resolves and
// before any recorded code runs. With PS2X_OM1_CERT=1 the copy is skipped when the entry is certified: the recorded
// code reachable from it holds no trap site (om1_jump_resolve / om1_fallthrough_miss) and no unresolved indirect
// branch, so the run can only end SERVED. Entry misses never execute code, so they need no copy either.
struct OlcBackup
{
    uint8_t* dst;
    const uint8_t* src;
    uint32_t size;
    bool taken; // out: the copy was made for this run
    bool certified; // out: the entry was certified (copy skipped)
};
OlcDisposition olc_execute(uint32_t budget, OlcBackup* backup);

// OM2: true while a served offline run left a cycle-budget-break resume
// parked (and the resume path is enabled). The bridge skips its per-run
// re-seed on that resume, like MV2 (which seeds once): live state is the
// break's own, and the export/import round trip loses JIT-private parts of
// it (pending_q/pending_p, the four flag instances).
bool olc_resume_parked();
// IV1 prototype: PS2X_OM1_LEAN=1 (default off) = no signal-mask save on the per-job trap arm, chunked code diff,
// shared VU1 data (the library exports VU1.Mem; run() skips both 16 KiB staging copies when data == VU1.Mem).
bool olc_lean();
int olc_lean_level();           // 0 off, 1 shared data + per-job backup (exact restart), 2 shared, no backup
bool olc_last_miss_was_trap(); // the last MISS came from a mid-run jump/fall trap (VU1.Mem may be mutated)

// OBE1: PS2X_OM1_CERT=1 (default off) = skip the LEAN=1 per-job backup for certified entries.
bool olc_cert_enabled();
// OBE1 test knob: PS2X_OM1_FORCE_JUMP_MISS=N (default 0 = off) turns every Nth om1_jump_resolve call into a jump
// miss (a mid-run trap after the program has run), to exercise the backup restore on a real route.
// OBE1 counters: backups taken, backups skipped on certified entries, bytes copied, certifier scans and scanned words.
void olc_backup_stats(uint64_t& taken, uint64_t& elided, uint64_t& bytes, uint64_t& scans, uint64_t& words,
                      uint64_t& forced);

// Counters for the det-gate miss/restart report.
void olc_stats(uint64_t& served, uint64_t& fallback, uint64_t& miss, uint64_t& parks,
               uint64_t& jumpMisses, uint64_t& fallMisses);

// Log helper: stderr always, plus om1.log when a log dir applies
// ($PS2X_OM1_LOGDIR, else $HOME/Documents on iOS). iOS has no retrievable
// console, so the miss counts and footprint go to the file.
void olc_log(const char* fmt, ...);

// Process phys_footprint in bytes (Apple only; 0 elsewhere).
uint64_t olc_footprint();
