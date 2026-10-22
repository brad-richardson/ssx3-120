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
// qp-cancel-1 Part 2: whatever connection-lost dialog face accepts, the
// strand is the same — the accept SHOWS the stock Online Main Menu again
// (0x1a8a40 with the Online Main as the new top; the other switch args, the
// dismiss caller and the filter's parent all vary run to run, so none of them
// is an input). switchHook records the reshown Online Main; the switch runs
// stock. The only input is the shown state matching the one our root covered
// at entry. (P24/P26/P32: 0x1c0138 from the strand is dead — see the switchHook
// note. The back-out probe P30/P33 decides the route home.)
inline bool qpOnlineMainReturnTakesLogoff(uint32_t shown, bool readyStage, uint32_t recordedBelow)
{
    return readyStage && recordedBelow != 0u && shown == recordedBelow;
}
// qp-cancel-1 Part 2 (a): the silent death face tears our pair WITHOUT any
// dialog and the strand's Online Main is a FRESH object (P34/P37: entry slot
// dead, words blank, cross rebuilds; the entry-matching predicate above
// misses it). Its vtbl is NOT our root's 0x46ad48 (P37 eval), so liveness
// cannot mean vtbl equality: it means a sane object (bounds, non-null
// mapped vtbl) that is none of our three pair members. 0x1c0138 itself
// bails on bad input (P26), and the torn-pair call is excluded by the
// membership check (P27). Attempt the Part-1 Logoff shape exactly once,
// only when every guard holds: ready stage, pair confirmed dead, no dialog
// up (never preempt one: P24), a recent 0x1a8a40 show while ready, a sane
// shown state that is not our pair, and no earlier attempt on this show.
inline bool qpStrandLogoffReady(bool readyStage, bool pairDead, bool dialogUp,
                                bool freshShowRecent, bool shownSane,
                                bool shownIsPair, bool alreadyAttempted)
{
    return readyStage && pairDead && !dialogUp && freshShowRecent &&
           shownSane && !shownIsPair && !alreadyAttempted;
}
}
