// full120 slice 3: dispatch cold side moved from ps2_fh1_full120.h: the
// hook-interest table content (which source/target PCs each FIX group
// lists). Body verbatim; the k* PC constants stay runtime-side until
// their last consumers move (later slices). The runtime reaches this body
// through Module.fh1BuildHookInterest (same-name wrapper in
// ps2_fh1_full120.h); config gathering, the table cache and the lean-front
// filter stay runtime-side.
#include "Ssx3Fh1Dispatch.h"

#include "ps2_fh1_full120.h"

#include <algorithm>
#include <cstring>
#include <utility>
#include <vector>

namespace ps2_ssx3_fh1
{
using namespace ps2_fh1;

void buildHookInterest(const ps2_fh1::HookConfig *cfg, ps2_fh1::HookInterest *out)
{
    const HookConfig &c = *cfg;
    HookInterest r;
    auto addSrc = [&](uint32_t pc) { r.src.push_back(pc); };
    auto addTgt = [&](uint32_t pc) { r.tgt.push_back(pc); };
    const bool on = c.mode == Mode::Always || (c.mode == Mode::Events && c.guestActive);
    if (c.mode == Mode::Always)
        addSrc(kHookSite); // manager-init patch site
    if (c.mode == Mode::Events)
    {
        // eventsOnBranch sites (entry/exit facts + the app-update flip test).
        addSrc(kRaceTickCallSite);
        addSrc(kRaceTick2CallSite);
        addSrc(kAppUpdateSite);
        addSrc(kLsGateSite); // online-stock-2: lockstep gate (no-op without a 120 link)
        addSrc(kCkSite);     // online-stock-2: checksum verdict (no-op unless PS2X_NET_STOCK120)
        addTgt(kNetPause);   // online-stock-2: NetGame pause/resume (skipped while racing)
        addTgt(kTimedSend);  // online-stock-2: quik search window (no-op unless PS2X_NET_STOCK120)
        addTgt(kSelectorDispatch);
        addTgt(kRaceTickCallee);
        addTgt(kRaceTick2Callee);
        addTgt(kSession);
        // FH27 bonusflip: the trick-state recorder in eventsOnBranch (0x1218ac -> 0x117c28) runs
        // in stock windows too, so it is not on-gated (flips rescale states seen while inactive).
        if ((c.main & kFixBonus) != 0u && (c.main & kFixBonusFlip) != 0u)
            addTgt(kTrickPass);
        if (c.replay)
        {
            // RPL1: recorder/player calls and input reset/destroy, in every phase.
            addSrc(ps2_fh1_rpl::kRecordSite);
            addSrc(ps2_fh1_rpl::kPlaySite);
            addTgt(ps2_fh1_rpl::kInputReset);
            addTgt(ps2_fh1_rpl::kInputFree);
        }
    }
    if (c.mode != Mode::Off && (c.main & kFixLife2) != 0u)
    {
        for (uint32_t pc : kLife2Targets) addTgt(pc);
        for (uint32_t pc : kLife2Sources) addSrc(pc);
    }
    if (c.mode != Mode::Off && (c.main & (kFixTrailRing | kFixRng)) == (kFixTrailRing | kFixRng))
        addTgt(kTrailRingAlloc); // allocation happens before the events flip
    if ((c.main & kFixClock) != 0u && on)
    {
        addTgt(kProducer);
        addTgt(kPeriod10s);
    }
    if ((c.main & kFixRaceClock) != 0u && on)
    {
        addSrc(kRaceTick2CallSite);
        addTgt(kRaceTick2Callee);
        addSrc(kRaceTickCallSite);
        addTgt(kRaceTickCallee);
    }
    if ((c.main & kFixLaunch) != 0u && on)
        addTgt(kLaunchHelper);
    if ((c.main & kFixSession) != 0u && on)
    {
        addSrc(kSessionCallSite);
        addTgt(kSession);
    }
    if ((c.main & kFixQuery) != 0u && on)
    {
        addSrc(kCruiseQuerySite);
        addTgt(kCruiseQuery);
    }
    if ((c.main & kFixShadowPub) != 0u && (c.main & kFixQuery) != 0u && on)
    {
        addSrc(kShadowPubBarSite);
        addTgt(kShadowPubBar);
        addSrc(kShadowPubShapeSite);
        addTgt(kShadowPubShape);
    }
    const bool parity = ((c.main & (kFixRng | kFixTrick | kFixAiGate | kFixJcam | kFixPid | kFixC2Cap | kFixInput2 | kFixInputChain | kFixQuery)) != 0u && on) ||
                        ((c.fix12 & (kFix12Particles | kFix12Flare)) != 0u && on);
    if (parity)
        addSrc(kAppUpdateSite); // parityHook counts app-update dispatches
    if ((c.main & kFixRng) != 0u && on)
    {
        // rngHook: odd-update skips of the trail/lookAt calls, the two stream-1
        // Bernoulli rolls, and the flash-countdown hold (any source each).
        addTgt(kTrailPush);
        addTgt(kLookAt);
        addTgt(kRngDraw0);
        addTgt(kFlashSched);
        addSrc(kLensRollSite);
        addSrc(kFlashRollSite);
    }
    if ((c.main & kFixTrick) != 0u && on)
    {
        addSrc(kComboSite);
        addTgt(kComboAccrue);
    }
    if ((c.main & kFixGround2) != 0u && on)
        addTgt(0x113e80u);
    if ((c.main & kFixStuntChg) != 0u && on)
        addTgt(kStuntChgUpdate); // FLP2: stunt-charge hold pre-hook (any source)
    if ((c.main & kFixNormcap) != 0u && on)
        addTgt(kNormcapGround); // NRM1: pre-hook arms the post-map correction record (any source)
    if ((c.main & kFixJcam) != 0u && on)
        addTgt(kJcamJump); // FH28: countdown pre-hook + retention post-hook (any source)
    if ((c.main & kFixJcam2) != 0u && on)
    {
        addTgt(kJcam2Solver); addTgt(kJcam2Launch); addTgt(kJcam2Query);
    }
    if ((c.main & kFixEnvFilt) != 0u && on)
        addTgt(kEnvFiltUpdate); // FH30: conversion only at kEnvFiltSite
    if ((c.main & kFixPid) != 0u && on)
        addTgt(kPidUpdate); // FH29: second-update hold + rollback (any source)
    if ((c.main & kFixC2Cap) != 0u && on)
        addTgt(kC2Update); // FH28: odd-update skip (any source)
    if ((c.main & kFixAiGate) != 0u && on)
    {
        // aiGateHook: odd-update answers of the race-tick read at 4 sites.
        addSrc(0x10bdccu);
        addSrc(0x10da6cu);
        addSrc(0x10dc30u);
        addSrc(0x10b8acu);
        addTgt(kRaceTickGet);
    }
    if ((c.fix12 & kFix12Particles) != 0u && on)
    {
        addSrc(kParticleSiteAll);
        addSrc(kParticleSiteOne);
        addTgt(kParticlePass);
    }
    if ((c.fix12 & kFix12Flare) != 0u && on)
    {
        addSrc(kFlareProbeSite);
        addTgt(kFlareProbeLoop);
    }
    if ((c.main & kFixBonus) != 0u && on)
    {
        addSrc(kBonusSite);
        addTgt(kBonusNext);
    }
    if ((c.main & kFixLift) != 0u && on)
    {
        addSrc(kLiftProbeSite);
        addTgt(kLiftProbe);
    }
    if ((c.main & kFixFlags) != 0u && on)
        addTgt(kFlagUpdate);
    if ((c.main & kFixRclock) != 0u)
        addTgt(kRenderFrameGet); // also answered after exit (frozen offset)
    if ((c.fix12 & (kFix12Spin | kFix12Texanim | kFix12Loops | kFix12FxTimer)) != 0u && on)
    {
        addTgt(kSpinUpdate);
        addTgt(kTexUv);
        addTgt(kTexFlip);
        addTgt(kLoopMode1);
        addTgt(kLoopMode2);
        addTgt(kLoopMode3);
        addTgt(kFxTimerUpdate);
        addTgt(kLoopCtor); // FH27 loops2: the ctor pre-hook (merge MRG2: HK1 predates FH27)
    }
    for (uint32_t t : c.srcTgts)
        addTgt(t); // PS2X_FH1_SRC watch list (diagnostic, runs whatever the mode)
    if (c.fh26s != 0u)
        addTgt(0x133308u); // stick-angle tracker tap
    if (c.fh9r != 0u)
        r.always = true; // FH9 AI tap replicates per-branch state: run the chain
    addTgt(0x111728u); // IN4 rider-action tap (knob-checked inside; one compare)
    if (c.draw && on)
    {
        addSrc(kRenderGateMode1);
        addSrc(kRenderGateMode0);
        addTgt(kRenderTarget);
    }
    if (on && (c.main & (kFixChase2|kFixCamera)) == (kFixChase2|kFixCamera))
        addSrc(0x1626e4u);
    if (c.tapOn)
    {
        addTgt(kUpdateTarget);
        addTgt(kRenderTarget);
        for (uint32_t t : c.tapCountPcs)
            addTgt(t);
        if (c.histRanges)
            r.always = true; // FH10 hist counts every target inside its ranges
    }
    auto dedup = [](std::vector<uint32_t> &v) {
        std::sort(v.begin(), v.end());
        v.erase(std::unique(v.begin(), v.end()), v.end());
    };
    dedup(r.src);
    dedup(r.tgt);
    *out = std::move(r);
}

// Full120 slice 4: the branch-chain body (onBranchT) moved verbatim from
// ps2_fh1_full120.h. The template stays a template (one TU, two call
// shapes); the runtime reaches it through Module.fh1OnBranch, with `fast`
// selecting the cached-flags (FASTHOOKS=1) or the original-call order.
// Inline guest-conversion state, flag readers, hook bodies and the k* PC
// constants stay runtime-side until their last consumers move (later
// slices); unqualified uses below resolve to ps2_fh1 via the using above.
template <bool Fast>
inline bool onBranchT(uint8_t *ram, R5900Context *ctx, uint32_t sourcePc, uint32_t targetPc, PS2Runtime *runtime = nullptr)
{
    // GT3: Fast reads the once-computed flags; !Fast makes the original calls
    // in the original order. Every flag is fixed after its first read.
    const BranchFlags *const bf = Fast ? &branchFlags() : nullptr;
    const auto flag = [bf](bool BranchFlags::*m, bool (*fn)()) { return Fast ? bf->*m : fn(); };
    if ((Fast ? bf->always : mode() == Mode::Always) && sourcePc == kHookSite && !g_patched)
    {
        g_patched = true;
        patchAtManagerInit(ram);
    }
    if (Fast ? bf->events : mode() == Mode::Events)
        eventsOnBranch(ram, ctx, sourcePc, targetPc);
    if (flag(&BranchFlags::life2, life2Fix))
        life2Hook(ram, ctx, sourcePc, targetPc);
    if (flag(&BranchFlags::trailring, trailRingFix))
        trailRingAllocPre(ram, ctx, targetPc);
    const bool on = Fast ? (bf->always || (bf->events && g_guestActive)) : hooksOn();
    if (on && flag(&BranchFlags::clock, clockFix))
        clockPreHook(ram, targetPc);
    if (on && flag(&BranchFlags::raceClock, raceClockFix))
        raceClockPreHook(ram, ctx, sourcePc, targetPc);
    if (on && flag(&BranchFlags::launch, launchFix))
        launchPreHook(ram, ctx, targetPc);
    bool skip = on && flag(&BranchFlags::session, sessionFix) && sessionSkip(sourcePc, targetPc);
    if (on && (Fast ? bf->parity : (rngFix() || trickFix() || aiGateFix() || particlesFix() || flareFix() || jcamFix() ||
                                    pidFix() || c2capFix() || input2Fix() || inputChainFix())))
        parityHook(ram, sourcePc);
    if (on && flag(&BranchFlags::rng, rngFix))
        skip = rngHook(ram, ctx, sourcePc, targetPc) || skip;
    if (Fast ? bf->events : mode() == Mode::Events)
        skip = stock120NetPauseSkip(ram, targetPc) || skip; // online-stock-2: local-only pause
    if (Fast ? bf->events : mode() == Mode::Events)
        stock120QuikWindowHook(ram, ctx, targetPc); // online-stock-2: 120 s quick-match search
    bool queryWasSkipped = false;
    if (on && flag(&BranchFlags::query, queryFix))
    {
        queryWasSkipped = querySkip(ram, ctx, sourcePc, targetPc);
        skip = queryWasSkipped || skip;
    }
    if (on && flag(&BranchFlags::query, queryFix))
        queryPreHook(ctx, sourcePc, targetPc);
    if (on && asymkFix() && sourcePc == kCruiseQuerySite && targetPc == kCruiseQuery)
    {
        // Scope to pressed contact (pre-query qdepth < 0, where the
        // penetration adjust fires): leaving/lip approaches (qdepth >= 0)
        // keep vnret, preserving lip contact and takeoff behavior.
        // REV1 (RV27 F3): trust the chain only under querySkip's surf sanity
        // ([R+0x438] <= 0xffff); a stale [P+0x18] otherwise leaves qdepth at
        // its safe 0.0f default (the vnret/status-quo branch, as on a read
        // failure before) instead of classifying on arbitrary RAM bits.
        uint32_t pa = getRegU32(ctx, 4), r = 0u, surf = 0u, qd = 0u;
        float qdepth = 0.0f;
        if (rd32(ram, pa + 0x18u, r) && rd32(ram, r + 0x438u, surf) && surf <= 0xffffu &&
            rd32(ram, r + 0x454u, qd))
            std::memcpy(&qdepth, &qd, 4);
        uint32_t k;
        if (qdepth < 0.0f)
            k = queryWasSkipped ? 0x00000000u : kAsymKStock;
        else
            k = (fixMask() & kFixVnRet) != 0u ? 0xbe66d021u : kAsymKStock; // status quo ante
        wr32(ram, kAsymKPoolK, k);
    }
    if (on && flag(&BranchFlags::trick, trickFix) && g_rngOdd && sourcePc == kComboSite && targetPc == kComboAccrue)
        skip = true;
    if (on && flag(&BranchFlags::aiGate, aiGateFix))
        skip = aiGateHook(ctx, sourcePc, targetPc) || skip;
    if (on && flag(&BranchFlags::particles, particlesFix) && g_rngOdd && targetPc == kParticlePass &&
        (sourcePc == kParticleSiteAll || sourcePc == kParticleSiteOne))
        skip = true;
    if (on && flag(&BranchFlags::flare, flareFix) && g_rngOdd && sourcePc == kFlareProbeSite &&
        targetPc == kFlareProbeLoop)
        skip = true;
    if (on && flag(&BranchFlags::ground2, ground2Fix))
        ground2PreHook(ctx, targetPc);
    if (on && flag(&BranchFlags::stuntchg, stuntchgFix))
        stuntchgPreHook(ram, ctx, targetPc);
    if (on && flag(&BranchFlags::normcap, normcapFix))
        normcapPreHook(ctx, targetPc);
    if (on && flag(&BranchFlags::jcam2, jcam2Fix))
        skip = jcam2Hook(ram,ctx,sourcePc,targetPc,runtime) || skip;
    if (on && flag(&BranchFlags::jcam, jcamFix))
        jcamPreHook(ram, ctx, targetPc);
    if (on && flag(&BranchFlags::envFilt, envFiltFix))
        envFiltPreHook(ctx, sourcePc, targetPc);
    if (on && flag(&BranchFlags::pid, pidFix))
        pidPreHook(ram, ctx, targetPc);
    if (on && flag(&BranchFlags::c2cap, c2capFix) && g_rngOdd && targetPc == kC2Update)
        skip = true;
    if (on && flag(&BranchFlags::bonus, bonusFix))
        bonusHook(ram, ctx, sourcePc, targetPc);
    if (on && flag(&BranchFlags::lift, liftFix))
        liftHook(ctx, sourcePc, targetPc);
    if (on && flag(&BranchFlags::flags, flagsFix))
        flagsPreHook(ram, ctx, targetPc);
    if (flag(&BranchFlags::rclock, rclockFix)) // also after exit: S keeps its frozen offset
        skip = rclockHook(ram, ctx, targetPc) || skip;
    if (on && flag(&BranchFlags::fh12, fh12Hooks))
        fh12PreHook(ram, ctx, targetPc);
    if (!Fast || bf->src)
    {
        srcTap(ram, ctx, sourcePc, targetPc);
        fh26TapCall(ram, ctx, targetPc);
    }
    if (!Fast || bf->fh9)
        fh9OnBranch(ram, ctx, sourcePc, targetPc, skip);
    // IN4 rider layer (PS2X_INPUT_DIAG=1): edges of the rider action-block
    // watch bits at each entry of the rider control dispatcher 0x111728
    // (a1 = block filled by 0x121068; low 20 bits of +0 = action-map bits;
    // the IN3 in3-in tap's site, branch in3 4774566). One compare per branch
    // when off; the note itself early-outs on the cached knob.
    if (targetPc == 0x111728u && ctx)
        in4NoteRider(ram, ctx);
    const bool drawSkip = on && flag(&BranchFlags::draw, drawLimit) && !skip && drawHook(ctx, sourcePc, targetPc);
    skip = skip || drawSkip;
    if (on && !skip && flag(&BranchFlags::trails,trailsFix)) trailsPreHook(ctx,sourcePc,targetPc);
    if (on && !skip && flag(&BranchFlags::trailring,trailRingFix)) trailRingPushPre(ram,ctx,targetPc);
    if (on && !skip && flag(&BranchFlags::chase2,chase2Fix)) chase2PreHook(ctx,sourcePc,targetPc);
    if (on && !skip && flag(&BranchFlags::shadowpub, shadowPubFix))
        shadowPubPreHook(ram, ctx, sourcePc, targetPc);
    if (Fast && !bf->tap)
        return skip;
    Tap &t = tap();
    if (!t.on)
        return skip;
    fh10HistBranch(targetPc);
    if (targetPc == kUpdateTarget)
        ++t.updates;
    else if (targetPc == kRenderTarget && !drawSkip)
        ++t.renders;
    for (size_t i = 0; i < t.nCount; ++i)
        if (t.countPcs[i] == targetPc)
        {
            ++t.counts[i];
            if (t.logArgs && ctx)
                for (int r = 0; r < 4; ++r)
                    t.args[i][r] = getRegU32(ctx, 4 + r);
        }
    return skip;
}

bool onBranch(uint8_t *ram, R5900Context *ctx, uint32_t sourcePc, uint32_t targetPc, PS2Runtime *runtime,
              bool fast)
{
    return fast ? onBranchT<true>(ram, ctx, sourcePc, targetPc, runtime)
                : onBranchT<false>(ram, ctx, sourcePc, targetPc, runtime);
}
} // namespace ps2_ssx3_fh1

namespace ps2_fh1
{
// Full120 slice 5: post-call state definitions (moved from
// ps2_fh1_full120.h / ps2_fh1_pub.h). The chain above arms them; the
// consume-side bodies below read them. Runtime users (pre-hook bodies,
// savestate, dispatch guards, tests) see them through extern
// declarations; the no-module binary gets its copies from the none glue.
PostCall g_post;
bool g_postArmed = false;
PubRecord g_pub;
bool g_pubArmed = false;
NormcapPost g_normcap;
bool g_normcapArmed = false;
NormcapSlot g_normcapSlots[kNormcapSlots];
NormcapOwner g_normcapOwners[kNormcapSlots];
} // namespace ps2_fh1

namespace ps2_ssx3_fh1
{
using namespace ps2_fh1;

void postUnwindRestore(uint8_t *ram) noexcept
{
    trailRingRngRestore(ram);
    pubRestore(ram); // JDR2: an unwound published call keeps no published word
    normcapUnwind();
    if (!g_postArmed)
        return;
    if (g_post.kind == 6u)
        pidHoldRelease(ram, g_post.obj, g_post.saved, true);
    else if (g_post.kind == 5u)
        wr32(ram, g_post.obj + 0x2c4u, g_post.saved[0]);
    g_post = PostCall{};
    g_postArmed = false;
}

void onReturn(uint8_t *ram, R5900Context *ctx, uint32_t targetPc, bool returned)
{
    trailRingOnReturn(ram, ctx, targetPc);
    if (g_pubArmed)
        pubOnReturn(ram, ctx, targetPc);
    normcapReturnMatch(ram, ctx, targetPc, returned);
    if (!g_postArmed)
        return;
    if (targetPc != g_post.target || !ctx || getRegU32(ctx, 29) != g_post.sp)
        return;
    g_postArmed = false;
    if (g_post.kind == 9u)
    {
        if (returned) chase2OnReturn(ram,ctx);
        return;
    }
    if (g_post.kind == 6u)
    {
        pidHoldRelease(ram, g_post.obj, g_post.saved, returned); // gains come back even when suspended
        return;
    }
    if (!returned)
        return;
    if (g_post.kind == 7u)
    {
        ground2OnReturn(ram);
        return;
    }
    if (g_post.kind == 8u)
    {
        queryOnReturn(ram, ctx);
        return;
    }
    if (g_post.kind == 11u)
    {
        stuntchgOnReturn(ram);
        return;
    }
    if (g_post.kind == 5u)
    {
        jcamOnReturn(ram);
        return;
    }
    if (g_post.kind != 0u)
    {
        fh12OnReturn(ram);
        return;
    }
    for (int i = 0; i < 6; ++i)
    {
        const uint32_t a = g_post.obj + kFlagPhaseOffs[i];
        uint32_t bits = 0u;
        if (!rd32(ram, a, bits) || bits == g_post.saved[i])
            continue;
        float o = 0.0f, n = 0.0f;
        std::memcpy(&o, &g_post.saved[i], 4);
        std::memcpy(&n, &bits, 4);
        float d = n - o;
        if (d < 0.0f)
            d += 1.0f;
        if (!(d >= 0.0f && d < 1.0f))
            continue;
        float v = o + d * 0.5f;
        if (v >= 1.0f)
            v -= 1.0f;
        std::memcpy(&bits, &v, 4);
        wr32(ram, a, bits);
    }
}
} // namespace ps2_ssx3_fh1
