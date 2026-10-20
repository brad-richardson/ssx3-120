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
users: Ubuntu in WSL2). Play targets: Android arm64, iOS. Install the host
toolchain **before** cloning (a clean machine has no git yet):

- macOS: Xcode command-line tools (provides git and the Metal toolchain),
  then `brew install cmake ninja llvm python pkg-config autoconf automake libtool bison`.
- Linux (Ubuntu 24.04):
  `sudo apt-get update && sudo apt-get install -y python3 git cmake ninja-build clang lld g++ pkg-config curl ca-certificates unzip zip make autoconf automake libtool bison flex openjdk-17-jdk`.
  Windows users run all of this inside WSL2 Ubuntu, on its Linux
  filesystem (never `/mnt/c`).

Then install [`uv`](https://docs.astral.sh/uv/) — the `./build` wrapper runs
the builder through it, provisioning Python plus the font-pack libraries,
so no system Python setup — and clone:

```sh
git clone --recurse-submodules https://github.com/brad-richardson/ssx3-120.git ssx3-120
cd ssx3-120
./build android --ssx3-iso ~/SSX3.iso --accept-android-licenses
```

`./build doctor --target android --work <dir>` (or `prepare`, `mac`,
`ios`) re-checks the prerequisites — including the managed JDK/SDK under
your `--work` directory — and prints the exact install line when
something is missing.

Everything the build makes goes in `~/ssx3-work` (pass `--work <dir>` to
use another folder outside this checkout). Plan on ~25 GB. It holds your
APK and per-work `version-code.txt`. Your Android signing key lives
outside any work dir at `~/.config/ssx3-120/android-signing.keystore`
(created once, reused by every work dir; an existing work-dir
`ssx3-signing.keystore` is preferred when present so current installs
keep upgrading; `--keystore` overrides): back it up, because a
different key forces an uninstall, which wipes saves.

That one command runs doctor → prepare → android-libs → android in order
and resumes (prepare and android-libs are skipped when their outputs are
current). The JDK and the Android SDK/NDK are fetched automatically when
missing — pinned Temurin JDK 17 plus Google's command-line tools and SDK
packages, SHA-256-verified, into `--work/toolchain/` (project-local; no
system install, no sudo). Google's SDK licence must be accepted:
`--accept-android-licenses` is the documented path (interactive runs may
answer the prompt instead). Add `--install` to push to a connected device
afterwards: the builder installs the APK from
`apps/android/app/build/outputs/apk/release/app-release.apk` with
`adb install -r` (existing saves kept) and pushes your disc payload
beside it — the ISO stays outside the APK. The device must already be
visible to `adb devices` (USB debugging on, on-device trust prompt
accepted; use `--serial <adb-serial>` when several devices are
connected). WSL2 users attach the phone to Ubuntu first with `usbipd`,
otherwise `adb devices` is empty. `--prebuilt` to reuse a verified prebuilt support release when
one matches, and `--tricky-iso <Tricky.iso>` to validate your Tricky disc
and build the Tricky layer from it with the in-tree converter driver
(standalone step after prepare: `python3 build.py tricky --iso
<Tricky.iso> --work <work>`). `make android ISO=~/SSX3.iso` wraps the
same command. Supported Tricky discs:
[docs/tricky-inputs.md](docs/tricky-inputs.md).

### Why is the Tricky step slower?

The SSX 3 build recompiles SSX 3 and reads assets straight from your
disc. Tricky courses are converted to run inside SSX 3's engine — so SSX 3
handling, tricks and scoring apply — which means converting every course's
geometry, collision, resets, race lines, gems, HUD and menus, plus audio
when you have the tools for it, then recompressing each course archive in
the game's own archive format. Measured on a MacBook Air (M4, 10 cores, 16 GB), from a fresh clone:
the Android build takes about **14 minutes** without Tricky, and about
**1 h 10 min** with `--tricky-iso` (so the Tricky step adds roughly an
hour); a rerun of the same command reuses everything and finishes in
about 11 minutes. Faster machines are quicker (a 12-core Linux box
converts the Tricky courses in about 40 minutes); older or smaller
machines take longer. The step is optional: skip
it by leaving out `--tricky-iso`. It runs once, then its outputs are
cached in your work directory: a rerun reuses the layer when the disc,
converter, audio mode and toolchain are unchanged, and otherwise refuses
the existing `tricky/` directory — remove it or use a fresh `--work`
directory. Before anything heavy the builder requires **30 GB free** on
the work volume (4 GiB RAM minimum; lower `--jobs` under 8 GiB).
Tricky audio (`--tricky-audio on|off|auto`, default `auto`) builds the
music/speech phases only when the local `ffmpeg` (>= 6.0) and
`vgmstream-cli` (r1900+) probes pass — nothing is downloaded. Without
them, or with `off`, those courses play SSX 3's soundtrack.

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

iOS (macOS Apple silicon host with Xcode and its Metal toolchain: if `doctor`
reports `unable to find utility "metallib"`, run
`xcodebuild -downloadComponent MetalToolchain` once;
iPhone/iPad target) needs
your Apple Development team id; the offline-VU1 stage is generated
from your disc into `work/om1-stage` by the builder:

```sh
./build ios --ssx3-iso ~/SSX3.iso --team <TEAM10>
```

Find the 10-character id in Xcode > Settings > Accounts after signing in
with your Apple ID (free personal teams work but must re-sign weekly).
Xcode's automatic signing creates the Apple Development certificate the
build uses — a Distribution certificate installs but refuses to launch.
Add `--install` to push to the connected device (plain install; saves
are kept — never uninstall the app): connect over USB, trust the
computer, enable Developer Mode (Settings > Privacy & Security), and
pass `--device <udid>` from `xcrun devicectl list devices` when more
than one device is attached. The app id is
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

Disk, memory and time budgets (F15; measured 2026-10-18 from existing
build work dirs, Mac mini host): keep **25 GB free** for an Android cold
build, **20 GB free** for iOS, **12 GB free** for prepare/Mac. The builder
checks this before downloading anything and fails with the numbers when
the work filesystem is too full. Measured components: full Android SDK
3.4 GB (NDK 2.8 GB, build-tools 187 MB, platforms 126 MB, CMake 94 MB,
platform-tools 37 MB), managed Temurin JDK 17 0.3 GB, staged slim ISO
1.7 GB (your full 3.0 GB disc stays outside `--work`), prepare outputs
~0.8 GB (codegen 272 MB + generator build 504 MB + ELF 3.7 MB), Mac
runner build 1.9 GB, whole Mac work dir 5.8 GB, iOS work dir 13 GB
(several timestamped build dirs), APK ~0.34 GB — plus Gradle/Xcode
caches outside `--work`, hence the ~2x headroom. RAM: 8+ GB recommended.
Hosts with 8 GB or less build with reduced parallelism automatically
(`--jobs 2`, one ThinLTO link job, 2 GB Gradle heap: slow but complete);
larger hosts use `--jobs 8`, a RAM-sized ThinLTO cap and a 4 GB heap.
Explicit `--jobs`, `--thinlto-jobs` and `--gradle-heap-gb` always win, and
the builder logs the effective values. Low RAM is a warning, a full disk
is an error. Measured game-link peaks (SSX 3, NDK lld, 9,455 generated
files; all outputs byte-identical across job counts):

| ThinLTO jobs | 18 | 8 | 4 | 2 | 1 |
| --- | --- | --- | --- | --- | --- |
| Link peak (GiB) | 8.4 | 6.3 | 5.1 | 4.6 | 4.1 |

The game compile peaks ~7.5 GiB at `-j8` versus 2.4 GiB at `-j2` (hence the
`--jobs` cap on small hosts); Apple ld shows the same shape (5.8 GiB
default, 3.2 GiB capped). Last-resort fallback: game IPO off (1.4 GiB
link, output differs, small device-CPU cost).

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
- Render defaults are automatic: capable devices (iOS, or Android with
  5+ GiB RAM) use panel-native rendering on a 1080p panel, otherwise 2×
  with CAS 0.25; smaller devices use 1× with no CAS. The in-game
  Resolution setting overrides the default.
- One launcher icon ("SSX 3"). Mode (60/120) and Resolution changes need
  a full app restart (settings are read once per process): fully close
  the app before switching. The Mode lives in settings and persists
  across launches (default 120). Select+Start together (within ~200 ms)
  or the "…" button opens the settings panel.
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
menu; the in-game settings panel has no Online row.

Before filing, check [docs/known-issues.md](docs/known-issues.md) for current Alpha rough edges.

## Provenance and licence

Fresh repo, no imported history. `SOURCE_PROVENANCE.md` + `SOURCE_MANIFEST.tsv`
record every file's pinned source and bytes; `THIRD_PARTY_NOTICES.md` names
every vendored component. GPL-3.0 (`LICENSE`).
