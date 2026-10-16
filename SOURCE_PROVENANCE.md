# SOURCE_PROVENANCE.md

Fresh product repository (repo-split-2, step 2). There is no imported
history: every file is either byte-identical to a pinned source or authored
for the composition. Per-file bytes, origins and sizes: `SOURCE_MANIFEST.tsv`
(`status=verbatim` for all 96 snapshot files; composition edits follow as
reviewable diffs on top).

## Pinned sources (frozen 2026-10-15)

| Source | Pin | Content |
| --- | --- | --- |
| Engine `brad-richardson/PS2Recomp` (`ssx3`) | `f7a96f23…1453` (`deps/PS2Recomp` gitlink) | builder `tools/ssx3/`, game metadata `games/ssx3/`, public docs `docs/ssx3-*.md`, full `android/` app shell, `LICENSE` |
| `the private orchestration repo` (private orchestration, stays private) | `ed718e09` | det gate (`mac_gate.sh`, `boot/`, knob checks), replay scripts, generic bench basis, curated docs — all generalized for public use; no orchestration, briefs, receipts, cards or device tooling copied |
| Public infra tree (staged, never committed anywhere) | the staged public-infra tree per the online-open plan §8 (never committed anywhere) | `infra/` (terraform, deploy/harden/backup scripts, lobby service files) minus `.terraform/` provider binaries |
| Generator (separate source pin, same upstream repo) | `467e86e85fee4dd73c8252f40a02c6394116888e` | drives `prepare`; never repinned implicitly (landed linux-host-1 commit; `ps2xRecomp/` tree byte-identical to the original lane commit `03c10b5a`) |
| Vendor `brad-richardson/armsx2` | `afad5a04c2668a10e12adadaa51d54c46f832236` | renderer/VU source; support-archive identity in `deps.lock.json` |
| Turnip (optional Vulkan driver) | `turnip-ssx3-ad094bb9` (`tools/ssx3/turnip.json`) | hashes in `deps.lock.json` |

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

## Lobby separation

The `ssx3-lobby` matchmaker/relay server lives in its own repository (MIT);
it is not vendored here. `infra/` holds only operator-side service files
(`lobby-rust/ssx3-lobby.service`, `lobby.toml`, `lobby-compose.yaml`) and
pins the lobby release per deploy. MComm is never published.

## Never in this repo or its releases

Generated EE/VU sources, ISO/ELF, converted worlds/audio/HUD, texture
dumps/packs, recordings, memory cards, private pins, signing keys,
disc-bearing work dirs, or game-linked APKs. Users build those locally from
their own disc under an external `--work` directory.

## Licence

GPL-3.0 (`LICENSE`, unchanged from the engine). Per-file copyright headers
are preserved. Third-party components and their notices: see
`THIRD_PARTY_NOTICES.md`.
