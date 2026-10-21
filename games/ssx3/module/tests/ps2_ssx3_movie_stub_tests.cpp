#include "MiniTest.h"
#include "Ssx3Movie.h"

#include "ps2_runtime.h"

#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

// snd-seam-1 family #9: the SSX 3 movie completion stubs moved here
// verbatim (still unwired: no hook address is runtime-verified, so these
// pin the handler contract — log one line, return 1 — for the future
// boot-ladder wiring, which only adds PS2_STUB_LIST + TOML entries).
// Each run resets the counter first: tc.Run blocks execute in name order,
// so no run may assume another ran before it.
namespace
{
void setReg(R5900Context &ctx, int reg, uint32_t v)
{
    ctx.r[reg] = _mm_set_epi64x(0, static_cast<int64_t>(static_cast<int32_t>(v)));
}
} // namespace

void register_ps2_ssx3_movie_stub_tests();

void register_ps2_ssx3_movie_stub_tests()
{
    MiniTest::Case("Ps2Ssx3MovieStub", [](TestCase &tc)
                   {
        tc.Run("counter resets and null context is safe", [](TestCase &t)
               {
            ps2_stubs::resetSsx3MovieStubState();
            t.Equals(ps2_stubs::ssx3MovieStubInvocations(), 0ull, "counter resets");
            // Null context is safe (a defensive hook shape still logs).
            ps2_stubs::ssx3MoviePlayComplete(nullptr, nullptr, nullptr);
            t.Equals(ps2_stubs::ssx3MovieStubInvocations(), 1ull, "play counts"); });
        tc.Run("blocking play with a null path returns 1", [](TestCase &t)
               {
            ps2_stubs::resetSsx3MovieStubState();
            R5900Context play{};
            play.pc = 0x002534A8u;
            setReg(play, 31, 0x001A1D00u);
            setReg(play, 5, 0u); // $a1 = null arg block: path renders "(null)"
            std::vector<uint8_t> ram(PS2_RAM_SIZE, 0u);
            ps2_stubs::ssx3MoviePlayComplete(ram.data(), &play, nullptr);
            t.Equals(getRegU32(&play, 2), 1u, "play returns 1");
            t.Equals(ps2_stubs::ssx3MovieStubInvocations(), 1ull, "play counts"); });
        tc.Run("blocking play reads the path through the arg block", [](TestCase &t)
               {
            ps2_stubs::resetSsx3MovieStubState();
            constexpr uint32_t kArgs = 0x00100000u, kPath = 0x00100040u;
            const std::string path = "data/movies/TEST.MPC";
            std::vector<uint8_t> ram(PS2_RAM_SIZE, 0u);
            std::memcpy(ram.data() + kArgs, &kPath, 4u);
            std::memcpy(ram.data() + kPath, path.c_str(), path.size() + 1u);
            R5900Context pathed{};
            setReg(pathed, 5, kArgs);
            ps2_stubs::ssx3MoviePlayComplete(ram.data(), &pathed, nullptr);
            t.Equals(getRegU32(&pathed, 2), 1u, "pathed play returns 1");
            t.Equals(ps2_stubs::ssx3MovieStubInvocations(), 1ull, "pathed play counts"); });
        tc.Run("poll returns done", [](TestCase &t)
               {
            ps2_stubs::resetSsx3MovieStubState();
            std::vector<uint8_t> ram(PS2_RAM_SIZE, 0u);
            R5900Context poll{};
            ps2_stubs::ssx3MoviePollComplete(ram.data(), &poll, nullptr);
            // Matches the sceMpegIsEnd true convention (1 = stream ended).
            t.Equals(getRegU32(&poll, 2), 1u, "poll returns 1");
            t.Equals(ps2_stubs::ssx3MovieStubInvocations(), 1ull, "poll counts"); });
    });
}
