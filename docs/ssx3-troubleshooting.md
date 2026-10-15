# SSX 3 troubleshooting

New local preparation/build entry: [tools/ssx3](../tools/ssx3/README.md). Supported build host: macOS Apple silicon; play targets: Android arm64 / iOS. Windows/x86 is unsupported.

First record the source revision and the exact stage that failed. Stop at a
wrong input hash, ABI mismatch or guest crash; do not change several knobs to
hide the failure. This source preview still has [private build gaps](ssx3-build.md).

| Symptom | Check / next step | Source |
| --- | --- | --- |
| ISO/ELF checksum differs | Compare both supported hashes; stop before generation. Another region or revision is not supported by this contract. | [Input contract](ssx3-inputs.md) |
| `missing pinned input` or `missing GE1 bundle` | The library script needs private staging; installing CMake alone does not close this gap. It has no dry-run mode. | [build.sh](../ps2xRuntime/third_party/armsx2/build.sh#L55), [iOS](../ps2xRuntime/third_party/armsx2/build.sh#L124) |
| `om1rt-adapter needs -D...` | The older Mac build.sh consumes private OM1RT inputs; the public tools/ssx3 mac-libs command uses the JIT bridge without that stage. | [OM1 CMake](../ps2xRuntime/third_party/armsx2/om1/rt/CMakeLists.txt#L12) |
| Android duplicate `libhardware.so` | Remove only the duplicate from your own staged JNI directory; the HAL builds from source. | [Gradle guard](../android/app/build.gradle#L93) |
| `[gs:external] GE1 dlopen` failure | Verify `PS2X_GS_EXTERNAL_LIBRARY`, host ABI and dependent libraries against your local build manifest. | [loader](../ps2xRuntime/src/lib/gs/ps2_gs_external_backend.cpp#L142) |
| microVU `dlopen failed` / ABI mismatch | Verify `PS2X_MICROVU_LIB` and matching source/library stage. Default dynamic names are `libmv2_microvu.so` or `.dylib`; iOS uses a static offline core. | [microVU loader](../ps2xRuntime/src/lib/vu/ps2_microvu.cpp#L152) |
| GS resource/shader init failure | Check `GE1_GS_RESOURCES_DIR` exists and contains resources from the matching renderer build, including Apple metallibs where required. | [resource setup](../ps2xRuntime/third_party/armsx2/ge1/ge1_gs.cpp#L241), [shader staging](../ps2xRuntime/third_party/armsx2/ge1/CMakeLists.txt#L35) |
| Failed to load ELF / missing env layer | Check the compiled Android boot path, sibling `ps2x.env`/`full120.env`, and `PS2X_CD_IMAGE`; verify file permissions. | [startup](../ps2xRuntime/src/main.cpp#L303), [layer loader](../ps2xRuntime/src/lib/ps2_android_runtime.cpp#L78) |
| Android update signing error | Stop the app, verify a private card/settings backup, and rebuild with the original signing key. Do not uninstall or clear storage to bypass it. | [Android signing and saves](../android/README.md#protect-saves-and-upgrade-keys) |
| 120 selected but drawing at 60 | Confirm the process restarted and loaded the full120 layer. The 60-draw split mode already uses 120 physics. | [launch/env contract](../android/README.md#controls-and-60120) |

Slow simulation means guest updates per wall second are below the intended
rate, even if the panel presents smoothly. Do not report presents/s as guest
frames/s. For stock-rate windows compare guest vsyncs/s with 59.94; for
full-120 event windows compare with 119.88. State the mode, window and whether
logs/diagnostics were enabled; menus and pause are stock-rate windows.
The event-rate implementation is in
[full120 timing](../ps2xRuntime/src/lib/Kernel/EeScheduler.cpp#L510).

## Relevant validation

For an already configured Mac arm64 build, build the runtime suite target and
run it **from the fork checkout root** (some cases read source by relative path):

```sh
cmake --build build --target ps2x_tests
./build/ps2xTest/ps2x_tests
```

Use the actual binary path if your generator/output directory differs.
The target is defined in [ps2xTest/CMakeLists.txt](../ps2xTest/CMakeLists.txt#L148).
This is not a substitute for compatible game input generation, a determinism
check, or a save/reload and controls check. No build or test run is claimed by
these documentation changes. Research-wide Python unittest discovery is not
the product runtime gate. Portable Python-tool dependency inventory is pending;
do not infer a stdlib-only requirement for unrelated research tools.

## Bounded diagnostic receipt

Record source SHAs, OS/architecture, compiler/SDK/NDK versions, failing command
with paths replaced by placeholders, input hash match/mismatch, library/resource
hashes, and the first error plus up to 100 surrounding lines. Android env and
launch messages use `ps2x`; take a bounded local log snapshot:

```sh
adb logcat -d -t 200 -s ps2x raylib
```

See [Android log tag](../ps2xRuntime/src/lib/ps2_android_runtime.cpp#L22).
Native stdout/stderr may not appear in logcat; missing messages are not proof
of success. Redact personal paths, device IDs and credentials before sharing.
Exclude disc/ELF/BIOS bytes, extracted assets, cards, generated sources,
recordings and game captures. Keep full logs and backups local. This guide
does not redirect SSX 3 fork support requests to upstream projects.
