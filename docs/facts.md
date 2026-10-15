# Verified facts

Mechanisms and gotchas established by gated lanes, for writing briefs and reading code. Each
line names its source lane (`local/research/<ID>/`; full index: `local/research/DOC2/proposed/lanes.md`).
Standing rules live in `AGENTS.md`; orchestration lessons in `docs/orchestration.md`.
Dropped lines (retired paths, landed fixes) live in git.

## Guest (SSX 3 on the EE)

- Music is EE-mixed PCM: EA-XA decoded and mixed on the EE at 36 kHz stereo, 384 frames per
  sound tick in tag 1; the sound tick is 93.75 Hz of guest time. **The tag-1 payload is planar:**
  384 s16 for one channel, then 384 for the other; the first block feeds the SPU2 **right** input.
  SNDDRV resamples 3→4 (linear, phase 0.5 from the saved last sample) to 48 kHz. At Select Character
  PCSX2's final output is exactly that, with no IOP-mixer, voice or reverb content. (AU8; AU2–AU7
  read it as interleaved, which is wrong.) The tag-1 record `0x512E40` sits inside DMA buffer
  `0x512B80`. (AU2, AU4)
- Movie end: the guest reads `[[mpeg+0x40]+0]` at `0x402b38`; the MPEG HLE writes the
  `sceMpegIsEnd` word. `0x548840` is the single startup-movie codec pool node. (E48, E49)
- Widescreen: options block `0x535610` bits 20–21 (0 off / 1 16:9 / 2 anamorphic), applied by
  `0x228c08` → vtable `0x377950`; profile loads re-apply via `0x152bb0`. (W1)
- Load game issues GetDir `/BASLUS-20772-GAM*` at tick 1740 on an empty card. (E55D14)
- Memory card: our `mc0` is a host directory (`PS2X_MC_ROOT`, else `<elf dir>/mc0`); GetDir synthesizes
  attributes and uses host mtime. SSX 3 autoloads `BASLUS-20772-SET*`/`GAM*` at boot (ticks ~118–373),
  which delays the title ~200 ticks. PS2 card images decode as 528-byte pages, 2-page clusters,
  physical = FAT cluster + alloc offset (41 on Brad's card). (E55D16)
- SSX 3 never calls `SetGsCrt` (syscall 0x02); the `sceGsResetGraph` stub applies NTSC SMODE1
  `0x740814504`. `SMODE2=0x1` field bob → `PS2X_DEINTERLACE=weave` default. (GB3, E32)
- SSX 3 uses UCAB (`0x30000000`) for DMA-bound data: a trace fold of `& 0x1FFFFFFF` misses those
  stores. (T58, T59)
- Missing indirect-call targets: `PS2X_MISSING_FUNCTION_POLICY` (dev `stop`, release
  `continue`) plus counters; add targets via `extra_function_starts` in `games/ssx3/ssx3.toml`.
  The Ghidra sweep CSV can merge getter clusters into one function (e.g. `0x396b40` inside
  `sub_00396958`; `0x3c9520`/`0x3c95f0` inside `sub_003C9420`). (E46, E56, AU3)

- `DATA/CONFIG/SLUSOVF.BIG` is a BIGF archive of `overlay.dat` (0x1903ac B, a relocatable MIPS
  ELF: EE code overlay) + `config.dat`. It was unreachable before AU9's `0x3E3968` fix (the
  file-table bsearch missed it); whether the route now loads/executes it is CT1's question.
- The game's file table is qsort-ed with comparator `0x3E3968` (`dsubu` + `bltz/bgtz`); with
  32-bit sign branches, bsearch misses `banks.inf` and 5 other files, so no SFX banks load. (AU9)

## Runtime semantics and builds
- On the Odin at 120 Hz events, `odin_run.py`'s `race hitches >50/>100` (stitched SurfaceFlinger `--latency` polls) counts the holes between poll dumps on a ≈ 5 s grid, not stalls. Quote the within-dump max interval and the app's `[perf] maxgap_ms` instead. (OPK1)
- On iOS, launch-env keys override the bundled `ps2x.env` only for `PS2X_*` names; a launch `GE1_*` key (e.g. `GE1_UPSCALE=2`) silently loses to the file value. Edit the staged bundle env to change GE1 keys for a leg. (HT1)

- FP model: `-ffp-contract=off` (clang fuses MADD otherwise); EE thread RTZ + FZ like PCSX2.
  Before E53's IEEE scope, PATH1 was rasterized under the VU core's RTZ rounding. (E52, E53, GB4)
- `long double` is quad soft-float on arm64 Android (plain double on Mac); VU1 uses
  `VuWide=double`. (E45)
- Speed builds need `PS2X_ENABLE_DIAG_TAPS=OFF`: with taps, guest `.text` is 3.2× and every
  store calls a tap. (N5)
- Determinism: free-running boots aren't frame-deterministic (the RNG was seeded from host time
  via `sceCdReadClock`); use `PS2X_DETERMINISTIC=1` (fixed RTC, guest-cycle event order) and the
  VBlank XXH64 tap for A/B. (E55A2, E55B2, E55C2)
- Live presents are torn at wall-clock-racy cut points; CPU-backend VRAM is a pure function of
  the GS stream, so gate GS changes by capture + replay. (GB2, GB3)
- `PS2X_FRAME_DUMP_ONCE_TICKS` accepts three entries; gfx-stats `top` is (FRAME.fbp,
  PRIM.type). Runtime output is line-atomic from fork `d538412`. (GB6, E47, LG1)
- Replay and `ps2x_tests` are desktop-only; Android sets `PS2X_BUILD_TEST=OFF`. (N8D7M12)

- Models the fixes rely on (RR1/RV3): PATH3 data queued while masked is released one EOP packet per
  `MSKPATH3 0` window; the GIF arbiter still stable-sorts a drain by path (GA1 measures
  inversions); DMA completes at the CHCR store; chain walker has a runaway guard, not a 4096 cap;
  VU1 runs with a 65,536-cycle budget per program (cap hits = a bug, PCSX2 max 23,540), VU0 4,096;
  INTC dispatched: VBlank 2/3, timers 9–12 only. The CPU GS backend lacks dither, mip/LOD,
  COLCLAMP, AA1, SCANMSK: it is **not a pixel reference**; PCSX2 gsrunner on our stream is. (RV3)
- Mac boots run GE1 (extracted PCSX2 GS) on MoltenVK (below); det-hash is
  renderer-independent with readback off. Speed boots are unpaced: ratios > 1 mean headroom. (GB8, F1, CN2a)
- Reference equivalence: what stays Odin-only is subgroup width (Apple 32 vs Adreno wave64;
  can't be forced either way), descriptor buffers, Turnip driver CPU cost/thermals, Adreno
  bilinear rounding. (GB9)

- Lab save states: a state's `pad_prefix_sha` hashes the `PS2X_PAD_SCRIPT` env string (`@<path>`), not the
  script contents, so a state refuses to load under a moved script path (`savestate.cpp:363`). Keep the path. (FH18)
- Wipeout recovery: state 8 `0x12cb68` adds +0.2834 per press of action `0x11` (square in the FH12 lab), and the
  same holds in full-120 events with `FIX=all`. Circle does nothing in either mode. (FH18)

- Mac det/speed boots run on GE1 (extracted PCSX2 GS) on MoltenVK: `PS2X_GS_BACKEND=external` + the pinned GE1 dylib. The det hash
  is renderer-independent with readback off (paraLLEl, CPU and GE1 all IDENTICAL). The GE1 savestate carries the armed l2h FIFO
  (blob v3). Mac GE1 is ~2× slower than paraLLEl in the heavy All-Peak race (GS-bound); det boots are ≤ 1.4× (GE1S follows up).
  The CPU GS backend is det-identical but 6–8× slower. (CN2a, CN2b)

## Odin / Android / GPU

- paraLLEl-GS's hierarchical binner mis-bins at wave128 on Adreno (Apple never uses it); the
  fork forces wave64. Mac vs Adreno hardware bilinear rounding differs by ±1–2 LSB. (N8X1)
- Ship Turnip: the proprietary Adreno driver loses composite draws, varying per run. Turnip HAL:
  `hw_device_t` has `reserved[12]`; `GetInstanceProcAddr` is at offset 136. The app namespace
  lacks `libhardware.so`, so Turnip needs an app-local shim whose `hw_get_module` returns
  `-ENOENT`. (G40–G43, N8B2–N8C2)
- Android input: raylib reads keyboard and gamepad together; inject with `sendevent` on event8
  (gamepad) / event5 (keyboard); `input keyevent BUTTON_*` never reaches the game. (N6)
- simpleperf on the Odin works with `--app`, not `-p`. raylib `fopen`/`ExportImage` fails in
  the app dir: use `ExportImageToMemory` + `std::ofstream`. (N4, N8D5E)
- Android stdout→logcat keeps at most 1,023 chars per record, and drops lines on big bulk
  flushes: pull on-device output files instead. (N8D5L, N8D7M13)
- The Android emulator (AVD) can't create a Turnip device on its virtual GPU: glue tests only.
  (NAVD1)
- On the mini the only `Controller` HID match is an internal NAND sensor, not a gamepad. (E55D4)

## iOS

- Pinned raylib SDL2 `GetWindowScaleDPI()` returns 1; I27B's DPI patch fixes the viewport.
  Without an iPad device family the app runs in an iPhone-compat portrait window. (I27, IPAD1)

- iOS runs GE1 (Metal). Brad's iPhone reaches the Mac over Wi-Fi only and drops when it
  sleeps. (F2 Part 3b)

## PCSX2 reference

- T48 VU1 startPCs are in 8-byte units: PCSX2 `0x0/0x2/0x8/0x73` = ours `0x0/0x10/0x40/0x398`.
  Healthy VU1 programs: ≤ 2,090 cycles at Select Character, race max 23,540. (E36, T48)
- PCSX2 runs zero VU0 micro-programs and zero VIF0 words at settled Select Character. (T57)
- Healthy Snow Jam load: ENTER → race in ~109 emu-s, one continuous 990-iteration `_sceCdSC`
  loop, 1.75 MB SPU voice upload. (T47)
- `local/research/G46/g46_rec2gs.py` converts our GS stream to `.gs` for PCSX2 gsrunner. (G46)

## Workers

- OpenCode's `../*` edit rule overrides a scoped allow when the pane starts in `ssx3`: launch
  fork-editing opencode workers from the private worktree cwd. OpenCode `ssh` is denied. (E55D14,
  GB7C7, N8D7M12P6M6)
- Local Qwen: bounded, scripted extraction with an immutable checker; semantic verdicts stay
  with the orchestrator; an empty LSP result is a tooling gap, not proof of no callers. (X9–X14)
- Engine timing (both platforms): one reference with the GameCube ↔ PS2 address map, experiments and open 120 Hz
  questions is `docs/research/ssx3-engine-timing.md` (EN1); read it before any timing/120 Hz work.

# facts.md additions (09-29/09-30 gated lanes)

Proposed lines for `docs/facts.md`, grouped by its sections. Each names its lane
(`local/research/<ID>/REPORT.md`, gate section unless noted). Only gated lanes are in the main list;
ungated reports are listed at the end, for after their gates.

## Guest (SSX 3 on the EE)

### Full-120 (per-update terms the FIX groups convert)

- **The general mechanism:** SSX 3 steps most game-time state by a fixed 1/60 per update (constants such
  as `[0x49b798]`, `[0x49bf84]`, `[0x49b680]`), not by a measured dt, so running updates at 120/s plays
  those systems 2×. Each fix is a named `FIX` group that halves the term only in events mode (verify-then-
  write words, reversible at events exit, or a hook); `FIX=all` takes every group. Full census: 1,179
  rows, each with a status, in `FH13/coverage.tsv`. (FH1–FH14)
- Rider animations: 13 of the 14 step words `0x49b180…0x49b1ac` + `0x49b1b4` are per-update (`anim`,
  `0x3c888889` → `0x3c088889`); trick bonus `[state+0x3c]` via `0x119708` → `0x1176f8` (`bonus`). In-air
  points match stock after both. (FH8)
- Rider takeoff slew `0x49ba0c` (0.05 per update, no dt) applies to every rider, player included; at
  120 unconverted it wound down 2× and the rival ran +4 % (`takeoff`). AI `race_tick % N` gates are
  `aigate`. (FH9)
- Boost/uber machine `0x1200d0`: `f20 = R+0x300 × [0x49b798]` steps timers `R+0x2e8/+0x2ec` and the uber
  timer `R+0x2f0` (`boost`). Boost meter: the shown fill moves toward `R+0x2f8` by `[0x49b680]/[0x49b684]`
  per update; the HUD `0x29ab40` clicks (sound 0x96 via `0x2adca0`) per new coil = `floor(10·fill)`
  (`meter`). Rail handler `0x13af28` steps `R+0x300 × [0x49bf84]` (`rail`, was 2.2×). Steering triple
  `0x113e80` (`R+0x1f0`) + rate words `0x49b4c4/0x49b4cc` (`steer`). State 9 `0x12f398` is the
  white-fade course reset (`reset`). (FH10)
- cFlagManager `0x34b818` per-update phase steps (`flags`, via the post-call hook `ps2_fh1::onReturn`).
  (FH11)
- Ground snap/pull `0x13c878` toward the snow (`[0x49c008]`), per update: made the rider stick to crests,
  up to +6 % on a crest segment (`ground`). Start, acceleration, top speed, air and carving otherwise match
  stock within ~0.5–0.7 %. (FV1)
- Plant/tree sway `0x38ec40`: phase = render-frame count × freq/600 (`rclock`, reads a stock clock
  instead of the render device frame count `0x6214d4` +0x5a74). Particle emitter step `0x370788`
  (`emitter`). Rider FX fader `0x2e1f70`, glint decay `0x2e1120`, board-wake scroll `0x2ef6d0` (`fx`).
  Game-side audio key-ons and streaming/LOD show no 2× in a race. (FH13)
- Events mode now enters at the race start (gate drop-in), not after the drop lands (`entry`). The start
  stick-phase (~1°) is a one-update sample-and-hold in `0x113e80`. (FH14)
- Trick-fail/landing matches stock within half a stock frame: the ch2 safe-to-land bit at contact
  `0x139c88`, same L1 release boundary (4918 lands, 4920 fails) in both modes. (FH15)
- Backcountry rivals (The Throne, Happiness) run 1.00–1.04× of stock pace in events; the rubber band is
  off and the time scale is 1.0 there. Throne save state: `FH16/state-b2-23800.bin`. (FH16)
- Pad recorder v4 stamps guest vsync ticks (PRC1 `dd338f4d`); the round trip is exact
  through 60↔120 pairs, and Brad's v3 files replay with 0 mismatches. (PRC1)

### Frontend, courses and the disc

- **Disc names:** SSX 3 walks the disc's own ISO9660 tree at boot (`cdfs_build_toc` `0x3E4040`,
  recursive `0x3E3758`, one sector each into `0x519C80`, `maxFiles` 500, 163 files) and indexes paths by
  a backwards `h*33+c` hash (`0x3E36F8`, seed `0x1505`, qsort cmp `0x3E3968`); lookups `0x3E4508` return
  index+1. Loose host files are invisible: new files need directory records, which `PS2X_CD_OVERLAY`
  adds (a runtime virtual directory, pseudo-LBNs past the image). (TK2, TK3)
- **World archive per event:** the world loader ignores event 0's `archive` field alone; a manifest
  `archive` row per event makes the whole world stream from the overlay (e.g. `GARI.BIG` instead of
  `BAM.BIG`). Tricky mode = per-event archive/name rows + same-length RAM string pokes, switched live by
  the L3+R3 chord; Stock undoes every write (positions bit-identical to stock). (TK3, TK9)
- **Loading-screen picture:** the setup copies its COURSPIC path at every event load (`0x233180` →
  `func_3168B0(buf, 0x47BDB0, 0x100)`, ELF string `data/ui/courspic.big`), so a menu switch takes effect
  at the next event. `COURSPIC.BIG` = 17 × 256×256 8-bit SSH named `<CODE>.ssh`; `GL:99QPEvent` picks by
  event index through the RAM table `0x442950` (event ≥ 17 → Snow Jam). (TK8, TK9)
- **Composite worlds:** a Tricky course as an overlay composite is ~1.3–1.5 MB (changed groups only,
  every unchanged group byte-identical to the host world) instead of a ~113 MB world copy; rides are
  position-identical to the full-world ride. (TK10)
- **`.LUI` frontend format:** `"IYF\0"` v3.9; +0xC screen table `{u32 nameHash, u32 offset}` (searched
  by `func_397CC0`); +0x10 image atlas (24 B entries into `<NAME>_1.SSH`); +0x14 font list. Screens are
  RefPack (`10 FB`); widget records `{u16 type, u16 size, u32 nameHash, …}`: 23 text (locale key at
  +0x24), 17 sprite, 16 group, 19 menu. Names use the PJW-style hash of `func_317618`. `FE.LUI` loads at
  every frontend entry (76 screens; Select Event is screen `Map`), `GL.LUI` at every event load, `OV.LUI`
  in game, `FL.LUI` at Quit. Dumper `lui_dump.py`. (TK8)
- The Select Event screen `Map` holds 9 fixed menus `Peak{1,2,3}{Race,Freestyle,Freeride}Locations`
  (4/4/3, 5/5/5, 8/8/6 rows); a nav slot without a widget crashes (TK6's slot). The main menu's
  "Online" row is dead on our route and now hosts "Tricky Courses" (help text in `FEAMER.LOC`). (TK8,
  TK11)
- PS2 model/instance formats re-encode byte for byte on stock (Tricky scenery import: Garibaldi 3,332
  instances, 169 rails that grind). (TK13)
- The Aloha/Elysium halts are one mechanism: the game's GS VRAM block allocator (`sub_003663D8`
  coalesce, heap `0x876660`, 4 MiB) walks a damaged chain; on paraLLEl garbage PATH1 transfers spin the
  GS. The damaging write is not found yet (TK15). (TK14)
- Pop-in is the game camera's single far plane (30000; streaming at 1.5× far), set at the projection
  setter `0x376c58`; there is no per-model distance LOD. (LOD1)
- `sub_0037E120` is a skinning/packet builder, not a copy routine; the host DMA source-chain walk is
  3.66 % of Odin GameThread self. (HLE1)
- Loads are wait-bound: `sceCdRead` is instant in the runtime, no `sceCdSt*` on these routes, the EE is
  idle 70–90 % per loading frame; race load ~2.8 s. (LD1)

## Runtime semantics and builds

- Draw limiter `PS2X_SSX3_DRAW_HZ=60|90`: skipped updates skip the game's render call; the sim trace is
  identical, but kept frames differ slightly (≤ 1856 px, ≤ 2 levels on 4/24) because render-path RAM is
  shared with update code, so it's classed guest-affecting. (DL1)
- iOS pacing: the guest's 60/s is the EE scheduler's wall-clock VBlank, so headroom tests need
  `PS2X_EVENT_CLOCK=cycles`. iOS file layering: bundle < preset < Documents < launcher. (IQ1)
- Android launcher 60/120: a trampoline sets the launch mode before the env loader; 120 layers
  `files/full120.env` over `files/ps2x.env`. (TG1)
- Bionic `setjmp` saves the signal mask (a syscall per scheduler transfer); `PS2X_EE_JMP_NOMASK` skips
  it. (GT3, GT4)
- FH1's per-branch hook predicate dispatch was ~10 % of Odin GameThread cycles at full-120 (≈ 0.43
  ms/update); `PS2X_SSX3_FULL120_FASTHOOKS=1` is a pure refactor (det-identical on and off). (GT3)
- Runtime diagnostic output is line-atomic from fork `d538412` (cerr/cout line buffers, raylib TraceLog
  through one writer; raylib lines now on the `ps2x` logcat tag). (LG1)

## Odin / Android / GPU

- **The Odin panel's "120 Hz" is 121.7 Hz (8.215 ms).** Guest pacing at 119.88 drifts past it and holds
  ~3.2 refreshes/s even at full speed; `PS2X_VSYNC_LOCK=1` paces guest VBlank on SurfaceFlinger's latch
  grid (holds 0.4–0.5/s, +1.5 % game speed; menus 60.9). Some All-Peak stretches show at 60 because the
  game itself renders every other update there. (PX1, `REPORT-smoothness.md`)
- Under the lock, the audio stretcher toggles ~0.7/s; `PS2X_VSYNC_LOCK_AUDIO=tempo` holds a constant
  +1.5 % tempo with pitch unchanged (0 toggles, 0 dropouts). (AU15)
- **Turnip bugs (TU3, Mesa fork `bc003541232`):** A = stale dummy colour-buffer draw-state groups after a
  colour-less pipeline (fixed at the root, `9932ef49bf3`); B = an unidentified sysmem-path error for
  `ONE_MINUS_SRC1_*` dual-source blends, worked around by preferring GMEM for those passes (+4 % race
  GPU). With both, Turnip is pixel-exact vs Qualcomm with no GE1 workaround; `GE1_VK_BLENDCONST_REEMIT`
  is unnecessary. (TU3)
- **Upstream PCSX2 draw-buffering bugs** (fixed in our fork): `FlushBuffers` restores the env without
  `UpdateContext()` (stale XYOFFSET, the B pass culled); `VertexKick` binds buffer references before the
  overlap flush (a vertex into an abandoned slot). Draw buffering also exposed a GP6 split bug (scissor
  refresh + draw serial on the front). (GS10)
- "Thermal 3" is a reported status, not a clock cap: no cooling device engages and clocks stay at the
  Performance floors (primes ≥ 3.28 GHz, GPU ≥ 900 MHz). Small losses (~0.987×/10 min) come from heavy
  stretches where the EE critical path (busy ~5–6 + ~4 ms waiting on MTVU/GS) exceeds 8.33 ms. ADPF is
  inert under the Performance floors. (PW2)
- MTVU tails are content bursts (ice-cave section, +64 % runs) plus a busy-span accounting artifact, not
  JIT compiles (0 in the race). Offline microVU on Android is +1.45 ms/f slower than the JIT. The GS Back
  spin is bounded at 50 µs (`GE1_BACK_SPIN_US`). (VG1, VG2)
- GPU first-use stalls: on the play path (Turnip, 2×, PW1 prewarm + flush) warm replays show 0 frames
  > 16 ms and 0 pipeline creates; the Turnip pipeline cache persists (5.7 MB on Brad's device). Cold
  stalls are pipe+spv compiles; GPU1's ~30 ms spikes were heavy Qualcomm frames. (SH1)
- Upscale: 2× / native 2.41× fit 120 on the Odin GPU (race p99 4.2 / 5.3 ms); native/3× show a cliff-mesh
  seam no PCSX2 half-pixel mode removes; AF16 costs +0.37 ms at 2×; trilinear is PS2-trilinear by default;
  colour matches PCSX2 defaults within ~1 %. (UR1, UR2)

## iOS

- iOS display-sized export (`PS2X_GE1_EXPORT_SIZE=display`): the exact 16:9 game rect drawn 1:1 — iPad
  2360×1328, iPhone 2347×1320; iPad 2.5× fits (GPU 3.3 ms/f, 1.00×, thermal 0), native 2.96× doesn't.
  Before IX1 the iOS export was fixed at 640×480, so scales above 2× were wasted. (IX1, IP4)
- iOS bundled default = offline microVU + GIF/VIF stages: iPad race MTVU 10.7 → 3.5 ms/f vs static VU1,
  1.00× on all engines. (IB4, IB5)

## Workers

- Auto-mode classifiers deny third-party ML model runs and variable/glob-built `rm` paths; workers stop
  correctly and don't retry (AU13 Part 1, 09-30 deletes rule).

---

## Not yet gated (add after their gates)

- AU16: under `LOCK_AUDIO=tempo` a rounding surplus of ~0.7 frame per callback piles up in the
  stretcher: +1.0–1.06 ms of audio delay per second of play. Still the pending audio-latency fix
  (`local/research/AU16/`).

- **adb in shell loops:** `adb shell` reads stdin, so inside `… | while read` it swallows the rest of the list. Use `adb shell -n … </dev/null` (and `</dev/null` on `adb push`) in loops. (PB16T Tricky install, 10-01: only the first file reached the device.)

- **opencode panes exit once a run finishes or an update prompt shows (10-02):** a follow-up `herdr agent prompt` to a `done` pane often lands in an exited session (IPB1, TK35). Re-launch with `launch-gomuse.sh <ID>` and a Part-N prompt instead of resuming. **Root cause:** opencode's "Update Available" dialog. A `herdr agent prompt` (its Enter) confirms the update, and the app exits after updating. `launch-gomuse.sh` now passes `"autoupdate":false` + `OPENCODE_DISABLE_AUTOUPDATE=1`; for panes started before that, `herdr agent send-keys <a> esc` (Skip) before prompting.

- **Tricky latent crash (TK37, 10-02):** SSX 3's camera spatial query (`0x22ADD8` builds a stack query object; `0x22A830` walks the instance tree) keeps inside/straddle lists of 512 × 8 B with no bounds check (appends `0x22aabc`/`0x22ac54`/`0x22a0ec`/`0x22a114`/`0x22a670`/`0x22a238`). Tricky imports put a whole course in one location, so a dense view can straddle > 512 nodes, overwrite the list-1 count, and JALR to garbage via `0x22C078` → vfunc +0x300 → `0x34FED8`. Instances file at level = exponent(max_extent·0.714), capped at 11 (`0x328F28`). Fix: the TK38 runtime guard; the TK37b AABB floor adds headroom.
- **Runtime hooks only fire at `dispatchGuestBranch` (TK38, 10-02):** generated code dispatches only on cross-function calls; in-function recursion (`goto label_…`), returns and jump tables never reach a hook. A guard on an in-function loop must act at the next cross-function call (post-walk repair) or at callee entry.
- **Tricky AP rides (APH1, 10-02):** the closed-loop autopilot (`PS2X_TK12_AP_*`, `PS2X_TK12_CLIP`) is in fork `ssx3` ≥ `72ea599` as a dev knob. Runners built before that ride the pad script open-loop, so their AP env is inert (TK36). Use `pool.py get` on a tip ≥ `72ea599`.
- **iOS perf legs: no dumps when judging hitches (ISH2, 10-02).** `PS2X_GS_IOSURFACE_DUMP_DIR` dumps at ticks 2100/3000 and writes GS driver-report sidecars on the GS thread: 11–13 ms fake spikes. On clean runs the iPad has no GS hitch > 16 ms after boot; tick 1606 is EE-heavy (ee.cpu ~21 ms).
- **Tricky black starts = guest heap ENOMEM (TK39, 10-02):** the runtime's HLE pools at `0x1F00000–0x1F31000` cap `SetupHeap`/`EndOfHeap` at `0x1F00000` (896 KB under a real kernel). Heavy Tricky worlds (Snowdream, Garibaldi) load within 8 KB of it; when timing leaves no 64 KB hole the race-load `_memalign_r` hits ENOMEM every frame (`__errno` from `0x31e69c`), the race never inits, and the screen stays black on the load frame. It was misread as a Turnip/sign-bind bug (TKP2, TK35, MR1). Fix: `PS2X_HLE_POOLS_LOW=1` (Tricky layer).
- **Upstream's IOP emulator can't run SSX 3 (IOPE1, 10-02).** With `PS2X_IOP_MODE=emulator` on the rebased fork (`8187015`), the Mac boots and loads 9 IRX modules on the R3000 interpreter. Draw work then repeats identically every window from tick ~600 (a static screen) and audio is 100 % underruns, i.e. no sound driver. SSX 3 stays on `hleIopMode` (our SND/CD/MC HLE). Using upstream's emulator as a reference for our 4 unhandled SIDs would first need its EA SNDDRV/SPU2 import support. Log: `~/dev/ssx3-work/IOPE1/emulator/boot.log`.
- **Never regenerate SSX 3 codegen casually after UPR1 (RRV1, 10-02).** The rebased recompiler (upstream entry discovery, the `ps2_call_list` GetRomName removal, the MMIO override change) emits different code: 9,281 of 9,457 files changed, 365 of them beyond spelling; registrations 399,813 → 399,737 (20 added / 96 removed / 1,142 retargeted). Play and det keys use the pinned codegen `a1c5e02f` (old recompiler output, byte-identical). A regen is a guest-affecting change: full gates plus new keys.
- **Lab savestates from before UPR1 are v1** (`stub:sif`/`syscalls`) and are refused by `8187015`'s exact-version loader until RBF1's migration lands. Use the pinned old lab runner (R1) for them, or re-mint.
- **The iPad's 2.5× export disables ARMSX2's two-pass vertex kernel (GKV1, 10-03).** `DeviceCullGridShift` is 0 for non-power-of-two scales, so every iPad batch takes the legacy per-vertex kick; the iPhone (2×), Odin (2×) and Mac (1×) run the kernel for ~97 % of vertices. GS-side numbers from iPad profiles (IHP1 lever 3) overstate the phone's GS cost. `GE1_GKV1_SHIFT0=1` (exact, gkv1 branch) fixes the iPad.

- **TFX prewarm record files are superset logs (MPW2, 10-04):** the prewarm thread re-records every key it builds, so true misses = appended keys − the seed list. iOS bundle paths are read-only (`PW1: cannot append`); for a census, use a writable absolute path (the GE1 lib takes any path).
- **Det mode and memory cards (DET2, 10-04):** card-file mtimes reached guest RAM via `sceMcGetDir`, so det boots with freshly copied cards diverged at ~t123. Fixed at `fe73e8e`: under `PS2X_DETERMINISTIC=1` the dates come from the fixed CD RTC calendar. Tricky boots also need the full Tricky layer (`--profile play` + the six `tricky.env` guards); without `DRAWTABLE_GUARD`, Elysium overflows the draw table at ~t2502 (wild JALR `0x2ed508→0x2c07`).
- **Native ports of guest functions only pay with a contained hot region (SKN1, 10-04):** `sub_0037E120` (skinning, 11 % of iPad GameThread) spreads its cost over 2,236 live instructions and 16 call sites. An exact generated port was performance-neutral (+0.6 %). VNP1 won because its VU0 programs were small and hot. Census first (dispatch counts per block) before porting.
- **Tricky HUD `.SSH` textures use a LINEAR 8-bit CLUT and unswizzled texels (TK43a2, 10-04):** the TK8-lineage decoder's CSM1 entry swap scrambles them (gold/red noise). Check any future SSH decode against a real-game still before compositing.
- **Player-1 rider struct, course-independent (TK43d, 10-04):** `R = [[[0x4a28a8]+0x84]+0xC]+0x28` (gp-0x848 → game global → race object (`func_28B1D8`) → players[0]). It matches every per-course AP pointer (MERQ 0x53ff94, ALOHA 0x5400e4, MESA 0x540064, SSX 3 0x53ff4c) through wipeouts and resets. Meter: fill `R+0x2f8`, level `R+0x2f4`.

## Odin display: SurfaceFlinger VSync tracker can stick at 60 Hz (DSP2, 10-09)

- **The state:** after a display doze/wake (10-06 night, probably while hot), SF's VSync tracker stayed on the 60 Hz model, mid-transition to 120 (`mModePtrTransitioningTo` 120, `mPeriodConfirmationInProgress=1`, no HW vsync samples, SDM `vsync on: 0`).
- **The effect:** every layer latched at most 60/s for ~2.6 days while the panel and `activeMode` showed 120. The app's votes can't fix it.
- **Detect:** `dumpsys SurfaceFlinger | grep -aE 'mDisplayModePtr|mPeriodConfirmationInProgress'` should read 120 Hz and 0.
- **Fix:** a screen power cycle (Brad), or a reboot.
- **Consequence:** Odin SF-timing numbers from 10-07 to 10-09 (`--latency`, present latency) were taken on a 60 Hz compositor. DSP3 adds the automatic check.

## Matched-load comparisons: the load point matters (JMP6, 10-09)

- **The hybrid update:** the first events update after loading a stock mid-race savestate runs with rider dt 1/60 but the steer at the 120 step.
- **Near a knife edge** (a special-triangle contact, a lip), that ~1-update phase error splits the lines. JMP5's gap-A save 7290 missed a contact that stock hits (a ~21° heading snap) and produced a false "early lip / 16.7 ms ollie window".
- **The rule:** save past any contact, at least a few ticks before the window of interest. Pair by game time (A18 ordinal), never by script tap number.

## Odin A/B validity: lane APKs must match the play build recipe (TRM1, 10-10)

- **The confound:** a lane APK built without PGO (`pgo-bb399bbe68df`) or with different jnilibs measured **+1.2 ms GameThread** against the PB51 PGO play APK on identical code. Every base-vs-tip delta was confounded.
- **Valid designs:**
  - (a) the same APK with a knob A/B (GSB2/NRS1/HUD4/OUT1 style);
  - (b) both arms built with the play recipe (PGO + pinned jnilibs) from the same driver.
- **Pad-script clock (RFN1 10-11):** in full-120 races the pad clock counts real 60 Hz present vsyncs, while tap ticks run at 120 Hz. Race-phase pad entries are wall-ms: `pad_ms = 28560 + (tap-1712)*1000/120` (I26), not `tap*100000/5994`. So one pad fires at the same race moment in stock 60 and every 120 mode (races last ~271–276 wall-s). `lx=255` = right. Source: `local/research/RFN1/REPORT.md` §3.
- **Race session state word (FIN1, 10-12):** the race session object (`0x1463340` on the I26/RFN1 routes; first arg of `sub_0026F4A8`, called once per app update) is a state machine on `[session+0x0]` (set by `sub_0026F4A0`). Seen sequence: `0 → 0xF → 0xA → 0xB → 0x0` (race) `→ 0xD` (results, within 6 ticks of the race-clock freeze, at both 60 and 120) `→ 0x1` (auto-retry). The finish crossing (`0x12b3d0` → `sub_0026FA50`) sets `+0x60C=1`, which drives the 0xD transition. A pause-menu quit teardown also passes through 0xD, so telemetry reads the word at the stop edge, never latched. `+0x610` is not "finished": it's set only by a saturating per-rider counter (`0x26d258`) and freezes race time without stopping the state age.
- **Post-hook gating (AIR1, 10-12):** `ps2_fh1::onReturn` (via `ps2_runtime.cpp`) fires only when `g_post`/`g_pub`/`g_normcap` is armed. A new dedicated post slot must join that arm condition, or its post never runs (AIR1 saw a silent no-op, then a 2.7× flip).
- **120 flips/spins over-rotate: the stunt-charge chain (AIR1/FLP1/FLP2, 10-12):** `sub_001211F8` slews the stunt charge `R+0x2A4` (spin) / `R+0x2B0` (flip) per update, unconverted, so it charges 2× per wall-s at 120. The takeoff setter `0x133250` then sets the rate `R+0x2E0`/`R+0x2DC` 1.41×, which drives the stunt step S+0x18/0x1C (0.704×/update) and the pose (1.39×/ms). Result: an incomplete-flip crash at 633 vs 751 ms; clean jumps are unaffected (gravity is converted). The anim track is converted (f12 1/60 vs 1/120) and the FH8 node functions do run (FLP1's "never run" counted interior pcs). S+0x2C ≡ 0, and the S+0x18 step comes from the second store `0x134318` via `[0x49bca4]` × `R+0x2E0`. Fix: opt-in bit 60 `stuntchg` halves each charge delta (fork `3c9d793a`); a ±9 % flip / +5.9 % spin phase residual remains. `R+0x2BC` is inert in stunts.