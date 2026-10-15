# SSX 3 engine timing: one GameCube + PS2 reference

Why: the parked GameCube 120 Hz work and the PS2 TM1–TM4 lane mapped the same
engine structure independently (app-manager `checkHalt` loop, timer
accumulator, 30-entry buffered input ring). This file merges them so neither
side is rediscovered. Docs only; no new experiment.

Pins: GC GXBE69 DOL sha256 `b92162d6…29ce`, map rev `ec889b87…f7a35`
[GC-analysis §Initial]; PS2 `SLUS_207.72` sha256 `1b49d05c…af7bc`, gp
`0x4a30f0` [RV6 §Scope]. GC addresses are DOL VAs; PS2 addresses are EE VAs.
"Proven live" = observed in a live boot; "static only" = disassembly/ELF read;
"inferred" = fits counts but order/role unobserved. GC↔PS2 pairs are marked
*structural match* (same shape, role not cross-proven) or *verified same role*
(same role demonstrated live on both).

## 1. The frame, end to end (platform-neutral)

Pipeline (steady state, one video tick):

```
video tick (VI retrace / VBlank-end) → worker wake → timer/accumulator
  → producer writes buffered record(s) into ring → main loop drains ring
  → 0..N app updates (catch-up bounded, else resync) → per-update game work
  + rider integration (NOT necessarily inside the app update) → snapshot/copy
  → render gate (mode-dependent) → scene render + elapsed-time bookkeeping
  → present (host display opportunity)
```

Terms, as used here [RV6 §1; TM1 §Counts; GC-analysis §Resolved]:

- **Tick**: one video-tick service opportunity (one VI retrace on GC, one
  VBlank on PS2). The engine's scheduling quantum.
- **Update**: one execution of the active application's update callback
  (GC `0x8010550C`, PS2 `0x2306b8`). Steady state: 1/tick on both.
- **Render**: one execution of the scene-render path through the render
  callback (GC `0x8010A4C8`, PS2 `0x22b008`), counted only if it passes the
  readiness gate and traverses view setup + frame end.
- **Present**: a host display delivery (positive Metal `presentedTime` on GC
  tooling; a display present on PS2/Odin). Updates, renders and presents are
  counted separately; none implies the others [GC-pacing §Policy; RV6 §5].

Catch-up rules (PS2 proven live; GC structurally analogous but its bound is
unnamed) [RV6 §2; TM1 §Observed; GC-analysis §Initial]:

1. Each tick's producer output is an integer ≥ 0 derived from
   accumulator-before/after (fraction retained). Multiplier 1 ⇒ 1/tick.
2. The drain loop consumes until the ring is empty or the catch-up bound
   hits (PS2 bound 12 at `A+0x20`). Each success runs one app update.
3. Past the bound, PS2 resynchronizes (consumer ← `(producer+29)%30`) and
   returns backlog accounting instead of running the skipped updates —
   there is no unbounded faithful catch-up [RV6 §2].
4. Rendering is separately gated: PS2 mode 0 renders when `$s1 != 0`, mode 1
   requires `$s1 >= 2`; success clears `$s1`, failure retains it [RV6 §2].
   GC renders pass a two-entry graphics-queue readiness gate instead
   [GC-render-seam §Handoff].

Two facts both lanes learned the hard way: the input ring is **not** a physics
substep counter (four channels ≈ controller input on GC; two channels consumed
per success on PS2) [GC-analysis §Resolved; RV6 §1], and the rider may move
**outside** the app update (GC: renders never move the rider but updates do;
PS2: neither update nor render writes `0x5409c0` — integrators + a snapshot
copy do) [GC-f §Phase 1; TM1 §Rider; TM2 §Writer].

## 2. Address map

Manager objects: GC global manager via `0x803DA9D8` (vtable `0x802E5468`,
active app vtable `0x802E543C`) [GC-analysis §Resolved]; PS2 manager `A` at
`[gp+0x2a74]` (live `0x4c9428`), vtable `0x47d9c8` at `A+0x5c`, active table
`0x47d110` [RV6 §1; TM1 §Observed].

| Engine piece | GameCube | PS2 | Confidence | GC↔PS2 | Source |
| --- | --- | --- | --- | --- | --- |
| Manager nominal rate (60) | obj field off 20 = 60, set at `0x801CD750` | `A+0x10` = 60, set `0x316d6c/98` | GC static+live lead; PS2 proven live | structural match | [GC-analysis §Initial; RV6 §1; TM1 §Observed] |
| Manager dt (1/60) | not found (no single dt; `count/60.0` at render) | `A+0x14` = `*(0x4a01f8)`, set `0x316d64/88` | PS2 proven live | not found on GC | [GC-native §Boundaries; RV6 §1; TM1 §Observed] |
| Producer multiplier / accumulator | acc off 44 += off 40 at `0x801CD248` | `A+0x24` = 1.0 / `A+0x28` = 0.0, stepped `0x317364–c8` | GC live role; PS2 proven live | verified same role | [GC-analysis §Initial; RV6 §1; TM1 §Which] |
| Catch-up bound | not found | `A+0x20` = 12, checked `0x317188–90` | PS2 proven live | not found on GC | [RV6 §1–2; TM1 §Observed] |
| Render mode | 2-entry queue gate, count `r13-20556` | `A+0x34` = 0, tested `0x3171e0–244` | both proven live | structural match (different gates) | [GC-native §Boundaries; RV6 §1–2; TM1 §RV7] |
| Smoothed rate metric (not dt) | not found | `A+0x2c/0x30` = 59.99995/0.9 | PS2 proven live | not found on GC | [RV6 §1; TM1 §Observed] |
| Timer wrapper / worker wake | `0x801CD144` via worker `0x801CAB4C`, wake `0x801CABAC` | `0x317348` via `0x317500`, INTC-3 worker `0x31ac08` | both proven live | verified same role | [GC-native §Boundaries; RV6 §1; TM1 §Observed] |
| Producer / consumer / 30-ring | `0x8010EEA0` / `0x8010EECC`; ring fill `0x801D14C4`, drain `0x801D158C` | `0x227f58` / `0x227e98`; ring `0x326b88/48/60`, obj `[gp-0x850]` | both proven live | verified same role | [GC-analysis §Resolved; RV6 §1; TM1 §Observed] |
| Main loop + `checkHalt` | `0x801CD33C` (via `0x801CD750`) | `0x317180` drain in `sub_00316F00` | both proven live | verified same role | [GC-analysis §Initial; RV6 §1–2; TM1 §Counts] |
| App-update slot | active vtable+28 → `0x8010550C` | active table+`0x34` → `0x2306b8` | both proven live | verified same role | [GC-analysis §Resolved; RV6 §1; TM1 §Observed] |
| App-render slot | active vtable+32 → `0x8010A4C8` | active table+`0x3c` → `0x22b008` | both proven live | verified same role | [GC-analysis §Resolved; RV6 §1; TM1 §Observed] |
| Game update under app update | `0x8001FB58` via app+12/obj+36/slot+16 | not found (update body unmapped) | GC proven live | not found on PS2 | [GC-native §Boundaries; GC-f §Phase 2] |
| View update | `0x8006AF2C` → `0x80069B04` | not found | GC proven live | not found on PS2 | [GC-native §Boundaries; GC-f §Phase 2] |
| Rider integrator(s) | fixed-step/update, sites unmapped to one PC | `pos+=delta`: `0x1380b4` (mode 1) / `0x13e0dc` (mode 2) | GC inferred; PS2 proven live | structural match | [GC-f §Phase 1; TM3 §Part A] |
| Integrator private 1/60 | ~209 loads, clusters; no per-site patch | K1 `*(0x49be9c)`, K2 `*(0x49c08c)`, K3 `*(0x49c12c)`, each single-reader | GC static only; PS2 proven live | structural match | [GC-f §Phase 2; TM4 §Part A–B] |
| Damping / friction per-step | `0x8002784C` ×0.98/0.956/0.978333 (`0x803DB7E8/EC/F0`); governor `0x8002AD44` | `vel+=` at `0x1380e4/448`, `0x13e120`; rescale `0x13c86c` | GC proven live; PS2 proven live | structural match | [GC-f §Phase 3b/3d; TM4 §Part A] |
| Global tick counter + stamps | global stamped/element (values equal); mod-6 periodic body+`0x24/28` | `A+0x1c` via drain `0x3171a4`; stamps not mapped | GC proven live; PS2 proven live (counter only) | structural match | [GC-f §Phase 3b–d; TM1 §RV7; TM2 §HUD] |
| Countdown / race-start time base | start-gate setup `0x80007xxx` (unmapped) | C1–C4: `0x49b480/8c/a0/bf1c` in `sub_00139A20→113648`; thresholds `0x49b494/bf20` | GC inferred; PS2 proven live | structural match | [GC-f §Phase 3d; TM4 §Part 3] |
| Snapshot copy to observed pos | not found (probe reads rider+240 live) | `sq` at `0x120e40` (`[a0+0x110]`→`0x5409c0`), via `0x1218b4` | PS2 proven live | not found on GC | [GC-native §Experiments; TM2 §Writer] |
| HUD clock input + formatter | not found | input `A+0x1c`, slope `(updcnt−1690)/60`; formatter not found | PS2 input proven live | not found on both (formatter) | [TM1 §HUD; TM2 §HUD; TM4 §Part 3] |
| Frame-skip / half-rate gate | readiness count==2 rejects (polled) | Metro: `[[this+0x84]+0x10]>=2` else `0x2ee400(6)>0.9`; NOPs `0x230704/10` | both proven live | structural match | [GC-render-seam §Handoff; RV6 §2; RV7 §4; TM1 §RV7] |
| Render elapsed bookkeeping | app+168; `count/60.0` (`0x803DCEE0`) at `0x8010AC0C` (+`0x80109CB4`); helpers `0x8015C5A0`, `0x80139F20` | `(A+0x1c−saved)×A+0x14` at `0x3175b4–d8`; `10.0`/`0x317530` on transition path only | GC proven live; PS2 static only | structural match | [GC-native §Boundaries; GC-f §Phase 2; RV6 §2–3] |
| Live position observable | rider+240 (12 B) | `0x5409c0` triple (snapshot); live `[obj+0x110]`, obj `0x1465c40` | both proven live | verified same role (both downstream of integration) | [GC-native §Experiments; TM2–3] |
| VBlank/retrace → INTC chain | VI → `0x801CABAC` → worker → `0x801CD144` | VBlankEnd → INTC 3 → `0x31a490` → sema → `0x317500` | GC live; PS2 static+live wake | verified same role | [GC-native §Boundaries; RV6 §1; TM1–2] |

Notes: GC "offset 20/32/40/44" are the analysis doc's units (not re-derived
here) [GC-analysis §Initial/§Resolved]. PS2 `$s1` mixes executed updates with
resync accounting — not a physics counter [RV6 §2]. PS2 1/120 words at
`sub_0031BE50/31C040` are sin/cos Taylor coefficients, not timestep [TM4 §B].

## 3. What each experiment proved or ruled out

### GameCube (parked; methodology transfers, addresses/timings do not)

- **Repeated render / seam** [GC-native §Experiments; GC-render-seam
  §Tests]: 119/120 naive repeats fail the readiness gate; with queue-wait +
  helper-skip, 120/120 extra draws complete with watched state unchanged.
  Frozen normal/offset/restored: offset changes 2.76M/2.83M pixels, restored is
  byte-identical. Proved: a repeatable draw boundary exists. Ruled out:
  bypassing the gate or naive "draw twice" as a product path.
- **Independent schedule** [GC-schedule §Evidence]: host-copy variant,
  1,888 draws at 75.52/s with updates at 59.72/s, 396/396 extras clean, 0
  retries. Proved: extra draws can coexist with the validated update rate.
  Ruled out: guest double-buffering (single XFB; second pointer zero; the
  completion-mode probe faulted at PC `0x400` before any extra render).
- **Pose interpolation** [GC-interp §Measured]: identity + history path works
  (8,192-key bounded history, teleport/NaN rejection), but immediate-XF
  uploads cost ~1,700 blended matrices/frame — the phone vetoes everything
  (1 extra, 186 vetoes, 3 s guard exit). Proved: mechanism; ruled out:
  immediate-upload as shippable. Retained host palettes are the prerequisite.
- **Host replay** [GC-replay §Milestone/§Correctness]: 120/120 replays,
  unmodified replays byte-identical, offset replay differs; single replay
  ≈ 4 ms non-display-bound. Then **blocked by design**: the decoder has
  guest-visible/persistent side effects (PE/timing, EFB cache, XFB/present,
  live-RAM reads, register/batch state), so the loop was removed pending a
  `ReplayContext`. Ruled out: replay without renderer isolation.
- **Reprojection** [GC-reproj §Result/§Capture]: paired-background HUD alpha
  recovered (7.04 % coverage, mean α 0.727, 99.99 % within 1 level; EFB is
  `RGB8_Z24`, no dest alpha); footprint splat closes 77–81 % of holes;
  camera/depth convention validated on two static walls (MAE 10.28→1.12,
  14.50→2.60). Open: live scheduler, GPU-resident cost, dynamic objects.
- **F spike: true 120 Hz sim** [GC-f §Phase 1–4b]: 2× updates hold 97 % guest
  speed on desktop (pair 5.31 ms, p95 10.31) — survives the kill floor. But:
  v0 (11× 1/60→1/120 + 60→120) over-corrects to 0.75× (fixed-step damping
  applies twice); v1 (+√damping) reaches 0.93×/0.97× free-run, then diverges
  on the movie (float epsilon flips a threshold within ~100 doubled ticks;
  butterfly does the rest). Integer bookkeeping (global counter stamps,
  mod-6 periodic, script phase) is exactly normalizable (v3b suite PASS);
  floats are bounded-jitter with flips — ceiling is **statistical parity**,
  and the 4-movie bias battery passes leaning-go (lateral mean −863 units,
  mixed signs). Live inputs need no latch (re-poll is correct; latching
  would double-fire edges). Mid-tick draws evict ordinaries on desktop
  (59/59 rejected) but phone XFB extras are exactly additive (92 displayed/s).
  Route F verdict: CONDITIONAL-GO to bias trial, then dead on this build
  for ship (D6; reserve.md).
- **Pacing/overhead** [GC-pacing §Evidence; GC-cpu §Spikes]: short near-120
  sections exist (119.75 displayed/s over 4 s, 8.334 ms p95); no 25 s
  sustained pass. Extra-draw cost is CPU-busy (wall−CPU ≈ 0.03 ms); probe
  emission ≈ 1–2 µs/callback (not the bottleneck); O2→O3 shows no benefit.

### PS2 (current; TM lane + reviews)

- **RV6 static model** [RV6 §1–4]: VBlank-end-driven buffered fixed-step with
  catch-up and separately gated rendering; nominal 60/1/60 plus independent
  literals and timer-count consumers. Corrected X3's branch map (six edges +
  counter-reset site). Predicted: doubling VBlank ⇒ accelerated gameplay;
  halving manager dt alone ⇒ slowed dt-consumers only.
- **TM1: chain live** [TM1 §§Outcome–Which]: 601/601 VBlanks deliver exactly
  1 wake → 1 record → 1 consume → 1 update → 1 render; all RV6 addresses
  bit-exact; tap non-perturbing (armed run det-identical). Rejected for the
  exercised path: render-coupled updates, substeps in one update (for
  `0x5409c0`), variable-time consumers. Found: `0x5409c0` bit-stable across
  all 601 updates (writer elsewhere); Metro `bc1t` never taken (NOPs a no-op
  here); HUD slope exactly 1 s/60 updates.
- **TM2: writer + clock input** [TM2 §§Outcome–HUD]: `0x5409c0` ← one `sq` at
  `0x120e40` (snapshot of `[obj+0x110]`), thread 1, after the update, outside
  the render frame; DMA ruled out (new SPR_FROM watch rows silent). HUD input
  ← `A+0x1c` at the drain; `/60` formatter not found (no div, no 60/3600
  const, no float→int in render body + 10 callees + producer/consumer/update).
- **TM3: integrators + rate-120 probe** [TM3 §§A–B]: `[obj+0x110]` ← two
  alternating `pos += delta` sites (`0x1380b4`/`0x13e0dc`), after the update,
  no manager dt/rate on path. Probe (`A+0x10`=120, `A+0x14`=1/120,
  `A+0x24`=2.0): exactly 2 updates/VBlank, route still races — but motion
  1.89×/VBlank (full steps) and HUD 2× (hard `/60`). Proved: manager rate/dt
  are not the physics timestep; audio correctly ignores the probe (own
  93.75 Hz timebase, counters identical).
- **TM4: delta chain + coherent probes** [TM4 §§A–C, Parts 2–3]:
  `delta = vel × ([obj+0x300] × K)`, K1–K3 private single-reader 1/60s;
  `[obj+0x300]` = 1.0 init-set (zero race writes). `=2` (+K1–K3→1/120):
  half-steps exact (tscale median 0.5002) but 1.148×/VBlank — FAIL ±5 %,
  mode-1-specific velocity heat. `=3` (+halve `0x49b828`): 1.152× —
  **K4 exonerated** (1e-3 trajectory perturbation); residual is a pre-window
  initial condition (1000 vs 635, decaying to +3.8 %). `=4` (+countdown
  C1–C4→1/120): launch speed fixed (0.977→1.000 over 33 VBs) but **phase
  timing breaks** (countdown/ride jump-table flicker, 230-unit pos gap,
  teleports leak into cruise; 1.108× confounded). 40 indirect render targets
  enumerated; HUD `/60` + −1690 base still unlocated. Lane closed; 120 Hz
  research paused until the Odin nears real time.
- **Community patches** [RV7 §4; RV6 §2]: Metro NOPs suppress one conditional
  flag-update (branch + store), not host cost — no-op on TM1's route. 2P
  `+2` changes accounting/render policy (reaches bound 12 sooner; meets
  mode-1 threshold after 1 update), not the timestep. Aspect scalars
  `0x622600/604` = 0.75/1.0 are live and constant (game's own anamorphic
  values) [TM1 §RV7]. No verified normal-speed 120 Hz PS2 patch exists in
  the inspected collections.

## 4. Open questions for true 120 Hz (dependency order)

Each names the PS2 lead, then what GC already settled (if anything) — so the
PS2 lane does not redo it.

1. **Countdown/race-start phase machine (PS2 lead; GC partially solved).**
   `=4` proved C1–C4 drive launch velocity but constant-halving breaks the
   countdown→ride selector, which reads per-update timer values [TM4 §Part
   3]. GC hit the same class: counters/periodics/scripts at 2× gate
   transitions and seed ±1-tick flips; the fix is once-per-tick integer
   replay (counter + stamps + app flag + RNG), proven by the v3b suite
   [GC-f §Phase 3d–e]. Still open on PS2: which phase-timer reads are
   accumulators (halve) vs thresholds/sequences (keep per-VBlank); the 230-
   unit pos gap's steering/phase lead; teleports leaking into cruise.
2. **Cruise aux/accel inputs (PS2 lead; GC solved the method).** Mode-1
   `sub_00138960`'s per-call aux rewrite (gain 200.0, no dt) is static
   plausibility only after the K4 lesson — needs an aligned early-race
   live window before any halve [TM4 §Parts 2–3]. GC's rule applies
   directly: single-reader const + live per-phase attribution first; the
   governor double-apply (~3 %/window) stays a renorm candidate, not replay
   [GC-f §Phase 3d; TM4 §Part 2].
3. **Float butterfly ceiling (GC solved; PS2 must adopt the bar).** GC proved
   two half-steps never bit-match one full step and flips land within ~100
   doubled ticks; the only viable bar is statistical (rate parity + line
   plausibility + unbiased multi-movie battery), with guest-timed windows
   (host windows inject ±30 % noise) [GC-f §Phase 3b–d, §Bias]. PS2 has no
   bias battery yet and must not quote single-pair drift as conversion
   error. Guest-timed edges already exist via det boots + tick windows
   [TM2 §Coverage].
4. **HUD clock formatter + race-start base (open on both; PS2 closer).**
   Input `A+0x1c`, slope `/60`, base −1690 (PS2) all live; the formatter and
   base store are unlocated after render body + 10 direct + 40 indirect
   targets [TM2 §HUD; TM4 §Part 3]. GC never found its HUD clock either;
   its render-elapsed `count/60.0` seam is the only mapped analog [GC-f
   §Phase 2]. Next: digit-buffer watch or deeper `jalr` chains from the UI
   cluster (`sub_00376938` et al.).
5. **AI riders' path (open on both).** Single call sites per integrator, no
   per-rider loop below `sub_001216E0`, one obj exercised; second snapshot
   site `0x128bc0` hints a second path [TM4 §Parts 2–3]. GC never separated
   AI either. Next: watch on a second rider's position (address unknown).
6. **Damping/accel renormalization set (PS2 lead; GC solved the math).**
   Equal damping over one old step needs half-step coefficients multiplying
   to `d` (√d each) — never `d/2` [RV6 §3; GC-f §Phase 3b]. GC's census
   method (SDA-resolved loads, cluster over singletons, mixed-rate 1/30
   precedent) and per-state attribution transfer directly [GC-f §Phase 2].
7. **Input cadence at 120 Hz (GC solved for live; PS2 open).** GC: game polls
   ~2×/VI (~117/s); doubled updates consume the DTM stream 2×, but live
   re-poll is already correct and latching would double-fire edges — gate 2
   became research-tooling-only [GC-f §Gate 2]. PS2: which producer samples
   carry fresh pad state is unmeasured; two samples/wake may repeat state;
   I26-FAST's vsync-indexed navigation is not a 120 Hz input oracle
   [RV6 §5–6].
8. **Ring overflow / backlog behavior (open on both).** Stock backlog volume,
   30-ring overflow/aliasing vs threshold-12 interaction, and intentionally
   discarded elapsed time are unmeasured on either platform [RV6 §5–6; TM1
   §Gaps]. Needs a guest-load perturbation brief (queued, not run).
9. **Timer/RCNT/audio ratio consumers (PS2 lead).** EE timers (four clocks,
   IRQs 9–12) and the 93.75 Hz sound tick are separate paths; no RCNT read
   appears in producer/consumer arithmetic (bounded negative), but race/
   audio/SDK consumers are unaudited [RV6 §4; facts.md §Guest]. A 120 Hz sim
   clock must leave GS fields, sound and SDK timeouts intact.
10. **Sim/render split + Odin service budgets (PS2 lead; GC de-risked
    presentation).** GC proved idle-time extras fit the drain cycle on both
    desktop and phone (zero eviction; 92 displayed/s combined) while
    mid-tick draws evict ordinaries — so the end architecture is VI-paced
    extras, not render-every-update [GC-f §Gate 4a–b]. PS2 still needs: 60 Hz
    budgets first, then the split schedule, then 120 Hz scheduling on Odin
    with unique-updates/wall-s distributions [RV6 §5; RV4 §4].

Non-goals restated: global 1/60 search-and-replace, doubling EE/VBlank/audio
clocks, the published FPS patches as timestep patches, and GameCube replay/
interpolation revival all stay parked [RV6 §5–6; RV7 §4].

## 5. How to measure (pointers, not recipes)

- **PS2 live chain**: TM1's observation-only tap (`PS2X_TIMING_TAP=1`,
  VBlank/update/render counts, `A` fields, pos before/after) — non-
  perturbing, det-identical [TM1 §§Tap–Commands].
- **PS2 writer attribution**: `PS2X_DIAG_WATCH=<addrs>` +
  `PS2X_DIAG_WATCH_TICKS=<from-to>` (store-tick windows; DMA `SPR_FROM`
  rows; `tm4-jalr` dispatch-target tap for indirect edges). Needs a
  DIAG_TAPS build; bradflix builds are non-DIAG [TM2 §Coverage; TM4 §Part 3].
- **PS2 probes**: `PS2X_TM_RATE120=1/2/3/4` ladder (manager-only → +K1–K3 →
  +K4 → +C1–C4); each step's acceptance is motion/VBlank ±5 % + HUD slope +
  SND counters + det-hash-from-tick-39 [TM3 §B; TM4 §§C, Parts 2–3].
- **GC present/upgrade traces**: `tools/gamecube_present_trace.py`
  (Metal `presentedTime` observer), `tools/gamecube_native_trace.py`
  (callback probes, double-render/scheduler/interp drivers),
  `tools/gamecube_schedule_trace.py` + `gamecube_f_regression.py` +
  `gamecube_parity_compare.py` (guest-aligned compare, counter-rate,
  drift report) [GC-analysis §Baseline; GC-f §Phase 4–4a].
- **GC determinism**: single-core `SSX3_MOVIE_PLAY` + guest-XFB arming gives
  byte-identical world color/depth; dual-core diverges in ~3 s and is not a
  comparator [GC-reproj §HUD; GC-f §Scouts]. PS2: `PS2X_DETERMINISTIC=1` +
  VBlank XXH64 tap + `baseline.py` keys [facts.md §Runtime; TM1 §Tap].
- ** Budgets**: speed numbers only from diagnostics-off builds, guest vsyncs
  per wall second ÷ 59.94; vsync-pad route I26-FAST for race boots
  [AGENTS.md].

## Sources (short keys used above)

GC-analysis `docs/research/120hz-analysis.md`; GC-native `…/120hz-native-
path.md`; GC-schedule `…/120hz-independent-schedule.md`; GC-replay
`…/120hz-host-replay.md`; GC-render-seam `…/120hz-render-seam.md`; GC-interp
`…/120hz-native-interpolation.md`; GC-reproj `…/120hz-reprojection.md`; GC-f
`…/120hz-f-spike.md`; GC-pacing `…/120hz-pacing-acceptance.md`; GC-cpu
`…/120hz-cpu-overhead-spikes.md`; reserve/plan `docs/reserve/plan-120fps-
2026-09-17.md`; reserve.md `docs/reserve.md`; RV6 `docs/research/review-
2026-09-26-astra-120hz.md`; RV7 `…-astra-emulators.md`; TM1–TM4
`local/research/TM{1,2,3,4}/REPORT.md`; facts.md `docs/facts.md`.
Reserve/ skim (collision, rails, scenery, world, materials) is course data,
out of scope per the brief; current `docs/reserve.md` carries no
determinism/timing section (parked summary only).
