# ssx3-120

Upscaled, 120 fps, Tricky-capable SSX 3 with rudimentary online — built from
**your own disc**. A few commands from this repo give an installable Android
APK; Mac hosts also get a local runner for testing. No game data, art,
BIOS, or derived assets are published here or in any release: bring your own
USA disc (`SLUS_207.72`).

Gameplay is **120-only**: physics, tricks and rider update at 120/s at
normal wall-clock speed. The 60 option is plain stock 60. Faster emulation,
duplicated presents and frame interpolation are different outcomes and are
reported as such. An emulator's 2× speedhack is fast-forward, not 120.

## Build from your own disc

Supported build hosts: macOS Apple silicon, Linux x86_64/arm64 (Windows
users: Ubuntu in WSL2). Play targets: Android arm64, iOS. You need a JDK,
the Android SDK/NDK, CMake and Ninja — `doctor` tells you exactly what is
missing.

```sh
git clone --recurse-submodules <this-repo> ssx3-120
cd ssx3-120
python3 build.py doctor --target android   # or: prepare, mac
python3 build.py prepare --work ~/ssx3-work --iso ~/SSX3.iso
python3 build.py android-libs --work ~/ssx3-work
python3 build.py android --work ~/ssx3-work
```

Keep the work directory outside this checkout and private: it holds your
disc image, generated game code, cards and signing keys. The support
libraries build from source by default: first-time cold build on a Mac
mini (M5 Pro, 4 jobs, empty caches) is prepare ~80 s, Android libraries
~600 s, APK link+package ~260 s (resumed link). Your machine will differ;
`build.py` prints per-stage timing. A verified prebuilt release, when one
matches this checkout, is tried first and falls back to source
automatically; details in [tools/ssx3](tools/ssx3/README.md).

What the builder does with your disc: validates the ISO/ELF hashes, writes
a slim image plus a hole table into `--work`, generates EE code with the
pinned generator into `--work/codegen` (never into this repo), and links it
with the pinned runtime. Game-free support libraries (runtime, renderer,
microVU) build from source; a checksum mismatch or missing prebuilt
archive falls back to source automatically.

## Layout

- `build.py` — product entrypoint (forwards to `tools/ssx3/` with the pinned engine).
- `tools/ssx3/` — builder, doctor, host recipes, font/shader transforms.
- `games/ssx3/` — game metadata (TOML + function map) for code generation.
- `presets/` — stock/120/platform env layers staged onto device.
- `apps/android/` — complete Gradle app shell (own package id, own keystore).
- `apps/` — iOS/Desktop platform glue arrives with the resource-ownership step.
- `deps/PS2Recomp` — pinned engine submodule (runtime, recompiler, tests).
- `deps.lock.json` — every pin: engine, generator, vendor, toolchains, archive identity.
- `tests/det/` — the public determinism gate: four det keys against your own disc.
- `tests/` — no-disc product checks (run on every push).
- `tools/replay/`, `tools/bench/` — pad record/replay scripts, generic Android bench.
- `infra/` — operator-side lobby infra (terraform, deploy/harden/backup scripts, service files).
- `docs/` — build/input/troubleshooting guides, verified facts, engine timing research.

The runtime is still SSX-aware inside the pinned engine; the game-module
seam (static `ssx3_game_module` + hook contract) lands after v0. Until then
this repo is the product home with an explicitly pinned engine, not a
minimal fork.

## Determinism

After any guest-affecting change the four keys must be IDENTICAL
(`reference`, `play`, `full120events`, `play120`):

```sh
./tests/det/mac_gate.sh --work ~/ssx3-work --iso ~/SSX3.iso
```

New behavior needs a new key and sign-off — never a silent re-mint. Guest
hashes don't prove graphics or cost: pair them with fixed captures
(`docs/facts.md`).

## Devices and upscale

- Android/Adreno: native panel resolution; 2× and native 2.41× fit 120 Hz on
  race content (p99 4.2 / 5.3 ms). Turnip driver recommended (the proprietary
  driver loses composite draws). Below-native is bench-only. CAS sharpening
  approved; FXAA/ShadeBoost out.
- iPhone/iPad: 2×. iOS uses an offline VU core; never uninstall the app
  (it wipes saves) — upgrade with a plain install.
- Upscale recipe: stage the resolution env layer, confirm the fixed capture
  set is clean, then ride. See `docs/facts.md` (upscale evidence) and
  `apps/android/README.md`.

## Online (rudimentary)

Online is on: Quick Matchup connects to a lobby server you (or a friend)
operate — match + UDP relay, no direct peer IPs. The default server is
`ssx.bradr.dev`; the lobby source lives in its own repo, and this repo
holds the operator side (`infra/`: one small cloud host, deploy, harden,
backup, rollback). To play offline, just don't pick Online in the menu;
the in-game settings panel also has an Online row that turns it off.

## Provenance and licence

Fresh repo, no imported history. `SOURCE_PROVENANCE.md` + `SOURCE_MANIFEST.tsv`
record every file's pinned source and bytes; `THIRD_PARTY_NOTICES.md` names
every vendored component. GPL-3.0 (`LICENSE`).
