// SPDX-License-Identifier: GPL-3.0-or-later OR MIT
// astra-qp-direct-1: stock online session and FE constructors, without pad
// navigation. Explicit experimental value of the existing Quick Play gate.
// Guest calls use stack continuations so checkpoint unwinds remain resumable.
#include "Ssx3QuickPlayDirect.h"
#include "ps2_net_dirtysock.h"
#include "ps2_runtime.h"
#include "ps2_ui_toast.h"
#include "runtime/ps2_memory.h"

// Standard library.
#include <chrono>
#include <cstdio>
#include <cstring>

namespace {
constexpr uint32_t kMain = 0x194ea8, kPad = 0x3ffa58, kReturn = 0x200abc;
constexpr uint32_t kMagic = 0x51504431, kFrame = 0x300;
PS2Runtime::RecompiledFunction mainOriginal{}, padOriginal{}, returnOriginal{};
template<uint32_t Pc> PS2Runtime::RecompiledFunction closeOriginal{};
PS2Runtime::RecompiledFunction drawOriginal{}, filterOriginal{}, switchOriginal{};
enum class Stage { Idle, Connect, Auth, Persona, RootQueued, Queued, Ready, Leaving, Failed };
struct State {
    Stage stage = Stage::Idle;
    uint32_t menu = 0, ui = 0, config = 0, root = 0, popup = 0, menuScreen = 0, below = 0, stranded = 0;
    uint32_t pairRoot = 0, dlgFired = 0, lastShown = 0, lastShownTick = 0, strandAttempted = 0;
    uint32_t strandEvalLogged = 0, strandSeen = 0;
    uint64_t tap = 0, lastTick = 0, injections = 0, rootRetired = 0;
    std::chrono::steady_clock::time_point started{};
} state;
uint32_t rd(const uint8_t *ram, uint32_t a) {
    uint32_t v = 0; if (a <= PS2_RAM_SIZE - 4) std::memcpy(&v, ram + a, 4); return v;
}
void wr(uint8_t *ram, uint32_t a, uint32_t v) { if (a <= PS2_RAM_SIZE - 4) std::memcpy(ram+a, &v, 4); }
bool object(uint32_t p, uint32_t n) { return p >= 0x10000 && p <= PS2_RAM_SIZE - n; }
// qp-label-2 menu words (EE RAM u32): main 0x053da6c, sub 0x053dc18.
// The link-death dialog reads 0x5/0x8; healthy sessions never show 0x5.
constexpr uint32_t kMenuMain = 0x053da6cu, kMenuSub = 0x053dc18u;
void reg(R5900Context *c, int r, uint32_t v) { c->r[r] = _mm_set_epi64x(0, static_cast<int32_t>(v)); }
uint64_t tick(PS2Runtime *r) { return r->memory().gs().vsyncTick.load(std::memory_order_relaxed); }
void log(PS2Runtime *r, const char *event, uint32_t value=0) {
    std::fprintf(stderr, "[qp-direct] tick=%llu event=%s value=%u elapsed_ticks=%llu injected=%llu wall_ms=%lld host_epoch_ms=%lld\n",
        static_cast<unsigned long long>(tick(r)), event, value,
        static_cast<unsigned long long>(tick(r)-state.tap),
        static_cast<unsigned long long>(ps2_net::qpSyntheticPresses()-state.injections),
        static_cast<long long>(std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now()-state.started).count()),
        static_cast<long long>(std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count()));
    std::fflush(stderr);
}
void call(uint8_t *ram, R5900Context *c, uint32_t pc, uint32_t next,
          uint32_t a0=0, uint32_t a1=0, uint32_t a2=0, uint32_t a3=0) {
    wr(ram, getRegU32(c,29)+4, next);
    reg(c,4,a0); reg(c,5,a1); reg(c,6,a2); reg(c,7,a3); reg(c,31,kReturn); c->pc=pc;
}
void finish(uint8_t *ram, R5900Context *c) {
    const auto f=getRegU32(c,29), ret=rd(ram,f+8);
    std::memcpy(c->r,ram+f+0x40,sizeof(c->r));
    // Only caller-owned scratch is cleared; the guest BXStrings own copies.
    std::memset(ram+f,0,kFrame);
    c->pc=ret;
}
void fail(uint8_t *ram, R5900Context *c, PS2Runtime *r, const char *why) {
    state.stage=Stage::Failed; log(r,why); finish(ram,c);
}
bool begin(uint8_t *ram, R5900Context *c, uint32_t pc, uint32_t next,
           uint32_t a0=0, uint32_t a1=0) {
    const auto sp=getRegU32(c,29);
    if (sp < kFrame || sp > PS2_RAM_SIZE) return false;
    const auto f=sp-kFrame;
    std::memset(ram+f,0,kFrame);
    wr(ram,f,kMagic); wr(ram,f+8,c->pc);
    std::memcpy(ram+f+0x40,c->r,sizeof(c->r));
    reg(c,29,f); call(ram,c,pc,next,a0,a1); return true;
}
void continuation(uint8_t *ram, R5900Context *c, PS2Runtime *r) {
    const auto f=getRegU32(c,29);
    if (!object(f,kFrame) || rd(ram,f)!=kMagic) { returnOriginal(ram,c,r); return; }
    const auto result=getRegU32(c,2), auth=rd(ram,0x4a3028);
    const auto phase=rd(ram,f+4);
    switch (phase) {
    case 0: call(ram,c,0x14dc80,1); break;
    case 1: call(ram,c,0x14dd58,2,result,0); break;
    case 2:
        if (!object(result,16)) { fail(ram,c,r,"invalid-profile"); break; }
        state.config=result; call(ram,c,0x145108,3,result,2); break;
    case 3: call(ram,c,0x255860,4); break;
    case 4: call(ram,c,0x25b650,5); break;
    case 5: call(ram,c,0x260f80,6); break;
    case 6: call(ram,c,0x266ca0,7); break;
    case 7: {
        wr(ram,state.menu+0x48,1);
        const auto vt=rd(ram,state.config+12);
        if (!object(vt,16)) { fail(ram,c,r,"invalid-profile-vtable"); break; }
        int16_t adjust=0; std::memcpy(&adjust,ram+vt+8,2);
        call(ram,c,rd(ram,vt+12),8,state.config+adjust); break;
    }
    case 8: call(ram,c,0x1a86e8,9,1); break;
    case 9: {
        // Stock network-config accept starts the net manager ([gp-0x238])
        // through 0x255f50; its state machine then reaches state 5, the only
        // state whose update services game-link slot 0 (gmut/comp after
        // +ses). Start it the same way, then run netw/star now rather than
        // on its next update so the session connect below keeps its order.
        const auto net=rd(ram,getRegU32(c,28)-0x238);
        if (!object(net,0x34)) { fail(ram,c,r,"net-manager-unavailable"); break; }
        call(ram,c,0x255f50,40,net,0); break;
    }
    case 40:
        std::memcpy(ram+f+0x2c0,"mc0:Broadband#0",15);
        call(ram,c,0x3f3c50,41,f+0x2c0); break;
    case 41:
        // The stock state-2 handler's store after its own netw/star.
        wr(ram,rd(ram,getRegU32(c,28)-0x238)+0x20,3);
        [[fallthrough]];
    case 10:
        // qp-failclosed-1: without a usable per-install login there is no
        // identity to enter with. Never proceed on the Mc-served bootstrap
        // bytes; refuse the entry here (Main is still up, so this lands
        // back on the Main Menu exactly like Part 2's flat Back: no
        // disconnect dialog, no memory-card prompt) with one player-facing
        // toast. The send choke point drops any stray auth as well.
        if (!object(auth,0x300)) { fail(ram,c,r,"invalid-auth-struct"); break; }
        if (!ps2_net::qpCopyIdentity(ram,f+0x240)) {
            ps2x::ui::toast(ps2x::ui::kQpLoginMissingToast, 8.0f);
            fail(ram,c,r,"login-unavailable"); break;
        }
        call(ram,c,0x3184e8,11,auth+0x48,f+0x240); break;
    case 11: call(ram,c,0x3184e8,12,auth+0x4c,f+0x280); break;
    case 12: call(ram,c,0x25b6d8,13,auth,0); break;
    case 13:
        state.stage=Stage::Connect; log(r,"connect-started"); finish(ram,c); break;
    case 20:
        state.stage=Stage::Auth; log(r,"auth-sent"); finish(ram,c); break;
    case 21:
        state.stage=Stage::Persona; log(r,"persona-selected"); finish(ram,c); break;
    case 30:
        if (!object(result,0x6d0)) { fail(ram,c,r,"root-allocation-failed"); break; }
        call(ram,c,0x1bfbb8,31,result,state.ui); break;
    case 31:
        state.root=result;
        call(ram,c,0x317d70,32,0x734,0x4654e0); break;
    case 32:
        if (!object(result,0x734)) { fail(ram,c,r,"popup-allocation-failed"); break; }
        call(ram,c,0x1c0f40,33,result,state.ui,state.root); break;
    case 33:
        state.popup=result;
        wr(ram,state.popup+0x18,0x82e); wr(ram,0x4a4e88,1);
        state.stage=Stage::RootQueued;
        call(ram,c,0x39f400,34,state.ui+0x18,state.root); break;
    case 34:
        // The Main close animation already completed while connecting.
        // Finish its callback after queuing the successor so normal FE
        // cleanup runs before the new root initializes shared UI resources.
        if (state.menuScreen && !rd(ram,state.menuScreen+0xd0)) wr(ram,state.menuScreen+0xd0,state.menu);
        wr(ram,state.menu+0x1c,(rd(ram,state.menu+0x1c)&~0x3f00u)|0x700u);
        log(r,"root-queued"); finish(ram,c); break;
    case 36:
        state.stage=Stage::Ready; log(r,"filter-ready"); finish(ram,c); break;
    case 35:
        state.stage=Stage::Queued; log(r,"root-and-filter-queued"); finish(ram,c); break;
    case 50:
        log(r,"strand-logoff-returned",result); finish(ram,c); break;
    case 51:
        log(r,"strand-logoff-b-returned",result); finish(ram,c); break;
    default: fail(ram,c,r,"unknown-continuation"); break;
    }
}
void mainHook(uint8_t *ram, R5900Context *c, PS2Runtime *r) {
    const auto menu=getRegU32(c,4), item=getRegU32(c,5), event=getRegU32(c,6);
    if (event==5 && object(item,0x1c) && rd(ram,item+0x18)==0x31 &&
        object(menu,0x58)) {
        // Repeated accepts while the asynchronous connection is pending do
        // not launch another session or another frontend state.
        if (state.stage!=Stage::Idle && state.stage!=Stage::Ready && state.stage!=Stage::Leaving &&
            state.stage!=Stage::Failed) {
            c->pc=getRegU32(c,31); return;
        }
        // qp-freeze-1: the stock entry parks forever when the lobby never
        // answers (silent SYN drop: no reply, no error, guest waits on
        // @dir). Probe reachability first, bounded, on this game thread;
        // refuse flat on Main with the player copy instead of starting a
        // session that can never complete. A re-tap retries the probe.
        if (const char *unreachable = ps2_net::qpProbeLobby(8000u)) {
            log(r,"lobby-unavailable");
            ps2x::ui::toast(unreachable, 8.0f);
            c->pc=getRegU32(c,31); return;
        }
        state={}; state.menu=menu; state.ui=rd(ram,menu+0x10);
        state.tap=tick(r); state.injections=ps2_net::qpSyntheticPresses();
        state.started=std::chrono::steady_clock::now();
        if (object(state.ui,0x20)) {
            c->pc=getRegU32(c,31);
            if (begin(ram,c,0x1a86e8,0,0)) { log(r,"tap",rd(ram,menu+0x1c)); return; }
        }
        state.stage=Stage::Failed; log(r,"invalid-entry");
    }
    // Disabling the held Main's widgets (close callback 0x39ce48) reports
    // a selection change, event 2. Main reacts as if still active and the
    // state it starts is left dangling when the queue retires it.
    if (event==2 && menu==state.menu &&
        (state.stage==Stage::Connect || state.stage==Stage::Auth || state.stage==Stage::Persona)) {
        log(r,"held-menu-event",event); c->pc=getRegU32(c,31); return;
    }
    mainOriginal(ram,c,r);
}
template<uint32_t Pc> void closeHook(uint8_t *ram, R5900Context *c, PS2Runtime *r) {
    // The UI's accept animation independently closes its owning FE state.
    // 0x39ce78 sets lifecycle 6 without the delete bit; with no successor
    // it then reopens Main Menu, whose initializer destroys the session.
    // Defer only that owner phase store for this one pending transaction;
    // the normal queue operation retires Main before the authenticated root
    // initializes. The rest still runs: 0x39ce48 = 0x398638(screen+0x40,0)
    // (disable the widget group) then phase 6, so tail-call that routine
    // with the caller's ra. padHook then detaches the held screen from Main
    // (screen+0xd0, which both callbacks check before the phase store), so
    // 0x39ce98 runs unchanged and Main's widget timeline events stay inert
    // until the queue retires it (case 34 reattaches).
    const auto screen=getRegU32(c,4);
    // Part 2 (a) teardown-hook probe, record-only: at link death the entry
    // pair is already torn (P27/P29). Log every close of our pair, and every
    // close while Ready with its caller, to identify the death-time closer.
    // No behavior change: always falls through to the original below.
    if (screen != 0 && (state.stage==Stage::Ready || screen==state.popup || screen==state.root)) {
        log(r, Pc==0x39ce48 ? "teardown-close-48" : "teardown-close-98", screen);
        log(r, "teardown-close-ra", getRegU32(c,31));
    }
    if (Pc==0x39ce48 && object(screen,0xd4) && rd(ram,screen+0xd0)==state.menu &&
        (state.stage==Stage::Connect || state.stage==Stage::Auth || state.stage==Stage::Persona)) {
        log(r,"deferred-menu-close",getRegU32(c,31));
        state.menuScreen=screen;
        reg(c,4,screen+0x40); reg(c,5,0); c->pc=0x398638;
        return;
    }
    closeOriginal<Pc>(ram,c,r);
}
void drawHook(uint8_t *ram, R5900Context *c, PS2Runtime *r) {
    // Online Main is only the filter's parent here: never drawn, including
    // its dimmed backdrop behind the filter and its close after a switch.
    // Forget it once its close has had time to finish.
    if (state.rootRetired && tick(r)-state.rootRetired>120) state.root=0;
    if (state.root && getRegU32(c,4)==state.root && rd(ram,state.root+8)==0x46ad48 &&
        (state.stage==Stage::RootQueued || state.stage==Stage::Queued ||
         state.stage==Stage::Ready || state.stage==Stage::Leaving)) {
        c->pc=getRegU32(c,31); return;
    }
    drawOriginal(ram,c,r);
}
void switchHook(uint8_t *ram, R5900Context *c, PS2Runtime *r) {
    // 0x1a8a40 shows a new top online state; the second arg is ignored by the
    // switch itself. Entry shows our filter (caller 0x1c1b0c); a later switch
    // is the disconnect strand coming back.
    // qp-cancel-1 Part 2: any connection-lost accept SHOWS the stock Online
    // Main Menu again — that landing is the strand, whatever face or caller
    // dismissed the dialog. Only record it; the switch runs stock. Entry's
    // covered state is recorded to recognize the return; anything else stays
    // stock. (P24/P26/P32: every 0x1c0138 route from the strand is dead —
    // stock-OnlineMain Logoff no-ops, torn-root Logoff is unsafe, the UI
    // transplant corrupts the strand. The back-out probe P30/P33 decides the
    // route home.)
    const auto shown=getRegU32(c,4), ra=getRegU32(c,31);
    if (ra==0x1c1b0c && state.stage==Stage::Ready) {
        state.below=getRegU32(c,5);
        state.pairRoot=state.root;
    }
    // Part 2 (a): record EVERY top-state show while Ready with its tick, so
    // padHook can tell a fresh post-tear strand show from a stale pointer.
    // Record-only (one line per switch); the switch itself runs stock.
    if (state.stage==Stage::Ready && shown != 0u) {
        state.lastShown=shown; state.lastShownTick=tick(r);
        log(r,"strand-switch",shown);
    }
    if (ps2_quick_play_direct::qpOnlineMainReturnTakesLogoff(shown, state.stage==Stage::Ready, state.below) &&
        object(shown,0x6d0)) {
        state.stranded=shown; state.strandSeen=shown; log(r,"stranded-online-main",shown);
    }
    if (state.root && !state.rootRetired &&
        (state.stage==Stage::Ready || state.stage==Stage::Leaving)) state.rootRetired=tick(r);
    switchOriginal(ram,c,r);
}
void filterHook(uint8_t *ram, R5900Context *c, PS2Runtime *r) {
    // Back (event 6) on our filter, idle or searching: take Online Main's
    // Logoff confirmation, 0x1c0138(root, child, 0xF): close the child,
    // switch to a new cFEStateMainMenu (whose init destroys the session,
    // auth, game link and net manager) and log off. No disconnect dialog,
    // no cFEStateNetAccountMem save prompt, no login/persona screens.
    // Stock Back on a searching filter instead closes into the hidden Online
    // Main (drawHook hides our root: black gap, "menus gone") and then the
    // disconnect-confirm trench — that is qp-cancel-1 (and the long way to
    // the Part-2 strand). The in-flight search's lobby session dies with the
    // session teardown below.
    const auto self=getRegU32(c,4), event=getRegU32(c,6);
    const bool isOurs = self==state.popup && object(self,0x734) &&
        object(state.root,0x6d0) && rd(ram,self+0x20)==state.root;
    if (ps2_quick_play_direct::qpBackTakesLogoff(event, state.stage==Stage::Ready, isOurs)) {
        const bool searching = rd(ram,self+0x6ec) != 0u;
        state.stage=Stage::Leaving; log(r, searching ? "back-to-main-search" : "back-to-main");
        reg(c,4,state.root); reg(c,5,self); reg(c,6,0xf); c->pc=0x1c0138;
        return;
    }
    filterOriginal(ram,c,r);
}
void padHook(uint8_t *ram, R5900Context *c, PS2Runtime *r) {
    const auto returnPc=getRegU32(c,31);
    // Death telemetry: the link-death words (0x5/0x8) appear with the death
    // handling, at which point the entry pair is already torn (P27/P29) — so
    // this only records the moment, one shot per entry.
    if (state.stage==Stage::Ready && !state.dlgFired &&
        rd(ram,kMenuMain)==0x5u && rd(ram,kMenuSub)==0x8u) {
        state.dlgFired=1; log(r,"dialog-pair-torn");
    }
    // Part 2 (a) fix candidate: one deferred 0x1c0138 on a FRESH post-tear
    // strand object (P34: silent face, pair slot ptr=0, cross rebuilds a live
    // Online Main the entry-matching detector misses). Guards (pure gate
    // qpStrandLogoffReady): ready, pair vtbl flipped dead, no dialog up,
    // switch within the last 600 ticks, shown live and not our pair, once.
    // begin()+return skips the pad leaf for one tick (mainHook precedent).
    // P26 no-op or wrong-landing verdicts come from the leg, not here.
    if (state.stage==Stage::Ready) {
        const auto now0=tick(r);
        const bool pairDead = state.pairRoot != 0u && rd(ram,state.pairRoot+8) != 0x46ad48u;
        const bool dialogUp = rd(ram,kMenuMain)==0x5u && rd(ram,kMenuSub)==0x8u;
        const bool fresh = state.lastShown != 0u && now0 >= state.lastShownTick &&
            now0-state.lastShownTick <= 600u;
        // P37: the fresh strand Online Main is sane but its vtbl is not our
        // root's 0x46ad48, so equality is the wrong liveness test. Sanity =
        // bounds + non-null mapped vtbl + none of our three pair members
        // (root, pairRoot, popup: the torn-pair call of P27 is excluded).
        const uint32_t shownVtbl = state.lastShown != 0u && object(state.lastShown,0x10u) ?
            rd(ram,state.lastShown+8) : 0u;
        const bool sane = state.lastShown != 0u && object(state.lastShown,0x6d0u) &&
            shownVtbl != 0u && object(shownVtbl,0x40u);
        const bool isPair = state.lastShown != 0u &&
            (state.lastShown==state.pairRoot || state.lastShown==state.root ||
             state.lastShown==state.popup);
        const bool evaluated = state.lastShown != 0u && state.strandAttempted==state.lastShown;
        // One guard-vector snapshot per show while anything is interesting
        // (pair dead, dialog up, or fresh show): bits P=1 D=2 F=4 L=8 I=16.
        // Tells exactly which guard blocks when no attempt fires (P35).
        if (state.lastShown != 0u && state.strandEvalLogged != state.lastShown &&
            (pairDead || dialogUp || fresh)) {
            state.strandEvalLogged = state.lastShown;
            const uint32_t vec = (pairDead?1u:0u) | (dialogUp?2u:0u) | (fresh?4u:0u) |
                (sane?8u:0u) | (isPair?16u:0u);
            log(r,"strand-eval",vec);
            log(r,"strand-eval-vt",shownVtbl);
        }
        if (ps2_quick_play_direct::qpStrandLogoffReady(true, pairDead, dialogUp, fresh,
                                                       sane, isPair, evaluated)) {
            state.strandAttempted=state.lastShown;
            const auto child=rd(ram,state.lastShown+0x69c);
            if (object(child,0x10u) && begin(ram,c,0x1c0138,50,state.lastShown,child)) {
                reg(c,6,0xf);
                state.stage=Stage::Leaving;
                log(r,"strand-logoff-attempt",state.lastShown); return;
            }
            log(r,"strand-logoff-skipped",state.lastShown);
        }
        // Part 2 (b): dialog-face strand with a LIVE pair (P42: post-accept
        // eval shows pair alive on the entry object). Fire the Part-1 shape
        // on our own live pair — the strand object itself stays untouched
        // (P26 no-op, P32 corrupt). Child prefers our filter (Part-1-exact);
        // falls back to the strand show when the filter is gone.
        if (state.strandSeen != 0u) {
            const bool pairLiveB = state.pairRoot != 0u && rd(ram,state.pairRoot+8)==0x46ad48u;
            const bool freshB = state.lastShown==state.strandSeen && now0 >= state.lastShownTick &&
                now0-state.lastShownTick <= 600u;
            const bool evalB = state.strandAttempted==state.strandSeen;
            if (ps2_quick_play_direct::qpStrandLiveLogoffReady(true, pairLiveB, dialogUp, freshB, evalB)) {
                state.strandAttempted=state.strandSeen;
                uint32_t childB = 0u;
                if (state.popup != 0u && object(state.popup,0x734u) &&
                    rd(ram,state.popup+0x20)==state.pairRoot) childB = state.popup;
                else if (object(state.strandSeen,0x6d0u)) childB = state.strandSeen;
                if (childB != 0u && begin(ram,c,0x1c0138,51,state.pairRoot,childB)) {
                    reg(c,6,0xf);
                    state.stage=Stage::Leaving;
                    log(r,"strand-logoff-b-attempt",state.pairRoot); return;
                }
                log(r,"strand-logoff-b-skipped",state.strandSeen);
            }
        }
    }
    // qp-cancel-1 Part 2: the strand was recorded by switchHook (Online Main
    // reshown while Ready). P26/P32: every 0x1c0138 route is dead here —
    // stock-Online Main Logoff no-ops, torn-root Logoff is unsafe, and the UI
    // transplant corrupts the strand into a re-dialog loop. So record only:
    // the back-out probe (P30/P33) decides the route home.
    if (state.stage==Stage::Ready && state.stranded) {
        state.stranded=0; log(r,"strand-stock");
    }
    padOriginal(ram,c,r);
    // The original pad glue is an HLE leaf. A chained Tricky refresh can
    // redirect it; do not nest another continuation over that redirect.
    if (c->pc!=returnPc || state.stage==Stage::Idle || state.stage==Stage::Failed || state.stage==Stage::Ready ||
        state.stage==Stage::Leaving)
        return;
    const auto now=tick(r);
    if (state.lastTick==now) return;
    state.lastTick=now;
    const auto auth=rd(ram,0x4a3028);
    if (state.menuScreen && rd(ram,state.menuScreen+0xd0)==state.menu &&
        (state.stage==Stage::Connect || state.stage==Stage::Auth || state.stage==Stage::Persona)) {
        wr(ram,state.menuScreen+0xd0,0); log(r,"held-menu-detached");
    }
    if (now-state.tap>1200) { state.stage=Stage::Failed; log(r,"timeout",object(auth,0x300)?rd(ram,auth):0); return; }
    if (!object(auth,0x300)) return;
    const auto phase=rd(ram,auth);
    if (state.stage==Stage::Connect && phase==1) {
        begin(ram,c,0x25c610,20,auth);
    } else if (state.stage==Stage::Auth && phase==1 && rd(ram,auth+0x6c)>0) {
        begin(ram,c,0x25cd28,21,auth,0);
    } else if (state.stage==Stage::Persona && phase==1 && rd(ram,auth+0xc)==1) {
        // Stock news-complete parcel 0x111 is what normally leaves persona.
        log(r,"session-ready");
        if (begin(ram,c,0x317d70,30,0x6d0,0x45ff28)) { reg(c,6,0); reg(c,7,0); }
    } else if (state.stage==Stage::RootQueued && rd(ram,state.root+0x40) &&
               ((rd(ram,state.root+0x1c)>>8)&63)==5) {
        begin(ram,c,0x39f290,35,state.ui+0x18,state.popup);
    } else if (state.stage==Stage::Queued && rd(ram,state.popup+0x40) &&
               ((rd(ram,state.popup+0x1c)>>8)&63)==5) {
        const auto screen=rd(ram,state.popup+0x40), ok=rd(ram,state.popup+0x6f4);
        if (!object(screen,0xe8) || !object(ok,0x60)) {
            state.stage=Stage::Failed; log(r,"filter-control-unavailable"); return;
        }
        // Stock cUIMenu::setSelected, using the filter's own TextOK
        // control. This is focus assignment, not an input event.
        uint32_t child=ok, menu=0;
        for (unsigned depth=0; depth<8 && object(child,0x60); ++depth) {
            const auto parent=rd(ram,child+0x40);
            if (!object(parent,0xa8)) break;
            const auto vt=rd(ram,parent+8);
            if (object(vt,0x40) && rd(ram,vt+0x3c)==0x39a928 &&
                rd(ram,parent+0x5c)==screen) { menu=parent; break; }
            child=parent;
        }
        if (!menu) { state.stage=Stage::Failed; log(r,"filter-menu-unavailable"); return; }
        if (rd(ram,menu+0xa0)==child && (rd(ram,child+0x14)&0x10)) {
            state.stage=Stage::Ready; log(r,"filter-ready");
        } else if (begin(ram,c,0x39ad38,36,menu,child)) reg(c,6,1);
    }
}
} // namespace
namespace ps2_quick_play_direct {
void apply(PS2Runtime &r) {
    if (!ps2_net::enabled() || ps2_net::qpNavMode()!=3) return;
    const auto *ram=r.memory().getRDRAM();
    if (!ram || rd(ram,kMain)!=0x27bdffc0 || rd(ram,kReturn)!=0) {
        std::fprintf(stderr,"[qp-direct] refused: entry words do not match\n"); return;
    }
    mainOriginal=r.lookupFunction(kMain); padOriginal=r.lookupFunction(kPad); returnOriginal=r.lookupFunction(kReturn);
    const uint32_t calls[]={0x1a86e8,0x14dc80,0x14dd58,0x145108,0x255860,0x25b650,0x260f80,0x266ca0,
                           0x255f50,0x398638,0x3f3c50,0x3184e8,0x25b6d8,0x25c610,0x25cd28,0x317d70,0x1bfbb8,0x1c0f40,0x39f400,0x39f290,0x39ad38,
                           0x1c0138,0x1c1560,0x1a8a40};
    if (!mainOriginal || !padOriginal || !returnOriginal) { std::fprintf(stderr,"[qp-direct] refused: hook unavailable\n"); return; }
    for (const auto pc:calls) if (!r.lookupFunction(pc)) {
        std::fprintf(stderr,"[qp-direct] refused: guest function unavailable pc=%x\n",pc); return;
    }
    r.replaceFunction(kPad,padHook);
    if (ps2_net::qpNavMode()==3) {
        r.replaceFunction(kMain,mainHook); r.replaceFunction(kReturn,continuation);
        closeOriginal<0x39ce48> = r.lookupFunction(0x39ce48);
        closeOriginal<0x39ce98> = r.lookupFunction(0x39ce98);
        r.replaceFunction(0x39ce48,closeHook<0x39ce48>);
        r.replaceFunction(0x39ce98,closeHook<0x39ce98>);
        drawOriginal=r.lookupFunction(0x39e6b8);
        r.replaceFunction(0x39e6b8,drawHook);
        filterOriginal=r.lookupFunction(0x1c1560); switchOriginal=r.lookupFunction(0x1a8a40);
        r.replaceFunction(0x1c1560,filterHook); r.replaceFunction(0x1a8a40,switchHook);
    }
    state={}; std::fprintf(stderr,"[qp-direct] armed: stock session calls, pad navigator bypassed\n");
}
}
