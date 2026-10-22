# Map — Tricky world + audio build pipeline

Two discs in (owned SSX 3 USA + SSX Tricky USA), one device layer out, all
under a private `--work` dir. One driver: `tools/ssx3/tricky/build_tricky.py`,
invoked as the `build.py tricky` step. Nothing here ships game data: the
pipeline reads the user's own discs and writes converted outputs into the
build work dir, which stays outside this checkout.

## 1. Entry contract

```sh
./build tricky --tricky-iso <Tricky.iso> --ssx3-iso <SSX3.iso> \
  --work <work>/tricky [--jobs N] [--tricky-audio on|off|auto]
```

- Refuses an existing `--work` dir; fails closed (stage outputs are
  structure-checked — WAV shape, non-empty, phase acceptance — with SHAs
  recorded).
- System deps: uv + python3 stdlib only. `ffmpeg` + `vgmstream-cli` are
  optional local installs for the audio phases (`on` fails without them,
  `off` skips, `auto` runs when both probe clean); with audio off the
  runtime falls back to SSX 3 audio.
- Course build order: `pipe, untrack, snow, gari, aloha, mesa, merq, elys,
  alaska, mega` (`build_tricky.py: BUILD_COURSES`).

## 2. Phases in order

Driver order in `main()`: worlds → preview → menu → [speech → music →
english → music-inf → song] → hud → gems → resetvol → layout. The bracketed
audio phases run only when `--tricky-audio` resolves to on.

| # | Phase | Inputs (disc members) | Outputs (under `--work`) |
| --- | --- | --- | --- |
| 1 | `phase_worlds` | SSX3 `DATA/MODELS/BAM.BIG` (host BAM); Tricky `DATA/MODELS/<C>.BIG` per course | `host-bam/BAM.BIG`; per-course `w-<c>-*/<C>.BIG`; `donor/<c>/*` |
| 2 | `phase_preview` | per-course worlds | `cp/COURSPIT.BIG` (course preview art) |
| 3 | `phase_menu` | COURSPIT | `menu/DATA/UI/TP*.SSH` (menu panes) |
| 4 | `phase_speech` | Tricky + SSX3 `DATA/AUDIO/SPEECH.BIG` | `speech/*`, composite `speech-tricky-big` |
| 5 | `phase_music` | Tricky `DATA/AUDIO/MUSIC.BIG` (17 songs); SSX3 `bep.mus` template | `music/*`, music pools |
| 6 | `phase_english` | SSX3 ISO + English base | `english-silent-big` |
| 7 | `phase_music_inf` | SSX3 `DATA/CONFIG/MUSIC.INF` | `music-inf-tricky-big` |
| 8 | `phase_hud` | Tricky `DATA/TEXTURES/HUDGAME.SSH` | `hud/atlas/trickyhud.bin` |
| 9 | `phase_song` | Tricky `DATA/AUDIO/JUKEBOX.BIG` member `itstricky` | `song/song-36k.wav`, `tricky-song.wav` |
| 10 | `phase_gems` | per-course worlds, donor trees, Tricky ISO | `gems/gems-345.tsv`, `gems/gems-483.tsv` |
| 11 | `phase_resetvol` | `donor/`, `resetvol-plans.json` | `tricky-resetvol.tsv` (out-of-bounds zones) |
| 12 | `phase_layout` | all phase outputs | `tricky-ov/DATA/WORLDS/<C>.BIG`, `tricky-ov/DATA/UI/*`, top-level device files, `m-tricky.txt`, `tricky.env`, `SKIPPED.txt` |

Per-course world recipe rows (donor, tools, oracles) live in
`tools/ssx3/tricky/run_chain.py`. The rung ladder inside phase 1 is
`extract, ps2, build, race, resets, scenery, terrain, sg, cand, finalw,
bind, floor, composite, play, blend, fix, s1..s5, routes`; stages a course
has not wired are skipped with a note.

## 3. Where outputs land in the app

- The builder (`tools/ssx3/build.py`) stages only the allowlisted
  `TRICKY_DEVICE_FILES` / `TRICKY_DEVICE_TREES`; `stage_tricky()` re-verifies
  against `tricky_inputs.json`, copies the payload, and rewrites the baked
  app id in `m-tricky.txt`/`tricky.env`.
- Device layout: overlay worlds at `.../files/tricky-ov/DATA/WORLDS/`;
  audio/HUD/song/tables at the top of `.../files/`; env keys in `tricky.env`
  (the two audio keys are dropped when audio is off). Manifest aliases map
  stock BIGs to the built composites; audio off emits no audio aliases.
- A converted course as an overlay composite is ~1.3–1.5 MB of changed
  groups (every unchanged group byte-identical to the host world) instead of
  a ~113 MB world copy.

## 4. Resume and skips

- **Resume:** `--from-phase speech --reuse-worlds <finished-work>` copies that
  run's world finals + host BAM + gems inputs into the fresh `--work`, then
  runs preview onward. Refuses same-dir; loud `REUSED-SKIP` for courses with
  no final.
- **Skips (loud, recorded, not silent):** `audio-off` (no local tools —
  worlds/HUD/menus/gems build regardless); `gems-partial` (strict gate needs
  courses whose reset data is complete); `english-base` (needs the English
  merge input). All skips land in `SKIPPED.txt` and drop manifest rows.

## 5. Typical wall time

Fresh full run: roughly 3 hours on a 12-worker container host (Merqury +
Elysium chains dominate at ~1 h combined). Speech/music/HUD/gems add a
fraction of that. Resume-from-speech re-runs the late phases in ~20 min.
Fresh-run target with course pooling: ≤ 60 min.
