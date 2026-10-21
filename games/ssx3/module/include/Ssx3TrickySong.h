#pragma once

// snd-seam-1 family #4: the Tricky song mix-contribution provider. The host
// audio output (`ps2_snd_audio_output.cpp`) calls these when set; null (or
// no module) is silence. The song player itself (`ps2_ssx3_tricky_song.h`)
// stays in the runtime until the Tricky families move (lane #2): the layer,
// the runtime and the song tests still include it there. This TU only
// forwards the three audio-output touch points (init, mix, probe).

#include "runtime/game_module.h"

#include <cstddef>
#include <cstdint>

namespace ps2_snd_tricky_song
{

void initFromEnvHook();
void mixIntoHook(int16_t *out, size_t frames, const char *path);
void probeSnapshot(ps2_game_module::TrickySongProbe *out);

} // namespace ps2_snd_tricky_song
