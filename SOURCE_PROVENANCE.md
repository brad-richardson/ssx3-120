# SOURCE_PROVENANCE.md

Fresh product repository (repo-split-2, step 2). There is no imported
history: every file is either byte-identical to a pinned source or authored
for the composition. Per-file bytes, origins and sizes: `SOURCE_MANIFEST.tsv`
(`status=verbatim` for all 96 snapshot files; composition edits follow as
reviewable diffs on top).

## Pinned sources (frozen 2026-10-16, engine bumped s120-pin-1)

| Source | Pin | Content |
| --- | --- | --- |
| Engine `brad-richardson/PS2Recomp` (`ssx3`) | `5235b114…73a8` (`deps/PS2Recomp` gitlink) | builder `tools/ssx3/`, game metadata `games/ssx3/`, public docs `docs/ssx3-*.md`, full `android/` app shell, `LICENSE` |
| `the private orchestration repo` (private orchestration, stays private) | `ed718e09` | det gate (`mac_gate.sh`, `boot/`, knob checks), replay scripts, generic bench basis, curated docs — all generalized for public use; no orchestration, briefs, receipts, cards or device tooling copied |
| Public infra tree (staged, never committed anywhere) | the staged public-infra tree per the online-open plan §8 (never committed anywhere) | `infra/` (terraform, deploy/harden/backup scripts, lobby service files) minus `.terraform/` provider binaries |
| Generator (separate source pin, same upstream repo) | `467e86e85fee4dd73c8252f40a02c6394116888e` | drives `prepare`; never repinned implicitly (landed linux-host-1 commit; `ps2xRecomp/` tree byte-identical to the original lane commit `03c10b5a`) |
| Vendor `brad-richardson/armsx2` | `afad5a04c2668a10e12adadaa51d54c46f832236` | renderer/VU source; support-archive identity in `deps.lock.json` |
| Turnip (optional Vulkan driver) | StevenMXZ Gen8 V37 community zip (`tools/ssx3/turnip.json`) | archive + driver + notices hashes in `deps.lock.json`; double-SHA pin is the integrity check (unattested third-party build; see trust note in `tools/ssx3/README.md`) |

## Path remaps (rename-only, bytes preserved)

- `android/` → `apps/android/` (Gradle native-build source made explicit via `-Pps2xEngineCMakeLists`; see below).
- `tools/ssx3/presets/*.env` → `presets/*.env` (`stage` reads the new location).
- Lab tooling copies → `tests/det/*`, `tools/replay/*`, `tools/bench/*` (generalized; see commit message).

## Composition edits (not verbatim; reviewed in the step-2 commit)

- `tools/ssx3/build.py`: explicit product/engine roots (`--engine`, default
  `deps/PS2Recomp` submodule; `SSX3_ENGINE_DIR` override). Runner configure,
  vendor-adapter assembly, Android stub copy and Gradle invocation now resolve
  engine files under `ENGINE`; metadata, presets and app shell resolve under
  the product root.
- `tools/ssx3/prebuilt.py`: archive `contract()` hashes the engine checkout
  under engine-relative paths, so the support-bundle stamp is stable across
  the move for the same engine pin (`39faf513…`, verified equal from both
  roots). `SOURCES.json`/`source_sha` record the engine SHA. Product
  composition is recorded in `deps.lock.json`, not in the archive stamp.
- `apps/android/app/build.gradle`: native-build `path` is now the explicit
  `ps2xEngineCMakeLists` property (default
  `../../../deps/PS2Recomp/CMakeLists.txt`); `build.py` passes the absolute
  engine CMakeLists.
- `tools/ssx3/prebuilt-consumer.cmake`: removed (byte-identical copy remains
  in the engine at the pin; both build paths consume the engine's copy via
  `CMAKE_CURRENT_SOURCE_DIR`, so a product duplicate could only rot).
- `tools/ssx3/tests/test_public_install_stage.py`: stages the APK under
  `apps/android/` (product layout) instead of the engine's `android/`.
- `tools/ssx3/test_prebuilt.py`: contract/drift git queries read the engine
  checkout (`prebuilt.ENGINE`), not the product root.
- s120-pin-1 (engine `f7a96f23` → `5235b114`): re-synced `build.py`
  (`verified_driver` community-zip path, `--prebuilt` opt-in flag),
  `prebuilt.py` (contract `5cfcdafd…`: SND headers moved to the game
  module, `ps2_pcm_ring.h` added), `presets/ps2x.env` (online rows
  dropped until 120 netplay, GPU-aware autotune default), `knobs.tsv`
  (+8 netsync knobs, default off), `turnip.json` (V37 community zip),
  `OfflineBridge.cpp` (packed microVU STATUS reseed); carried the new
  `verify-turnip.sh`, `build_tk_font.py`, `build_tk_list_art.py`,
  `tests/test_android_source_default.py` and
  `tests/test_turnip_driver.py` verbatim. Product edits re-applied:
  `apps/android` APK path, engine-relative `prebuilt-releases.json`
  link, `prebuilt.ENGINE` test retarget, signed-off play-policy preset
  tail. Vendor armsx2 stays `afad5a04` (the engine at the new pin still
  pins it in `build.py`/`prebuilt.py`).

## Bundled PGO profiles (pgo-public-1)

`tools/ssx3/pgo/android/` (PB58) and `tools/ssx3/pgo/ios/` (IPB30) hold
gzip-compressed LLVM indexed profiles (`.profdata.gz`, ~1.7/1.6 MB; 32 MB
uncompressed) plus `MANIFEST.json` provenance. Both were trained against fork
`84b9ece9` and codegen register `ad96edb6…` (SPR1 ABI-v2); the manifests record
the uncompressed SHA256, trained fork/routes and the register SHA the builder
gates on. Profiles contain IR function names, hashes and counters only (for
example `_Z21sub_…_0x…`, `ps2_mtvu`, `ps2_snd_spu` symbols); they carry no ISO,
ELF, game source or save bytes. The builder decompresses the profile into the
user's private `--work` dir, verifies the SHA, and applies it only when the
user's `codegen/register_functions.cpp` SHA matches the trained register SHA;
otherwise it prints one line and builds without PGO (`--no-pgo` always skips;
`--pgo-profile` overrides with the user's own file).

## Lobby source (vendored, lobby-src-1)

The `ssx3-lobby` matchmaker/relay server is vendored here as the source of
truth: `infra/lobby-rust/` holds 24 files byte-identical to `ssx3-lobby`
`main` `408932e` (NOT the `online-120-2` branch `b3905ce`, which deploys
later with the 120 client change; verified per-file with `cmp` against
`git show 408932e`). `cargo test --locked --workspace` there is green
(41 passed, 0 failed), and the musl release build from the vendored tree
reproduces the live binary byte-for-byte (SHA-256 `af6a37df…f2d`).
`infra/lobby-rust/` also retains two operator overlays that are not part
of the lobby source: `ssx3-lobby.service` (production unit, identical to
the live unit, SHA-256 `54e28e95…e44e`) and `lobby.toml` (production
template, refreshed repo-to-repo from the lab infra tree at `20794f7a` —
the limits-load-1 deploy source — checksum-identical to the live config
`5d74f9f2…`, key names verified equal). online-v0-1 (2026-10-17)
added the Quick Play endpoint (`[state_sync]` on UDP 3757; relay range
3658-3756, `max_pairs` 98) and deployed it: running binary SHA-256
`ca0f26ef…` (musl rebuild from this tree reproduces it), live config
`277b3d94…` = this template, unit unchanged; the prior binary
`af6a37df…` is kept as `ssx3-lobby.previous` and the prior config as
`/etc/ssx3-lobby/lobby.toml.pre-online-v0-1`. The separate
`ssx3-lobby` repository stays for history until its `online-120-2` branch
merges. MComm is never published.

## Never in this repo or its releases

Generated EE/VU sources, ISO/ELF, converted worlds/audio/HUD, texture
dumps/packs, recordings, memory cards, private pins, signing keys,
disc-bearing work dirs, or game-linked APKs. Users build those locally from
their own disc under an external `--work` directory.

## Licence

GPL-3.0 (`LICENSE`, unchanged from the engine). Per-file copyright headers
are preserved. Third-party components and their notices: see
`THIRD_PARTY_NOTICES.md`.
