#ifndef SSX3_VIS_NATIVE_H
#define SSX3_VIS_NATIVE_H

// VNP1: native port of SSX 3's VU0 visibility test, micro program 0xDB8 of VU0
// image 40829a098c260b4f (the AABB/frustum test called by the terrain test
// 0x22A128 and the instance test 0x229FC8), plus the walker's 0x570 (VNP1P2).
// PS2X_SSX3_VIS_NATIVE unset/1 runs them in host code (default); =check runs
// both and compares; =0 keeps the VU0 engine (the reference path).
//
// split-input-1 #3 (Part 2): moved here from ps2xRuntime (verbatim); the
// runtime reaches it through the visNative Module hooks below (ABI 9), and
// the VU0 engine stays the no-module path.
//
// Contract: on return, every R5900Context field that executeVU0Microprogram's
// exact path exports (VF/VI/ACC/Q/P/I/R, MAC/status/clip, TPC/PC, ITOP, VPU
// stat) holds the same bits the engine leaves. The program writes no VU0 data
// memory, and VU0 runs charge no EE cycles. The engine's own scratch state
// (VU0Interpreter m_state/pipelines) is not updated: every VU0 start rewrites
// it before reading it, so only a savestate's raw VU0 unit bytes can tell
// (see local/research/VNP1/REPORT.md, contract table).

#include <cstdint>

#include "ps2_runtime.h"

struct VuState;

namespace ps2_ssx3_vis_native
{
    constexpr uint64_t kImageHash = 0x40829a098c260b4full;
    constexpr uint32_t kStartPc = 0xDB8u;
    // VNP1P2: the walker's AABB program (0xAC8's body with four leading NOP
    // pairs, no BAL; E at 0x6D0 so the end pc is 0x6E0; vi15 untouched).
    // Reached through VCALLMS (0x22a934). VF10-13/15/16 come from ctx.
    constexpr uint32_t kStartPc570 = 0x570u;

    // True when this build has the vector FMAC core the port mirrors.
    bool available();

    // The 4 KiB VU0 code image is the one the port was written for. Cached
    // per code generation (same identity rule as lookupRecompProgram).
    bool imageMatches(const uint8_t *vu0Code, uint64_t generation);

    // Runs program 0xDB8 on ctx with VU0 data memory vu0Data (4 KiB, read
    // only). Caller has checked imageMatches().
    void runDb8(R5900Context *ctx, const uint8_t *vu0Data);

    // Runs program 0x570 on ctx (reads no data memory). Caller has checked
    // imageMatches().
    void runP570(R5900Context *ctx);

    // Test hook (ps2x_tests): the runtime's exact VU0 export.
    void exportVu0StateForTest(const VuState &state, R5900Context *ctx);

    // Every R5900Context field a VU0 start reads or writes.
    struct Vu0Snapshot
    {
        __m128 vf[32];
        __m128 acc;
        __m128 r;
        uint16_t vi[16];
        float q, p, i;
        uint16_t status;
        uint32_t mac, clip, clip2, vpuStat, vpuStat2, tpc, itop, pc, fbrst;
    };
    Vu0Snapshot snapshot(const R5900Context &ctx);
    void restore(R5900Context &ctx, const Vu0Snapshot &s);

    // Bitwise compare; prints each differing field when print is set.
    bool equal(const Vu0Snapshot &ref, const Vu0Snapshot &got, bool print);

    // check mode: counts calls and mismatches, prints the first mismatches
    // with their inputs and a periodic summary (stderr, "[vnp1]").
    void noteCheck(uint32_t startPc, const Vu0Snapshot &in, const Vu0Snapshot &ref, const Vu0Snapshot &got);

    // Module-hook providers (ABI 9): the VU0-start dispatch the runtime
    // calls at executeVU0Microprogram. mode() reads PS2X_SSX3_VIS_NATIVE
    // once (0 = engine, 1 = serve in host code, 2 = both, compared);
    // matchesPc covers both programs; run serves mode 1; checkBegin runs
    // the host copy around the engine run (snapshots held module-side),
    // checkEnd compares and reports.
    int visNativeMode();
    bool visNativeMatchesPc(uint32_t startPC);
    void visNativeRun(R5900Context *ctx, const uint8_t *vu0Data, uint32_t startPC);
    void visNativeCheckBegin(R5900Context *ctx, const uint8_t *vu0Data, uint32_t startPC);
    void visNativeCheckEnd(R5900Context *ctx, uint32_t startPC);
}

#endif // SSX3_VIS_NATIVE_H
