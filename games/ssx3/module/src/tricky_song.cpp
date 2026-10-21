// snd-seam-1 family #4: Tricky song mix-contribution provider bodies. Each
// forwards one audio-output touch point to the song player (which stays in
// the runtime until the Tricky families move). Output-only: guest hashes
// never cover PCM, so these run (or stay silent) without moving det keys.
#include "Ssx3TrickySong.h"

#include "ps2_ssx3_tricky_song.h"

namespace ps2_snd_tricky_song
{

void initFromEnvHook()
{
    ps2_ssx3_tricky_song::initFromEnv();
}

void mixIntoHook(int16_t *out, size_t frames, const char *path)
{
    ps2_ssx3_tricky_song::mixInto(out, frames, path);
}

void probeSnapshot(ps2_game_module::TrickySongProbe *out)
{
    if (out == nullptr)
        return;
    const ps2_ssx3_tricky_song::Player &pl = ps2_ssx3_tricky_song::player();
    const ps2_ssx3_tricky_song::BurstStats &b = pl.last;
    out->burstsStarted = pl.burstsStarted;
    out->burstsDone = pl.burstsDone;
    out->consumed = b.consumed;
    out->songPeak = b.songPeak;
    out->busBefore = b.busBefore;
    out->busAfter = b.busAfter;
    out->sat = b.sat;
    out->path = b.path;
}

} // namespace ps2_snd_tricky_song
