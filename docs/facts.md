# Verified facts

Mechanisms and gotchas established by gated testing, for contributors and
for reading engine code. Claims that still need a receipt are marked
accordingly. Guest addresses are EE virtual addresses for the supported USA
disc (`SLUS_207.72`); engine paths are relative to the pinned engine
(`deps/PS2Recomp`, see `deps.lock.json`).

## Guest (SSX 3 on the EE)

- Spin vs grab is a stock admission rule, not a conversion bug: the
  directional trick tracker `0x133308` calls the grab-stage update `0x1352a8`
  (call at `0x13356c`) *before* testing fresh spin admission. `0x1352a8`
  returns 1 iff animation channel 2's metadata class is 0x12–0x14, and a
  nonzero return takes the branch at `0x133648`, which skips new-spin
  admission. A spin started while a grab animation holds class 0x12/0x13 is
  **ignored, not queued**. Stock-60 replay of the same inputs rejects both
  spins too.
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
  = 17 × 256×256 8-bit SSH named `<CODE>.ssh`, picked by event index
  through the RAM table `0x442950` (event ≥ 17 falls back to Snow Jam).
- **Loading icon:** the pre-race card-check stage shows the loading icon; the
  flyover skip cuts that stage and clears the icon with it.
- Trick-fail/landing matches stock within half a stock frame: the ch2
  safe-to-land bit at contact `0x139c88`, same L1 release boundary in both
  modes.
- Loads are wait-bound: `sceCdRead` is instant in the runtime; the EE is idle
  70–90 % per loading frame; race load ~2.8 s.
- **Frontend `.LUI` format:** `"IYF\0"` v3.9; +0xC screen table
  `{u32 nameHash, u32 offset}` searched by `func_397CC0`; +0x10 image atlas
  (24 B entries into `<NAME>_1.SSH`); +0x14 font list. Screens are RefPack
  (`10 FB`) widget records: 23 text (locale key at +0x24), 17 sprite,
  16 group, 19 menu. `FE.LUI` loads at every frontend entry (76 screens),
  `GL.LUI` at every event load, `OV.LUI` in game, `FL.LUI` at Quit.
- The Select Event `Map` screen holds 9 fixed menus
  `Peak{1,2,3}{Race,Freestyle,Freeride}Locations` (4/4/3, 5/5/5, 8/8/6 rows);
  a nav slot without a widget crashes. The main menu's dead "Online" row
  hosts the Tricky Courses entry on Tricky builds.
- Stock world geometry re-encodes byte for byte: a converted course built as
  an overlay composite is ~1.3–1.5 MB of changed groups (every unchanged
  group byte-identical to the host world) instead of a ~113 MB world copy;
  rides are position-identical to the full-world ride.
- Pop-in is the game camera's single far plane (30000; streaming at 1.5×
  far), set at the projection setter `0x376c58`; there is no per-model
  distance LOD.
- The Aloha/Elysium halt is one mechanism: the game's GS VRAM block
  allocator (coalesce `sub_003663D8`, heap `0x876660`, 4 MiB) walks a damaged
  chain on garbage PATH1 transfers and the GS spins. The damaging write is
  not found.
- **Tricky out-of-bounds zones** redirect into the stock fall reset
  (`0x116120` → state 9 `0x12f398`, respawn near the fall point via
  `0x112d58`), not the start record; one reset per zone crossing.
- **Collision-query capacity:** the spatial query at `0x334888` keeps bounded
  inside/straddle lists; a global capacity clamp fixes the stack-overflow
  abort when a rider sits in dense converted collision.
- **Tricky course→carrier event map** (one mode per course; carrier hospitably
  supplies gates, reset paths and AI slots):

| Course | Carrier event (stock name) | Kept mode |
| --- | --- | --- |
| Garibaldi | ev0 Snow Jam | Race |
| Snowdream | ev1 Metro-City | Race |
| Elysium Alps | ev16 The Throne | Race |
| Mesablanca | ev2 Ruthless Ridge | Race |
| Merqury City | ev4 Gravitude | Race |
| Aloha Ice Jam | ev14 Happiness | Race |
| Pipedream | ev11 The Junction | Showoff |
| Untracked | ev7 Kick Doubt | Showoff |
| Tokyo Megaplex | ev15 Ruthless | Race |
| Alaska | ev6 Style Mile | Showoff |

### Full-120 (per-update terms the FIX groups convert)

SSX 3 steps most game-time state by a fixed 1/60 per update, not by a
measured dt, so running updates at 120/s plays those systems 2×. Each fix is
a named `FIX` group that halves the term only in events mode; `FIX=all`
takes every group. Gameplay is 120-only; the 60 option is plain stock 60.
Boot, menus, loading and pause stay at stock rate in `events` mode; events
mode enters at the race start (gate drop-in), not after the drop lands.

- Rider animations: 13 of the 14 step words `0x49b180…0x49b1ac` + `0x49b1b4`
  are per-update; trick bonus `[state+0x3c]` via `0x119708` → `0x1176f8`.
  In-air points match stock after both.
- Rider takeoff slew `0x49ba0c` (0.05 per update, no dt) applies to every
  rider, player included; AI `race_tick % N` gates are `aigate`.
- Boost/uber machine `0x1200d0` steps timers `R+0x2e8/+0x2ec` and the uber
  timer `R+0x2f0` (`boost`); rail handler `0x13af28` (`rail`); steering
  triple `0x113e80` (`steer`); state 9 `0x12f398` is the white-fade course
  reset (`reset`).
- Ground snap/pull `0x13c878` toward the snow, per-update: made the rider
  stick to crests. Start, acceleration, top speed, air and carving otherwise
  match stock within ~0.5–0.7 %.
- Plant/tree sway `0x38ec40` reads a stock clock instead of the render device
  frame count; particle emitter `0x370788` (`emitter`); rider FX fader,
  glint decay, board-wake scroll (`fx`). Game-side audio key-ons and
  streaming/LOD show no 2× in a race.
- The FIX inventory (each verified against the ELF word-for-word): `rider`
  (private integration dt), `countdown` (solver quantum/residual/reciprocal
  as a set), `drag` (retention square-root, additive Z steps halved),
  `event` (node dt 1/60→1/120), `slew` (bound `k→1−sqrt(1−k)`),
  `clock`/`clocksign` (wake cadence, periodic restamp with signed elapsed),
  `raceclock` (alternate-update holds), `session` (whole-session frame
  counters once per pair), `timers` (private seconds), `camera` (pool
  clocks/retentions, rooted interpolation), `launch` (wall-transition
  projection), `stick`/`stick2` (hold timers, air offset slews),
  `speedcap` (`.9/.97→sqrt`), `rng` (stock service cadence),
  `trick`/`bonus`/`bonusflip` (combo cadence, award rate, flip lifetime),
  `anim` (13 node dt words), `aigate` (modulo decisions once per pair),
  `takeoff` (fixed 0.05 bound halved), `flags` (phase delta post-call),
  `rail` (private dt; balance coefficient untouched), `reset` (1/40→1/80
  fade step), `meter` (display step), `boost` (private dt + thresholds),
  `entry` (start-gate airborne admission), `rclock` (stock-time ordinal from
  the accumulator), `emitter` (six private dt words), `fx` (glint/fader/
  scroll clocks), `clock2` (VBlank accumulator accounting),
  `hudfill`/`hudprog`/`popups` (display slews), `pose` (rider lean bounds),
  `pid` (camera controller history once per pair), `c2cap` (counter/rate
  pairing), `spawn` (glint substream stock dt under `rng`),
  `input2` (digital guard/repeat ages in half-tick units),
  `ground2` (exact piecewise steering half-map), `jcam`/`jcam2` (camera
  countdown/damping, private stock-cadence predictor for the camera query
  only), `life2` (streaming ages/deadlines, replay ordinals),
  `envfilt` (squared-gain filter transform), `spin`/`texanim`/`loops`/
  `loops2`/`pulse` (angle/UV/loop steps), `recover` (wipeout bar decay),
  `crash`/`crashbody` (solver/body dt), `fxtimer` (post-call half
  decrement), `bounce`/`particles` (phase step, whole-pass cadence),
  `uber` (display bound), `snddt` (sound-tree dt).
- Deliberately outside `all`: `ground` (halving the adhesion force would
  double-scale it — the outer `rider` dt already integrates it; `ground2`
  covers the steering term instead), `lift`, `flare` (iOS-only visual
  cadence). `query` + `vnret` + `groundstep` + `shadowpub` are a coupled
  ground feel-test package (query cadence vs publication timing), not
  default. `inputchain` (alternating pool words under a parked tracker
  phase) regressed a landing relative to stock and stays out of the play
  layer; `input2`/`stick`/`stick2` stand on their own.
- Measured matches vs stock (fixed-input scripted lines, integer game-time
  pairing): carve onset within 0.05°; rival finish 3:16 vs 3:15 with no AI
  wipeouts; crest segment within ~0.5 %; trick-fail/landing identical within
  half a stock frame; boost drain −0.005/s; white-fade reset 40.5 vs 41
  stock ticks; backcountry rivals 1.00–1.04× stock pace with rubber band
  off. Residuals under study: the bounded normal-response cap
  (`c.z=max(c.z,−40)`, applied once per update vs twice per period),
  selector-5 recovery timing, and contact cardinality at lips.
- Draw limiter `PS2X_SSX3_DRAW_HZ=60|90`: skipped updates skip the game's
  render call; the sim trace is identical, but kept frames differ slightly
  because render-path RAM is shared with update code, so it is classed
  guest-affecting.
- Pad recorder v4 stamps guest vsync ticks; the round trip is exact through
  60↔120 pairs.
- The session object (`0x1463340` region) is a state machine on
  `[session+0x0]`: `0 → 0xF → 0xA → 0xB → 0x0` (race) `→ 0xD` (results,
  within 6 ticks of the race-clock freeze, at both 60 and 120) `→ 0x1`
  (auto-retry). A pause-menu quit teardown also passes through 0xD, so
  telemetry reads the word at the stop edge, never latched.

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
  VBlank 2/3, timers 9–11 only. The CPU GS backend lacks dither, mip/LOD,
  COLCLAMP, AA1, SCANMSK: it is **not a pixel reference**; PCSX2 gsrunner on
  the captured stream is.
- **Guest heap ceiling is `0x1FD8000`** (lifted from `0x1F31000`, where HLE
  pools capped `SetupHeap`/`EndOfHeap` ~896 KB under a real kernel). Heavy
  converted worlds load within tens of KB of the old ceiling; below it the
  race load hits ENOMEM every frame and the screen stays black on the load
  frame.
- iOS pacing: the guest's 60/s is the EE scheduler's wall-clock VBlank, so
  headroom tests need `PS2X_EVENT_CLOCK=cycles`. iOS file layering: bundle <
  preset < Documents < launcher.
- Android launcher 60/120: a trampoline sets the launch mode before the env
  loader; 120 layers `files/full120.env` over `files/ps2x.env`.
- Runtime diagnostic output is line-atomic (one writer; raylib lines on the
  `ps2x` logcat tag).
- Fixed upstream defects carried in this tree: the PCSX2 draw-buffering env
  restore (stale XYOFFSET, vertex-bind overlap) and the Turnip stale
  colour-buffer state after a colour-less pass plus the sysmem-path
  `ONE_MINUS_SRC1_*` dual-source blend workaround (prefer GMEM for those
  passes).
- Post-call hooks fire only when armed: a new dedicated post slot must join
  that arm condition, or its post never runs (silent no-op, then a 2.7×
  flip).
- A savestate hashes the `PS2X_PAD_SCRIPT` env string (`@<path>`), not the
  script contents, so a state refuses to load under a moved script path.
  Keep the path.

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
- Matched-load comparisons: save past any contact, at least a few ticks
  before the window of interest, and pair by game time (the game-time ordinal), never
  by script tap number. The first events update after loading a stock
  mid-race state runs mixed (rider dt 1/60, steer at the 120 step), so a
  ~1-update phase error near a knife edge is a harness artifact, not a
  conversion bug.

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
- The panel's "120 Hz" may actually be ~121.7 Hz (8.215 ms). Guest pacing at
  119.88 drifts past it; a vsync lock paces guest VBlank on the compositor
  latch grid (holds drift, +1.5 % game speed; menus 60.9). Verify with
  `dumpsys SurfaceFlinger` before quoting smoothness numbers. A display that
  dozed can stick the compositor at 60 Hz until a power cycle; check the
  VSync tracker first when numbers halve overnight.
- Under the lock, the audio stretcher toggles ~0.7/s; a tempo mode holds a
  constant +1.5 % tempo with pitch unchanged (0 toggles, 0 dropouts).
- **Upscale:** 2× / native 2.41× fit 120 on Adreno GPUs (race p99 4.2 /
  5.3 ms); native/3× show a cliff-mesh seam no PCSX2 half-pixel mode
  removes; AF16 costs +0.37 ms at 2×; colour matches PCSX2 defaults within
  ~1 %.
- Turnip pipeline cache persists on-device; warm replays show no pipeline
  creates. Cold stalls are pipe/shader compiles.
- "Thermal 3" is a reported status, not a clock cap: no cooling device
  engages and clocks stay at the performance floors. Small losses (~0.987×
  per 10 min) come from heavy stretches where the EE critical path exceeds
  8.33 ms.
- MTVU tails are content bursts (ice-cave section) plus a busy-span
  accounting artifact, not JIT compiles (0 in the race). Offline microVU on
  Android is slower than the JIT per frame.
- **adb in shell loops:** `adb shell` reads stdin, so inside `… | while read`
  it swallows the rest of the list. Use `adb shell -n …` (and `</dev/null`
  on `adb push`) in loops.

## iOS

- Launch-env keys override the bundled `ps2x.env` only for `PS2X_*` names; a
  launch `GE1_*` key silently loses to the file value. Edit the staged
  bundle env to change GE1 keys.
- iOS file layering: bundle < preset < Documents < launcher.
- iOS display-sized export (`PS2X_GE1_EXPORT_SIZE=display`): the exact 16:9
  game rect drawn 1:1; 2× fits the phone GPU, higher scales don't. On iPad,
  non-power-of-two scales disable the two-pass vertex kernel, so GS-side
  numbers from iPad profiles overstate the phone's GS cost; a
  `GE1_GKV1_SHIFT0`-style exact fix restores it.
- iOS bundled default = offline microVU + GIF/VIF stages.
- **Never uninstall the app** (it wipes Documents: cards, env, logs); upgrade
  with a plain install.
- Pinned raylib `GetWindowScaleDPI()` returns 1; a DPI patch fixes the
  viewport. Without an iPad device family the app runs in an iPhone-compat
  portrait window.

## PCSX2 reference

- VU1 startPCs are in 8-byte units: PCSX2 `0x0/0x2/0x8/0x73` = ours
  `0x0/0x10/0x40/0x398`. Healthy VU1 programs: ≤ 2,090 cycles at Select
  Character, race max 23,540.
- PCSX2 runs zero VU0 micro-programs and zero VIF0 words at settled Select
  Character.
- Our GS stream converts to `.gs` for PCSX2 gsrunner; that is the pixel
  reference, not the CPU backend.
- Healthy Snow Jam load: ENTER → race in ~109 emu-s, one continuous
  990-iteration `_sceCdSC` loop, 1.75 MB SPU voice upload.

## Valid A/B rules

- Under any cap, speed hides wins as idle — report CPU-ms/guest-frame or busy
  fraction, never just wall speed.
- Test APKs must match the play recipe (PGO + pinned libraries): an APK
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

