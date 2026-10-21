#pragma once

#include <cstdint>

// full120 slices 3+4: dispatch cold side (hook-interest table content)
// and hot side (branch-chain body) moved from ps2_fh1_full120.h to
// games/ssx3/module/src/fh1_dispatch.cpp. The runtime reads them through
// Module.fh1BuildHookInterest / Module.fh1OnBranch; this header declares
// the module-side entry points. Config gathering, the table cache, the
// refused guard, the lean-front filter and the call sites stay
// runtime-side; inline state, flag readers, hook bodies and the k* PC
// constants stay runtime-side until their last consumers move (later
// slices).

namespace ps2_fh1
{
struct HookConfig;
struct HookInterest;
} // namespace ps2_fh1

struct R5900Context;
class PS2Runtime;

namespace ps2_ssx3_fh1
{
void buildHookInterest(const ps2_fh1::HookConfig *cfg, ps2_fh1::HookInterest *out);
bool onBranch(uint8_t *ram, R5900Context *ctx, uint32_t sourcePc, uint32_t targetPc, PS2Runtime *runtime,
              bool fast);
void postUnwindRestore(uint8_t *ram) noexcept;
void onReturn(uint8_t *ram, R5900Context *ctx, uint32_t targetPc, bool returned);
}
