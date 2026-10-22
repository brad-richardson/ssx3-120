// flyover-2: skip Tricky's donor flyover via the guest's own transition.
//
// On Tricky courses the pre-race intro plays the donor world's flyover over
// Tricky geometry. Instead of showing it (or holding a camera), advance the
// intro phase machine exactly as the "Press X to skip" handler does: the
// dispatcher (sub_00230F40) treats [race+0x210] as a transition selector, so
// writing 2 during phase 1 runs the normal phase-1 exit / phase-2 enter path
// (info card) and the machine clears the selector. No synthetic pad input;
// Script updates, input, countdown, music and announcer are untouched. The
// card still waits for the player's X, so countdown/GO timing is unchanged.
//
// tk-alaska-opus-1: phase 1 also runs the pre-race memory-card check, which
// sets the LoadIndicator's bit 2 (the in-race "Loading..." icon) in its update
// and clears it only as the check runs to its end. The skip exits phase 1 after
// one update, so the bit stayed on for the whole race. Only that update sets
// bit 2, so once the machine has left phase 1 the skip clears it the way the
// guest's ClearBit (sub_001F36D8) does.
#include "Ssx3TrickyIntro.h"
#include "ps2_runtime.h"
#include "ps2_ssx3_tricky_list.h"
#include "runtime/ps2_memory.h"

#include <cstdlib>
#include <cstring>

namespace
{
constexpr uint32_t kCompose = 0x0015e668u;
constexpr uint32_t kRaceVtable = 0x0047d110u; // in-race phase machine (owns +0x94 LoadIndicator)
PS2Runtime::RecompiledFunction g_compose = nullptr;

bool inRam(uint32_t address, uint32_t size)
{
    return address >= 0x1000u && !(address & 3u) && address <= PS2_RAM_SIZE - size;
}

uint32_t read32(const uint8_t *ram, uint32_t address)
{
    uint32_t value = 0;
    if (inRam(address, 4u))
        std::memcpy(&value, ram + address, sizeof(value));
    return value;
}

void skipTrickyFlyover(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
{
    // A mapped donor world means a Tricky ride is loading (direct launch and
    // the navigator both pin the course world here; stock never maps). The
    // manifest mode reader cannot be used: the launch overwrites the donor
    // event's mode row, so it reports Stock on most courses.
    if (ram && ps2_ssx3_tricky_list::mapState().activeEvent >= 0)
    {
        const uint32_t race = read32(ram, read32(ram, 0x004a28a8u) + 0x84u);
        const uint32_t phase = read32(ram, race + 0x214u);
        const uint32_t indicator = read32(ram, race + 0x94u);
        if (phase != 1u && read32(ram, race) == kRaceVtable && inRam(indicator + 4u, 8u) &&
            (read32(ram, indicator + 4u) & (1u << 2)))
        {
            const uint32_t mask = read32(ram, indicator + 4u) & ~(1u << 2);
            std::memcpy(ram + indicator + 4u, &mask, sizeof(mask));
            if (mask == 0u)
                std::memset(ram + indicator + 8u, 0, 4u); // spin timer
        }
        if (phase == 1u && inRam(race + 0x210u, 4u))
        {
            // Phase 1 (flyover) on a Tricky course: request the card through
            // the guest transition selector. Fires once: the machine moves to
            // phase 2 on the next dispatch and clears the selector, so later
            // ticks see phase != 1 and chain through untouched.
            const uint32_t card = 2u;
            std::memcpy(ram + race + 0x210u, &card, sizeof(card));
        }
    }
    g_compose(ram, ctx, runtime);
}
} // namespace

namespace ps2_ssx3_tricky_intro
{
void applyTrickyFixedIntro(PS2Runtime &runtime)
{
    const char *setting = std::getenv("PS2X_SSX3_TRICKY_FIXED_INTRO");
    if (setting && std::strcmp(setting, "0") == 0)
        return;
    const uint8_t *ram = runtime.memory().getRDRAM();
    if (!ram || read32(ram, kCompose) != 0x27bdfeb0u ||
        read32(ram, kCompose + 4u) != 0xe7b60130u)
        return;
    g_compose = runtime.lookupFunction(kCompose);
    if (g_compose)
        runtime.replaceFunction(kCompose, &skipTrickyFlyover);
}
} // namespace ps2_ssx3_tricky_intro
