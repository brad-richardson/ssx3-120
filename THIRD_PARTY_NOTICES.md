# THIRD_PARTY_NOTICES.md

This product composes the pinned engine (`deps/PS2Recomp @ 35cfdcdd`) with
its declared dependencies. Full license texts and per-file notices ship
with every support bundle (`notices/` assembled by `tools/ssx3/prebuilt.py`
from the paths below) and with source checkouts of the linked projects.
Nothing here replaces those texts.

| Component | Engine path / upstream | Licence |
| --- | --- | --- |
| PS2Recomp upstream (`ran-j/PS2Recomp`) and the `ssx3` product fork | `deps/PS2Recomp`, `LICENSE` | GPL-3.0 |
| ARMSX2 adapter/build carry (GS + microVU platform recipes; renderer/VU core stays in the vendor repo) | `ps2xRuntime/third_party/armsx2` (+ `README.md:5–12,33–44`); vendor `brad-richardson/armsx2 @ afad5a04` | GPL-3.0-or-later adapters; see vendor tree |
| rcheevos (vendored source + local compatibility code + build recipe; SSX achievement gating is product policy) | `ps2xRuntime/third_party/rcheevos` | per vendored `COPYING`/headers |
| SoundTouch (generic host-audio support) | `ps2xRuntime/third_party/soundtouch` (`README.at1.md:6–25`) | per `COPYING.TXT` |
| xxHash header (generic hashing) | `ps2xRuntime/include/runtime/third_party/xxhash.h` | per header |
| PCSX2 Gaussian interpolation table (travels with the SND/SPU module) | `ps2xRuntime/include/ps2_snd_spu_gauss.h:1–5` | attributed in header; third-party code |
| Studio font notice (upstream tool asset) | engine tool assets | per notice file |
| Gradle wrapper scripts/JAR (product app build dependency) | `apps/android/gradle/` | per wrapper licence; JAR is binary build tooling |
| plutovg `v1.1.0` / plutosvg `v0.0.7` (Mac library recipe sources) | fetched by `tools/ssx3/build.py` `mac-libs` into the private work dir | per upstream licences; notices assembled into bundles |
| Turnip `turnip-ssx3-ad094bb9` (optional Vulkan driver binary + notices) | `tools/ssx3/turnip.json` (hashes in `deps.lock.json`) | per `turnip-notices.zip` |

Corresponding source for every binary the builder downloads or ships is
identified by exact URL + SHA in `deps.lock.json`, `tools/ssx3/turnip.json`
and each bundle's `SOURCES.json`/`SHA256SUMS`.
