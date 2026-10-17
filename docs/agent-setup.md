# Agent setup: building SSX 3 for Android or iOS

You are a coding agent helping the user build and install this game from
**their own disc**. Read this whole file first, then run exactly what it
says. Do not improvise toolchains, flags, or signing.

## Ground rules

- Bring your own disc: the user supplies their USA `SSX 3 (USA).iso`
  (`SLUS_207.72`). Never commit game data, ISOs, generated code, cards,
  keystores, or provisioning profiles. The work directory stays outside
  this checkout and private.
- The builder resumes: `prepare` and the `*-libs` steps are skipped when
  their outputs are current. Re-run the same command to continue.
- iOS: never uninstall the app (it wipes saves) — upgrade with a plain
  install, which is what `--install` does.
- If a step fails, read its error to the end and fix the named cause
  (missing tool, unaccepted licence, unknown device). Do not retry a
  failed Xcode/Gradle step with different flags hoping it passes.

## Android (host: macOS Apple silicon, Linux; target: Android arm64)

```sh
./build android --ssx3-iso ~/SSX3.iso --work ~/ssx3-work --accept-android-licenses
./build android --ssx3-iso ~/SSX3.iso --work ~/ssx3-work --accept-android-licenses --install
```

The JDK and Android SDK/NDK download automatically (pinned, SHA-checked)
unless `--no-auto-download` is passed — then set `JAVA_HOME`,
`ANDROID_HOME`, and the NDK yourself. Debug step by step with
`python3 build.py doctor --target android`, `prepare`, `android-libs`,
`android`. `--device <serial>` picks among several devices
(`adb devices` lists them).

## iOS (host: macOS Apple silicon with Xcode; target: iPhone/iPad)

Three user values are required: the Apple Development **team id**
(10 characters, Xcode > Settings > Accounts > Team), the connected
device, and the offline-VU1 microVU stage directory.

```sh
./build ios --dry-run --team <TEAM10> --om1-stage <OM1STAGE> --work ~/ssx3-work
./build ios --ssx3-iso ~/SSX3.iso --team <TEAM10> --om1-stage <OM1STAGE> --work ~/ssx3-work
./build ios --ssx3-iso ~/SSX3.iso --team <TEAM10> --om1-stage <OM1STAGE> --work ~/ssx3-work --install
```

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

After install, launch the app on the device and confirm it stays up:
`xcrun devicectl device process launch --device <udid>
com.ssx3recomp.<team-lower>`, then check the process is still listed a
minute later. Gameplay feel is the user's call — hand the device back.

## Checks before handing back

- `python3 tools/ssx3/tests/test_build_onecmd.py` and (for iOS)
  `python3 tools/ssx3/tests/test_ios.py` pass.
- The exact commands run, their SHAs/timings, and anything the user must
  decide (device choice, Tricky ISO, gameplay feel) go in the handoff.
