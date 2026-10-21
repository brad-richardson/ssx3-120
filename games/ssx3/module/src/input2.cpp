// split-input-1 #5: SSX 3 input path (`ssx3-input2`).
//
// Moved verbatim from ps2xRuntime/src/lib/ps2_runtime.cpp (INP2 block and
// `ssx3-input2` registration) into the game module as a table row; only
// the apply function gains external linkage. Generic runtime keeps no
// input2 policy.
#include "ps2_runtime.h"
#include "ps2_fh1_full120.h"
#include "ps2_fh1_input2.h"
#include "runtime/ee_guest_unwind.h"

#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace ps2_ssx3_input2
{
namespace
{
    PS2Runtime::RecompiledFunction g_input2Update = nullptr;
    PS2Runtime::RecompiledFunction g_inputChainBoundary = nullptr, g_inputChainResume = nullptr;
    void input2UpdateWrapper(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t bank = getRegU32(ctx, 4) & 0x1ffffffu, count = getRegU32(ctx, 5);
        const uint32_t ra = getRegU32(ctx, 31);
        uint32_t oldCount = 0u;
        if (count > 24u || bank > 0x2000000u-4u-24u*28u || !ps2_fh1::rd32(ram, bank, oldCount))
        {
            g_input2Update(ram, ctx, runtime);
            return;
        }
        std::array<ps2_fh1::Input2Record, 24> old{};
        for (uint32_t i = 0; i < std::min(count, oldCount); ++i)
            std::memcpy(&old[i], ram + bank + 4u + i*28u, 28u);
        const unsigned step = ps2_fh1::hooksOn() ? 1u : 2u;
        g_input2Update(ram, ctx, runtime);
        if (ctx->pc != ra || ps2_guest_unwind::pending()) return;
        for (uint32_t i = 0; i < count; ++i)
        {
            const uint32_t base = bank + 4u + i*28u;
            uint32_t rawBits = 0u;
            if (!ps2_fh1::rd32(ram, base, rawBits)) return;
            float raw; std::memcpy(&raw, &rawBits, 4u);
            ps2_fh1::input2Step(old[i], raw, step);
            old[i].raw = rawBits;
            std::memcpy(ram + base, &old[i], 28u);
        }
    }
    void inputChainBoundaryWrapper(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t ra = getRegU32(ctx, 31);
        g_inputChainBoundary(ram, ctx, runtime);
        if (ra != 0x133648u || ctx->pc != ra || ps2_guest_unwind::pending() ||
            !ps2_fh1::hooksOn() || !ps2_fh1::g_rngOdd) return;
        const uint32_t s = getRegU32(ctx, 16);
        uint32_t phase = 0u;
        if (!ps2_fh1::rd32(ram, s + 0xcu, phase)) return;
        std::array<uint32_t, 10> angles{};
        bool readable = true;
        for (uint32_t i = 0; i < angles.size(); ++i)
            readable &= ps2_fh1::rd32(ram, s + 0x10u + i*4u, angles[i]);
        // Neutral phase 3 admits input at the original guest boundary. Its
        // target stores must survive into pose/animation selection (134c54).
        // S+30/+34 retain sequence rotation after target/pose normalization,
        // so a completed spin still parks post-120 chaining, as in INP3.
        if (!readable || !ps2_fh1::inputChainNeutral(phase, angles))
            ps2_fh1::wr32(ram, s + 0xcu, ps2_fh1::inputChainPark(phase));
    }
    void inputChainResumeWrapper(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        // Restore before all downstream rate/pose/animation consumers, even
        // when this call resumes after a scheduler checkpoint.
        if (getRegU32(ctx, 31) == 0x133c48u)
        {
            const uint32_t s = getRegU32(ctx, 16);
            uint32_t phase = 0u;
            if (ps2_fh1::rd32(ram, s + 0xcu, phase))
                ps2_fh1::wr32(ram, s + 0xcu, ps2_fh1::inputChainRestore(phase));
        }
        g_inputChainResume(ram, ctx, runtime);
    }
} // namespace

void applyInput2(PS2Runtime &runtime)
{
    if (ps2_fh1::input2Fix())
    {
        g_input2Update = runtime.lookupFunction(0x321298u);
        if (!g_input2Update || !runtime.replaceFunction(0x321298u, &input2UpdateWrapper))
        {
            std::fprintf(stderr, "input2 refused: missing input updater\n");
            std::abort();
        }
        std::fprintf(stderr, "input2: digital stock clocks only\n");
    }
    if (ps2_fh1::inputChainFix())
    {
        g_inputChainBoundary = runtime.lookupFunction(0x114130u);
        g_inputChainResume = runtime.lookupFunction(0x14dc80u);
        if (!g_inputChainBoundary || !g_inputChainResume ||
            !runtime.replaceFunction(0x114130u, &inputChainBoundaryWrapper) ||
            !runtime.replaceFunction(0x14dc80u, &inputChainResumeWrapper))
        {
            std::fprintf(stderr, "inputchain refused: missing tracker boundaries\n");
            std::abort();
        }
        std::fprintf(stderr, "inputchain: stock directional recurrence; grabs/raw/animation stay 120\n");
    }
}

} // namespace ps2_ssx3_input2
