# tools/ssx3/tricky/ — Tricky audio, HUD and sidecar converters (tk-port-2)

Port of the private TK-lane converter scripts into the public
bring-your-own-disc builder (`build.py tricky`, see the tk-public plan
`docs/research/tk-public-plan-2026-10-15.md` §1/§4 in the lab repo).
Copy rule: **verbatim copies with re-rooted imports and no behaviour
edits**, except hardcoded lane work paths, which became CLI args (listed
per file below). No game data, converted assets or disc-derived bytes
live here — only code plus two lane-authored data defaults.

Provenance (lane source → this file):

| Ported file | Source | Adaptation |
| --- | --- | --- |
| `speech_eaxa.py` | TK25b `tk25b_eaxa.py` | none (verbatim) |
| `speech_catalog.py` | TK25b `tk25b_catalog.py` | WORK/VGM/EXT/STR/WAV → CLI args; `__main__` guard |
| `speech_slots.py` | TK25c `tk25c_slots.py` | WORK/WORK25B → `--tk25b-work`/`--work-dir`; sibling imports; `__main__` guard; `main(argv)` |
| `speech_loud.py` | TK25c `tk25c_loud.py` | cache path via `init_cache()` (was a hardcoded lane path) |
| `check_slots.py` | TK25b `tk25b_check_slots.py` | `__main__` guard only |
| `speech_composite.py` | TK25c `tk25c_composite.py` | none (already CLI-clean) |
| `speech_merge.py` | TKP4 `tooling/tkp4_speech.py` | none (already CLI-clean) |
| `mpf3to4.py` | TKP4b `tooling/mpf3to4.py` (the TK41b fixed converter + graph check) | none (already guarded) |
| `mus1_walk.py` | TK41b `tooling/mus1_walk.py` (= MUS1's, identical) | none |
| `music_remus.py` | TK41 `tooling/tk41_remus.py` | lane `sys.path` CWD hack → sibling dir |
| `music_selfcheck.py` | TK41 `tooling/tk41_selfcheck.py` | workdir is now a required arg (was a private default) |
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
  REPORT §1 mapping table and the built members (inside the merged
  composite) survive. Rider+meter merge is reproducible via
  `speech_merge.py` extract+merge; meter re-transcode from the ISO is a
  documented gap for a follow-up lane.
- World-chain readers this package imports but does not own
  (`wlib.py`, `tricky_pbd.py` + `ps2mdr.py`, `replace_terrain.py`,
  `gamecube_scenery.py`, `ps2_as_gc30.py`, `big.py`/`extract_big.py`,
  `donor_inventory.py`, TK10 `plan-all.json` full text, TK18
  `alaska-b-scenery-summary.json`) belong to the tk-port-1 world-pipeline
  port into this same directory. Until that lands, these scripts resolve
  those names against this directory; the golden runs below used the
  private originals read-only via `PYTHONPATH`.

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
   reproduced byte-identical by this lane).
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
| `vgmstream-cli` | master `7dc938fa2f210943b37c7b6511852b516ef432ab` (lane build) | MicroTalk decode only. Public recipe (TK25b): `brew fetch vgmstream libvorbis libogg speex libao`, `git clone --depth 1 https://github.com/vgmstream/vgmstream.git`, then the in-tree build per its docs; binary at `<build>/cli/vgmstream-cli` |
| `bsdtar` | system libarchive | single-file ISO9660 extract (atlas) |
| `python3` | stdlib only (all scripts) | 3.14/3.15 used in validation |

Golden table (all reproduced by this lane; see REPORT):

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
| slot acceptance | 186 replaced / 8 kept | — | `check_slots.py` 7/7 |
| `mpf3to4 check` | 35/35 PASS | — | all pool members |

Notes for `tk-build-1`: speech composites resolve their `iso` segments
against the **SSX3** disc (they patch SSX3's BIGs); the Tricky ISO only
supplies donor bytes. tk-audio-1's speech-step2 Big Air curation
(`speech-step2-big fc9192da…`) is provisional (pending Brad audition +
Odin) and is NOT part of this port — recorded here so the delta can be
applied later without disturbing today's pipeline.
