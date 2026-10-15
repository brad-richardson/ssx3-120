# SSX 3 build prerequisites and current gaps

New local preparation/build entry: [tools/ssx3](../tools/ssx3/README.md). Supported build host: macOS Apple silicon; play targets: Android arm64 / iOS. Windows/x86 is unsupported.

This is the maintainer build dependency inventory (**maintainer only**).
The maintainer-only `ps2xRuntime/third_party/armsx2/build.sh` recipe below
uses private pinned inputs and a private build host; strangers must not use
it — build through the public entry, [tools/ssx3](../tools/ssx3/README.md),
instead. The new public entry
regenerates EE code and builds a Mac baseline without PGO or generated VU0;
optimized device play builds still require additional local preparation. Prepare the
[exact supported inputs](ssx3-inputs.md) locally first. Library compilation and
SSX 3 guest-code generation are separate steps.

## Inputs by platform

| Target | Toolchain / public source | Currently private or externally staged inputs |
| --- | --- | --- |
| macOS arm64 | Apple C/C++ compiler, CMake, Ninja; PS2Recomp and PCSX2 fork `armsx2-ssx3-four` | SSX 3 EE generation, matching VU0 inputs, renderer resources, GE1 dependency prefix, OM1RT sources/header trees and recordings used by the current microVU library recipe |
| Android arm64 | JDK 17, SDK 34, NDK 28.2.13676358, CMake 3.22.1; Gradle/AGP; PS2Recomp plus matching GE1/microVU sources | EE/VU0 generated directories, JNI libraries and shader resources; renderer script delegates to a private remote build recipe; optional locally built Mesa Turnip |
| iOS arm64 / arm64 simulator | Xcode SDK/toolchain, CMake, Ninja; GE1 static archives and source headers | EE/VU0 generation, SDL2 staging, GE1 dependency bundle/resources, OM1 offline VU1 core + tables + matching archives; private app-staging recipe and signing setup |

Evidence: [root CMake minimum](../CMakeLists.txt#L1),
[Android properties/toolchain](../android/app/build.gradle#L5),
[Mac assembly](../ps2xRuntime/third_party/armsx2/build.sh#L55),
[Android delegation](../ps2xRuntime/third_party/armsx2/build.sh#L95),
[iOS assembly](../ps2xRuntime/third_party/armsx2/build.sh#L122),
[iOS runtime linking](../ps2xRuntime/CMakeLists.txt#L895).

## What the maintainer-only script does

`ps2xRuntime/third_party/armsx2/build.sh` is **maintainer only**. Strangers
must not use it; use [tools/ssx3](../tools/ssx3/README.md) instead. It is the maintainer's host recipe.
Its interface is `build.sh <mac|android|ios|ios-sim> --armsx2 <checkout>
[--build-root <scratch>] [--jobs N]`; it has no `--help` or dry-run mode
([parser](../ps2xRuntime/third_party/armsx2/build.sh#L29)). It requires clean
source trees, archives the supplied ARMSX2 HEAD, and records both source SHAs.
It does **not** fetch a fixed renderer revision or generate the game inputs.
The public tools/ssx3 entry avoids this staging for its Mac baseline and
provides an unvalidated Android source dependency/library command. Portable
iOS offline recording/generation remains outstanding.

Mac builds `libge1_gs.dylib`, `ge1_replay` and `libmv2_microvu.dylib` using
private pinned dependencies and OM1RT inputs
([configuration](../ps2xRuntime/third_party/armsx2/build.sh#L81)). Android stages
GE1 and mv2 sources, then invokes a private SSH/Docker build helper
([delegation](../ps2xRuntime/third_party/armsx2/build.sh#L107)). iOS builds static
GE1 from a staged dependency bundle; it does not produce the offline VU1 stage
([iOS branch](../ps2xRuntime/third_party/armsx2/build.sh#L122)). OM1 consumes
recorded assembly/tables and private core sources; coverage of missing programs
must be validated separately ([OM1 inputs](../ps2xRuntime/third_party/armsx2/om1/rt/CMakeLists.txt#L1)).

The runtime accepts `PS2X_GAME_CODEGEN_DIR`, `PS2X_VU0_RECOMP_DIR`,
`PS2X_OM1_STAGE` and `PS2X_OM1_LIB`
([EE](../ps2xRuntime/CMakeLists.txt#L790),
[VU0](../ps2xRuntime/CMakeLists.txt#L840),
[OM1](../ps2xRuntime/CMakeLists.txt#L895)). Their presence is not proof that
they match this source revision. Keep a local manifest of generator/config/map,
transforms, output hashes, renderer/microVU/resource hashes and toolchain.
No immutable cross-platform public release pin is available yet.

PGO is optional: Android's `ps2xPgoData` defaults empty and
`ps2xPgoGenerate` defaults OFF
([properties](../android/app/build.gradle#L17)). The private iOS staging helper
also supports `--no-pgo`. A no-PGO clean-input build has not been independently
validated here. Do not reuse a profile trained against different generated code.

## Licences and source records

Credit [PS2Recomp upstream](https://github.com/ran-j/PS2Recomp) and preserve
its [GPL v3 licence](../LICENSE). GE1/microVU build on
[PCSX2/ARMSX2 fork sources](https://github.com/brad-richardson/armsx2/tree/armsx2-ssx3-four);
these adapters are GPL-3.0-or-later. Optional Turnip comes from
[Mesa fork sources](https://github.com/brad-richardson/mesa/tree/ssx3), mostly
MIT with component-specific notices
([inventory](https://github.com/brad-richardson/mesa/blob/ssx3/docs/license.rst)).
SoundTouch, rcheevos and fetched libraries retain their own notices
([SoundTouch](../ps2xRuntime/third_party/soundtouch/README.at1.md),
[rcheevos licence](../ps2xRuntime/third_party/rcheevos/LICENSE)). Record exact
source revisions and applicable notices for the components you build.
Software licences do not authorize distributing game-derived assets.

Continue with [Android setup](../android/README.md) or
[troubleshooting and validation](ssx3-troubleshooting.md).
