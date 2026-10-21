#include "MiniTest.h"
#include "ps2_fh1_full120.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>

static ps2_fh1::HookConfig hk1AllActive()
{
    ps2_fh1::HookConfig c;
    c.mode = ps2_fh1::Mode::Events;
    c.main = ps2_fh1::allMain() | ps2_fh1::kFixLift; // all plus the opt-in lift (covers every census hook)
    c.fix12 = ps2_fh1::all12();
    c.guestActive = true;
    c.draw = true;
    c.tapOn = true;
    c.tapCountPcs.push_back(0x445566u);
    c.histRanges = false;
    c.srcTgts.push_back(0xdead00u);
    c.fh26s = 0x1234u;
    c.fh9r = 0u;
    return c;
}

void register_ps2_fh1_hooktable_tests()
{
    using namespace ps2_fh1;
    MiniTest::Case("Ps2Fh1HookTable", [](TestCase &tc)
    {
        tc.Run("census sources hit (FIX=all, events, active)", [](TestCase &t)
        {
            const HookInterest hi = buildHookInterest(hk1AllActive());
            t.IsFalse(hi.always, "no always-run without fh9/hist");
            const uint32_t dead = 0x000001u; // not a real PC: row must hit via src only
            const uint32_t srcs[] = {
                0x12912cu, 0x113dccu, 0x3171b4u, // events facts + parity
                0x230bb8u,                       // sessionSkip
                0x2f3be0u, 0x390c98u,            // rng Bernoulli rolls
                0x11a3f0u,                       // trick combo
                0x10bdccu, 0x10da6cu, 0x10dc30u, 0x10b8acu, // aiGate gates
                0x128f20u, 0x11198cu,            // particle pass callers
                0x1197acu,                       // bonus rate store
                0x15f0ecu,                       // lift probe
                0x317208u, 0x31723cu,            // draw gates
            };
            for (uint32_t s : srcs)
                t.IsTrue(hookTableHit(hi, s, dead), "src hits");
        });

        tc.Run("census targets hit (FIX=all, events, active)", [](TestCase &t)
        {
            const HookInterest hi = buildHookInterest(hk1AllActive());
            const uint32_t dead = 0x000001u;
            const uint32_t tgts[] = {
                0x111408u, 0x1013a8u, 0x12a250u, 0x26f4a8u, // events facts
                0x317348u, 0x1e1458u,                        // clock domains
                0x114298u,                                   // launch wall response
                0x3710d0u, 0x115d48u, 0x3177f0u, 0x390c60u,  // rng cadence
                0x117638u,                                   // trick accrual
                0x1298c8u,                                   // aiGate race-tick read
                0x2dd0b8u,                                   // particle pass
                0x1176f8u,                                   // bonus rate halve
                0x32e100u,                                   // lift probe
                0x34b818u,                                   // flag update
                0x395510u,                                   // rclock getter
                0x346258u, 0x35f7d0u, 0x35f410u, 0x341e48u, 0x341ec0u, 0x341f38u,
                0x2e2260u, // fh12 spin/texanim/loops/fxtimer
                0xdead00u,  // PS2X_FH1_SRC watch
                0x133308u,  // FH26 stick tap
                0x111728u,  // IN4 rider dispatcher
                0x22b008u,  // draw render slot
                0x2306b8u,  // tap update target
                0x445566u,  // tap count pc
            };
            for (uint32_t g : tgts)
                t.IsTrue(hookTableHit(hi, dead, g), "tgt hits");
        });

        tc.Run("random other PCs miss", [](TestCase &t)
        {
            const HookInterest hi = buildHookInterest(hk1AllActive());
            uint32_t s = 0x12345678u;
            auto next = [&] {
                s = s * 1664525u + 1013904223u;
                return (s >> 8) & 0xffffffu;
            };
            int checked = 0;
            while (checked < 512)
            {
                const uint32_t src = next(), tgt = next();
                if (hookTableHit(hi, src, tgt))
                    continue; // a census PC by chance: not a miss sample
                ++checked;
            }
            t.Equals(checked, 512, "512 miss samples");
            // Spot checks far from any game PC miss.
            t.IsFalse(hookTableHit(hi, 0x000002u, 0x000003u), "tiny pcs miss");
            t.IsFalse(hookTableHit(hi, 0xbcbcbcu, 0xcdcdcdu), "mid pcs miss");
        });

        tc.Run("always-run diagnostics", [](TestCase &t)
        {
            HookConfig fh9 = hk1AllActive();
            fh9.fh9r = 0x77u;
            t.IsTrue(buildHookInterest(fh9).always, "fh9 tap forces always");
            HookConfig hist = hk1AllActive();
            hist.histRanges = true;
            t.IsTrue(buildHookInterest(hist).always, "hist ranges force always");
        });

        tc.Run("inactive events drop on-gated PCs, keep entry/exit state", [](TestCase &t)
        {
            HookConfig c = hk1AllActive();
            c.guestActive = false;
            const HookInterest hi = buildHookInterest(c);
            t.IsFalse(hi.always, "no always");
            // On-gated hooks are out while the guest flip is inactive.
            t.IsFalse(hookTableHit(hi, 0x000001u, 0x317348u), "clock tgt out");
            t.IsFalse(hookTableHit(hi, 0x230bb8u, 0x000001u), "session src out");
            // The draw render slot stays via the tap counters (tapOn), but the
            // draw gate sources are out while inactive.
            t.IsFalse(hookTableHit(hi, 0x317208u, 0x000001u), "draw gate src out");
            t.IsTrue(hookTableHit(hi, 0x000001u, 0x22b008u), "render tgt stays (tap)");
            t.IsFalse(hookTableHit(hi, 0xaaa000u, 0x000001u), "lab src out");
            // Entry/exit detection, rclock continuity and mode-agnostic taps stay.
            t.IsTrue(hookTableHit(hi, 0x3171b4u, 0x000001u), "app-update src stays");
            t.IsTrue(hookTableHit(hi, 0x000001u, 0x26f4a8u), "session tgt stays (finish fact)");
            t.IsTrue(hookTableHit(hi, 0x000001u, 0x395510u), "rclock stays");
            t.IsTrue(hookTableHit(hi, 0x000001u, 0x111728u), "in4 stays");
        });

        tc.Run("FH27: bonusflip recorder (any phase) and loops2 ctor (active)", [](TestCase &t)
        {
            HookConfig c = hk1AllActive();
            c.main &= ~static_cast<uint64_t>(kFixBonusFlip); // DEF1: bonusflip is in all; clear it for the negative case
            t.IsFalse(hookTableHit(buildHookInterest(c), 0x000001u, 0x117c28u), "no recorder without bonusflip");
            c.main |= kFixBonusFlip;
            t.IsTrue(hookTableHit(buildHookInterest(c), 0x000001u, 0x117c28u), "recorder hits (active)");
            t.IsTrue(hookTableHit(buildHookInterest(c), 0x000001u, 0x341aa0u), "loops2 ctor hits (active)");
            c.guestActive = false;
            t.IsTrue(hookTableHit(buildHookInterest(c), 0x000001u, 0x117c28u), "recorder hits (stock window)");
        });

        tc.Run("always mode lists the manager-init site", [](TestCase &t)
        {
            HookConfig c = hk1AllActive();
            c.mode = Mode::Always;
            const HookInterest hi = buildHookInterest(c);
            t.IsTrue(hookTableHit(hi, 0x316da8u, 0x000001u), "kHookSite src hits");
        });

        // REV1 (RV32 F6): every PC onBranchT can act on must be listed by
        // buildHookInterest, or the hook is silently dead in table mode.
        // Census derived 2026-10-12 from every sourcePc/targetPc comparison
        // in onBranchT and the hook bodies it calls (grep targetPc/sourcePc
        // in ps2_fh1_full120.h + ps2_fh1_pub.h + ps2_fh1_life2.inl). A new
        // hook extends this list in the same commit. Deliberate exceptions,
        // dispatched outside onBranch: kInterval (function-table wrapper in
        // ps2_runtime.cpp) and the onReturn targets (armed post records).
        tc.Run("REV1: onBranchT PC census is complete in buildHookInterest", [](TestCase &t)
        {
            HookConfig c;
            c.mode = Mode::Events;
            c.main = ~0ull; // every FIX bit, named or not: BHI over-approximates per bit
            c.fix12 = ~0u;
            c.guestActive = true;
            c.draw = true;
            c.tapOn = true;
            c.replay = true;
            c.fh26s = 1u;
            const HookInterest hi = buildHookInterest(c);
            t.IsFalse(hi.always, "census config is a real table (fh9/hist off)");
            const uint32_t dead = 0x000001u;
            char msg[96];
            auto pair = [&](uint32_t src, uint32_t tgt) {
                std::snprintf(msg, sizeof(msg), "pair %06x->%06x hits", src, tgt);
                t.IsTrue(hookTableHit(hi, src, tgt), msg);
            };
            auto srcOnly = [&](uint32_t src) {
                std::snprintf(msg, sizeof(msg), "src %06x hits", src);
                t.IsTrue(hookTableHit(hi, src, dead), msg);
            };
            auto tgtOnly = [&](uint32_t tgt) {
                std::snprintf(msg, sizeof(msg), "tgt %06x hits", tgt);
                t.IsTrue(hookTableHit(hi, dead, tgt), msg);
            };
            // Events facts / entry-exit (eventsOnBranch).
            srcOnly(kAppUpdateSite);
            srcOnly(kLsGateSite); // online-stock-2 lockstep gate
            srcOnly(kCkSite);     // online-stock-2 checksum verdict
            tgtOnly(kNetPause);   // online-stock-2 NetGame pause skip
            tgtOnly(kTimedSend);  // online-stock-2 quik search window
            pair(kRaceTickCallSite, kRaceTickCallee);
            pair(kRaceTick2CallSite, kRaceTick2Callee);
            tgtOnly(kSelectorDispatch);
            tgtOnly(kSession);
            pair(kTrickPassSite, kTrickPass);
            pair(ps2_fh1_rpl::kRecordSite, ps2_fh1_rpl::kRecord);
            pair(ps2_fh1_rpl::kPlaySite, ps2_fh1_rpl::kPlay);
            tgtOnly(ps2_fh1_rpl::kInputReset);
            tgtOnly(ps2_fh1_rpl::kInputFree);
            // Life2 streaming hooks.
            for (uint32_t pc : kLife2Targets)
                tgtOnly(pc);
            for (uint32_t pc : kLife2Sources)
                srcOnly(pc);
            // Clock/session/launch/rclock.
            tgtOnly(kProducer);
            tgtOnly(kPeriod10s);
            pair(kSessionCallSite, kSession);
            tgtOnly(kLaunchHelper);
            tgtOnly(kRenderFrameGet);
            // RNG cadence + trail consumers of the same push target.
            tgtOnly(kTrailPush);
            tgtOnly(kTrailRingAlloc);
            tgtOnly(kLookAt);
            pair(kLensRollSite, kRngDraw0);
            pair(kFlashRollSite, kRngDraw0);
            tgtOnly(kFlashSched);
            // Ground query pair (query, asymk, shadowpub mark/sample).
            pair(kCruiseQuerySite, kCruiseQuery);
            pair(kShadowPubBarSite, kShadowPubBar);
            pair(kShadowPubShapeSite, kShadowPubShape);
            tgtOnly(kNormcapGround);
            // Per-update skip/accrue gates.
            pair(kComboSite, kComboAccrue);
            for (uint32_t site : {0x10bdccu, 0x10da6cu, 0x10dc30u, 0x10b8acu})
                pair(site, kRaceTickGet);
            pair(kParticleSiteAll, kParticlePass);
            pair(kParticleSiteOne, kParticlePass);
            pair(kFlareProbeSite, kFlareProbeLoop);
            pair(kLiftProbeSite, kLiftProbe);
            pair(kBonusSite, kBonusNext);
            pair(kEnvFiltSite, kEnvFiltUpdate);
            // Target-only pre-hooks (any source).
            tgtOnly(0x113e80u); // ground2
            tgtOnly(kStuntChgUpdate);
            tgtOnly(kJcamJump);
            tgtOnly(kJcam2Launch);
            pair(kJcam2Site, kJcam2Solver);
            pair(kJcam2QuerySite, kJcam2Query);
            tgtOnly(kPidUpdate);
            tgtOnly(kC2Update);
            tgtOnly(kFlagUpdate);
            srcOnly(0x1626e4u); // chase2
            // FH12 post-call family.
            tgtOnly(kSpinUpdate);
            tgtOnly(kTexUv);
            tgtOnly(kTexFlip);
            tgtOnly(kLoopMode1);
            tgtOnly(kLoopMode2);
            tgtOnly(kLoopMode3);
            tgtOnly(kLoopCtor);
            tgtOnly(kFxTimerUpdate);
            // Draw limiter + taps.
            pair(kRenderGateMode0, kRenderTarget);
            pair(kRenderGateMode1, kRenderTarget);
            tgtOnly(kUpdateTarget);
            tgtOnly(kRenderTarget);
            tgtOnly(0x133308u); // FH26 stick tap (fh26s set)
            tgtOnly(0x111728u); // IN4 rider dispatcher
            // Always mode: the manager-init patch site is a source.
            HookConfig alwaysCfg = c;
            alwaysCfg.mode = Mode::Always;
            t.IsTrue(hookTableHit(buildHookInterest(alwaysCfg), kHookSite, dead),
                     "always-mode kHookSite src hits");
            // Inactive events: entry/exit machinery stays, on-gated PCs go.
            HookConfig idle = c;
            idle.guestActive = false;
            const HookInterest hiIdle = buildHookInterest(idle);
            t.IsTrue(hookTableHit(hiIdle, kAppUpdateSite, dead), "idle: app-update src stays");
            t.IsTrue(hookTableHit(hiIdle, dead, kSelectorDispatch), "idle: selector stays");
            t.IsTrue(hookTableHit(hiIdle, dead, kTrickPass), "idle: bonusflip recorder stays");
            t.IsTrue(hookTableHit(hiIdle, ps2_fh1_rpl::kRecordSite, dead), "idle: replay src stays");
            t.IsTrue(hookTableHit(hiIdle, dead, kTrailRingAlloc), "idle: trailring alloc stays");
            t.IsTrue(hookTableHit(hiIdle, dead, kRenderFrameGet), "idle: rclock stays");
            t.IsFalse(hookTableHit(hiIdle, dead, kProducer), "idle: clock tgt out");
            t.IsFalse(hookTableHit(hiIdle, dead, kCruiseQuery), "idle: query tgt out");
            t.IsFalse(hookTableHit(hiIdle, dead, kStuntChgUpdate), "idle: stuntchg tgt out");
        });

        // Full120 slice 3: relocated from ps2xTest/src/ps2_fh1_replay_rate_tests.cpp
        // with the table content (the replay sites are module-owned rows now).
        tc.Run("replay sites ride the table in both phases", [](TestCase &t)
        {
            HookConfig c;
            c.mode = Mode::Events;
            for (bool active : {false, true})
            {
                c.guestActive = active;
                c.replay = true;
                auto h = buildHookInterest(c);
                t.IsTrue(hookTableHit(h, ps2_fh1_rpl::kRecordSite, 1u), "record site");
                t.IsTrue(hookTableHit(h, ps2_fh1_rpl::kPlaySite, 1u), "play site");
                t.IsTrue(hookTableHit(h, 1u, ps2_fh1_rpl::kInputReset), "reset");
                t.IsTrue(hookTableHit(h, 1u, ps2_fh1_rpl::kInputFree), "destroy");
                c.replay = false;
                h = buildHookInterest(c);
                t.IsFalse(hookTableHit(h, ps2_fh1_rpl::kRecordSite, 1u), "off: no record site");
                t.IsFalse(hookTableHit(h, ps2_fh1_rpl::kPlaySite, 1u), "off: no play site");
            }
        });

        tc.Run("knob defaults to table when unset", [](TestCase &t)
        {
            if (std::getenv("PS2X_SSX3_FULL120_HOOKS") == nullptr)
                t.IsTrue(hookPreclassify(), "default is table");
        });
    });
}
