// astra-qp-direct-1: stock online session and FE constructors, without pad
// navigation. Explicit experimental value of the existing Quick Play gate.
// Guest calls use stack continuations so checkpoint unwinds remain resumable.
#include "Ssx3QuickPlayDirect.h"
#include "ps2_net_dirtysock.h"
#include "ps2_runtime.h"
#include "runtime/ps2_memory.h"
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
    uint32_t menu = 0, ui = 0, config = 0, root = 0, popup = 0, menuScreen = 0;
    uint64_t tap = 0, lastTick = 0, injections = 0, rootRetired = 0;
    std::chrono::steady_clock::time_point started{};
} state;
uint32_t rd(const uint8_t *ram, uint32_t a) {
    uint32_t v = 0; if (a <= PS2_RAM_SIZE - 4) std::memcpy(&v, ram + a, 4); return v;
}
void wr(uint8_t *ram, uint32_t a, uint32_t v) { if (a <= PS2_RAM_SIZE - 4) std::memcpy(ram+a, &v, 4); }
bool object(uint32_t p, uint32_t n) { return p >= 0x10000 && p <= PS2_RAM_SIZE - n; }
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
        if (!object(auth,0x300) || !ps2_net::qpCopyIdentity(ram,f+0x240)) {
            fail(ram,c,r,"identity-or-auth-unavailable"); break;
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
    // 0x1a8a40 replaces the top online state (Matchup, or Main Menu below);
    // after it the root only plays its close.
    if (state.root && !state.rootRetired &&
        (state.stage==Stage::Ready || state.stage==Stage::Leaving)) state.rootRetired=tick(r);
    switchOriginal(ram,c,r);
}
void filterHook(uint8_t *ram, R5900Context *c, PS2Runtime *r) {
    // Back (event 6) on the idle filter: stock notifies Online Main, which
    // pops the filter and stays active; its own Back then offers
    // "Disconnect from server", whose Yes pushes cFEStateNetAccountMem (the
    // save prompt). Take Online Main's Logoff confirmation instead,
    // 0x1c0138(root, child, 0xF): close the child, switch to a new
    // cFEStateMainMenu (whose init destroys the session, auth, game link and
    // net manager) and log off. A searching filter keeps stock Back (abort).
    const auto self=getRegU32(c,4), event=getRegU32(c,6);
    if (event==6 && state.stage==Stage::Ready && self==state.popup && object(self,0x734) &&
        !rd(ram,self+0x6ec) && object(state.root,0x6d0) && rd(ram,self+0x20)==state.root) {
        state.stage=Stage::Leaving; log(r,"back-to-main");
        reg(c,4,state.root); reg(c,5,self); reg(c,6,0xf); c->pc=0x1c0138;
        return;
    }
    filterOriginal(ram,c,r);
}
void padHook(uint8_t *ram, R5900Context *c, PS2Runtime *r) {
    const auto returnPc=getRegU32(c,31);
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
