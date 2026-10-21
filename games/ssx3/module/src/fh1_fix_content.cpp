// full120 slice 1: FIX-policy content moved from ps2_fh1_fix.h (one family,
// one commit). Bodies are verbatim; only the lookup signatures changed from
// std::string to const char * (strcmp comparison), matching the module ABI's
// plain types. The bit enum stays in ps2_fh1_fix.h with the consumers (they
// move in later slices); this TU reads the bits from that header.
#include "Ssx3Fh1FixContent.h"
#include "runtime/game_module.h"
#include "ps2_fh1_fix.h"

#include <cstdint>
#include <cstring>

namespace ps2_ssx3_fh1
{
// Bit enumerators (kFix*) stay in ps2_fh1_fix.h with the consumers; only the
// name/bit tables moved here. The "all" composition below is read through
// allMain()/all12(), never snapshotted: see ps2_fh1_fix.h.
using namespace ps2_fh1;
namespace
{
bool streq(const char *a, const char *b) noexcept
{
    return std::strcmp(a, b) == 0;
}

uint64_t fixItemMain(const char *item) noexcept
{
    if (streq(item, "life2")) return kFixLife2;
    if (streq(item, "rider")) return kFixRider;
    if (streq(item, "countdown")) return kFixCountdown;
    if (streq(item, "drag")) return kFixDrag;
    if (streq(item, "event")) return kFixEvent;
    if (streq(item, "slew")) return kFixSlew;
    if (streq(item, "clock")) return kFixClock;
    if (streq(item, "raceclock")) return kFixRaceClock;
    if (streq(item, "session")) return kFixSession;
    if (streq(item, "timers")) return kFixTimers;
    if (streq(item, "camera")) return kFixCamera;
    if (streq(item, "launch")) return kFixLaunch;
    if (streq(item, "stick")) return kFixStick;
    if (streq(item, "speedcap")) return kFixSpeedcap;
    if (streq(item, "rng")) return kFixRng;
    if (streq(item, "trick")) return kFixTrick;
    if (streq(item, "anim")) return kFixAnim;
    if (streq(item, "bonus")) return kFixBonus;
    if (streq(item, "lift")) return kFixLift;
    if (streq(item, "aigate")) return kFixAiGate;
    if (streq(item, "takeoff")) return kFixTakeoff;
    if (streq(item, "flags")) return kFixFlags;
    if (streq(item, "steer")) return kFixSteer;
    if (streq(item, "rail")) return kFixRail;
    if (streq(item, "reset")) return kFixReset;
    if (streq(item, "meter")) return kFixMeter;
    if (streq(item, "boost")) return kFixBoost;
    if (streq(item, "ground")) return kFixGround;
    if (streq(item, "inputchain")) return kFixInputChain;
    if (streq(item, "input2")) return kFixInput2;
    if (streq(item, "ground2")) return kFixGround2;
    if (streq(item, "entry")) return kFixEntry;
    if (streq(item, "rclock")) return kFixRclock;
    if (streq(item, "emitter")) return kFixEmitter;
    if (streq(item, "fx")) return kFixFx;
    if (streq(item, "clock2")) return kFixClock2;
    if (streq(item, "hudfill")) return kFixHudfill;
    if (streq(item, "stick2")) return kFixStick2;
    if (streq(item, "clocksign")) return kFixClockSign;
    if (streq(item, "bonusflip")) return kFixBonusFlip;
    if (streq(item, "jcam2")) return kFixJcam2;
    if (streq(item, "jcam")) return kFixJcam;
    if (streq(item, "pose")) return kFixPose;
    if (streq(item, "pid")) return kFixPid;
    if (streq(item, "c2cap")) return kFixC2Cap;
    if (streq(item, "spawn")) return kFixSpawn;
    if (streq(item, "envfilt")) return kFixEnvFilt;
    if (streq(item, "trails")) return kFixTrails;
    if (streq(item, "chase2")) return kFixChase2;
    if (streq(item, "query")) return kFixQuery;
    if (streq(item, "vnret")) return kFixVnRet;
    if (streq(item, "groundstep")) return kFixGroundStep;
    if (streq(item, "trailring")) return kFixTrailRing;
    if (streq(item, "normcap")) return kFixNormcap;
    if (streq(item, "shadowpub")) return kFixShadowPub;
    if (streq(item, "sel5")) return kFixSel5;
    if (streq(item, "sel5b")) return kFixSel5b;
    if (streq(item, "boostfill")) return kFixBoostFill;
    if (streq(item, "stuntchg")) return kFixStuntChg;
    if (streq(item, "asymk")) return kFixAsymK;
    return 0u;
}

uint32_t fixItem12(const char *item) noexcept
{
    if (streq(item, "spin")) return kFix12Spin;
    if (streq(item, "texanim")) return kFix12Texanim;
    if (streq(item, "loops")) return kFix12Loops;
    if (streq(item, "recover")) return kFix12Recover;
    if (streq(item, "pulse")) return kFix12Pulse;
    if (streq(item, "crash")) return kFix12Crash;
    if (streq(item, "fxtimer")) return kFix12FxTimer;
    if (streq(item, "bounce")) return kFix12Bounce;
    if (streq(item, "particles")) return kFix12Particles;
    if (streq(item, "crashbody")) return kFix12CrashBody;
    if (streq(item, "uber")) return kFix12Uber;
    if (streq(item, "popups")) return kFix12Popups;
    if (streq(item, "snddt")) return kFix12SndDt;
    if (streq(item, "hudprog")) return kFix12HudProg;
    if (streq(item, "loops2")) return kFix12Loops2;
    if (streq(item, "flare")) return kFix12Flare;
    return 0u;
}

const char *fixBitNameMain(unsigned bit) noexcept
{
    static constexpr const char *kNames[62] = {
        "rider", "countdown", "drag", "event", "slew", "clock", "raceclock", "session", // 0-7
        "timers", "camera", "launch", "stick", "speedcap", "rng", nullptr, "trick", // 8-15 (14 = ramp)
        "anim", "bonus", "lift", "aigate", "takeoff", "flags", "steer", "rail", // 16-23
        "reset", "meter", "boost", "ground", "entry", "rclock", "emitter", "fx", // 24-31
        "clock2", "hudfill", "stick2", "clocksign", "bonusflip", "jcam", "pose", "pid", // 32-39
        "c2cap", "spawn", "envfilt", "ground2", "jcam2", "life2", "input2", "inputchain", // 40-47
        "trails", "chase2", "query", nullptr, "vnret", "shadowpub", "groundstep", "trailring", // 48-55
        "normcap", "sel5", "sel5b", "boostfill", "stuntchg", "asymk", // 56-61
    };
    return bit < 62u ? kNames[bit] : nullptr;
}

const char *fixBitName12(unsigned bit) noexcept
{
    static constexpr const char *kNames[16] = {
        "spin", "texanim", "loops", "recover", "pulse", "crash", "fxtimer", "bounce", // 0-7
        "particles", "crashbody", "uber", "popups", "snddt", "hudprog", "loops2", "flare", // 8-15
    };
    return bit < 16u ? kNames[bit] : nullptr;
}

bool asymkMaskOk(uint64_t mask) noexcept
{
    return (mask & (kFixAsymK | kFixQuery | kFixVnRet)) ==
           (static_cast<uint64_t>(kFixAsymK) | kFixQuery | kFixVnRet);
}

bool normcapMaskRequested(uint64_t mask) noexcept
{
    return (mask & (kFixNormcap | kFixVnRet | kFixQuery)) ==
           (static_cast<uint64_t>(kFixNormcap) | kFixVnRet | kFixQuery);
}

bool normcapMaskOk(uint64_t mask) noexcept
{
    return normcapMaskRequested(mask) && !asymkMaskOk(mask);
}

bool normcapMaskSuppressed(uint64_t mask) noexcept
{
    return normcapMaskRequested(mask) && asymkMaskOk(mask);
}

// constexpr so kPolicy is constant-initialized: allMain()/all12() in
// ps2_fh1_fix.h read the policy on every call, and nothing snapshots it at
// namespace scope (the module table itself is not constant-initialized, so
// any snapshot would race it and stick at empty).
constexpr uint64_t fixAllMain() noexcept
{
    return kFixRider | kFixCountdown | kFixDrag | kFixEvent | kFixSlew | kFixClock | kFixRaceClock | kFixSession | kFixTimers |
        kFixCamera | kFixLaunch | kFixStick | kFixSpeedcap | kFixRng | kFixTrick | kFixAnim | kFixBonus | kFixAiGate |
        kFixTakeoff | kFixFlags | kFixSteer | kFixRail | kFixReset | kFixMeter | kFixBoost | kFixEntry |
        kFixRclock | kFixEmitter | kFixFx | kFixClock2 | kFixHudfill | kFixStick2 | kFixClockSign | kFixBonusFlip |
        kFixPose | kFixC2Cap | kFixSpawn | kFixEnvFilt | kFixInput2 | kFixChase2;
}

constexpr uint32_t fixAll12() noexcept
{
    return kFix12Spin | kFix12Texanim | kFix12Loops | kFix12Recover | kFix12Pulse |
        kFix12Crash | kFix12FxTimer | kFix12Bounce | kFix12Particles |
        kFix12CrashBody | kFix12Uber | kFix12Popups | kFix12SndDt | kFix12HudProg |
        kFix12Loops2;
}

constexpr ps2_game_module::Fh1FixPolicy kPolicy{fixAllMain(),
                                            fixAll12(),
                                            &fixItemMain,
                                            &fixItem12,
                                            &fixBitNameMain,
                                            &fixBitName12,
                                            &asymkMaskOk,
                                            &normcapMaskRequested,
                                            &normcapMaskOk,
                                            &normcapMaskSuppressed};
} // namespace

const ps2_game_module::Fh1FixPolicy &fixPolicy() noexcept
{
    return kPolicy;
}
} // namespace ps2_ssx3_fh1
