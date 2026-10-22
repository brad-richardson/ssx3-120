# Tricky host review: how Tricky courses ride on SSX 3 carriers

**Point-in-time analysis (2026-10-22).** This note summarizes a read-only
design review of the Tricky course conversion. Each Tricky course is baked
into a stock SSX 3 event location ("carrier") that supplies gates, AI
paths, reset paths, timers and physics; donor data from the Tricky disc
fills it. Read it as the map of *what the carrier supplies vs what the
donor supplies*, the known gaps, and the fix directions. Items marked
**Fixed since** landed after the review.

## 1. Course→carrier map

One mode per course (the hidden carrier rows for the other mode exist but
are not shipped). Race carriers host 6 gates; backcountry carriers host 2,
slopestyle/pipe carriers 3.

| Course | Carrier event (stock name) | Kept mode |
| --- | --- | --- |
| Garibaldi | ev0 Snow Jam (Peak 1 race) | Race |
| Snowdream | ev1 Metro-City (Peak 1 race) | Race |
| Elysium Alps | ev16 The Throne (Peak 3 backcountry) | Race |
| Mesablanca | ev2 Ruthless Ridge (Peak 2 race) | Race |
| Merqury City | ev4 Gravitude (Peak 3 race) | Race |
| Aloha Ice Jam | ev14 Happiness (Peak 1 backcountry) | Race |
| Pipedream | ev11 The Junction (Peak 1 pipe) | Showoff |
| Untracked | ev7 Kick Doubt (Peak 3 slopestyle) | Showoff |
| Tokyo Megaplex | ev15 Ruthless (Peak 2 backcountry) | Race |
| Alaska | ev6 Style Mile (Peak 2 slopestyle) | Showoff |

Forced placements: all usable 6-gate race hosts were taken, so Elysium went
to backcountry ev16, Aloha to backcountry ev14, Megaplex to backcountry
ev15, and Alaska/Untracked to slopestyle ev6/ev7. Free hosts nobody uses:
the ev3 race slot (its gates sit on two track paths, which the gate tool
refuses), the ev5 slopestyle slot, ev12/ev13 pipes, and three big-air slots.

## 2. Carrier vs donor: who supplies what

| Subsystem | Carrier (host) supplies | Donor (Tricky disc) supplies | Gap |
| --- | --- | --- | --- |
| Race line / gates | Gate count comes from the host `starts` table: 6 on race hosts, 3 on slopestyle/pipe, 2 on backcountry | Donor `.aip` race chain replaces the gate track chain; checkpoints, finish and the progress-meter table are regenerated | Backcountry/slopestyle hosts drop donor start lanes (2–3 gates kept of 6). Showoff `.sop` start lines are never consumed |
| AI rider paths | Host `old_ai` reset-enabled slots cap what fits (most constrained on backcountry hosts) | Donor `.aip` AI paths (40–182 reset-enabled per course) written into host slots; all path events stripped to 0 | Truncation on small-slot carriers (Elysium keeps 42/182, Merqury 68/145). Donor path events (jump/trick/branch cues) are dropped everywhere, so AI riders get no line cues on donor paths |
| Reset paths / surfaces / volumes | Host reset-path pool queried by the state-9 course reset; reset terrain surfaces flagged per patch | Donor reset paths, donor reset patches (`.pbd` surface 0), donor `ResetZone` meshes baked to a sidecar table | Courses with flagging off leave authored OOB patches as plain snow. **Fixed since:** zone hits now enter the stock fall reset (respawn near the fall point, one reset per crossing), not the start record |
| Out-of-bounds triggers | Stock fall resets (`0x116120`/`0x1235f8` → state 9) | Donor `ResetZone` triangles (5 courses) + donor OOB surface patches (8 of 10 courses) | **Fixed since:** reset-surface and sidecar-placement corrections (Merqury's table used the wrong host transform, ~713k units off; Garibaldi's disagreed by ~20k) landed and flow into the port |
| Start / spawn records | Host record table, partially relocated (race gates + freeride start) | Donor 6 start lanes | 6-rider Race rules on 2-gate/3-gate hosts fall back to `starts[0]` for the extra slots. **Fixed since:** Megaplex runs under Race rules for 6 riders |
| Timer / rules | Host discipline per event governs HUD/timer/finish | Mode functions only, no timer constants | Showoff on a race carrier expires at GO; slopestyle countdown (~1:20) expires before long-course finishes — hence one mode per course |
| AI skill / rubber band | Host per-event AI level, rubber band, time-scale slew | Path preference weights (copied raw, unvalidated) + stripped events | Donor pace cues missing; world scale 0.55 vs SSX 3 physics speed is unmeasured |
| Collision / hulls | Host kind-12 slots | Donor collision meshes filtered by converter heuristics (radius, budget, corridor, hull-wrapping) | Skipped colliders (clip-through) vs coarse hulls (invisible walls) — the two failure modes trade off per course |
| Heap budget | Host-defined arena, ceiling `0x1FD8000` after the lift | Course + texture groups per world | Old ceiling starved heavy worlds (black load screen); lifted with a second pool. See `docs/facts.md` |

## 3. Symptom → mechanism (verified reads)

- **Elysium: riders vanish after ~20 s, standings still change.** 6 riders on
  2 gates: slots 2–5 share one start record, and the kept AI path network
  (42/182) runs out under them near the end of the two gate start paths.
- **Aloha/unhosted resets → course start.** The old zone entry placed the
  rider at its own start record and ended in control state 6 — never the
  stock state-9 route reset. **Fixed since:** zone hits enter the stock
  fall reset.
- **AI always fast / player sometimes fast.** Donor AI paths carry zero
  events, so the speed-cap scan never fires on Tricky lines; combined with
  groomed snow and 0.55 world scale, courses ride short. Data, not a 120 Hz
  clock miss (stock-vs-120 rival pace matches to ~1–4 %).
- **Merqury: invisible walls + clipping + missing OOB.** Collision skipped
  on hundreds of buildings (route-radius, budget, corridor filters) while
  kept buildings wear coarse hulls; the OOB sidecar used the wrong host
  transform. **Fixed since:** placements and reset surfaces corrected (see
  §2).

## 4. Options (tabled, not chosen)

- **A. Convert per course on today's carriers** — rebuild the sidecar from
  the build recipe, point zone handling at the stock state-9 reset (done),
  carry donor AI events with a proven type/unit mapping, add gate records
  for extra slots, append AI/reset records past host slot counts, collide
  from the donor race line with split hulls, flag authored OOB patches.
  Independent, bisectable; grows AIP/heap usage.
- **B. Re-host by type** — move a 2-gate backcountry Race course to the free
  6-gate race slot once the gate tool supports two-track gates, or run
  backcountry courses with Rival args (2 riders = 2 gates).
- **C. Two carriers per course (both modes)** — build each course twice
  (Race + Showoff hosts) and pin a per-mode archive in the picker; doubles
  conversion time, layer size and heap fits.
- **D. Convert-time fit gate, not a fix** — `run_chain` fails hard on gate
  count vs riders, kept-path %, zero carried AI events, sidecar/build
  placement mismatch, OOB patch coverage, solid share near the race line,
  and heap headroom. Catches every converter-side mechanism before a build
  ships; fixes nothing on its own.

Not tabled: a Tricky-owned event/location that stops inheriting a stock
host (largest unknown, not costed). World scale 0.55 appears in no option:
changing it rebuilds every course and needs its own rider-size measurement
first.
