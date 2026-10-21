#pragma once

// snd-seam-1 Part 5 slice 4: the Tricky layer's per-VBlank reducer. The
// scheduler calls this provider at the exact site the reducer ran; the
// reducer body moves here from the runtime in a later slice (with the
// layer header), so this header will grow then.

#include <cstddef>
#include <cstdint>

namespace ps2_tricky_layer
{

// Forwards to the runtime reducer today (same call, same order, same
// bytes); owns the reduction once the layer moves.
void tickHook(uint8_t *rdram, size_t ramSize, uint64_t tick);

} // namespace ps2_tricky_layer
