# THIRD_PARTY_NOTICES.md

The SoundTouch static link, the community Turnip binary, the RE function
map / hook addresses, and the whole-repo GPL-3.0 framing ship here with
each component's license noted. This file is that record: every
component with its license and source. Full license texts ship with
every support bundle (`notices/` assembled by `tools/ssx3/prebuilt.py`
from the paths below) and with source checkouts of the linked projects.
Nothing here replaces those texts.

Every build stages these notices into the APK assets (`third_party/`
under the generated notices dir, wired in as an assets source set):
source builds collect the repo LICENSE, the runtime third-party tree,
the GE1 vendor checkout and the dependency clones plus a `SOURCES.json`
with the exact pins (same collector as the prebuilt bundles, which stage
their fuller `notices/` instead); the Turnip notices join them whenever
Turnip ships. There is no About screen rendering them on-device yet, so
this file remains the human-readable record alongside each bundle's
`notices/` directory.

| Component | Source / pin | Licence |
| --- | --- | --- |
| SSX 3 lobby server (`infra/lobby-rust`, original code for this project) | `infra/lobby-rust/` | GPL-3.0-or-later (top-level `LICENSE`) |
| PS2Recomp upstream (`ran-j/PS2Recomp`) and the `ssx3` product fork | `deps/PS2Recomp @ 1ce278b8`, `LICENSE` | GPL-3.0 |
| ARMSX2 adapter/build carry (GS + microVU platform recipes; renderer/VU core stays in the vendor repo); PCSX2-derived GE1 code travels under the same carry | `ps2xRuntime/third_party/armsx2` (`README.md`); vendor `brad-richardson/armsx2 @ 4b75f706` | GPL-3.0-or-later adapters; GE1/PCSX2-derived parts GPL-3.0+; see vendor tree |
| Offline VU1 host core (four handwritten dispatch/bridge/stub sources rebuilt into the iOS OM1 stage) | `tools/ssx3/offline-core/` (`README.md`); derived from PCSX2/ARMSX2 microVU | GPL-3.0-or-later |
| SoundTouch (generic host-audio support), linked statically into the support libraries | `ps2xRuntime/third_party/soundtouch` (`README.at1.md:6–25`), `COPYING.TXT` | LGPL-2.1. Relink path: the full source build lives in this repo — the builder compiles `libps2x_soundtouch.a` from the vendored source by default (`tools/ssx3/prebuilt.py` archives it; `tools/ssx3/README.md` source-by-default), so anyone can rebuild and replace that archive and relink |
| raylib (UI/overlay rendering) 5.5, fetched at configure time | `https://github.com/raysan5/raylib.git` tag `5.5` via `FetchContent` (`ps2xRuntime/CMakeLists.txt`); EGL-alpha + iOS-SDL2-DPI patches in `ps2xRuntime/cmake/` | zlib |
| sse2neon (ARM SIMD translation header, ARM targets only), fetched at configure time | `https://github.com/DLTcollab/sse2neon.git` tag `v1.9.1` via `FetchContent` (`CMakeLists.txt`) | MIT |
| Dear ImGui + rlImGui (desktop debug UI only; never Android/iOS) | `https://github.com/ocornut/imgui.git` tag `v1.92.7-docking`, `https://github.com/raylib-extras/rlImGui.git` tag `Raylib_5_5` via `FetchContent` (`ps2xRuntime/CMakeLists.txt`) | MIT |
| Neuropol fallback font | engine `ps2xRuntime/resources/fonts/` (`LICENSE-Neuropol.txt`, `Neuropol-CC0.otf`) | CC0 1.0 Universal; see notice file |
| Studio fonts (upstream tool assets) | engine `ps2xStudio/external/Font/`: `Font_1.ttf` (Inter Regular, sidecar `Font_1-OFL.txt`), `Font_2.ttf` (DejaVu Sans Mono, licence embedded in the font) | SIL Open Font License 1.1 (Inter); Bitstream licence with public-domain DejaVu changes (DejaVu Sans Mono) |
| SCE symbol database (upstream scanner resource compiled into the analyzer) | engine `ps2xAnalyzer/include/ps2recomp/sce_symbol_database_data.h` (generated from sce-symbol-scanner symboldb resources; symbol/hash/version/relocation metadata, no program code) | GPL-3.0 (same engine tree) |
| Turnip (optional Vulkan driver binary) + its notices | Binary: `StevenMXZ/Adreno-Tools-Drivers` v37 zip (`tools/ssx3/turnip.json`, `deps.lock.json` SHAs); notices: own Mesa build `turnip-notices.zip` (same files) | Mesa MIT; notices per `turnip-notices.zip`. Both artefacts unattested third-party builds (see trust note in `tools/ssx3/README.md`); double-SHA pins are the integrity check |
| xxHash header (generic hashing) | `ps2xRuntime/include/runtime/third_party/xxhash.h` (header names it, line 6) | BSD 2-Clause |
| PCSX2 Gaussian interpolation table (travels with the SND/SPU module) | `games/ssx3/module/include/ps2_snd_spu_gauss.h:1–5` (SPDX `GPL-3.0+`) | GPL-3.0-or-later |
| FFmpeg 7.1.1 (iOS movie decode, minimal build) | `https://ffmpeg.org/releases/ffmpeg-7.1.1.tar.xz`, SHA-256 `733984395e0dbbe5c046abda2dc49a5544e7e0e1e2366bba849222ae9e3a03b1` (`tools/ssx3/build.py` `FFMPEG_TARBALL_SHA`; recipe `ps2xRuntime/cmake/iOS-FFmpeg-7.1.1.md`) | LGPL-2.1-or-later as configured (recipe documents the subset) |
| MoltenVK 1.4.2 (iOS Vulkan framework, staged binary, no source build) | `https://github.com/KhronosGroup/MoltenVK/releases/download/v1.4.2/MoltenVK-ios.tar`, archive SHA-256 `b5d947b1660e6e9fed40b9cd2387e160aaab9e80b775c0cef7e14059405178c1` (`tools/ssx3/moltenvk.json`) | Apache-2.0 (upstream LICENCE) |
| SDL3 (Android + iOS dependency source) | `https://github.com/libsdl-org/SDL` tag `release-3.2.26`, commit `badbf8da4ee72b3ef599c721ffc9899e8d7c8d90` (`tools/ssx3/build.py` `ANDROID_DEPS` / `IOS_DEPS`) | zlib |
| SDL2 iOS prebuilt (iOS runtime only) | Prebuilt SDL2 `release-2.32.10` tarball `https://github.com/libsdl-org/SDL/releases/download/release-2.32.10/SDL2-2.32.10.tar.gz`, SHA-256 `5f5993c530f084535c65a6879e9b26ad441169b3e25d789d83287040a9ca5165`, located via `SDL2_DIR` (`ps2xRuntime/CMakeLists.txt`) | zlib |
| Android vendor dependency sources (built from source by the vendor recipe; resolved commits recorded in each bundle's `SOURCES.json`) | Tags + commits in `tools/ssx3/build.py` `ANDROID_DEPS`: libpng v1.6.51, libjpeg-turbo 3.1.4.1, zstd v1.5.7, lz4 v1.10.0, libwebp v1.6.0, sdl3 release-3.2.26, freetype VER-2-14-1, plutovg v1.3.2, plutosvg v0.0.7, curl curl-8_11_1, libpcap libpcap-1.10.5, shaderc v2025.4 (plus glslang pinned inside the vendor script) | libpng licence; libjpeg-turbo (BSD-3-Clause / IJG / zlib); zstd (BSD-3-Clause or GPL-2.0); lz4 (BSD-2-Clause or GPL-2.0); libwebp (BSD-3-Clause); sdl3 (zlib); freetype (FreeType Licence or GPL-2.0); plutovg / plutosvg (MIT); curl licence (permissive); libpcap (BSD-3-Clause); shaderc (Apache-2.0); glslang (BSD-3-Clause). Licence texts ship in bundle `notices/` |
| iOS vendor dependency sources (same-generation pins as Android where they overlap; seated on commits) | `tools/ssx3/build.py` `IOS_DEPS` (libpng v1.6.51, libjpeg-turbo 3.1.90, zstd v1.5.7, lz4 v1.10.0, libwebp v1.6.0, freetype VER-2-14-1, sdl3 release-3.2.26) plus rapidyaml / plutovg / plutosvg from the vendor tree | Same licences as the Android row; rapidyaml (MIT) |
| vgmstream (Tricky speech decode build tool only; never shipped) | Local install only, gated by a minimum-version and capability probe (`tools/ssx3/tricky/tool_paths.py`): `ffmpeg` >= 6.0, `vgmstream-cli` r1900+; macOS `brew install vgmstream`, or build the CLI from `https://github.com/vgmstream/vgmstream.git` when your distro lacks it. Nothing is downloaded or pinned. | Permissive ISC-style (use/copy/modify/distribute with notice), per lock note |
| Inherited Vita modules (five `.suprx` files; outside the Android/iOS recipes) | `deps/PS2Recomp/ps2xRuntime/vita/module/` at the engine pin in `deps.lock.json` | Upstream provenance/licence inventory still needs an explicit record; the project GPL label alone is not a per-binary notice |
| GE1 prewarm selector blobs (Vulkan + Metal shader prewarm data; carried in the vendor tree) | engine `ps2xRuntime/third_party/armsx2/ge1/tfx-prewarm-vk.bin`, `tfx-prewarm-metal.bin` (with `tfx-prewarm-data.h`) at the engine pin in `deps.lock.json` | Upstream provenance/licence inventory still needs an explicit record; the project GPL label alone is not a per-binary notice |
| Bundled PGO profiles (indexed instrumentation profiles: function names, hashes and counters only; no game bytes) | `tools/ssx3/pgo/android/profile.profdata.gz`, `tools/ssx3/pgo/ios/profile.profdata.gz` (with `MANIFEST.json` trained fork/codegen SHAs) | GPL-3.0 (same tree); profile data holds no game bytes |
| Procedural iOS app icon (generated at build time, not hand art) | engine `ps2xRuntime/tools/make_ios_icon.swift` recipe; staged `ps2xRuntime/ios/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png` at the engine pin in `deps.lock.json` | GPL-3.0 (same engine tree) |
| Gradle wrapper scripts/JAR (product app build dependency); Android Gradle Plugin 8.6.1 via the plugin portal; Gradle 8.9 distribution | `apps/android/gradle/`, `apps/android/build.gradle` | per wrapper licence; JAR is binary build tooling. Google SDK/NDK packages (`platforms;android-34`, `build-tools;34.0.0`, `ndk;28.2.13676358`, `cmake;3.22.1`, `platform-tools`) are fetched during the build and require accepting Google's SDK licence (`--accept-android-licenses`) |
| plutovg / plutosvg (Mac library recipe sources) | fetched by `tools/ssx3/build.py` `mac-libs` (`v1.1.0` / `v0.0.7`) into the private work dir | MIT; notices assembled into bundles |
| Python build helpers | `uv`-provisioned `numpy` + `Pillow` (font-pack tools only; `tools/ssx3/build.py` header) | numpy BSD-3-Clause, Pillow HPND; stdlib otherwise |
| Temurin JDK 17 (auto-fetched toolchain) | Adoptium release API (`deps.lock.json` bootstrap pins, into `--work/toolchain/`) | GPL-2.0 with Classpath Exception |

Corresponding source for every downloaded binary the builder fetches or
ships is identified by exact URL + SHA in `deps.lock.json`,
`tools/ssx3/turnip.json`, `tools/ssx3/moltenvk.json` and each bundle's
`SOURCES.json`/`SHA256SUMS` — with one exception: the community Turnip
record provides binary hashes but no exact matching Mesa source commit,
so its pin is a binary-integrity check, not verified corresponding
source. rcheevos was removed from the engine tree and the product
bundle recipe, so it has no row here.
