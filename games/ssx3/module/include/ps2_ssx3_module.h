#pragma once

// module-seam-1: the SSX 3 game module's entry (runtime/game_module.h).
// ps2x_attach_game_module(<target> ssx3_game_module) makes the target's
// ps2_game_module::linkedModule() return this table.

#include "runtime/game_module.h"

const ps2_game_module::Module &ps2x_ssx3_game_module() noexcept;
