// VNP1: native port of SSX 3's VU0 visibility test (micro program 0xDB8 of
// image 40829a098c260b4f). See Ssx3VisNative.h for the contract.
//
// The port follows the VU0 engine's own model, not real hardware:
// - FMAC arithmetic, exact-result classification, value overrides, MAC and
//   status assembly and the product sticky are the vector FMAC core's
//   (vu/ps2_vu_fmac_simd.h, fmacSimd with the exact float mode), reused
//   through its helpers with the same expressions;
// - CLIP is execUpperImpl's raw-bit compare;
// - VI writes truncate to int16; LQ addresses wrap at the 4 KiB data memory;
// - flag reads (FCGET, FMAND) see the flag write issued four pairs earlier:
//   every path of this program issues one pair per cycle (no stalls; VNP1
//   tracer on the engine, local/research/VNP1/trace-*.txt) and kFmacLatency
//   is 4, so a reader at cycle t sees writers issued at t-4 or earlier;
// - no branch in the program reads a VI register written by the pair before
//   it, so the branch VI backup never applies.
// The program, as the engine runs it (disassembly: local/research/VNP1):
//   0xDB8  VF10..13 = data[64..67]; VF15 = VF20; VF16 = VF21; BAL 0xAC8
//   0xAC8  8 AABB corners through VF10..13, CLIP each against its w;
//          VI1 = OR of the clip codes, VI2 = AND (both & 0x3F); JR
//   0xDF8  VI2 != 0: VI1 = 1, end.        VI13 = VI1 (delay slot)
//   0xE18  VF6 = VF22 (delay slot); VI14 != 0: BAL 0xC18 (VI10 = 0x50),
//          VI1 != 0 on return: VI1 = 2, end
//   0xE60  VI13 == 0: VI1 = 0, end
//   0xE80  VF10..13 = data[68..71]; VF15/16 again; BAL 0xAC8;
//          VI1 == 0: VI1 = 3, end; else VI1 = 4, end
//   0xC18  for each set bit of VI14 (low to high): 5 planes at data[VI10..],
//          scaled by VF6; d = ACC.x - 1 * VF6.w per plane; a negative d
//          (MAC sign x, read by FMAND four pairs later) skips to the next
//          bit; all five non-negative: VI1 = 1, return. No bit left: VI1 = 0.

#include "Ssx3VisNative.h"

#include "ps2_fpmode.h"
#include "ps2_runtime.h"
#include "runtime/ps2_vu_core.h"
#include "vu/ps2_vu_fmac_simd.h"

#define XXH_INLINE_ALL
#include "runtime/third_party/xxhash.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace ps2_ssx3_vis_native
{
#if PS2X_VU1_FMAC_SIMD_AVAILABLE
    namespace
    {
        using namespace ps2_vu1_fmac_simd;

        // MAC of the last flag write, its current status nibble, and the OR of
        // every write's (current | product sticky) nibble.
        struct Flags
        {
            uint32_t mac = 0u;
            uint32_t cur = 0u;
            uint32_t sticky = 0u;
        };

        PS2X_VU1_ALWAYS_INLINE inline v4f nz(v4f v)
        {
            return (v4f)normalize4((v4u)v);
        }

        template <int kLane>
        PS2X_VU1_ALWAYS_INLINE inline v4f bc(v4f v)
        {
            return __builtin_shufflevector(v, v, kLane, kLane, kLane, kLane);
        }

        PS2X_VU1_ALWAYS_INLINE inline v4f loadVf(const __m128 &v)
        {
            v4f out;
            std::memcpy(&out, &v, sizeof(out));
            return out;
        }

        PS2X_VU1_ALWAYS_INLINE inline void storeVf(__m128 &dst, v4f v)
        {
            std::memcpy(&dst, &v, sizeof(v));
        }

        // applyDest for one register: only the dest lanes change.
        PS2X_VU1_ALWAYS_INLINE inline v4f merge(v4f old, v4f result, uint8_t dest)
        {
            const v4i dm = destMask(dest);
            return (v4f)(((v4i)result & dm) | ((v4i)old & ~dm));
        }

        // fmacSimd<kArith, *, *, exact> on normalized operands (vs, the
        // broadcast/vector second operand b, ACC); returns the result lanes
        // (value overrides applied) and commits the flags into f.
        template <int kArith>
        PS2X_VU1_ALWAYS_INLINE inline v4f fmac(v4f vs, v4f b, v4f acc, uint8_t dest, Flags &f)
        {
#if defined(__clang__)
#pragma clang fp contract(off)
#endif
            constexpr bool kProductSum = kArith == kMadd || kArith == kMsub;
            v4f result;
            if constexpr (kArith == kAdd)
                result = vs + b;
            else if constexpr (kArith == kMul)
            {
                const v4f product = vs * b;
                result = product;
            }
            else if constexpr (kArith == kMadd)
            {
                const v4f product = vs * b;
                result = acc + product;
            }
            else
            {
                static_assert(kArith == kMsub, "VNP1: ops used by 0xDB8 only");
                const v4f product = vs * b;
                result = acc - product;
            }

            const v2d sLo = lo2(vs), sHi = hi2(vs);
            const v2d bLo = lo2(b), bHi = hi2(b);
            v2d eLo, eHi;
            v2d pLo = {0.0, 0.0}, pHi = {0.0, 0.0};
            if constexpr (kArith == kAdd)
            {
                eLo = sLo + bLo;
                eHi = sHi + bHi;
            }
            else
            {
                pLo = sLo * bLo;
                pHi = sHi * bHi;
                if constexpr (kArith == kMul)
                {
                    eLo = pLo;
                    eHi = pHi;
                }
                else
                {
                    const v2d aLo = lo2(acc), aHi = hi2(acc);
                    if constexpr (kArith == kMadd)
                    {
                        eLo = aLo + pLo;
                        eHi = aHi + pHi;
                    }
                    else
                    {
                        eLo = aLo - pLo;
                        eHi = aHi - pHi;
                    }
                }
            }

            const v4i dm = destMask(dest);
            const Class4 c = classify(eLo, eHi);
            const v4i laneFlags = flags4(c) & dm;

            v4u bits = (v4u)result;
            const v4u signBits = (v4u)c.neg & 0x80000000u;
            const v4u toZero = (v4u)(c.zero | c.under);
            const v4u toMax = (v4u)c.over;
            bits = (bits & ~toZero) | (signBits & toZero);
            bits = (bits & ~toMax) | ((signBits | 0x7F7FFFFFu) & toMax);

            uint32_t extraSticky = 0u;
            if constexpr (kProductSum)
                extraSticky = orLanes(flags4(classify(pLo, pHi)) & dm);

            const v4i spread = (laneFlags & 1) | ((laneFlags & 2) << 3) | ((laneFlags & 4) << 6) |
                               ((laneFlags & 8) << 9);
            const v4i shifts = {3, 2, 1, 0};
            const uint32_t current = orLanes(laneFlags) & 0xFu;
            f.mac = orLanes(spread << shifts);
            f.cur = current;
            f.sticky |= current | extraSticky;
            return (v4f)bits;
        }

        // execUpperImpl's CLIP (CLIPw.xyz vfN, vfNw): raw bits, no
        // normalization.
        PS2X_VU1_ALWAYS_INLINE inline uint32_t clip(v4f v)
        {
            uint32_t b[4];
            std::memcpy(b, &v, sizeof(b));
            const int32_t limit = (b[3] & 0x7F800000u) != 0u ? static_cast<int32_t>(b[3] & 0x7FFFFFFFu) : 0x007FFFFF;
            const auto exceeds = [limit](uint32_t bits, uint32_t signMask)
            {
                const uint32_t flipped = bits ^ signMask;
                int32_t ordered;
                std::memcpy(&ordered, &flipped, sizeof(ordered));
                return ordered > limit;
            };
            uint32_t flags = 0u;
            flags |= exceeds(b[0], 0x00000000u) ? 0x01u : 0u;
            flags |= exceeds(b[0], 0x80000000u) ? 0x02u : 0u;
            flags |= exceeds(b[1], 0x00000000u) ? 0x04u : 0u;
            flags |= exceeds(b[1], 0x80000000u) ? 0x08u : 0u;
            flags |= exceeds(b[2], 0x00000000u) ? 0x10u : 0u;
            flags |= exceeds(b[2], 0x80000000u) ? 0x20u : 0u;
            return flags;
        }

        PS2X_VU1_ALWAYS_INLINE inline v4f loadData(const uint8_t *data, int32_t qw)
        {
            // LQ: ((vi + imm) * 16) & (dataSize - 1); always in range here.
            const uint32_t addr = (static_cast<uint32_t>(qw) * 16u) & (PS2_VU0_DATA_SIZE - 1u);
            v4f v;
            std::memcpy(&v, data + addr, sizeof(v));
            return v;
        }

        // Register file the program touches (VF0 is the constant 0,0,0,1).
        struct Regs
        {
            v4f vf[17]; // VF0..VF16 (VF9/VF14 unused, never stored)
            v4f acc;
            int32_t vi[16];
            uint32_t clip;
            Flags flags;
        };

        // 0xAC8: corners from VF15 (min) / VF16 (max) through VF10..13.
        PS2X_VU1_ALWAYS_INLINE inline void sub0ac8(Regs &r)
        {
            constexpr uint8_t kXyzw = 0xFu;
            const v4f n10 = nz(r.vf[10]), n11 = nz(r.vf[11]), n12 = nz(r.vf[12]), n13 = nz(r.vf[13]);
            const v4f n15 = nz(r.vf[15]), n16 = nz(r.vf[16]);
            const v4f one = bc<3>(nz(r.vf[0]));
            const v4f zero = {0.0f, 0.0f, 0.0f, 0.0f};
            Flags &f = r.flags;
            v4f acc;
            // Issue order: corners (z,y) = (15,15), (15,16), (16,15), (16,16);
            // each pair of corners takes x from VF15 then VF16.
            acc = fmac<kMul>(n13, one, zero, kXyzw, f);
            acc = fmac<kMadd>(n12, bc<2>(n15), nz(acc), kXyzw, f);
            acc = fmac<kMadd>(n11, bc<1>(n15), nz(acc), kXyzw, f);
            r.vf[1] = fmac<kMadd>(n10, bc<0>(n15), nz(acc), kXyzw, f);
            r.vf[2] = fmac<kMadd>(n10, bc<0>(n16), nz(acc), kXyzw, f);
            acc = fmac<kMul>(n13, one, zero, kXyzw, f);
            acc = fmac<kMadd>(n12, bc<2>(n15), nz(acc), kXyzw, f);
            acc = fmac<kMadd>(n11, bc<1>(n16), nz(acc), kXyzw, f);
            r.vf[3] = fmac<kMadd>(n10, bc<0>(n15), nz(acc), kXyzw, f);
            r.vf[4] = fmac<kMadd>(n10, bc<0>(n16), nz(acc), kXyzw, f);
            acc = fmac<kMul>(n13, one, zero, kXyzw, f);
            acc = fmac<kMadd>(n12, bc<2>(n16), nz(acc), kXyzw, f);
            acc = fmac<kMadd>(n11, bc<1>(n15), nz(acc), kXyzw, f);
            r.vf[5] = fmac<kMadd>(n10, bc<0>(n15), nz(acc), kXyzw, f);
            r.vf[6] = fmac<kMadd>(n10, bc<0>(n16), nz(acc), kXyzw, f);
            acc = fmac<kMul>(n13, one, zero, kXyzw, f);
            acc = fmac<kMadd>(n12, bc<2>(n16), nz(acc), kXyzw, f);
            acc = fmac<kMadd>(n11, bc<1>(n16), nz(acc), kXyzw, f);
            r.vf[7] = fmac<kMadd>(n10, bc<0>(n15), nz(acc), kXyzw, f);
            r.vf[8] = fmac<kMadd>(n10, bc<0>(n16), nz(acc), kXyzw, f);
            r.acc = acc;

            uint32_t c[9];
            for (int n = 1; n <= 8; ++n)
                c[n] = clip(r.vf[n]);
            // Clip register after the 8 CLIPs; FCGET pairs see (c1|c2),
            // (c3|c4), (c5|c6), (c7|c8) as their low codes.
            r.clip = (c[5] << 18) | (c[6] << 12) | (c[7] << 6) | c[8];
            const uint32_t any = c[1] | c[2] | c[3] | c[4] | c[5] | c[6] | c[7] | c[8];
            const uint32_t all = c[1] & c[2] & c[3] & c[4] & c[5] & c[6] & c[7] & c[8];
            r.vi[1] = static_cast<int16_t>(any & 0x3Fu);
            r.vi[2] = static_cast<int16_t>(all & 0x3Fu);
            r.vi[3] = static_cast<int16_t>((c[6] << 6) | c[7]);
            r.vi[4] = static_cast<int16_t>((c[7] << 6) | c[8]);
            r.vi[5] = 0x3F;
        }

        // 0xC18: the plane-set loop over the set bits of VI14.
        PS2X_VU1_ALWAYS_INLINE inline void sub0c18(Regs &r, const uint8_t *data)
        {
            Flags &f = r.flags;
            r.vi[11] = 1; // delay slot of 0xC18's IBEQ
            if (static_cast<int16_t>(r.vi[14]) == 0)
            {
                r.vi[1] = 0;
                return;
            }
            for (;;)
            {
                r.vi[1] = static_cast<int16_t>(r.vi[14] & r.vi[11]);
                r.vi[14] = static_cast<int16_t>(r.vi[14] - r.vi[1]);
                if (r.vi[1] != 0)
                {
                    for (int j = 0; j < 5; ++j)
                        r.vf[1 + j] = loadData(data, r.vi[10] + j);
                    const v4f n0 = nz(r.vf[0]);
                    r.vf[7] = merge(r.vf[7], fmac<kAdd>(n0, bc<3>(n0), n0, 0x8u, f), 0x8u);
                    r.vi[1] = 0x80;
                    const v4f n6 = nz(r.vf[6]);
                    for (int j = 1; j <= 5; ++j)
                        r.vf[j] = merge(r.vf[j], fmac<kMul>(nz(r.vf[j]), n6, n6, 0xEu, f), 0xEu);

                    // One plane: ADDAy.x, MADDAz.x, MADDAw.x, MSUBw.x vf0.
                    const v4f n6w = bc<3>(n6);
                    const auto opA = [&](int j)
                    {
                        const v4f nj = nz(r.vf[j]);
                        r.acc = merge(r.acc, fmac<kAdd>(nj, bc<1>(nj), nj, 0x8u, f), 0x8u);
                    };
                    const auto opB = [&](int j)
                    {
                        r.acc = merge(r.acc, fmac<kMadd>(nz(r.vf[7]), bc<2>(nz(r.vf[j])), nz(r.acc), 0x8u, f), 0x8u);
                    };
                    const auto opC = [&](int j)
                    {
                        r.acc = merge(r.acc, fmac<kMadd>(nz(r.vf[7]), bc<3>(nz(r.vf[j])), nz(r.acc), 0x8u, f), 0x8u);
                    };
                    const auto opD = [&]() -> uint32_t
                    {
                        (void)fmac<kMsub>(nz(r.vf[7]), n6w, nz(r.acc), 0x8u, f);
                        return f.mac;
                    };
                    // Issue order with the FMAND/IBEQ interleave (each FMAND
                    // sees the MSUBw four pairs back; a taken IBEQ still runs
                    // its delay slot, the next plane's MADDAz).
                    opA(1); opB(1); opC(1);
                    const uint32_t mac1 = opD();
                    opA(2); opB(2); opC(2);
                    const uint32_t mac2 = opD();
                    r.vi[2] = static_cast<int16_t>(mac1 & 0x80u);
                    bool rejected = false;
                    opA(3);
                    if (r.vi[2] == 0x80) { opB(3); rejected = true; }
                    if (!rejected)
                    {
                        opB(3); opC(3);
                        const uint32_t mac3 = opD();
                        r.vi[2] = static_cast<int16_t>(mac2 & 0x80u);
                        opA(4);
                        if (r.vi[2] == 0x80) { opB(4); rejected = true; }
                        if (!rejected)
                        {
                            opB(4); opC(4);
                            const uint32_t mac4 = opD();
                            r.vi[2] = static_cast<int16_t>(mac3 & 0x80u);
                            opA(5);
                            if (r.vi[2] == 0x80) { opB(5); rejected = true; }
                            if (!rejected)
                            {
                                opB(5); opC(5);
                                const uint32_t mac5 = opD();
                                r.vi[2] = static_cast<int16_t>(mac4 & 0x80u);
                                if (r.vi[2] == 0x80)
                                    rejected = true;
                                else
                                {
                                    r.vi[2] = static_cast<int16_t>(mac5 & 0x80u);
                                    if (r.vi[2] == 0x80)
                                        rejected = true;
                                }
                            }
                        }
                    }
                    if (!rejected)
                    {
                        r.vi[1] = 1;
                        return;
                    }
                }
                // 0xD88: next plane set.
                r.vi[10] = static_cast<int16_t>(r.vi[10] + 5);
                const bool more = static_cast<int16_t>(r.vi[14]) != 0;
                r.vi[11] = static_cast<int16_t>(r.vi[11] + r.vi[11]);
                if (!more)
                {
                    r.vi[1] = 0;
                    return;
                }
            }
        }
    }

    bool available()
    {
        return true;
    }

    // copyVu0StateToContext's view of the end state (shared by runDb8 and
    // runP570: both leave VF9/VF14, VF17-31, Q/P/I and ITOP untouched).
    PS2X_VU1_ALWAYS_INLINE inline void exportState(R5900Context *ctx, const Regs &r, uint32_t endPc)
    {
        ctx->vu0_vf[0] = _mm_set_ps(1.0f, 0.0f, 0.0f, 0.0f);
        for (int i = 1; i <= 16; ++i)
            if (i != 9 && i != 14)
                storeVf(ctx->vu0_vf[i], r.vf[i]);
        for (int i = 0; i < 16; ++i)
            ctx->vi[i] = static_cast<uint16_t>(r.vi[i]);
        ctx->vi[0] = 0;
        storeVf(ctx->vu0_acc, r.acc);
        alignas(16) uint32_t rWords[4];
        _mm_storeu_si128(reinterpret_cast<__m128i *>(rWords), _mm_castps_si128(ctx->vu0_r));
        ctx->vu0_r = _mm_castsi128_ps(_mm_set1_epi32(static_cast<int32_t>(0x3F800000u | (rWords[0] & 0x007FFFFFu))));
        ctx->vu0_mac_flags = r.flags.mac;
        ctx->vu0_clip_flags = r.clip;
        ctx->vu0_clip_flags2 = r.clip;
        ctx->vu0_status = static_cast<uint16_t>((ctx->vu0_status & 0xFF0u) | (r.flags.sticky << 6) | r.flags.cur);
        ctx->vu0_pc = endPc;
        ctx->vu0_tpc = endPc;
        ctx->vu0_vpu_stat = ctx->vu0_vpu_stat & 0xFF00u;
        ctx->vu0_vpu_stat2 = 0;
    }

    void runDb8(R5900Context *ctx, const uint8_t *vu0Data)
    {
        // VuCore::run's FP control (round toward zero + flush to zero);
        // usually already the EE thread's, so no FPCR write.
        ps2_fpmode::ScopedPs2Mode fpMode;

        Regs r;
        r.vf[0] = v4f{0.0f, 0.0f, 0.0f, 1.0f};
        for (int i = 1; i <= 16; ++i)
            r.vf[i] = loadVf(ctx->vu0_vf[i]);
        for (int i = 0; i < 16; ++i)
            r.vi[i] = static_cast<int16_t>(ctx->vi[i]);
        r.vi[0] = 0;
        r.acc = loadVf(ctx->vu0_acc);
        r.clip = ctx->vu0_clip_flags;
        const v4f vf20 = loadVf(ctx->vu0_vf[20]);
        const v4f vf21 = loadVf(ctx->vu0_vf[21]);
        const v4f vf22 = loadVf(ctx->vu0_vf[22]);

        uint32_t endPc = 0u;
        // 0xDB8
        for (int i = 0; i < 4; ++i)
            r.vf[10 + i] = loadData(vu0Data, 64 + i);
        r.vf[15] = vf20;
        r.vf[16] = vf21;
        r.vi[15] = (0xDE8 + 16) / 8;
        sub0ac8(r);
        // 0xDF8
        r.vi[13] = r.vi[1];
        if (r.vi[2] != 0)
        {
            r.vi[1] = 1;
            endPc = 0xE18u;
        }
        else
        {
            // 0xE18
            r.vf[6] = vf22;
            bool ended = false;
            if (r.vi[14] != 0)
            {
                r.vi[15] = (0xE28 + 16) / 8;
                r.vi[10] = 0x50;
                sub0c18(r, vu0Data);
                if (r.vi[1] != 0)
                {
                    r.vi[1] = 2;
                    endPc = 0xE58u;
                    ended = true;
                }
            }
            if (!ended)
            {
                // 0xE60
                if (r.vi[13] == 0)
                {
                    r.vi[1] = 0;
                    endPc = 0xE80u;
                }
                else
                {
                    // 0xE80
                    for (int i = 0; i < 4; ++i)
                        r.vf[10 + i] = loadData(vu0Data, 68 + i);
                    r.vf[15] = vf20;
                    r.vf[16] = vf21;
                    r.vi[15] = (0xEB0 + 16) / 8;
                    sub0ac8(r);
                    if (r.vi[1] == 0)
                    {
                        r.vi[1] = 3;
                        endPc = 0xEE0u;
                    }
                    else
                    {
                        r.vi[1] = 4;
                        endPc = 0xEF0u;
                    }
                }
            }
        }

        // copyVu0StateToContext's view of the end state.
        exportState(ctx, r, endPc);
    }

    void runP570(R5900Context *ctx)
    {
        // VNP1P2: the walker's AABB program. The four leading NOP pairs move
        // vf0 to vf0 (no FMAC, no flag write); the body is sub0ac8 with
        // VF10-13/15/16 from ctx (the walker's macro-op setup; .w lanes
        // carried over from earlier programs). No data-memory read, no BAL
        // (VCALLMS leaves vi15 alone), E at 0x6D0 so the end pc is 0x6E0.
        ps2_fpmode::ScopedPs2Mode fpMode;

        Regs r;
        r.vf[0] = v4f{0.0f, 0.0f, 0.0f, 1.0f};
        for (int i = 1; i <= 16; ++i)
            r.vf[i] = loadVf(ctx->vu0_vf[i]);
        for (int i = 0; i < 16; ++i)
            r.vi[i] = static_cast<int16_t>(ctx->vi[i]);
        r.vi[0] = 0;
        r.acc = loadVf(ctx->vu0_acc);
        r.clip = ctx->vu0_clip_flags;

        sub0ac8(r);

        exportState(ctx, r, 0x6E0u);
    }
#else
    bool available()
    {
        return false;
    }

    void runDb8(R5900Context *, const uint8_t *)
    {
    }

    void runP570(R5900Context *)
    {
    }
#endif

    bool imageMatches(const uint8_t *vu0Code, uint64_t generation)
    {
        struct Cache
        {
            const uint8_t *code = nullptr;
            uint64_t generation = 0;
            bool valid = false;
            bool match = false;
        };
        static thread_local Cache cache;
        if (cache.valid && cache.code == vu0Code && cache.generation == generation)
            return cache.match;
        cache.valid = true;
        cache.code = vu0Code;
        cache.generation = generation;
        cache.match = vu0Code != nullptr && XXH64(vu0Code, PS2_VU0_CODE_SIZE, 0) == kImageHash;
        return cache.match;
    }

    Vu0Snapshot snapshot(const R5900Context &ctx)
    {
        Vu0Snapshot s;
        std::memset(&s, 0, sizeof(s));
        for (int k = 0; k < 32; ++k)
            s.vf[k] = ctx.vu0_vf[k];
        s.acc = ctx.vu0_acc;
        s.r = ctx.vu0_r;
        for (int k = 0; k < 16; ++k)
            s.vi[k] = ctx.vi[k];
        s.q = ctx.vu0_q;
        s.p = ctx.vu0_p;
        s.i = ctx.vu0_i;
        s.status = ctx.vu0_status;
        s.mac = ctx.vu0_mac_flags;
        s.clip = ctx.vu0_clip_flags;
        s.clip2 = ctx.vu0_clip_flags2;
        s.vpuStat = ctx.vu0_vpu_stat;
        s.vpuStat2 = ctx.vu0_vpu_stat2;
        s.tpc = ctx.vu0_tpc;
        s.itop = ctx.vu0_itop;
        s.pc = ctx.vu0_pc;
        s.fbrst = ctx.vu0_fbrst;
        return s;
    }

    void restore(R5900Context &ctx, const Vu0Snapshot &s)
    {
        for (int k = 0; k < 32; ++k)
            ctx.vu0_vf[k] = s.vf[k];
        ctx.vu0_acc = s.acc;
        ctx.vu0_r = s.r;
        for (int k = 0; k < 16; ++k)
            ctx.vi[k] = s.vi[k];
        ctx.vu0_q = s.q;
        ctx.vu0_p = s.p;
        ctx.vu0_i = s.i;
        ctx.vu0_status = s.status;
        ctx.vu0_mac_flags = s.mac;
        ctx.vu0_clip_flags = s.clip;
        ctx.vu0_clip_flags2 = s.clip2;
        ctx.vu0_vpu_stat = s.vpuStat;
        ctx.vu0_vpu_stat2 = s.vpuStat2;
        ctx.vu0_tpc = s.tpc;
        ctx.vu0_itop = s.itop;
        ctx.vu0_pc = s.pc;
        ctx.vu0_fbrst = s.fbrst;
    }

    bool equal(const Vu0Snapshot &ref, const Vu0Snapshot &got, bool print)
    {
        bool same = true;
        const auto field = [&](const char *name, const void *a, const void *b, size_t size, uint32_t index)
        {
            if (std::memcmp(a, b, size) == 0)
                return;
            same = false;
            if (!print)
                return;
            uint32_t wa[4] = {}, wb[4] = {};
            std::memcpy(wa, a, size < 16 ? size : 16);
            std::memcpy(wb, b, size < 16 ? size : 16);
            std::fprintf(stderr, "[vnp1]   %s[%u] ref %08x %08x %08x %08x got %08x %08x %08x %08x\n", name, index,
                         wa[0], wa[1], wa[2], wa[3], wb[0], wb[1], wb[2], wb[3]);
        };
        for (uint32_t k = 0; k < 32u; ++k)
            field("vf", &ref.vf[k], &got.vf[k], sizeof(ref.vf[k]), k);
        for (uint32_t k = 0; k < 16u; ++k)
            field("vi", &ref.vi[k], &got.vi[k], sizeof(ref.vi[k]), k);
        field("acc", &ref.acc, &got.acc, sizeof(ref.acc), 0);
        field("r", &ref.r, &got.r, sizeof(ref.r), 0);
        field("q", &ref.q, &got.q, sizeof(ref.q), 0);
        field("p", &ref.p, &got.p, sizeof(ref.p), 0);
        field("i", &ref.i, &got.i, sizeof(ref.i), 0);
        field("status", &ref.status, &got.status, sizeof(ref.status), 0);
        field("mac", &ref.mac, &got.mac, sizeof(ref.mac), 0);
        field("clip", &ref.clip, &got.clip, sizeof(ref.clip), 0);
        field("clip2", &ref.clip2, &got.clip2, sizeof(ref.clip2), 0);
        field("vpu_stat", &ref.vpuStat, &got.vpuStat, sizeof(ref.vpuStat), 0);
        field("vpu_stat2", &ref.vpuStat2, &got.vpuStat2, sizeof(ref.vpuStat2), 0);
        field("tpc", &ref.tpc, &got.tpc, sizeof(ref.tpc), 0);
        field("itop", &ref.itop, &got.itop, sizeof(ref.itop), 0);
        field("pc", &ref.pc, &got.pc, sizeof(ref.pc), 0);
        field("fbrst", &ref.fbrst, &got.fbrst, sizeof(ref.fbrst), 0);
        return same;
    }

    void noteCheck(uint32_t startPc, const Vu0Snapshot &in, const Vu0Snapshot &ref, const Vu0Snapshot &got)
    {
        static uint64_t calls = 0, mismatches = 0;
        static uint64_t callsDb8 = 0, calls570 = 0, mismDb8 = 0, mism570 = 0;
        static uint64_t results[5] = {};
        ++calls;
        if (startPc == kStartPc570)
            ++calls570;
        else
            ++callsDb8;
        if (ref.vi[1] < 5u)
            ++results[ref.vi[1]];
        if (!equal(ref, got, false))
        {
            ++mismatches;
            if (startPc == kStartPc570)
                ++mism570;
            else
                ++mismDb8;
            if (mismatches <= 16u)
            {
                uint32_t w[12];
                std::memcpy(w, &in.vf[20], 16);
                std::memcpy(w + 4, &in.vf[21], 16);
                std::memcpy(w + 8, &in.vf[22], 16);
                std::fprintf(stderr,
                             "[vnp1] MISMATCH pc=%03x call=%llu vf20=%08x,%08x,%08x,%08x vf21=%08x,%08x,%08x,%08x "
                             "vf22=%08x,%08x,%08x,%08x vi14=%04x ref_vi1=%u got_vi1=%u\n",
                             startPc, static_cast<unsigned long long>(calls), w[0], w[1], w[2], w[3], w[4], w[5], w[6], w[7],
                             w[8], w[9], w[10], w[11], in.vi[14], ref.vi[1], got.vi[1]);
                (void)equal(ref, got, true);
            }
        }
        if ((calls & 0xFFFFu) == 0u || calls == 1024u)
            std::fprintf(stderr, "[vnp1] check calls=%llu mismatches=%llu results=%llu,%llu,%llu,%llu,%llu pc=db8:%llu,570:%llu mism_db8=%llu mism_570=%llu\n",
                         static_cast<unsigned long long>(calls), static_cast<unsigned long long>(mismatches),
                         static_cast<unsigned long long>(results[0]), static_cast<unsigned long long>(results[1]),
                         static_cast<unsigned long long>(results[2]), static_cast<unsigned long long>(results[3]),
                         static_cast<unsigned long long>(results[4]), static_cast<unsigned long long>(callsDb8),
                         static_cast<unsigned long long>(calls570), static_cast<unsigned long long>(mismDb8),
                         static_cast<unsigned long long>(mism570));
    }

    // split-input-1 #3 (Part 2): the VU0-start dispatch provider. The runtime
    // calls these at executeVU0Microprogram in the same order the moved block
    // ran: pc match, knob mode, image match, then serve (mode 1) or the
    // check-mode snapshot pair around the engine run. Check snapshots live
    // here (noteCheck already keeps its counters in function statics); the
    // EE is single-threaded.
    int visNativeMode()
    {
        static const int mode = []
        {
            const char *value = std::getenv("PS2X_SSX3_VIS_NATIVE");
            // Default on (VNP1P2: exact refactor, 3 det keys IDENTICAL); 0 = the VU0 engine, check = both.
            int m = 1;
            if (value != nullptr && std::strcmp(value, "check") == 0)
                m = 2;
            else if (value != nullptr && value[0] == '0')
                m = 0;
            if (m == 0)
                return 0;
            const char *floatMode = std::getenv("PS2X_VU_FLOAT");
            const bool pcsx2Float = floatMode != nullptr && std::strcmp(floatMode, "pcsx2") == 0;
            if (!available() || pcsx2Float)
            {
                std::fprintf(stderr, "[vnp1] PS2X_SSX3_VIS_NATIVE=%s ignored (available=%d pcsx2_float=%d)\n",
                             value, available() ? 1 : 0, pcsx2Float ? 1 : 0);
                return 0;
            }
            std::fprintf(stderr, "[vnp1] PS2X_SSX3_VIS_NATIVE=%s\n", m == 2 ? "check" : "1");
            return m;
        }();
        return mode;
    }

    bool visNativeMatchesPc(uint32_t startPC)
    {
        return startPC == kStartPc || startPC == kStartPc570;
    }

    void visNativeRun(R5900Context *ctx, const uint8_t *vu0Data, uint32_t startPC)
    {
        if (startPC == kStartPc570)
            runP570(ctx);
        else
            runDb8(ctx, vu0Data);
    }

    namespace
    {
        struct VisCheckState
        {
            Vu0Snapshot in;
            Vu0Snapshot native;
        };
        VisCheckState &visCheckState()
        {
            static VisCheckState st;
            return st;
        }
    }

    void visNativeCheckBegin(R5900Context *ctx, const uint8_t *vu0Data, uint32_t startPC)
    {
        VisCheckState &st = visCheckState();
        st.in = snapshot(*ctx);
        visNativeRun(ctx, vu0Data, startPC);
        st.native = snapshot(*ctx);
        restore(*ctx, st.in);
    }

    void visNativeCheckEnd(R5900Context *ctx, uint32_t startPC)
    {
        const VisCheckState &st = visCheckState();
        noteCheck(startPC, st.in, snapshot(*ctx), st.native);
    }
}
