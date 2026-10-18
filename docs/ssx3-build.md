# SSX 3 build prerequisites and current gaps

Public entry: [tools/ssx3](../tools/ssx3/README.md) (or `python3 build.py`
at the repo root). Supported build hosts: macOS Apple silicon, Linux
x86_64/arm64 (Windows users: Ubuntu in WSL2); play targets: Android arm64 /
iOS. `doctor` verifies the toolchain before anything downloads.

The public entry regenerates EE code and builds a Mac baseline without PGO
or generated VU0; Android builds the support libraries from source by
default (a verified game-free archive is tried first when one matches;
checksum mismatch falls back to source). Prepare the
[exact supported inputs](ssx3-inputs.md) locally first. Library compilation
and SSX 3 guest-code generation are separate steps.

## Inputs by platform

| Target | Toolchain / public source | Still staged locally or private |
| --- | --- | --- |
| macOS arm64 | Apple or Homebrew Clang, CMake, Ninja; engine + `armsx2` vendor pin (fetched by the recipe) | SSX 3 EE generation (your disc), GE1 dependency prefix (Homebrew + plutovg/plutosvg, fetched), renderer resources from the matching vendor build |
| Android arm64 | JDK 17, SDK 34, NDK 28.2.13676358, CMake 3.22.1; Gradle/AGP; engine + matching GE1/microVU sources (or verified support archives) | EE/VU0 generated directories (your disc), JNI libraries, shader resources; optional Mesa Turnip (downloaded release or local build); your own signing keystore |
| iOS arm64 / arm64 simulator | Xcode SDK/toolchain, CMake, Ninja | EE/VU0 generation, OM1 offline VU1 core + tables, signing setup; the portable iOS offline-VU1 recipe is still incomplete |

Evidence: [engine CMake minimum](../deps/PS2Recomp/CMakeLists.txt#L1),
[Android properties/toolchain](../apps/android/app/build.gradle#L5),
[Mac assembly](../deps/PS2Recomp/ps2xRuntime/third_party/armsx2/build.sh#L55),
[iOS assembly](../deps/PS2Recomp/ps2xRuntime/third_party/armsx2/build.sh#L122),
[iOS runtime linking](../deps/PS2Recomp/ps2xRuntime/CMakeLists.txt#L895).

## What the renderer's own recipe does

`ps2xRuntime/third_party/armsx2/build.sh` is the renderer's host recipe
inside the engine (public file, advanced use). Prefer
[tools/ssx3](../tools/ssx3/README.md), which drives the same sources with
pinned fetches. The script's interface is `build.sh
<mac|android|ios|ios-sim> --armsx2 <checkout> [--build-root <scratch>]
[--jobs N]`; it has no `--help` or dry-run mode
([parser](../deps/PS2Recomp/ps2xRuntime/third_party/armsx2/build.sh#L29)). It requires clean
source trees, archives the supplied ARMSX2 HEAD, and records both source SHAs.
It does **not** fetch a fixed renderer revision or generate the game inputs.
Portable iOS offline recording/generation remains outstanding.

Mac builds `libge1_gs.dylib`, `ge1_replay` and `libmv2_microvu.dylib`
([configuration](../deps/PS2Recomp/ps2xRuntime/third_party/armsx2/build.sh#L81)). Android stages
GE1 and mv2 sources for the NDK build. iOS builds static
GE1 from a staged dependency bundle; it does not produce the offline VU1 stage
([iOS branch](../deps/PS2Recomp/ps2xRuntime/third_party/armsx2/build.sh#L122)). OM1 consumes
recorded assembly/tables and staged core sources; coverage of missing programs
must be validated separately ([OM1 inputs](../deps/PS2Recomp/ps2xRuntime/third_party/armsx2/om1/rt/CMakeLists.txt#L1)).

The runtime accepts `PS2X_GAME_CODEGEN_DIR`, `PS2X_VU0_RECOMP_DIR`,
`PS2X_OM1_STAGE` and `PS2X_OM1_LIB`
([EE](../deps/PS2Recomp/ps2xRuntime/CMakeLists.txt#L790),
[VU0](../deps/PS2Recomp/ps2xRuntime/CMakeLists.txt#L840),
[OM1](../deps/PS2Recomp/ps2xRuntime/CMakeLists.txt#L895)). Their presence is not proof that
they match this source revision. Keep a local manifest of generator/config/map,
transforms, output hashes, renderer/microVU/resource hashes and toolchain
(`prepare` writes `inputs.json` for the game side; `deps.lock.json` pins the
rest).

PGO is optional with bundled profiles: `tools/ssx3/pgo/android/` (PB58)
and `tools/ssx3/pgo/ios/` (IPB30) ship gzip-compressed `.profdata` plus
`MANIFEST.json` provenance. `build.py android`/`ios` apply the bundled profile
(`-Pps2xPgoData` / Xcode `-fprofile-use`) only when the user's
`codegen/register_functions.cpp` SHA matches the trained register SHA;
otherwise they print one line and build without PGO. `--no-pgo` always skips;
`--pgo-profile` uses your own file. `ps2xPgoGenerate` defaults OFF
([properties](../apps/android/app/build.gradle#L17)). Do not reuse a profile
trained against different generated code.

## Licences and source records

Credit [PS2Recomp upstream](https://github.com/ran-j/PS2Recomp) and preserve
its [GPL v3 licence](../LICENSE). GE1/microVU build on
[PCSX2/ARMSX2 fork sources](https://github.com/brad-richardson/armsx2/tree/armsx2-ssx3-four);
these adapters are GPL-3.0-or-later. Optional Turnip comes from
[Mesa fork sources](https://github.com/brad-richardson/mesa/tree/ssx3), mostly
MIT with component-specific notices
([inventory](https://github.com/brad-richardson/mesa/blob/ssx3/docs/license.rst)).
SoundTouch, rcheevos and fetched libraries retain their own notices
([SoundTouch](../deps/PS2Recomp/ps2xRuntime/third_party/soundtouch/README.at1.md),
[rcheevos licence](../deps/PS2Recomp/ps2xRuntime/third_party/rcheevos/LICENSE)). Record exact
source revisions and applicable notices for the components you build.
Software licences do not authorize distributing game-derived assets.

Continue with [Android setup](../apps/android/README.md) or
[troubleshooting and validation](ssx3-troubleshooting.md).
