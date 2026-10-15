# GameCube learnings inventory (EN2, 2026-09-26)

Everything from the parked GameCube/Dolphin route **except** timing/120 Hz
(EN1: `docs/research/ssx3-engine-timing.md`), mapped to the PS2 static-recomp
runtime (EE/VU/GS/SPU2 via recompile/HLE on Odin/iPhone/Mac; PCSX2-parity
accuracy target, `AGENTS.md` Product direction). Every claim cites file +
section. PS2 context: `docs/status.md`, `docs/numbers-ledger.md`,
`docs/research/review-2026-09-26-astra-perf.md` (RV4),
`review-2026-09-26-astra-emulators.md` (RV7), `local/research/CP1/REPORT.md`.

## 1. Summary

- **Dual-core won; micro-opts didn't.** Moving FIFO/decode/vertex work off the
  CPU thread fixed the stadium section (speed 0.887→1.000); the classifier,
  conversion-split, O3 and probe-buffering spikes bounded helper work at ~1–2%
  of CPU samples and were correctly rejected (`performance-review-2026-09-13.md`
  §§Batch 1, Micro-optimization spikes).
- **The big lever was generated-code shape, not helpers.** Every guest
  instruction as a switch target forbids cross-instruction register allocation;
  76–83% of entry cases are prunable; guest bodies are 45–57% of cycles
  (`review-2026-09-17-recomp-perf.md` §2). PS2 analog (VU1 blocks) is RV4 rank 1.
- **Per-op wins don't reproduce at game level.** psq −30%/call → flat; conversion
  split 2.79%→0% module-wide; fcmp inline +347 KB → −7.3% wall (layout/I-cache
  regime) (`review-2026-09-17-recomp-perf.md` §2, `review-2026-09-17-architecture.md` §2).
- **Measure off the wall, in the ship config.** A u32 throttle overflow voided
  nine verdicts; movie determinism double-decodes every FIFO byte; backend A/Bs
  ran OGL on both arms (`review-2026-09-17-recomp-perf.md` §§1, 4).
- **Free runs can't A/B anything.** Same archive twice: median 449, max 69,184
  units apart within 0.21 s; movie replay agrees on all 879 control-flow rows
  (`aloha-conversion.md` §§9.2–9.3). PS2 has `PS2X_DETERMINISTIC=1` (`docs/facts.md`).
- **Phone boots are CPU-bound, not latency-bound.** FastDiscSpeed saved 26% on
  Mac, ~0 on phone; the 5 s pre-menu wait is asset load; checkpoint resume
  turns 20 s into 1.8 s (`startup-shortcut.md` §§Measured, lever).
- **Course data is largely platform-neutral** (BIGF, RefPack, bit-identical
  terrain coefficients, byte-identical AIP); GX tiling, TEV scales and record
  layouts are GC-only (`gamecube-feasibility.md` §What carries over).
- **Content pipeline knowledge transfers as method**: validated course compiler,
  capacity/residency auditing, habit of immutable recipes + readback
  (`reserve/architecture-review.md` §§Prioritized; `full-course-experiment.md`).
- **Receipts must name effective config + binary identity**, or verdicts rot
  (M5 profiled a pre-Wave-B module; Metal validation inherited from shell)
  (`review-2026-09-17-architecture.md` §1, `review-2026-09-17-recomp-perf.md` §4).
- **Idle-skip failed twice-shaped**: naive skip regressed; the "contradictory"
  numbers were a capped-speed metric artefact (CPU share carried the signal)
  (`review-2026-09-17-recomp-perf.md` §1). RV7 queues wait-loop work only after
  hot polling is found.

## 2. Performance inventory

Transfer key: **direct** = applies directly · **technique** = applies as a
technique · **done (lane)** = already done on PS2 · **n/a** = not applicable ·
**?** = unknown. EN1 owns 120 Hz rows; only the CPU-cost fact is kept here.

| Optimization | What it did | Measured effect (number + conditions) | Cost & risk | PS2 transfer |
| --- | --- | --- | --- | --- |
| Dual-core CPU/GPU split | `CPUThread=True`: FIFO decode + vertex conversion on 2nd thread | Phone render CPU med 9.49→7.77 ms (p95 10.51→8.43); stadium speed 0.887→1.000; headroom 1.00→1.25; slow rows 34→3 (~150 s rides); thermal nominal→fair; +110 MB (`performance-review-2026-09-13.md` §Batch 1) | 2nd-thread power/thermals; replay/pose diagnostics assume single-core | **done (E)**: MTVU threaded VIF1/VU1/GIF, det-identical (MT1: Mac 1.31×, Odin 0.378× leg; ledger) |
| Fast-FP (JIT-fidelity inline FP) | Scalar + paired-single arithmetic inline at Dolphin-JIT semantics; NaN/exception fallback | Desktop replay 1,022/1,022 identical control rows over 1.07B dispatches; module 76.6→80.4 MB (`performance-review-2026-09-13.md` §Batch 2). Phone pair **inconclusive** (12.9% render edge tracked 10.5% lighter workload; lines diverged in ~3 s) (`performance-batch2-followup.md` §Result) | Correctness bar = JIT defaults, not interpreter; FPR/gameplay-memory equivalence unverified | **technique** (E already did this class: VR1/VB1/VR2 VU1 recompile 1.35–1.44× bit-exact; ledger) |
| FP classifier early-return | Integer-only FPRF fast path for normal finite results | Mixed: gen-FMA −2.8% med in one corpus, flat/noisy elsewhere; identical-code controls varied −20%..+28% — rejected (`float-arithmetic-audit.md` §§First timing, Scoped) | Tiny slice (FMA ~1.5% desktop); benchmark-layout sensitivity | **technique** as process lesson only (microbenchmarks need same-address controls) |
| Conversion-split inlining | `always_inline` fast path for normal f32↔f64 bit converts; cold slow path | Outlined-shape 0.66×/0.62× per convert, but already-inlined shape **regressed 1.19×/1.12×**, rare inputs +18–29%; game level ~1.5% slice, module-wide 2.79%→0%, no wall change (`float-conversion-spike.md` §§Correctness, Bounded check) | +5.3% object text; keeps strict FP flags | **technique** (inline by force only where probe shows outlined; verify at game level) |
| O2 vs O3 generated module | Same C, only opt level changed (fresh builds, Clang 23.1 pair) | No O3 benefit: extra wall med 7.9–8.4 ms O2 vs 8.3–8.5 O3, unmatched trajectories + observer warnings; O3 text −1.3%; keep O2 (`120hz-cpu-overhead-spikes.md` §O2 vs O3). Closed (`performance-review-2026-09-13.md` §Priorities) | Rebuild cost only | **technique — and contrast**: Android −O3 gave +5.3% Odin race (BA1, ledger). Flags are per-target; measure each |
| −O0→−O2+ThinLTO (Odin) | Optimized + LTO the generated module | 2.5–3.5×; "−O2 *deleted* call overhead" = structural-cost signature (`review-2026-09-17-recomp-perf.md` §2) | Link time (one-object ThinLTO link 230 s desktop; `float-conversion-spike.md`) | **done (E/N)** (Release builds; ThinLTO audited: +0 over −O3, BA1) |
| psq helper merge | Fold per-site constants into quant-load/store helpers | Could not pay: `static inline` helpers outline everywhere (still 250–280 samples post-merge); fix = `always_inline` or split to fast path (`review-2026-09-17-architecture.md` §2) | — | **technique** (verify inlining by symbol absence, not wall time) |
| fcmp inline | Forced-inline FP compare helper | **Regressed**: +347 KB in 82 MB → −7.3% wall throughput, ABA-confirmed → layout/I-cache-bound regime (`review-2026-09-17-recomp-perf.md` §2) | — | **technique** (code-layout sensitivity; order-file/PGO idea — RV4 ranks PGO 0–3, queue) |
| Entry-switch pruning (proposed, not landed) | Restrict per-function entry switch to block leaders ∪ return targets | Static: 76.5–82.7% of 4,088 cases prunable across 3 chunks; ~6-instr regalloc windows; est ~1.15× overall (`review-2026-09-17-recomp-perf.md` §2) | Must enumerate all entry addresses (starts, return hooks, loop heads, exception vectors) | **technique** = RV4 rank 1: VU1 block residency, fewer state round trips (VR2 stage 4 in flight) |
| PC-store pruning (proposed) | Clear `materialize_pc` except at observable boundaries | Static: PC stored at 95% (3,876/4,088) of instructions today (`review-2026-09-17-recomp-perf.md` §2) | Same entry-enumeration proof | **technique** (EE already goto + backward-edge checkpoints; external JAL still `dispatchGuestBranch` — RV4 §1) |
| Memory-access fast paths (proposed) | `#if`-out never-installed write journal; const-fold GC RAM layout; inline gather-pipe test; `preserve_most`; fastmem | M7: `HookExternalWrite` 1.69% + `Write32` 1.16% + caller-side indirect-call cost; every store pays reservation load + journal branch (`review-2026-09-17-recomp-perf.md` §3) | Fastmem is the large structural item | **technique** / partly **done (E)**: EE FAST/RAM-MMIO/guard specialization exists (RV4 §1); audit for always-off diagnostic branches on hot path |
| `convert_to_double` inline | `always_inline` a 0.66% out-of-line helper | Not measured post-fix (`review-2026-09-17-recomp-perf.md` §3) | Trivial | **technique** (same audit class as psq) |
| Chunk-granular lookup | 24 MiB int-per-word table → s16 per 256 B granule + binary search on mixed | Correctness covered by the 1,022-row replay; no isolated timing (`performance-review-2026-09-13.md` §Batch 2) | — | **?** (EE dispatch 17% inclusive vs 0.16% self is mostly callees — RV4 §1; different dispatcher shape) |
| Idle skip | Skip guest idle spin (`loop_80288ED4`, 7.8% desktop) | **Failed**: earlier attempts regressed/failed to progress (`120hz-cpu-overhead-spikes.md` §Audit; `normal-frame-cpu-spike.md` §Next work); `CoreTiming::Idle` skips one slice only — correct form = fast-forward to next event + host sleep (`performance-review-2026-09-13.md` §Runtime overhead) | Thermal, not FPS, is the metric | **technique** with care: PCSX2-parity allows wait-loop detection, but RV7 says queue only after hot polling is found; HLE `waitVSync` exists |
| Idle A/B metric lesson | Same experiment read three ways | "−30% Mac, ≈0% desktop O2, +27% Odin" reconciled: capped speed saturates at 1.00; CPU column carried it (ON 1.00 @56% CPU vs OFF 0.62–0.93 @84%) (`review-2026-09-17-recomp-perf.md` §1) | — | **done (process)**: ledger already requires CPU-ms/frame under a cap |
| Throttle-overflow fix (`1f1bc2d`) | u32 wrap at speed 10 made a 1.16262 phantom wall | Voided 9 verdicts (fast-FP 0.00, SyncGPU, pinning, EGL=Vulkan, EFB 2×, validation…); first real Odin race 0.83×/0.63× OGL (`review-2026-09-17-recomp-perf.md` §1) | s64 multiply within 4× of limit; prefer divide-first or `__int128` | **done (process)**: use unlimited mode (`EmulationSpeed=0` analog); ledger's exclusive-host + ≥2-run rules |
| FastDiscSpeed | Buffered DVD reads (latency model only) | Mac menu 20.10→14.85 s (−26.1%, paired n=6) (`loading-speed-spike.md`); phone ≈0 (CPU-bound; ≤1.2 s of 20 s boot was ever latency) (`startup-shortcut.md` §Measured) | Needs card r/w/relaunch verification per device | **n/a** as a knob (no equivalent DVD-timing model); **technique**: per-device measurement; RV7 queues host caching only if measured |
| Memcard read speedup | 8× modeled read rate (writes untouched) | Stock cost 1.32 s, zero writes; card-size override removed (scan not size-proportional); real wait = 5.0 s CPU-bound asset load (`startup-shortcut.md` §§Both levers, card screen) | Save durability depends on write timing | **technique**: PS2 `mc0` is a host dir, autoload delays title ~200 ticks (E55D16, `docs/facts.md`) — measure before touching |
| Boot checkpoint resume | Savestate at main-menu readiness instead of cold boot | 1.8 s to menu vs ~20 s cold; 104 MB in ~0.1 s; 16 restores vs 84 identity rejections (whole-`files/` hash incl. app build) (`startup-shortcut.md` §§lever, pre-menu) | Identity must narrow to what a savestate depends on; hashing 1.3 GB costs ~1.9 s/launch | **technique** (E has SS1 save states, dev default off; play builds use saves — `docs/status.md`) |
| Startup movie-skip | Clear pending mask `0xf` at `0x803d7f64`; START after complete state-5 title update | Phone menu 20.61 s; input-race fix (edge between resource-ready and activation is lost) covered by synthetic regression (`startup-shortcut.md` §§Seams, Installed build) | Dev-only; faithful playback separate | **done (E)**: `PS2X_SKIP_MOVIE`, default off, dev-only (`AGENTS.md`) |
| Dispatch sampling default-off + trial-step hoist | Presence-based env sampling + 2 Step calls × 8M dispatches/s removed from prod path | "Costs nothing to remove"; cleaner later measurements (`performance-review-2026-09-13.md` §Runtime overhead) | — | **done (E/N)**: `PS2X_ENABLE_DIAG_TAPS=OFF` for speed builds (N5, `docs/facts.md`) |
| Probe/trace buffering | 64 KiB stdio buffer vs flush-per-callback | 1–2 µs/callback vs ~7 ms callback — not promoted; crash-tail risk (`120hz-cpu-overhead-spikes.md` §Callback trace) | — | **technique** (same reason PS2 taps are compile-gated) |
| "Determinism off 0.0%", pinning, EGL=Vulkan, EFB 2× | Four A/Bs at the phantom wall | All **void**; determinism-mode double-decodes every FIFO byte so the re-test is likely a real win; first genuine backend compare: Vulkan 0.73× vs OGL 0.83× race (`review-2026-09-17-recomp-perf.md` §§1, 4) | — | **technique** as harness caution (verify *effective* config in receipt; movie/det harness is pessimistic vs ship) |
| Texture-cache Safe mode, video-thread atomics, EFB copies | CRC32 rehash 0.58%, `__aarch64_cas1` 1.52% + futex 3.2%, `CopyRenderTargetToTexture` 1.18% (M7) | Never A/B'd; open (`review-2026-09-17-recomp-perf.md` §6) | — | **?** (paraLLEl has own invalidation/CLUT tracking — RV7 §3; CP1's cost is `submit_empty` waits, not cache) |
| Vertex-loader specialization | Fused AOT loaders for the game's small fixed `(vtxDesc, VAT)` set | Desktop "FIFO+vertex+encode" 1.56 ms of 15.76 (~1 ms lever); Odin used ARM64 JIT loader while iOS/desktop used software — every cross-platform render compare crossed that line (`review-2026-09-17-recomp-perf.md` §4) | — | **n/a** (no GC vertex loader on PS2 path); **technique** (record loader/codec identity in every receipt) |
| Guest↔GPU empty-queue sync | Guest syncs GPU every frame with `queue_before=0` in all 1,360 renders | ~3.3 ms floor + scene-scaling part; 2.74 ms CPU-busy spin, 34% of render CPU; fix (defer completion) proposed, never scheduled (`review-2026-09-17-recomp-perf.md` §5) | — | **technique** (audit sync/wait points: CP1 shows 6.7 ms/frame GS-queue backpressure on MTVU; FS2 owns the submit wait) |
| Extra frame = whole frame (kept for CPU cost only; EN1 owns 120 Hz) | Guest re-entry re-runs scene traversal + FIFO + conversion (4.7–9.1 ms med phone) vs host replay's ms-scale decode+submit (`performance-review-2026-09-13.md` §Structural 3; `review-2026-09-17-recomp-perf.md` §5) | — | — | **n/a** (no guest-renderer re-entry on PS2 route) |
| Per-dispatch hooks (host-call style probes) | `StartupBoot::Step` + `Interpolation::Step` before every dispatch; Android trial `Step` did atomics + clock before PC filter | Removed/hoisted as free wins; no doc records a *failed host_call-hook optimization* per se — the recorded lesson is hook cost on the dispatch path (`performance-review-2026-09-13.md` §Runtime overhead; `review-2026-09-17-architecture.md` §6) | Per-dispatch clock reads once cost 25–30% (`120hz-cpu-overhead-spikes.md` §Next priority) | **technique** (PS2 tap-gating already follows this) |

## 3. Game-logic / engine knowledge

GC addresses are GXBE69 rev 0 (DOL SHA `b92162d6…29ce`) unless noted. PS2
equivalents cite where they live or say unknown.

**Event / location / mode tables (course selection).**
GC: events `0x802CE5EC` (23×100 B), topology `0x802E2880` (23×40 B), mode
`0x802E2C18` (23×8 B); boot-time manifest redirect rewrites code/archive/name/
location/mode in RAM, verified by 69 index words; archive basename must equal
BIGF member basenames; case-insensitive; discipline follows the menu slot
(`course-selection.md` §§manifest, fields, discipline). Two courses resident at
once works (`aloha-conversion.md` §9.1). PS2 equivalent: **known** — same tables
in SLUS_207.72 at file offsets `0x33e940` (24 records) and `0x33f24c`
(`peaks-and-locations.md` §§Findings, event table); rename tool exists
(`patch_executable.py`); per-location audio sessions `ses_*` at `0x341774`.

**Race course: tracks, gates, kind-21 line.** GC kind-14 AIP is PS2-LE bytes;
Snow Jam chain 3→7, finish = type-0 event on track 7 @19,362, checkpoints =
type-18; six gates pair with AI paths by lateral order; kind-21 (6,536 B, 325
nodes, stride 20) regenerated within 0.1 units — stale table caused the 28%
meter bug; trailer `b=2` race/backcountry/hub, `b=4` slopestyle; slopestyle 3
gates, backcountry 2 (`gamecube-world.md` §Race course; `aloha-conversion.md`
§2 items 3–4). PS2 equivalent: **unknown** for kind-21 bytes; AIP container
shared (`.aip`/`.sop` same container; `aloha-conversion.md` §2 item 2).

**Reset / wipeout semantics.** GC: patch+10 flag `0x0002` → `reset(rider,0,1)`
via `8000606C–84` (copied at `80008650/54`); surface 18 = wipeout path
(`80005ED4`); surface-18 profile v1 **superseded** (reset-terrain loops);
grounded reset-path compiler: rejects reset/slop>45°/airborne, 200-path cap;
build 027 hash `a67ec9e5…` (`gamecube-collision.md` §§Direct flag, Runtime
evidence). PS2 equivalent: **partially known** — PS2 terrain record carries the
same surface/collision halfwords LE (`gamecube-world.md` §Terrain resource);
flag-bit behavior unverified on PS2.

**Static collision.** Target uint8 indices (partition uint32), one bbox/10
tris, opcode `0x9B` vertices ÷4; instance IDs must be consecutive (loader
derefs 0..count−1 unchecked, `80246158–218`); contact proven under movie
playback inside collider rid 524's box, y pinned 68,742 for 1.8 s
(`gamecube-collision.md` §§Remaining, Static compiler; `aloha-conversion.md`
§9.3). PS2 equivalent: **unknown**.

**Rails / splines (kind 8).** GC coefficients at segment+12 (PS2-reader +16 is
wrong here); loader `8024807C`, registration `80247970` consumes bounds
+104/+116; inverse-distance fit rescales by `(100·s)` powers under uniform
scale; donor error ≤0.261 predates conversion; binding `0003000a` is
diagnostic, style-13 mapping unverified; 50/50 grind observed, transfers open
(`gamecube-rails.md` §§Layout, Ownership, Reproduce). PS2 equivalent:
**unknown** (offsets differ by platform).

**Scenery / instances.** Kinds 24–27 shared arrays, kind-23 order
pos/UV/normal (loader `802467E4`), kind-2/3 models/instances; **28**-entry
global+location capacity tables (ctor `8024A9F8`); `0x9a` quarter-unit vs
`0x9b` whole-unit positions; copied donor geometry bit 0 corrupts display
lists (`9a000305`→`ffff9a02`) — flags profile is explicit, not portable;
texture residency is a separate budget from geometry (group 31 stalled at
181/191); kind-22 bank must stay terminal (`gamecube-scenery.md` §§Verified,
Target format). PS2 equivalent: **unknown**.

**Scripts / LUN / start gate.** Kind-16 layout endian-swapped-identical
(offsets 56/64/68/76/84), LUN magic `0x4E554C`, empty-return shape 20/36/36;
runtime visibility bit = low half of word; countdown lights + registration/
removal run from the course script; 29/49 locations pad LUN sections to
16 B (`gamecube-world.md` §Other records; `gamecube-scenery.md`
§§visibility bit, live slot, lights; `aloha-conversion.md` §2 item 6).
**Scoring is not in the course script** (`aloha-conversion.md` §10).
PS2 equivalent: **partially** — `SCDAT.BIG` 214 members, scripts not keyed by
location; event scripting lookup still open (`peaks-and-locations.md` §8.6).

**Rider state / telemetry.** Reset enters main state 9 via `800333C0→80028D20`
(chains through rider+1816/+3376; +1012 is *not* main state); state 6 = loading
(rider allocated ≠ briefing ready); MemoryWatcher pointer chains are
executable-specific, changed-words-only, implicit-zero
(`gamecube-collision.md` §Runtime evidence). PS2 equivalent: **position only**
— tracked EE `0x5409c0` (`tools/track_position.py` docstring); state fields
unknown.

## 4. Data / asset knowledge

**Platform-neutral (content, containers, math).**
BIGF archive + reader/writer; members `bam.{gdb,gsb,ghm,gsm}` ↔ PS2
`{sdb,ssb,phm,psm}`; **CBXS/CEND 32 KiB blocks, LE length, RefPack `10 FB`**
(`gamecube-world.md` §§Container, Stream; `rebuild-experiment.md` §What was
rebuilt). GDB/SDB record layout identical modulo endianness (88/96/68 B;
49 locations; 30,644 patches; 159 PS2 vs 205 GC groups)
(`gamecube-world.md` §Index; `gamecube-feasibility.md` §What carries over).
**Terrain 16-vector coefficients bit-identical** (3,885/3,885, max diff 0),
448 B NBD/PBD records, coefficients at byte 80
(`gamecube-feasibility.md` §What carries over). **AIP byte-identical**
(59,688 B; `gamecube-feasibility.md`). Surface/collision/flag halfword
*meanings*, corner UV *values*, patch chaining by coincident endpoints, and
the placement transform (anchor/yaw-73°/scale-0.55 method) all transfer
(`gamecube-world.md` §§Terrain resource, Race course). Tricky inventory: 10
courses, 730–4,268 patches, largest 1.9 MB terrain vs ~11 MB/race-location
budget — textures dominate (`tricky-courses.md`). Terrain contact follows
edited coefficients within 4 units (`rebuild-experiment.md` §Collision
result). Locale `.LOC` layout (`LOCH`/`LOCT`/`LOCL`, UTF-16LE, slot-fit rule;
Snow Jam = CMNAMER 549) is PS2-disc content
(`locale-tables.md`). Location anatomy: race = 8 texture groups + 1 gameplay
group (`location-anatomy.md`). RefPack optimal-parse encoder exists
(`refpack_optimal.py`).

**GC-only.** GX tiling (CMPR 8×8, 16-bit 4×4, CI8 8×4), kind-9/10 header
(`0x10|GXfmt`; CI8/CMPR stock, +RGB565/RGB5A3 Tricky), CI8 palette chunk,
SHPG containers, binding words 412/416, lightmap cell half-texel inset
(`gamecube-world.md` §Textures). **TEV lightmap scale 2 (SSX 3) vs 1
(Tricky)** — halve donor channels into RGBA8; 108 matching draw pairs;
reconstruction ≤1 code value (`gamecube-materials.md` §Verified transfer).
Terrain record field map (430 B, tag `0x9B7F4F`, corners 336, bounds 384,
ordinal `track<<24|rid`) vs PS2 432 B / `0x4045B7` / `rid<<8|track`
(`gamecube-world.md` §Terrain resource). Kind-11 occlusion curtains (17 on
Snow Jam; one crossed 210 sight lines — remove on full replacement)
(`gamecube-world.md` §Other records). GDB global capacities at +24 (kind 9:
788, kind 10: 662) and per-location 28-capacities at +32
(`gamecube-world.md` §Index). Button-glyph repaint is GC-sheet-specific
(`patch_ui_glyphs.py`). Texture-remaster runbook (dump→inventory→upscale→pack;
magenta proof-pack; R&B 198 textures/64 MB; whole-game method) is
Dolphin-pipeline-specific and archived by policy
(`texture-remaster.md` §§7–8; `asset-policy.md`).

**PS2-side notes from the same era.** PS2 path used ISO rebuild + PCSX2
(`odin-testing.md` §§Put the build, handheld; `full-course-experiment.md`
§Placement); run-gari-010 ISO SHA `5e53a815…` (`odin-testing.md`).
`SLUSOVF.BIG` overlay + file-table bsearch bug are PS2-lane findings, not GC
(`docs/facts.md` §Guest).

### 4a. Asset / texture preload (added by the orchestrator, 09-26; Brad asked)

GameCube finding (`docs/reserve/texture-remaster.md` §10.12–10.13; `docs/reserve/asset-policy.md` §Normalization):
- **Loading textures on first draw causes mid-ride stalls.** With Dolphin's hi-res pack loaded lazily
  (`CacheHiresTextures` off), every burst of new art — a crash, a camera cut, new terrain — read and
  PNG-decoded on the frame that needed it; the frame rate dipped and recovered.
- **Preload moves the cost to boot:** `SSXPreloadTextures` loaded the whole pack at start (894 MB decoded
  for `pack-v8`, 8,806 PNGs before the first frame; fine on an 8 GB phone, slow to boot).
- **Block compression removes it instead:** the phones support BC (A18 Pro `supportsBCTextureCompression = True`),
  so a DDS pack (927 files with mip chains, `tools/texture_pack_dds.py`) uploads without decoding and BC1 cuts
  894 MB to ~110 MB, which makes preloading nearly free.
- Startup side (`startup-shortcut.md`): the ~5 s pre-menu wait on the phone was CPU-bound asset load; latency
  knobs (fast disc) saved ~0 there.

PS2 route: **not applicable yet, documented for later.**
- Stock SSX 3 on our runtime has no host texture pack: the guest uploads textures into the 4 MB GS VRAM itself,
  and paraLLEl renders from that (no texture-replacement path). The lessons apply if/when we add hi-res texture
  replacement (a remaster pack): preload or block-compress, never decode on the draw.
- Nearest current analogues: paraLLEl's **pipeline/shader compiles** (MD1 counted 3,496 pipeline creates during the
  title screen and none in the race — already a de facto warm-up) and **Turnip's first-use shader compiles** on the
  Odin; host **file-read caching** of the ISO for loading screens (measure first; GC showed latency knobs don't help a
  CPU-bound load).
- Candidate items (parked): texture-replacement design (preload + BC, paraLLEl hook) if a remaster is ever wanted;
  a pipeline-cache persist/prewarm check on the Odin (does the first race stutter on first-time shader compiles?).

## 5. Tooling inventory

One line per tool (phrasing follows `tools/README.md` + docstrings); PS2
verdicts: **reuse** = usable as-is · **adapt** = port the idea ·
**done** = PS2 lane already has it · **gc** = GC-only · **hist** = PS2-era,
kept for reference. Harness pieces marked (H).

**Read-only inspection.** `inspect_disc.py` ISO9660/EA-BIG inspection —
**reuse** · `probe_worlds.py` staged-world inspector + previews — **adapt**
(PS2 `.big`) · `verify_inputs.py` ISO inventory vs bsdtar — **reuse** ·
`inventory_tricky.py` Tricky PS2 course inventory — **hist** ·
`location_inventory.py` per-location kind census — **reuse** (PS2 SDB) ·
`gamecube_research.py` static research leads — **gc** · `gamecube_telemetry.py`
rider observations via MemoryWatcher — **adapt** (PS2: pad/vblank taps exist) ·
`pine.py` minimal read-only PINE client — **reuse** (PCSX2 ref, T lane).

**Native runtime harness (H, GC).** `native_gamecube.py` pinned macOS AOT
build/run — **gc** (method: pinned deps + receipts → **adapt**) ·
`gamecube_game_dir.py` stage a game dir differing in worlds — **adapt** ·
`course_manifests.py` write course-redirect manifests — **adapt** (PS2 RAM-patch
analog for test events?) · `gamecube_course_check.py` bounded course checks —
**adapt** (PS2: `ssx3_boot.py` + leases cover this → **done**, E/N) ·
`gamecube_course_fixture.py` race-start fixtures — **adapt** ·
`gamecube_input.py` bounded pipe-controller input — **adapt** (PS2: pad scripts
I26-FAST → **done**, E) · `native_determinism_check.py` movie record/replay/
dispatch-trace compare — **adapt** (PS2: `PS2X_DETERMINISTIC=1` + VBlank hash →
**done**, E) · `gamecube_native_trace.py` callback observer/JSONL — **adapt** ·
`native_route.py` waypoint-steering lib — **adapt** (PS2 autopilot candidate) ·
`gamecube_movie_ab.py` trajectory-gated movie A/B (resolves ≥3–4%) — **adapt**
(PS2 det-hash A/B → **done**, E) · `gamecube_parity_compare.py`
guest-time-aligned parity — **adapt** · `gamecube_f_regression.py` F-battery —
**gc** (EN1) · `gamecube_schedule_check/schedule_trace.py` scheduling
player/summary — **gc** (EN1) · `gamecube_perf_spike.py` probe-overhead
compares — **adapt** · `native_probe_io_bench.py` trace-emitter CPU/storage
bound — **adapt** · `gamecube_draw_trace.py` GX draw-state diagnostic players +
comparator — **adapt** (PS2: `g46_rec2gs.py` + gsrunner → partly **done**, G/T) ·
`gamecube_present_trace.py` Metal presentation observer — **adapt**
(`metal_gpu_ms.py` below) · `gamecube_replay_check.py` captured-frame
prerequisite check — **adapt** (PS2: capture+replay GS gate → **done**, G) ·
`gamecube_startup.py` isolated startup observer — **adapt** ·
`gamecube_line_tables.py` guest-PC line tables for chunks — **adapt** ·
`native_float_classify/conversion/module_spike.py`, `native_module_opt_spike.py`
isolated FP/codegen experiments with receipts — **adapt** (method reused by
VR/VB lanes) · `loading_speed_spike.py` FastDiscSpeed paired A/B — **gc**
(config; method → **adapt**).

**Course conversion (GC donor→slot).** `gamecube_terrain.py` terrain+art+paths
replacer — **gc** (PS2 analog `replace_terrain.py` below) ·
`gamecube_scenery/scenery_import.py` reader/importer — **gc** ·
`gamecube_collision/collision_import/collision_check/collision_shrink/
collision_trace.py` reader/importer/checks — **gc** ·
`gamecube_splines/spline_import.py` rail reader/importer — **gc** ·
`gamecube_surfaces/surface_import.py` behaviour transfer — **gc** ·
`gamecube_materials.py` lighting translation + profiles — **gc** (profile idea
→ **adapt**) · `gamecube_textures.py` texture/lightmap import — **gc** ·
`gamecube_interactions.py` physics→behaviour binding — **gc** ·
`gamecube_lun.py` LUN assembler/placer — **gc** · `gamecube_startgate{,_handler,
_trace}.py` countdown assets/handler/observer — **gc** · `gamecube_shape.py`
`.gsh` list/decode/patch — **gc** · `gamecube_slot_probe.py` target-slot needs
— **gc** · `gamecube_reset_paths.py` grounded reset-path compiler — **gc** ·
`gamecube_cleanup.py` dependency cleanup — **gc** · `gamecube_world.py` world
reader/writer — **gc** (PS2: SDB/SSB equivalents below) ·
`gamecube_snow_check.py` ground-corruption scorer — **adapt** (oracle idea) ·
`gamecube_shot_align.py` screenshot-sequence aligner — **adapt** ·
`course_preset/route.py`, `race_course.py` presets, route measure, race
assembly — **adapt** (preset+recipe pattern) · `build_gc_iso.py` GameCube disc
rebuild — **gc**.

**PS2-era archive/image/ride tools (hist; directly PS2-shaped).**
`build_course_image.py` verified image = rebuilt world + event name + locale —
**reuse** pattern · `build_world_experiment.py` bounded control/bump archives —
**reuse** · `build_test_images.py` test ISOs by verified block substitution —
**reuse** · `recompress_stream.py` in-place block re-encode — **reuse** ·
`relayout_stream.py` own block boundaries (also emits valid GC blocks —
`gamecube-world.md` §Stream) — **reuse** · `relocate_archive.py` world archive
into padding extent — **reuse** · `grow_group.py` grow a group with raised
patches — **reuse** · `replace_terrain.py` full Tricky-PBD terrain replacement —
**reuse** · `import_terrain.py` M4 corridor placement — **reuse** ·
`import_crossing.py` ride-log vs imported/original surface — **reuse** ·
`patch_crossing.py` rider-Z vs original/edited surface — **reuse** ·
`patch_executable.py` event rename in-image (menu showed "Garibaldi") —
**reuse** · `patch_geometry.py` conservative Bezier-hull bounds — **reuse** ·
`patch_locale.py` slot-fitting locale edits — **reuse** ·
`ride_autopilot/course/locations/route.py`, `ride_compare.py` PINE waypoint
steering, descent record, location mapping, Green-Station routes, trace compare
(floor-vs-A/B stats) — **reuse/adapt** (PCSX2-side; `ride_compare.py` method →
**adapt** for PS2 traces) · `terrain_contact.py` rider-vs-decoded-terrain
contact — **reuse** · `track_position.py` EE-`0x5409c0` JSONL logger — **reuse** ·
`prepare_emulator.py` separate PCSX2 profiles/launchers — **reuse** (T lane) ·
`inspect_state_patch.py` find disc patch in savestate EE RAM — **reuse** ·
`frame_match.py` pair screenshots across replays by rider position — **adapt** ·
`refpack_encode.py` deterministic 10FB encoder — **reuse** ·
`refpack_optimal.py` optimal-parse 10FB for fixed blocks — **reuse** ·
`course_census.py` per-course render-cost census — **adapt** (PS2 cost census
idea) · `paths.py` env-driven data locations — **reuse**.

**120 Hz / media (EN1 owns; kept to one line).**
`gamecube_reprojection{,_hud,_warp}.py`, `mobile_pacing_check.py`,
`mobile_frame_cost.py`, `mobile_report.py`, `course_census.py`,
`android_trial.py` — **gc**; `mobile_frame_cost.py` filtering discipline
(thermal/config-matched rows or inconclusive,
`performance-batch2-followup.md` §Bounded comparison) → **adapt**.

**Texture remaster (archived; `asset-policy.md`).** `texture_pack_{plan,union,
audit,blend,cap,dds}.py`, `texture_dump_inventory.py`,
`texture_compare_sheet.py`, `upscale_textures.py`, `foliage_detail.py`,
`pack_mipmaps.py`, `lightmap_orientation.py` (8-orientation R² fit picked
identity: Snow Jam 0.287, Tricky 0.612 — `gamecube-world.md` §Runtime) —
**gc**; audit-gate pattern → **adapt**.

**Device / platform.** `mobile_gamecube.py` iOS build/sign/deploy/collect —
**gc** (per-lane deploy scripts cover PS2) · `metal_capture.py` /
`metal_gpu_ms.py` GPU-trace capture/busy-time — **reuse** (Mac GPU) ·
`deploy_odin.py` push disc image over adb with size+MD5 verify — **adapt**
(pattern predates APK flow; verify-before-use rule kept) ·
`live_course_request.py` desktop course-request protocol — **gc** ·
`native_replay.py` replay iOS benchmark sequences on desktop — **adapt** ·
`ios_diagnostic_sources.py` iOS-only diagnostic copies — **gc** ·
`macos/share_keepalive.py`, `muse_mon.py`, `muse_brief.sh`,
`odin_sampler.sh`, `odin_wireless.sh` shell helpers — **reuse** as useful.

## 6. Measurement and process lessons (still relevant)

- **Determinism first, then A/B.** Record (movie/inputs) → replay ≥2× for the
  floor → change one thing; free runs diverge immediately (449 med units, same
  archive) while replays agree 879/879 (`aloha-conversion.md` §§9.2–9.3).
  Dual-core/device runs are nondeterministic by construction; route-control or
  movie-driven input is the pairing instrument
  (`performance-batch2-followup.md` §Result; `route-control.md`).
- **Under any cap, speed hides wins as idle** — report CPU-ms/guest-frame or
  busy fraction (`review-2026-09-17-recomp-perf.md` §1). Already a ledger rule.
- **Receipts pin effective config + binary identity**: module SHA (M5 staleness,
  `review-2026-09-17-architecture.md` §1), requested-vs-effective flags (backend
  override, `review-2026-09-17-recomp-perf.md` §1), inherited env (Metal
  validation, §4), loader/codec identity (vertex-loader JIT vs software, §4).
- **Measurement configs differ from ship**: determinism double-decodes FIFO;
  budget verdicts from movie runs carry a tax the product won't pay
  (`review-2026-09-17-recomp-perf.md` §4). Same caution for PS2 det-harness vs release.
- **Observers perturb**: per-dispatch clocks −25–30%; failed MemoryWatcher
  reads can raise guest PI interrupts (use checked reads);
  host-timed input ≠ same trajectory; screenshots/readbacks inside the window
  (`120hz-cpu-overhead-spikes.md` §Next priority; `review-2026-09-17-recomp-perf.md` §4).
- **Oracles must check the thing claimed**: `present.csv` passed on duplicate
  frames; `blended>0` (better: blended/loaded ratio) is the interpolation gate
  (`review-2026-09-16-architecture.md` §1;
  `review-2026-09-17-architecture.md` §5). Trial lifecycle in scattered statics
  with comment-only ordering invariants caused exactly this class of bug (§2).
- **Verdict-grade runs get an exclusive host/device** (sibling-run
  contamination, ENOSPC, exFAT zeroing) + a current-numbers ledger, or stale
  numbers keep getting cited (`review-2026-09-17-recomp-perf.md` §7). Both are
  now standing rules (`AGENTS.md`; `docs/numbers-ledger.md`).
- **Phone/device discipline that transferred**: thermal state as the binding
  metric; always `install`; force-stop after every run; battery/AC + keyguard
  checks (`performance-review-2026-09-13.md` §Batch 1; `AGENTS.md` §Devices).
- **Course/content method**: validated-compiler mindset (bad reference fails
  the build with an ID, not in-game), immutable recipes + readback, separate
  residency budgets (geometry vs images), course owns its environment
  (`reserve/architecture-review.md` §§Prioritized; `gamecube-scenery.md`
  §Target format).

## 7. Candidate PS2 work items (ranked; orchestrator to consider)

1. Audit always-on diagnostic branches on the EE/VU hot path (GC's journal
   branch + sampling default) — `review-2026-09-17-recomp-perf.md` §3.
2. Specialize one measured hot constant-target external call past
   `dispatchGuestBranch` (GC's direct-C-call item) — RV4 §1; `...-recomp-perf.md` §2.
3. Order-file/PGO for generated code after codegen settles (fcmp −7.3% lesson;
   ingredients exist) — `...-recomp-perf.md` §§2, 6.
4. Idle/wait-loop fast-forward to next event + host sleep, only after hot
   polling is demonstrated (PCSX2 `IntCHackCheck`/loop-detection model) —
   `...-recomp-perf.md` §1; RV7 §2.
5. Host-side FS caching for loads if measured (GC: latency knobs ≈0 on
   CPU-bound phone; never earlier guest completion) — `startup-shortcut.md`
   §Measured; RV7 §2.
6. Narrow save-state identity to what a savestate depends on; cache the tree
   hash (GC: 84 rejections, ~1.9 s/launch hash) — `startup-shortcut.md` §lever.
7. PS2 waypoint autopilot (PINE/pad-script steering) for deliberate-contact and
   gate/finish claims — `route-control.md`; `ride_autopilot.py`.
8. PS2 boot-time RAM redirect for test events (GC course-manifest pattern;
   needs PS2 table addresses) — `course-selection.md`.
9. Reuse `patch_executable/patch_locale/refpack_*` + rebuild tools for PS2 test
   ISOs (rename/menu/description/terrain edits) — `locale-tables.md`;
   `rebuild-experiment.md`.
10. Guest-time-aligned trace parity + rider-position frame pairing for PS2 A/B
    (`gamecube_parity_compare.py`, `frame_match.py`, `ride_compare.py` floor
    method) — `aloha-conversion.md` §9.3.
