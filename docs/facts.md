# Verified facts

Mechanisms and gotchas established by gated testing, for contributors and
for reading engine code. Claims that still need a receipt are marked
accordingly. Guest addresses are EE virtual addresses for the supported USA
disc (`SLUS_207.72`); engine paths are relative to the pinned engine
(`deps/PS2Recomp`, see `deps.lock.json`).

## Guest (SSX 3 on the EE)

- Music is EE-mixed PCM: EA-XA decoded and mixed on the EE at 36 kHz stereo,
  384 frames per sound tick in tag 1; the sound tick is 93.75 Hz of guest
  time. **The tag-1 payload is planar:** 384 s16 for one channel, then 384
  for the other; the first block feeds the SPU2 **right** input. SNDDRV
  resamples 3→4 (linear, phase 0.5 from the saved last sample) to 48 kHz.
- Movie end: the guest reads `[[mpeg+0x40]+0]` at `0x402b38`; the MPEG HLE
  writes the `sceMpegIsEnd` word.
- Widescreen: options block `0x535610` bits 20–21 (0 off / 1 16:9 /
  2 anamorphic), applied by `0x228c08` → vtable `0x377950`; profile loads
  re-apply via `0x152bb0`.
- Load game issues GetDir `/BASLUS-20772-GAM*` at tick 1740 on an empty card.
- Memory card: the runtime `mc0` is a host directory (`PS2X_MC_ROOT`, else
  `<elf dir>/mc0`); GetDir synthesizes attributes and uses host mtime. SSX 3
  autoloads `BASLUS-20772-SET*`/`GAM*` at boot (ticks ~118–373), which delays
  the title ~200 ticks.
- SSX 3 never calls `SetGsCrt` (syscall 0x02); the `sceGsResetGraph` stub
  applies NTSC SMODE1 `0x740814504`.
- SSX 3 uses UCAB (`0x30000000`) for DMA-bound data: a trace fold of
  `& 0x1FFFFFFF` misses those stores.
- Missing indirect-call targets: `PS2X_MISSING_FUNCTION_POLICY` (dev `stop`,
  release `continue`) plus counters; add targets via `extra_function_starts`
  in `games/ssx3/ssx3.toml`.
- `DATA/CONFIG/SLUSOVF.BIG` is a BIGF archive of `overlay.dat` (a
  relocatable MIPS EE code overlay) + `config.dat`. The game's file table is
  qsort-ed with comparator `0x3E3968`; with 32-bit sign branches, bsearch
  misses `banks.inf` and 5 other files, so no SFX banks load.
- **Disc names:** SSX 3 walks the disc's own ISO9660 tree at boot
  (`cdfs_build_toc` `0x3E4040`, recursive `0x3E3758`) and indexes paths by a
  backwards `h*33+c` hash (seed `0x1505`, qsort cmp `0x3E3968`); maxFiles
  500, 163 files. Loose host files are invisible: new files need directory
  records, which `PS2X_CD_OVERLAY` adds (a runtime virtual directory,
  pseudo-LBNs past the image).
- **World archive per event:** the world loader ignores event 0's `archive`
  field alone; a manifest `archive` row per event makes the whole world
  stream from the overlay. Tricky mode = per-event archive/name rows +
  same-length RAM string pokes.
- **Loading-screen picture:** the setup copies its COURSPIC path at every
  event load (`0x233180`, ELF string `data/ui/courspic.big`); `COURSPIC.BIG`
  = 17 × 256×256 8-bit SSH named `<CODE>.ssh`.
- Trick-fail/landing matches stock within half a stock frame: the ch2
  safe-to-land bit at contact `0x139c88`, same L1 release boundary in both
  modes.
- Loads are wait-bound: `sceCdRead` is instant in the runtime; the EE is idle
  70–90 % per loading frame; race load ~2.8 s.

### Full-120 (per-update terms the FIX groups convert)

SSX 3 steps most game-time state by a fixed 1/60 per update, not by a
measured dt, so running updates at 120/s plays those systems 2×. Each fix is
a named `FIX` group that halves the term only in events mode; `FIX=all`
takes every group. Gameplay is 120-only; the 60 option is plain stock 60.

- Rider animations: 13 of the 14 step words `0x49b180…0x49b1ac` + `0x49b1b4`
  are per-update; trick bonus `[state+0x3c]` via `0x119708` → `0x1176f8`.
  In-air points match stock after both.
- Rider takeoff slew `0x49ba0c` (0.05 per update, no dt) applies to every
  rider, player included; AI `race_tick % N` gates are `aigate`.
- Boost/uber machine `0x1200d0` steps timers `R+0x2e8/+0x2ec` and the uber
  timer `R+0x2f0` (`boost`); rail handler `0x13af28` (`rail`); steering
  triple `0x113e80` (`steer`); state 9 `0x12f398` is the white-fade course
  reset (`reset`).
- Ground snap/pull `0x13c878` toward the snow, per update: made the rider
  stick to crests. Start, acceleration, top speed, air and carving otherwise
  match stock within ~0.5–0.7 %.
- Plant/tree sway `0x38ec40` reads a stock clock instead of the render device
  frame count; particle emitter `0x370788` (`emitter`); rider FX fader,
  glint decay, board-wake scroll (`fx`). Game-side audio key-ons and
  streaming/LOD show no 2× in a race.
- Events mode enters at the race start (gate drop-in), not after the drop
  lands (`entry`).
- Draw limiter `PS2X_SSX3_DRAW_HZ=60|90`: skipped updates skip the game's
  render call; the sim trace is identical, but kept frames differ slightly
  because render-path RAM is shared with update code, so it is classed
  guest-affecting.

## Runtime semantics and builds

- FP model: `-ffp-contract=off` (clang fuses MADD otherwise); EE thread RTZ +
  FZ like PCSX2.
- `long double` is quad soft-float on arm64 Android (plain double on Mac);
  VU1 uses `VuWide=double`.
- Speed builds need `PS2X_ENABLE_DIAG_TAPS=OFF`: with taps, guest `.text` is
  3.2× and every store calls a tap.
- Bionic `setjmp` saves the signal mask (a syscall per scheduler transfer);
  `PS2X_EE_JMP_NOMASK` skips it.
- Live presents are torn at wall-clock-racy cut points; CPU-backend VRAM is a
  pure function of the GS stream, so gate GS changes by capture + replay.
- `PS2X_FRAME_DUMP_ONCE_TICKS` accepts three entries.
- Replay and `ps2x_tests` are desktop-only; Android sets
  `PS2X_BUILD_TEST=OFF`.
- Mac boots run GE1 (extracted PCSX2 GS) on MoltenVK; the det hash is
  renderer-independent with readback off. Speed boots are unpaced: ratios > 1
  mean headroom.
- Models the fixes rely on: PATH3 data queued while masked is released one
  EOP packet per `MSKPATH3 0` window; DMA completes at the CHCR store; VU1
  runs with a 65,536-cycle budget per program, VU0 4,096; INTC dispatched:
  VBlank 2/3, timers 9–12 only. The CPU GS backend lacks dither, mip/LOD,
  COLCLAMP, AA1, SCANMSK: it is **not a pixel reference**; PCSX2 gsrunner on
  the captured stream is.

## Determinism gate

- Free-running boots aren't frame-deterministic (the RNG was seeded from host
  time via `sceCdReadClock`); use `PS2X_DETERMINISTIC=1` (fixed RTC,
  guest-cycle event order) and the VBlank XXH64 tap for A/B. See
  `tests/det/README.md`.
- Det mode and memory cards: card-file mtimes reached guest RAM via
  `sceMcGetDir`, so det boots with freshly copied cards diverged; under
  `PS2X_DETERMINISTIC=1` the dates come from the fixed CD RTC calendar.
- A savestate's `pad_prefix_sha` hashes the `PS2X_PAD_SCRIPT` env string
  (`@<path>`), not the script contents, so a state refuses to load under a
  moved script path. Keep the path.
- The four det keys (`reference`, `play`, `full120events`, `play120`) must be
  IDENTICAL after any guest-affecting change; a new key needs sign-off, never
  silent re-minting.

## Android / GPU

- paraLLEl-GS's hierarchical binner mis-bins at wave128 on Adreno; the fork
  forces wave64. Mac vs Adreno hardware bilinear rounding differs by ±1–2
  LSB.
- Ship Turnip on Adreno: the proprietary driver loses composite draws,
  varying per run. The app namespace lacks `libhardware.so`, so Turnip needs
  an app-local shim whose `hw_get_module` returns `-ENOENT`.
- Android input: raylib reads keyboard and gamepad together; `input keyevent
  BUTTON_*` never reaches the game.
- Android stdout→logcat keeps at most 1,023 chars per record, and drops
  lines on big bulk flushes: pull on-device output files instead.
- **Upscale:** 2× / native 2.41× fit 120 on Adreno GPUs (race p99 4.2 /
  5.3 ms); native/3× show a cliff-mesh seam no PCSX2 half-pixel mode
  removes; AF16 costs +0.37 ms at 2×; colour matches PCSX2 defaults within
  ~1 %.
- Turnip pipeline cache persists on-device; warm replays show no pipeline
  creates. Cold stalls are pipe/shader compiles.
- **Display-rate caveat:** some 120 Hz Android panels actually run at
  ~121.7 Hz. Guest pacing at 119.88 drifts past such a panel; verify with
  `dumpsys SurfaceFlinger` before quoting smoothness numbers.
- **adb in shell loops:** `adb shell` reads stdin, so inside `… | while read`
  it swallows the rest of the list. Use `adb shell -n … </dev/null` in loops.

## iOS

- Launch-env keys override the bundled `ps2x.env` only for `PS2X_*` names; a
  launch `GE1_*` key silently loses to the file value. Edit the staged
  bundle env to change GE1 keys.
- iOS file layering: bundle < preset < Documents < launcher.
- iOS display-sized export (`PS2X_GE1_EXPORT_SIZE=display`): the exact 16:9
  game rect drawn 1:1; 2× fits the phone GPU, higher scales don't.
- **Never uninstall the app** (it wipes Documents: cards, env, logs); upgrade
  with a plain install.

## PCSX2 reference

- T48 VU1 startPCs are in 8-byte units: PCSX2 `0x0/0x2/0x8/0x73` = ours
  `0x0/0x10/0x40/0x398`. Healthy VU1 programs: ≤ 2,090 cycles at Select
  Character, race max 23,540.
- PCSX2 runs zero VU0 micro-programs and zero VIF0 words at settled Select
  Character.
- Our GS stream converts to `.gs` for PCSX2 gsrunner (see the engine's
  `ps2xTest` tooling); that is the pixel reference, not the CPU backend.

## Valid A/B rules

- Under any cap, speed hides wins as idle — report CPU-ms/guest-frame or busy
  fraction, never just wall speed.
- Lane APKs must match the play recipe (PGO + pinned libraries): an APK
  built without PGO or with different libraries measured +1.2 ms GameThread
  on identical code. Valid designs: same APK with a knob A/B, or both arms
  built with the play recipe from the same driver.
- In full-120 races the pad clock counts real 60 Hz present vsyncs while tap
  ticks run at 120 Hz; race-phase pad entries are wall-ms, so one pad fires
  at the same race moment in stock 60 and every 120 mode.
- Receipts pin effective config + binary identity (requested vs effective
  flags, module SHAs); verdict-grade runs get an exclusive host/device.
- Guest hashes do not prove graphics/audio output or cost: pair them with
  fixed captures and relevant audio outputs.
