// snd-seam-1 Part 5: TK11 Tricky-mode frontend entry, moved verbatim from
// ps2xRuntime/src/lib/ps2_runtime.cpp (the `ssx3-tricky-menu` override).
// Every wrapper acts at its function's entry, so an EE checkpoint unwind
// never skips hook work. Knob off: nothing is wrapped. The policy headers
// stay runtime-side; `ps2_ssx3_tricky_list.h` and
// `ps2_ssx3_course_manifest.h` are included read-only, never edited.
#include "Ssx3TrickyMenu.h"

#include "ps2_ssx3_course_manifest.h"
#include "ps2_ssx3_tricky_layer.h"
#include "ps2_ssx3_tricky_list.h"
#include "ps2_ssx3_tricky_menu.h"
#include "ps2_ssx3_tricky_preview.h"
#include "ps2_runtime.h"
#include "runtime/ps2_memory.h"
#include "runtime/ps2_savestate.h"

#include <cstdio>
#include <cstring>
#include <filesystem>
#include <string>
#include <system_error>

// TK11: Tricky mode frontend entry (PS2X_SSX3_TRICKY_MENU=1, default off;
// ps2_ssx3_tricky_menu.h). Every wrapper acts at its function's entry (or,
// for the scePadRead glue, after a stub that never calls guest code), so an
// EE checkpoint unwind never skips hook work. Knob off: nothing is wrapped.
namespace
{
    PS2Runtime::RecompiledFunction g_tkFeUpdate = nullptr;
    PS2Runtime::RecompiledFunction g_tkLoadingUpdate = nullptr;
    PS2Runtime::RecompiledFunction g_tkMenuBuild = nullptr;
    PS2Runtime::RecompiledFunction g_tkMenuEvent = nullptr;
    PS2Runtime::RecompiledFunction g_tkMapFill = nullptr;
    PS2Runtime::RecompiledFunction g_tkPadGlue = nullptr;
    PS2Runtime::RecompiledFunction g_tkLuiLoad = nullptr;
    PS2Runtime::RecompiledFunction g_tkTrampoline = nullptr;
    PS2Runtime::RecompiledFunction g_tkMapInfo = nullptr;
    PS2Runtime::RecompiledFunction g_tkFileLookup = nullptr;
    __m128i g_tkV0{}; // scePadRead's return registers across a list refresh
    __m128i g_tkV1{};

    void tkLog(PS2Runtime *runtime, const std::string &msg)
    {
        if (msg.empty())
            return;
        const unsigned long long tick =
            runtime ? static_cast<unsigned long long>(runtime->memory().gs().vsyncTick.load(std::memory_order_relaxed))
                    : 0ull;
        std::fprintf(stderr, "[ssx3-tricky] tick=%llu %s\n", tick, msg.c_str());
    }

    void tkSetGpr(R5900Context *ctx, int reg, uint32_t value)
    {
        ctx->r[reg] = _mm_set_epi64x(0, static_cast<int64_t>(static_cast<int32_t>(value)));
    }

    // FE-owned guest continuation. All call arguments and the original FE
    // entry registers live in guest RAM across scheduler checkpoints.
    constexpr uint32_t kDirectMagic = 0x544b4431u;
    constexpr uint32_t kDirectFrame = 0x240u;

    bool tkDirectContinue(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        using namespace ps2_ssx3_tricky;
        const uint32_t f = getRegU32(ctx, 29);
        if (f > PS2_RAM_SIZE - kDirectFrame || rd32(ram, f) != kDirectMagic)
            return false;
        const uint32_t phase = rd32(ram, f + 4);
        const uint32_t config = rd32(ram, f + 12);
        const auto call = [&](uint32_t target, uint32_t next, uint32_t a0, uint32_t a1) {
            wr32(ram, f + 4, next);
            tkSetGpr(ctx, 4, a0);
            tkSetGpr(ctx, 5, a1);
            tkSetGpr(ctx, 31, kTrampoline);
            ctx->pc = target;
        };
        tkLog(runtime, "direct probe phase=" + std::to_string(phase) +
              " result=" + std::to_string(getRegU32(ctx, 2)));
        switch (phase)
        {
        case 0:
            call(0x14dd58u, 1, 0, 0); // current profile configuration
            break;
        case 1:
            wr32(ram, f + 12, getRegU32(ctx, 2));
            call(0x145108u, 2, getRegU32(ctx, 2), rd32(ram, f + 0x1c)); // context
            break;
        case 2:
            wr32(ram, 0x535c08u, rd32(ram, f + 0x10)); // donor event
            wr32(ram, getRegU32(ctx, 28) - 0x1f38u, 1);
            call(0x144df0u, 3, config, rd32(ram, f + 0x14)); // nav node +4
            break;
        case 3:
            call(0x1451e8u, 4, config, rd32(ram, f + 0x18)); // nav node +8
            break;
        case 4:
        {
            const uint32_t vtable = rd32(ram, config + 12);
            int16_t adjust = 0;
            std::memcpy(&adjust, ram + vtable + 8, sizeof(adjust));
            call(rd32(ram, vtable + 12), 5, config + adjust, 0);
            break;
        }
        case 5:
            tkSetGpr(ctx, 6, 0x100);
            tkSetGpr(ctx, 7, 0);
            call(0x317d70u, 6, 8, 0x45ce60u); // stock loading-state allocation
            break;
        case 6:
            call(0x232720u, 7, getRegU32(ctx, 2), 0); // loading-state constructor
            break;
        case 7:
            call(0x316ef8u, 8, rd32(ram, 0x4a28a8u), getRegU32(ctx, 2));
            break;
        default:
        {
            char stateLine[256];
            const uint32_t app = rd32(ram, 0x4a28a8u);
            std::snprintf(stateLine, sizeof(stateLine),
                          "direct state event=%u type=%u context=%u rules=%u config=0x%x app=0x%x "
                          "queued=0x%x mapEntries=%llu",
                          rd32(ram, 0x535c08u), ram[0x535c10u], ram[0x535c11u], ram[0x535c12u],
                          config, app, rd32(ram, app + 4),
                          static_cast<unsigned long long>(state().mapEntries));
            tkLog(runtime, stateLine);
            const uint32_t ret = rd32(ram, f + 0x20);
            std::memcpy(ctx->r, ram + f + 0x40, sizeof(ctx->r));
            wr32(ram, f, 0);
            tkSetGpr(ctx, 29, f + kDirectFrame);
            ctx->pc = ret; // re-enter the original FE update, with its original a0/ra
            ps2_ssx3_fe_launch::note("loading-queued", runtime->memory().gs().vsyncTick.load());
            tkLog(runtime, "direct loading queued at FE update; no stock menu navigation");
            break;
        }
        }
        return true;
    }

    bool tkDirectStart(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime,
                       const ps2_ssx3_fe_launch::Request &request)
    {
        using namespace ps2_ssx3_tricky;
        // Tap-path loader repair (tricky-direct-2 RE): the menu walk leaves
        // the load-select row for [0x5305B0] at -1 (the walk's 0x1819ec
        // accept handler stores the [s6+20] byte, 0xFF, at row+12), while a
        // clean chain has BSS 0 there. The loading writer (0x1298e0 family)
        // scales it (s0<<2) into the event-data base [0x4A28A0], so -1 reads
        // the neighbour word ([g0+12008], a small int) into the load record
        // instead of the object at [g0+12012]; the record's virtual call then
        // dies on a JALR-null (0x125ba0, a1=0x2). Restore the no-history
        // value when (and only when) the row still holds the stale -1.
        {
            uint32_t idx = 0u;
            std::memcpy(&idx, ram + 0x5305B0u, sizeof(idx));
            if (idx <= 64u)
            {
                const uint32_t row = 0x534FE0u + idx * 28u + 12u;
                uint32_t sel = 0u;
                std::memcpy(&sel, ram + row, sizeof(sel));
                if (sel == 0xFFFFFFFFu)
                {
                    const uint32_t zero = 0u;
                    std::memcpy(ram + row, &zero, sizeof(zero));
                    char fix[96];
                    std::snprintf(fix, sizeof(fix),
                                  "direct load-select repair row=%u -1 -> 0", idx);
                    tkLog(runtime, fix);
                }
            }
        }
        const uint32_t sp = getRegU32(ctx, 29);
        if (sp < kDirectFrame || sp > PS2_RAM_SIZE) return false;
        ps2_ssx3_tricky::state().directActive = true; // bypasses Map: keep the overlay shut so the pad reaches the briefing
        const uint32_t f = sp - kDirectFrame;
        std::memset(ram + f, 0, kDirectFrame);
        ps2_ssx3_tricky::wr32(ram, f, kDirectMagic);
        wr32(ram, f + 8, getRegU32(ctx, 31));
        wr32(ram, f + 0x10, request.event);
        wr32(ram, f + 0x14, request.type);
        wr32(ram, f + 0x18, request.rules);
        wr32(ram, f + 0x1c, request.context);
        wr32(ram, f + 0x20, ctx->pc);
        std::memcpy(ram + f + 0x40, ctx->r, sizeof(ctx->r));
        tkSetGpr(ctx, 29, f);
        ps2_ssx3_fe_launch::note("fe-consume", runtime->memory().gs().vsyncTick.load());
        return tkDirectContinue(ram, ctx, runtime);
    }

    void tkDirectProbe(PS2Runtime *runtime)
    {
        namespace tkl = ps2_ssx3_tricky_list;
        const char *probe = std::getenv("PS2X_SSX3_TRICKY_DIRECT_PROBE");
        if (!tkl::directKnob() || !probe || std::strcmp(probe, "1") != 0) return;
        const char *course = std::getenv("PS2X_SSX3_TRICKY_DIRECT_COURSE");
        const int sel = tkl::directCourseByCode(course ? course : "GARI");
        const char *opt = std::getenv("PS2X_SSX3_TRICKY_DIRECT_OPT");
        if (sel < 0 || (opt && std::strcmp(opt, "0") != 0 && std::strcmp(opt, "1") != 0))
        {
            tkLog(runtime, "direct probe: unknown course or option; request rejected");
            return;
        }
        tkl::ListState list;
        list.sel = static_cast<uint8_t>(sel);
        list.mode = opt && std::strcmp(opt, "1") == 0 ? 1 : 0;
        tkl::requestDirect(list);
    }

    void tkFeUpdateWrapper(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        using namespace ps2_ssx3_tricky;
        // Interior resume PCs belong to the same generated function. Never
        // consume there, in pad glue, or during a preview continuation.
        if (ctx->pc == 0x1a27a0u && !state().redirect)
        {
            const uint32_t app = rd32(ram, 0x4a28a8u);
            const uint32_t fe = getRegU32(ctx, 4);
            const uint32_t sp = getRegU32(ctx, 29);
            if (app && app < PS2_RAM_SIZE - 0x5c && fe && fe < PS2_RAM_SIZE - 16 &&
                rd32(ram, app) == fe && rd32(ram, fe) == 0x46d7c0u &&
                rd32(ram, app + 4) == 0 && rd32(ram, app + 0x58) == 0 &&
                sp >= kDirectFrame && sp <= PS2_RAM_SIZE)
            {
                ps2_ssx3_fe_launch::Request request;
                // tk-fe-2: consume only a request from the live picker
                // session; a stale (closed-session) park is dropped here.
                const uint64_t liveSession =
                    ps2_ssx3_tricky_list::pickerSession().load(std::memory_order_acquire);
                if (ps2_ssx3_fe_launch::take(request, liveSession))
                {
                    const auto venue = ps2_ssx3_tricky_list::takeDirectVenue();
                    if (ps2_ssx3_tricky_list::applyCourseMap(ram, PS2_RAM_SIZE, venue))
                    {
                        if (tkDirectStart(ram, ctx, runtime, request)) return;
                    }
                    tkLog(runtime, "FE request refused: venue mapping did not apply");
                    ps2_ssx3_fe_launch::finish(runtime->memory().gs().vsyncTick.load());
                    state().directActive = false;
                }
            }
        }
        g_tkFeUpdate(ram, ctx, runtime);
    }

    void tkLoadingUpdateWrapper(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        if (ctx->pc == 0x232930u)
            ps2_ssx3_fe_launch::loading(runtime->memory().gs().vsyncTick.load());
        g_tkLoadingUpdate(ram, ctx, runtime);
    }

    // preserve-fix-1: stock handles/count remain untouched. Only exact
    // overlay names get IDs in appended slots while mode owns the table.
    void tkFileLookupWrapper(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        if (ctx->pc == 0x3e4508u &&
            ps2_ssx3_file_table::lookup(ram, PS2_RAM_SIZE, getRegU32(ctx, 4), getRegU32(ctx, 6)))
        {
            tkSetGpr(ctx, 2, 1);
            ctx->pc = getRegU32(ctx, 31);
            return;
        }
        g_tkFileLookup(ram, ctx, runtime);
    }

    // Patches every CMNAMER copy in RAM (the game reads it once at boot).
    void tkPatchLabel(uint8_t *rdram, PS2Runtime *runtime)
    {
        using namespace ps2_ssx3_tricky;
        State &s = state();
        for (uint32_t a = findLoc(rdram, PS2_RAM_SIZE, kCmnAmer); a; a = findLoc(rdram, PS2_RAM_SIZE, kCmnAmer, a + 4u))
        {
            std::string msg;
            const int r = patchLabel(rdram, PS2_RAM_SIZE, a, msg);
            if (r < 0)
                s.labelRefused = true;
            else
                s.labelDone = true;
            tkLog(runtime, msg);
        }
    }

    // Main-menu build (0x194D18 runs once per row as the menu appears):
    // leaving Single Event or quitting to the title lands here, so Tricky
    // mode ends and the Map controller is forgotten.
    void tkMenuBuildWrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        using namespace ps2_ssx3_tricky;
        State &s = state();
        ps2_ssx3_fe_launch::menuBuild(runtime->memory().gs().vsyncTick.load());
        s.mapController = 0u;
        s.directActive = false;
        if (s.tricky)
        {
            std::string msg;
            modeSet(rdram, false, msg);
            s.tricky = false;
            tkLog(runtime, msg.empty() ? "mode: Tricky off (main menu)" : msg);
        }
        // Part 4: picker world swaps (applyCourseMap) are per-launch; put
        // every mapped donor record back when the frontend rebuilds so a
        // stock event never rides a dirty record. Skips clean records, so
        // knob-off and picker-unused boots write nothing here.
        if (!ps2_ssx3_tricky_list::restoreAllCourseMaps(rdram, PS2_RAM_SIZE))
            tkLog(runtime, "map restore refused at menu build");
        // The build calls this once per row: scan FEAMER (reloaded at each
        // frontend entry) once per tick.
        static uint64_t helpTick = ~0ull;
        const uint64_t tick = runtime ? runtime->memory().gs().vsyncTick.load(std::memory_order_relaxed) : 0u;
        for (uint32_t a = tick == helpTick ? 0u : findLoc(rdram, PS2_RAM_SIZE, kFeAmer); a;
             a = findLoc(rdram, PS2_RAM_SIZE, kFeAmer, a + 4u))
        {
            std::string msg;
            patchHelp(rdram, PS2_RAM_SIZE, a, msg);
            tkLog(runtime, msg);
        }
        helpTick = tick;
        g_tkMenuBuild(rdram, ctx, runtime);
    }

    // Main-menu event handler (this, item, event): on X, Previews takes the
    // Single Event state and turns Tricky mode on; any other row turns it off.
    void tkMenuEventWrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        using namespace ps2_ssx3_tricky;
        State &s = state();
        const uint32_t item = getRegU32(ctx, 5) & 0x1FFFFFFFu;
        if (getRegU32(ctx, 6) == kEventAccept && item && item + 0x40u < PS2_RAM_SIZE)
        {
            const uint32_t st = ps2_ssx3_tricky::rd32(rdram, item + kItemState);
            bool previews = false;
            const uint32_t next =
                onMainMenuAccept(ps2_ssx3_tricky::rd32(rdram, item + kItemWidgetHash), st, previews);
            std::string msg;
            if (previews)
            {
                ps2_ssx3_tricky::wr32(rdram, item + kItemState, next);
                s.tricky = true;
                char buf[96];
                std::snprintf(buf, sizeof(buf), "main menu: Tricky Courses -> Single Event (item 0x%06x state 0x%x -> 0x%x)",
                              item, st, next);
                tkLog(runtime, buf);
                modeSet(rdram, true, msg);
                // toast-audit-1: no mode toast (the Tricky list overlay itself
                // confirms the mode; tkLog below keeps the record).
                tkDirectProbe(runtime);

            }
            else if (s.tricky)
            {
                modeSet(rdram, false, msg);
                s.tricky = false;
            }
            tkLog(runtime, msg);
        }
        g_tkMenuEvent(rdram, ctx, runtime);
    }

    void tkMapFillWrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        ps2_ssx3_tricky::state().mapController = getRegU32(ctx, 4);
        ++ps2_ssx3_tricky::state().mapEntries; // tk-menu-1: the overlay's reopen latch
        ps2_ssx3_tricky::state().directActive = false; // a real Map entry re-arms the overlay
        g_tkMapFill(rdram, ctx, runtime);
    }

    // scePadRead glue: the stub (TK7/TK9 chord inside) sets pc = ra and never
    // calls guest code. After a chord changed the rows while the Map screen is known,
    // return into 0x200DE8(controller) with ra = the trampoline instead.
    void tkPadGlueWrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        using namespace ps2_ssx3_tricky;
        static uint8_t rows[kRowsBytes + kTopoBytes];
        const bool armed = rowsArmed();
        if (armed)
        {
            std::memcpy(rows, rdram + ps2_ssx3_course::kEventBase, kRowsBytes);
            std::memcpy(rows + kRowsBytes, rdram + ps2_ssx3_course::kTopoBase, kTopoBytes);
        }
        g_tkPadGlue(rdram, ctx, runtime);
        State &s = state();
        if (!s.labelDone && !s.labelRefused)
            tkPatchLabel(rdram, runtime);
        if (ps2_ssx3_course::appUpdateFn(rdram) == ps2_ssx3_course::kGameUpdate)
        {
            s.mapController = 0u;
            s.wasGame = true;
            ps2_ssx3_fe_launch::playing(runtime->memory().gs().vsyncTick.load());
            return;
        }
        // tk-card-1: event-exit teardown. Quitting/restarting lands on peak
        // select, loading or title without a main-menu build, so the build
        // hook never tears the mode down. Attempt it on the game->frontend
        // edge (or a pending retry); menu navigation mid-drive is frontend
        // without an edge and stays untouched.
        // tk-fe-2: restore the donor records FIRST (pure RAM writes, never
        // gated), then switch the mode off. A loader stuck on mapped
        // records keeps the mode step refused ("location X is loading")
        // while the mapping that blocks it stays in place — restoring
        // first lets the loader drain so the retry can complete instead of
        // wedging with Tricky on forever (morning-build post-race freeze).
        // tkTeardownStuck counts consecutive refused attempts (reset on
        // success); the watchdog logs every 60th while wedged.
        static uint64_t tkTeardownStuck = 0;
        if (eventExitStep(s.wasGame, false, s.tricky, s.teardownPending) == EventExitAction::Attempt)
        {
            const bool restoreOk = ps2_ssx3_tricky_list::restoreAllCourseMaps(rdram, PS2_RAM_SIZE);
            std::string msg;
            const bool modeRet = modeSet(rdram, false, msg);
            const bool refused = !modeRet && !msg.empty();
            const EventExitResult r = eventExitApplied(refused, restoreOk);
            s.tricky = r.tricky;
            s.teardownPending = r.pending;
            if (r.pending)
            {
                // Stuck-teardown watchdog: loud, device-visible, 1/sec. A
                // transient loader-busy clears on retry; a persistent
                // refusal names its reason instead of wedging silently.
                if (++tkTeardownStuck % 60u == 1u)
                    tkLog(runtime, std::string("event exit: teardown STUCK attempts=") +
                                      std::to_string(tkTeardownStuck) + " (" +
                                      (refused ? msg : "map restore failed") + ")");
            }
            else
            {
                tkTeardownStuck = 0;
                tkLog(runtime, msg.empty() ? "event exit: Tricky off (no menu build)" : msg);
            }
        }
        if (s.wasGame)
            ps2_ssx3_fe_launch::finish(runtime->memory().gs().vsyncTick.load());
        s.wasGame = false;
        if (!armed || s.redirect || !s.mapController ||
            (std::memcmp(rows, rdram + ps2_ssx3_course::kEventBase, kRowsBytes) == 0 &&
             std::memcmp(rows + kRowsBytes, rdram + ps2_ssx3_course::kTopoBase, kTopoBytes) == 0))
            return;
        s.redirect = true;
        s.returnPc = ctx->pc;
        s.sp = getRegU32(ctx, 29);
        g_tkV0 = ctx->r[2];
        g_tkV1 = ctx->r[3];
        tkSetGpr(ctx, 31, kTrampoline);
        tkSetGpr(ctx, 4, s.mapController);
        ctx->pc = kMapFill;
        char buf[96];
        std::snprintf(buf, sizeof(buf), "list refresh: 0x%x(0x%x), then back to 0x%x", kMapFill, s.mapController,
                      s.returnPc);
        tkLog(runtime, buf);
    }

    // Optional overlay files are part of the layer, not a new runtime knob.
    // Old presets with only COURSPIT retain their original frontend path.
    bool tkPreviewAsset(uint8_t *ram, int event)
    {
        using namespace ps2_ssx3_tricky_preview;
        if (!course(ram, PS2_RAM_SIZE, event)) return false;
        const char *overlay = std::getenv("PS2X_CD_OVERLAY");
        const uint32_t code = ps2_ssx3_tricky::rd32(ram, 0x442950u + event * 4);
        if (!overlay || !range(code, 5, PS2_RAM_SIZE)) return false;
        std::string name("TP");
        name.append(reinterpret_cast<char *>(ram + code), 4);
        name += ".SSH";
        std::error_code ec;
        return std::filesystem::is_regular_file(std::filesystem::path(overlay) / "DATA" / "UI" / name, ec);
    }

    // TK52: the continuation, its return address, arguments and register
    // spill are guest stack bytes. A checkpoint or state load at any guest
    // call therefore resumes the same phase without pending host state.
    void tkMapInfoWrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        using namespace ps2_ssx3_tricky_preview;
        using ps2_ssx3_tricky::rd32;
        using ps2_ssx3_tricky::wr32;
        const uint32_t controller = getRegU32(ctx, 4);
        const uint32_t sp = getRegU32(ctx, 29);
        // A list-refresh continuation already owns this guest-stack frame.
        if (range(sp, kFrameBytes, PS2_RAM_SIZE) && rd32(rdram, sp) == kFrameMagic && rd32(rdram, sp + 0x2dc) == 1)
        {
            g_tkMapInfo(rdram, ctx, runtime);
            return;
        }
        const auto &modes = ps2_ssx3_course::courseModes();
        bool previous = false;
        if (range(controller, 0x2f0, PS2_RAM_SIZE))
        {
            const uint32_t app = rd32(rdram, 0x4a28a8u);
            const uint32_t async = range(app, 0x120, PS2_RAM_SIZE) ? rd32(rdram, app + 0x11c) : 0;
            const uint32_t slot = rd32(rdram, controller + 0x2d8);
            previous = slot < 5 && range(async, 5 * 0x11c, PS2_RAM_SIZE) &&
                std::memcmp(rdram + async + slot * 0x11c, "data/ui/TP", 10) == 0;
        }
        const bool selected = modes.armed && ps2_ssx3_course::modeCurrent(modes, rdram) &&
            tkPreviewAsset(rdram, cursorEvent(rdram, PS2_RAM_SIZE, controller));
        if ((!selected && !previous) ||
            sp < kFrameBytes || !range(sp - kFrameBytes, kFrameBytes, PS2_RAM_SIZE))
        {
            g_tkMapInfo(rdram, ctx, runtime);
            return;
        }
        const uint32_t frame = sp - kFrameBytes;
        std::memset(rdram + frame, 0, kFrameBytes);
        wr32(rdram, frame, kFrameMagic);
        wr32(rdram, frame + 8, controller);
        wr32(rdram, frame + 12, getRegU32(ctx, 31));
        tkSetGpr(ctx, 29, frame);
        tkSetGpr(ctx, 31, ps2_ssx3_tricky::kTrampoline);
        g_tkMapInfo(rdram, ctx, runtime);
    }

    bool tkPreviewContinue(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        using namespace ps2_ssx3_tricky_preview;
        using ps2_ssx3_tricky::rd32;
        using ps2_ssx3_tricky::wr32;
        const uint32_t f = getRegU32(ctx, 29);
        if (!range(f, kFrameBytes, PS2_RAM_SIZE) || rd32(ram, f) != kFrameMagic) return false;
        const uint32_t controller = rd32(ram, f + 8);
        const uint32_t phase = rd32(ram, f + 4);
        const auto finish = [&]() {
            const uint32_t pc = rd32(ram, f + 12);
            std::memcpy(ctx->r, ram + f + 0x20, sizeof(ctx->r));
            if (rd32(ram, f + 0x2dc) == 1)
            {
                std::memcpy(&ctx->r[2], ram + f + 0x320, 16);
                std::memcpy(&ctx->r[3], ram + f + 0x330, 16);
            }
            wr32(ram, f, 0);
            tkSetGpr(ctx, 29, f + kFrameBytes);
            tkSetGpr(ctx, 31, pc);
            ctx->pc = pc;
        };
        const auto call = [&](uint32_t pc, uint32_t next, uint32_t a0, uint32_t a1) {
            wr32(ram, f + 4, next);
            tkSetGpr(ctx, 4, a0);
            tkSetGpr(ctx, 5, a1);
            tkSetGpr(ctx, 31, ps2_ssx3_tricky::kTrampoline);
            ctx->pc = pc;
        };
        if (phase == 0)
        {
            std::memcpy(ram + f + 0x20, ctx->r, sizeof(ctx->r));
            const int event = cursorEvent(ram, PS2_RAM_SIZE, controller);
            const uint32_t app = rd32(ram, 0x4a28a8u);
            const uint32_t async = range(app, 0x120, PS2_RAM_SIZE) ? rd32(ram, app + 0x11cu) : 0;
            const uint32_t slot = rd32(ram, controller + 0x2d8u);
            const uint32_t up = widget(ram, PS2_RAM_SIZE, rd32(ram, controller + 0x2e8u), "HelpText");
            const auto &modes = ps2_ssx3_course::courseModes();
            const bool selected = modes.armed && ps2_ssx3_course::modeCurrent(modes, ram) && tkPreviewAsset(ram, event);
            if (!selected)
            {
                // A cursor move onto a stock/excluded row on the same peak
                // must reload that peak, even though the peak index did not change.
                wr32(ram, controller + 0x2d4, 0xffffffffu);
                call(0x203990u, 3, controller, 0);
                return true;
            }
            if (slot >= 5 || !range(async, 5 * 0x11c, PS2_RAM_SIZE) || !up)
            {
                tkLog(runtime, "preview: cursor/async/widget unavailable");
                finish();
                return true;
            }
            wr32(ram, f + 0x10, async);
            wr32(ram, f + 0x14, slot);
            wr32(ram, f + 0x18, static_cast<uint32_t>(event));
            wr32(ram, f + 0x1c, up);
            call(0x1a38c0u, 1, async, slot); // owned async slot cleanup
        }
        else if (phase == 1)
        {
            const uint32_t async = rd32(ram, f + 0x10), slot = rd32(ram, f + 0x14);
            const uint32_t data = async + slot * 0x11cu;
            const uint32_t event = rd32(ram, f + 0x18);
            const uint32_t code = rd32(ram, 0x442950u + event * 4);
            if (!range(code, 5, PS2_RAM_SIZE)) { finish(); return true; }
            std::snprintf(reinterpret_cast<char *>(ram + data), 0x100, "data/ui/TP%.4s.ssh", ram + code);
            wr32(ram, data + 0x108, 1); // stock async load
            wr32(ram, data + 0x114, 1); // stock status: start load
            wr32(ram, controller + 0x2dc, 0); // Map waits for slot READY
            const uint32_t pic = rd32(ram, controller + 0x2e4);
            if (range(pic, 0x80, PS2_RAM_SIZE)) wr32(ram, pic + 0x7c, 0);
            const uint32_t row = ps2_ssx3_course::kEventBase + event * ps2_ssx3_course::kEventStride;
            std::snprintf(reinterpret_cast<char *>(ram + f + 0x240), 128, "Ride %.31s from SSX Tricky.", ram + row + 4);
            tkLog(runtime, "preview: event=" + std::to_string(event) + " slot=" + std::to_string(slot) +
                  " file=" + reinterpret_cast<const char *>(ram + data));
            call(0x3a0c50u, 2, rd32(ram, f + 0x1c), f + 0x240); // owned cUIText copy
        }
        else if (phase == 2)
        {
            // The host mountain's 17 route meshes are separate widgets.
            // Use their own color setter, which also updates vertex colors.
            // +0x1c alone would leave the cached geometry drawing old routes.
            const uint32_t bases[] = {0x4713a8u, 0x471420u, 0x471488u};
            const uint32_t counts[] = {6, 6, 5};
            uint32_t index = rd32(ram, f + 0x2d0);
            for (; index < 17; ++index)
            {
                uint32_t group = index < 6 ? 0 : index < 12 ? 1 : 2;
                uint32_t row = index - (group == 0 ? 0 : group == 1 ? counts[0] : counts[0] + counts[1]);
                const uint32_t name = rd32(ram, bases[group] + row * 4);
                if (!range(name, 32, PS2_RAM_SIZE) || std::memchr(ram + name, 0, 32) == nullptr) continue;
                const uint32_t obj = widget(ram, PS2_RAM_SIZE, rd32(ram, controller + 0x2e8),
                                           reinterpret_cast<const char *>(ram + name));
                if (!obj) continue;
                wr32(ram, f + 0x2d0, index + 1);
                call(0x3a3398u, 2, obj, f + 0x300); // zero RGBA, native mesh update
                return true;
            }
            // Original MapInfo owns SE_box/SE_check location markers through
            // the current UI state. Its normal show/hide calls restore them
            // on stock rows; hide them with that same native operation here.
            for (; index < 33; ++index)
            {
                const uint32_t marker = index - 17;
                std::snprintf(reinterpret_cast<char *>(ram + f + 0x240), 128,
                              marker < 8 ? "SE_box%u" : "SE_check%u", marker % 8);
                wr32(ram, f + 0x2d0, index + 1);
                call(0x39e8b8u, 2, rd32(ram, controller + 0x2ec), f + 0x240);
                return true;
            }
            finish();
        }
        else
            finish();
        return true;
    }

    void tkTrampolineWrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        if (tkDirectContinue(rdram, ctx, runtime)) return;
        if (tkPreviewContinue(rdram, ctx, runtime)) return;
        using namespace ps2_ssx3_tricky;
        State &s = state();
        if (s.redirect && getRegU32(ctx, 29) == s.sp)
        {
            ctx->r[2] = g_tkV0;
            ctx->r[3] = g_tkV1;
            tkSetGpr(ctx, 31, s.returnPc);
            ctx->pc = s.returnPc;
            s.redirect = false;
            ++s.refreshes;
            tkLog(runtime, "list refreshed #" + std::to_string(s.refreshes));
            // The existing chord refresh fills row labels only. Refresh the
            // pane too when the TK52 assets are installed, including Stock
            // restoration without requiring another cursor move.
            const char *overlay = std::getenv("PS2X_CD_OVERLAY");
            std::error_code ec;
            using namespace ps2_ssx3_tricky_preview;
            const uint32_t sp = getRegU32(ctx, 29);
            if (overlay && range(s.mapController, 0x2f0, PS2_RAM_SIZE) && sp >= kFrameBytes &&
                range(sp - kFrameBytes, kFrameBytes, PS2_RAM_SIZE) &&
                std::filesystem::is_regular_file(std::filesystem::path(overlay) / "DATA/UI/TPARA1.SSH", ec))
            {
                const uint32_t f = sp - kFrameBytes;
                std::memset(rdram + f, 0, kFrameBytes);
                wr32(rdram, f, kFrameMagic);
                wr32(rdram, f + 8, s.mapController);
                wr32(rdram, f + 12, s.returnPc);
                wr32(rdram, f + 0x2dc, 1);
                std::memcpy(rdram + f + 0x320, &ctx->r[2], 16);
                std::memcpy(rdram + f + 0x330, &ctx->r[3], 16);
                tkSetGpr(ctx, 29, f);
                tkSetGpr(ctx, 4, s.mapController);
                tkSetGpr(ctx, 31, kTrampoline);
                ctx->pc = kMapInfo;
            }
            return;
        }
        tkLog(runtime, "trampoline reached without a pending refresh (sp mismatch or no redirect)");
        g_tkTrampoline(rdram, ctx, runtime);
    }

    void tkLuiLoadWrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        tkPatchLabel(rdram, runtime);
        g_tkLuiLoad(rdram, ctx, runtime);
    }
} // namespace

namespace ps2_tricky_menu
{

void applyTrickyMenu(PS2Runtime &runtime)
{
    using namespace ps2_ssx3_tricky;
    if (!knob())
        return;
    const uint8_t *ram = runtime.memory().getRDRAM();
    if (!ram || ps2_ssx3_tricky::rd32(ram, ps2_ssx3_tricky_preview::kMapInfo) != 0x27bdf440u ||
        ps2_ssx3_tricky::rd32(ram, 0x3e4508u) != 0x27bdfea0u ||
        ps2_ssx3_tricky::rd32(ram, 0x1a27a0u) != 0x27bdff70u)
    {
        std::fprintf(stderr, "[ssx3-tricky] refused: Map info entry does not match\n");
        return;
    }
    for (const EntryWord &w : kEntryWords)
        if (!ram || ps2_ssx3_tricky::rd32(ram, w.pc) != w.word)
        {
            std::fprintf(stderr, "[ssx3-tricky] refused: 0x%x reads 0x%08x, expected 0x%08x; nothing wrapped\n",
                         w.pc, ram ? ps2_ssx3_tricky::rd32(ram, w.pc) : 0u, w.word);
            return;
        }
    struct Hook
    {
        uint32_t pc;
        PS2Runtime::RecompiledFunction *orig;
        PS2Runtime::RecompiledFunction wrap;
    };
    const Hook hooks[] = {{0x1a27a0u, &g_tkFeUpdate, &tkFeUpdateWrapper},
                          {0x232930u, &g_tkLoadingUpdate, &tkLoadingUpdateWrapper},
                          {kMainMenuItemState, &g_tkMenuBuild, &tkMenuBuildWrapper},
                          {kMainMenuEvent, &g_tkMenuEvent, &tkMenuEventWrapper},
                          {kMapFill, &g_tkMapFill, &tkMapFillWrapper},
                          {kPadReadGlue, &g_tkPadGlue, &tkPadGlueWrapper},
                          {kLuiLoad, &g_tkLuiLoad, &tkLuiLoadWrapper},
                          {kTrampoline, &g_tkTrampoline, &tkTrampolineWrapper},
                          {ps2_ssx3_tricky_preview::kMapInfo, &g_tkMapInfo, &tkMapInfoWrapper},
                          {0x3e4508u, &g_tkFileLookup, &tkFileLookupWrapper}};
    for (const Hook &h : hooks)
        if (!(*h.orig = runtime.lookupFunction(h.pc)))
        {
            std::fprintf(stderr, "[ssx3-tricky] refused: no function at 0x%x; nothing wrapped\n", h.pc);
            return;
        }
    for (const Hook &h : hooks)
        if (!runtime.replaceFunction(h.pc, h.wrap))
        {
            std::fprintf(stderr, "[ssx3-tricky] cannot wrap 0x%x\n", h.pc);
            std::abort();
        }
    state().on = true;
    std::fprintf(stderr, "[ssx3-tricky] armed: Previews -> Tricky Courses, Select Event refresh after picker switches\n");
}

} // namespace ps2_tricky_menu
