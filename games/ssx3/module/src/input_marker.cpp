// split-input-1 #5: SSX 3 input marker (`ssx3-input-marker`, LIM1/LIM2).
//
// Moved verbatim from ps2xRuntime/src/lib/Kernel/Stubs/Pad.cpp (marker
// comment block through lim1Apply, plus the marker test hooks) into the
// game module: the `ssx3-input-marker` registration becomes a table row,
// and the per-read tap becomes the notePadRead Module hook. Generic
// runtime keeps only the hook call plus the padrec stamp (generic record
// state). Deltas vs the original, all noted at their sites: hoisted
// generic services arrive via Stubs/Pad.h; the trigger ms returns through
// markMsOut instead of stamping inline; lim1Apply and the test hooks gain
// external linkage; button bits are local copies.
#include "Ssx3InputMarker.h"
#include "Stubs/Pad.h"
#include "ps2_build_id.h"
#include "ps2_fh1_full120.h"
#include "ps2_fh1_input2.h"
#include "ps2_runtime.h"
#include "runtime/ee_guest_unwind.h"
#include "runtime/ps2_savestate.h"

#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <mutex>
#include <string>
#include <vector>

namespace ps2_stubs
{
    namespace
    {
        // LIM1 DEV-ONLY input marker (PS2X_INPUT_MARKER=1). Unset/anything-else
        // (default) = one relaxed atomic check per port-0 pad read, zero behavior
        // change, and no guest-function wrappers (the game override below
        // returns early). When armed, every port-0 read pushes one row onto a
        // host-side ring (4,096 rows: ~10 s at 120 Hz even at two reads per
        // update) carrying the raw pad, the update's 60/120 phase, the FIX
        // echo, a read-time input2 bank snapshot, and the latest read-only
        // guest-call latches (stick-angle tracker 0x133308, completion gate
        // 0x114db8). LIM2 v2 appends a read-time rider sample (pos/vel/|v|,
        // P+0xde0/de4 contact state, the 0x13d1b8 query frame, the uber words,
        // live S+4, award/boost counts) plus the selector-dispatch (0x111408),
        // award (0x119708) and boost (0x10e098) latches.
        // Pressing L3+R3 within 100 ms of each other (the chord passes through
        // to the guest unchanged) stamps `# MARK <ms>` into the active padrec
        // and, 2 s later, writes markers/marker-<UTC>.tsv with the rows in
        // [trigMs-8000, trigMs+2000]. The tracker/gate latches take the
        // player's rider only (TK43d chain; rival AI calls bump *_others)
        // when the chain resolves, every call unfiltered when it dangles
        // (menus). Cap 50 markers/session. The dir is
        // <PS2X_PAD_RECORD_DIR>/../markers (Odin files/markers/); iOS falls
        // back to $HOME/Documents/markers and desktop to ./markers. All guest
        // taps are reads; the det hash is unaffected (proven by the knob-on/off
        // det gate in local/research/LIM1/REPORT.md).
        struct MarkerTrackerLatch
        {
            bool have = false;
            uint64_t tick = 0u; // guest vsync tick at the call
            uint64_t half = 0u; // stockHalfTicks() at the call
            uint32_t s = 0u;    // a0: tracker struct
            uint32_t in0 = 0u;  // [a1+0]: digital input word (INP6: d-pad visible here)
            uint32_t in1 = 0u;  // [a1+4]
            uint32_t s0 = 0u;   // S+0x0 (grab-window flag word)
            uint32_t grab = 0u; // S+0x4 (grab stage 0/1/2/3/5; INP6)
            uint32_t grabId = 0u; // S+0x8 (grab id, -1 = none)
            uint32_t phase = 0u;  // S+0xC (directional phase; 3 = idle)
            uint32_t target = 0u; // S+0x10 (spin target, float bits)
            uint32_t a30 = 0u;    // S+0x30 (accumulated sequence rotation)
            uint32_t r = 0u;      // [S+0x58]: rider struct (0 = unread)
            uint32_t px = 0u, py = 0u, pz = 0u; // R+0x110/4/8 position, float bits (13e0dc proves R+0x110)
            uint32_t vx = 0u, vy = 0u, vz = 0u; // R+0x1e0/4/8 velocity, float bits
            uint32_t meter = 0u;                // R+0x2f4 (meter level; facts.md TK43d)
            uint32_t w438 = 0u;                 // R+0x438 (FH26 word)
            uint32_t wde0 = 0u, wde4 = 0u;      // R+0xde0/4 (control state)
            uint32_t v0 = 0u;                   // post-call v0
            uint32_t others = 0u; // rival-tracker 0x133308 calls since this latch (player filter)
        };
        struct MarkerGateLatch
        {
            bool have = false;
            uint64_t tick = 0u, half = 0u;
            uint32_t a0 = 0u, a1 = 0u, a2 = 0u, a3 = 0u; // a0 = R (rider ptr; INP6)
            uint32_t f0 = 0u, f1 = 0u, f12 = 0u, f20 = 0u, f21 = 0u, f13 = 0u,
                     f14 = 0u; // float bits (srcTap set)
            uint32_t v0 = 0u, v1 = 0u; // post-call: v0 = gate outcome (0 = admit path)
            uint32_t others = 0u; // rival-rider 0x114db8 calls since this latch (player filter)
        };
        struct MarkerSelLatch
        {
            bool have = false;
            uint64_t tick = 0u, half = 0u;
            uint32_t p = 0u;   // a0: rider owner (R = [P+0x18])
            uint32_t sel = 0u; // [P+0xde0] at dispatch: 0 ground, 1 air, 4 rail (FH10)
            uint32_t ctl = 0u; // [P+0xde4] control state
            uint32_t others = 0u; // rival-rider dispatches since this latch (player filter)
        };
        // LIM2 v2 rider sample: read-time (every row, whatever the tracker
        // does), sampled in padMarkerOnRead right after the input2 snapshot.
        struct MarkerRiderSample
        {
            bool rHave = false;
            uint32_t r = 0u;
            uint32_t rpx = 0u, rpy = 0u, rpz = 0u; // R+0x110 live position
            uint32_t rvx = 0u, rvy = 0u, rvz = 0u; // R+0x1e0 live velocity
            double rspd = 0.0;                     // |v|, host-side
            uint32_t rsel = 0u, rctl = 0u; // [P+0xde0/de4] live via latched P (0 when P unknown)
            uint32_t rp = 0u;
            uint64_t rselTick = 0u; // dispatch latch tick (freshness proof)
            uint32_t qnx = 0u, qny = 0u, qnz = 0u; // R+0x370 query normal
            uint32_t qdepth = 0u;                  // R+0x454 query depth
            uint32_t qmode = 0u;                   // R+0x438 live (0 none, else surface index)
            uint32_t qsurf = 0u;                   // R+0x430 query surface id
            uint32_t qpx = 0u, qpy = 0u, qpz = 0u; // R+0x460 query contact point
            uint32_t umeter = 0u;                  // R+0x2f4 uber level
            uint32_t uboost = 0u;                  // R+0x2f8 boost fill
            uint32_t utimer = 0u;                  // R+0x2f0 uber timer
            bool sHave = false;
            uint32_t s2 = 0u;   // S from the tracker latch (stable per race)
            uint32_t stage = 0u; // [S+4] live trick stage
            uint64_t awardN = 0u, awardO = 0u; // player/other 0x119708 calls since the last row
            uint64_t awardTick = 0u;
            uint32_t awardA1 = 0u;
            uint64_t boostN = 0u, boostO = 0u; // player/other 0x10e098 calls since the last row
            uint64_t boostTick = 0u;
            uint32_t boostAmt = 0u; // f12 float bits at the last call
        };
        struct MarkerRow
        {
            uint64_t tick = 0u, ms = 0u;
            uint16_t buttons = 0xFFFFu; // active-low word handed to the guest
            uint8_t lx = 0x80u, ly = 0x80u, rx = 0x80u, ry = 0x80u;
            uint8_t f120 = 0u; // ps2_fh1::Mode as int (0 off, 1 always, 2 events)
            uint8_t half = 0u; // events: stockHalfTicks()&1; else 0
            uint64_t fix = 0u;
            uint32_t fix12 = 0u;
            MarkerTrackerLatch tr{};
            MarkerGateLatch g{};
            MarkerRiderSample rs{};
            // input2 bank snapshot, taken at read time (the bank updates
            // every update, faster than the ~1/6-update tracker; the 0x321298
            // wrapper only discovers bank/count, never reads records, so wrap
            // order vs input2's own wrapper cannot matter).
            uint32_t i2Bank = 0u;
            uint32_t i2Count = 0u;
            uint64_t i2Wrapped = 0u; // cumulative intercepted 0x321298 calls
            ps2_fh1::Input2Record i2[24]{};
        };
        struct MarkerPending
        {
            uint64_t trigMs = 0u, trigTick = 0u;
            std::time_t wall = 0;
            uint32_t index = 0u;
        };
        struct PadMarker
        {
            std::mutex mutex;
            bool enabled = false;
            std::string dir;
            std::vector<MarkerRow> ring;
            uint64_t head = 0u; // next write slot
            uint64_t count = 0u; // rows held (<= cap)
            uint16_t prevButtons = 0xFFFFu;
            uint64_t l3Ms = 0u, r3Ms = 0u; // rising-edge ms while held
            bool haveL3 = false, haveR3 = false;
            bool chordLatched = false; // chord already fired for the current joint hold
            std::vector<MarkerPending> pending;
            uint32_t markersDone = 0u; // written + pending (cap 50)
            bool capLogged = false;
            bool testTickSet = false;
            uint64_t testTick = 0u;
            uint64_t lastMs = 0u;
            // LIM2: cumulative award/boost counts at the last pushed row.
            uint64_t lastAwardN = 0u, lastAwardO = 0u, lastBoostN = 0u, lastBoostO = 0u;
        };
        PadMarker g_padMarker;
        std::atomic<bool> g_padMarkerArmed{false};
        // Latches live outside the mutex: production writes them on the EE
        // thread only (same thread that pushes ring rows); tests inject them
        // with no EE running.
        MarkerTrackerLatch g_lim1TrackerLatch;
        MarkerGateLatch g_lim1GateLatch;
        uint32_t g_lim1Input2Bank = 0u, g_lim1Input2Count = 0u;
        uint64_t g_lim1Input2Wrapped = 0u;
        // LIM2: selector-dispatch latch + award/boost cumulative counters.
        // Same threading as the LIM1 latches (EE thread only in production).
        MarkerSelLatch g_lim2SelLatch;
        uint64_t g_lim2AwardN = 0u, g_lim2AwardO = 0u, g_lim2AwardTick = 0u;
        uint32_t g_lim2AwardA1 = 0u;
        uint64_t g_lim2BoostN = 0u, g_lim2BoostO = 0u, g_lim2BoostTick = 0u;
        uint32_t g_lim2BoostAmt = 0u;
        const uint8_t *g_lim1TestRdram = nullptr; // test hook only; production reads runtime RAM

        constexpr uint64_t kPadMarkerCap = 4096u;
        constexpr uint64_t kPadMarkerChordMs = 100u;
        constexpr uint64_t kPadMarkerBeforeMs = 8000u;
        constexpr uint64_t kPadMarkerAfterMs = 2000u;
        constexpr uint32_t kPadMarkerCap50 = 50u;

        uint64_t padMarkerReadMs(uint64_t tick, bool next)
        {
            // Same script clock as the recorder (PR3): events mode reads the
            // stock-time accumulator, which only knows "now".
            if (ps2_fh1::eventsMode() && !g_padMarker.testTickSet)
            {
                const uint64_t half = ps2_fh1::stockHalfTicks() + (next ? ps2_fh1::stockHalfStepNext() : 0u);
                return (half * 100000ull) / (5994ull * 2ull);
            }
            return padScriptVsyncTickToMs(next ? tick + 1u : tick);
        }

        std::string padMarkerDefaultDir()
        {
            if (const char *rec = std::getenv("PS2X_PAD_RECORD_DIR"))
            {
                if (rec[0] != '\0')
                {
                    namespace fs = std::filesystem;
                    const fs::path parent = fs::path(rec).parent_path();
                    if (!parent.empty())
                    {
                        return (parent / "markers").string();
                    }
                    return "markers";
                }
            }
#if defined(PS2X_IOS)
            if (const char *home = std::getenv("HOME"))
            {
                if (home[0] != '\0')
                {
                    return std::string(home) + "/Documents/markers";
                }
            }
            return {};
#elif defined(__ANDROID__)
            // The app files dir is not visible here; the Odin play env always
            // sets PS2X_PAD_RECORD_DIR, so without it stay disarmed (loud).
            return {};
#else
            return "markers";
#endif
        }

        void padMarkerArmLocked(const std::string &dir, const char *source)
        {
            namespace fs = std::filesystem;
            if (dir.empty())
            {
                std::fprintf(stderr, "input-marker: PS2X_INPUT_MARKER=1 but no output dir; marker off\n");
                return;
            }
            std::error_code ec;
            fs::create_directories(dir, ec);
            if (ec)
            {
                std::fprintf(stderr, "input-marker: cannot create %s (%s); marker off\n",
                             dir.c_str(), source);
                return;
            }
            g_padMarker.dir = dir;
            g_padMarker.ring.assign(kPadMarkerCap, MarkerRow{});
            g_padMarker.enabled = true;
            g_padMarkerArmed.store(true, std::memory_order_relaxed);
            std::fprintf(stderr, "input-marker: armed dir=%s\n", dir.c_str());
        }

        bool padMarkerArm(const std::string &dir, const char *source)
        {
            std::lock_guard<std::mutex> lock(g_padMarker.mutex);
            if (g_padMarker.enabled)
            {
                return true;
            }
            padMarkerArmLocked(dir, source);
            return g_padMarker.enabled;
        }

        void padMarkerPushLocked(const MarkerRow &row)
        {
            g_padMarker.ring[g_padMarker.head % kPadMarkerCap] = row;
            ++g_padMarker.head;
            if (g_padMarker.count < kPadMarkerCap)
            {
                ++g_padMarker.count;
            }
        }

        double padMarkerF32(uint32_t bits)
        {
            float f = 0.0f;
            std::memcpy(&f, &bits, 4u);
            return static_cast<double>(f);
        }

        bool lim1PlayerR(const uint8_t *rdram, uint32_t &out); // defined with the wrappers below

        void padMarkerWriteInput2(std::FILE *f, const MarkerRow &row)
        {
            // Compact bank dump: i:raw,press,release,level,repeat,countdown,guard;... (hex).
            for (uint32_t i = 0; i < row.i2Count && i < 24u; ++i)
            {
                const ps2_fh1::Input2Record &r = row.i2[i];
                std::fprintf(f, "%s%u:%x,%x,%x,%x,%x,%x,%x", i == 0u ? "" : ";", i, r.raw,
                             r.press, r.release, r.level, r.repeat, r.countdown, r.guard);
            }
        }

        void padMarkerWriteRow(std::FILE *f, const MarkerRow &row)
        {
            const MarkerTrackerLatch &tr = row.tr;
            const MarkerGateLatch &g = row.g;
            std::fprintf(f, "%llu\t%llu\t%04x\t%u\t%u\t%u\t%u\t%u\t%u\t%016llx\t%08x",
                         static_cast<unsigned long long>(row.tick),
                         static_cast<unsigned long long>(row.ms), row.buttons, row.lx, row.ly,
                         row.rx, row.ry, row.f120, row.half,
                         static_cast<unsigned long long>(row.fix), row.fix12);
            std::fprintf(f, "\t%u\t%llu\t%llu\t%08x\t%08x\t%08x\t%08x\t%x\t%x\t%x\t%.9g\t%08x\t%08x",
                         tr.have ? 1u : 0u, static_cast<unsigned long long>(tr.tick),
                         static_cast<unsigned long long>(tr.half), tr.s, tr.in0, tr.in1, tr.s0,
                         tr.grab, tr.grabId, tr.phase, padMarkerF32(tr.target), tr.a30, tr.r);
            std::fprintf(f, "\t%.9g\t%.9g\t%.9g\t%.9g\t%.9g\t%.9g\t%08x\t%08x\t%08x\t%08x\t%08x\t%u",
                         padMarkerF32(tr.px), padMarkerF32(tr.py), padMarkerF32(tr.pz),
                         padMarkerF32(tr.vx), padMarkerF32(tr.vy), padMarkerF32(tr.vz), tr.meter,
                         tr.w438, tr.wde0, tr.wde4, tr.v0, tr.others);
            std::fprintf(f, "\t%08x\t%u\t%llu\t", row.i2Bank, row.i2Count,
                         static_cast<unsigned long long>(row.i2Wrapped));
            padMarkerWriteInput2(f, row);
            std::fprintf(f, "\t%u\t%llu\t%llu\t%08x\t%08x\t%08x\t%08x",
                         g.have ? 1u : 0u, static_cast<unsigned long long>(g.tick),
                         static_cast<unsigned long long>(g.half), g.a0, g.a1, g.a2, g.a3);
            std::fprintf(f, "\t%.9g\t%.9g\t%.9g\t%.9g\t%.9g\t%.9g\t%.9g\t%08x\t%08x\t%u",
                         padMarkerF32(g.f0), padMarkerF32(g.f1), padMarkerF32(g.f12),
                         padMarkerF32(g.f20), padMarkerF32(g.f21), padMarkerF32(g.f13),
                         padMarkerF32(g.f14), g.v0, g.v1, g.others);
            // LIM2 v2 rider sample (appended; v1 columns above are unchanged).
            const MarkerRiderSample &rs = row.rs;
            std::fprintf(f,
                         "\t%u\t%08x\t%.9g\t%.9g\t%.9g\t%.9g\t%.9g\t%.9g\t%.9g"
                         "\t%08x\t%08x\t%08x\t%llu"
                         "\t%.9g\t%.9g\t%.9g\t%.9g\t%08x\t%08x\t%.9g\t%.9g\t%.9g"
                         "\t%08x\t%.9g\t%.9g"
                         "\t%u\t%08x\t%x"
                         "\t%llu\t%llu\t%llu\t%08x\t%llu\t%llu\t%llu\t%.9g\n",
                         rs.rHave ? 1u : 0u, rs.r, padMarkerF32(rs.rpx), padMarkerF32(rs.rpy),
                         padMarkerF32(rs.rpz), padMarkerF32(rs.rvx), padMarkerF32(rs.rvy),
                         padMarkerF32(rs.rvz), rs.rspd, rs.rsel, rs.rctl, rs.rp,
                         static_cast<unsigned long long>(rs.rselTick), padMarkerF32(rs.qnx),
                         padMarkerF32(rs.qny), padMarkerF32(rs.qnz), padMarkerF32(rs.qdepth),
                         rs.qmode, rs.qsurf, padMarkerF32(rs.qpx), padMarkerF32(rs.qpy),
                         padMarkerF32(rs.qpz), rs.umeter, padMarkerF32(rs.uboost),
                         padMarkerF32(rs.utimer), rs.sHave ? 1u : 0u, rs.s2, rs.stage,
                         static_cast<unsigned long long>(rs.awardN),
                         static_cast<unsigned long long>(rs.awardO),
                         static_cast<unsigned long long>(rs.awardTick), rs.awardA1,
                         static_cast<unsigned long long>(rs.boostN),
                         static_cast<unsigned long long>(rs.boostO),
                         static_cast<unsigned long long>(rs.boostTick), padMarkerF32(rs.boostAmt));
        }

        void padMarkerWriteLocked(const MarkerPending &p, bool truncated)
        {
            namespace fs = std::filesystem;
            char stamp[32] = {0};
            padRecordUtc(stamp, sizeof(stamp), p.wall, "%Y%m%d-%H%M%S");
            std::string path;
            std::FILE *f = nullptr;
            for (int n = 0; n <= 100; ++n)
            {
                char name[64];
                if (n == 0)
                {
                    std::snprintf(name, sizeof(name), "marker-%s.tsv", stamp);
                }
                else
                {
                    std::snprintf(name, sizeof(name), "marker-%s-%d.tsv", stamp, n + 1);
                }
                path = g_padMarker.dir + "/" + name;
                std::error_code ec;
                if (fs::exists(path, ec) || ec)
                {
                    path.clear();
                    continue;
                }
                f = std::fopen(path.c_str(), "w");
                if (f)
                {
                    break;
                }
                path.clear();
            }
            if (!f)
            {
                std::fprintf(stderr, "input-marker: cannot open a session file in %s; marker #%u lost\n",
                             g_padMarker.dir.c_str(), p.index);
                return;
            }
            char fix[256], f120[64];
            std::snprintf(f120, sizeof(f120), "%s", padRecordKnob("PS2X_SSX3_FULL120"));
            std::snprintf(fix, sizeof(fix), "%s", padRecordKnob("PS2X_SSX3_FULL120_FIX"));
            padRecordSanitize(f120);
            padRecordSanitize(fix);
            std::fprintf(f, "# input-marker v2 #%u trigger_ms=%llu trigger_tick=%llu%s\n",
                         p.index, static_cast<unsigned long long>(p.trigMs),
                         static_cast<unsigned long long>(p.trigTick), truncated ? " truncated" : "");
            std::fprintf(f, "# build=%s full120 FULL120=%s FULL120_FIX=%s\n", ps2x::buildId(), f120,
                         fix);
            std::fprintf(f, "# window_ms=[%llu,%llu] buttons=active-low-as-handed-to-guest "
                            "target=float(S+0x10) pos=R+0x110 vel=R+0x1e0 "
                            "v2=read-time-rider-sample\n",
                         static_cast<unsigned long long>(p.trigMs > kPadMarkerBeforeMs
                                                             ? p.trigMs - kPadMarkerBeforeMs
                                                             : 0u),
                         static_cast<unsigned long long>(p.trigMs + kPadMarkerAfterMs));
            std::fprintf(f, "tick\tms\tbuttons\tlx\tly\trx\try\tf120\thalf\tfix\tfix12\t"
                            "tr_have\ttr_tick\ttr_half\ts\tin0\tin1\ts0\tgrab\tgrabid\tphase\ttarget\ta30\tr\t"
                            "px\tpy\tpz\tvx\tvy\tvz\tmeter\tw438\twde0\twde4\ttr_v0\ttr_others\t"
                            "i2_bank\ti2_count\ti2_wrapped\ti2_recs\t"
                            "g_have\tg_tick\tg_half\ta0\ta1\ta2\ta3\tf0\tf1\tf12\tf20\tf21\tf13\tf14\tv0\tv1\tg_others\t"
                            "r_have\tr_live\trpx\trpy\trpz\trvx\trvy\trvz\trspd\t"
                            "rsel\trctl\trp\trsel_tick\t"
                            "qnx\tqny\tqnz\tqdepth\tqmode\tqsurf\tqpx\tqpy\tqpz\t"
                            "umeter\tuboost\tutimer\t"
                            "s_have\ts2\tstage\t"
                            "award_n\taward_o\taward_tick\taward_a1\t"
                            "boost_n\tboost_o\tboost_tick\tboost_amt\n");
            const uint64_t loMs = p.trigMs > kPadMarkerBeforeMs ? p.trigMs - kPadMarkerBeforeMs : 0u;
            const uint64_t hiMs = truncated ? g_padMarker.lastMs : p.trigMs + kPadMarkerAfterMs;
            for (uint64_t i = 0u; i < g_padMarker.count; ++i)
            {
                const MarkerRow &row =
                    g_padMarker.ring[(g_padMarker.head - g_padMarker.count + i) % kPadMarkerCap];
                if (row.ms < loMs || row.ms > hiMs)
                {
                    continue;
                }
                padMarkerWriteRow(f, row);
            }
            std::fflush(f);
            std::fclose(f);
            std::fprintf(stderr, "input-marker: wrote %s\n", path.c_str());
        }

        void padMarkerExitFlush()
        {
            // Best-effort finalize of triggers younger than 2 s at exit (the
            // boot's stop tick may land inside the post window); a force-stop
            // loses those, but the padrec MARK line already stamped them.
            std::unique_lock<std::mutex> lock(g_padMarker.mutex, std::try_to_lock);
            if (!lock.owns_lock() || !g_padMarker.enabled)
            {
                return;
            }
            for (const MarkerPending &p : g_padMarker.pending)
            {
                padMarkerWriteLocked(p, true);
            }
            g_padMarker.pending.clear();
        }

        void padMarkerResetLocked()
        {
            g_padMarker.enabled = false;
            g_padMarker.dir.clear();
            g_padMarker.ring.clear();
            g_padMarker.head = 0u;
            g_padMarker.count = 0u;
            g_padMarker.prevButtons = 0xFFFFu;
            g_padMarker.l3Ms = 0u;
            g_padMarker.r3Ms = 0u;
            g_padMarker.haveL3 = false;
            g_padMarker.haveR3 = false;
            g_padMarker.chordLatched = false;
            g_padMarker.pending.clear();
            g_padMarker.markersDone = 0u;
            g_padMarker.capLogged = false;
            g_padMarker.testTickSet = false;
            g_padMarker.testTick = 0u;
            g_padMarker.lastMs = 0u;
            g_padMarker.lastAwardN = 0u;
            g_padMarker.lastAwardO = 0u;
            g_padMarker.lastBoostN = 0u;
            g_padMarker.lastBoostO = 0u;
            g_lim1TrackerLatch = MarkerTrackerLatch{};
            g_lim1GateLatch = MarkerGateLatch{};
            g_lim1Input2Bank = 0u;
            g_lim1Input2Count = 0u;
            g_lim1Input2Wrapped = 0u;
            g_lim2SelLatch = MarkerSelLatch{};
            g_lim2AwardN = 0u;
            g_lim2AwardO = 0u;
            g_lim2AwardTick = 0u;
            g_lim2AwardA1 = 0u;
            g_lim2BoostN = 0u;
            g_lim2BoostO = 0u;
            g_lim2BoostTick = 0u;
            g_lim2BoostAmt = 0u;
            g_lim1TestRdram = nullptr;
            g_padMarkerArmed.store(false, std::memory_order_relaxed);
        }

        // LIM2: read-time rider sample. Host-only (rd32-checked guest reads,
        // no guest writes). R resolves the way LIM1 does (TK43d chain); P via
        // the selector-dispatch latch; S via the tracker latch (stable per
        // race, so the live [S+4] read stays valid while grounded).
        void padMarkerSampleRider(const uint8_t *rdram, MarkerRow &row)
        {
            MarkerRiderSample &rs = row.rs;
            if (rdram)
            {
                uint32_t r = 0u;
                if (lim1PlayerR(rdram, r))
                {
                    rs.rHave = true;
                    rs.r = r;
                    ps2_fh1::rd32(rdram, r + 0x110u, rs.rpx);
                    ps2_fh1::rd32(rdram, r + 0x114u, rs.rpy);
                    ps2_fh1::rd32(rdram, r + 0x118u, rs.rpz);
                    ps2_fh1::rd32(rdram, r + 0x1e0u, rs.rvx);
                    ps2_fh1::rd32(rdram, r + 0x1e4u, rs.rvy);
                    ps2_fh1::rd32(rdram, r + 0x1e8u, rs.rvz);
                    const double vx = padMarkerF32(rs.rvx), vy = padMarkerF32(rs.rvy),
                                 vz = padMarkerF32(rs.rvz);
                    rs.rspd = std::sqrt(vx * vx + vy * vy + vz * vz);
                    ps2_fh1::rd32(rdram, r + 0x370u, rs.qnx);
                    ps2_fh1::rd32(rdram, r + 0x374u, rs.qny);
                    ps2_fh1::rd32(rdram, r + 0x378u, rs.qnz);
                    ps2_fh1::rd32(rdram, r + 0x454u, rs.qdepth);
                    ps2_fh1::rd32(rdram, r + 0x438u, rs.qmode);
                    ps2_fh1::rd32(rdram, r + 0x430u, rs.qsurf);
                    ps2_fh1::rd32(rdram, r + 0x460u, rs.qpx);
                    ps2_fh1::rd32(rdram, r + 0x464u, rs.qpy);
                    ps2_fh1::rd32(rdram, r + 0x468u, rs.qpz);
                    ps2_fh1::rd32(rdram, r + 0x2f4u, rs.umeter);
                    ps2_fh1::rd32(rdram, r + 0x2f8u, rs.uboost);
                    ps2_fh1::rd32(rdram, r + 0x2f0u, rs.utimer);
                }
                if (g_lim2SelLatch.have && g_lim2SelLatch.p != 0u)
                {
                    rs.rp = g_lim2SelLatch.p;
                    rs.rselTick = g_lim2SelLatch.tick;
                    ps2_fh1::rd32(rdram, rs.rp + 0xde0u, rs.rsel);
                    ps2_fh1::rd32(rdram, rs.rp + 0xde4u, rs.rctl);
                }
                if (g_lim1TrackerLatch.s != 0u)
                {
                    rs.sHave = true;
                    rs.s2 = g_lim1TrackerLatch.s;
                    ps2_fh1::rd32(rdram, rs.s2 + 0x4u, rs.stage);
                }
            }
            rs.awardN = g_lim2AwardN - g_padMarker.lastAwardN;
            rs.awardO = g_lim2AwardO - g_padMarker.lastAwardO;
            rs.awardTick = g_lim2AwardTick;
            rs.awardA1 = g_lim2AwardA1;
            rs.boostN = g_lim2BoostN - g_padMarker.lastBoostN;
            rs.boostO = g_lim2BoostO - g_padMarker.lastBoostO;
            rs.boostTick = g_lim2BoostTick;
            rs.boostAmt = g_lim2BoostAmt;
            g_padMarker.lastAwardN = g_lim2AwardN;
            g_padMarker.lastAwardO = g_lim2AwardO;
            g_padMarker.lastBoostN = g_lim2BoostN;
            g_padMarker.lastBoostO = g_lim2BoostO;
        }

        // Active-low button bits (values from Pad.cpp, which owns the pad state).
        constexpr uint16_t kPadBtnL3 = 1u << 1;
        constexpr uint16_t kPadBtnR3 = 1u << 2;

        // ---- LIM1 guest-call latches (read-only; armed only) ----
        PS2Runtime::RecompiledFunction g_lim1TrackerNext = nullptr;
        PS2Runtime::RecompiledFunction g_lim1GateNext = nullptr;
        PS2Runtime::RecompiledFunction g_lim1Input2Next = nullptr;

        uint64_t lim1VsyncTick(PS2Runtime *runtime)
        {
            return runtime ? runtime->memory().gs().vsyncTick.load(std::memory_order_relaxed) : 0u;
        }

        uint32_t lim1FpuBits(R5900Context *ctx, int f)
        {
            uint32_t bits = 0u;
            if (ctx && f >= 0 && f < 32)
            {
                std::memcpy(&bits, &ctx->f[f], 4u);
            }
            return bits;
        }

        // Player-rider identity (facts.md TK43d, course-independent, verified
        // through wipeouts/resets): R = [[[0x4a28a8]+0x84]+0xC]+0x28. False
        // when any link is unreadable or null (menus / no race object), in
        // which case the latches below take every call unfiltered.
        bool lim1PlayerR(const uint8_t *rdram, uint32_t &out)
        {
            uint32_t w = 0u;
            if (!ps2_fh1::rd32(rdram, 0x4a28a8u, w) || w == 0u)
            {
                return false;
            }
            if (!ps2_fh1::rd32(rdram, w + 0x84u, w) || w == 0u)
            {
                return false;
            }
            if (!ps2_fh1::rd32(rdram, w + 0x0cu, w) || w == 0u)
            {
                return false;
            }
            if (!ps2_fh1::rd32(rdram, w + 0x28u, w) || w == 0u)
            {
                return false;
            }
            out = w;
            return true;
        }

        void lim1TrackerWrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
        {
            // Player filter (A3 showed rival AI trackers interleaving on the
            // same call site): when the TK43d chain resolves, only the
            // player's tracker latches; rival calls just bump others. When it
            // dangles (menus), every call latches unfiltered.
            bool latched = false;
            if (g_padMarkerArmed.load(std::memory_order_relaxed))
            {
                const uint32_t s = getRegU32(ctx, 4);
                uint32_t r = 0u;
                ps2_fh1::rd32(rdram, s + 0x58u, r);
                uint32_t player = 0u;
                if (!lim1PlayerR(rdram, player))
                {
                    player = r; // chain dangling: take every call unfiltered
                }
                if (r != player)
                {
                    ++g_lim1TrackerLatch.others;
                }
                else
                {
                    latched = true;
                    MarkerTrackerLatch lat{};
                    lat.have = true;
                    lat.tick = lim1VsyncTick(runtime);
                    lat.half = ps2_fh1::stockHalfTicks();
                    lat.s = s;
                    lat.r = r;
                    const uint32_t in = getRegU32(ctx, 5);
                    uint32_t w = 0u;
                    if (ps2_fh1::rd32(rdram, in, w))
                    {
                        lat.in0 = w;
                    }
                    if (ps2_fh1::rd32(rdram, in + 4u, w))
                    {
                        lat.in1 = w;
                    }
                    ps2_fh1::rd32(rdram, lat.s + 0x0u, lat.s0);
                    ps2_fh1::rd32(rdram, lat.s + 0x4u, lat.grab);
                    ps2_fh1::rd32(rdram, lat.s + 0x8u, lat.grabId);
                    ps2_fh1::rd32(rdram, lat.s + 0xcu, lat.phase);
                    ps2_fh1::rd32(rdram, lat.s + 0x10u, lat.target);
                    ps2_fh1::rd32(rdram, lat.s + 0x30u, lat.a30);
                    if (lat.r != 0u)
                    {
                        ps2_fh1::rd32(rdram, lat.r + 0x110u, lat.px);
                        ps2_fh1::rd32(rdram, lat.r + 0x114u, lat.py);
                        ps2_fh1::rd32(rdram, lat.r + 0x118u, lat.pz);
                        ps2_fh1::rd32(rdram, lat.r + 0x1e0u, lat.vx);
                        ps2_fh1::rd32(rdram, lat.r + 0x1e4u, lat.vy);
                        ps2_fh1::rd32(rdram, lat.r + 0x1e8u, lat.vz);
                        ps2_fh1::rd32(rdram, lat.r + 0x2f4u, lat.meter);
                        ps2_fh1::rd32(rdram, lat.r + 0x438u, lat.w438);
                        ps2_fh1::rd32(rdram, lat.r + 0xde0u, lat.wde0);
                        ps2_fh1::rd32(rdram, lat.r + 0xde4u, lat.wde4);
                    }
                    g_lim1TrackerLatch = lat;
                }
            }
            const uint32_t ra = getRegU32(ctx, 31);
            g_lim1TrackerNext(rdram, ctx, runtime);
            if (latched && ctx->pc == ra && !ps2_guest_unwind::pending())
            {
                g_lim1TrackerLatch.v0 = getRegU32(ctx, 2);
            }
        }

        void lim1GateWrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
        {
            // Same player filter as the tracker (a0 = R; INP6): rival-rider
            // gate calls just bump others.
            bool latched = false;
            if (g_padMarkerArmed.load(std::memory_order_relaxed))
            {
                const uint32_t a0 = getRegU32(ctx, 4);
                uint32_t player = 0u;
                if (!lim1PlayerR(rdram, player))
                {
                    player = a0; // chain dangling: take every call unfiltered
                }
                if (a0 != player)
                {
                    ++g_lim1GateLatch.others;
                }
                else
                {
                    latched = true;
                    MarkerGateLatch lat{};
                    lat.have = true;
                    lat.tick = lim1VsyncTick(runtime);
                    lat.half = ps2_fh1::stockHalfTicks();
                    lat.a0 = a0;
                    lat.a1 = getRegU32(ctx, 5);
                    lat.a2 = getRegU32(ctx, 6);
                    lat.a3 = getRegU32(ctx, 7);
                    lat.f0 = lim1FpuBits(ctx, 0);
                    lat.f1 = lim1FpuBits(ctx, 1);
                    lat.f12 = lim1FpuBits(ctx, 12);
                    lat.f20 = lim1FpuBits(ctx, 20);
                    lat.f21 = lim1FpuBits(ctx, 21);
                    lat.f13 = lim1FpuBits(ctx, 13);
                    lat.f14 = lim1FpuBits(ctx, 14);
                    g_lim1GateLatch = lat;
                }
            }
            const uint32_t ra = getRegU32(ctx, 31);
            g_lim1GateNext(rdram, ctx, runtime);
            if (latched && ctx->pc == ra && !ps2_guest_unwind::pending())
            {
                g_lim1GateLatch.v0 = getRegU32(ctx, 2);
                g_lim1GateLatch.v1 = getRegU32(ctx, 3);
            }
        }

        void lim1Input2Wrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
        {
            // Bank discovery only (same guard as input2's own wrapper); each
            // ring row snapshots the records at read time, so wrap order vs
            // input2's own wrapper cannot matter.
            if (g_padMarkerArmed.load(std::memory_order_relaxed))
            {
                const uint32_t bank = getRegU32(ctx, 4) & 0x1ffffffu;
                const uint32_t count = getRegU32(ctx, 5);
                if (count <= 24u && bank <= 0x2000000u - 4u - 24u * 28u)
                {
                    g_lim1Input2Bank = bank;
                    g_lim1Input2Count = count;
                    ++g_lim1Input2Wrapped;
                }
            }
            g_lim1Input2Next(rdram, ctx, runtime);
        }

        // ---- LIM2 v2 latches: selector dispatch + award/boost counters ----
        // Entry-only (no post-call reads, so guest unwinds cannot skew them).
        PS2Runtime::RecompiledFunction g_lim2SelNext = nullptr;
        PS2Runtime::RecompiledFunction g_lim2AwardNext = nullptr;
        PS2Runtime::RecompiledFunction g_lim2BoostNext = nullptr;

        void lim2SelWrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
        {
            // 0x111408 selector dispatch, a0 = owner P. The dispatch runs
            // every update for every rider (ground, air, rail), so this latch
            // is the stall-proof contact state. Player filter via R =
            // [P+0x18] (valid pre-call; the query's chain, same struct).
            if (g_padMarkerArmed.load(std::memory_order_relaxed))
            {
                const uint32_t p = getRegU32(ctx, 4);
                uint32_t r = 0u;
                ps2_fh1::rd32(rdram, p + 0x18u, r);
                uint32_t player = 0u;
                if (!lim1PlayerR(rdram, player))
                {
                    player = r; // chain dangling: take every call unfiltered
                }
                if (r != player)
                {
                    ++g_lim2SelLatch.others;
                }
                else
                {
                    MarkerSelLatch lat{};
                    lat.have = true;
                    lat.tick = lim1VsyncTick(runtime);
                    lat.half = ps2_fh1::stockHalfTicks();
                    lat.p = p;
                    ps2_fh1::rd32(rdram, p + 0xde0u, lat.sel);
                    ps2_fh1::rd32(rdram, p + 0xde4u, lat.ctl);
                    g_lim2SelLatch = lat;
                }
            }
            g_lim2SelNext(rdram, ctx, runtime);
        }

        void lim2AwardWrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
        {
            // 0x119708 trick award (all 5 call sites in 0x1352a8 pass a0 =
            // [R+0x790], the rider's score state). Player filter: a0 must
            // read back as the player R's [R+0x790].
            if (g_padMarkerArmed.load(std::memory_order_relaxed))
            {
                const uint32_t a0 = getRegU32(ctx, 4);
                uint32_t player = 0u, score = 0u;
                const bool known =
                    lim1PlayerR(rdram, player) && ps2_fh1::rd32(rdram, player + 0x790u, score);
                if (!known || a0 == score)
                {
                    ++g_lim2AwardN;
                    g_lim2AwardTick = lim1VsyncTick(runtime);
                    g_lim2AwardA1 = getRegU32(ctx, 5);
                }
                else
                {
                    ++g_lim2AwardO;
                }
            }
            g_lim2AwardNext(rdram, ctx, runtime);
        }

        void lim2BoostWrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
        {
            // 0x10e098 boost add, a0 = R (it reads R+0x304/0x86c/0x2f8, so
            // every call site passes the rider). Player filter: a0 == player R.
            if (g_padMarkerArmed.load(std::memory_order_relaxed))
            {
                const uint32_t a0 = getRegU32(ctx, 4);
                uint32_t player = 0u;
                if (!lim1PlayerR(rdram, player))
                {
                    player = a0; // chain dangling: take every call unfiltered
                }
                if (a0 == player)
                {
                    ++g_lim2BoostN;
                    g_lim2BoostTick = lim1VsyncTick(runtime);
                    g_lim2BoostAmt = lim1FpuBits(ctx, 12);
                }
                else
                {
                    ++g_lim2BoostO;
                }
            }
            g_lim2BoostNext(rdram, ctx, runtime);
        }

    } // namespace

        // split-input-1 #5: the Module hook (was padMarkerOnRead). Scalars only:
        // the pad state type stays runtime-side, production RAM arrives on the
        // hook, and a true return hands the trigger ms back for the runtime's
        // padrec stamp (the stamp touches generic record state, so it stays).
        bool notePadRead(uint16_t buttons, uint8_t lx, uint8_t ly, uint8_t rx, uint8_t ry,
                                 uint64_t guestVsyncTick, int port, int slot, const uint8_t *hookRdram,
                                 uint64_t *markMsOut)
        {
            if (!g_padMarkerArmed.load(std::memory_order_relaxed))
            {
                return false;
            }
            std::lock_guard<std::mutex> lock(g_padMarker.mutex);
            if (!g_padMarker.enabled)
            {
                return false;
            }
            if (port != 0 || slot != 0)
            {
                return false;
            }
            const uint64_t tick = g_padMarker.testTickSet ? g_padMarker.testTick : guestVsyncTick;
            const uint64_t nowMs = padMarkerReadMs(tick, false);
            g_padMarker.lastMs = nowMs;
            MarkerRow row{};
            row.tick = tick;
            row.ms = nowMs;
            row.buttons = buttons;
            row.lx = lx;
            row.ly = ly;
            row.rx = rx;
            row.ry = ry;
            row.f120 = static_cast<uint8_t>(ps2_fh1::mode());
            row.half = (ps2_fh1::eventsMode() && !g_padMarker.testTickSet)
                           ? static_cast<uint8_t>(ps2_fh1::stockHalfTicks() & 1u)
                           : 0u;
            row.fix = ps2_fh1::fixMask();
            row.fix12 = ps2_fh1::fixMask12();
            row.tr = g_lim1TrackerLatch;
            row.g = g_lim1GateLatch;
            row.i2Bank = g_lim1Input2Bank;
            row.i2Count = g_lim1Input2Count;
            row.i2Wrapped = g_lim1Input2Wrapped;
            // Production RAM arrives on the hook; tests point the snapshot at test RAM.
            const uint8_t *rdram = g_lim1TestRdram ? g_lim1TestRdram : hookRdram;
            if (row.i2Count > 0u && row.i2Count <= 24u &&
                row.i2Bank <= 0x2000000u - 4u - 24u * 28u && rdram)
            {
                for (uint32_t i = 0; i < row.i2Count; ++i)
                {
                    std::memcpy(&row.i2[i], rdram + row.i2Bank + 4u + i * 28u, 28u);
                }
            }
            else
            {
                row.i2Count = 0u;
            }
            padMarkerSampleRider(rdram, row); // LIM2 v2: same read-time point, every row
            padMarkerPushLocked(row);
            bool chordFired = false;
            // Chord edges on the effective (guest-visible) state.
            const bool l3Down = (buttons & kPadBtnL3) == 0u;
            const bool r3Down = (buttons & kPadBtnR3) == 0u;
            const bool wasL3Down = (g_padMarker.prevButtons & kPadBtnL3) == 0u;
            const bool wasR3Down = (g_padMarker.prevButtons & kPadBtnR3) == 0u;
            if (l3Down && !wasL3Down)
            {
                g_padMarker.l3Ms = nowMs;
                g_padMarker.haveL3 = true;
            }
            if (!l3Down && wasL3Down)
            {
                g_padMarker.haveL3 = false;
                g_padMarker.chordLatched = false;
            }
            if (r3Down && !wasR3Down)
            {
                g_padMarker.r3Ms = nowMs;
                g_padMarker.haveR3 = true;
            }
            if (!r3Down && wasR3Down)
            {
                g_padMarker.haveR3 = false;
                g_padMarker.chordLatched = false;
            }
            g_padMarker.prevButtons = buttons;
            if (!g_padMarker.chordLatched && l3Down && r3Down && g_padMarker.haveL3 &&
                g_padMarker.haveR3)
            {
                const uint64_t gap = g_padMarker.l3Ms > g_padMarker.r3Ms
                                         ? g_padMarker.l3Ms - g_padMarker.r3Ms
                                         : g_padMarker.r3Ms - g_padMarker.l3Ms;
                if (gap <= kPadMarkerChordMs)
                {
                    g_padMarker.chordLatched = true;
                    if (g_padMarker.markersDone >= kPadMarkerCap50)
                    {
                        if (!g_padMarker.capLogged)
                        {
                            g_padMarker.capLogged = true;
                            std::fprintf(stderr,
                                         "input-marker: marker cap 50 reached; ignoring further chords\n");
                        }
                    }
                    else
                    {
                        MarkerPending p{};
                        p.trigMs = nowMs;
                        p.trigTick = tick;
                        p.wall = std::time(nullptr);
                        p.index = g_padMarker.markersDone + 1u;
                        g_padMarker.pending.push_back(p);
                        ++g_padMarker.markersDone;
                        std::fprintf(stderr, "input-marker: trigger #%u ms=%llu tick=%llu\n",
                                     p.index, static_cast<unsigned long long>(nowMs),
                                     static_cast<unsigned long long>(tick));
                        if (markMsOut != nullptr)
                        {
                            *markMsOut = nowMs;
                        }
                        chordFired = true;
                        // UX1: the course picker confirms chords the same way
                        // (status line + Android toast; store-only elsewhere).
                        // toast-audit-1: dev-only behind PS2X_DEV_TOASTS=1.
                        if (const char *dt = std::getenv("PS2X_DEV_TOASTS");
                            dt != nullptr && std::strcmp(dt, "1") == 0)
                            ps2_savestate::noteQuickStatus("input marked");
                    }
                }
            }
            // Flush triggers whose 2 s post window has elapsed.
            for (auto it = g_padMarker.pending.begin(); it != g_padMarker.pending.end();)
            {
                if (nowMs >= it->trigMs + kPadMarkerAfterMs)
                {
                    padMarkerWriteLocked(*it, false);
                    it = g_padMarker.pending.erase(it);
                }
                else
                {
                    ++it;
                }
            }
            return chordFired;
        }

    void lim1Apply(PS2Runtime &runtime)
    {
        const char *v = std::getenv("PS2X_INPUT_MARKER");
        if (!v || std::strcmp(v, "1") != 0)
        {
            return;
        }
        if (!padMarkerArm(padMarkerDefaultDir(), "env"))
        {
            return;
        }
        std::atexit(padMarkerExitFlush);
        int tracker = 0, gate = 0, input2 = 0, sel = 0, award = 0, boost = 0;
        g_lim1TrackerNext = runtime.lookupFunction(0x133308u);
        if (g_lim1TrackerNext && runtime.replaceFunction(0x133308u, &lim1TrackerWrapper))
        {
            tracker = 1;
        }
        g_lim1GateNext = runtime.lookupFunction(0x114db8u);
        if (g_lim1GateNext && runtime.replaceFunction(0x114db8u, &lim1GateWrapper))
        {
            gate = 1;
        }
        g_lim1Input2Next = runtime.lookupFunction(0x321298u);
        if (g_lim1Input2Next && runtime.replaceFunction(0x321298u, &lim1Input2Wrapper))
        {
            input2 = 1;
        }
        g_lim2SelNext = runtime.lookupFunction(0x111408u);
        if (g_lim2SelNext && runtime.replaceFunction(0x111408u, &lim2SelWrapper))
        {
            sel = 1;
        }
        g_lim2AwardNext = runtime.lookupFunction(0x119708u);
        if (g_lim2AwardNext && runtime.replaceFunction(0x119708u, &lim2AwardWrapper))
        {
            award = 1;
        }
        g_lim2BoostNext = runtime.lookupFunction(0x10e098u);
        if (g_lim2BoostNext && runtime.replaceFunction(0x10e098u, &lim2BoostWrapper))
        {
            boost = 1;
        }
        std::fprintf(stderr, "input-marker: hooks tracker=%d gate=%d input2=%d sel=%d award=%d boost=%d\n",
                     tracker, gate, input2, sel, award, boost);
    }

    bool setPadMarkerDirForTest(const char *dir)
    {
        if (!dir || dir[0] == '\0')
        {
            return false;
        }
        std::lock_guard<std::mutex> lock(g_padMarker.mutex);
        padMarkerResetLocked();
        padMarkerArmLocked(dir, "test");
        return g_padMarker.enabled;
    }

    void setPadMarkerTickForTest(uint64_t tick)
    {
        std::lock_guard<std::mutex> lock(g_padMarker.mutex);
        g_padMarker.testTickSet = true;
        g_padMarker.testTick = tick;
    }

    void setPadMarkerTrackerForTest(uint32_t s, uint32_t in0, uint32_t grab, uint32_t phase,
                                   uint32_t target)
    {
        std::lock_guard<std::mutex> lock(g_padMarker.mutex);
        MarkerTrackerLatch lat{};
        lat.have = true;
        lat.tick = g_padMarker.testTick;
        lat.s = s;
        lat.in0 = in0;
        lat.in1 = 0x5a5a5a5au;
        lat.s0 = 0x11u;
        lat.grab = grab;
        lat.grabId = 0xFFFFFFFFu;
        lat.phase = phase;
        lat.target = target;
        lat.a30 = 0x22u;
        lat.r = 0x01400000u;
        lat.px = 0x3F800000u; // 1.0
        lat.py = 0x40000000u; // 2.0
        lat.pz = 0x40400000u; // 3.0
        lat.vx = 0x40800000u; // 4.0
        lat.vy = 0x40900000u; // 4.5
        lat.vz = 0x40A00000u; // 5.0
        lat.meter = 0x33u;
        lat.w438 = 0x44u;
        lat.wde0 = 0x55u;
        lat.wde4 = 0x66u;
        lat.v0 = 0x77u;
        g_lim1TrackerLatch = lat;
    }

    void setPadMarkerGateForTest(uint32_t a0, uint32_t v0)
    {
        std::lock_guard<std::mutex> lock(g_padMarker.mutex);
        MarkerGateLatch lat{};
        lat.have = true;
        lat.tick = g_padMarker.testTick;
        lat.a0 = a0;
        lat.a1 = 0x11111111u;
        lat.a2 = 0x22222222u;
        lat.a3 = 0x33333333u;
        lat.f0 = 0x3F800000u; // 1.0
        lat.f1 = 0x40000000u; // 2.0
        lat.f12 = 0x40400000u; // 3.0
        lat.f20 = 0x40800000u; // 4.0
        lat.f21 = 0x40A00000u; // 5.0
        lat.f13 = 0x40C00000u; // 6.0
        lat.f14 = 0x40E00000u; // 7.0
        lat.v0 = v0;
        lat.v1 = 0x99u;
        g_lim1GateLatch = lat;
    }

    void setPadMarkerInput2ForTest(uint32_t bank, uint32_t count)
    {
        std::lock_guard<std::mutex> lock(g_padMarker.mutex);
        g_lim1Input2Bank = bank;
        g_lim1Input2Count = count <= 24u ? count : 0u;
        ++g_lim1Input2Wrapped;
    }

    void setPadMarkerRdramForTest(const uint8_t *rdram)
    {
        std::lock_guard<std::mutex> lock(g_padMarker.mutex);
        g_lim1TestRdram = rdram;
    }

    void setPadMarkerSelForTest(uint32_t p, uint32_t sel, uint32_t ctl)
    {
        std::lock_guard<std::mutex> lock(g_padMarker.mutex);
        MarkerSelLatch lat{};
        lat.have = true;
        lat.tick = g_padMarker.testTick;
        lat.p = p;
        lat.sel = sel;
        lat.ctl = ctl;
        g_lim2SelLatch = lat;
    }

    void setPadMarkerAwardForTest(uint64_t n, uint64_t tick, uint32_t a1)
    {
        std::lock_guard<std::mutex> lock(g_padMarker.mutex);
        g_lim2AwardN = n;
        g_lim2AwardTick = tick;
        g_lim2AwardA1 = a1;
    }

    void setPadMarkerBoostForTest(uint64_t n, uint64_t tick, uint32_t amt)
    {
        std::lock_guard<std::mutex> lock(g_padMarker.mutex);
        g_lim2BoostN = n;
        g_lim2BoostTick = tick;
        g_lim2BoostAmt = amt;
    }

    void closePadMarkerForTest()
    {
        std::lock_guard<std::mutex> lock(g_padMarker.mutex);
        for (const MarkerPending &p : g_padMarker.pending)
        {
            padMarkerWriteLocked(p, true);
        }
        padMarkerResetLocked();
    }

    void clearPadMarkerForTest()
    {
        std::lock_guard<std::mutex> lock(g_padMarker.mutex);
        padMarkerResetLocked();
    }
} // namespace ps2_stubs
