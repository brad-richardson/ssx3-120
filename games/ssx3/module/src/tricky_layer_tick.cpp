// snd-seam-1 Part 5 slice 4: the scheduler's Tricky-layer VBlank call
// behind a module provider (ABI 7). The forward below is byte-identical to
// the inline call it replaces; the reducer body itself moves here in a
// later slice, at which point this TU owns the reduction.
#include "Ssx3TrickyLayer.h"

#include "ps2_ssx3_tricky_layer.h"
#include "ps2_ssx3_tricky_records.h"

namespace ps2_tricky_layer
{

void tickHook(uint8_t *rdram, size_t ramSize, uint64_t tick)
{
    namespace records = ps2_ssx3_tricky_records;
    if (records::validRam(rdram, ramSize))
        records::tick(records::state(), rdram, ramSize,
                      ps2_ssx3_course::appUpdateFn(rdram) == ps2_ssx3_course::kGameUpdate,
                      ps2_ssx3_tricky::state().tricky || ps2_ssx3_tricky_list::mapState().activeEvent >= 0,
                      records::exactPath());
    ps2_ssx3_tricky_layer::onVBlankTick(rdram, ramSize, tick);
}

} // namespace ps2_tricky_layer
