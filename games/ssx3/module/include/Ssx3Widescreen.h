#pragma once

// split-input-1 #7: SSX 3 widescreen default. The module owns the option-
// word policy (armed by the `ssx3-widescreen-default` row from
// PS2X_WIDESCREEN); the runtime calls the hooks at its two read sites.
#include <cstdint>

class PS2Runtime;

namespace ps2_ssx3_widescreen
{
    // Table apply ("ssx3-widescreen-default"): reads PS2X_WIDESCREEN
    // (default anamorphic) and arms the hooks below.
    void applySsx3Widescreen(PS2Runtime &runtime);
    // 0x228C08 dispatch tap: enforces the armed mode on the guest options
    // word. Null (or no module) keeps the game default.
    void noteWidescreenDispatch(uint8_t *rdram, uint32_t sourcePc);
    // Present-setup query: true when the armed mode is anamorphic.
    bool widescreenAnamorphic();
} // namespace ps2_ssx3_widescreen
