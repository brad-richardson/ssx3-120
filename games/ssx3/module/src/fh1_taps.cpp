// full120 slice 6: tap-target lists (game branch targets/sites named by
// the env-driven tap bodies) moved from ps2_fh1_full120.h. Values
// verbatim; the runtime tap bodies stay generic and read them through
// Module.fh1TapTargets (null reads as zeros: no tap fires).
#include "Ssx3Fh1Taps.h"

#include "runtime/game_module.h"

namespace ps2_ssx3_fh1
{
namespace
{
const ps2_game_module::Fh1TapTargets kTapTargets{
    0x133308u, // stickTracker (fh26 stick-angle tracker call)
    0x317810u, // fh9RngTarget
    0x1298c8u, // fh9GateTarget
    {0x10b8acu, 0x10bdccu, 0x10da6cu, 0x10dc30u}, // fh9GateSite
    {0x10db3cu, 0x10b8d0u, 0x10b910u, 0x10c1dcu, 0x115e4cu, 0x115fa4u, 0x10b468u, 0u}, // fh9RngSite
    0x11fec8u, // fh9WrapTarget
    0x111538u, // fh9StateTarget
    0x11fed0u, // fh9WrapSite
    0x312ae8u, // fh9SubTarget
    0x12e8e8u, // fh9SubSite
    0x313868u, // fh9CrossTarget
    0x313938u, // fh9ExactTarget
    0x3139a8u, // fh9EndTarget
    0x3135b0u, // fh9AdvTarget
};
} // namespace

const ps2_game_module::Fh1TapTargets &tapTargets() noexcept
{
    return kTapTargets;
}
} // namespace ps2_ssx3_fh1
