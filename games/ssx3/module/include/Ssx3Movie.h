#pragma once

#include "ps2_stubs.h"

// P7: SSX3 EA-movie (`.mpc`) HLE completion stub.
//
// Survey (P7 REPORT): the 14 EA intro movies under `data/movies/*.mpc` are
// played by EA's statically-linked RCMP player (cluster `0x3B0340-0x3B3308`),
// which demuxes the MPC container itself and decodes through libmpeg
// (`sceMpegInit/Create/AddBs/AddCallback/GetPicture`, already HLE-wired in
// `games/ssx3/ssx3.toml`). The boot sequencer `sub_001A1CE8` selects one
// movie per pass from the pointer table at `0x441248` and dispatches it via
// `sub_002534A8@0x2534A8`.
//
// These handlers report successful completion for a hooked movie function.
// They are *not* wired yet: the boot ladder has not reached any movie stage,
// so no hook address is runtime-verified. A future brief wires one by adding
// the handler name to `PS2_STUB_LIST` (`ps2xRuntime/include/ps2_call_list.h`)
// and a `"<name>@0x<addr>"` entry to the TOML `stubs` list, then re-recomps.
//
// Hook candidates (ranked; addresses are EE VAs in SLUS_207.72):
//   1. `sub_002534A8@0x2534A8` + ssx3MoviePlayComplete: per-movie play
//      dispatch, `$a1[0]` = movie-path pointer, real body returns 1.
//   2. Any poll-style "is movie done?" entry + ssx3MoviePollComplete:
//      returns 1 (done), matching the `sceMpegIsEnd` true convention.
// Do NOT hook `sub_001A1CE8@0x1A1CE8` itself: it does non-movie work around
// the table reads.
//
// First-firing receipt: grep the boot log for `[ssx3movie]`; the first line
// carries `n=1`.
//
// Future decoder attach point: keep these stubs for the boot ladder, then
// replace the MPC *demux* (not decode) — either teach `Stubs/MPEG.cpp`'s
// FFmpeg path the EA `MPCh` container or pre-demux MPC to MPEG-2 ES and feed
// the existing `sceMpegAddBs`/`sceMpegGetPicture` HLE (ffprobe reads all 14
// files as `ea` / `mpeg2video` 512x448 @ 29.97 fps).

namespace ps2_stubs
{
    // Blocking-play hook: logs one line, returns 1 (success).
    void ssx3MoviePlayComplete(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime);
    // Poll hook: logs one line, returns 1 (done).
    void ssx3MoviePollComplete(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime);

    // Test hooks (mirror the resetMpegStubState pattern).
    void resetSsx3MovieStubState();
    uint64_t ssx3MovieStubInvocations();
}
