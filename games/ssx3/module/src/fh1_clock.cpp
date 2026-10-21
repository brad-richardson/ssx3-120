// full120 slice 2: clock policy moved from ps2_fh1_full120.h (and the
// `ssx3-full120-clock` override from ps2_runtime.cpp): the VBlank/EE rate
// switch, the stock-time accumulators, the VBlank commit, the manager word
// table and its helpers. Bodies are verbatim; they operate on the same
// inline guest-conversion state (declared in ps2_fh1_full120.h, which moves
// with the last consumers in later slices). The runtime reaches every body
// through Module.fh1* fields (same-name wrappers in ps2_fh1_full120.h).
#include "Ssx3Fh1Clock.h"

#include "game_overrides.h"
#include "ps2_fh1_full120.h"
#include "runtime/ee_guest_unwind.h"

#include <array>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace ps2_ssx3_fh1
{
// Inline state and bit enumerators stay runtime-side for now (later slices);
// unqualified uses below resolve to this TU's moved bodies first.
using namespace ps2_fh1;

uint32_t vblankDivisor() noexcept
{
    if (full120Refused())
        return 1u;
    if (mode() == Mode::Always)
        return 2u;
    if (mode() == Mode::Events)
        return g_schedActive ? 2u : 1u;
    return 1u;
}

// Scheduler: the divisor for the VBlank interval being scheduled now.
uint32_t schedDivisor() noexcept
{
    const uint32_t d = vblankDivisor();
    g_divEnded = g_divNext;
    g_divNext = d;
    return d;
}

uint32_t eeClockShiftNow() noexcept
{
    if (full120Refused())
        return 0u;
    if (mode() == Mode::Events)
        return g_commitActive ? eeClockShift() : 0u;
    return eeClockShift();
}

uint64_t stockHalfTicksLegacy() noexcept
{
    return g_stockHalf.load(std::memory_order_relaxed);
}
uint64_t stockHalfTicks() noexcept
{
    return exactStockClock() ? g_stockHalfExact.load(std::memory_order_relaxed) : stockHalfTicksLegacy();
}
// Half-periods the accumulator adds at the next VBlankStart.
uint32_t stockHalfStepNext() noexcept
{
    return (exactStockClock() ? g_divNext : g_divThis) == 2u ? 1u : 2u;
}

// Word table shared by the flip/step writers and the fh28 override below.
// (No anonymous namespace: inputChainPoolFlip/Step are Module fields, so
// their definitions must match Ssx3Fh1Clock.h in ps2_ssx3_fh1.)
struct Word
{
    uint64_t fix; // 0 = always (manager)
    uint32_t address;
    uint32_t expected;
    uint32_t replacement;
    const char *label;
};

// Inputchain owns the same seven words normally converted by stick/stick2.
// Keep one definition for the shared table, cadence writer and flip writer.
constexpr std::array<Word, 7> kInputChainWords = {{
    {kFixStick, 0x49bbe8u, kSixtieth, kHundredTwentieth, "stick_steady_133878"},
    {kFixStick, 0x49bbf8u, kSixtieth, kHundredTwentieth, "stick_hold0_133974"},
    {kFixStick, 0x49bbfcu, kSixtieth, kHundredTwentieth, "stick_hold1_1339a0"},
    {kFixStick, 0x49bc98u, kSixtieth, kHundredTwentieth, "stick_slew0_134138"},
    {kFixStick, 0x49bca4u, kSixtieth, kHundredTwentieth, "stick_slew1_1342b4"},
    {kFixStick2, 0x49bc94u, 0x3d4cc9c7u, 0x3cccc9c7u, "stick2_offset0_1340a8"},
    {kFixStick2, 0x49bca0u, 0x3d4cd331u, 0x3cccd331u, "stick2_offset1_134224"},
}};

void inputChainPoolFlip(uint8_t *ram, bool toActive) noexcept
{
    for (const Word &w : kInputChainWords)
        wr32(ram, w.address, toActive ? w.replacement : w.expected);
}

void inputChainPoolStep(uint8_t *ram, bool odd) noexcept
{
    for (const Word &w : kInputChainWords)
        wr32(ram, w.address, odd ? 0u : w.expected);
}

bool fh28PoolOverride(uint32_t address, uint64_t mainMask) noexcept
{
    if ((mainMask & kFixSpawn) != 0u && address == 0x49f628u)
        return true; // spawn: the 0x2e1520 caller's dt (fx halves it)
    if ((mainMask & kFixC2Cap) != 0u && address == 0x49c5fcu)
        return true; // c2cap: the C2 ramp rate (camera halves it)
    if ((mainMask & kFixInputChain) != 0u)
        for (const Word &w : kInputChainWords)
            if (address == w.address)
                return true;
    return false;
}

// patchAtManagerInit's 3-arg call (defaults evaluated here, as before).
// Declared before its user; defined after the 6-arg body below.
bool applyWords(uint8_t *ram, uint32_t a, bool toActive);

void patchAtManagerInit(uint8_t *ram)
{
    uint32_t a = 0u;
    if (!rd32(ram, kMgrPtr, a) || !a || (a & 3u) || (a & 0x1FFFFFFFu) + 0x60u > PS2_RAM_SIZE)
    {
        std::fprintf(stderr, "fh1-full120-refused manager ptr=%08x\n", a);
        std::abort();
    }
    if (!applyWords(ram, a, true))
    {
        refuseFull120ForSession("pool word mismatch at manager init");
        return;
    }
    std::fprintf(stderr, "fh1-full120-armed manager=%08x rate=120 dt=%08x vblank_div=%u ee_x=%u fix=0x%llx\n",
                 a, kHundredTwentieth, vblankDivisor(), 1u << eeClockShift(),
                 static_cast<unsigned long long>(fixMask()));
}


bool applyWords(uint8_t *ram, uint32_t a, bool toActive)
{
    const FixMasks m = ps2_fh1::fixMasks();
    return applyWords(ram, a, toActive, m.main, m.fh12, ps2_fh1::kFull120PlayBuild);
}

bool applyWords(uint8_t *ram, uint32_t a, bool toActive, uint64_t mainMask, uint32_t fix12Mask,
                  bool playBuild)
{
    const std::array<Word, 127> words = {{
        {0u, a + 0x10u, 60u, 120u, "rate"},
        {0u, a + 0x14u, kSixtieth, kHundredTwentieth, "dt"},
        {0u, a + 0x24u, 0x3f800000u, 0x3f800000u, "mult(stock)"},
        {kFixRider, 0x49be9cu, kSixtieth, kHundredTwentieth, "K1"},
        {kFixRider, 0x49c08cu, kSixtieth, kHundredTwentieth, "K2"},
        {kFixRider, 0x49c12cu, kSixtieth, kHundredTwentieth, "K3"},
        {kFixRider, 0x49b828u, kSixtieth, kHundredTwentieth, "K4"},
        {kFixCountdown, 0x49b480u, kSixtieth, kHundredTwentieth, "C1"},
        {kFixCountdown, 0x49b48cu, kSixtieth, kHundredTwentieth, "C2"},
        {kFixCountdown, 0x49b4a0u, kSixtieth, kHundredTwentieth, "C3"},
        {kFixCountdown, 0x49bf1cu, kSixtieth, kHundredTwentieth, "C4"},
        {kFixCountdown, 0x49b494u, kSixtieth, kHundredTwentieth, "quantum"},
        {kFixCountdown, 0x49b498u, 0x3c23d70au, 0x3ba3d70au, "epsilon"},
        {kFixCountdown, 0x49b49cu, 0x426fffffu, 0x42efffffu, "residual_recip"},
        {kFixCountdown, 0x49bf20u, kSixtieth, kHundredTwentieth, "gap_floor"},
        {kFixCountdown, 0x49bf24u, 0x426fffffu, 0x42efffffu, "gap_recip"},
        {kFixDrag, 0x49b4a4u, 0xbb5a740fu, 0xbadaa2bdu, "damping"},
        {kFixDrag, 0x49b4a8u, 0xc1fd5556u, 0xc17d5556u, "z_up"},
        {kFixDrag, 0x49b4acu, 0xc162aaabu, 0xc0e2aaabu, "z_down"},
        {kFixEvent, 0x49b1b0u, kSixtieth, kHundredTwentieth, "event_step_10496c"},
        // FH8: 0x10496c is one of 14 animation-node step functions (dispatch
        // 0x103578 by node type). Each advances its track by f12*[own 1/60
        // word] through 0x3135b0 (time += rate*speed*step; anim events fire as
        // the time crosses them, 0x313868 -> track +0xb0 bits). The other 13
        // words (single readers) stayed 1/60, so rider animations played 2x per
        // second at 120: All-Peak first-cliff trick track (0x1048ec/0x1049ec)
        // +0.0217 per 120 Hz update, trick-bonus event (0x135460 -> 0x119708)
        // 10 stock frames early (fh8-d3/d4); lab fh8-d6: bonus at stock 4509.5
        // vs 4509, in-air 3230 -> 3080 (stock 3040).
        {kFixAnim, 0x49b180u, kSixtieth, kHundredTwentieth, "anim_step_103c80"},
        {kFixAnim, 0x49b184u, kSixtieth, kHundredTwentieth, "anim_step_103da4"},
        {kFixAnim, 0x49b188u, kSixtieth, kHundredTwentieth, "anim_step_103f74"},
        {kFixAnim, 0x49b18cu, kSixtieth, kHundredTwentieth, "anim_step_104048"},
        {kFixAnim, 0x49b190u, kSixtieth, kHundredTwentieth, "anim_step_1040b4"},
        {kFixAnim, 0x49b194u, kSixtieth, kHundredTwentieth, "anim_step_1040c8"},
        {kFixAnim, 0x49b198u, kSixtieth, kHundredTwentieth, "anim_step_1043b4"},
        {kFixAnim, 0x49b19cu, kSixtieth, kHundredTwentieth, "anim_step_104554"},
        {kFixAnim, 0x49b1a0u, kSixtieth, kHundredTwentieth, "anim_step_104618"},
        {kFixAnim, 0x49b1a4u, kSixtieth, kHundredTwentieth, "anim_step_1047ac"},
        {kFixAnim, 0x49b1a8u, kSixtieth, kHundredTwentieth, "anim_step_10487c"},
        {kFixAnim, 0x49b1acu, kSixtieth, kHundredTwentieth, "anim_step_1048c4"},
        {kFixAnim, 0x49b1b4u, kSixtieth, kHundredTwentieth, "anim_step_1049c4"},
        // 0x114124 applies this fraction of the remaining R+0x214 gap once per
        // update: alpha_half = 1-sqrt(1-alpha_stock).
        {kFixSlew, 0x49b4f0u, 0x3d2aa635u, 0x3cac76f5u, "mode0_slew_114124"},
        // FH2 live 1/60 clocks (PS2X_FH1_SCANF, stock t1900..2000; writers by DIAG_WATCH):
        // HUD object +0x190 += [gp-0x5758] at 0x1eb034/44 (0x1e9a30 race HUD),
        {kFixTimers, 0x49d998u, kSixtieth, kHundredTwentieth, "hud_clock_1eb02c"},
        // rider object +0x35c += [gp-0x7b54] up to 1.0 at 0x115d98-db4,
        // FH7: with FIX=rng, 0x115d48 (which holds this ramp) runs at stock
        // cadence, so the ramp word stays 1/60 (kFixRamp = timers && !rng).
        {kFixRamp, 0x49b59cu, kSixtieth, kHundredTwentieth, "rider_ramp_115d98"},
        // per-rider timer list [e+4] -= [gp-0x7f94] at 0x101538 (0x1013a8, RV13 row).
        {kFixTimers, 0x49b15cu, kSixtieth, kHundredTwentieth, "rider_timers_101538"},
        // FH4: rider trick/air state 0x117c28 (from 0x1218ac): air timer +0x30
        // += tscale*[gp-0x7b00] at 0x117cf8/0x117d14, fed to 0x119210 (trick
        // curve; points accrue into +0x84 at 0x117d18); +0xa4 -= the same.
        {kFixTimers, 0x49b5f0u, kSixtieth, kHundredTwentieth, "air_trick_clock_117c28"},
        // FH5: stick-angle tracker 0x133308 (P+0x3f0, from 0x111728), each word a
        // single reader: +0x54 += [gp-0x7508] (0x133878, steady-angle timer),
        // hold timers +0x48/+0x4c += [gp-0x74f8]/[gp-0x74f4] (0x133974/0x1339a0),
        // angle slews +0x14->+0x1c and +0x10->+0x18 at owner rate*[gp-0x7458]/
        // [gp-0x744c] (0x134138/0x1342b4). Stock 1/60 per update ran 2x in the
        // air (fh5-k1/k2 SCANL); the lab (fh5-k4) moved the first-cliff score
        // 3930 -> 3530 (stock 3200).
        kInputChainWords[0], kInputChainWords[1], kInputChainWords[2],
        kInputChainWords[3], kInputChainWords[4],
        // FH26 stick2 (FCR1 1; in "all" since Brad 10-02): the bounded steps before those slews move the offsets S+0x28/S+0x2c
        // toward their targets by [0x49bc94]/[0x49bca0] (~0.05, single GP readers 0x1340a8/0x134224) times a
        // gap factor <= 1 and (0.5 + 0.5|in|), clamped at the target, with no dt: 2x per stock tick at 120.
        // Halved (exponent - 1, exact): one stock step per two updates while the clamp doesn't bind.
        kInputChainWords[5], kInputChainWords[6],
        // FH7 (FA1 3.2): 0x11b3f8 per rider per update R+0x2e4 = d*cap +
        // (1-d)*target with no dt: at 120 the post-air/post-boost cap relaxes
        // 2x as fast in real time. Retentions d -> sqrt(d), each word a single
        // reader (0x11b60c easing down 0.97, 0x11b5cc easing up 0.9). FA1 lab
        // f3: FR1-R1 rival +7.4 % -> +2.3 %, stock line again.
        {kFixSpeedcap, 0x49b730u, 0x3f7851ecu, 0x3f7c217au, "speedcap_down_11b60c"},
        {kFixSpeedcap, 0x49b728u, 0x3f666666u, 0x3f72dce8u, "speedcap_up_11b5cc"},
        // FH7 trick: style accumulator +0x1c of the rider trick state
        // (0x117fe0 at 0x1188b8-cc) += tweak * [gp-0x7a74] once per update;
        // the HUD style bonus is round10(+0x1c * 10000 + 5) (0x117908, shown
        // via 0x1171a8 from 0x118ebc). 0.05/60 per update -> 0.05/120 (class
        // b, single reader, not a timestep value so the census missed it).
        // Lab fh7-n6: All-Peak first-cliff style bonus +300 -> +150 (stock +160).
        {kFixTrick, 0x49b67cu, 0x3a5a740eu, 0x39da740eu, "style_rate_1188b8"},
        // CM2 §4 / convert.txt (GameCamera 0x1580e10 chase chain, pool words
        // reloaded every tick): class a retentions r -> sqrt(r) ...
        {kFixCamera, 0x49cfe4u, 0x3f59999au, 0x3f6c0535u, "cam_C1_kA0"},
        {kFixCamera, 0x49cfe8u, 0x3f7851ecu, 0x3f7c217au, "cam_C1_kA1"},
        {kFixCamera, 0x49cfd4u, 0x3e8aefe0u, 0x3f055b3fu, "cam_C1_kB0"},
        {kFixCamera, 0x49cfd8u, 0x3f666666u, 0x3f72dce8u, "cam_C1_kB1"},
        {kFixCamera, 0x49d044u, 0x3f6c97c5u, 0x3f761aeeu, "cam_height"},
        {kFixCamera, 0x49cff4u, 0x3f6c89d3u, 0x3f7613aeu, "cam_dist_grow"},
        {kFixCamera, 0x49cff8u, 0x3f684c59u, 0x3f73dc80u, "cam_dist_shrink"},
        {kFixCamera, 0x49d010u, 0x3f7a77e2u, 0x3f7d3813u, "cam_height2"},
        {kFixCamera, 0x49d028u, 0x3f733333u, 0x3f798497u, "cam_fov0"},
        {kFixCamera, 0x49c644u, 0x3f7fbe77u, 0x3f7fdf39u, "cam_fov"},
        {kFixCamera, 0x49d02cu, 0x3f75c28fu, 0x3f7ad3e7u, "cam_lookat_kick"},
        {kFixCamera, 0x49cff0u, 0x3f7ae148u, 0x3f7d6d55u, "cam_side_kick5"},
        {kFixCamera, 0x49d070u, 0x3f787fadu, 0x3f7c38b3u, "cam_lateral"},
        {kFixCamera, 0x49c628u, 0x3f749d64u, 0x3f7a3e1fu, "cam_heading_base"},
        {kFixCamera, 0x49c624u, 0x3d21a510u, 0x3ca3b0d2u, "cam_heading_span"}, // sqrt(base+span)-sqrt(base)
        {kFixCamera, 0x49c49cu, 0x3f7851ecu, 0x3f7c217au, "cam_lift_decay"},
        {kFixCamera, 0x49c7d8u, 0x3f4ccccdu, 0x3f64f92eu, "cam_air_r"},
        {kFixCamera, 0x49c7dcu, 0x3e4ccccdu, 0x3dd8368fu, "cam_air_1mr"},
        // ... class b per-tick decrements halved ...
        {kFixCamera, 0x49d00cu, 0x3fe227ceu, 0x3f6227ceu, "cam_dist_impulse_decay"},
        {kFixCamera, 0x49c5fcu, 0x3abb3ee7u, 0x3a3b3ee7u, "cam_C2_ramp"},
        {kFixCamera, 0x49c5dcu, 0x3bda740fu, 0x3b5a740fu, "cam_ctl_blend2"},
        {kFixCamera, 0x49c5e0u, 0x3c899268u, 0x3c099268u, "cam_ctl_blend3"},
        // ... class e/h 1/60 clocks -> 1/120 (cap 4-1/60 -> 4-1/120).
        {kFixCamera, 0x49c64cu, kSixtieth, kHundredTwentieth, "cam_lookat_timer"},
        {kFixCamera, 0x49c654u, kSixtieth, kHundredTwentieth, "cam_jump_init"},
        {kFixCamera, 0x49c7a0u, kSixtieth, kHundredTwentieth, "cam_jump_clk_a"},
        {kFixCamera, 0x49c7a4u, kSixtieth, kHundredTwentieth, "cam_jump_clk_b"},
        {kFixCamera, 0x49c79cu, 0x407eeeefu, 0x407f7777u, "cam_jump_cap"},
        {kFixCamera, 0x49c7d0u, kSixtieth, kHundredTwentieth, "cam_air_clk"},
        {kFixCamera, 0x49c808u, kSixtieth, kHundredTwentieth, "cam_air_clk2"},
        {kFixCamera, 0x49c810u, kSixtieth, kHundredTwentieth, "cam_air_clk3"},
        {kFixCamera, 0x49c814u, kSixtieth, kHundredTwentieth, "cam_air_clk4"},
        {kFixCamera, 0x49c7c8u, kSixtieth, kHundredTwentieth, "cam_ramp_a"},
        {kFixCamera, 0x49c7ccu, kSixtieth, kHundredTwentieth, "cam_ramp_b"},
        {kFixCamera, 0x49c5d8u, kSixtieth, kHundredTwentieth, "cam_ctl_blend"},
        {kFixCamera, 0x49c5ccu, kSixtieth, kHundredTwentieth, "cam_mode_timer"},
        {kFixCamera, 0x49c824u, kSixtieth, kHundredTwentieth, "cam_shake_clk"},
        {kFixCamera, 0x49c820u, kSixtieth, kHundredTwentieth, "cam_shake_dur"},
        {kFixAiGate, 0x49b40cu, kSixtieth, kHundredTwentieth, "ai_react_hold"},
        // FH9 takeoff: 0x12f730 (rider control state 4, every rider) sets the slew rate of the
        // control triples +0x1f4/+0x200/+0x218/+0x224 to this word each update; the slewer
        // 0x1211f8 steps value toward target by <= rate per update (no dt), and state 4 exits
        // once +0x1f0/+0x214/+0x220 reach 0, so at 120 takeoff ended in half the time (AI Mac,
        // FR1-R1 race 7.77 s: 0.18 s vs stock 0.35 s). Single reader 0x12f9f4. With it the
        // rival finishes 03:16 vs stock 03:15 with 0 AI wipeouts (fh9-p1; before: 03:23, 2).
        {kFixTakeoff, 0x49ba0cu, 0x3d4cccceu, 0x3cccccceu, "takeoff_slew"},
        // FH10 steer: 0x113e80 (steering triple R+0x1f0, every rider in ground/air/pre-jump states) sets
        // target = stick and rate = clamp(12|gap|, 0.1, 14) * [0x49b4c4] per update, 0x113f38 (triple
        // +0x22c) the same with [0x49b4cc]: a per-second rate times a private 1/60 step (class h).
        // 0x113f88 (lean +0x214 / crouch +0x220 triples) uses per-update rates: fixed returns
        // [0x49b4d0]/[0x49b4d4] and the floor [0x49b4e0] halve (class b); the gap gain [0x49b4dc] 0.1
        // -> 1-sqrt(0.9) (class a, like TS1's 0x49b4f0). All single readers. Brad's full-right carve on
        // the All-Peak state (stock 4432-4461): events turned in ~1.5-2x faster (heading lead to 1.46 deg,
        // RMS 0.92); with these words the onset tracks stock within 0.05 deg, RMS 0.49 (fh10-l2).
        {kFixSteer, 0x49b4c4u, kSixtieth, kHundredTwentieth, "steer_dt_113e80"},
        {kFixSteer, 0x49b4ccu, kSixtieth, kHundredTwentieth, "steer_dt_113f38"},
        {kFixSteer, 0x49b4d0u, 0x3d2aa635u, 0x3caaa635u, "lean_return_113fa8"},
        {kFixSteer, 0x49b4d4u, 0x3dcccdc2u, 0x3d4ccdc2u, "crouch_return_113fd4"},
        {kFixSteer, 0x49b4dcu, 0x3dcccdc2u, 0x3d523279u, "crouch_gain_114024"},
        {kFixSteer, 0x49b4e0u, 0x3c23d7cfu, 0x3ba3d7cfu, "crouch_floor_114030"},
        // FH10 rail: the rail slide handler 0x13af28 (selector P+0xde0 = 4) steps with f23 = R+0x300 *
        // [0x49bf84] (single reader 0x13afc8), a private 1/60 dt: at 120 the rider slid along the rail
        // at 2.2x speed (3455 vs 1558 u/s, same velocity vector). With 1/120: 1779/2159 vs 1558/2354 u/s,
        // rail left at stock 6012 vs 6015 (common state s2-5700, fh10-r1/r2/r3).
        {kFixRail, 0x49bf84u, kSixtieth, kHundredTwentieth, "rail_dt_13afc8"},
        // FH10 reset: rider state 9 (0x12f398, the white-fade course reset) ramps +4 to 1 by R+0x300 *
        // [0x49b9d4] (1/40, single reader 0x12f39c) per update: 41 updates in either mode, so 20.5 stock
        // ticks at 120. Halved: 40.5 stock ticks vs stock 41 (fh10-r1/r2/r3).
        {kFixReset, 0x49b9d4u, 0x3cccccceu, 0x3c4cccceu, "reset_fade_12f39c"},
        // FH10 meter: 0x117fe0 posts the HUD boost meter (messages 5/6, 0x118950/0x1189b4); the shown
        // fill moves toward the boost R+0x2f8 by [0x49b680]/[0x49b684] (1/60, single readers) per update,
        // and the HUD (0x29ab40) clicks once per coil floor(10*fill). Stock fills 1/60 per stock tick;
        // events filled 2x (coils every 3 vs 6 stock ticks, fh10-m1/m2).
        {kFixMeter, 0x49b680u, kSixtieth, kHundredTwentieth, "meter_fill5_118950"},
        {kFixMeter, 0x49b684u, kSixtieth, kHundredTwentieth, "meter_fill6_1189b4"},
        // FH10 boost: the boost/uber machine 0x1200d0 (per rider, per update) steps its timers R+0x2e8,
        // R+0x2ec, the uber timer R+0x2f0 (20 s / 60 s, set by 0x10e098/0x10e9e8) and the boost drains
        // (0.005/s, 0.024/s in mode 2) by f20 = R+0x300 * [0x49b798] (1/60); [0x49b79c]/[0x49b7a0] are
        // one-step thresholds on +0x2ec/+0x2f0. All single readers. Uber lab (boost poked to 0.99 before
        // the first All-Peak landing): stock uber timer -1.00/s, drain -0.005/s; events -2.00/s (uber
        // over in 10 s), -0.010/s; with these words -1.00/s, -0.005/s (fh10-u1/u2/u3).
        {kFixBoost, 0x49b798u, kSixtieth, kHundredTwentieth, "boost_dt_1200d4"},
        {kFixBoost, 0x49b79cu, kSixtieth, kHundredTwentieth, "boost_step_120124"},
        {kFixBoost, 0x49b7a0u, kSixtieth, kHundredTwentieth, "uber_step_1201d8"},
        // FV1 ground: the ground handler 0x13d818 gets its normal force from 0x13c878(h = R+0x454, the
        // rider's height above the snow). While above it (h > 0) the pull back down is h * [0x49c008]
        // (-0.033333, single reader 0x13c88c) scaled by the rider table, applied once per update: at 120
        // the rider was pulled down twice per stock frame and stayed on the slope over crests (All-Peak
        // straight run, crest at 7.6 s: stock airborne at 4824, events at 4860 with |v| held at the
        // ground cap 2083, 0.14 s lost). Halved: airborne at 4823, lag <= 0.03 s and distance within
        // 0.6 % through 10.5 s (fv1-s2/s3, lab word fv1-w1).
        {kFixGround, 0x49c008u, 0xbd08882fu, 0xbc88882fu, "ground_pull_13c88c"},
        // FH13 emitter: the particle emitter step 0x370788 ages the emitter by 1/A.rate (per time) but
        // advances its frame phase +0x184 by +0x188 * f12 per update, f12 = a private 1/60 passed by
        // the three emitter classes (0x3459a8 at 0x345b78/0x345b8c; 0x345d80 at 0x345e3c; 0x345ef8 at
        // 0x345fd0/0x346000/0x34601c), each word a single reader (codegen grep). The frame index wraps at
        // the int +0x180, so emitter animations (spray/snow puffs) played 2x at 120 (class h).
        {kFixEmitter, 0x4a059cu, kSixtieth, kHundredTwentieth, "emitter_dt_345b78"},
        {kFixEmitter, 0x4a05a0u, kSixtieth, kHundredTwentieth, "emitter_dt_345b8c"},
        {kFixEmitter, 0x4a05a4u, kSixtieth, kHundredTwentieth, "emitter_dt_345e3c"},
        {kFixEmitter, 0x4a05a8u, kSixtieth, kHundredTwentieth, "emitter_dt_345fd0"},
        {kFixEmitter, 0x4a05acu, kSixtieth, kHundredTwentieth, "emitter_dt_346000"},
        {kFixEmitter, 0x4a05b0u, kSixtieth, kHundredTwentieth, "emitter_dt_34601c"},
        // FH13 fx: the rider effects controller (P+0xb40, 0x2e1120 from the rider pass, per rider per
        // update): +0x1c -= [0x49f60c] (1/600, floored), timer +0x20 += [0x49f610] (1/60) against a period
        // in seconds (+0x24, 0.4..1.0), and +0x14 += rate * [0x49f628] (1/60); its fader 0x2e1f70 moves
        // +0x10 (P+0xb50) up by [0x49f68c] 0.01 or down by [0x49f688] 1/60 per update. All single readers,
        // all measured 2x per stock second at 120 (FR1-R1 P+0xb50/b5c/b60, FH13 r1s/r1e dumps). The
        // controller's trail-push dt words (0x49f62c/30/9c/a0 -> 0x3717c0 -> 0x3710d0) stay 1/60: FH7 rng
        // already pushes trails at stock cadence. The board-wake scroll 0x2ef6d0 (per rider, from the rider
        // pass) moves its phase +0x10 by |v| * [0x49f7b4] (1/6000) per update (AI Mac 0x16d1ef0: 2x).
        {kFixFx, 0x49f60cu, 0x3ada740fu, 0x3a5a740fu, "fx_glint_decay_2e1230"},
        {kFixFx, 0x49f610u, kSixtieth, kHundredTwentieth, "fx_glint_timer_2e1240"},
        {kFixFx, 0x49f628u, kSixtieth, kHundredTwentieth, "fx_glint_dt_2e1384"},
        {kFixFx, 0x49f688u, kSixtieth, kHundredTwentieth, "fx_fader_down_2e1fc8"},
        {kFixFx, 0x49f68cu, 0x3c23d70bu, 0x3ba3d70bu, "fx_fader_up_2e1fe0"},
        {kFixFx, 0x49f7b4u, 0x392ec33eu, 0x38aec33eu, "wake_scroll_2ef8bc"},
        // FH20 hudfill: the race-HUD trick-combo readout (HUD object +0x70 of 0x16bf728, gated by +0x6c)
        // fills by [gp-0x570C] = [0x49d9e4] (1/48) per update at 0x1ebf70 (store 0x1ebf98), clamped at 1.0,
        // inside the race-HUD update sub_0x1e9a30 (writer 0x1ebf98, ra 0x1ebb6c). The pool word's only
        // reader in the whole codegen is 0x1ebf70 (class b, single reader -> halve). KD lab: stock
        // +0.0208333/stock-tick, events +0.0416666 (ratio 2.000); halved: 1.000x (fh20-E21).
        {kFixHudfill, 0x49d9e4u, 0x3caaaaabu, 0x3c2aaaabu, "hudfill_1ebf70"},
        // FH28 pose: the pose/lean control triples (current R+0x1fc, bound R+0x200, target R+0x204)
        // slew toward the target by at most the bound per update in 0x1211f8 (no dt multiply), so
        // at 120 the lean stepped twice per stock tick (FXT1 T2: -0.0667/wall-tick, clamp in half
        // the time). The eight bound words below (all 1/30, each a single lwc1 reader, no writers)
        // feed R+0x200 (and sibling triple bounds) from the rider-state setters; halving them is
        // exact (a power-of-two scale) and totals one stock bound per pair (class b).
        {kFixPose, 0x49bb04u, 0x3d088889u, kSixtieth, "pose_bound_1318a4"},
        {kFixPose, 0x49bb08u, 0x3d088889u, kSixtieth, "pose_bound_131908"},
        {kFixPose, 0x49bb0cu, 0x3d088889u, kSixtieth, "pose_bound_131940"},
        {kFixPose, 0x49bb10u, 0x3d088889u, kSixtieth, "pose_bound_131968"},
        {kFixPose, 0x49bb18u, 0x3d088889u, kSixtieth, "pose_bound_1319d8"},
        {kFixPose, 0x49bb30u, 0x3d088889u, kSixtieth, "pose_bound_131b78"},
        {kFixPose, 0x49b958u, 0x3d088889u, kSixtieth, "pose_bound_12e8e0"},
        {kFixPose, 0x49b95cu, 0x3d088889u, kSixtieth, "pose_bound_12e998"},
        // JMP6 vnret: the ground handler 0x13d818 runs two per-update retentions with no dt. After the
        // ground query (0x13ec18, rider state not 2/3/13) it adds n * (-0.4 * v.n) to the rider-relative
        // velocity (push z clamped >= -40, an immediate) and rescales to the old speed: 0.6 of the normal
        // component is kept per update, 0.36 per stock tick at 120, so bump/lip pops flatten (Gravitude
        // clean pair: v.n peak 38 vs 52 u/s, the 4223/4265 stock airs never leave the snow). Retention
        // 0.6 -> sqrt(0.6): word -(1 - sqrt(0.6)). The lean brake (0x13e028: velocity *= 0.9 per update
        // while |R+0x214| > 0.5, |v| < 500, n.z > 0.866) is the same class: 0.9 -> sqrt(0.9). Both words
        // are single GP readers (codegen grep). The -40 clamp is an immediate and stays per update.
        {kFixVnRet, 0x49c124u, 0xbecccccdu, 0xbe66d021u, "vn_retain_13ec18"},
        {kFixVnRet, 0x49c0a8u, 0x3f666666u, 0x3f72dce9u, "lean_brake_13e028"},
        // SEL1 sel5: the selector-5 handler 0x1391a8 advances its elapsed S+0x88 by R+0x300 * [0x49beec]
        // (1/60, single reader 0x1391ac, codegen grep) against duration S+0x84, and the same private dt
        // scales the phase-1 angular step (0x139278: S+0x80 * dt * 0.5 into sincos). At 120 the timer and
        // the angle run 2x per wall second (phase 1 0.167 s vs stock 0.351 s at matched S84); 1/120 restores
        // the stock wall rates. The S84 initializer 0x138ba0 (f12 = 0.5 * clamp, unconverted pool inputs)
        // and the phase-2 solver words are untouched.
        {kFixSel5, 0x49beecu, kSixtieth, kHundredTwentieth, "sel5_dt_1391ac"},
        // SEL2 sel5b (needs sel5): the circle handler 0x132a30 charges R+0x250 toward R+0x258 by
        // R+0x254 per update (slew applied at 0x121448); R+0x254 scales with f8 = R+0x300 *
        // [0x49bb90] (1/60, single reader 0x132b34, codegen grep). The episode exits when
        // |R+0x250| >= 1.0 (0x132b70 -> 0x132d44: 0x32c=5 -> 0x132dd8: sel 5->1): 65 state-2
        // updates in every episode, stock and 120, so 120 exits at half the wall time and the
        // still-true entry guard re-enters (5 episodes vs stock's 1). 1/120 restores the stock
        // wall rate. Phase-2 words need no conversion ([0x49bef0] is a per-launch seed;
        // [0x49bef8]'s consumer never runs; [0x49bef4]/[0x49befc] already converted by crash).
        {kFixSel5b, 0x49bb90u, kSixtieth, kHundredTwentieth, "sel5b_charge_132b34"},
        // BST1 boostfill: sq_boost_gate 0x114130 consumes boost fill R+0x2f8 by [0x49b4fc]
        // (0.0015068, single reader 0x114220, codegen grep; no writers) per update while SQ is
        // held (timer R+0x2e8 0, mode R+0x304 != 0, level R+0x2f4 < 10). At 120 the gate runs
        // 2x per stock tick, so a held boost empties in half the wall time (PIP2: C60 drain
        // 0.0015901/update = step + base 0.0000833; the base drain already halves via boost's
        // [0x49b798] dt, and the R+0x2fc strength publish is immediates, so only this word
        // converts). Halving is exact (power-of-two scale).
        {kFixBoostFill, 0x49b4fcu, 0x3ac57fccu, 0x3a457fccu, "boostfill_consume_114220"},
    }};
    uint64_t mask = mainMask;
    if ((mask & kFixTimers) != 0u && (mask & kFixRng) == 0u)
        mask |= kFixRamp;
    if ((mask & kFixSel5) == 0u)
        mask &= ~kFixSel5b; // sel5b needs sel5 (the phase-1 timer half of the selector-5 rate fix)
    std::vector<Word> all;
    for (const Word &w : words)
        if ((w.fix == 0u || (mask & w.fix) != 0u) && !fh28PoolOverride(w.address, mask))
            all.push_back(w);
    // FH12 words (own mask). recover: the wipeout state 0x12cb68 decays the recovery bar P+0x2c0+0x70 by
    // [0x49b914]/[0x49b918] (+-1/240, single readers 0x12cbc4/0x12cbdc) per update; a circle press adds
    // 17*[0x49b928] once per press (edge: held 818 ms adds once, fh12-R1), so only the decay is halved.
    // pulse: 0x2e39d8 advances the rider colour pulse angle P+0xb04 by 2pi*[0x49f6c0] (1/60, single
    // reader 0x2e3a10) per update: 2 Hz at 120 (0x144acc4 +0.10472 -> +0.20944 rad per stock tick, fh12-L1/L2).
    // crash: in a wipeout the rider body is advanced by the fixed-step motion solver 0x113648 (quantum and
    // z_up already converted), but its callers pass the time to advance as a private 1/60: 0x137750
    // (R+0x300 * [0x49be78], the case run in the Kick Doubt heat-1 wipeout) and 0x1391a8 ([0x49bef4],
    // [0x49befc], same class, not seen live). Each is a single reader. At 120 the solver ran two quanta
    // per update and the crashed rider fell and slid 2x (vz -31.67 per update; stock -31.67 per stock
    // tick); with 1/120: -15.83 per update, position within stock per stock tick (fh12-C5/C6/C7).
    // bounce (FH22): sub_0x13d818 advances the rider bounce phase S+0 (MN1 0x1464e30) by
    // f20 * [0x49c13c] (2.65, single reader 0x13f0d4) per update, wraps at 2pi, and stores
    // 7.5 * f20 * sin(phase) to R+0x31c, which sub_0x11eb98 adds (when > 0) along R+0x370 to the rider
    // pose. At 120 the phase ran 2x per stock tick (+0.148 -> +0.295); 1.325 restores stock frequency.
    // crashbody (FH23): the wipeout body B (0x144a1f0 on Kick Doubt, R = [B+0x40]) is stepped by 0x136f30
    // with f4 = R+0x300 * [0x49be38] (1/60, single reader 0x136f48): R+0x130 += v*f4 (store 0x136f8c),
    // v += (-0.2vx, -0.2vy, -1800)*f4, spin *= 1 - 0.5*f4, quaternion R+0x140 integrated by f4. The board
    // bone (23, 0x152de50) and the 8 board-spray objects (0x1456a60, stride 0x210) follow R+0x130. At 120
    // the body moved a full stock step per update (16 -> 2x per stock tick, fh23-W4/W5); 1/120: 1.00x (E2).
    // uber (FH23): 0x117fe0 slews HUD message 9 (the uber meter, slot +0x57c) toward 1 - R+0x2f0*[0x49b698]
    // by [0x49b69c] (1/60, single reader 0x118cfc) per update; in a wipeout it falls to -1 and the slot is
    // dropped (the meter greys). Events greyed ~20 stock ticks early (s6321 vs 6341, fh23-S1/E1), like
    // FH10's meter messages 5/6.
    // popups (FH24): the HUD timed-slot step 0x116fb8 (only caller 0x117f70) adds [0x49b5d8] (1/60, single
    // reader 0x116fe4) to slot +8 per update and drops the slot at +4 (1.5 s): the crash score popups
    // (slots 35/36) left in half the time at 120 (fh24-S1b/E1b +1/60 vs +1/30 per stock tick).
    // snddt (FH24): the race calls the sound-tree update 0x285bf8 at 0x22c014 with dt = int(S+0xac) *
    // [0x49df10] (1/60, single reader 0x22c008; S+0xac = 1 per update), passed through 0x2ab958/0x2ab7a0/
    // 0x2ab6b0 to the tree's timers: e.g. the 5.0 s fade started at the wipeout (0x5c5c10+0x188, 0x2a68b0).
    // hudprog (FH24): the race-HUD progress table 0x4c8bc8 (stride 0x18, per rider; read by 0x20fb40 from
    // the race-HUD update 0x1e9a30) follows its targets with per-update slew limits: progress +8 by
    // +-[0x49dc44] (138.9, single reader 0x210694, negated in place) in 0x210618, the gap +0x10 by
    // [0x49dc08] / [0x49dc04] (+-46.3, single readers 0x20eef8 / 0x20ee78) in 0x20eda0. After the wipeout
    // the readout caught up at 2x (fh24-E1b); all three halved: 1.000x (fh24-V2).
    const std::array<std::pair<uint32_t, Word>, 14> words12 = {{
        {kFix12Crash, {0u, 0x49be78u, kSixtieth, kHundredTwentieth, "crash_dt_137754"}},
        {kFix12Crash, {0u, 0x49bef4u, kSixtieth, kHundredTwentieth, "crash_dt_13940c"}},
        {kFix12Crash, {0u, 0x49befcu, kSixtieth, kHundredTwentieth, "crash_dt_1394f4"}},
        {kFix12Recover, {0u, 0x49b914u, 0x3b88a358u, 0x3b08a358u, "recover_decay_12cbc4"}},
        {kFix12Recover, {0u, 0x49b918u, 0xbb88a358u, 0xbb08a358u, "recover_decay_12cbdc"}},
        {kFix12Pulse, {0u, 0x49f6c0u, kSixtieth, kHundredTwentieth, "pulse_step_2e3a10"}},
        {kFix12Bounce, {0u, 0x49c13cu, 0x4029999au, 0x3fa9999au, "bounce_phase_13f0d4"}},
        {kFix12CrashBody, {0u, 0x49be38u, kSixtieth, kHundredTwentieth, "crashbody_dt_136f48"}},
        {kFix12Uber, {0u, 0x49b69cu, kSixtieth, kHundredTwentieth, "uber_slew_118cfc"}},
        {kFix12Popups, {0u, 0x49b5d8u, kSixtieth, kHundredTwentieth, "popups_step_116fe4"}},
        {kFix12SndDt, {0u, 0x49df10u, kSixtieth, kHundredTwentieth, "snddt_22c008"}},
        {kFix12HudProg, {0u, 0x49dc44u, 0x430ae38fu, 0x428ae38fu, "hudprog_slew_210694"}},
        {kFix12HudProg, {0u, 0x49dc08u, 0x42392f69u, 0x41b92f69u, "hudprog_gap_up_20eef8"}},
        {kFix12HudProg, {0u, 0x49dc04u, 0xc2392f69u, 0xc1b92f69u, "hudprog_gap_down_20ee78"}},
    }};
    for (const auto &w : words12)
        if ((fix12Mask & w.first) != 0u)
            all.push_back(w.second);
    bool mismatch = false;
    for (const Word &w : all)
    {
        // TRN2 asymk owns 0x49c124 while on (per-update 0/stock/vnret by
        // contact): skip the static verify, the restore below still resets it.
        // REV1: keyed on the applied mask (asymkMaskOk), the same set asymkFix
        // accepts, so the word-table seam stays testable without the env.
        if (!toActive && asymkMaskOk(mask) && w.address == kAsymKPoolK)
            continue;
        const uint32_t want = toActive ? w.expected : w.replacement;
        uint32_t got = 0u;
        if (!rd32(ram, w.address, got) || got != want)
        {
            std::fprintf(stderr, "fh1-full120-refused word=%s addr=%08x got=%08x expected=%08x (%s)\n",
                         w.label, w.address, got, want, toActive ? "enter" : "exit");
            if (!playBuild)
                std::abort();
            // REV1 play build: on enter nothing has been written yet, so the
            // guest keeps its (unexpected-content) stock words untouched; on
            // exit fall through and force-write the stock words so the flip
            // to stock still completes. The caller disables the session.
            if (toActive)
                return false;
            mismatch = true;
            break;
        }
    }
    for (const Word &w : all)
        wr32(ram, w.address, toActive ? w.replacement : w.expected);
    if ((mask & kFixInputChain) != 0u)
        inputChainPoolFlip(ram, toActive);
    std::fprintf(stderr, "fh1-full120 words=%zu %s\n", all.size(), toActive ? "active" : "stock");
    return !mismatch;
}

void clockPostHook(uint8_t *ram, R5900Context *ctx, uint32_t targetPc)
{
    if (targetPc != kInterval || !hooksOn())
        return;
    const uint32_t obj = getRegU32(ctx, 4);
    uint32_t bits = 0u;
    if (!rd32(ram, obj + 0xdcu, bits))
        return;
    float f = 0.0f;
    std::memcpy(&f, &bits, 4);
    f *= 0.5f;
    std::memcpy(&bits, &f, 4);
    wr32(ram, obj + 0xdcu, bits);
}

void onVBlankCommit(uint8_t *ram, uint64_t tick)
{
    if (mode() == Mode::Events)
    {
        g_divThis = g_divNext;
        if (!g_stockInit)
        {
            // A fresh boot starts at tick 1; a state loaded from a stock run
            // carries stock history up to tick-1.
            g_stockInit = true;
            g_stockHalf.store(2u * (tick - 1u), std::memory_order_relaxed);
            g_stockHalfExact.store(2u * (tick - 1u), std::memory_order_relaxed);
        }
        // FH5 counted by the interval scheduled at this VBlank (schedDivisor
        // runs before processEvent): the full interval before the entry VBlank
        // counts as a half, and the half before the exit VBlank as a full.
        g_stockHalf.fetch_add(g_divThis == 2u ? 1u : 2u, std::memory_order_relaxed);
        // FH17: count the interval that just ended.
        g_stockHalfExact.fetch_add(g_divEnded == 2u ? 1u : 2u, std::memory_order_relaxed);
        if (g_commitActive != g_schedActive)
        {
            g_commitActive = g_schedActive;
            g_flipPending = true;
            static uint32_t lines = 0u;
            if (lines++ < 64u)
                std::fprintf(stderr, "fh1-events commit %s tick=%llu half=%llu legacy=%llu\n",
                             g_commitActive ? "enter" : "exit", static_cast<unsigned long long>(tick),
                             static_cast<unsigned long long>(g_stockHalfExact.load(std::memory_order_relaxed)),
                             static_cast<unsigned long long>(stockHalfTicksLegacy()));
        }
    }
    if (mode() == Mode::Always && !g_patched && !full120Refused())
    {
        uint32_t a = 0u, rate = 0u, dt = 0u;
        if (rd32(ram, kMgrPtr, a) && a && !(a & 3u) && rd32(ram, a + 0x10u, rate) &&
            rd32(ram, a + 0x14u, dt) && rate == 60u && dt == kSixtieth)
        {
            g_patched = true;
            std::fprintf(stderr, "fh1-full120-late-arm tick=%llu\n", static_cast<unsigned long long>(tick));
            patchAtManagerInit(ram);
        }
    }
}

// FH1: full120 clock fix (PS2X_SSX3_FULL120_FIX=clock) wraps the leaf
// 0x3a7058 so its interval is in stock wakes (ps2_fh1::clockPostHook). A
// table wrapper covers direct dispatches and checkpoint resumes alike; the
// leaf has no checkpoint, so it always returns to ra.
namespace
{
    PS2Runtime::RecompiledFunction g_fh1Interval = nullptr;

    void fh1IntervalWrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t ra = getRegU32(ctx, 31);
        g_fh1Interval(rdram, ctx, runtime);
        if (ctx->pc == ra && !ps2_guest_unwind::pending())
            clockPostHook(rdram, ctx, ps2_fh1::kInterval);
    }

    void applyFh1Full120(PS2Runtime &runtime)
    {
        if (!ps2_fh1::clockFix())
            return;
        g_fh1Interval = runtime.lookupFunction(ps2_fh1::kInterval);
        if (!g_fh1Interval || !runtime.replaceFunction(ps2_fh1::kInterval, &fh1IntervalWrapper))
        {
            std::fprintf(stderr, "fh1-full120-refused cannot wrap 0x%x\n", ps2_fh1::kInterval);
            std::abort();
        }
        std::fprintf(stderr, "fh1-full120 clock: 0x%x wrapped (interval in stock wakes)\n", ps2_fh1::kInterval);
    }
}

PS2_REGISTER_GAME_OVERRIDE("ssx3-full120-clock",
                           "SLUS_207.72",
                           0x00100008u,
                           0u,
                           applyFh1Full120);

} // namespace ps2_ssx3_fh1
