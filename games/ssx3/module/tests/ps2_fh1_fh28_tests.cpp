#include "MiniTest.h"
#include "ps2_fh1_full120.h"

#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

namespace
{
float bitsToFloat(uint32_t b)
{
    float f = 0.0f;
    std::memcpy(&f, &b, 4);
    return f;
}

uint32_t floatToBits(float f)
{
    uint32_t b = 0u;
    std::memcpy(&b, &f, 4);
    return b;
}
} // namespace

void register_ps2_fh1_fh28_tests()
{
    using namespace ps2_fh1;
    MiniTest::Case("Ps2Fh1Fh28", [](TestCase &tc)
    {
        tc.Run("FH33 steering post-hook is classified only while active", [](TestCase &t)
        {
            HookConfig c; c.mode = Mode::Events; c.main = kFixGround2; c.guestActive = true;
            t.IsTrue(hookTableHit(buildHookInterest(c), 0u, 0x113e80u), "active steering hook");
            c.guestActive = false;
            t.IsFalse(hookTableHit(buildHookInterest(c), 0u, 0x113e80u), "inactive steering untouched");
            c.guestActive = true; c.main = allMain();
            t.IsFalse(hookTableHit(buildHookInterest(c), 0u, 0x113e80u), "default steering untouched");
        });


        tc.Run("FH32 C parser and audited camera query routing", [](TestCase &t)
        {
            t.IsTrue((allMain()&kFixJcam2)==0u,"opt in only");
            t.IsTrue((kFixJcam2&kFixGround2)==0u,"camera and steering masks independent");
            t.Equals(parseFix("all,jcam2,-jcam2").main,allMain(),"off mask unchanged");
            t.Equals(parseFix("all,jcam2").main,allMain()|kFixJcam2,"parses");
            HookConfig c; c.mode=Mode::Events;c.main=kFixJcam2;c.guestActive=true;
            for(uint32_t pc:{kJcam2Solver,kJcam2Launch,kJcam2Query})
                t.IsTrue(hookTableHit(buildHookInterest(c),0u,pc),"active hook hit");
            c.guestActive=false;
            t.IsFalse(hookTableHit(buildHookInterest(c),kJcam2QuerySite,kJcam2Query),"inactive miss");
            std::vector<uint8_t> ram(PS2_RAM_SIZE);
            R5900Context ctx{};SET_GPR_U32(&ctx,4,0x1000u);
            wr32(ram.data(),0x1004u,0x2000u);wr32(ram.data(),0x2788u,0x3000u);
            jcam2Reset();auto &shadow=jcam2Shadow(0x3000u);
            shadow.ready=true;shadow.body[0xacu/4u]=1u;
            t.IsFalse(jcam2Hook(ram.data(),&ctx,0x111111u,kJcam2Query,nullptr),"other consumers not intercepted");
            t.IsTrue(jcam2Hook(ram.data(),&ctx,kJcam2QuerySite,kJcam2Query,nullptr),"camera uses shadow");
            t.Equals(getRegU32(&ctx,2),1u,"contact published");
            shadow.body[0xacu/4u]=0u;
            jcam2Hook(ram.data(),&ctx,kJcam2QuerySite,kJcam2Query,nullptr);
            t.Equals(getRegU32(&ctx,2),0u,"absent contact stays false");
            jcam2Reset();
        });
        tc.Run("FH32 C private evaluation preserves live RAM CPU and cycles", [](TestCase &t)
        {
            std::vector<uint8_t> ram(PS2_RAM_SIZE,0x5au);
            R5900Context ctx{};PS2Runtime runtime;
            SET_GPR_U32(&ctx,4,0x1000u);SET_GPR_U32(&ctx,5,0x2000u);
            SET_GPR_U32(&ctx,6,0x3000u);SET_GPR_U32(&ctx,31,0x4000u);
            ctx.f[12]=1.0f/120.0f;
            wr32(ram.data(),0x49bf1cu,kHundredTwentieth);
            runtime.eeScheduler().reset(ram.data(),ctx);
            runtime.eeScheduler().publishSnapshot();
            const auto originalClock=runtime.eeScheduler().snapshot().eeCycle;
            const R5900Context original=ctx;
            const auto before=ram;
            jcam2Reset();auto &shadow=jcam2Shadow(0x1000u);
            const auto fn=+[](uint8_t *privateRam,R5900Context *privateCtx,PS2Runtime *rt)
            {
                // Nested preview work is bounded and never charges guest time.
                (void)rt->eeCheckpointDue(100u);
                wr32(privateRam,0x1000u+0xacu,1u);
                wr32(privateRam,0x5000u,0xdeadbeefu); // simulated geometry cache write
                uint32_t dt=0u;std::memcpy(&dt,&privateCtx->f[12],4u);
                wr32(privateRam,0x1000u+0x98u,dt);
                privateCtx->pc=getRegU32(privateCtx,31);
            };
            t.IsTrue(jcam2Service(ram.data(),ctx,runtime,shadow,10u,fn),"private evaluation complete");
            t.IsTrue(ram==before,"all live RAM untouched, including stack and pools");
            t.IsTrue(std::memcmp(&ctx,&original,sizeof(ctx))==0,"all live CPU fields untouched");
            t.Equals(shadow.body[0x98u/4u],kSixtieth,"stock dt supplied");
            t.IsTrue(jcam2Contact(shadow),"shadow contact captured");
            t.IsFalse(runtime.m_fh32Preview,"guard released");
            runtime.eeScheduler().publishSnapshot();
            t.Equals(runtime.eeScheduler().snapshot().eeCycle,originalClock,"preview excludes cycles from live scheduler");
            jcam2Reset();
        });
        tc.Run("FH32 C restart parity, held publication and pause lifecycle", [](TestCase &t)
        {
            std::vector<uint8_t> ram(PS2_RAM_SIZE);
            R5900Context ctx{};SET_GPR_U32(&ctx,4,0x1000u);
            for(uint64_t first:{10u,11u})
            {
                jcam2Reset();auto &s=jcam2Shadow(0x1000u);
                t.IsTrue(jcam2Due(s,first),"immediate service on either parity");
                s.ready=true;s.tick=first;s.body[0xacu/4u]=0u;
                t.IsFalse(jcam2Due(s,first+1u),"alternate update held");
                t.IsTrue(jcam2Due(s,first+2u),"stock cadence");
                t.IsFalse(jcam2Contact(s),"no contact invented on held update");
                jcam2Hook(ram.data(),&ctx,0x139a04u,kJcam2Launch,nullptr);
                t.IsTrue(jcam2Due(s,first+1u),"restart services immediately regardless of parity");
                jcam2Reset();
                t.IsFalse(jcam2Shadow(0x1000u).ready,"event exit/re-entry discards publication");
            }
            jcam2Reset();
        });
        tc.Run("FH30 envfilt is in all (PRM1, Brad 10-11) and its table follows active events", [](TestCase &t)
        {
            t.IsTrue((allMain() & kFixEnvFilt) != 0u, "in all");
            const FixMasks f = parseFix("all,envfilt");
            t.IsTrue(f.ok, "parses");
            t.Equals(f.main, allMain(), "already in all");
            t.Equals(parseFix("all,envfilt,-envfilt").main, allMain() & ~static_cast<uint64_t>(kFixEnvFilt), "opt-out");
            HookConfig c;
            c.mode = Mode::Events;
            c.main = kFixEnvFilt;
            c.guestActive = true;
            t.IsTrue(hookTableHit(buildHookInterest(c), kEnvFiltSite, kEnvFiltUpdate), "active hit");
            c.guestActive = false;
            t.IsFalse(hookTableHit(buildHookInterest(c), kEnvFiltSite, kEnvFiltUpdate), "inactive miss");
        });

        tc.Run("FH30 squared-gain half input composes to stock retention", [](TestCase &t)
        {
            for (const float f : {0.00001f, 0.01f, 0.08f, 0.3f, 0.8f, 0.999f})
            {
                const double h = envFiltHalfInput(f);
                const double halfRetention = 1.0 - h * h;
                t.IsTrue(std::fabs(halfRetention * halfRetention - (1.0 - double(f)*f)) < 1e-7,
                         "pair retains stock gap");
            }
            // Boundary/copy branches and invalid inputs retain their exact bits.
            for (const uint32_t b : {0u, 0x80000000u, 0xbf800000u, 0x3f800000u,
                                     0x40000000u, 0x7f800000u, 0x7fc00000u})
                t.Equals(floatToBits(envFiltHalfInput(bitsToFloat(b))), b, "preserves non-response input");
        });

        tc.Run("FH30 pre-hook converts only the audited caller", [](TestCase &t)
        {
            R5900Context ctx{};
            ctx.f[12] = 0.08f;
            envFiltPreHook(&ctx, 0x2c0990u, kEnvFiltUpdate);
            t.Equals(floatToBits(ctx.f[12]), floatToBits(0.08f), "other interpolation untouched");
            envFiltPreHook(&ctx, kEnvFiltSite, 0x123456u);
            t.Equals(floatToBits(ctx.f[12]), floatToBits(0.08f), "other target untouched");
            envFiltPreHook(&ctx, kEnvFiltSite, kEnvFiltUpdate);
            t.IsTrue(std::fabs(ctx.f[12] - 0.056613925f) < 1e-7f, "live input converted");
            envFiltPreHook(nullptr, kEnvFiltSite, kEnvFiltUpdate);
        });

        tc.Run("pool overrides only fire for their group", [](TestCase &t)
        {
            const uint64_t all28 =
                static_cast<uint64_t>(kFixJcam | kFixPose | kFixPid | kFixC2Cap | kFixSpawn);
            // Each override fires only with its own bit.
            t.IsTrue(fh28PoolOverride(0x49f628u, static_cast<uint64_t>(kFixSpawn)), "spawn word");
            t.IsTrue(fh28PoolOverride(0x49f628u, allMain()), "spawn word fires under all (PRM1)");
            t.IsTrue(fh28PoolOverride(0x49c5fcu, static_cast<uint64_t>(kFixC2Cap)), "c2cap word");
            t.IsTrue(fh28PoolOverride(0x49c5fcu, allMain()), "c2cap word fires under all (PRM1)");
            t.IsFalse(fh28PoolOverride(0x49c624u, all28), "pid span stays patched");
            t.IsFalse(fh28PoolOverride(0x49c628u, all28), "pid base stays patched");
            // Nothing else is overridden, even with every FH28 bit on.
            t.IsFalse(fh28PoolOverride(0x49c650u, all28), "unpatched jump clock untouched");
            t.IsFalse(fh28PoolOverride(0x49bb04u, all28), "pose word untouched");
            t.IsFalse(fh28PoolOverride(0x49be9cu, all28), "rider word untouched");
            t.IsFalse(fh28PoolOverride(0x49f628u, 0u), "empty mask overrides nothing");
        });

        tc.Run("jcam retention correction composes to d per pair", [](TestCase &t)
        {
            // FXT1 T1b: d = 0.9649, offsets decay by exactly d per stock tick.
            // The guest multiplies by d; the hook sees the product and divides
            // by sqrt(d), so each corrected update retains sqrt(d) and the
            // pair recovers d.
            const float d = 0.9649f, pre = -9.582f;
            const uint32_t postBits = floatToBits(pre * d); // the guest multiply
            const float got = bitsToFloat(jcamRetainCorrect(postBits, floatToBits(d)));
            t.IsTrue(std::fabs(got - pre * std::sqrt(d)) <= 1e-5f, "one update retains sqrt(d)");
            const float step = got / pre;
            t.IsTrue(std::fabs(step * step - d) <= 1e-6f, "pair composes to d");
        });

        tc.Run("jcam retention correction guards bad d", [](TestCase &t)
        {
            const uint32_t offBits = floatToBits(-9.582f);
            t.Equals(jcamRetainCorrect(offBits, 0u), offBits, "d = 0 leaves the offset");
            t.Equals(jcamRetainCorrect(offBits, 0x80000000u), offBits, "d = -0 leaves the offset");
            t.Equals(jcamRetainCorrect(offBits, 0xbf800000u), offBits, "negative d leaves the offset");
            t.Equals(jcamRetainCorrect(offBits, 0x7fc00000u), offBits, "NaN d leaves the offset");
            t.Equals(jcamRetainCorrect(offBits, 0x7f800000u), offBits, "Inf d leaves the offset");
            t.Equals(jcamRetainCorrect(offBits, 0x40000000u), offBits, "d = 2 leaves the offset");
            t.Equals(jcamRetainCorrect(0u, floatToBits(0.9649f)), 0u, "zero offset stays zero");
        });

        tc.Run("pose halving is an exact power-of-two scale", [](TestCase &t)
        {
            // The pool table halves 1/30 (0x3d088889) to 1/60 (kSixtieth);
            // assert the assumption the table bakes in.
            t.Equals(static_cast<uint32_t>(0x3d088889u) >> 0u, 0x3d088889u, "stock bits");
            t.Equals(kSixtieth, 0x3c888889u, "replacement bits");
            t.IsTrue(bitsToFloat(kSixtieth) == bitsToFloat(0x3d088889u) * 0.5f, "exact half");
        });

        tc.Run("pid hold: zero gains in, history and idx rolled back out", [](TestCase &t)
        {
            // FH29: the second update of a pair runs 0x162c78 with zero gains
            // (out = O[p]) and its slot + idx write is undone afterwards.
            std::vector<uint8_t> ram(PS2_RAM_SIZE, 0u);
            const uint32_t shot = 0x15812d0u;
            const float kp = 0.0113945f, ki = 0.0f, kd = 0.00127851f;
            wr32(ram.data(), shot + kPidIdx, 2u);
            for (uint32_t s = 0; s < 5; ++s)
                for (int h = 0; h < 3; ++h)
                    wr32(ram.data(), shot + kPidHist[h] + s * 4u, floatToBits(0.1f * (h + 1) + 0.01f * s));
            wr32(ram.data(), shot + kPidGains[0], floatToBits(kp));
            wr32(ram.data(), shot + kPidGains[1], floatToBits(ki));
            wr32(ram.data(), shot + kPidGains[2], floatToBits(kd));
            const std::vector<uint8_t> before = ram;
            uint32_t saved[7] = {};
            t.IsTrue(pidHoldArm(ram.data(), shot, saved), "arms");
            uint32_t g = 1u;
            for (uint32_t off : kPidGains)
            {
                rd32(ram.data(), shot + off, g);
                t.Equals(g, 0u, "gain zeroed for the held call");
            }
            // The held call: out = O[p] + 0, writes E/O/R at idx 2, idx -> 3.
            uint32_t prevOut = 0u;
            rd32(ram.data(), shot + kPidHist[1] + 1u * 4u, prevOut);
            wr32(ram.data(), shot + kPidHist[0] + 8u, floatToBits(0.77f));
            wr32(ram.data(), shot + kPidHist[1] + 8u, prevOut);
            wr32(ram.data(), shot + kPidHist[2] + 8u, floatToBits(0.77f - bitsToFloat(prevOut)));
            wr32(ram.data(), shot + kPidIdx, 3u);
            pidHoldRelease(ram.data(), shot, saved, true);
            t.IsTrue(ram == before, "returned call: state exactly as before (gains, slot, idx)");
        });

        tc.Run("pid hold: a suspended call gets its gains back and keeps its writes", [](TestCase &t)
        {
            std::vector<uint8_t> ram(PS2_RAM_SIZE, 0u);
            const uint32_t shot = 0x15812d0u;
            wr32(ram.data(), shot + kPidIdx, 4u);
            wr32(ram.data(), shot + kPidGains[0], floatToBits(0.0113945f));
            wr32(ram.data(), shot + kPidGains[2], floatToBits(0.00127851f));
            uint32_t saved[7] = {};
            t.IsTrue(pidHoldArm(ram.data(), shot, saved), "arms");
            wr32(ram.data(), shot + kPidIdx, 0u); // the resumed call commits (4 -> 0)
            pidHoldRelease(ram.data(), shot, saved, false);
            uint32_t v = 0u;
            rd32(ram.data(), shot + kPidGains[0], v);
            t.Equals(v, floatToBits(0.0113945f), "Kp back");
            rd32(ram.data(), shot + kPidGains[2], v);
            t.Equals(v, floatToBits(0.00127851f), "Kd back");
            rd32(ram.data(), shot + kPidIdx, v);
            t.Equals(v, 0u, "suspended call's idx write kept");
        });

        tc.Run("pid hold refuses a bad ring index", [](TestCase &t)
        {
            std::vector<uint8_t> ram(PS2_RAM_SIZE, 0u);
            const uint32_t shot = 0x15812d0u;
            wr32(ram.data(), shot + kPidIdx, 5u);
            wr32(ram.data(), shot + kPidGains[0], floatToBits(0.5f));
            uint32_t saved[7] = {};
            t.IsFalse(pidHoldArm(ram.data(), shot, saved), "idx 5 refused");
            uint32_t v = 0u;
            rd32(ram.data(), shot + kPidGains[0], v);
            t.Equals(v, floatToBits(0.5f), "gains untouched on refusal");
            t.IsFalse(pidHoldArm(ram.data(), PS2_RAM_SIZE, saved), "out-of-RAM shot refused");
        });

        tc.Run("pid: two 120 updates with the hold equal one stock recurrence step", [](TestCase &t)
        {
            // Model of the guest recurrence (0x162df0-0x162efc) on the ring.
            struct Pid
            {
                float E[5], O[5], R[5], kp, ki, kd;
                uint32_t idx;
                float step(float e)
                {
                    const uint32_t p = (idx + 4u) % 5u;
                    float sum = 0.0f;
                    for (float r : R)
                        sum += r;
                    const float out = O[p] + kp * (e - O[p]) + ki * sum + kd * (E[p] - e);
                    E[idx] = e;
                    O[idx] = out;
                    R[idx] = e - out;
                    idx = (idx + 1u) % 5u;
                    return out;
                }
            };
            Pid stock{{0.1f, 0.2f, 0.3f, 0.4f, 0.5f}, {0.01f, 0.02f, 0.03f, 0.04f, 0.05f}, {}, 0.0113945f, 0.001f, 0.00127851f, 3u};
            Pid held = stock;
            const float e = 0.51f;
            const float outStock = stock.step(e);
            const float outCommit = held.step(e); // first update of the pair
            // Second update: zero gains, then roll back slot + idx.
            Pid snap = held;
            held.kp = held.ki = held.kd = 0.0f;
            const float outHeld = held.step(0.53f);
            t.IsTrue(outHeld == outCommit, "held call reproduces the committed output");
            held = snap; // release: gains, slot and idx back
            t.IsTrue(outCommit == outStock, "commit equals the stock step");
            t.IsTrue(std::memcmp(&held, &stock, sizeof(Pid)) == 0, "pair leaves the stock state");
        });

        tc.Run("hook interest lists the FH28 targets only when on", [](TestCase &t)
        {
            HookConfig c;
            c.mode = Mode::Events;
            c.main = allMain();
            c.guestActive = true;
            t.IsFalse(hookTableHit(buildHookInterest(c), 0x000001u, 0x1635f8u), "jcam out of all");
            t.IsTrue(hookTableHit(buildHookInterest(c), 0x000001u, 0x162998u), "c2cap in all (PRM1)");
            t.IsFalse(hookTableHit(buildHookInterest(c), 0x000001u, 0x162c78u), "pid out of all");
            c.main |= static_cast<uint64_t>(kFixJcam | kFixPose | kFixPid | kFixC2Cap | kFixSpawn);
            const HookInterest hi = buildHookInterest(c);
            t.IsTrue(hookTableHit(hi, 0x000001u, 0x1635f8u), "jcam tgt hits");
            t.IsTrue(hookTableHit(hi, 0x000001u, 0x162c78u), "pid tgt hits (FH29)");
            t.IsTrue(hookTableHit(hi, 0x000001u, 0x162998u), "c2cap tgt hits");
            t.IsTrue(hookTableHit(hi, 0x3171b4u, 0x000001u), "parity src hits");
            c.guestActive = false;
            const HookInterest idle = buildHookInterest(c);
            t.IsFalse(hookTableHit(idle, 0x000001u, 0x1635f8u), "jcam out while inactive");
            t.IsFalse(hookTableHit(idle, 0x000001u, 0x162998u), "c2cap out while inactive");
            t.IsFalse(hookTableHit(idle, 0x000001u, 0x162c78u), "pid out while inactive");
        });

        tc.Run("post-call unwind restores a pid hold and clears the record", [](TestCase &t)
        {
            // CAM2 R1: a pid call unwound past its return (checkpoint, longjmp,
            // transfer) must get its gains, ring slot and idx back; the record
            // is fully cleared so no later return can match it stale.
            const PostCall keepPost = g_post;
            const bool keepArmed = g_postArmed;
            std::vector<uint8_t> ram(PS2_RAM_SIZE, 0u);
            const uint32_t shot = 0x100000u;
            wr32(ram.data(), shot + kPidIdx, 2u);
            for (uint32_t s = 0; s < 5; ++s)
                for (int h = 0; h < 3; ++h)
                    wr32(ram.data(), shot + kPidHist[h] + s * 4u, floatToBits(0.1f * (h + 1) + 0.01f * s));
            wr32(ram.data(), shot + kPidGains[0], floatToBits(0.0113945f));
            wr32(ram.data(), shot + kPidGains[1], floatToBits(0.0f));
            wr32(ram.data(), shot + kPidGains[2], floatToBits(0.00127851f));
            const std::vector<uint8_t> before = ram;
            uint32_t saved[7] = {};
            t.IsTrue(pidHoldArm(ram.data(), shot, saved), "arms");
            g_post.target = kPidUpdate; g_post.sp = 0x11u; g_post.obj = shot;
            g_post.kind = 6u;
            for (int i = 0; i < 7; ++i) g_post.saved[i] = saved[i];
            g_postArmed = true;
            // The callee starts (slot + idx advance) but never returns.
            wr32(ram.data(), shot + kPidHist[0] + 8u, floatToBits(0.77f));
            wr32(ram.data(), shot + kPidIdx, 3u);
            postUnwindRestore(ram.data());
            t.IsTrue(ram == before, "pre-call state back (gains, slot, idx)");
            t.IsFalse(g_postArmed, "disarmed");
            t.Equals(g_post.target, 0u, "target cleared");
            // Idempotent, and the cleared record matches no return.
            postUnwindRestore(ram.data());
            t.IsTrue(ram == before, "second restore is a no-op");
            R5900Context ctx{};
            const uint32_t sp = 0x11u;
            std::memcpy(&ctx.r[29], &sp, 4);
            onReturn(ram.data(), &ctx, kPidUpdate, true);
            t.IsTrue(ram == before, "stale return matches nothing");
            g_post = keepPost; g_postArmed = keepArmed;
        });

        tc.Run("post-call unwind restores jcam n; save-only kinds just clear", [](TestCase &t)
        {
            const PostCall keepPost = g_post;
            const bool keepArmed = g_postArmed;
            std::vector<uint8_t> ram(PS2_RAM_SIZE, 0u);
            const uint32_t shot = 0x100000u;
            // jcam: the odd-update bump is reverted.
            wr32(ram.data(), shot + 0x2c4u, 7u);
            g_post.obj = shot; g_post.kind = 5u; g_post.saved[0] = 7u;
            g_post.target = kJcamJump; g_post.sp = 0x22u; g_postArmed = true;
            wr32(ram.data(), shot + 0x2c4u, 8u); // the pre-hook bump
            postUnwindRestore(ram.data());
            uint32_t n = 0u;
            rd32(ram.data(), shot + 0x2c4u, n);
            t.Equals(n, 7u, "bump reverted");
            t.IsFalse(g_postArmed, "disarmed");
            // flags: save-only, RAM untouched, record cleared.
            const uint32_t obj = 0x110000u;
            for (int i = 0; i < 6; ++i)
                wr32(ram.data(), obj + kFlagPhaseOffs[i], floatToBits(0.5f));
            g_post.obj = obj; g_post.kind = 0u; g_postArmed = true;
            for (int i = 0; i < 6; ++i)
                wr32(ram.data(), obj + kFlagPhaseOffs[i], floatToBits(0.9f)); // callee advanced
            const std::vector<uint8_t> afterCall = ram;
            postUnwindRestore(ram.data());
            t.IsTrue(ram == afterCall, "no RAM undo for save-only kinds");
            t.IsFalse(g_postArmed, "disarmed");
            g_post = keepPost; g_postArmed = keepArmed;
        });

        tc.Run("pre-hook arming is call-kind-only", [](TestCase &t)
        {
            // CAM2 R1b: a pre-hook must not clobber a record armed by a
            // different kind (a lost pid record sticks zeroed gains). Same-kind
            // re-arm still overwrites (fresh-read corrections stay valid).
            const PostCall keepPost = g_post;
            const bool keepArmed = g_postArmed, keepOdd = g_rngOdd;
            std::vector<uint8_t> ram(PS2_RAM_SIZE, 0u);
            R5900Context ctx{};
            auto setReg = [&](int r, uint32_t v) { std::memcpy(&ctx.r[r], &v, 4); };
            const uint32_t shot = 0x100000u, obj2 = 0x110000u, scratch = 0x120000u, shot2 = 0x130000u;
            // An armed pid record with distinct saved state.
            wr32(ram.data(), shot + kPidIdx, 1u);
            wr32(ram.data(), shot + kPidGains[0], floatToBits(0.0113945f));
            uint32_t saved[7] = {};
            t.IsTrue(pidHoldArm(ram.data(), shot, saved), "pid arms");
            g_postArmed = true;
            g_post.target = kPidUpdate; g_post.sp = 0x11u; g_post.obj = shot; g_post.kind = 6u;
            for (int i = 0; i < 7; ++i) g_post.saved[i] = saved[i] + 100u; // distinct
            const PostCall pidRecord = g_post;
            // flags must refuse: record and phases untouched.
            for (int i = 0; i < 6; ++i)
                wr32(ram.data(), obj2 + kFlagPhaseOffs[i], floatToBits(0.25f));
            setReg(4, obj2); setReg(29, 0x33u);
            flagsPreHook(ram.data(), &ctx, kFlagUpdate);
            t.IsTrue(std::memcmp(&g_post, &pidRecord, sizeof(PostCall)) == 0, "flags refuses a pid record");
            // jcam must refuse before its counter bump.
            g_rngOdd = true;
            wr32(ram.data(), scratch + 0x60u, 1u);
            wr32(ram.data(), obj2 + 0x2d0u, 0u);
            wr32(ram.data(), obj2 + 0x2c4u, 9u);
            setReg(4, obj2); setReg(5, scratch);
            jcamPreHook(ram.data(), &ctx, kJcamJump);
            t.IsTrue(std::memcmp(&g_post, &pidRecord, sizeof(PostCall)) == 0, "jcam refuses a pid record");
            uint32_t n = 0u;
            rd32(ram.data(), obj2 + 0x2c4u, n);
            t.Equals(n, 9u, "refused jcam leaves the counter alone");
            // pid keeps refusing while armed (existing behavior): a fresh
            // shot's gains stay live and the old record stands.
            wr32(ram.data(), shot2 + kPidIdx, 1u);
            wr32(ram.data(), shot2 + kPidGains[0], floatToBits(0.5f));
            setReg(4, shot2);
            pidPreHook(ram.data(), &ctx, kPidUpdate);
            uint32_t g = 0u;
            rd32(ram.data(), shot2 + kPidGains[0], g);
            t.Equals(g, floatToBits(0.5f), "fresh gains untouched while armed");
            t.IsTrue(std::memcmp(&g_post, &pidRecord, sizeof(PostCall)) == 0, "pid record intact");
            // Unarmed: flags arms normally; same-kind re-arms.
            g_post = PostCall{}; g_postArmed = false;
            setReg(4, obj2);
            flagsPreHook(ram.data(), &ctx, kFlagUpdate);
            t.IsTrue(g_postArmed && g_post.kind == 0u && g_post.obj == obj2, "flags arms when clear");
            setReg(4, shot);
            for (int i = 0; i < 6; ++i)
                wr32(ram.data(), shot + kFlagPhaseOffs[i], floatToBits(0.75f));
            flagsPreHook(ram.data(), &ctx, kFlagUpdate);
            uint32_t s0 = 0u;
            rd32(ram.data(), shot + kFlagPhaseOffs[0], s0);
            t.IsTrue(g_postArmed && g_post.obj == shot && g_post.saved[0] == s0, "same-kind re-arm overwrites");
            g_post = keepPost; g_postArmed = keepArmed; g_rngOdd = keepOdd;
        });
    });
}
