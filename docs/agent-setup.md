# Agent setup: building SSX 3 for Android or iOS

You are a coding agent helping the user build and install this game from
**their own disc**. Read this whole file first, then run exactly what it
says. Do not improvise toolchains, flags, or signing.

## Start here: discover before you run

1. **Target.** Android (`./build android ...`) or iOS (`./build ios ...`).
   Android builds on macOS Apple silicon or Linux x86_64/arm64 (Windows:
   Ubuntu in WSL2, everything inside its Linux filesystem, never
   `/mnt/c`); iOS needs macOS Apple silicon with Xcode (including its
   Metal toolchain).
2. **Inputs.** The user supplies their USA `SSX 3 (USA).iso`
   (`SLUS_207.72`); confirm the path exists before running anything.
   Optional: their Tricky USA disc (`--tricky-iso`, see
   `docs/tricky-inputs.md`) — without it the build is stock SSX 3 only.
3. **Host toolchain before clone.** A clean machine has no git yet:
   macOS needs Xcode command-line tools plus
   `brew install cmake ninja llvm python pkg-config autoconf automake libtool bison`;
   Ubuntu 24.04 needs `sudo apt-get update && sudo apt-get install -y
   python3 git cmake ninja-build clang lld g++ pkg-config curl
   ca-certificates unzip zip make autoconf automake libtool bison flex
   openjdk-17-jdk` (full lines in `tools/ssx3/README.md`). Then install
   [`uv`](https://docs.astral.sh/uv/).
4. **Checkout.** This repo needs its engine submodule: clone with
   `git clone --recurse-submodules https://github.com/brad-richardson/ssx3-120.git`, or recover with
   `git submodule update --init -- deps/PS2Recomp`. Every builder command
   below fails closed with that recovery line when the submodule is
   missing or stale.
5. **Prerequisites.** The `./build` wrapper runs the builder through uv
   (pinned Python plus the font-pack libraries, no system Python setup).
   Then run the doctor before anything heavy, from the repo root with
   your work directory so managed-toolchain probes resolve:
   `./build doctor --target android --work <dir>`
   (or `--target ios` on a Mac). It prints the exact install line for
   anything missing. `python3 build.py doctor` without uv can report a
   healthy toolchain as missing; prefer the wrapper.

## Ground rules

- Bring your own disc: the user supplies their USA `SSX 3 (USA).iso`
  (`SLUS_207.72`). Never commit game data, ISOs, generated code, cards,
  keystores, or provisioning profiles. The work directory stays outside
  this checkout and private.
- The builder resumes: `prepare` and the `*-libs` steps are skipped when
  their outputs are current. Re-run the same command to continue — and do
  not stack a build and a build+install: one `--install` command builds
  (or resumes) and installs; the second example below is not a rebuild.
- iOS: never uninstall the app (it wipes saves) — upgrade with a plain
  install, which is what `--install` does.
- If a step fails, read its error to the end and fix the named cause
  (missing tool, unaccepted licence, unknown device). Do not retry a
  failed Xcode/Gradle step with different flags hoping it passes.

## Android (host: macOS Apple silicon, Linux; target: Android arm64)

```sh
./build android --ssx3-iso ~/SSX3.iso --accept-android-licenses
./build android --ssx3-iso ~/SSX3.iso --accept-android-licenses --install
```

The JDK and Android SDK/NDK download automatically (pinned, SHA-checked)
unless `--no-auto-download` is passed — then set `JAVA_HOME`,
`ANDROID_HOME`, and the NDK yourself. Debug step by step with
`python3 build.py doctor --target android`, `prepare`, `android-libs`,
`android`. `--serial <adb-serial>` picks among several Android devices
(`adb devices` lists them); `--device <udid>` is iOS-only and
`--serial` is Android-only — the builder rejects the swapped combination.
WSL2: attach the phone to Ubuntu first (`usbipd`: the Windows-visible
phone is not visible in Ubuntu until forwarded), authorize USB debugging
and accept the on-device trust prompt; otherwise `adb devices`
is empty and the doctor says so before anything builds. Keep 25 GB free
under the work directory (`~/ssx3-work` unless `--work` is given; README budgets); the builder preflights this.

## iOS (host: macOS Apple silicon with Xcode; target: iPhone/iPad)

One user value is required to build: the Apple Development **team id**
(10 characters, Xcode > Settings > Accounts > Team — sign in with an
Apple ID first; free personal teams work, re-sign weekly). Before running
the builder, confirm Xcode has an Apple Development certificate and its
private key for that team (create one in Xcode first if needed; doctor
requires it before the automatic-signing build runs). A connected
device is required only for `--install`: connect it over USB, trust the
computer, enable Developer Mode (Settings > Privacy & Security), and find
its UDID with `xcrun devicectl list devices`. The offline-VU1 microVU
stage is generated from your disc into `work/om1-stage` by the builder
(`--om1-stage` overrides it; Mac-only generation).

```sh
./build ios --ssx3-iso ~/SSX3.iso --dry-run --team <TEAM10>
./build ios --ssx3-iso ~/SSX3.iso --team <TEAM10>
./build ios --ssx3-iso ~/SSX3.iso --team <TEAM10> --install
```

The dry run needs no prepared outputs. Keep 20 GB free under the work directory (`~/ssx3-work` by default).

What each run does: `doctor` (Xcode, CMake, signing identity, device),
`prepare` (ISO validation + codegen), `ios-libs` (GE1 renderer, OM1
microVU, SDL2, MoltenVK — built from source, ~10 min first time), `ios`
(build the app with Xcode automatic signing, stage the disc payload and
presets, re-sign with the provisioning profile's own certificate).
`--install` pushes to the one connected physical device; with several,
add `--device <udid>` from `xcrun devicectl list devices`.

Facts the builder depends on (do not "fix" these):

- The bundle id is `com.ssx3recomp.<team-lower>`; it must match the
  provisioning profile Xcode already manages for that team.
- Signing uses the Apple Development certificate named in the profile,
  not a Distribution one — anything else installs but refuses to launch.
- The engine plist template keeps its own id; the builder rewrites the
  staged copy and says so in the log.

After install, confirm the app stays up. Android: `adb shell pm path
<app-id>` lists the installed package, then launch it from the device and
confirm it stays in the foreground. iOS: launch on the device and confirm
it stays up: `xcrun devicectl device process launch --device <udid>
com.ssx3recomp.<team-lower>`, then check the process is still listed a
minute later. Gameplay feel is the user's call — hand the device back.

## Checks before handing back

- `python3 tools/ssx3/tests/test_build_onecmd.py` and (for iOS)
  `python3 tools/ssx3/tests/test_ios.py` pass. The tests are stdlib-only;
  if `python3` is missing, provision it with `uv python install` and run
  them with that interpreter — never a different toolchain than the build.
- The exact commands run, their SHAs/timings, and anything the user must
  decide (device choice, Tricky ISO, gameplay feel) go in the handoff.
  Name the product/engine/toolchain/disc identities and disclose the
  Tricky stop (stock-only without `--tricky-iso`) before requesting
  inputs.
