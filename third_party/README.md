# third_party/

Code written by others, kept under its own licence. Nothing here is
covered by the repository's MIT OR GPL-3.0-or-later default; each file's
header and `REUSE.toml` name its licence and holders.

| Directory | Origin | Licence |
| --- | --- | --- |
| `offline-core/` | Offline VU1 host core (dispatch, bridge, host stubs) for the iOS OM1 stage, derived from PCSX2/ARMSX2 microVU (see its `README.md`) | GPL-3.0-or-later |

Third-party files that a build needs at a fixed path stay where they are
and are declared in `REUSE.toml`: the Gradle wrapper
(`apps/android/gradlew`, `gradlew.bat`, `gradle/wrapper/gradle-wrapper.jar`;
Apache-2.0) and the PCSX2-derived SPU2 headers
(`games/ssx3/module/include/ps2_snd_spu.h`, `ps2_snd_spu_gauss.h`;
GPL-3.0-or-later), whose paths the engine's support-bundle contract names.
The engine submodule `deps/PS2Recomp` carries its own GPL-3.0 notices.
