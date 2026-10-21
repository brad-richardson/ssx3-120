#pragma once

// snd-seam-1 Part 5 slice 3: draw-distance scaling (LOD1/TLS1). The module
// owns the `ssx3-lod-scale` override; `ps2_ssx3_lod.h` stays runtime-side
// (read-only), as does the course manifest the Tricky predicate reads.

class PS2Runtime;

namespace ps2_lod_scale
{

// Load-time apply (same ELF/entry/crc as the runtime override it replaces).
// Knob off (both scales 0): wraps nothing.
void applyLodScale(PS2Runtime &runtime);

} // namespace ps2_lod_scale
