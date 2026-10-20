# Android build and play notes

Start with the two lines in [README](../README.md) (`./build android
--ssx3-iso ~/SSX3.iso --accept-android-licenses`), or hand the repo to an
agent via [agent-setup.md](agent-setup.md). This page holds the
Android-specific quirks. Where the builder already prints the fix, that is
noted instead of repeated.

## Host setup

Install the host toolchain **before** cloning (a clean machine has no git yet):

- macOS: Xcode command-line tools (provides git),
  then `brew install cmake ninja llvm python pkg-config autoconf automake libtool bison`.
- Linux (Ubuntu 24.04):
  `sudo apt-get update && sudo apt-get install -y python3 git cmake ninja-build clang lld g++ pkg-config curl ca-certificates unzip zip make autoconf automake libtool bison flex openjdk-17-jdk`.
  Windows users run all of this inside WSL2 Ubuntu, on its Linux
  filesystem (never `/mnt/c`).

Linux arm64 also requires working x86_64 translation and x86_64 runtime
libraries for Google's Android SDK/NDK binaries; see the Linux-arm64
section in `tools/ssx3/README.md`. (Tool-handled: doctor executes NDK
Clang, SDK CMake/Ninja and aapt2 to catch missing translation; merely
having the files installed is insufficient.)

Then install [`uv`](https://docs.astral.sh/uv/) — the `./build` wrapper runs
the builder through it, provisioning Python plus the font-pack libraries,
so no system Python setup — and clone:

```sh
git clone --recurse-submodules https://github.com/brad-richardson/ssx3-120.git ssx3-120
cd ssx3-120
```

## Doctor and the one command

`./build doctor --target android --work <dir>` (or `prepare`, `mac`,
`ios`) re-checks the prerequisites — including the managed JDK/SDK under
your `--work` directory — and prints the exact install line when
something is missing. Run it before anything heavy; the fix it prints is
the documented path.

That one command runs doctor → prepare → android-libs → android in order
and resumes (prepare and android-libs are skipped when their outputs are
current).

Step-by-step (for debugging; the subcommands keep working unchanged):

```sh
python3 build.py doctor --target android   # or: prepare, mac
python3 build.py prepare --work ~/ssx3-work --iso ~/SSX3.iso
python3 build.py android-libs --work ~/ssx3-work
python3 build.py android --work ~/ssx3-work
```

## Toolchain fetch and SDK licences

The JDK and the Android SDK/NDK are fetched automatically when
missing — pinned Temurin JDK 17 plus Google's command-line tools and SDK
packages, SHA-256-verified, into `--work/toolchain/` (project-local; no
system install, no sudo). Google's SDK licence must be accepted:
`--accept-android-licenses` is the documented path (interactive runs may
answer the prompt instead).

Prefer your own toolchain? `--no-auto-download` never fetches: the builder
uses only what it finds and fails with a precise
"missing X; set Y or drop --no-auto-download" message. Search order: JDK =
`--jdk`, `JAVA_HOME`, managed `WORK/toolchain/jdk-*`, common installs
(Android Studio, Temurin/system JDKs); SDK = `ANDROID_HOME` (or
`ANDROID_SDK_ROOT`), managed `WORK/toolchain/android-sdk`, common installs
(`~/Android/Sdk`, `~/Library/Android/sdk`, …); NDK = `--ndk`,
`ANDROID_NDK_HOME`, `<sdk>/ndk/28.2.13676358`.

## Install, signing keys and saves

Everything the build makes goes in `~/ssx3-work` (pass `--work <dir>` to
use another folder outside this checkout). Plan on ~25 GB. It holds your
APK and per-work `version-code.txt`. Your Android signing key lives
outside any work dir at `~/.config/ssx3-120/android-signing.keystore`
(created once, reused by every work dir; an existing work-dir
`ssx3-signing.keystore` is preferred when present so current installs
keep upgrading; `--keystore` overrides): back it up, because a
different key forces an uninstall, which wipes saves. (Tool-handled: the
builder prints `Back up your keystore; a different key forces an
uninstall, which wipes saves.` after signing.)

There is one app icon ("SSX 3"). The 60/120 Mode lives in settings and
sticks between launches (default 120); 60 is the plain stock game.
Rebuilding and reinstalling keeps your saves: `--install` installs over
the current app (`adb install -r` on Android) and never removes cards or
saves. Back up your Android signing key
(`~/.config/ssx3-120/android-signing.keystore`): a different key forces an
uninstall, which wipes saves. Keep the work directory outside this
checkout and private: it holds your disc image, generated game code,
cards and signing keys.

Add `--install` to push to a connected device afterwards: the builder
installs the APK from
`apps/android/app/build/outputs/apk/release/app-release.apk` with
`adb install -r` (existing saves kept) and pushes your disc payload
beside it — the ISO stays outside the APK. The device must already be
visible to `adb devices` (USB debugging on, on-device trust prompt
accepted; use `--serial <adb-serial>` when several devices are
connected). WSL2 users attach the phone to Ubuntu first with `usbipd`,
otherwise `adb devices` is empty. (Tool-handled: with no device listed,
doctor fails closed naming `adb devices` and `usbipd` before anything
builds.)

## Turnip and display

On Android the game uses the Turnip graphics driver. `--no-turnip` omits
the driver. A failed download prints a warning and builds with the
system Vulkan driver. Adreno selects Turnip when present; a load failure
falls back to the system driver.

The game renders at your screen's resolution or above. Capable devices
(iOS, or Android with 5+ GiB RAM) use panel-native rendering on a 1080p
panel, otherwise 2× with light sharpening; smaller devices use 1× with no
sharpening. The in-game Resolution setting overrides the default. Race
courses hold 120 fps at screen resolution and at 2× on tested Adreno
phones. Lower resolutions exist for performance testing only. (Below-native
rendering is bench-only; CAS sharpening is approved, FXAA and ShadeBoost
are out — see [facts.md](facts.md).)

## Disk, memory and time budgets

Disk, memory and time budgets (measured 2026-10-18 from existing
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
is an error.

The support libraries build from source by default; pass `--prebuilt` to
reuse a verified prebuilt release when one matches this checkout, with
automatic fallback to source. What the builder does with your disc:
validates the ISO/ELF hashes, writes a slim image plus a hole table into
`--work`, generates EE code with the pinned generator into
`--work/codegen` (never into this repo), and links it with the pinned
runtime. Game-free support libraries (runtime, renderer, microVU) build
from source; with `--prebuilt`, a checksum mismatch or missing prebuilt
archive falls back to source automatically.

## Tricky build notes

`--prebuilt` to reuse a verified prebuilt support release when one
matches, and `--tricky-iso <Tricky.iso>` to validate your Tricky disc
and build the Tricky layer from it with the in-tree converter driver
(standalone step after prepare: `python3 build.py tricky --iso
<Tricky.iso> --work <work>`). `make android ISO=~/SSX3.iso` wraps the
same command. Supported Tricky discs: [tricky-inputs.md](tricky-inputs.md).

The Tricky build takes longer because every course is converted into SSX
3's engine. Measured on a MacBook Air (M4, 10 cores, 16 GB), from a fresh
clone: the Android build takes about **14 minutes** without Tricky, and
about **1 h 10 min** with `--tricky-iso` (so the Tricky step adds roughly
an hour); a rerun of the same command reuses everything and finishes in
about 11 minutes. The step is optional: skip it by leaving out
`--tricky-iso`. It runs once, then its outputs are cached in your work
directory: a rerun reuses the layer when the disc, converter, audio mode
and toolchain are unchanged, and otherwise refuses the existing `tricky/`
directory — remove it or use a fresh `--work` directory. Before anything
heavy the builder requires **30 GB free** on the work volume (4 GiB RAM
minimum; lower `--jobs` under 8 GiB). Tricky audio
(`--tricky-audio on|off|auto`, default `auto`) builds the music/speech
phases only when the local `ffmpeg` (>= 6.0) and `vgmstream-cli` (r1900+)
probes pass — nothing is downloaded. Without them, or with `off`, those
courses play SSX 3's soundtrack. Full disc keys, rerun rules and audio
choices: [tricky-inputs.md](tricky-inputs.md).
