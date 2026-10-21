#pragma once

// split-input-1 #5: SSX 3 input marker (`ssx3-input-marker`, LIM1/LIM2).
// The game module owns the marker state, the guest-call latch wrappers,
// the per-read tap (notePadRead) and the test hooks below. The runtime
// keeps only the hook call at the pad-read site plus the generic padrec
// stamp; hoisted generic services (script clock, record-header utils)
// stay declared in Stubs/Pad.h.
#include <cstddef>
#include <cstdint>

class PS2Runtime;

namespace ps2_stubs
{
    // Table apply ("ssx3-input-marker"): arms from PS2X_INPUT_MARKER and
    // wraps the tracker/gate/input2/sel/award/boost call sites when set.
    void lim1Apply(PS2Runtime &runtime);
    // Per-read tap after the record tap; scalars only. True hands the
    // trigger ms back for the runtime padrec stamp. Null (or no module)
    // skips the marker.
    bool notePadRead(uint16_t buttons, uint8_t lx, uint8_t ly, uint8_t rx, uint8_t ry,
                     uint64_t guestVsyncTick, int port, int slot, const uint8_t *hookRdram,
                     uint64_t *markMsOut);
    // Test hooks (moved from Stubs/Pad.h): arm on an explicit dir, drive
    // the tick, inject latch samples, point the read-time input2 snapshot
    // at test RAM, finalize pending triggers or reset. Production code
    // paths never call these.
    bool setPadMarkerDirForTest(const char *dir);
    void setPadMarkerTickForTest(uint64_t tick);
    void setPadMarkerTrackerForTest(uint32_t s, uint32_t in0, uint32_t grab, uint32_t phase,
                                    uint32_t target);
    void setPadMarkerGateForTest(uint32_t a0, uint32_t v0);
    void setPadMarkerInput2ForTest(uint32_t bank, uint32_t count);
    void setPadMarkerRdramForTest(const uint8_t *rdram);
    void setPadMarkerSelForTest(uint32_t p, uint32_t sel, uint32_t ctl);
    void setPadMarkerAwardForTest(uint64_t n, uint64_t tick, uint32_t a1);
    void setPadMarkerBoostForTest(uint64_t n, uint64_t tick, uint32_t amt);
    void closePadMarkerForTest();
    void clearPadMarkerForTest();
} // namespace ps2_stubs
