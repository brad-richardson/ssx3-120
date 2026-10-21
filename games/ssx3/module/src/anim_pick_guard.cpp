// module-seam-1: moved verbatim from ps2xRuntime/src/lib/ps2_runtime.cpp
// (fork 1d7d4081); the wrappers and their state keep their names.
#include "ps2_ssx3_anim_pick_guard.h"
#include "ssx3_module_internal.h"

#include <atomic>
#include <cstdio>
#include <cstdlib>

// HNG2: animation-variant pick guard (PS2X_SSX3_ANIM_PICK_GUARD, default on;
// ps2_ssx3_anim_pick_guard.h). A zero-weight pick returns anim 0 instead of
// reaching the picker's divide-by-zero trap (break 7 -> 0x80000080 freeze);
// every other pick runs the original. "0": nothing is wrapped.
namespace
{
    PS2Runtime::RecompiledFunction g_animPick = nullptr;

    void animPickWrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        ps2_ssx3_anim_pick::Decision d = ps2_ssx3_anim_pick::Decision::Stock;
        const uint32_t event = getRegU32(ctx, 5), mask = getRegU32(ctx, 6);
        if (ps2_ssx3_anim_pick::guardEntry(rdram, ctx, &d))
        {
            static std::atomic<bool> logged{false};
            if (!logged.exchange(true))
                std::fprintf(stderr, "[hng2] zero-weight anim pick guarded event=%u mask=%u\n", event, mask);
            return;
        }
        if (d == ps2_ssx3_anim_pick::Decision::Unsafe)
        {
            static std::atomic<bool> logged{false};
            if (!logged.exchange(true))
                std::fprintf(stderr, "[hng2] zero-weight anim pick NOT guarded (anim %u not resident) event=%u mask=%u\n",
                             ps2_ssx3_anim_pick::kSafeAnim, event, mask);
        }
        g_animPick(rdram, ctx, runtime);
    }

    // HNG3: the same knob also wraps cRiderAnimBase_play (0x3128e8). An event
    // past the 437-entry AIAnimEventMap takes the wrapper's own "no animation"
    // exit ($v0 = 0x1B6) instead of indexing the record table out of range
    // (0x312994 address error -> 0x80000080 freeze; Brad 10-12 21:35).
    PS2Runtime::RecompiledFunction g_animPlay = nullptr;

    void animPlayWrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t event = getRegU32(ctx, 5);
        if (ps2_ssx3_anim_pick::guardPlayEntry(ctx))
        {
            static std::atomic<bool> logged{false};
            if (!logged.exchange(true))
                std::fprintf(stderr, "[hng3] out-of-range anim event guarded in play event=%u (table %u)\n", event,
                             ps2_ssx3_anim_pick::kEventCount);
            return;
        }
        g_animPlay(rdram, ctx, runtime);
    }
}

// Registered by the module table (ssx3_module.cpp) as "ssx3-anim-pick-guard".
void ps2_ssx3_anim_pick::applyAnimPickGuard(PS2Runtime &runtime)
{
    if (!ps2_ssx3_anim_pick::enabled())
    {
        std::fprintf(stderr, "[hng2] anim pick guard off (PS2X_SSX3_ANIM_PICK_GUARD=0): stock trap\n");
        return;
    }
    g_animPick = runtime.lookupFunction(ps2_ssx3_anim_pick::kPicker);
    if (!g_animPick || !runtime.replaceFunction(ps2_ssx3_anim_pick::kPicker, &animPickWrapper))
    {
        std::fprintf(stderr, "[hng2] cannot wrap 0x%x\n", ps2_ssx3_anim_pick::kPicker);
        std::abort();
    }
    g_animPlay = runtime.lookupFunction(ps2_ssx3_anim_pick::kPlay);
    if (!g_animPlay || !runtime.replaceFunction(ps2_ssx3_anim_pick::kPlay, &animPlayWrapper))
    {
        std::fprintf(stderr, "[hng3] cannot wrap 0x%x\n", ps2_ssx3_anim_pick::kPlay);
        std::abort();
    }
}
