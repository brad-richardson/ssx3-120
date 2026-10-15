// SPDX-License-Identifier: GPL-3.0-or-later
// OM1 Part 4: offline microVU core for the fork runtime (Mac dylib first).
//
// Provenance: the dispatch/install/resolve/trap-mirror code below is copied
// VERBATIM from the Part 3 bench (branch om1 @ e22c8dc,
// ps2xRuntime/tools/vu1bench/om1_engines.cpp), which served 548,966 jobs with
// 0 mismatch vs the JIT oracle. Bench-verbatim code keeps its om1_ names and
// comments so a diff stays mechanical. Everything NEW for the runtime is
// marked `// OM1 P4 (new vs bench):` and uses the olc_ prefix.
//
// Deltas vs the bench (complete list):
//  1. No jitdump oracle, no WX scan, no timing, no Om1Job I/O. Seed/export/
//     GIF-arming move to the bridge (OfflineBridge.cpp, MV2's shape).
//  2. olc_execute() = the bench Execute mirror's core (VPU_STAT early-out,
//     TPC <<=3, resume disarm, lookup+resolve, trap-arm, resume-stub call,
//     >>=3, flags clear). TPC arrives as an INDEX (bridge/seed set it, as
//     the bench seed did); the budget is the runtime's.
//  3. No hwIntcIrq: MV2's EMBED mode compiles the inline irq OUT of
//     Execute (armsx2.patch @ recMicroVU1::Execute) and the runtime takes
//     completion via the exported VPU_STAT D/T bits; the flags clear stays.
//  4. olc_reset_live_vu() runs at end of init AND on every MISS return: it
//     zeroes the VURegs pipeline subset the bench seed zeroed (fmac/ialu
//     pipes+positions, fdiv, efu, ebit/branch/pcs, flags, xgkick state) and
//     restores the microVU.prog lookup seizure (quick/lpState/cur/isSame/
//     cleared/curFrame/x86 sentinels). Rationale: the bench's miss path ran
//     the oracle (full re-seed + coherent prog evolution) before the next
//     offline job; the runtime's miss path runs the STATIC engine, which
//     touches neither. Restoring makes every post-miss job start from the
//     proven fresh-init state (bench job #1 ran from exactly this state).
//     Installed programs/managers/blocks/jumpCaches are NOT touched (owned,
//     process-lifetime); stale jumpCache entries self-invalidate because
//     their key (quick[pc/8].prog) was cleared.
//  5. Counters kept (served/fallback/miss/parks/jumpMisses/fallMisses) and
//     exposed via olc_stats() for the det-gate miss/restart report.
// Uppercase markers: OM1P4_CORE.
//
// ARMSX2 pin: 247fa6f09499f69192c52c0542305ed16119518b (+MV2 hunks, OM1 tree).

#include "OfflineCore.h"

#include <algorithm>
#include <atomic>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <map>
#include <setjmp.h>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include <dlfcn.h>
#if defined(__APPLE__)
#include <mach/mach.h>
#include <TargetConditionals.h>
#endif

// ---- ARMSX2 (bench's include set minus bench-only test-env/jit/oracle) ----
#include "Config.h"
#include "Gif_Unit.h"
#include "VU.h"
#include "VUmicro.h"
#include "VU1Trace.h"
#include "common/FPControl.h"
#include "common/AlignedMalloc.h"
#include "common/Darwin/DarwinMisc.h"
#include "Memory.h"
#include "arm64/microVU-arm64.h"
#include "arm64/microVU_Persist-arm64.h"

// The recorder TU (microVU-arm64.cpp, linked here) strongly defines
// om1::om1_no_codegen_flag=false. The offline engine sets it true at init,
// arming the abort guards at the three codegen choke points (mVUreset,
// mVUopenCodeCache, compile entry). Plain extern: this TU always links the
// provider, so the header's weak attribute is unnecessary here.
namespace om1
{
extern bool om1_no_codegen_flag;
}

// The header's `_mVUt extern ...` declarations are NOT extern-template
// declarations (wrong syntax position), so including the .inl bodies makes
// this TU implicitly instantiate _mVUt templates it odr-uses. Suppress that:
// the definitions live in microVU-arm64.cpp.
extern template void* mVUcompileJIT<0>(u32 startPC, uptr ptr);
extern template void* mVUcompileJIT<1>(u32 startPC, uptr ptr);
extern template void* mVUsearchProg<0>(u32 startPC, uptr pState);
extern template void* mVUsearchProg<1>(u32 startPC, uptr pState);

#ifndef PCSX2_RECOMPILER_TESTS
#error "offline core requires ENABLE_RECOMPILER_TEST_HOOKS=ON (gif hooks)"
#endif
// NOTE: no PS2X_MICROVU_EMBED requirement. The dylib links the PCSX2 archive
// as-is (bench-identical, thread-opening Reserve included); the offline path
// never calls Reset/Execute, so EMBED is behavior-neutral here.

// Behavior contracts the mirrors below depend on (all constexpr in
// pcsx2/arm64/microVU_Misc-arm64.h). If upstream flips one, this TU must not
// silently diverge.
static_assert(!doWholeProgCompare, "cmpProg mirror assumes range compare");
static_assert(doJumpCaching && !doJumpAsSameProgram, "jump_resolve mirror shape");
static_assert(sizeof(microRegInfo) == 96, "tables carry 96B pStates");
static_assert(!IsDevBuild, "mVUwaitMTVU replica omits the dev-only DevCon line");

// ---- symbols the recorded TEXT references ----------------------------------
// The .s files reference three TU-local (static) ARMSX2 symbols by their
// record-TU mangled names. They have no external linkage, so the consumer
// provides globals under those exact (asm-label) names:
//
//  _ZL7mVUglob      the clamp/const table (static constexpr: values identical
//                  in every TU, so a copy is exact)
//  _ZL7mVUEBitv     one fetch_or (body copied from microVU_Misc-arm64.inl:317)
//  _ZL11mVUwaitMTVUv WaitVU (body copied from microVU_Misc-arm64.inl:303;
//                  dev-only DevCon line omitted; static_assert above guards)

// Non-const: namespace-scope const implies internal linkage in C++, which would
// hide this from the recorded chunks (and clang confusingly re-underscores
// it). The chunks only read it.
alignas(32) mVU_Globals om1_mVUglob_storage asm("_ZL7mVUglob") = mVUglob;

void om1_mVUEBit_impl() asm("_ZL7mVUEBitv");
void om1_mVUEBit_impl()
{
    vu1Thread.mtvuInterrupts.fetch_or(VU_Thread::InterruptFlagVUEBit, std::memory_order_release);
}

void om1_mVUwaitMTVU_impl() asm("_ZL11mVUwaitMTVUv");
void om1_mVUwaitMTVU_impl()
{
    vu1Thread.WaitVU();
}

// ---- offline engine state --------------------------------------------------
// OM1 P4 (new vs bench): flat table headers from the private staging dir
// (-I$(PS2X_OM1_TABLES_DIR)); generated inputs, never committed.
#include "sp1_tables.h"
#include "mid_tables.h"
#include "full_tables.h"
#include "menus_tables.h"
#include "late_tables.h"
#include "sv1_tables.h"
// IB2/IB3 9-tag union (OC1 grav/rb/jx; same shape as the OM1 six).
#include "grav_tables.h"
#include "rb_tables.h"
#include "jx_tables.h"

namespace
{
struct Om1TableSet
{
    const char* tag = nullptr;
    // Normalized row views (pointers into the tag's const tables; no copy).
    struct BlockRow
    {
        uint64_t lo, hi;
        uint32_t startPC;
        uint8_t exact;
        const uint8_t* pState;
        const uint8_t* pStateEnd;
        uint8_t hasJC;
        void* code;
        uint32_t objIdx;
    };
    struct ImageRow
    {
        uint64_t lo, hi;
        const uint8_t* code;
        const uint16_t* ranges;
        uint32_t nranges;
    };
    struct SlotRow
    {
        const char* name;
        uint32_t block;
        uint32_t field;
    };
    std::vector<BlockRow> blocks;
    std::vector<ImageRow> images;
    std::vector<SlotRow> slots;
    void* resumeAddr = nullptr;
    void* exitAddr = nullptr;
    // Per-row installed block copies (parallel to blocks; filled at init).
    std::vector<microBlock*> installed;
};

std::vector<Om1TableSet> g_tags;
std::map<std::pair<uint64_t, uint64_t>, microProgram*> g_programs;
std::map<std::pair<microProgram*, uint32_t>, microBlockManager*> g_managers;
bool g_off_inited = false;
void* g_resumeAddr = nullptr;

// Counters (no locks: the runtime calls from one MTVU worker at a time).
uint64_t g_served = 0, g_fallback = 0, g_miss = 0, g_parks = 0;
uint64_t g_jumpMisses = 0, g_fallMisses = 0;
// OM2: budget-break resumes served offline (was: every one a FATAL park).
uint64_t g_resumes = 0;
// OM2 step 3 diagnostic: why each coverage fallback missed (1 = no program
// matches micro-mem, 2 = program but no block manager at the PC, 3 = manager
// but no matching pState), and the recorded pStates per manager so a pState
// miss can be diffed against its nearest recorded neighbour.
uint64_t g_fbWhy[4] = {};
std::map<const void*, std::vector<microRegInfo>> g_mgrStates;
static void om2_note_pstate_miss(uint32_t pc, const void* mgr, const microRegInfo& live);
uint64_t g_scanHits = 0;
static bool om2_scan_enabled()
{
    static const bool on = [] {
        const char* e = std::getenv("PS2X_OM1_SCAN");
        return e && e[0] == '1';
    }();
    return on;
}

// OM2: PS2X_OM1_RESUME=0 restores the OC2 behavior (a parked resume returns
// MISS, which the runtime's continue loop turns into a FATAL). Default on.
static bool om2_resume_enabled()
{
    static const bool on = [] {
        const char* e = std::getenv("PS2X_OM1_RESUME");
        return !(e && e[0] == '0');
    }();
    return on;
}

// OM2: UTC wall stamp (second resolution, the perf log's `wall=` format) for
// [om1] lines, so miss bursts can be placed against perf-log seconds.
static const char* om2_wall()
{
    static thread_local char buf[24];
    const time_t t = time(nullptr);
    struct tm tm;
    gmtime_r(&t, &tm);
    strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &tm);
    return buf;
}
// OC2 census: exact per-(kind,tpc,start_pc) miss counts. Key pack:
// kindIdx(8) << 32 | tpc(16) << 16 | start_pc(16).
std::map<uint64_t, uint64_t> g_missKeys;
uint64_t g_lastCensusServed = 0, g_lastCensusMiss = 0;
// OC2 deny flag: set by the denylist branch, consumed by the fallback site.
bool g_lastDeny = false;
uint64_t g_denied = 0;
// OC2: park denylist. A stub-level park (recorded cycle accounting explodes on
// rare jobs: I26 0x1460 at 4.96B stub cycles with zero breaks under the live
// JIT) cannot be resumed (recorded stubs lack live register state), so the
// runtime's continue-loop resume FATALs. Jobs matching the denylist take a
// fresh MISS instead (exact static restart, det-proven) and so never park.
// PS2X_OM1_DENY="SPC[:LO64][;...]": SPC = hex kick PC (exact match), LO64 =
// 16-hex contentHash.low64 of the resolved program ('*' or omitted = any).
// (Tried: stub budget UINT32_MAX — instant park on job #1. The budget seeds
// the stub's 32-bit cycle base (mVUlookupProg_VU1's `cycles` arg); MAX wraps
// its arithmetic. Any u32 budget still parks the bogus-billions jobs.)
struct Oc2DenyEnt
{
    uint32_t spc;
    uint64_t lo;
    bool anyProg;
};
static std::vector<Oc2DenyEnt>& oc2_deny_list()
{
    static std::vector<Oc2DenyEnt> v;
    static bool once = false;
    if (!once)
    {
        once = true;
        if (const char* e = std::getenv("PS2X_OM1_DENY"))
        {
            std::string s(e);
            size_t i = 0;
            while (i < s.size())
            {
                size_t j = s.find(';', i);
                if (j == std::string::npos)
                    j = s.size();
                std::string ent = s.substr(i, j - i);
                size_t c = ent.find(':');
                std::string spcS = (c == std::string::npos) ? ent : ent.substr(0, c);
                std::string loS = (c == std::string::npos) ? "*" : ent.substr(c + 1);
                Oc2DenyEnt d;
                d.spc = static_cast<uint32_t>(std::strtoul(spcS.c_str(), nullptr, 16));
                d.anyProg = (loS == "*" || loS.empty());
                d.lo = d.anyProg ? 0 : std::strtoull(loS.c_str(), nullptr, 16);
                if (!spcS.empty())
                    v.push_back(d);
                i = j + 1;
            }
        }
    }
    return v;
}

static bool oc2_denied(uint32_t pc, const microProgram* prog)
{
    const auto& v = oc2_deny_list();
    if (v.empty() || !prog)
        return false;
    for (const auto& d : v)
    {
        if (d.spc == (pc & 0x3ff8u) && (d.anyProg || d.lo == prog->contentHash.low64))
            return true;
    }
    return false;
}

// Trap landing for fallthrough/jump misses. Armed around each resume() call.
sigjmp_buf g_trapBuf;
volatile int g_trapArmed = 0;
volatile int g_trapKind = 0; // 1 = fallthrough, 2 = jump

// OBE1 test knob (see OfflineCore.h): every Nth om1_jump_resolve call misses.
uint64_t g_forcedJumpMisses = 0;
static bool obe1_force_jump_miss()
{
    static const uint64_t every = [] {
        const char* e = std::getenv("PS2X_OM1_FORCE_JUMP_MISS");
        return e ? std::strtoull(e, nullptr, 10) : 0ull;
    }();
    static uint64_t calls = 0;
    if (!every || ++calls % every != 0)
        return false;
    ++g_forcedJumpMisses;
    return true;
}

// mVUcmpProg replica (microVU-arm64.cpp:1559, doWholeProgCompare=false path):
// range-compare of the owned program against live micro-mem; on match sets
// cleared/cur/isSame exactly like the original.
bool om1_cmpProg(microVU& mVU, microProgram& prog)
{
    for (const microRange& range : *prog.ranges)
    {
        if (std::memcmp(reinterpret_cast<const u8*>(prog.data) + range.start,
                        reinterpret_cast<const u8*>(mVU.regs().Micro) + range.start,
                        static_cast<size_t>(range.end - range.start)) != 0)
            return false;
    }
    mVU.prog.cleared = 0;
    mVU.prog.cur = &prog;
    mVU.prog.isSame = -1;
    return true;
}

// mVUsearchProg resolve-half mirror (no deques/contentMap/hydrate/compile):
// creation-order range-compare, quick fill, block search. Returns the host
// entry or nullptr (caller falls back). `pc` arrives in the caller's units:
// masked (dispatch, like mVUexecute) or raw (jump, like mVUcompileJIT).
// OC2: `kickDeny` gates the park denylist: kicks (+MSCN resumes) may deny,
// jumps must not (a denied jump defeats its jumpCache: I26 showed 74k
// jump-misses where the unlisted union serves — the park is a kick property).
void* om1_slowResolve(uint32_t pc, microRegInfo* key, bool kickDeny)
{
    microVU& mVU = microVU1;
    microProgramQuick& quick = mVU.prog.quick[mVU.regs().start_pc / 8];
    unsigned skipped = 0; // OM2 scan prototype
    for (const auto& kv : g_programs)
    {
        microProgram* prog = kv.second;
        if (!om1_cmpProg(mVU, *prog))
            continue;
        // OC2: park denylist (before quick fill, so denied pairs are never
        // trusted by the fast path): fresh MISS -> exact static restart.
        // Kicks only (kickDeny): jumps resolve + cache normally.
        if (kickDeny && oc2_denied(pc, prog))
        {
            g_lastDeny = true;
            ++g_denied;
            return nullptr;
        }
        microBlockManager* block = prog->block[pc / 8];
        quick.block = block;
        quick.prog = prog;
        // OM2 step 3 prototype (PS2X_OM1_SCAN=1, default off): several owned
        // programs can range-match live micro-mem (each covers only the code
        // its own recording touched); keep scanning instead of giving up on
        // the first match that lacks a block (or pState) at this PC. Exact:
        // a block's code depends only on micro-mem inside its program's
        // ranges, which matched.
        if (!block)
        {
            if (om2_scan_enabled() && ++skipped)
                continue;
            ++g_fbWhy[2];
            return nullptr;
        }
        microBlock* pBlock = block->search(mVU, key);
        if (!pBlock)
        {
            if (om2_scan_enabled() && ++skipped)
                continue;
            ++g_fbWhy[3];
            om2_note_pstate_miss(pc, block, *key);
            return nullptr;
        }
        if (skipped)
            ++g_scanHits;
        return pBlock->hostEntry;
    }
    ++g_fbWhy[om2_scan_enabled() ? 2 : 1]; // scan: "matched but nothing served" counts as pc
    return nullptr;
}

#define OM1_CODE_SIZE 0x4000u

bool om1_build_tag(Om1TableSet& t, std::string& err)
{
    microVU& mVU = microVU1;
    // Pass 1: merge images by content hash (stable order: table order).
    std::vector<microProgram*> imgProg;
    imgProg.reserve(t.images.size());
    for (const auto& im : t.images)
    {
        auto key = std::make_pair(im.lo, im.hi);
        auto it = g_programs.find(key);
        if (it != g_programs.end())
        {
            imgProg.push_back(it->second);
            continue;
        }
        microProgram* prog =
            static_cast<microProgram*>(_aligned_malloc(sizeof(microProgram), 64));
        if (!prog)
        {
            err = "aligned_malloc failed for microProgram";
            return false;
        }
        std::memset(prog, 0, sizeof(microProgram));
        prog->idx = mVU.prog.total++;
        prog->ranges = new std::deque<microRange>();
        for (uint32_t r = 0; r < im.nranges; ++r)
        {
            const s32 rs = static_cast<s32>(im.ranges[r * 2]);
            const s32 re = static_cast<s32>(im.ranges[r * 2 + 1]);
            if (rs < 0 || re < rs || re > static_cast<s32>(OM1_CODE_SIZE))
            {
                err = std::string("bad range in tag ") + t.tag;
                return false;
            }
            prog->ranges->emplace_back(microRange{rs, re});
        }
        prog->startPC = 0; // never read by the resolve/dispatch paths we run
        prog->contentHash.low64 = im.lo;
        prog->contentHash.high64 = im.hi;
        prog->contentHashValid = true;
        prog->writeGenAtAnchor = mVU.microMemWriteGen;
        prog->refcount = 0;
        prog->observed.clear();
        prog->persist = nullptr;
        std::memcpy(prog->data, im.code, OM1_CODE_SIZE);
        g_programs[key] = prog;
        imgProg.push_back(prog);
    }
    // Pass 2: install blocks via the REAL microBlockManager::add (dedups
    // first-wins exactly like the JIT: repeat add()s return the first copy).
    t.installed.assign(t.blocks.size(), nullptr);
    const uint32_t jcCount = mVU.progSize / 2;
    for (size_t i = 0; i < t.blocks.size(); ++i)
    {
        const auto& b = t.blocks[i];
        if (b.objIdx >= imgProg.size())
        {
            err = std::string("block objIdx out of range in tag ") + t.tag;
            return false;
        }
        microProgram* prog = imgProg[b.objIdx];
        auto mkey = std::make_pair(prog, b.startPC);
        microBlockManager*& mgr = g_managers[mkey];
        if (!mgr)
            mgr = new microBlockManager();
        const uint32_t slot = b.startPC / 8;
        if (slot >= static_cast<uint32_t>(mProgSize / 2))
        {
            err = std::string("block startPC out of range in tag ") + t.tag;
            return false;
        }
        if (!prog->block[slot])
            prog->block[slot] = mgr;
        else if (prog->block[slot] != mgr)
        {
            err = std::string("manager collision in tag ") + t.tag;
            return false;
        }
        microBlock tmpl;
        std::memset(&tmpl, 0, sizeof(tmpl));
        std::memcpy(&tmpl.pState, b.pState, sizeof(microRegInfo));
        std::memcpy(&tmpl.pStateEnd, b.pStateEnd, sizeof(microRegInfo));
        tmpl.x86ptrStart = static_cast<u8*>(b.code);
        tmpl.hostEntry = b.code;
        tmpl.jumpCache = b.hasJC ? new microJumpCache[jcCount] : nullptr;
        g_mgrStates[mgr].push_back(tmpl.pState); // OM2 step 3 diagnostic
        microBlock* added = mgr->add(mVU, &tmpl);
        if (!added)
        {
            err = "manager add returned null";
            return false;
        }
        t.installed[i] = added;
    }
    // Pass 3: fill init slots (consumer-owned microBlock field pointers).
    for (const auto& s : t.slots)
    {
        if (s.block >= t.installed.size() || !t.installed[s.block])
        {
            err = std::string("initslot block out of range in tag ") + t.tag;
            return false;
        }
        if (s.field + sizeof(void*) > sizeof(microBlock))
        {
            err = std::string("initslot field out of range in tag ") + t.tag;
            return false;
        }
        // macOS dlsym takes the name WITHOUT the Mach-O underscore.
        const char* lookup = s.name;
        if (lookup[0] == '_')
            ++lookup;
        void* addr = dlsym(RTLD_DEFAULT, lookup);
        if (!addr)
        {
            err = std::string("initslot symbol not found: ") + s.name + " (" +
                  (dlerror() ? dlerror() : "?") + ")";
            return false;
        }
        *static_cast<void**>(addr) =
            reinterpret_cast<u8*>(t.installed[s.block]) + s.field;
    }
    return true;
}

// No-JIT init (P3.1): the data-only subset of
// RecompilerTestEnvironment::Initialize that the offline engine needs.
// Skipped deliberately (each verified unused by the offline run path):
//   - executable code buffers (iPSX2_FORCE_EE_INTERP=1 => s_code_memory=null)
//   - EE/IOP Reserve+Reset (psxRec/recCpu/intCpu/psxInt) and the VU0 rec
//   - every Reset() (dispatcher emission); microVU1 dispatch fields are
//     seized by the caller exactly as before
//   - parking lots, IOP/EE counters, psxCpu/Cpu/CpuVU wiring (no EE/IOP
//     execution; hwIntcIrq short-circuits on the zeroed INTC_MASK)
//   - cpuinfo (emitter ISA checks only; nothing emits here)
// Kept: FPU default, data mappings + zeroing, manual persist recording,
// CpuMicroVU1.Reserve (mVUinit state + the parked MTVU thread), XgKickHack
// env mirror. All compiled code (startFunct/Resume, exitFunct, model and
// waitMTVU stubs) comes from the RECORDED chunks.
bool om1_nojit_init(std::string& err)
{
    FPControlRegister::SetCurrent(FPControlRegister::GetDefault());
    DarwinMisc::iPSX2_FORCE_EE_INTERP = 1;
    if (!SysMemory::Allocate())
    {
        err = "SysMemory::Allocate failed (data mappings)";
        return false;
    }
    mVUPersist::SetTestManualRecording(true);
    CpuMicroVU1.Reserve();
    SysMemory::Reset();
    if (const char* xgkick = std::getenv("PCSX2_VU_XGKICKHACK"))
        EmuConfig.Gamefixes.XgKickHack = std::atoi(xgkick) != 0;
    return true;
}

// ---- config: verbatim copy of the adapter's MV2 setup ----------------------
void om1_config()
{
    auto fpcr = FPControlRegister::GetDefault().DisableExceptions().SetDenormalsAreZero(true).SetFlushToZero(true);
    fpcr.SetRoundMode(FPRoundMode::ChopZero);
    EmuConfig.Cpu.VU1FPCR = fpcr;
    FPControlRegister::SetCurrent(fpcr);
    auto& rc = EmuConfig.Cpu.Recompiler;
    rc.vu1Overflow = true;
    rc.vu1ExtraOverflow = false;
    rc.vu1SignOverflow = false;
    rc.vu1ExactMode = false;
    EmuConfig.Speedhacks.vuThread = false;
    EmuConfig.Speedhacks.vu1Instant = false;
    EmuConfig.Speedhacks.vuFlagHack = false;
    EmuConfig.Gamefixes.XgKickHack = false;
}

// Normalize one tag's const tables into row views (no copy of the bytes).
template <typename B, typename I, typename S>
void om1_normalize_tag(Om1TableSet& t, const char* tag, const B* blocks, uint32_t nblocks,
                       const I* images, uint32_t nimages, const S* slots, uint32_t nslots,
                       void* (*resumeFn)(), void* (*exitFn)())
{
    t.tag = tag;
    t.blocks.reserve(nblocks);
    for (uint32_t i = 0; i < nblocks; ++i)
    {
        Om1TableSet::BlockRow r;
        r.lo = blocks[i].lo;
        r.hi = blocks[i].hi;
        r.startPC = blocks[i].startPC;
        r.exact = blocks[i].exact;
        r.pState = blocks[i].pState;
        r.pStateEnd = blocks[i].pStateEnd;
        r.hasJC = blocks[i].hasJC;
        r.code = blocks[i].code;
        r.objIdx = blocks[i].objIdx;
        t.blocks.push_back(r);
    }
    t.images.reserve(nimages);
    for (uint32_t i = 0; i < nimages; ++i)
    {
        Om1TableSet::ImageRow r;
        r.lo = images[i].lo;
        r.hi = images[i].hi;
        r.code = images[i].code;
        r.ranges = images[i].ranges;
        r.nranges = images[i].nranges;
        t.images.push_back(r);
    }
    t.slots.reserve(nslots);
    for (uint32_t i = 0; i < nslots; ++i)
    {
        Om1TableSet::SlotRow r;
        r.name = slots[i].name;
        r.block = slots[i].block;
        r.field = slots[i].field;
        t.slots.push_back(r);
    }
    t.resumeAddr = resumeFn();
    t.exitAddr = exitFn();
}

void om1_add_all_tags()
{
    {
        Om1TableSet t;
        om1_normalize_tag(t, "sp1", om1SP1_blocks, om1SP1_nblocks, om1SP1_images, om1SP1_nimages,
                          om1SP1_initslots, om1SP1_ninitslots, om1SP1_stub_resume, om1SP1_stub_exit);
        g_tags.push_back(std::move(t));
    }
    {
        Om1TableSet t;
        om1_normalize_tag(t, "mid", om1MID_blocks, om1MID_nblocks, om1MID_images, om1MID_nimages,
                          om1MID_initslots, om1MID_ninitslots, om1MID_stub_resume, om1MID_stub_exit);
        g_tags.push_back(std::move(t));
    }
    {
        Om1TableSet t;
        om1_normalize_tag(t, "full", om1FULL_blocks, om1FULL_nblocks, om1FULL_images,
                          om1FULL_nimages, om1FULL_initslots, om1FULL_ninitslots, om1FULL_stub_resume,
                          om1FULL_stub_exit);
        g_tags.push_back(std::move(t));
    }
    {
        Om1TableSet t;
        om1_normalize_tag(t, "menus", om1MENUS_blocks, om1MENUS_nblocks, om1MENUS_images,
                          om1MENUS_nimages, om1MENUS_initslots, om1MENUS_ninitslots,
                          om1MENUS_stub_resume, om1MENUS_stub_exit);
        g_tags.push_back(std::move(t));
    }
    {
        Om1TableSet t;
        om1_normalize_tag(t, "late", om1LATE_blocks, om1LATE_nblocks, om1LATE_images,
                          om1LATE_nimages, om1LATE_initslots, om1LATE_ninitslots, om1LATE_stub_resume,
                          om1LATE_stub_exit);
        g_tags.push_back(std::move(t));
    }
    {
        Om1TableSet t;
        om1_normalize_tag(t, "sv1", om1SV1_blocks, om1SV1_nblocks, om1SV1_images,
                          om1SV1_nimages, om1SV1_initslots, om1SV1_ninitslots, om1SV1_stub_resume,
                          om1SV1_stub_exit);
        g_tags.push_back(std::move(t));
    }
    {
        Om1TableSet t;
        om1_normalize_tag(t, "grav", om1GRAV_blocks, om1GRAV_nblocks, om1GRAV_images,
                          om1GRAV_nimages, om1GRAV_initslots, om1GRAV_ninitslots,
                          om1GRAV_stub_resume, om1GRAV_stub_exit);
        g_tags.push_back(std::move(t));
    }
    {
        Om1TableSet t;
        om1_normalize_tag(t, "rb", om1RB_blocks, om1RB_nblocks, om1RB_images,
                          om1RB_nimages, om1RB_initslots, om1RB_ninitslots,
                          om1RB_stub_resume, om1RB_stub_exit);
        g_tags.push_back(std::move(t));
    }
    {
        Om1TableSet t;
        om1_normalize_tag(t, "jx", om1JX_blocks, om1JX_nblocks, om1JX_images,
                          om1JX_nimages, om1JX_initslots, om1JX_ninitslots,
                          om1JX_stub_resume, om1JX_stub_exit);
        g_tags.push_back(std::move(t));
    }
}

// OM1 P4 (new vs bench): miss/periodic notes. ssx3_boot.py SIGTERMs the
// runner at the target tick, so shutdown() never runs; misses print exactly
// (kind + TPC + cumulative counters) and served prints every 1024 runs, so
// the boot log always holds the counts (served within 1024 of final).
// OM1 P5: notes go through olc_log (stderr + om1.log file on iOS).
FILE* g_log = nullptr;

void olc_log_open()
{
    if (g_log)
        return;
    std::string dir;
    if (const char* e = std::getenv("PS2X_OM1_LOGDIR"))
        dir = e;
#if defined(__APPLE__) && TARGET_OS_IPHONE
    else if (const char* h = std::getenv("HOME"))
        dir = std::string(h) + "/Documents";
#endif
    if (dir.empty())
        return;
    const std::string path = dir + "/om1.log";
    g_log = std::fopen(path.c_str(), "w");
    if (g_log)
        olc_log("[om1] log %s footprint=%llu\n", path.c_str(),
                (unsigned long long)olc_footprint());
}

static unsigned oc2_kind_idx(const char* kind)
{
    if (!std::strcmp(kind, "park"))
        return 1;
    if (!std::strcmp(kind, "fallback"))
        return 2;
    if (!std::strcmp(kind, "fall"))
        return 3;
    if (!std::strcmp(kind, "jump"))
        return 4;
    if (!std::strcmp(kind, "deny"))
        return 5;
    return 0; // "trap?" or future kinds
}

static const char* oc2_kind_name(unsigned idx)
{
    switch (idx)
    {
        case 1: return "park";
        case 2: return "fallback";
        case 3: return "fall";
        case 4: return "jump";
        case 5: return "deny";
        default: return "trap?";
    }
}

// OC2: full per-key census dump (one short line per key; fits olc_log's 512 B).
static void olc_note_census()
{
    for (const auto& kv : g_missKeys)
    {
        const unsigned kind = (unsigned)((kv.first >> 32) & 0xffu);
        const unsigned tpc = (unsigned)((kv.first >> 16) & 0xffffu);
        const unsigned spc = (unsigned)(kv.first & 0xffffu);
        olc_log("[om1] census kind=%s tpc=%04x start_pc=%04x count=%llu served=%llu miss=%llu "
                "wall=%s\n",
                oc2_kind_name(kind), tpc, spc, (unsigned long long)kv.second,
                (unsigned long long)g_served, (unsigned long long)g_miss, om2_wall());
    }
    olc_log("[om1] fbwhy prog=%llu pc=%llu pstate=%llu resumes=%llu scanhits=%llu wall=%s\n",
            (unsigned long long)g_fbWhy[1], (unsigned long long)g_fbWhy[2],
            (unsigned long long)g_fbWhy[3], (unsigned long long)g_resumes,
            (unsigned long long)g_scanHits, om2_wall());
    g_lastCensusServed = g_served;
    g_lastCensusMiss = g_miss;
}

// OM2 step 3: for the first 24 pState misses, name the bytes where the live
// key differs from the nearest recorded pState of the same manager (fields:
// 0 needExactMatch 1 flagInfo 2 q 3 p 4 xgkick 5 viBackUp 6 blockType 7 r,
// 8-11 xgkickcycles, 12 unused, 13 vi15v, 14-15 vi15, 16-31 VI[], 32-95 VF[]).
static void om2_note_pstate_miss(uint32_t pc, const void* mgr, const microRegInfo& live)
{
    static unsigned noted = 0;
    if (noted >= 24)
        return;
    ++noted;
    const auto it = g_mgrStates.find(mgr);
    const size_t n = it == g_mgrStates.end() ? 0 : it->second.size();
    int best = 1 << 30;
    const microRegInfo* bestS = nullptr;
    for (size_t i = 0; i < n; ++i)
    {
        const u8* a = reinterpret_cast<const u8*>(&live);
        const u8* b = reinterpret_cast<const u8*>(&it->second[i]);
        int d = 0;
        for (int k = 0; k < 96; ++k)
            d += a[k] != b[k];
        if (d < best)
        {
            best = d;
            bestS = &it->second[i];
        }
    }
    char diff[320];
    int off = 0;
    diff[0] = 0;
    if (bestS)
    {
        const u8* a = reinterpret_cast<const u8*>(&live);
        const u8* b = reinterpret_cast<const u8*>(bestS);
        for (int k = 0; k < 96 && off < (int)sizeof(diff) - 16; ++k)
            if (a[k] != b[k])
                off += std::snprintf(diff + off, sizeof(diff) - off, " %d:%02x/%02x", k, a[k], b[k]);
    }
    olc_log("[om1] pstate-miss pc=%04x start_pc=%04x recorded=%zu nearest_diff=%d live_quick=%016llx "
            "exact=%u bytes(live/rec):%s\n",
            pc, microVU1.regs().start_pc & 0xffffu, n, bestS ? best : -1,
            (unsigned long long)live.quick64[0], (unsigned)live.needExactMatch, diff);
}

// OC2 throttle of OM1 P4's per-miss note: exact per-key counts are kept in
// g_missKeys; stderr gets the first occurrence per (kind,tpc,start_pc) — with
// the program start_pc appended — plus a full census every 4096 misses (the
// served-side cadence below covers the rare-miss regime). Prefix-identical to
// OM1's line so existing parsers still match.
void olc_note_miss(const char* kind, uint32_t tpc)
{
    const uint32_t spc = microVU1.regs().start_pc & 0xffffu;
    const uint64_t key =
        (uint64_t)oc2_kind_idx(kind) << 32 | (uint64_t)(tpc & 0xffffu) << 16 | spc;
    const uint64_t n = ++g_missKeys[key];
    if (n == 1)
    {
        olc_log("[om1] miss kind=%s tpc=%04x served=%llu fallback=%llu miss=%llu parks=%llu "
                "jump=%llu fall=%llu fp=%llu start_pc=%04x wall=%s\n",
                kind, tpc, (unsigned long long)g_served, (unsigned long long)g_fallback,
                (unsigned long long)g_miss, (unsigned long long)g_parks,
                (unsigned long long)g_jumpMisses, (unsigned long long)g_fallMisses,
                (unsigned long long)olc_footprint(), spc, om2_wall());
    }
    if ((g_miss & 4095u) == 0)
        olc_note_census();
}

// OM1 P4 (new vs bench): post-miss / first-run canonical reset. Zeroes the
// VURegs pipeline subset the bench seed zeroed every job, and restores the
// microVU.prog lookup seizure to its init values (delta 4 in the header).
// Does NOT touch Micro/Mem/regs/TPC/cycle (the bridge seeds those per run),
// start_pc (resume continuity), or the installed programs (owned).
void olc_reset_live_vu()
{
    VURegs& vu = vuRegs[1];
    vu.ebit = vu.branch = vu.branchpc = vu.delaybranchpc = 0;
    vu.takedelaybranch = false;
    vu.flags = 0;
    vu.fmacreadpos = vu.fmacwritepos = vu.fmaccount = 0;
    vu.ialureadpos = vu.ialuwritepos = vu.ialucount = 0;
    std::memset(&vu.fdiv, 0, sizeof(vu.fdiv));
    std::memset(&vu.efu, 0, sizeof(vu.efu));
    std::memset(&vu.fmac, 0, sizeof(vu.fmac));
    std::memset(&vu.ialu, 0, sizeof(vu.ialu));
    vu.xgkickaddr = vu.xgkickdiff = vu.xgkicksizeremaining = 0;
    vu.xgkicklastcycle = 0;
    vu.xgkickcyclecount = vu.xgkickenable = vu.xgkickendpacket = 0;
    microVU& mVU = microVU1;
    std::memset(&mVU.prog.quick, 0, sizeof(mVU.prog.quick));
    std::memset(&mVU.prog.lpState, 0, sizeof(mVU.prog.lpState));
    mVU.prog.cur = nullptr;
    // total untouched: installed programs keep their idx assignments.
    mVU.prog.isSame = 0;
    mVU.prog.cleared = 0;
    mVU.prog.curFrame = 0;
    mVU.prog.x86start = mVU.prog.x86ptr = reinterpret_cast<u8*>(1);
    mVU.prog.x86end = reinterpret_cast<u8*>(static_cast<uptr>(-1));
}
} // namespace

extern "C"
{
// Fallthrough-trap landing: a recorded chunk fell off its end (no next
// chunk). Longjmps back to the run loop, which reports a miss.
void om1_fallthrough_miss()
{
    if (!g_trapArmed)
    {
        std::fprintf(stderr, "[om1] fatal: fallthrough trap with no armed run\n");
        std::abort();
    }
    g_trapKind = 1;
    siglongjmp(g_trapBuf, 1);
}

// mVUcompileJIT substitute (same (startPC, ptr) convention; the recorder
// rewrote only the BL target). Mirrors the doJumpAsSameProgram=false +
// doJumpCaching=true path of microVU_Branch-arm64.inl:560, with
// om1_slowResolve in place of mVUsearchProg and a trap in place of the
// compile on miss.
void* om1_jump_resolve(uint32_t startPC, uptr ptr)
{
    microVU& mVU = microVU1;
    mVU.regs().start_pc = startPC;
    microBlock* pBlock = reinterpret_cast<microBlock*>(ptr);
    microJumpCache& jc = pBlock->jumpCache[startPC / 8];
    const bool forced = obe1_force_jump_miss(); // OBE1 test knob, off by default
    if (!forced && jc.prog && jc.prog == mVU.prog.quick[startPC / 8].prog)
        return jc.hostEntry;
    void* v = forced ? nullptr : om1_slowResolve(startPC, &pBlock->pStateEnd, false); // OC2: jumps never deny
    if (!v)
    {
        if (!g_trapArmed)
        {
            std::fprintf(stderr, "[om1] fatal: jump miss with no armed run\n");
            std::abort();
        }
        g_trapKind = 2;
        siglongjmp(g_trapBuf, 1);
    }
    jc.prog = mVU.prog.quick[startPC / 8].prog;
    jc.x86ptrStart = v;
    jc.hostEntry = v;
    return v;
}
} // extern "C"

// OM1 P4 (new vs bench): runtime entry points.
uint64_t olc_footprint()
{
#if defined(__APPLE__)
    task_vm_info_data_t info;
    mach_msg_type_number_t n = TASK_VM_INFO_COUNT;
    if (task_info(mach_task_self(), TASK_VM_INFO, reinterpret_cast<task_info_t>(&info), &n) ==
        KERN_SUCCESS)
        return info.phys_footprint;
#endif
    return 0;
}

void olc_log(const char* fmt, ...)
{
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    const int len = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (len <= 0)
        return;
    std::fwrite(buf, 1, static_cast<size_t>(len), stderr);
    if (g_log) {
        std::fwrite(buf, 1, static_cast<size_t>(len), g_log);
        std::fflush(g_log);
    }
}

bool olc_init(std::string& err)
{
    // OC2: idempotent re-init for savestate load (SS4 resetForLoad does a
    // shutdown/configure cycle; dyld keeps the same image so the flag
    // survives). Tables are process-lifetime (already built); live state is
    // pristine pre-emulation (loads happen before the run loop), so a reset
    // of the live mirrors suffices — no run has executed between inits.
    if (g_off_inited)
    {
        olc_reset_live_vu();
        olc_log("[om1] offline re-init for state load (tables kept)\n");
        return true;
    }
    // No-JIT init: arm the codegen-abort guards FIRST, then bring up
    // the data-only subset. Any compile attempt aborts loud from here on.
    olc_log_open();
    om1::om1_no_codegen_flag = true;
    if (!om1_nojit_init(err))
        return false;
    om1_config();
    if (vu1_trace::g_enabled.load(std::memory_order_relaxed))
    {
        err = "vu1 tracing enabled; offline mirror omits trace calls";
        return false;
    }
    // Seize dispatch state. Everything else in microVU1 (index, sizes,
    // flags) is whatever the no-JIT Reserve() left; there are no live
    // dispatchers (all entry stubs come from the recorded chunks).
    // prog.prog per-PC deques stay null (never consulted: mVUclear and the
    // lookup fast path touch only quick[]/lpState).
    microVU& mVU = microVU1;
    std::memset(&mVU.prog.quick, 0, sizeof(mVU.prog.quick));
    std::memset(&mVU.prog.lpState, 0, sizeof(mVU.prog.lpState));
    mVU.prog.cur = nullptr;
    mVU.prog.total = 0;
    mVU.prog.isSame = 0; // always set by resolve before any consumer reads it
    mVU.prog.cleared = 0;
    mVU.prog.curFrame = 0;
    // In-range triple so mVUcleanUp's reset check never fires. mVUexecute's
    // bounds check never runs (we never call mVUexecute).
    mVU.prog.x86start = mVU.prog.x86ptr = reinterpret_cast<u8*>(1);
    mVU.prog.x86end = reinterpret_cast<u8*>(static_cast<uptr>(-1));
    om1_add_all_tags();
    for (auto& t : g_tags)
    {
        if (!om1_build_tag(t, err))
            return false;
        olc_log("[om1] tag %s: %u blocks %u images %u slots resume=%p exit=%p\n",
                     t.tag, (unsigned)t.blocks.size(), (unsigned)t.images.size(),
                     (unsigned)t.slots.size(), t.resumeAddr, t.exitAddr);
    }
    g_resumeAddr = g_tags[0].resumeAddr;
    if (!g_resumeAddr)
    {
        err = "first tag has no resume stub";
        return false;
    }
    // First-run canonical pipelines (delta 4): the bridge seeds regs-only,
    // so start the pipelines where the bench seed started every job.
    olc_reset_live_vu();
    olc_log("[om1] offline ready: %u programs %u managers fp=%llu\n",
            (unsigned)g_programs.size(), (unsigned)g_managers.size(),
            (unsigned long long)olc_footprint());
    g_off_inited = true;
    return true;
}

void olc_shutdown()
{
    // OC2: no-op by design (was: CpuMicroVU1.Shutdown + SysMemory::Release).
    // The SS4 savestate-load reset does shutdown/configure in-process; dyld
    // keeps the same image, so releasing the VU mappings here unmaps memory
    // the re-init still references (sleg2 SIGSEGV in ps2x_microvu_run+884).
    // Leaking is safe: process-exit needs no teardown, and if dyld ever does
    // unload, the fresh image takes the FULL init path (flag false). Owned
    // programs/managers/blocks already leaked by design (process-lifetime).
}

// Mirror of recMicroVU1::Execute (microVU-arm64.cpp:2213, EMBED shape): the
// VPU_STAT early-out, TPC shift and resume disarm are identical to the bench;
// the startFunct call is substituted (REAL fast-path lookup + resolve
// mirror, invoked through the RECORDED resume stub, which is Execute's own
// resume-path invocation shape); the flags/irq postamble keeps the flags
// clear but drops the inline irq (EMBED compiles it out; completion flows
// via the exported VPU_STAT D/T bits). TPC arrives as an INDEX.
// IV1 prototype (PS2X_OM1_LEAN=1, default off): see OfflineCore.h.
int olc_lean_level()
{
    static const int level = [] {
        const char* e = std::getenv("PS2X_OM1_LEAN");
        return (e && (e[0] == '1' || e[0] == '2')) ? e[0] - '0' : 0;
    }();
    return level;
}
bool olc_lean() { return olc_lean_level() != 0; }
static bool g_lastMissTrap = false;
bool olc_last_miss_was_trap() { return g_lastMissTrap; }

// OBE1: entry certification for the LEAN=1 backup. A run can only end in a mid-run MISS (with VU1.Mem already
// written) through a siglongjmp from om1_jump_resolve or om1_fallthrough_miss. The recorded code is immutable, so
// whether those are reachable from an entry is a fixed property of the entry address. The certifier walks the
// LINKED code from the entry (every reachable instruction, both arms of every conditional branch, into stubs and
// across chunks) and reports "can trap" when it reaches:
//   - a direct B/BL to either trap function;
//   - a BLR whose target it can't resolve from the recorder's `adrp xN; ldr xN,[xN,#off]; nop; nop; blr xN` pool
//     call, or that resolves to a trap function (the jump resolver is reached this way);
//   - any other register branch (BR, the jump-cache hit path; linker branch islands; anything else);
//   - more than kCertMaxWords instructions.
// BLR calls to other C helpers (XGKICK, clearlpState, the EFU/FPU models, waitMTVU, EBit) return normally: only the
// two trap functions longjmp. RET ends a path (the run returns to the resume stub's caller). Anything not recognised
// is conservative: the entry keeps its backup. Results are cached per entry address for the process lifetime.
bool olc_cert_enabled()
{
    static const bool on = [] {
        const char* e = std::getenv("PS2X_OM1_CERT");
        return e && e[0] == '1';
    }();
    return on;
}
static std::unordered_map<const void*, bool> g_certCanTrap; // entry -> can trap
static uint64_t g_backupsTaken = 0, g_backupsElided = 0, g_backupBytes = 0, g_certScans = 0, g_certWords = 0;
static constexpr size_t kCertMaxWords = 1u << 20;

static inline int64_t obe1_sx(uint64_t v, unsigned bits)
{
    return static_cast<int64_t>(v << (64 - bits)) >> (64 - bits);
}

// Target of the pool call ending at `blr` (or nullptr when the pattern isn't the recorder's).
static const void* obe1_pool_call_target(const uint32_t* blr)
{
    const unsigned rn = (*blr >> 5) & 31u;
    for (int back = 1; back <= 4; ++back)
    {
        const uint32_t w = blr[-back];
        if (w == 0xd503201fu) // nop
            continue;
        if ((w & 0xffc00000u) != 0xf9400000u || (w & 31u) != rn) // ldr xRt, [xRn, #imm12*8]
            return nullptr;
        const unsigned base = (w >> 5) & 31u;
        const uint64_t off = static_cast<uint64_t>((w >> 10) & 0xfffu) * 8u;
        const uint32_t* ap = blr - back - 1;
        const uint32_t a = *ap;
        if ((a & 0x9f000000u) != 0x90000000u || (a & 31u) != base) // adrp xBase, page
            return nullptr;
        const uint64_t imm = (static_cast<uint64_t>((a >> 5) & 0x7ffffu) << 2) | ((a >> 29) & 3u);
        const uint64_t page = (reinterpret_cast<uint64_t>(ap) & ~0xfffull) +
                              static_cast<uint64_t>(obe1_sx(imm, 21) * 4096);
        return *reinterpret_cast<void* const*>(page + off);
    }
    return nullptr;
}

static bool obe1_scan_can_trap(const void* entry)
{
    const void* const trapFall = reinterpret_cast<const void*>(&om1_fallthrough_miss);
    const void* const trapJump = reinterpret_cast<const void*>(&om1_jump_resolve);
    std::unordered_set<const uint32_t*> seen;
    std::vector<const uint32_t*> stack;
    auto push = [&](const uint32_t* p) {
        if (seen.insert(p).second)
            stack.push_back(p);
    };
    push(static_cast<const uint32_t*>(entry));
    ++g_certScans;
    while (!stack.empty())
    {
        if (seen.size() > kCertMaxWords)
            return true;
        const uint32_t* pc = stack.back();
        stack.pop_back();
        ++g_certWords;
        // Another entry already proven trap-free: its whole closure is too, so don't re-walk it.
        if (pc != entry)
        {
            const auto it = g_certCanTrap.find(pc);
            if (it != g_certCanTrap.end())
            {
                if (it->second)
                    return true;
                continue;
            }
        }
        const uint32_t w = *pc;
        if ((w & 0x7c000000u) == 0x14000000u) // B / BL imm26
        {
            const uint32_t* t = pc + obe1_sx(w & 0x3ffffffu, 26);
            if (t == trapFall || t == trapJump)
                return true;
            push(t);
            if (w & 0x80000000u) // BL returns
                push(pc + 1);
            continue;
        }
        if ((w & 0xff000010u) == 0x54000000u || (w & 0x7e000000u) == 0x34000000u) // B.cond, CBZ/CBNZ imm19
        {
            push(pc + obe1_sx((w >> 5) & 0x7ffffu, 19));
            push(pc + 1);
            continue;
        }
        if ((w & 0x7e000000u) == 0x36000000u) // TBZ/TBNZ imm14
        {
            push(pc + obe1_sx((w >> 5) & 0x3fffu, 14));
            push(pc + 1);
            continue;
        }
        if ((w & 0xfe000000u) == 0xd6000000u) // unconditional branch (register)
        {
            if ((w & 0xfffffc1fu) == 0xd65f0000u) // RET
                continue;
            if ((w & 0xfffffc1fu) == 0xd63f0000u) // BLR
            {
                const void* t = obe1_pool_call_target(pc);
                if (!t || t == trapFall || t == trapJump)
                    return true;
                push(pc + 1);
                continue;
            }
            return true; // BR and everything else in the class
        }
        push(pc + 1);
    }
    return false;
}

static bool obe1_entry_can_trap(const void* entry)
{
    const auto it = g_certCanTrap.find(entry);
    if (it != g_certCanTrap.end())
        return it->second;
    const bool r = obe1_scan_can_trap(entry);
    g_certCanTrap.emplace(entry, r);
    return r;
}

void olc_backup_stats(uint64_t& taken, uint64_t& elided, uint64_t& bytes, uint64_t& scans, uint64_t& words,
                      uint64_t& forced)
{
    taken = g_backupsTaken;
    elided = g_backupsElided;
    bytes = g_backupBytes;
    scans = g_certScans;
    words = g_certWords;
    forced = g_forcedJumpMisses;
}

OlcDisposition olc_execute(uint32_t budget, OlcBackup* backup)
{
    if (!THREAD_VU1)
    {
        if (!(VU0.VI[REG_VPU_STAT].UL & 0x100))
        {
            ++g_served;
            return OLC_SERVED;
        }
    }
    VU1.VI[REG_TPC].UL <<= 3;
    void* const resume = std::exchange(microVU1.resumeEntry, nullptr);
    const bool resumeRun = resume && !mVUPersist::IsRecordingEnabled();
    if (resumeRun && !om2_resume_enabled())
    {
        // OC2 behavior (PS2X_OM1_RESUME=0): refuse the parked resume.
        ++g_parks;
        ++g_miss;
        VU1.VI[REG_TPC].UL >>= 3;
        olc_note_miss("park", VU1.VI[REG_TPC].UL << 3);
        olc_reset_live_vu();
        return OLC_MISS;
    }
    // OM2: a cycle-budget break parked a resume. Continue it offline exactly
    // as recMicroVU1::Execute's VE-07 fast path does: enter the RECORDED
    // resume stub (startFunctResume) with the parked hostEntry. That is the
    // same stub and call shape every fresh offline job already uses (only
    // the entry differs), and the break's exit stub (cycleBreak) left all
    // live state in VURegs/microVU (lpState = the block's pState, flags,
    // Q/P, TPC); the bridge skips its re-seed for this run so nothing
    // overwrites it. The old reading ("needs the live stub's register
    // state") predates the check that the JIT resumes the same way (TK15
    // JIT boots: budget breaks + resumes with cumulative cycles=2.6e8).
    g_lastMissTrap = false;
    void* entry = nullptr;
    if (resumeRun)
    {
        entry = resume;
        microVU1.cycles = budget; // the stub stores these too; keep C++ view coherent
        microVU1.totalCycles = budget;
        if (++g_resumes <= 8 || (g_resumes & 1023u) == 0)
            olc_log("[om1] resume #%llu tpc=%04x start_pc=%04x served=%llu wall=%s\n",
                    (unsigned long long)g_resumes, VU1.VI[REG_TPC].UL & 0xffffu,
                    microVU1.regs().start_pc & 0xffffu, (unsigned long long)g_served,
                    om2_wall());
    }
    else
        entry = mVUlookupProg_VU1(VU1.VI[REG_TPC].UL, budget);
    if (!entry && !resumeRun)
        entry = om1_slowResolve(VU1.VI[REG_TPC].UL & 0x3ff8,
                                &microVU1.prog.lpState, true); // OC2: kicks (+MSCN) may deny
    if (!entry)
    {
        ++g_fallback;
        ++g_miss;
        VU1.VI[REG_TPC].UL >>= 3;
        // OC2: denied kicks log distinctly (deliberate static fallback).
        olc_note_miss(g_lastDeny ? "deny" : "fallback", VU1.VI[REG_TPC].UL << 3);
        g_lastDeny = false;
        olc_reset_live_vu();
        return OLC_MISS;
    }
    // OBE1: the LEAN=1 backup, now taken here (after the entry resolved, before any recorded code runs; nothing
    // between the bridge's old copy point and here writes VU1.Mem). Skipped for certified entries.
    if (backup)
    {
        backup->taken = false;
        backup->certified = olc_cert_enabled() && !obe1_entry_can_trap(entry);
        if (backup->certified)
            ++g_backupsElided;
        else
        {
            std::memcpy(backup->dst, backup->src, backup->size);
            backup->taken = true;
            ++g_backupsTaken;
            g_backupBytes += backup->size;
        }
    }
    g_trapArmed = 1;
    g_trapKind = 0;
    // IV1: the traps are plain-function siglongjmps (om1_fallthrough_miss / om1_jump_resolve), never a signal
    // handler, so the signal mask can't change between arm and jump; savemask=1 costs a sigprocmask (+ sigaltstack)
    // syscall pair per job for nothing. Lean arms without it.
    if ((olc_lean() ? sigsetjmp(g_trapBuf, 0) : sigsetjmp(g_trapBuf, 1)) != 0)
    {
        g_trapArmed = 0;
        ++g_miss;
        g_lastMissTrap = true;
        if (g_trapKind == 1)
            ++g_fallMisses;
        else if (g_trapKind == 2)
            ++g_jumpMisses;
        VU1.VI[REG_TPC].UL >>= 3;
        olc_note_miss(g_trapKind == 1 ? "fall" : (g_trapKind == 2 ? "jump" : "trap?"),
                      VU1.VI[REG_TPC].UL << 3);
        g_lastDeny = false; // a denied jump still logs as jump (vanishingly rare)
        olc_reset_live_vu();
        return OLC_MISS;
    }
    reinterpret_cast<mVUrecCallResume>(g_resumeAddr)(entry, budget);
    g_trapArmed = 0;
    VU1.VI[REG_TPC].UL >>= 3;
    if (microVU1.regs().flags & 0x4 && !THREAD_VU1)
    {
        microVU1.regs().flags &= ~0x4;
        // No hwIntcIrq: EMBED shape (delta 3).
    }
    ++g_served;
    if ((g_served & 16383u) == 0)
        olc_log("[om1] runs served=%llu miss=%llu fp=%llu resumes=%llu wall=%s backups=%llu elided=%llu "
                "backup_bytes=%llu cert_scans=%llu cert_words=%llu cert_entries=%zu forced_jump=%llu\n",
                (unsigned long long)g_served, (unsigned long long)g_miss,
                (unsigned long long)olc_footprint(), (unsigned long long)g_resumes, om2_wall(),
                (unsigned long long)g_backupsTaken, (unsigned long long)g_backupsElided,
                (unsigned long long)g_backupBytes, (unsigned long long)g_certScans,
                (unsigned long long)g_certWords, g_certCanTrap.size(),
                (unsigned long long)g_forcedJumpMisses);
    // OC2: served-side census cadence for the rare-miss regime (the miss-side
    // cadence is in olc_note_miss). Either trigger skips when nothing new.
    if ((g_served & 65535u) == 0 && !g_missKeys.empty() &&
        (g_served != g_lastCensusServed || g_miss != g_lastCensusMiss))
        olc_note_census();
    return OLC_SERVED;
}

bool olc_resume_parked()
{
    return microVU1.resumeEntry != nullptr && !mVUPersist::IsRecordingEnabled() &&
           om2_resume_enabled();
}

void olc_stats(uint64_t& served, uint64_t& fallback, uint64_t& miss, uint64_t& parks,
               uint64_t& jumpMisses, uint64_t& fallMisses)
{
    served = g_served;
    fallback = g_fallback;
    miss = g_miss;
    parks = g_parks;
    jumpMisses = g_jumpMisses;
    fallMisses = g_fallMisses;
}
