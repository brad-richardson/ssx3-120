# THIRD_PARTY_NOTICES.md

Per Brad 10-17 (`docs/decisions.md` → Standing), the SoundTouch static
link, the community Turnip binary, the RE function map / hook addresses,
and the whole-repo GPL-3.0 framing are approved **as long as each
component's license is noted**. This file is that record: every
component with its license and source. Full license texts ship with
every support bundle (`notices/` assembled by `tools/ssx3/prebuilt.py`
from the paths below) and with source checkouts of the linked projects.
Nothing here replaces those texts.

No about/licenses asset ships inside the APK today: `apps/android` has
no `assets/` directory and no About screen in its Java sources, so there
is nowhere to mirror these notices on-device. The shipping records are
this file (source tree) and each bundle's `notices/` directory.

| Component | Source / pin | Licence |
| --- | --- | --- |
| PS2Recomp upstream (`ran-j/PS2Recomp`) and the `ssx3` product fork | `deps/PS2Recomp @ 5235b114`, `LICENSE` | GPL-3.0 |
| ARMSX2 adapter/build carry (GS + microVU platform recipes; renderer/VU core stays in the vendor repo); PCSX2-derived GE1 code travels under the same carry | `ps2xRuntime/third_party/armsx2` (+ `README.md:5–12,33–44`); vendor `brad-richardson/armsx2 @ afad5a04` | GPL-3.0-or-later adapters; GE1/PCSX2-derived parts GPL-3.0+; see vendor tree |
| SoundTouch (generic host-audio support), linked statically into the support libraries | `ps2xRuntime/third_party/soundtouch` (`README.at1.md:6–25`), `COPYING.TXT` | LGPL-2.1. Relink path: the full source build lives in this repo — the builder compiles `libps2x_soundtouch.a` from the vendored source by default (`tools/ssx3/prebuilt.py` archives it; `tools/ssx3/README.md` source-by-default), so anyone can rebuild and replace that archive and relink |
| rcheevos (vendored source + local compatibility code + build recipe; SSX achievement gating is product policy) | `ps2xRuntime/third_party/rcheevos`, vendored `LICENSE` | MIT |
| raylib (UI/overlay rendering) 5.5, fetched at configure time | `https://github.com/raysan5/raylib.git` tag `5.5` via `FetchContent` (`ps2xRuntime/CMakeLists.txt`); EGL-alpha + iOS-SDL2-DPI patches in `ps2xRuntime/cmake/` | zlib |
| Neuropol fallback font | engine `ps2xRuntime/resources/fonts/` (`LICENSE-Neuropol.txt`, `Neuropol-CC0.otf`) | CC0 1.0 Universal; see notice file |
| Turnip (optional Vulkan driver binary) + its notices | Binary: `StevenMXZ/Adreno-Tools-Drivers` v37 zip (`tools/ssx3/turnip.json`, hashes in `deps.lock.json`); notices: own Mesa build `turnip-notices.zip` (same files) | Mesa MIT; notices per `turnip-notices.zip`. Both artefacts unattested third-party builds (see trust note in `tools/ssx3/README.md`); double-SHA pins are the integrity check |
| xxHash header (generic hashing) | `ps2xRuntime/include/runtime/third_party/xxhash.h` | per header |
| PCSX2 Gaussian interpolation table (travels with the SND/SPU module) | `ps2xRuntime/include/ps2_snd_spu_gauss.h:1–5` | attributed in header; third-party code |
| Studio font notice (upstream tool asset) | engine tool assets | per notice file |
| Gradle wrapper scripts/JAR (product app build dependency); Android Gradle Plugin 8.6.1 via the plugin portal; Gradle 8.9 distribution | `apps/android/gradle/`, `apps/android/build.gradle` | per wrapper licence; JAR is binary build tooling. Google SDK/NDK packages (`platforms;android-34`, `build-tools;34.0.0`, `ndk;28.2.13676358`, `cmake;3.22.1`, `platform-tools`) are fetched during the build and require accepting Google's SDK licence (`--accept-android-licenses`) |
| plutovg / plutosvg (Mac library recipe sources) | fetched by `tools/ssx3/build.py` `mac-libs` (`v1.1.0` / `v0.0.7`) into the private work dir | per upstream licences; notices assembled into bundles |
| Python build helpers | `uv`-provisioned `numpy` + `Pillow` (font-pack tools only; `tools/ssx3/build.py` header) | numpy BSD-3-Clause, Pillow HPND; stdlib otherwise |
| Temurin JDK 17 (auto-fetched toolchain) | Adoptium release API (`deps.lock.json` bootstrap pins, into `--work/toolchain/`) | GPL-2.0 with Classpath Exception |
| SDL2 iOS prebuilt (iOS runtime only) | Prebuilt SDL2 `release-2.32.10` for iphonesimulator, located via `SDL2_DIR` (`ps2xRuntime/CMakeLists.txt`) | zlib |

Corresponding source for every binary the builder downloads or ships is
identified by exact URL + SHA in `deps.lock.json`, `tools/ssx3/turnip.json`
and each bundle's `SOURCES.json`/`SHA256SUMS`.
