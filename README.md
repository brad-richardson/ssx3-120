# ssx3-120

Fan project, not affiliated with or endorsed by Electronic Arts. Bring your
own disc; no game data is distributed.

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
users: Ubuntu in WSL2). Play targets: Android arm64, iOS. The only
prerequisite is [`uv`](https://docs.astral.sh/uv/) (plus CMake, Ninja and a
C compiler, which `doctor` checks):

```sh
git clone --recurse-submodules <this-repo> ssx3-120
cd ssx3-120
./build android --ssx3-iso ~/SSX3.iso --work ~/ssx3-work --accept-android-licenses
```

That one command runs doctor → prepare → android-libs → android in order
and resumes (prepare and android-libs are skipped when their outputs are
current). The JDK and the Android SDK/NDK are fetched automatically when
missing — pinned Temurin JDK 17 plus Google's command-line tools and SDK
packages, SHA-256-verified, into `--work/toolchain/` (project-local; no
system install, no sudo). Google's SDK licence must be accepted:
`--accept-android-licenses` is the documented path (interactive runs may
answer the prompt instead). Add `--install` to push to a connected device
afterwards, `--prebuilt` to reuse a verified prebuilt support release when
one matches, and `--tricky-iso <Tricky.iso>` to validate your Tricky disc
(the Tricky port arrives in a later version, so the build stops with that
message for now). `make android ISO=~/SSX3.iso` wraps the same command.

Prefer your own toolchain? `--no-auto-download` never fetches: the builder
uses only what it finds and fails with a precise
"missing X; set Y or drop --no-auto-download" message. Search order: JDK =
`--jdk`, `JAVA_HOME`, managed `WORK/toolchain/jdk-*`, common installs
(Android Studio, Temurin/system JDKs); SDK = `ANDROID_HOME` (or
`ANDROID_SDK_ROOT`), managed `WORK/toolchain/android-sdk`, common installs
(`~/Android/Sdk`, `~/Library/Android/sdk`, …); NDK = `--ndk`,
`ANDROID_NDK_HOME`, `<sdk>/ndk/28.2.13676358`.

Step-by-step (for debugging; the subcommands keep working unchanged):

```sh
python3 build.py doctor --target android   # or: prepare, mac
python3 build.py prepare --work ~/ssx3-work --iso ~/SSX3.iso
python3 build.py android-libs --work ~/ssx3-work
python3 build.py android --work ~/ssx3-work
```

iOS (macOS Apple silicon host with Xcode; iPhone/iPad target) needs
your Apple Development team id and the offline-VU1 stage directory:

```sh
./build ios --ssx3-iso ~/SSX3.iso --work ~/ssx3-work --team <TEAM10> --om1-stage <OM1STAGE>
```

Add `--install` to push to the connected device (plain install; saves
are kept — never uninstall the app). The app id is
`com.ssx3recomp.<team-lower>`, signed with the Apple Development
certificate from Xcode's automatic signing for that team.

Handing this repo to a coding agent? Paste it this line:

> Read `docs/agent-setup.md` and build + install SSX 3 from my disc.

Keep the work directory outside this checkout and private: it holds your
disc image, generated game code, cards and signing keys. The support
libraries build from source by default: first-time cold build on a Mac
mini (M5 Pro, 4 jobs, empty caches) is prepare ~80 s, Android libraries
~600 s, APK link+package ~260 s (resumed link). Your machine will differ;
`build.py` prints per-stage timing. Support libraries build from source
by default; pass `--prebuilt` to reuse a verified prebuilt release when one
matches this checkout, with automatic fallback to source;
details in [tools/ssx3](tools/ssx3/README.md).

What the builder does with your disc: validates the ISO/ELF hashes, writes
a slim image plus a hole table into `--work`, generates EE code with the
pinned generator into `--work/codegen` (never into this repo), and links it
with the pinned runtime. Game-free support libraries (runtime, renderer,
microVU) build from source; with `--prebuilt`, a checksum mismatch or
missing prebuilt archive falls back to source automatically.

## Layout

- `build` — one-command wrapper (needs only `uv`; `make android ISO=…` wraps it).
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
`ssx.bradr.dev` (single definition: `presets/online.env`); to point at
another lobby, set `PS2X_NET_SERVER=<hostname-or-IPv4>` in your env with
`PS2X_NET=1`. The lobby source is vendored in `infra/lobby-rust/`, and
this repo holds the operator side (`infra/`: one small cloud host, deploy,
harden, backup, rollback). To play offline, just don't pick Online in the
menu; the in-game settings panel also has an Online row that turns it off.

## Provenance and licence

Fresh repo, no imported history. `SOURCE_PROVENANCE.md` + `SOURCE_MANIFEST.tsv`
record every file's pinned source and bytes; `THIRD_PARTY_NOTICES.md` names
every vendored component. GPL-3.0 (`LICENSE`).
