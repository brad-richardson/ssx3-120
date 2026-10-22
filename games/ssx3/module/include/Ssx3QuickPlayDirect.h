#pragma once
#include <cstdint>
class PS2Runtime;
namespace ps2_quick_play_direct {
void apply(PS2Runtime &runtime);
// qp-cancel-1: Back (event 6) on our Quick Match filter takes Online Main's
// Logoff confirmation (0x1c0138) straight to a fresh Main Menu — whether the
// filter is idle or still searching. Stock Back on a searching filter instead
// closes into the hidden Online Main (drawHook hides our root: black gap with
// no menus) and then the disconnect-confirm trench (0x846 dialog, "Yes" to
// cFEStateNetAccountMem, Select Login, Select Persona, Online Main). The
// search-in-progress flag (+0x6ec) is deliberately NOT an input here.
inline bool qpBackTakesLogoff(uint32_t event, bool readyStage, bool isOursFilter)
{
    return event == 6u && readyStage && isOursFilter;
}
}
