// split-input-1 #7: SSX 3 widescreen default (`ssx3-widescreen-default`).
//
// Moved verbatim from ps2xRuntime/src/lib/ps2_runtime.cpp (option-word
// state, apply, enforce, registration) into the game module: the
// `ssx3-widescreen-default` registration becomes a table row, and the
// two runtime read sites (0x228C08 dispatch enforce, present-setup
// anamorphic query) become Module hooks. Default behaviour unchanged:
// the row arms from PS2X_WIDESCREEN (default anamorphic) on every SSX 3
// boot, exactly as before.
#include "Ssx3Widescreen.h"
#include "ps2_present_geometry.h"
#include "ps2_runtime.h"

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>

namespace ps2_ssx3_widescreen
{
namespace
{
    std::atomic<bool> g_ssx3WidescreenActive{false};
    std::atomic<uint32_t> g_ssx3WidescreenMode{2u};
    constexpr uint32_t kSsx3OptionsWord = 0x00535610u;
    constexpr uint32_t kSsx3WidescreenMask = 0x00300000u;
} // namespace

void applySsx3Widescreen(PS2Runtime &)
{
    const char *env = std::getenv("PS2X_WIDESCREEN");
    const uint32_t mode = ps2x::present::ssx3WidescreenModeFromEnv(env);
    g_ssx3WidescreenMode.store(mode, std::memory_order_relaxed);
    g_ssx3WidescreenActive.store(true, std::memory_order_release);
    std::fprintf(stderr, "[widescreen] SSX3 mode=%u (PS2X_WIDESCREEN=%s)\n",
                 mode, env ? env : "default");
}

// SLUS_207.72 stores its video choice in bits 20-21 of the first options
// word. 0 is 4:3 and 2 is the menu's anamorphic choice. The game calls
// 0x228C08 to apply that choice after defaults, menu edits, and profile
// loading. Interpose at that call so all three paths obey the host option.
void noteWidescreenDispatch(uint8_t *rdram, uint32_t sourcePc)
{
    if (!rdram || !g_ssx3WidescreenActive.load(std::memory_order_acquire)) return;
    uint32_t word = 0u;
    std::memcpy(&word, rdram + kSsx3OptionsWord, sizeof(word));
    const uint32_t mode = g_ssx3WidescreenMode.load(std::memory_order_relaxed);
    const uint32_t wanted = (word & ~kSsx3WidescreenMask) | (mode << 20u);
    if (word == wanted) return;
    std::memcpy(rdram + kSsx3OptionsWord, &wanted, sizeof(wanted));
    std::fprintf(stderr, "[widescreen] apply source=0x%x guest_mode=%u host_mode=%u\n",
                 sourcePc, (word >> 20u) & 3u, mode);
}

// Reports the host-requested anamorphic default for the present path.
bool widescreenAnamorphic()
{
    return g_ssx3WidescreenActive.load(std::memory_order_acquire) &&
           g_ssx3WidescreenMode.load(std::memory_order_relaxed) == 2u;
}

} // namespace ps2_ssx3_widescreen
