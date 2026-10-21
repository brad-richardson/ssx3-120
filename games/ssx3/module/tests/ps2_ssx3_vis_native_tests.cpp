// VNP1: SSX 3 VU0 visibility test 0xDB8, native port vs the VU0 engine.
// Local only: needs the game's VU0 code image (PS2X_VR3_VU0_IMAGE, the
// vu0_40829a098c260b4f.bin dump); skipped when unset.
#include "MiniTest.h"
#include "ps2_runtime.h"
#include "runtime/gs/gs_frontend.h"
#include "runtime/ps2_memory.h"
#include "runtime/ps2_vu0.h"
#include "runtime/ps2_vu_state.h"
#include "Ssx3VisNative.h"
#include "ps2_fpmode.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <vector>

namespace
{
    struct VisRnd
    {
        uint64_t s;
        uint32_t next(uint32_t n)
        {
            s = s * 6364136223846793005ull + 1442695040888963407ull;
            return static_cast<uint32_t>((s >> 33) % n);
        }
        float range(float lo, float hi) { return lo + (hi - lo) * static_cast<float>(next(1u << 24)) / 16777216.0f; }
    };

    uint32_t visEdge(VisRnd &r)
    {
        static const uint32_t k[] = {0x00000000u, 0x80000000u, 0x3F800000u, 0xBF800000u, 0x00000001u,
                                     0x807FFFFFu, 0x7F7FFFFFu, 0xFF7FFFFFu, 0x7F800000u, 0xFF800000u,
                                     0x7FC00001u, 0x00800000u, 0x80800000u};
        return k[r.next(static_cast<uint32_t>(sizeof(k) / sizeof(k[0])))];
    }

    // mode 0: scene-like values; 1: with edge bit patterns; 2: raw bits.
    uint32_t visVal(VisRnd &r, int mode, float lo, float hi)
    {
        if (mode == 2)
            return r.next(4u) == 0u ? visEdge(r) : (r.next(0xFFFFu) << 16 | r.next(0x10000u));
        if (mode == 1 && r.next(6u) == 0u)
            return visEdge(r);
        float f = r.next(40u) == 0u ? 0.0f : r.range(lo, hi);
        uint32_t b;
        std::memcpy(&b, &f, sizeof(b));
        return b;
    }

    void visQ(void *dst, VisRnd &r, int mode, float lo, float hi)
    {
        uint32_t w[4];
        for (uint32_t &x : w)
            x = visVal(r, mode, lo, hi);
        std::memcpy(dst, w, sizeof(w));
    }

    void visCase(VisRnd &rnd, int mode, R5900Context &ctx, std::vector<uint8_t> &data)
    {
        for (int k = 0; k < 32; ++k)
            visQ(&ctx.vu0_vf[k], rnd, mode, -100.0f, 100.0f);
        for (int k = 0; k < 16; ++k)
            ctx.vi[k] = static_cast<uint16_t>(rnd.next(0x10000u));
        visQ(&ctx.vu0_acc, rnd, 2, 0.0f, 0.0f);
        visQ(&ctx.vu0_r, rnd, 2, 0.0f, 0.0f);
        uint32_t b = visVal(rnd, 2, 0.0f, 0.0f);
        std::memcpy(&ctx.vu0_q, &b, 4);
        b = visVal(rnd, 2, 0.0f, 0.0f);
        std::memcpy(&ctx.vu0_p, &b, 4);
        b = visVal(rnd, 2, 0.0f, 0.0f);
        std::memcpy(&ctx.vu0_i, &b, 4);
        ctx.vu0_mac_flags = rnd.next(0x10000u);
        ctx.vu0_clip_flags = rnd.next(2u) ? rnd.next(0x1000000u) : (rnd.next(0xFFFFu) << 16 | rnd.next(0x10000u));
        ctx.vu0_status = static_cast<uint16_t>(rnd.next(0x10000u));
        ctx.vu0_itop = rnd.next(0x400u);
        ctx.vu0_fbrst = rnd.next(0x10000u);
        ctx.vu0_vpu_stat = rnd.next(0x10000u);
        ctx.vu0_vpu_stat2 = rnd.next(0x10000u);
        ctx.vu0_pc = rnd.next(0x1000u);
        ctx.vu0_tpc = rnd.next(0x1000u);
        if (mode != 2)
        {
            const float cx = rnd.range(-60, 60), cy = rnd.range(-60, 60), cz = rnd.range(-60, 60);
            const float ex = rnd.range(0, 40), ey = rnd.range(0, 40), ez = rnd.range(0, 40);
            const float mn[4] = {cx - ex, cy - ey, cz - ez, 1.0f}, mx[4] = {cx + ex, cy + ey, cz + ez, 1.0f};
            std::memcpy(&ctx.vu0_vf[20], mn, 16);
            std::memcpy(&ctx.vu0_vf[21], mx, 16);
            const float sp[4] = {rnd.range(-2, 2), rnd.range(-2, 2), rnd.range(-2, 2), rnd.range(0, 50)};
            std::memcpy(&ctx.vu0_vf[22], sp, 16);
        }
        switch (rnd.next(5u))
        {
        case 0: ctx.vi[14] = 0; break;
        case 1: ctx.vi[14] = static_cast<uint16_t>(1u << rnd.next(16u)); break;
        case 2: ctx.vi[14] = 0xFFFFu; break;
        default: break;
        }
        for (int qw = 0; qw < 256; ++qw)
            visQ(data.data() + qw * 16, rnd, mode, -1.5f, 1.5f);
        if (mode != 2)
        {
            for (int m = 0; m < 2; ++m)
            {
                float *mat = reinterpret_cast<float *>(data.data() + (64 + 4 * m) * 16);
                for (int c = 0; c < 4; ++c)
                    mat[c * 4 + 3] = c == 3 ? rnd.range(10, 80) : rnd.range(-0.1f, 0.1f);
            }
            if (rnd.next(3u) == 0u)
                for (int qw = 80; qw < 170; ++qw)
                    reinterpret_cast<float *>(data.data() + qw * 16)[3] = rnd.range(0, 200);
        }
    }

    // VNP1P2: 0x570 inputs. VF10-13/15/16 are the program's inputs (the
    // walker's macro-op setup); everything else passes through. mode 0/1 use
    // scene-like projection rows + AABB; mode 2 is raw bits. Data memory is
    // randomized and must be byte-identical after (the program reads none).
    void visCase570(VisRnd &rnd, int mode, R5900Context &ctx, std::vector<uint8_t> &data)
    {
        for (int k = 0; k < 32; ++k)
            visQ(&ctx.vu0_vf[k], rnd, mode, -100.0f, 100.0f);
        for (int k = 0; k < 16; ++k)
            ctx.vi[k] = static_cast<uint16_t>(rnd.next(0x10000u));
        visQ(&ctx.vu0_acc, rnd, 2, 0.0f, 0.0f);
        visQ(&ctx.vu0_r, rnd, 2, 0.0f, 0.0f);
        uint32_t b = visVal(rnd, 2, 0.0f, 0.0f);
        std::memcpy(&ctx.vu0_q, &b, 4);
        b = visVal(rnd, 2, 0.0f, 0.0f);
        std::memcpy(&ctx.vu0_p, &b, 4);
        b = visVal(rnd, 2, 0.0f, 0.0f);
        std::memcpy(&ctx.vu0_i, &b, 4);
        ctx.vu0_mac_flags = rnd.next(0x10000u);
        ctx.vu0_clip_flags = rnd.next(2u) ? rnd.next(0x1000000u) : (rnd.next(0xFFFFu) << 16 | rnd.next(0x10000u));
        ctx.vu0_status = static_cast<uint16_t>(rnd.next(0x10000u));
        ctx.vu0_itop = rnd.next(0x400u);
        ctx.vu0_fbrst = rnd.next(0x10000u);
        ctx.vu0_vpu_stat = rnd.next(0x10000u);
        ctx.vu0_vpu_stat2 = rnd.next(0x10000u);
        ctx.vu0_pc = rnd.next(0x1000u);
        ctx.vu0_tpc = rnd.next(0x1000u);
        if (mode != 2)
        {
            // Projection rows, mirroring the 0xDB8 data qw 64-67 setup: the
            // w row is positive-ish (distance), off-diagonals near zero.
            float m10[4] = {1, 0, 0, 0}, m11[4] = {0, 1, 0, 0}, m12[4] = {0, 0, 1, 0}, m13[4] = {0, 0, 0, 1};
            m10[3] = rnd.range(-0.1f, 0.1f);
            m11[3] = rnd.range(-0.1f, 0.1f);
            m12[3] = rnd.range(-0.1f, 0.1f);
            m13[3] = rnd.range(10, 80);
            std::memcpy(&ctx.vu0_vf[10], m10, 16);
            std::memcpy(&ctx.vu0_vf[11], m11, 16);
            std::memcpy(&ctx.vu0_vf[12], m12, 16);
            std::memcpy(&ctx.vu0_vf[13], m13, 16);
            const float cx = rnd.range(-60, 60), cy = rnd.range(-60, 60), cz = rnd.range(-60, 60);
            const float ex = rnd.range(0, 40), ey = rnd.range(0, 40), ez = rnd.range(0, 40);
            const float mn[4] = {cx - ex, cy - ey, cz - ez, 1.0f}, mx[4] = {cx + ex, cy + ey, cz + ez, 1.0f};
            std::memcpy(&ctx.vu0_vf[15], mn, 16);
            std::memcpy(&ctx.vu0_vf[16], mx, 16);
        }
        for (int qw = 0; qw < 256; ++qw)
            visQ(data.data() + qw * 16, rnd, mode, -1.5f, 1.5f);
    }
}

void register_ps2_ssx3_vis_native_tests()
{
    MiniTest::Case("PS2SSX3VisNative", [](TestCase &tc)
    {
        tc.Run("native 0xDB8 matches the VU0 engine on the game image (local only)", [](TestCase &t)
        {
            const char *imagePath = std::getenv("PS2X_VR3_VU0_IMAGE");
            if (imagePath == nullptr)
            {
                std::fprintf(stderr, "[vnp1] skipped (PS2X_VR3_VU0_IMAGE unset)\n");
                return;
            }
            if (!ps2_ssx3_vis_native::available())
            {
                std::fprintf(stderr, "[vnp1] skipped (vector FMAC core unavailable)\n");
                return;
            }
            std::vector<uint8_t> code(PS2_VU0_CODE_SIZE, 0u);
            std::ifstream in(imagePath, std::ios::binary);
            in.read(reinterpret_cast<char *>(code.data()), static_cast<std::streamsize>(code.size()));
            t.IsTrue(in.gcount() == static_cast<std::streamsize>(code.size()), "game VU0 image read");
            t.IsTrue(ps2_ssx3_vis_native::imageMatches(code.data(), 1u), "image is 40829a098c260b4f");
            {
                std::vector<uint8_t> other = code;
                other[0xAE0] ^= 1u;
                t.IsTrue(!ps2_ssx3_vis_native::imageMatches(other.data(), 2u), "a changed image is refused");
            }
            const VU0Interpreter::RecompProgram *generated =
                VU0Interpreter::findRecompProgram(ps2_ssx3_vis_native::kImageHash);
            ps2_fpmode::ScopedPs2Mode eeMode; // the EE thread's FP control
            GS gs;
            // Engines: 0 = queued interpreter; 1 = generated guarded groups +
            // direct commit (the play path, when the image is compiled in).
            VU0Interpreter engines[2];
            engines[0].setDirectCommitForTest(0);
            engines[1].setDirectCommitForTest(1);
            engines[1].setBlocksForTest(2);
            engines[1].setRecompProgramForTest(generated);
            const int engineCount = generated != nullptr ? 2 : 1;
            if (generated == nullptr)
                std::fprintf(stderr, "[vnp1] generated image not compiled in; interpreter only\n");
            VisRnd rnd{0x7653A1D5EEDull};
            uint64_t cases = 0, mismatches = 0, results[5] = {};
            std::vector<uint8_t> data(PS2_VU0_DATA_SIZE);
            for (uint32_t n = 0; n < 60000u; ++n)
            {
                const int mode = n % 10u < 6u ? 0 : n % 10u < 9u ? 1 : 2;
                R5900Context ctx;
                visCase(rnd, mode, ctx, data);
                R5900Context nat = ctx;
                ps2_ssx3_vis_native::runDb8(&nat, data.data());
                const ps2_ssx3_vis_native::Vu0Snapshot got = ps2_ssx3_vis_native::snapshot(nat);
                for (int e = 0; e < engineCount; ++e)
                {
                    R5900Context ref = ctx;
                    std::vector<uint8_t> refData = data;
                    VU0Interpreter &vu = engines[e];
                    vu.resetForVu0Start();
                    PS2Runtime::importVu0Context(&ref, vu.state(), false);
                    vu.execute(code.data(), PS2_VU0_CODE_SIZE, refData.data(), PS2_VU0_DATA_SIZE, gs, nullptr,
                               ps2_ssx3_vis_native::kStartPc, 0u, ref.vu0_itop, 4096u);
                    ps2_ssx3_vis_native::exportVu0StateForTest(vu.state(), &ref);
                    const ps2_ssx3_vis_native::Vu0Snapshot want = ps2_ssx3_vis_native::snapshot(ref);
                    ++cases;
                    if (e == 0 && want.vi[1] < 5u)
                        ++results[want.vi[1]];
                    if (!ps2_ssx3_vis_native::equal(want, got, false) || refData != data)
                    {
                        if (++mismatches <= 4u)
                        {
                            std::fprintf(stderr, "[vnp1] mismatch case %u engine %d mode %d vi14=%04x\n", n, e, mode,
                                         ctx.vi[14]);
                            (void)ps2_ssx3_vis_native::equal(want, got, true);
                        }
                    }
                }
            }
            std::fprintf(stderr, "[vnp1] differential: %llu compares, %llu mismatches, results %llu/%llu/%llu/%llu/%llu\n",
                         static_cast<unsigned long long>(cases), static_cast<unsigned long long>(mismatches),
                         static_cast<unsigned long long>(results[0]), static_cast<unsigned long long>(results[1]),
                         static_cast<unsigned long long>(results[2]), static_cast<unsigned long long>(results[3]),
                         static_cast<unsigned long long>(results[4]));
            t.Equals(mismatches, uint64_t{0}, "native 0xDB8 equals the engine");
            for (uint64_t r : results)
                t.IsTrue(r > 100u, "every result code (0..4) covered");
        });
        tc.Run("native 0x570 matches the VU0 engine on the game image (local only)", [](TestCase &t)
        {
            const char *imagePath = std::getenv("PS2X_VR3_VU0_IMAGE");
            if (imagePath == nullptr)
            {
                std::fprintf(stderr, "[vnp1] skipped (PS2X_VR3_VU0_IMAGE unset)\n");
                return;
            }
            if (!ps2_ssx3_vis_native::available())
            {
                std::fprintf(stderr, "[vnp1] skipped (vector FMAC core unavailable)\n");
                return;
            }
            std::vector<uint8_t> code(PS2_VU0_CODE_SIZE, 0u);
            std::ifstream in(imagePath, std::ios::binary);
            in.read(reinterpret_cast<char *>(code.data()), static_cast<std::streamsize>(code.size()));
            t.IsTrue(in.gcount() == static_cast<std::streamsize>(code.size()), "game VU0 image read");
            t.IsTrue(ps2_ssx3_vis_native::imageMatches(code.data(), 1u), "image is 40829a098c260b4f");
            const VU0Interpreter::RecompProgram *generated =
                VU0Interpreter::findRecompProgram(ps2_ssx3_vis_native::kImageHash);
            ps2_fpmode::ScopedPs2Mode eeMode; // the EE thread's FP control
            GS gs;
            // Engines: 0 = queued interpreter; 1 = generated guarded groups +
            // direct commit (the play path, when the image is compiled in).
            VU0Interpreter engines[2];
            engines[0].setDirectCommitForTest(0);
            engines[1].setDirectCommitForTest(1);
            engines[1].setBlocksForTest(2);
            engines[1].setRecompProgramForTest(generated);
            const int engineCount = generated != nullptr ? 2 : 1;
            if (generated == nullptr)
                std::fprintf(stderr, "[vnp1] generated image not compiled in; interpreter only\n");
            VisRnd rnd{0x570A1DC0FF1ull};
            uint64_t cases = 0, mismatches = 0, inside = 0, straddle = 0, culled = 0;
            std::vector<uint8_t> data(PS2_VU0_DATA_SIZE);
            for (uint32_t n = 0; n < 60000u; ++n)
            {
                const int mode = n % 10u < 6u ? 0 : n % 10u < 9u ? 1 : 2;
                R5900Context ctx;
                visCase570(rnd, mode, ctx, data);
                R5900Context nat = ctx;
                ps2_ssx3_vis_native::runP570(&nat);
                const ps2_ssx3_vis_native::Vu0Snapshot got = ps2_ssx3_vis_native::snapshot(nat);
                t.IsTrue(got.pc == 0x6E0u && got.tpc == 0x6E0u, "native 0x570 ends at 0x6E0");
                for (int e = 0; e < engineCount; ++e)
                {
                    R5900Context ref = ctx;
                    std::vector<uint8_t> refData = data;
                    VU0Interpreter &vu = engines[e];
                    vu.resetForVu0Start();
                    PS2Runtime::importVu0Context(&ref, vu.state(), false);
                    vu.execute(code.data(), PS2_VU0_CODE_SIZE, refData.data(), PS2_VU0_DATA_SIZE, gs, nullptr,
                               ps2_ssx3_vis_native::kStartPc570, 0u, ref.vu0_itop, 4096u);
                    ps2_ssx3_vis_native::exportVu0StateForTest(vu.state(), &ref);
                    const ps2_ssx3_vis_native::Vu0Snapshot want = ps2_ssx3_vis_native::snapshot(ref);
                    ++cases;
                    if (e == 0)
                    {
                        if (want.vi[1] == 0)
                            ++inside;
                        else if (want.vi[2] != 0)
                            ++culled;
                        else
                            ++straddle;
                    }
                    if (!ps2_ssx3_vis_native::equal(want, got, false) || refData != data)
                    {
                        if (++mismatches <= 4u)
                        {
                            std::fprintf(stderr, "[vnp1] mismatch case %u engine %d mode %d vi1=%u vi2=%u\n", n, e,
                                          mode, want.vi[1], want.vi[2]);
                            (void)ps2_ssx3_vis_native::equal(want, got, true);
                        }
                    }
                }
            }
            std::fprintf(stderr, "[vnp1] differential 0x570: %llu compares, %llu mismatches, inside=%llu straddle=%llu culled=%llu\n",
                         static_cast<unsigned long long>(cases), static_cast<unsigned long long>(mismatches),
                         static_cast<unsigned long long>(inside), static_cast<unsigned long long>(straddle),
                         static_cast<unsigned long long>(culled));
            t.Equals(mismatches, uint64_t{0}, "native 0x570 equals the engine");
            t.IsTrue(inside > 100u, "inside covered");
            t.IsTrue(straddle > 100u, "straddle covered");
            t.IsTrue(culled > 100u, "culled covered");
        });
    });
}
