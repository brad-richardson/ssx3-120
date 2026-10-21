#pragma once

// full120 slice 6: tap-target lists (game branch targets/sites named by
// the env-driven tap bodies) moved from ps2_fh1_full120.h to
// games/ssx3/module/src/fh1_taps.cpp. The tap bodies (srcTap,
// fh26TapCall, fh9OnBranch, fh10HistBranch, in4NoteRider) stay generic in
// the runtime and read this table through Module.fh1TapTargets; this
// header declares the module-side entry point. The moved dispatch bodies
// (interest table, chain) keep their literals: same module, same values,
// cross-pinned by the hooktable census test.

namespace ps2_game_module
{
struct Fh1TapTargets;
}

namespace ps2_ssx3_fh1
{
const ps2_game_module::Fh1TapTargets &tapTargets() noexcept;
}
