// module-seam-1: the SSX 3 game module. Families move here from the runtime
// one at a time (docs: games/ssx3/module/README.md); each keeps its override
// name, ELF match and apply function. Table order is application order.
#include "ps2_ssx3_module.h"
#include "Ssx3Fh1Clock.h"
#include "Ssx3Fh1Dispatch.h"
#include "Ssx3Fh1Taps.h"
#include "Ssx3Fh1FixContent.h"
#include "Ssx3CopiedPayload.h"
#include "Ssx3SifHandshake.h"
#include "Ssx3LodScale.h"
#include "Ssx3StockMeter.h"
#include "Ssx3TrickyLayer.h"
#include "Ssx3TrickyMenu.h"
#include "Ssx3QuickPlayDirect.h"
#include "Ssx3TrickyRecordsPatch.h"
#include "Ssx3TrickyIntro.h"
#include "Ssx3TrickySong.h"
#include "Ssx3TrickyState.h"
#include "ps2_snd_spike.h"
#include "Ssx3Widescreen.h"
#include "Ssx3InputMarker.h"
#include "Ssx3VisNative.h"
#include "ssx3_module_internal.h"
#include "runtime/ps2_savestate.h"

#include <iterator>

namespace
{
    constexpr uint32_t kSsx3Entry = 0x00100008u;

    const ps2_game_overrides::Descriptor kOverrides[] = {
        {"ssx3-anim-pick-guard", "SLUS_207.72", kSsx3Entry, 0u, &ps2_ssx3_anim_pick::applyAnimPickGuard},
        {"ssx3-hle-iop", "SLUS_207.72", kSsx3Entry, 0u, &ps2_ssx3_hle_iop::applySsx3HleIop},
        {"ssx3-copied-payload", "SLUS_207.72", kSsx3Entry, 0u, &ps2_syscalls::applySsx3CopiedPayload},
        {"ssx3-sif-handshake", "SLUS_207.72", kSsx3Entry, 0u, &ps2_syscalls::applySsx3SifHandshake},
        {"ssx3-tricky-menu", "SLUS_207.72", kSsx3Entry, 0u, &ps2_tricky_menu::applyTrickyMenu},
        {"ssx3-tricky-stock-meter", "SLUS_207.72", kSsx3Entry, 0u, &ps2_stock_meter::applyTrickyStockMeter},
        {"ssx3-lod-scale", "SLUS_207.72", kSsx3Entry, 0u, &ps2_lod_scale::applyLodScale},
        {"ssx3-input2", "SLUS_207.72", kSsx3Entry, 0u, &ps2_ssx3_input2::applyInput2},
        {"ssx3-input-marker", "SLUS_207.72", kSsx3Entry, 0u, &ps2_stubs::lim1Apply},
        {"ssx3-widescreen-default", "SLUS_207.72", kSsx3Entry, 0u,
         &ps2_ssx3_widescreen::applySsx3Widescreen},
        {"ssx3-tricky-records-patch", "SLUS_207.72", kSsx3Entry, 0u,
         &ps2_ssx3_tricky_records_patch::applyTrickyRecordsPatch},
        {"ssx3-tricky-fixed-intro", "SLUS_207.72", kSsx3Entry, 0u,
         &ps2_ssx3_tricky_intro::applyTrickyFixedIntro},
        {"ssx3-quick-play-direct", "SLUS_207.72", kSsx3Entry, 0u, &ps2_quick_play_direct::apply},
    };

    // K1: the copied GetEntryAddress/TLB payload (Ssx3CopiedPayload.h). Both
    // hooks are no-ops until "ssx3-copied-payload" arms the quirk.
    // P1ac: the SIF ready-handshake (Ssx3SifHandshake.h). The hook is a
    // no-op until "ssx3-sif-handshake" arms the quirk.
    // snd-seam-1 family A: the SSX 3 SND progress hooks (ps2_snd_spike.h).
    // Always on for SSX 3 (the HLE runs on guest time with sound on or
    // off); the SND savestate providers follow in the same family.
    // snd-seam-1 family B: the SND savestate section lives here now (name,
    // version, registration). Same static-registration pattern as K1's
    // "syscalls:k1": this TU is always linked (it defines the module
    // entry), and section order is key-sorted, so registration timing is
    // inert. The version stays 1 (was ps2_savestate::kSndVersion): old
    // saves keep loading through the unchanged provider bodies.
    const bool kSndSavestateRegistered = ps2_savestate::registerSection(
        "snd", {1u, &ps2_snd_spike::saveSndState, &ps2_snd_spike::loadSndState, nullptr});
    const ps2_game_module::Module kModule{ps2_game_module::kAbiVersion,
                                          "ssx3",
                                          kOverrides,
                                          std::size(kOverrides),
                                          &ps2_syscalls::noteSsx3CopiedPayloadInstall,
                                          &ps2_syscalls::tryDispatchSsx3CopiedPayload,
                                          &ps2_syscalls::onSsx3SifSendCmd,
                                          &ps2_snd_spike::noteAddCmdHandler,
                                          &ps2_snd_spike::noteRpc,
                                          &ps2_snd_spike::onSendCmd,
                                          &ps2_snd_spike::onSetDma,
                                          &ps2_snd_spike::onSoundTick,
                                          ps2_snd_spike::kTickCycles,
                                          &ps2_snd_spike::saveSndState,
                                          &ps2_snd_spike::loadSndState,
                                          &ps2_snd_tricky_song::initFromEnvHook,
                                          &ps2_snd_tricky_song::mixIntoHook,
                                          &ps2_snd_tricky_song::probeSnapshot,
                                          &ps2_tricky_state::patchGrowSave,
                                          &ps2_tricky_state::patchGrowValidate,
                                          &ps2_tricky_state::patchGrowLoad,
                                          &ps2_tricky_layer::tickHook,
                                          &ps2_ssx3_fh1::fixPolicy(),
                                          &ps2_ssx3_fh1::schedDivisor,
                                          &ps2_ssx3_fh1::vblankDivisor,
                                          &ps2_ssx3_fh1::eeClockShiftNow,
                                          &ps2_ssx3_fh1::stockHalfTicks,
                                          &ps2_ssx3_fh1::stockHalfTicksLegacy,
                                          &ps2_ssx3_fh1::stockHalfStepNext,
                                          &ps2_ssx3_fh1::onVBlankCommit,
                                          &ps2_ssx3_fh1::applyWords,
                                          &ps2_ssx3_fh1::patchAtManagerInit,
                                          &ps2_ssx3_fh1::inputChainPoolFlip,
                                          &ps2_ssx3_fh1::inputChainPoolStep,
                                          &ps2_ssx3_fh1::fh28PoolOverride,
                                          &ps2_ssx3_fh1::buildHookInterest,
                                          &ps2_ssx3_fh1::onBranch,
                                          &ps2_ssx3_fh1::onReturn,
                                          &ps2_ssx3_fh1::postUnwindRestore,
                                          &ps2_ssx3_fh1::tapTargets(),
                                          &ps2_stubs::notePadRead,
                                          &ps2_ssx3_widescreen::noteWidescreenDispatch,
                                          &ps2_ssx3_widescreen::widescreenAnamorphic,
                                          &ps2_ssx3_vis_native::visNativeMode,
                                          &ps2_ssx3_vis_native::visNativeMatchesPc,
                                          &ps2_ssx3_vis_native::imageMatches,
                                          &ps2_ssx3_vis_native::visNativeRun,
                                          &ps2_ssx3_vis_native::visNativeCheckBegin,
                                          &ps2_ssx3_vis_native::visNativeCheckEnd};
}

const ps2_game_module::Module &ps2x_ssx3_game_module() noexcept
{
    return kModule;
}
