# tools/ssx3/tricky/ — Tricky world + audio/HUD/sidecar converters

Port of the internal Tricky converter scripts into the public
bring-your-own-disc builder (`python3 build.py tricky --iso <Tricky.iso>
--work <work>` after prepare, or `./build android --ssx3-iso …
--tricky-iso …` for the one-command build).
Copy rule: **verbatim copies with re-rooted imports and no behaviour
edits**, except hardcoded work paths, which became CLI args (listed
per file below). No game data, converted assets or disc-derived bytes
live here — only code plus authored recipe data (fence/drop lists,
blend specs, TSV defaults). Decoded census/glass tables are generated
into the workdir at build time, never tracked.

Provenance (origin → this file):

| Ported file | Source | Adaptation |
| --- | --- | --- |
| `speech_eaxa.py` | TK25b `tk25b_eaxa.py` | none (verbatim) |
| `speech_catalog.py` | TK25b `tk25b_catalog.py` | WORK/VGM/EXT/STR/WAV → CLI args; `__main__` guard |
| `speech_slots.py` | TK25c `tk25c_slots.py` | WORK/WORK25B → `--tk25b-work`/`--work-dir`; sibling imports; `__main__` guard; `main(argv)` |
| `speech_loud.py` | TK25c `tk25c_loud.py` | cache path via `init_cache()` (was a hardcoded work path) |
| `check_slots.py` | TK25b `tk25b_check_slots.py` | `__main__` guard only |
| `speech_composite.py` | TK25c `tk25c_composite.py` | none (already CLI-clean) |
| `speech_merge.py` | TKP4 `tooling/tkp4_speech.py` | none (already CLI-clean) |
| `english_base.py` | new glue (reconstructed ENGLISH base merge) | n/a: builds the 8-member ENGLISH base from the discs; per-slot takes from `data/english-decisions/` (embedded gains, never re-measured) |
| `data/english-decisions/*.decisions.tsv` | reconstructed recipe (curated sources + validated gains) | `slot<TAB>source<TAB>gain_db`; source `tricky-<Bank>-<Idx>` or KEEP-ORIGINAL |
| `mpf3to4.py` | TKP4b `tooling/mpf3to4.py` (the TK41b fixed converter + graph check) | none (already guarded) |
| `mus1_walk.py` | TK41b `tooling/mus1_walk.py` (= MUS1's, identical) | none |
| `music_remus.py` | TK41 `tooling/tk41_remus.py` | `sys.path` CWD hack → sibling dir |
| `music_selfcheck.py` | TK41 `tooling/tk41_selfcheck.py` | workdir is now a required arg (was a hardcoded default) |
| `music_composite.py` | TKP4b `tooling/tk41b_composite.py` | `__main__` guard only |
| `build_music_pool.py` | TKP4b `tooling/tkp4b_build_music.py` | REPO/TOOL/W/STAGE/S3/BUILT → CLI args; composite subprocess → sibling `music_composite.py`; `__main__` guard; `main(argv)` |
| `music_assign.py` | TKP4b `tooling/tkp4b_assign.py` | STAGE → argv[1]; `__main__` guard; `main(argv)` |
| `music-pool-default.tsv` | TKP4b `receipts/assign.tsv` B1/B2 rows | reassigned as the default pool (comments added) |
| `make_hud_atlas.py` | TK43c `tooling/make_trickyhud_atlas.py` (= TKP4b's copy) | none |
| `song_eaxa.py` | AU2 `au2_eaxa.py` | none |
| `bake_gems.py` | TK45C `tooling/bake_gems.py` | TK12 import → package sibling |
| `gem_step.py` | TK52 `tooling/gem_step.py` | module-level run wrapped in `main()` (identical statements); imports → siblings (`parse_desc` from `speech_merge.py`); TK18 receipt path → `--alaska-scenery` |
| `bake_mega_gems.py` | MGX1 `tooling/bake_mega_gems.py` | repo-rooted imports → siblings |
| `bake_reset_volumes.py` | RSV1 `tooling/bake_reset_volumes.py` | DONOR/UNTRACK/plans → CLI args; imports → siblings; `main(argv)` |
| `resetvol-plans.json` | TK10 `plan-all.json` (9 needed entries, kept fields) | subset with identical values |
| `mapnames.py` | TK13 `mapnames.py` | none (stdlib-only, 19 lines) |

Deliberately NOT ported (with reason):

- TK25b `tk25b_slots.py` — superseded by `speech_slots.py` (same mapping,
  plus loudness match); the shipped members were built by the latter.
- TK25b `tk25b_catalog.py` — ported as `speech_catalog.py` (see above).
  `tk25b_check_codec.py`, `tk25b_check_iso.py`, `tk25b_patch_iso.py` —
  one-off validation/exploration helpers, not builders.
- TK41 `tk41_intro32.py`, `tk41_jbmatch.py`, `tk41_render.py`,
  `tk41_wavmatch.py`, `tk41_composite.py` — TK41 experiment scaffolding and
  audits; the pool build does not call them.
- TK43b meter slot builders (`tk43b_slots*.py`, limiter, composite) — ran
  as uncommitted scratch and are **lost** (work dir pruned); only the
  mapping table and the built members (inside the merged
  composite) survive. Rider+meter merge is reproducible via
  `speech_merge.py` extract+merge; meter re-transcode from the ISO is a
  documented gap for follow-up work.
- World-chain readers this package imports but does not own
  (`wlib.py`, `tricky_pbd.py` + `ps2mdr.py`, `replace_terrain.py`,
  `gamecube_scenery.py`, `ps2_as_gc30.py`, `big.py`/`extract_big.py`,
  `donor_inventory.py`, TK10 `plan-all.json` full text, TK18
  `alaska-b-scenery-summary.json`) are the tk-port-1 world-pipeline
  port below in this same directory; all scripts resolve those names
  against this directory.

Pipelines (all read the user's own discs; all writes go to `--work`,
never into the repo):

1. Speech riders: extract 5 Tricky + 7 SSX3 `.dat` members →
   `speech_catalog.py` (vgmstream + ffmpeg) → per-target
   `speech_slots.py <ssx3.dat> <tk25b-decisions> <mapping> <out.dat>
   --target … --loudrows … --tk25b-work … --work-dir …` (needs the pinned
   TK25b mappings/decisions as inputs) → `check_slots.py` acceptance →
   `speech_composite.py <ssx3.iso> <members> <out>` → `speech-tricky-big`
   (`ed417ac3…`, 5,675,008 B). Meter members + `speech_merge.py merge`
   produce the shipped 9-member `07dcb666…` (6,187,008 B).
2. Music pool: extract 17 v3 pairs from the Tricky `MUSIC.BIG` →
   `music_remus.py <v3.mus> <v3.mpf> <template.sch> <32k.mus> <offsets>`
   (template = any stock SSX3 song stream; all share one header shape) →
   `mpf3to4.py convert <v3.mpf> <32k.mus> <v4.mpf> --offsets …` →
   `mpf3to4.py check` + `music_selfcheck.py` → `music_assign.py` (or the
   default TSV) → `build_music_pool.py --ssx3-iso … --stage-dir …
   --assign-tsv music-pool-default.tsv --out-dir …` → `975c0ae4…` +
   `0bc17a15…` (17 Tricky songs fill all 35 slots; downtime, ginandsin,
   kingbeat fit no slot and stay out).
3. HUD atlas: `make_hud_atlas.py <tricky.iso> <out-dir>` (needs `bsdtar`;
   asserts `HUDGAME.SSH` md5 `86d12a2d…`) → `trickyhud.bin`
   (`a7ccacd…`, 262,164 B, TKHUD2).
4. Menu song: decode the `itstricky` member of the Tricky `JUKEBOX.BIG`
   (`song_eaxa.decode`, stereo 36 kHz, 6,602,400 frames), then
   `ffmpeg -i itstricky-36k.wav -ar 48000 -af volume=-1.69dB
   tricky-song.wav` → (`9c5dbf71…`, 8,803,200 frames stereo s16;
   reproduced byte-identical).
5. Gems: `bake_gems.py --course ARCH:world:scenery:effects:group …`
   (345 rows, `c8407f43…`) → `gem_step.py` (+76 Alaska, +62 Pipedream;
   483 rows, `3f4cd72b…`) → `bake_mega_gems.py` (+38 MEGA; 521 rows,
   `467d0357…`).
6. Reset volumes: `bake_reset_volumes.py <out> --donor-dir …
   --untrack-dir … --plans resetvol-plans.json` (463 volumes, 1022
   triangles, `77acce03…`).

System dependencies (pinned, macOS + Linux):

| Dep | Pin | Notes |
| --- | --- | --- |
| `ffmpeg` / `ffprobe` | 9.0.2 (Homebrew) | resample, EA-XA decode, ebur128/volumedetect |
| `vgmstream-cli` | master `7dc938fa2f210943b37c7b6511852b516ef432ab` (built from source) | MicroTalk decode only. Public recipe: `brew fetch vgmstream libvorbis libogg speex libao`, `git clone --depth 1 https://github.com/vgmstream/vgmstream.git`, then the in-tree build per its docs; binary at `<build>/cli/vgmstream-cli` |
| `bsdtar` | system libarchive | single-file ISO9660 extract (atlas) |
| `python3` | stdlib only (all scripts) | 3.14/3.15 used in validation |

Golden table (all reproduced in-builder):

| Output | SHA-256 | Bytes | Via |
| --- | --- | --- | --- |
| `trickyhud.bin` | `a7ccacdabb2c…f28` | 262,164 | ISO rebuild |
| `tricky-resetvol.tsv` | `77acce0393b1…c8` | 184,913 | donor rebuild |
| `speech-tricky-big` (TK25c 7) | `ed417ac31032…cee` | 5,675,008 | extract+re-splice round-trip |
| `speech-tricky-big` (merged 9) | `07dcb666f9e2…087` | 6,187,008 | extract+merge round-trip |
| `music-tricky-big` | `975c0ae4bab4…140` | 328,390,656 | extract+re-splice round-trip |
| `music2-tricky-big` | `0bc17a157d94…e89` | 240,457,728 | extract+re-splice round-trip |
| `gems.tsv` (345) | `c8407f435bd4…` | — | donor rebuild |
| `gems.tsv` (483) | `3f4cd72b9394…5ff` | — | chain rebuild (= MGX1 record) |
| `tricky-gems.tsv` (521) | `467d0357a106…598` | 54,159 | chain rebuild, byte-identical |
| `english-base-big` (8-member base) | `f61c7418dc15…2d2c` | 31,180,800 | `english_base.py` from the discs, byte-identical (Mac vgmstream r2117 + ffmpeg 9.0.2) |
| `english-silent-big` (base + silenced DJ intros) | `8625f2ff25c2…728f` | 42,301,440 | `silence_intro_banks.py` on the rebuilt base (8 carried, 2 silenced, decode_peak 0) |
| slot acceptance | 186 replaced / 8 kept | — | `check_slots.py` 7/7 |
| `mpf3to4 check` | 35/35 PASS | — | all pool members |

Notes for the builder: speech composites resolve their `iso` segments
against the **SSX3** disc (they patch SSX3's BIGs); the Tricky ISO only
supplies donor bytes. The speech-step2 Big Air curation
(`speech-step2-big fc9192da…`) is provisional (pending audition)
and is NOT part of this port — recorded here so the delta can be
applied later without disturbing today's pipeline.
## Tricky world pipeline

Faithful port of the internal world converters (extract, world build,
chain, preview) for the public bring-your-own-disc builder. Plan of record:
extract → world build → chain → preview (stages below), with per-file
origins in §Provenance.

### Provenance

Every `.py` here is byte-identical to its internal source
**except** `sys.path` bootstrap lines and root-variable definitions, which are
re-rooted from the internal tree to this package
(`tools/ssx3/tricky/<sub>/`). Two behavior-neutral accommodations:

- `R = HERE.parents[1]` / `REPO = HERE.parents[3]` become `HERE.parent`: the
  package is one level flatter than the internal layout, so the
  grandparent step lands one level too high otherwise.
- A missing `from pathlib import Path` is added ahead of first use where a
  rewritten bootstrap line needs it (one file gets a duplicate import).

No converter logic, defaults, CLI shapes, constants or data paths change.
Equivalence evidence (internal scratch, not committed):
`audit_paths.py` (all 89 bootstraps evaluate to the mapped original entries,
in order) and `equiv.py` (identical load outcomes old vs new on all 89).

### Layout (origin in parentheses)

- `shared/` — `tools/*.py` closure the converters import (`inspect_disc`,
  `inventory_tricky`, `probe_worlds`, `replace_terrain`, `course_route`,
  `verify_inputs`, plus transitive `build_world_experiment`, `import_terrain`,
  `relayout_stream`, `location_inventory`, `gamecube_*`, `grow_group`,
  `paths`, `patch_geometry`, `race_course`, `refpack_encode`,
  `refpack_optimal`).
- `tk6/` (`TK6/retrack.py`, `members_of`), `tk12/` (`wlib`, race/rails/resets/
  route builders), `tk13/` (ps2col/ps2mdr/ps2swz/survey/tricky_pbd/mapnames).
- `tk10/` (build_world, make_composite), `tk16/` (ps2_as_gc, scenery16,
  rails16), `tk18/` (extract_big, make_race_world, make_route_tol).
- `tk19c/` (make_terrain_tex; the TK26/TK28b/TK33b copies are byte-identical),
  `tk20/` (rails20, near_keep, world_table), `tk21/` (luno_survey).
- `tk24c/` (bind_effects; TK24/TK33b copies identical),
  `tk26/` (terrain_tex23, route_textures),
  `tk28b/` (breakaway_passes, classify, scenery16 snapshot),
  `tk30/` (pipe builders: race/rails/scenery/terrain/ps2_as_gc30).
- `tk33b/` (make_scenery33, rails16 copy, terrain_tex19c copy, u0zero),
  `tk37/` (aabb_floor), `tk42c/` (blend builders incl. patch_blend).
- Chain: `tk56/` (fix_binds + survey/verify/table), `tk53/` (trigger_step,
  break_step), `tk57/` (interact_step), `tk51/` (patch_hidden_colliders,
  patch_source_bridge), `tk58/` (fix_hulls, hull).
- `tkp5/` (relocate_step, preview_step copy, verify_chain,
  event_refs), `tk52/` (preview_step, menu_assets).
- `tkroutes/` (`route_step.py`: world-data patches — Aloha
  covers3, Merqury wall2819, Megaplex tube94s2; proven by byte-identical
  rerun of the three route outputs).
- `run_chain.py` (validation driver; new glue, not a port): replays
  the recorded commands end to end from the two ISOs, plus the
  post-s5 `routes` rung (follow-up) on aloha/merq/mega.

### Deliberately not ported here

- `collect.py`, `donor_inventory.py`, `world_inventory.py` —
  audio/HUD/sidecar territory (ported above in this same
  directory: `TK52/gem_step.py` and `tkp4_speech.py`).
- `TK33/make_scenery33.py` — superseded ancestor; the chain runs TK33b's copy
  (resolved via `patch_source_bridge`'s path order, as upstream).
- `TK42/bind_effects.py` — older variant; the TKP3 binds used TK24c's.
- `TK16`/`TK18`/`TK20`/`TK28b`/`TK30`/`TK33b` `build_*.sh` drivers — hardcoded
  workdir paths; superseded by `run_chain.py`.
- Device/harness scripts (`boot*.sh`/`leg.sh`), receipts, sheets, pad files.

### Runtime inputs (never committed)

Two disc images plus authored recipe data that lives outside git:
SSX 3 USA ISO, Tricky USA ISO, donor extracts, `scenery-effects` JSONs,
`glass-targets.json` values (passed as CLI args by the driver), fence/
breakaway flag lists (reproducible via `classify.py`/`breakaway_passes.py`).
Only SHA-256 hex digests of outputs are committed (golden tests).

Dependencies: Python 3 stdlib only, except `tk28b/breakaway_passes.py`
(numpy, recipe-census use only).

