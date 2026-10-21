// snd-seam-1 Part 5 slice 2: stock-meter suppression on Tricky courses,
// moved verbatim from ps2xRuntime/src/lib/ps2_runtime.cpp (the
// `ssx3-tricky-stock-meter` override). The layer policy stays runtime-side;
// `ps2_ssx3_tricky_layer.h` is included read-only, never edited.

// tk-meter-1: the stock SSX 3 meter is not drawn on Tricky courses. SSX 3's
// HUD-1P renderer (cOVStateHUD1P_onRender2D, 0x1EC3F8) draws the meter
// through three helpers; while the Tricky meter replaces it
// (ps2_ssx3_tricky_layer::suppressStockMeter) the wrappers below skip exactly
// the meter's call sites (kStockMeterSites: coil fill, gold coil + glow, coil
// base, ball, S, SUPER UBER label + letter boxes) and pass every other call
// through. Nothing is painted over (TR3's covers are gone). Render-only: the
// rider/meter/race words and the AP trajectory are byte-identical with the
// sites skipped (tk-meter-1 V-runs). Knob off (PS2X_SSX3_TRICKY_HUD unset)
// wraps nothing; a different ELF (call-site words) refuses and keeps the
// stock meter.
#include "Ssx3StockMeter.h"

#include "ps2_runtime.h"
#include "ps2_ssx3_tricky_layer.h"
#include "runtime/ps2_memory.h"

#include <array>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <utility>

namespace
{
    constexpr uint32_t kTkMeterFns[] = {0x21d1a0u, 0x1f1190u, 0x21ed48u};
    constexpr size_t kTkMeterFnN = sizeof(kTkMeterFns) / sizeof(kTkMeterFns[0]);
    PS2Runtime::RecompiledFunction g_tkMeterOrig[kTkMeterFnN] = {};
    bool g_tkMeterHiddenLogged = false;

    template <size_t I>
    void tkStockMeterWrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        if (ctx && ps2_ssx3_tricky_layer::isStockMeterSite(kTkMeterFns[I], getRegU32(ctx, 31)) &&
            ps2_ssx3_tricky_layer::suppressStockMeter())
        {
            if (!g_tkMeterHiddenLogged)
            {
                g_tkMeterHiddenLogged = true;
                std::fprintf(stderr, "[ssx3-tricky-hud] stock meter hidden (first skipped call 0x%x from 0x%x)\n",
                             kTkMeterFns[I], getRegU32(ctx, 31));
            }
            return; // skipped draw: the dispatcher resumes at the return address
        }
        if (g_tkMeterOrig[I])
            g_tkMeterOrig[I](rdram, ctx, runtime);
    }

    template <size_t... I>
    constexpr std::array<PS2Runtime::RecompiledFunction, sizeof...(I)> tkStockMeterTable(std::index_sequence<I...>)
    {
        return {{&tkStockMeterWrapper<I>...}};
    }
} // namespace

namespace ps2_stock_meter
{

void applyTrickyStockMeter(PS2Runtime &runtime)
{
    const char *env = std::getenv("PS2X_SSX3_TRICKY_HUD");
    if (!env || env[0] != '1')
        return;
    // Every listed call site must hold `jal <helper>` (ra - 8): refuse on
    // any other ELF and keep the stock meter.
    const uint8_t *ram = runtime.memory().getRDRAM();
    for (const ps2_ssx3_tricky_layer::StockMeterSite &st : ps2_ssx3_tricky_layer::kStockMeterSites)
    {
        const uint32_t want = 0x0c000000u | ((st.fn >> 2) & 0x03ffffffu);
        uint32_t word = 0u;
        if (ram)
            std::memcpy(&word, ram + ((st.ra - 8u) & PS2_RAM_MASK), 4u);
        if (!ram || word != want)
        {
            std::fprintf(stderr,
                         "[ssx3-tricky-hud] refused: 0x%x reads 0x%08x, expected 0x%08x; stock meter kept\n",
                         st.ra - 8u, word, want);
            return;
        }
    }
    static const auto table = tkStockMeterTable(std::make_index_sequence<kTkMeterFnN>{});
    for (size_t i = 0; i < kTkMeterFnN; ++i)
    {
        g_tkMeterOrig[i] = runtime.lookupFunction(kTkMeterFns[i]);
        if (!g_tkMeterOrig[i])
        {
            std::fprintf(stderr, "[ssx3-tricky-hud] refused: no function at 0x%x; stock meter kept\n",
                         kTkMeterFns[i]);
            return;
        }
    }
    for (size_t i = 0; i < kTkMeterFnN; ++i)
        if (!runtime.replaceFunction(kTkMeterFns[i], table[i]))
        {
            std::fprintf(stderr, "[ssx3-tricky-hud] cannot wrap 0x%x\n", kTkMeterFns[i]);
            std::abort();
        }
    std::fprintf(stderr, "[ssx3-tricky-hud] stock meter draws wrapped (%zu sites; hidden on Tricky)\n",
                 sizeof(ps2_ssx3_tricky_layer::kStockMeterSites) /
                     sizeof(ps2_ssx3_tricky_layer::kStockMeterSites[0]));
}

} // namespace ps2_stock_meter
