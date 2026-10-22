# Full-120 conversion: running SSX 3 at 120 updates a second

SSX 3 steps almost all game-time state by a fixed 1/60 per update: pool
words holding 1/60 (and a few companion constants) are multiplied into
positions, velocities, timers, animation clocks and camera filters every
update. Run the update loop at 120 Hz unchanged and every one of those
systems plays 2×. The conversion finds each per-update term and halves it —
but only inside race events. Boot, menus, loading and pause stay at stock
rate (`events` mode).

## The general mechanism

There is no measured frame delta anywhere on these paths. Each system
consumes a hardcoded 1/60 slice: a private dt word, an additive step, a
fixed slew bound, or a per-update service (combo accrual, AI modulo gates,
particle passes). The conversion rule depends on the shape:

- **Private dt halved once.** Where the guest multiplies a force or velocity
  by its own timestep word, halve that word. The classic trap is halving the
  force coefficient *as well*: the snow-adhesion force at `0x13c878` is
  already integrated by the converted outer dt, so halving its coefficient
  too would quarter it per half-update. Force-vs-dt ownership has to be
  traced per call chain, not assumed per word.
- **Additive steps halved; event impulses kept.** A continuous increment
  (white-fade `1/40→1/80`, boost drain thresholds) halves; a per-landing
  award or a per-press meter coil stays stock.
- **Retentions square-rooted.** A per-update filter `x' = d·x + …` keeps its
  per-second decay with `d→sqrt(d)` (e.g. speed caps `.9/.97`, steering slew
  bounds); complementary input weights are adjusted to match.
- **Cadence, not values.** Combo service, AI `race_tick % N` gates, particle
  passes and whole-solver services run once per stock-equivalent pair (hold
  the alternate update, or serve the whole pass once per pair).
- **Stock domains stay stock.** Wake/ordinal counters, session/race clocks,
  RNG draw streams, integer contact thresholds, geometric tolerances and
  hardware/audio timers are preserved — halving a collision bound or an AI
  decision threshold changes the game, not the clock.

Each fix is a named `FIX` group applied by verify-then-write pool edits
(expected stock word checked before the events-mode word is written, and
restored on events exit) or by a hook; `FIX=all` takes every group. The full
inventory is in `docs/facts.md`. Deliberately outside `all` are `ground`
(the double-scale above), `lift`, and `flare` (iOS-only visual cadence); a
coupled ground feel-test package (`query` + `vnret` + `groundstep` +
`shadowpub`) and the tracker-phase experiment `inputchain` stay opt-in.

## The FIX groups and their sites

Guest PCs are EE virtual addresses for the USA disc (`SLUS_207.72`); pool
words are `0x49xxxx`-family constants.

- **Motion core:** `rider` (private integration dt at `0x137d68`,
  `0x13d8e4`, `0x13ee80`), `countdown` (prediction/execution solver quantum,
  residual, reciprocal as a coherent set), `drag` (air retention
  square-root, sign-dependent Z steps), `takeoff` (fixed 0.05 bound halved),
  `crash`/`crashbody` (solver and body/board dt), `bounce` (phase step),
  `recover` (wipeout-bar decay).
- **Control:** `stick`/`stick2` (input hold timers, air offset slews),
  `steer` (triple dt, fixed returns, proportional gain root) + `ground2`
  (exact piecewise half-map for the bounded steering residual),
  `speedcap` (cap relaxation root), `launch` (wall-transition projection),
  `input2` (digital guard/repeat ages in half-tick units, analog kept at
  120).
- **Tricks and scoring:** `anim` (13 node dt words) + `event` (the 14th,
  node rate×speed dt), `trick` (combo service cadence, style increment),
  `bonus` + `bonusflip` (award rate, mode-flip lifetime), `uber` (display
  bound; the drain itself is `boost`), `popups`/`hudprog`/`hudfill`/`meter`
  (display slews and coil steps).
- **Race systems:** `boost` (uber/resource timers + drain thresholds),
  `rail` (rail dt; balance coefficient untouched), `reset` (state-9 fade
  step), `aigate` (modulo AI decisions once per pair),
  `clock`/`clocksign`/`clock2`/`raceclock`/`session` (wake cadence, signed
  periodic restamp, VBlank accumulator, alternate-update holds, session
  frame counters), `entry` (start-gate airborne admission at the race
  start).
- **Camera:** `camera` (pool clocks/retentions), `pid` (controller history
  once per pair), `c2cap` (counter/rate pairing), `jcam` (countdown and
  landing-offset damping) + `jcam2` (a private stock-cadence predictor the
  camera query reads; live gameplay prediction is untouched).
- **World and presentation:** `flags` (phase delta post-call), `rclock`
  (stock-time ordinal from the accumulator, not the render device frame
  count), `emitter` (six private dt words), `fx` (glint/fader/wake-scroll
  clocks; the glint substream keeps stock dt under `rng`), `rng` (stock
  service cadence), `particles` (whole pass once per pair), `fxtimer`
  (post-call half decrement), `snddt` (sound-tree dt), `spin`/`texanim`/
  `loops`/`loops2`/`pulse` (angle/UV/loop phase steps), `envfilt`
  (squared-gain filter transform), `life2` (streaming ages/deadlines across
  flips, replay input ordinals).
- **Events mode** (`events`, not a conversion): enters at the race start via
  the first race-clock edge, so boot, menus, loading and pause never see
  converted words.

## Verification and measured matches

Every guest-affecting change must keep the four deterministic-boot hashes
(`reference`, `play`, `full120events`, `play120`) IDENTICAL, or mint a new
key through the full gate. Hashes alone do not prove feel, so each group
carries a fixed-input scripted line compared stock-vs-events at integer
game-time pairing (save past any contact, pair by the game-time ordinal —
the first events update after a stock load runs mixed, so tap-number
pairing near a contact is a harness artifact).

Measured matches on those lines: carve onset within 0.05°; rival finish
3:16 vs 3:15 with no AI wipeouts; crest segment within ~0.5 %; in-air trick
points identical after `anim`+`bonus`; trick-fail/landing identical within
half a stock frame (same ch2 safe-to-land bit, same L1 release boundary);
boost drain −0.005/s; white-fade reset 40.5 vs 41 stock ticks; backcountry
rivals 1.00–1.04× stock pace with rubber band off. Start, acceleration, top
speed, air and carving match within ~0.5–0.7 %.

Known residuals (open, under study): the bounded normal-response cap
(`c.z = max(c.z, −40)`) is evaluated twice per period instead of once, so
saturated recontacts can correct harder; selector-5 recovery advances a
stock 1/60 timer twice per period; contact/predicate cardinality at lips
(two geometric tests per period) can admit a different contact on the
intermediate state. None of these is fixed by halving another literal —
each needs its bounded map derived, not its constant cut.
