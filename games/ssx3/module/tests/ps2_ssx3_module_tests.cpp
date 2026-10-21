#include "MiniTest.h"
#include "Ssx3LodScale.h"
#include "Ssx3TrickyLayer.h"
#include "Ssx3StockMeter.h"
#include "Ssx3Fh1Clock.h"
#include "Ssx3Fh1Dispatch.h"
#include "Ssx3Fh1Taps.h"
#include "Ssx3Fh1FixContent.h"
#include "ps2_fh1_full120.h"
#include "ps2_fh1_fix.h"
#include "Ssx3TrickyMenu.h"
#include "Ssx3TrickyRecordsPatch.h"
#include "Ssx3TrickySong.h"
#include "Ssx3TrickyState.h"
#include "ps2_snd_spike.h"
#include "Ssx3Widescreen.h"
#include "Ssx3InputMarker.h"
#include "Ssx3VisNative.h"
#include "ps2_ssx3_anim_pick_guard.h"
#include "ps2_ssx3_lod.h"
#include "ps2_ssx3_tricky_menu.h"
#include "ps2_ssx3_module.h"
#include "runtime/ps2_savestate.h"

#include <cstdlib>
#include <cstring>
#include <iterator>
#include <string>
#include <vector>

void reset_ps2_test_function_table();
namespace ps2_ssx3_input2
{
void applyInput2(PS2Runtime &runtime);
}

// module-seam-1: the SSX 3 module is the binary's linked module, and the
// runtime's override pass installs its bindings (product suite only).
namespace
{
    bool g_pickReached = false;
    bool g_playReached = false;
    void sentinelPick(uint8_t *, R5900Context *, PS2Runtime *) { g_pickReached = true; }
    void sentinelPlay(uint8_t *, R5900Context *, PS2Runtime *) { g_playReached = true; }

    void setReg(R5900Context &ctx, int reg, uint32_t v)
    {
        ctx.r[reg] = _mm_set_epi64x(0, static_cast<int64_t>(static_cast<int32_t>(v)));
    }

    struct Bound
    {
        PS2Runtime::RecompiledFunction pick;
        PS2Runtime::RecompiledFunction play;
    };

    // Sentinels at both guarded entries, then the runtime's override pass.
    Bound bindFor(PS2Runtime &runtime, const char *elf)
    {
        reset_ps2_test_function_table();
        runtime.replaceFunction(ps2_ssx3_anim_pick::kPicker, &sentinelPick);
        runtime.replaceFunction(ps2_ssx3_anim_pick::kPlay, &sentinelPlay);
        ps2_game_overrides::applyMatching(runtime, elf, 0x00100008u, 0u, false);
        return {runtime.lookupFunction(ps2_ssx3_anim_pick::kPicker),
                runtime.lookupFunction(ps2_ssx3_anim_pick::kPlay)};
    }
}

void register_ps2_ssx3_module_tests()
{
    MiniTest::Case("Ps2Ssx3GameModule", [](TestCase &tc)
                   {
        tc.Run("the SSX 3 module is linked and passes the ABI check", [](TestCase &t)
               {
            const ps2_game_module::Module *m = ps2_game_module::linkedModule();
            t.IsTrue(m != nullptr, "module linked");
            t.IsTrue(m == &ps2x_ssx3_game_module(), "linked module is the SSX 3 entry");
            t.IsTrue(ps2_game_module::installed() == m, "installed() returns it");
            t.Equals(std::string(m->name), std::string("ssx3"), "name");
            t.Equals(m->abiVersion, ps2_game_module::kAbiVersion, "abi");
        });
        tc.Run("override table: the moved families, in order, with their old match", [](TestCase &t)
               {
            const ps2_game_module::Module &m = ps2x_ssx3_game_module();
            // Product overrides retain their application order.
            const char *kExpected[] = {"ssx3-anim-pick-guard", "ssx3-hle-iop", "ssx3-copied-payload",
                                       "ssx3-sif-handshake", "ssx3-tricky-menu",
                                       "ssx3-tricky-stock-meter", "ssx3-lod-scale",
                                       "ssx3-input2", "ssx3-input-marker",
                                       "ssx3-widescreen-default", "ssx3-tricky-records-patch",
                                       "ssx3-tricky-fixed-intro", "ssx3-quick-play-direct"};
            t.Equals(m.overrideCount, std::size(kExpected), "override count");
            for (size_t i = 0; i < m.overrideCount && i < std::size(kExpected); ++i)
            {
                const ps2_game_overrides::Descriptor &d = m.overrides[i];
                t.Equals(std::string(d.name), std::string(kExpected[i]), "name");
                t.Equals(std::string(d.elfName), std::string("SLUS_207.72"), "elf");
                t.Equals(d.entry, 0x00100008u, "entry");
                t.Equals(d.crc32, 0u, "crc");
                t.IsTrue(d.apply != nullptr, "apply");
            }
            t.IsTrue(m.overrides[4].apply == &ps2_tricky_menu::applyTrickyMenu, "tricky-menu apply");
            t.IsTrue(m.overrides[5].apply == &ps2_stock_meter::applyTrickyStockMeter, "stock-meter apply");
            t.IsTrue(m.overrides[6].apply == &ps2_lod_scale::applyLodScale, "lod-scale apply");
        });
        tc.Run("the Tricky-layer tick hook points at the module (ABI 7)", [](TestCase &t)
               {
            const ps2_game_module::Module &m = ps2x_ssx3_game_module();
            t.Equals(m.abiVersion, 16u, "abi"); // split-input-1 family #3: ABI 16
            t.IsTrue(m.trickyLayerTick == &ps2_tricky_layer::tickHook, "tick hook");
            // The forward matches the inline reducer on the early-out path
            // (layer off / null / empty): no state, no crash.
            ps2_tricky_layer::tickHook(nullptr, 0u, 41u);
            t.IsTrue(true, "forward early-outs");
        });
        tc.Run("lod-scale apply wraps nothing with the knobs off", [](TestCase &t)
               {
            // snd-seam-1 Part 5 slice 3: both scales default 0 (det keys)
            // installs no bindings — the keys cannot move.
            unsetenv("PS2X_SSX3_LOD_SCALE");
            unsetenv("PS2X_SSX3_TRICKY_LOD_SCALE");
            PS2Runtime runtime;
            reset_ps2_test_function_table();
            runtime.replaceFunction(ps2_ssx3_lod::kSetPerspective, &sentinelPick);
            ps2_game_overrides::applyMatching(runtime, "SLUS_207.72", 0x00100008u, 0u, false);
            t.IsTrue(runtime.lookupFunction(ps2_ssx3_lod::kSetPerspective) == &sentinelPick,
                     "perspective setter unwrapped");
            reset_ps2_test_function_table();
        });
        tc.Run("stock-meter apply wraps nothing with the knob off", [](TestCase &t)
               {
            // snd-seam-1 Part 5 slice 2: knob default off (det keys)
            // installs no bindings — the keys cannot move.
            unsetenv("PS2X_SSX3_TRICKY_HUD");
            PS2Runtime runtime;
            reset_ps2_test_function_table();
            runtime.replaceFunction(0x21d1a0u, &sentinelPick);
            ps2_game_overrides::applyMatching(runtime, "SLUS_207.72", 0x00100008u, 0u, false);
            t.IsTrue(runtime.lookupFunction(0x21d1a0u) == &sentinelPick, "meter helper unwrapped");
            reset_ps2_test_function_table();
        });
        tc.Run("tricky-menu apply wraps nothing with the knob off", [](TestCase &t)
               {
            // snd-seam-1 Part 5: knob default off (det keys) installs no
            // bindings — the keys cannot move. Sentinels first (a bare
            // lookup materializes a function, so absence is unwrappable).
            unsetenv("PS2X_SSX3_TRICKY_MENU");
            PS2Runtime runtime;
            reset_ps2_test_function_table();
            runtime.replaceFunction(ps2_ssx3_tricky::kMainMenuItemState, &sentinelPick);
            runtime.replaceFunction(ps2_ssx3_tricky::kMapFill, &sentinelPlay);
            ps2_game_overrides::applyMatching(runtime, "SLUS_207.72", 0x00100008u, 0u, false);
            t.IsTrue(runtime.lookupFunction(ps2_ssx3_tricky::kMainMenuItemState) == &sentinelPick,
                     "menu build unwrapped");
            t.IsTrue(runtime.lookupFunction(ps2_ssx3_tricky::kMapFill) == &sentinelPlay, "map fill unwrapped");
            reset_ps2_test_function_table();
        });
        tc.Run("the SND progress hooks point at the moved HLE (ABI 5)", [](TestCase &t)
               {
            const ps2_game_module::Module &m = ps2x_ssx3_game_module();
            t.Equals(m.abiVersion, 16u, "abi"); // split-input-1 family #3: ABI 16
            t.IsTrue(m.noteSndAddCmdHandler == &ps2_snd_spike::noteAddCmdHandler, "addcmdhandler");
            t.IsTrue(m.noteSndRpc == &ps2_snd_spike::noteRpc, "rpc snoop");
            t.IsTrue(m.onSndSendCmd == &ps2_snd_spike::onSendCmd, "sendcmd");
            t.IsTrue(m.onSndSetDma == &ps2_snd_spike::onSetDma, "setdma");
            t.IsTrue(m.onSndSoundTick == &ps2_snd_spike::onSoundTick, "soundtick");
            t.Equals(m.sndTickCycles, ps2_snd_spike::kTickCycles, "tick period");
            t.Equals(m.sndTickCycles, 3145728ull, "one tick is exactly 3,145,728 EE cycles");
            t.IsTrue(m.sndSaveState == &ps2_snd_spike::saveSndState, "save provider");
            t.IsTrue(m.sndLoadState == &ps2_snd_spike::loadSndState, "load provider");
            const auto &sections = ps2_savestate::registeredSections();
            const auto snd = sections.find("snd");
            t.IsTrue(snd != sections.end(), "snd section registered by the module");
            t.Equals(snd->second.version, 1u, "snd version frozen for old saves");
            t.IsTrue(snd->second.save == &ps2_snd_spike::saveSndState, "snd save hook");
            t.IsTrue(snd->second.load == &ps2_snd_spike::loadSndState, "snd load hook");
        });
        tc.Run("the Tricky song mix hooks point at the provider (ABI 5)", [](TestCase &t)
               {
            const ps2_game_module::Module &m = ps2x_ssx3_game_module();
            t.Equals(m.abiVersion, 16u, "abi"); // split-input-1 family #3: ABI 16
            t.IsTrue(m.sndTrickySongInit == &ps2_snd_tricky_song::initFromEnvHook, "init");
            t.IsTrue(m.sndTrickySongMix == &ps2_snd_tricky_song::mixIntoHook, "mix");
            t.IsTrue(m.sndTrickySongProbe == &ps2_snd_tricky_song::probeSnapshot, "probe");
        });
        tc.Run("the input path + widescreen default point at the moved rows and hooks (ABI 10)", [](TestCase &t)
               {
            const ps2_game_module::Module &m = ps2x_ssx3_game_module();
            t.Equals(m.abiVersion, 16u, "abi"); // split-input-1 family #3: ABI 16
            t.IsTrue(m.notePadRead == &ps2_stubs::notePadRead, "pad-read tap");
            t.IsTrue(m.overrides[7].apply == &ps2_ssx3_input2::applyInput2, "input2 row");
            t.IsTrue(m.overrides[8].apply == &ps2_stubs::lim1Apply, "input-marker row");
            t.IsTrue(m.noteWidescreenDispatch == &ps2_ssx3_widescreen::noteWidescreenDispatch,
                     "dispatch enforce");
            t.IsTrue(m.widescreenAnamorphic == &ps2_ssx3_widescreen::widescreenAnamorphic,
                     "anamorphic query");
            t.IsTrue(m.overrides[9].apply == &ps2_ssx3_widescreen::applySsx3Widescreen,
                     "widescreen row");
            t.IsTrue(m.overrides[10].apply == &ps2_ssx3_tricky_records_patch::applyTrickyRecordsPatch,
                     "records-patch apply");
        });
        tc.Run("the VU0 visibility tests point at the moved provider (ABI 10)", [](TestCase &t)
               {
            const ps2_game_module::Module &m = ps2x_ssx3_game_module();
            t.Equals(m.abiVersion, 16u, "abi"); // split-input-1 family #3: ABI 16
            t.IsTrue(m.visNativeMode == &ps2_ssx3_vis_native::visNativeMode, "knob mode");
            t.IsTrue(m.visNativeMatchesPc == &ps2_ssx3_vis_native::visNativeMatchesPc, "pc match");
            t.IsTrue(m.visNativeImageMatches == &ps2_ssx3_vis_native::imageMatches, "image match");
            t.IsTrue(m.visNativeRun == &ps2_ssx3_vis_native::visNativeRun, "serve");
            t.IsTrue(m.visNativeCheckBegin == &ps2_ssx3_vis_native::visNativeCheckBegin, "check begin");
            t.IsTrue(m.visNativeCheckEnd == &ps2_ssx3_vis_native::visNativeCheckEnd, "check end");
            t.IsTrue(ps2_ssx3_vis_native::visNativeMatchesPc(0xDB8u), "0xDB8 matches");
            t.IsTrue(ps2_ssx3_vis_native::visNativeMatchesPc(0x570u), "0x570 matches");
            t.IsTrue(!ps2_ssx3_vis_native::visNativeMatchesPc(0x1000u), "other PCs pass through");
        });
        tc.Run("the TK34 patch-grow providers point at the module (ABI 6)", [](TestCase &t)
               {
            const ps2_game_module::Module &m = ps2x_ssx3_game_module();
            t.Equals(m.abiVersion, 16u, "abi"); // split-input-1 family #3: ABI 16
            t.IsTrue(m.patchGrowSave == &ps2_tricky_state::patchGrowSave, "save");
            t.IsTrue(m.patchGrowValidate == &ps2_tricky_state::patchGrowValidate, "validate");
            t.IsTrue(m.patchGrowLoad == &ps2_tricky_state::patchGrowLoad, "load");
            const auto &sections = ps2_savestate::registeredSections();
            const auto gems = sections.find("trickygems");
            t.IsTrue(gems != sections.end(), "trickygems section registered by the module");
            t.Equals(gems->second.version, 3u, "trickygems owns course and user records restore state");
            t.Equals(gems->second.minLoadVersion, 1u, "legacy default-off stock saves supported");
            t.IsTrue(gems->second.save == &ps2_tricky_state::trickyGemsSave, "gems save hook");
            t.IsTrue(gems->second.load == &ps2_tricky_state::trickyGemsLoad, "gems load hook");
            t.IsTrue(gems->second.validate == &ps2_tricky_state::trickyGemsLoad, "gems validate hook");
        });
        tc.Run("the FIX-policy content is published by the module (ABI 7)", [](TestCase &t)
               {
            const ps2_game_module::Module &m = ps2x_ssx3_game_module();
            t.Equals(m.abiVersion, 16u, "abi"); // split-input-1 family : ABI 16
            t.IsTrue(m.fh1FixPolicy != nullptr, "policy published");
            t.IsTrue(m.fh1FixPolicy == &ps2_ssx3_fh1::fixPolicy(), "policy is the moved table");
            const ps2_game_module::Fh1FixPolicy &p = *m.fh1FixPolicy;
            t.Equals(p.itemMain("rider"), static_cast<uint64_t>(ps2_fh1::kFixRider), "rider bit");
            t.Equals(p.itemMain("asymk"), static_cast<uint64_t>(ps2_fh1::kFixAsymK), "asymk bit");
            t.Equals(p.item12("flare"), static_cast<uint32_t>(ps2_fh1::kFix12Flare), "flare bit");
            t.Equals(p.itemMain("nope"), 0u, "unknown main refuses");
            t.Equals(p.item12("nope"), 0u, "unknown fh12 refuses");
            t.IsTrue(p.allMain != 0u && p.all12 != 0u, "all is non-empty");
            t.IsTrue((p.allMain & ps2_fh1::kFixRider) != 0u, "rider in all");
            t.IsTrue((p.allMain & ps2_fh1::kFixLife2) == 0u, "life2 out of all");
            t.IsTrue(p.asymkOk != nullptr && p.normcapOk != nullptr, "compat published");
        });
        tc.Run("the clock policy is published by the module (ABI 8)", [](TestCase &t)
               {
            const ps2_game_module::Module &m = ps2x_ssx3_game_module();
            t.Equals(m.abiVersion, 16u, "abi"); // split-input-1 family : ABI 16
            t.IsTrue(m.fh1SchedDivisor == &ps2_ssx3_fh1::schedDivisor, "schedDivisor");
            t.IsTrue(m.fh1VblankDivisor == &ps2_ssx3_fh1::vblankDivisor, "vblankDivisor");
            t.IsTrue(m.fh1EeClockShiftNow == &ps2_ssx3_fh1::eeClockShiftNow, "eeClockShiftNow");
            t.IsTrue(m.fh1StockHalfTicks == &ps2_ssx3_fh1::stockHalfTicks, "stockHalfTicks");
            t.IsTrue(m.fh1StockHalfTicksLegacy == &ps2_ssx3_fh1::stockHalfTicksLegacy, "stockHalfTicksLegacy");
            t.IsTrue(m.fh1StockHalfStepNext == &ps2_ssx3_fh1::stockHalfStepNext, "stockHalfStepNext");
            t.IsTrue(m.fh1OnVBlankCommit == &ps2_ssx3_fh1::onVBlankCommit, "onVBlankCommit");
            t.IsTrue(m.fh1PatchAtManagerInit == &ps2_ssx3_fh1::patchAtManagerInit, "patchAtManagerInit");
            t.IsTrue(m.fh1InputChainPoolFlip == &ps2_ssx3_fh1::inputChainPoolFlip, "flip");
            t.IsTrue(m.fh1InputChainPoolStep == &ps2_ssx3_fh1::inputChainPoolStep, "step");
            t.IsTrue(m.fh1Fh28PoolOverride == &ps2_ssx3_fh1::fh28PoolOverride, "override");
            // No FULL120 env in the test process: stock defaults end to end.
            t.Equals(ps2_fh1::vblankDivisor(), 1u, "stock divisor");
            t.Equals(ps2_fh1::schedDivisor(), 1u, "stock sched divisor");
            t.Equals(ps2_fh1::eeClockShiftNow(), 0u, "stock shift");
            t.Equals(ps2_fh1::stockHalfTicks(), 0u, "stock half ticks");
            t.Equals(ps2_fh1::stockHalfStepNext(), 2u, "stock half step");
        });
        tc.Run("the dispatch cold side is published by the module (ABI 10)", [](TestCase &t)
               {
            const ps2_game_module::Module &m = ps2x_ssx3_game_module();
            t.Equals(m.abiVersion, 16u, "abi"); // split-input-1 family : ABI 16
            t.IsTrue(m.fh1BuildHookInterest == &ps2_ssx3_fh1::buildHookInterest, "table builder");
            // End to end through the runtime wrapper: FIX=all lists the
            // census PCs and misses a bogus pair.
            ps2_fh1::HookConfig c;
            c.mode = ps2_fh1::Mode::Events;
            c.main = ps2_fh1::allMain();
            c.fix12 = ps2_fh1::all12();
            c.guestActive = true;
            const ps2_fh1::HookInterest hi = ps2_fh1::buildHookInterest(c);
            t.IsTrue(ps2_fh1::hookTableHit(hi, 0x11a3f0u, 0x000001u), "combo src hits");
            t.IsTrue(ps2_fh1::hookTableHit(hi, 0x000001u, 0x117638u), "combo tgt hits");
            t.IsFalse(ps2_fh1::hookTableHit(hi, 0x000002u, 0x000003u), "bogus pair misses");
        });
        tc.Run("the dispatch hot side is published by the module (ABI 11)", [](TestCase &t)
               {
            const ps2_game_module::Module &m = ps2x_ssx3_game_module();
            t.Equals(m.abiVersion, 16u, "abi"); // split-input-1 family : ABI 16
            t.IsTrue(m.fh1OnBranch == &ps2_ssx3_fh1::onBranch, "chain body");
        });
        tc.Run("the post-call consume side is published by the module (ABI 12)", [](TestCase &t)
               {
            const ps2_game_module::Module &m = ps2x_ssx3_game_module();
            t.Equals(m.abiVersion, 16u, "abi"); // split-input-1 family : ABI 16
            t.IsTrue(m.fh1OnReturn == &ps2_ssx3_fh1::onReturn, "post-call fixups");
            t.IsTrue(m.fh1PostUnwind == &ps2_ssx3_fh1::postUnwindRestore, "unwind restore");
        });
        tc.Run("the tap-target lists are published by the module (ABI 13)", [](TestCase &t)
               {
            const ps2_game_module::Module &m = ps2x_ssx3_game_module();
            t.Equals(m.abiVersion, 16u, "abi");
            t.IsTrue(m.fh1TapTargets == &ps2_ssx3_fh1::tapTargets(), "tap targets");
            const ps2_game_module::Fh1TapTargets &tt = ps2_ssx3_fh1::tapTargets();
            t.Equals(tt.stickTracker, 0x133308u, "stick tracker");
            t.Equals(tt.fh9RngTarget, 0x317810u, "rng target");
            t.Equals(tt.fh9GateTarget, 0x1298c8u, "gate target");
            t.Equals(tt.fh9GateSite[0], 0x10b8acu, "gate site 0");
            t.Equals(tt.fh9GateSite[3], 0x10dc30u, "gate site 3");
            t.Equals(tt.fh9RngSite[0], 0x10db3cu, "rng site 0");
            t.Equals(tt.fh9RngSite[6], 0x10b468u, "rng site 6");
            t.Equals(tt.fh9WrapTarget, 0x11fec8u, "wrap target");
            t.Equals(tt.fh9StateTarget, 0x111538u, "state target");
            t.Equals(tt.fh9WrapSite, 0x11fed0u, "wrap site");
            t.Equals(tt.fh9SubTarget, 0x312ae8u, "sub target");
            t.Equals(tt.fh9SubSite, 0x12e8e8u, "sub site");
            t.Equals(tt.fh9CrossTarget, 0x313868u, "cross target");
            t.Equals(tt.fh9ExactTarget, 0x313938u, "exact target");
            t.Equals(tt.fh9EndTarget, 0x3139a8u, "end target");
            t.Equals(tt.fh9AdvTarget, 0x3135b0u, "adv target");
        });
        tc.Run("the override pass wraps both anim entries and chains to the originals", [](TestCase &t)
               {
            PS2Runtime runtime;
            const Bound b = bindFor(runtime, "SLUS_207.72");
            t.IsTrue(b.pick != &sentinelPick, "picker wrapped");
            t.IsTrue(b.play != &sentinelPlay, "play wrapped");

            // A single-variant pick and an in-range play event run the originals.
            std::vector<uint8_t> ram(PS2_RAM_SIZE, 0u);
            constexpr uint32_t kTable = 0x00b00000u;
            const uint32_t single = 7u | (1u << 16);
            std::memcpy(ram.data() + kTable + 5u * 4u, &single, 4u);
            R5900Context pick{};
            setReg(pick, 4, kTable);
            setReg(pick, 5, 5u);
            pick.pc = ps2_ssx3_anim_pick::kPicker;
            g_pickReached = false;
            b.pick(ram.data(), &pick, &runtime);
            t.IsTrue(g_pickReached, "picker original reached");

            R5900Context play{};
            setReg(play, 5, 3u);
            play.pc = ps2_ssx3_anim_pick::kPlay;
            g_playReached = false;
            b.play(ram.data(), &play, &runtime);
            t.IsTrue(g_playReached, "play original reached");
            reset_ps2_test_function_table();
        });
        tc.Run("another ELF gets none of the module's bindings", [](TestCase &t)
               {
            PS2Runtime runtime;
            const Bound b = bindFor(runtime, "SLUS_999.99");
            t.IsTrue(b.pick == &sentinelPick, "picker untouched");
            t.IsTrue(b.play == &sentinelPlay, "play untouched");
            t.IsTrue(!runtime.hleIopMode(), "IOP mode untouched");
            reset_ps2_test_function_table();
        });
        // module-seam-2: ssx3-hle-iop (UPR1) selects the HLE IOP for SSX 3.
        tc.Run("the override pass selects HLE IOP mode for SSX 3", [](TestCase &t)
               {
            PS2Runtime runtime;
            t.IsTrue(!runtime.hleIopMode(), "emulator mode before the pass");
            bindFor(runtime, "SLUS_207.72");
            t.IsTrue(runtime.hleIopMode(), "HLE IOP mode after the pass");
            reset_ps2_test_function_table();
        }); });
}
