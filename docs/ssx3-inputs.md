# Supported SSX 3 inputs

New local preparation/build entry: [tools/ssx3](../tools/ssx3/README.md). Supported build host: macOS Apple silicon; play targets: Android arm64 / iOS. Windows/x86 is unsupported.

The supported identity is SSX 3 (USA), PS2 serial **SLUS-20772**, with the
extracted executable named `SLUS_207.72`. Support is for these exact bytes,
not every USA pressing or another region:

| Input | SHA-256 |
| --- | --- |
| Full ISO | `3c2f8eb182c9c6208a6e8172a41e61c98f420abe3f42c845f6829aeb9761ebf5` |
| Extracted `SLUS_207.72` ELF | `1b49d05ca2793922180851b9e1ce9ae2291d61a7863565ac4e71f12e967af7bc` |

These are the recorded supported-input checksums, not a runtime checksum
preflight. Dump your own disc locally, preserve the ISO unchanged, and use a
local ISO extraction tool to copy `SLUS_207.72` from that same dump. Verify both:

```sh
shasum -a 256 "SSX 3 (USA).iso" SLUS_207.72
# Linux alternative: sha256sum "SSX 3 (USA).iso" SLUS_207.72
```

If either differs, stop before generating or building. A filename change cannot
make another revision compatible. No disc, ELF, BIOS, extracted asset or pack
is supplied or linked here. A BIOS dump is not an input to the documented runner
startup: it loads the ELF and uses runtime/IOP services
([startup](../ps2xRuntime/src/main.cpp#L303), [IOP](../ps2xIOP/README.md)).

Keep the ISO, extracted files, EE/VU generated code and recordings in your own
external input/build directories. The runner reads the ISO through
`PS2X_CD_IMAGE` and uses the ELF path supplied at startup
([paths](../ps2xRuntime/src/main.cpp#L198),
[disc configuration](../ps2xRuntime/src/main.cpp#L309)). Android's boot path is
compiled through `ps2xBootElf`; see [Android setup](../android/README.md).

The [public preparation script](../tools/ssx3/README.md) pins the historical
EE generator and rewrites current TOML/map paths for your local output. It
checks the extracted ELF and generated registration identities. This does not
reproduce optimized VU images, iOS offline recordings or PGO profiles; those
remain local generation tasks. Running a newer generic analyzer/recompiler is
not a substitute for the pinned EE generator. Do not publish generated code,
recordings, game-bearing binaries or extracted/remastered packs as receipts.
