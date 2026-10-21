#pragma once

// full120 slice 2: clock policy (VBlank/EE rate switch, stock-time
// accumulators, VBlank commit, manager word table and helpers) moved from
// ps2_fh1_full120.h to games/ssx3/module/src/fh1_clock.cpp. The runtime
// reaches every body through Module.fh1* fields (same-name wrappers in
// ps2_fh1_full120.h); this header declares the module-side entry points.

#include <cstdint>

namespace ps2_ssx3_fh1
{
uint32_t schedDivisor() noexcept;
uint32_t vblankDivisor() noexcept;
uint32_t eeClockShiftNow() noexcept;
uint64_t stockHalfTicks() noexcept;
uint64_t stockHalfTicksLegacy() noexcept;
uint32_t stockHalfStepNext() noexcept;
void onVBlankCommit(uint8_t *ram, uint64_t tick);
bool applyWords(uint8_t *ram, uint32_t a, bool toActive, uint64_t mainMask, uint32_t fix12Mask, bool playBuild);
void patchAtManagerInit(uint8_t *ram);
void inputChainPoolFlip(uint8_t *ram, bool toActive) noexcept;
void inputChainPoolStep(uint8_t *ram, bool odd) noexcept;
bool fh28PoolOverride(uint32_t address, uint64_t mainMask) noexcept;
}
