#pragma once

// full120 slice 1: FIX-policy content (name/bit tables, "all" composition,
// mask-compat predicates) moved from ps2_fh1_fix.h to
// games/ssx3/module/src/fh1_fix_content.cpp. The runtime reads it through
// Module.fh1FixPolicy; this header declares the module-side entry point.

namespace ps2_game_module
{
struct Fh1FixPolicy;
}

namespace ps2_ssx3_fh1
{
const ps2_game_module::Fh1FixPolicy &fixPolicy() noexcept;
}
