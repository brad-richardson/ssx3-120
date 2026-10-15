# Build SSX 3 from your own disc

Build hosts: **macOS on Apple silicon** and **Linux x86_64/arm64** for
Android arm64. Windows users run the Linux path inside **WSL2 Ubuntu**.
Mac runner and Metal library builds require macOS; iOS preparation remains incomplete. This source preview does not
provide game downloads or game-bearing app binaries. Start with the
[disc identity contract](../../docs/ssx3-inputs.md).

This entry provides a local EE generator, Mac runner build and native Metal
GS/microVU library build. The baseline uses interpreted VU0 and no PGO;
it is not the optimized device play build. A fresh clone built a signed
Android APK on macOS Apple silicon in PUB1 (2026-10-12). PUB2 rebuilt a signed
APK from a second fresh clone on 2026-10-13 with package id
`com.ssx3.recomp`; the ISO is outside the APK. Neither build is an on-device
acceptance result. iOS offline-VU1 preparation remains incomplete.

The runner-only path and source-built Metal/microVU libraries were validated
on macOS Apple silicon from an isolated fresh clone on 2026-10-06, using only
the caller's supported disc image. One boot reached the "Press START button"
title screen. The optional suite, Android and iOS were not validated by that
acceptance run.

## Mac

Install Xcode with its command-line and Metal toolchains, Python 3, Git,
CMake, Ninja, Homebrew LLVM and the public renderer dependencies. The build
reads Homebrew's prefix dynamically; it does not install tools for you:

```sh
brew install cmake ninja llvm python sdl3 libpng jpeg-turbo zstd lz4 webp freetype
```

Keep all outputs in an external directory; never commit generated sources.
Run from this checkout (replace the paths):

```sh
python3 tools/ssx3/build.py prepare --iso '/path/to/your/SSX 3 (USA).iso' --work /path/to/private-build
python3 tools/ssx3/build.py mac-libs --work /path/to/private-build
python3 tools/ssx3/build.py mac --work /path/to/private-build
python3 tools/ssx3/build.py run --work /path/to/private-build
```

## Linux and Windows (WSL2) hosts

Use Ubuntu 24.04 with native Python, Git, CMake, Ninja, Clang, a C++ standard
library and JDK 17. Windows users first install WSL2 Ubuntu, then run every
command inside Ubuntu; keep the checkout and work directory on its Linux
filesystem, rather than `/mnt/c`. The builder does not install system packages
or accept SDK licenses for you.

```sh
sudo apt-get update
sudo apt-get install -y python3 git cmake ninja-build clang lld g++ pkg-config curl ca-certificates unzip zip make autoconf automake libtool bison flex openjdk-17-jdk
python3 tools/ssx3/build.py doctor --target prepare
python3 tools/ssx3/build.py prepare --tools-only --work /path/to/private-build --jobs 4
```

`prepare --tools-only` builds the pinned native recompiler without a disc and
without generating game code. A later normal `prepare --iso ...` reuses that
compiler. `doctor` needs no work directory or disc and changes no files. It
prints failed checks and fixes and exits nonzero until prerequisites pass.
`CC`/`CXX` override compiler selection; versioned Clang names such as
`clang-18`/`clang++-18` are supported. Mac keeps its Homebrew LLVM preference.

Install Google's Android command-line tools into an SDK directory, set
`ANDROID_HOME` and `JAVA_HOME` (JDK 17), and put the JDK's `bin` on `PATH`.
Use the platform's command-line tools from the
[Android SDK downloads](https://developer.android.com/studio#command-tools).
Then install the exact components and review the licenses:

```sh
"$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" "platforms;android-34" "build-tools;34.0.0" "ndk;28.2.13676358" "cmake;3.22.1" "platform-tools"
"$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" --licenses
python3 tools/ssx3/build.py doctor --target android
```

**Linux arm64:** Google's Linux NDK and SDK native build tools are x86_64
executables. Configure x86_64 binfmt translation and its x86_64 userspace
libraries before building (for example, the Mac Linux VM's translation
support). Python, JDK and the generator can run as native arm64. Doctor
executes NDK Clang, SDK CMake/Ninja and aapt2 to catch missing translation;
merely having the files installed is insufficient. This is a translated
Android toolchain, not a native Linux-arm64 NDK. See
[Google's NDK host packages](https://developer.android.com/ndk/downloads).

With doctor passing, use the Android commands below, starting with normal
`prepare --iso ...` using your own disc. `android-libs` accepts `--ndk` or
finds the exact pinned version beneath `ANDROID_HOME`. `mac`, `mac-libs`
and `run` are Mac-only; Linux host support does not add a Linux play target.

## Font pack (on by default)

`run` rebuilds the four font atlases with the raster-edge recipe before
launching, unless `--skip-fonts` is passed. It needs one input the build
cannot make from the disc alone: a texture dump holding the four stock
font atlases, dumped from your own copy (place it at
`<work>/stock-dump`, or pass `--stock-dump <dir>`). Glyph metrics come
from the SFN tables in your own ISO. The builder
(`tools/ssx3/remaster_font.py`, numpy + Pillow) upscales the stock atlas
RGBA to 4x, replaces only the ink-cell alpha (edge-directed 8x upsample,
smoothstep, box downsample, clipped to the stock mask), and leaves RGB
untouched. Output lands in `<work>/font-pack` and is applied via
`GE1_TEX_REPLACE` for that run. No game data, glyph images, outlines or
atlases ship in the builder — only the recipe and measured constants.

`stage` builds the same pack into the device layout (`stage/files/font-pack`)
and appends `GE1_TEX_REPLACE` (+ `GE1_TEX_PRELOAD=1`) to
`stage/files/ps2x.env`, so `install` pushes the font with the APK files;
with `--skip-fonts` or no dump the env is left untouched and the game
boots stock fonts. A Part-7 survey of the USA disc proved the ISO carries
no dump-equal atlas pixels (SFN files hold metrics/kerning only; the word
sprites match no packed texture), so all four atlases still need the
user's own dump — there is no iOS bundle path in this builder.

The default Mac target is the runner. The development suite is optional and
requires analyzer/recompiler fixture targets; it is separate from the external
runner/title acceptance:

```sh
python3 tools/ssx3/build.py mac --work /path/to/private-build --suite
/path/to/private-build/runner-build/ps2xTest/ps2x_tests
```

Run that suite **from the checkout root**. The generator pin is
`03c10b5aeccd4fb673fecaea3cf4a5f5064afe14`. It selects overlapping resume owners by greatest candidate function start,
independent of host hash-table order. `prepare` checks ISO and extracted ELF SHA256, fetches the pinned
generator from the local checkout when present (otherwise the public fork), builds it, writes absolute local TOML
paths, and records the generated registration SHA256. Use a fresh work
directory for regeneration; existing output is refused. Registration hashes
from earlier generator pins do not describe the deterministic ownership rule.

Names are optional. The public tree contains no game symbol names and
`prepare` uses the generator's default `sub_…` identifiers. For local
development, supply your own untracked sidecar with
`prepare --symbol-names /path/to/local/ssx3-symbols.tsv`; the file remains
outside the checkout. The sidecar changes identifiers and comments, so
its generated registration SHA differs from the default name-free output.

`mac-libs` pins the public ARMSX2 fork to
`afad5a04c2668a10e12adadaa51d54c46f832236` (`armsx2-ssx3-four` tip, the play vendor; carries the on-device
upscale provider the adapter includes and the PB66 VMA allocator fix), builds plutovg v1.1.0 and plutosvg v0.0.7
locally, builds GE1 and the JIT bridge, and stages public renderer resources
and metallibs. It requires no OM1 recordings, offline stage or private header
checkout. Libraries stay in the build directory and use installed system
dependencies. Keep the directory in place while running.

`run` uses the original disc path, extracted ELF and a **new card root in this
work directory**; it bypasses startup movies. It does not use another user's
cards, texture packs or play settings. See [knobs.tsv](knobs.tsv) for a
source-registry snapshot; supported defaults remain defined by
[ps2_knobs.h](../../ps2xRuntime/include/ps2_knobs.h). Registry classes describe
the source census, not a promise that every diagnostic knob is supported.

## Android APK

Use JDK 17, Android SDK 34, NDK 28.2.13676358 and CMake 3.22.1. Set
`ANDROID_HOME` and `JAVA_HOME` to your installs. EE output from `prepare` is
shared with Android; no remote build host or PGO profile is needed:

```sh
python3 tools/ssx3/build.py android-libs --work /path/to/private-build --ndk "$ANDROID_HOME/ndk/28.2.13676358" --jobs 4
python3 tools/ssx3/build.py android --work /path/to/private-build --jnilibs /path/to/private-build/jniLibs --jobs 4
python3 tools/ssx3/build.py install --work /path/to/private-build
```

The signed public build defaults to `com.ssx3.recomp`, with its own
Android `files/` directory and card. Use `--app-id your.dotted.package` on
**both** `android` and `install` to choose another id; install checks that it
matches the built APK and rewrites the staged env paths. The Java and JNI
helper classes retain the `com.ps2x.runner` source namespace. The separate
package id allows side-by-side installation with an existing play build.

`install` uses adb to install the APK and SHA-check the ISO, ELF, envs and
GE1 resources in `/sdcard/Android/data/com.ssx3.recomp/files/`. A repeat
install skips files already matching by SHA and preserves existing cards and
saves. The ISO stays outside the APK. On Android 11+, the installed app uses
its own external files directory; adb has shell access to push those files.
Use `--serial DEVICE` if adb sees more than one device. `stage` remains a
local layout preview and prints manual adb commands. The shipped
Android presets are [presets/ps2x.env](presets/ps2x.env) and
[presets/full120.env](presets/full120.env), derived from the Odin play env
with every private path removed and the SET2 settings overlay on
(`PS2X_SETTINGS_OVERLAY=1`). The public preset selects the driver by Vulkan
GPU vendor and uses interpreted VU0 (`PS2X_VU0_RECOMP=0`). Reported RAM at
or above 5 GiB uses panel-native rendering on a 1080p panel, otherwise 2×,
with display-size export and CAS. Lower RAM uses 1× and 1:1 export with no
CAS; Android scales that output to the panel. The in-game Resolution setting
persists and overrides the render default.

JNI input must contain your locally source-built `arm64-v8a/libge1_gs.so`
and `libmv2_microvu.so`; never include `libhardware.so`, which this fork builds
from [HAL source](../../android/hwcompat/hwcompat.c). Optional custom Vulkan
comes from [Mesa sources](https://github.com/brad-richardson/mesa/tree/ssx3).
The public `android` step downloads the pinned release in
[`turnip.json`](turnip.json), verifies the binary and notices by SHA-256, and
packages both. `--no-turnip` omits them. A failed download prints a warning
and builds with the system Vulkan driver. Adreno selects Turnip when present;
a load failure falls back to the system driver.
The APK stays under `android/app/build/outputs/apk/release` locally.

`android-libs` invokes the pinned ARMSX2 public source dependency recipe
`.github/workflows/scripts/android/build-dependencies.sh` with your NDK,
ABI arm64-v8a and API 29, then builds the public GE1/mv2 adapters against
that local prefix. The dependency recipe has its own parallelism setting
(based on host CPU count). It stages JNI libraries and public renderer
resources into your work directory. This source-only route avoids the old
private remote helper and bundled dependency directory. Android library/APK
compilation and signing were validated on macOS Apple silicon by PUB1 and PUB2.
Installation, title, a finished race, controls, sound, and save/reload
remain for device acceptance. Linux hosts, the remaster pack and Tricky
preparation have not been validated by this route.

After building, follow [Android setup/save protection](../../android/README.md)
for the 60/120 launcher layers. `build.py
android` creates a per-user signing keystore once in your work directory
(`ssx3-signing.keystore`) and reuses it on every later build, with a
monotonic `versionCode` from `version-code.txt` in the same directory.
**Back up your keystore; a different key forces an uninstall, which wipes
saves.** Do not distribute generated code or apps containing it.

## Optional local optimization and iOS

VU0 images: run your own baseline runner with `PS2X_VU0_RECOMP_DUMP`
pointing to an existing private directory. The runtime emitter directly writes
`vu0e_<hash>.cpp` when it encounters the program; no pre-existing image is
needed. Supply that directory through CMake `PS2X_VU0_RECOMP_DIR` or Gradle
`ps2xVu0RecompDir` and rebuild, then use `PS2X_VU0_RECOMP=1`. If you already
have your own raw 4 KiB microprogram image, the public `vu1_fixture_gen
--image <image.bin> <output-dir>` can re-emit it without another boot. These
outputs are game-derived and stay private. The baseline command above
builds with interpreted VU0; it does not take another user's VU images.

Android PGO: use Gradle `-Pps2xPgoGenerate=ON
-Pps2xPgoHelperDir=/path/to/this/tools/ssx3/pgo`, play with your own disc and
save, pull your own raw profile, and merge with **the matching NDK's**
`llvm-profdata merge -o profile.profdata <raw files>`. Rebuild without the GEN
flag, passing `-Pps2xPgoData=/absolute/path/profile.profdata`. Profiles include
translated guest-function identities and counts; keep them private and
regenerate after guest-code changes. The helper only flushes every 30 seconds.
PGO is optional; no profile is bundled or fetched.

[iOS base preset](presets/ios.env) and [full120 layer](presets/full120-ios.env)
export settings without a personal host/card path or automatic pad route.
They require your own game inputs and offline VU1 stage. The handwritten
[offline host machinery](offline-core/README.md) is included as source,
separate from the external recorded assembly/tables. iOS has no JIT VU1;
its OM1 assembly/tables are local game-derived outputs. The complete portable
recording/generation and app-signing recipe remains incomplete; consult the
[build inventory](../../docs/ssx3-build.md). Shader prewarm lists are optional 24-byte host pipeline selector tuples
(PS/VS/extras, no texture, asset identifier, instruction or guest memory).
[tfx-selectors.json](tfx-selectors.json) contains 247 shader configurations;
write them to your private build directory with:

```sh
python3 tools/ssx3/write_prewarm.py /path/to/private-build/tfx-prewarm.bin
```

Set `GE1_TFX_PREWARM` to that writable file if desired. The renderer appends
new host selector tuples while running. The format is defined by
`PipelineSelectorMTL` in the pinned public ARMSX2 Metal renderer; this list
is configuration, separate from the forbidden offline VU recordings.

No ISO, ELF, BIOS, generated EE/VU source, offline recording, texture/font pack,
converted course/audio, memory card, capture, PGO profile or compiled app is
included here. Source pins, identity hashes, configuration and build recipes
can be shared; game-derived outputs and personal keys/settings stay local.
Preserve dependency licences from their source checkouts.

### Android prebuilt support libraries

`android-libs` and `android` default to `--prebuilt=auto`. A release matching
this checkout's source and toolchain contract is downloaded, checked against
`SHA256SUMS`, unpacked with a strict file allowlist, and verified file by file.
The runtime and platform bootstrap are static archives; GE1, microVU and the
hardware shim remain shared libraries. Your generated game library is linked
locally. Neither the support archive nor CI contains your generated game code,
ELF, ISO, saves or APK. MComm is not built or included.

Verified bundles are cached in `WORK/prebuilt-cache`; `--prebuilt-cache PATH`
selects another cache. A matching cache works offline. A missing release,
checksum mismatch, invalid archive, or source/toolchain mismatch falls back to
the source build. Source fallback needs the NDK and the usual public source
build dependencies. `--prebuilt=off` always selects source libraries. Custom
application IDs also use source libraries because the prebuilt bootstrap binds
the public app's external-data path.

```sh
python3 tools/ssx3/build.py android-libs --work /path/to/private-build --ndk /path/to/ndk/28.2.13676358
python3 tools/ssx3/build.py android --work /path/to/private-build
# Explicit source reference:
python3 tools/ssx3/build.py android-libs --prebuilt=off --work /path/to/private-build --ndk /path/to/ndk/28.2.13676358
python3 tools/ssx3/build.py android --prebuilt=off --work /path/to/private-build
```

The consumer checks the source contract again at CMake configure time. At
load, separate guards check the generated-code ABI and the requested runtime
contract; stale combinations fail loudly. Static runtime archives preserve the
existing final game-library link and avoid a cyclic runtime/game shared-library
dependency. The exact source route remains available with `--prebuilt=off`.

Maintainer release recipe (game-free, on an Android build host):

```sh
python3 tools/ssx3/prebuilt.py contract
python3 tools/ssx3/prebuilt.py build --work /path/to/empty-support-work --ndk /path/to/ndk/28.2.13676358 --jobs 4
```

The tag `android-prebuilt-<full-contract-hash>` triggers
`.github/workflows/android-prebuilt.yml`. Actions are pinned by commit; the
workflow uses NDK `28.2.13676358` and builds the paired renderer/microVU sources
from `brad-richardson/armsx2` at the recipe's exact vendor revision. The export
rejects generated-code inputs, scans the compilation graph and defined binary
symbols, and packages only the allowed support files. It omits inherited arcade
cards and unrelated UI assets from the vendor resources.

The release archive includes GPL/third-party notices and `notices/SOURCES.json`;
`SOURCE.json` beside the archive links the exact source revisions and recipe.
`SHA256SUMS` covers the archive. There is no separate vendor release dependency:
the PS2Recomp workflow builds and publishes the complete paired bundle. The
initial tag/release and CI acceptance must be completed before claiming a
prebuilt download is available; until then `auto` uses the source fallback.
