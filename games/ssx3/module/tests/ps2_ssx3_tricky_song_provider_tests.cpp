#include "MiniTest.h"
#include "Ssx3TrickySong.h"
// Staging only (white-box song state, mirrored from the shared song tests).
// This include moves with the Tricky families (lane #2); the provider under
// test does not depend on it.
#include "ps2_ssx3_tricky_song.h"

#include <cstdint>

// snd-seam-1 family #4: the provider forwards the audio-output touch
// points byte-identically (mix bodies are untouched; these tests pin the
// forwarding, both paths and the probe snapshot). The shared song tests
// keep asserting the mix math itself. The player is left idle afterwards,
// as in the shared tests (one static player per process).
using namespace ps2_snd_tricky_song;

namespace
{
void resetPlayer()
{
    using namespace ps2_ssx3_tricky_song;
    Player &p = player();
    p.cmd.store(0u, std::memory_order_relaxed);
    p.song.ok = false;
    p.song.frames = 0u;
    p.song.pcm.clear();
    p.resume = 0u;
    p.songPeak = 0;
    p.cur = BurstStats{};
    p.last = BurstStats{};
    p.burstsDone = 0u;
    p.burstsStarted = 0u;
    p.probeLastLeft = 0u;
    p.probeActive = false;
    p.probeMixLogged = false;
}
} // namespace

void register_ps2_ssx3_tricky_song_provider_tests()
{
    MiniTest::Case("Ps2Ssx3TrickySongProvider", [](TestCase &tc)
                   {
        tc.Run("the mix provider forwards identical bytes on both paths", [](TestCase &t)
               {
            using namespace ps2_ssx3_tricky_song;
            Player &p = player();
            p.song.frames = 4u;
            p.song.pcm = {30000, -30000, 30000, -30000, 100, 200, 300, 400};
            p.song.ok = true;
            p.cmd.store(0u, std::memory_order_relaxed);
            p.resume = 0u;
            startBurst(0u);
            int16_t direct[4] = {1000, -1000, 0, 0};
            mixIntoHook(direct, 2u, "direct");
            t.IsTrue(direct[0] == 31000 && direct[1] == -31000, "direct adds frame 0");
            t.IsTrue(direct[2] == 30000 && direct[3] == -30000, "direct adds frame 1");
            p.cmd.store((1ull << 32) | 1u, std::memory_order_relaxed); // off=0
            int16_t stretch[2] = {30000, -30000};
            mixIntoHook(stretch, 1u, "stretch");
            t.IsTrue(stretch[0] == 32767 && stretch[1] == -32768, "stretch saturates");
            resetPlayer();
        });
        tc.Run("the idle provider mixes silence and snapshots zeros", [](TestCase &t)
               {
            int16_t out[4] = {1000, -1000, 500, -500};
            mixIntoHook(out, 2u, "direct");
            t.IsTrue(out[0] == 1000 && out[1] == -1000 && out[2] == 500 && out[3] == -500,
                     "idle mixes nothing");
            ps2_game_module::TrickySongProbe probe{1u, 2u, 3u, 4, 5, 6, 7u, "x"};
            probeSnapshot(&probe);
            t.Equals(probe.burstsStarted, 0ull, "no bursts started");
            t.Equals(probe.burstsDone, 0ull, "none done");
            t.Equals(probe.consumed, 0ull, "nothing consumed");
            resetPlayer();
        }); });
}
