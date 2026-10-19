# Supported Tricky inputs

New local entry: `build.py tricky --iso <Tricky.iso>` (or the one-command
`./build android --ssx3-iso <SSX3.iso> --tricky-iso <Tricky.iso>`), which
validates the disc, generates the optional Tricky layer under your private
`--work` directory, and records `tricky_inputs.json` beside `inputs.json`.
`stage`/`install` pick the layer up when present and re-verify it before
pushing; with no `--tricky-iso` the app boots stock SSX 3 only. Details in
[tools/ssx3](../tools/ssx3/README.md).

The supported identity is SSX Tricky (USA), PS2 serial **SLUS-20326**, with
the extracted executable named `SLUS_203.26`. Support is for these exact
bytes, not every USA pressing or another region:

| Input | SHA-256 |
| --- | --- |
| Full ISO (2,902,425,600 bytes) | `deae53c10447e59c2c4cd1d3a904a1ed04cbc818444f4883962a0060da837970` |
| ISO MD5 (second key) | `7d8ee567145bdf0c236ce5f5c679108a` |

The builder checks, in order: the full-ISO SHA-256 against the key table,
the exact byte size, the `SLUS_203.26` name in the disc root, and the
internal markers `DATA/MODELS/GARI.BIG` and `DATA/AUDIO/MUSIC.BIG`. The
music converter checks the v3 `.mpf` members itself during the build. Dump
your own disc locally, preserve the ISO unchanged, and verify it:

```sh
shasum -a 256 "SSX Tricky (USA).iso"
# Linux alternative: sha256sum "SSX Tricky (USA).iso"
stat -f %z "SSX Tricky (USA).iso"   # macOS: expect 2902425600
stat -c %s "SSX Tricky (USA).iso"   # Linux: expect 2902425600
```

If the hash or size differs, stop before generating or building. A filename
change cannot make another revision compatible.

Other pressings are not supported yet: the NTSC-U Greatest Hits
(`SLUS-20326GH`), PAL (`SLES-50545`) and JP (`SLPS-25078`) dumps have never
been measured here, so no keys exist for them. A key is added by measuring a
local dump (hashes only, never uploads) once one is available; the converter
formats are expected identical, so no converter work rides along.

No disc, ELF, BIG, composite, TSV, WAV or pack is supplied or linked here.
Keep the ISO, the generated Tricky layer and recordings in your own
external input/build directories. Do not publish game-bearing binaries or
extracted/remastered packs as receipts.

## Capacity, reruns and audio choices

- **Free space:** the Tricky step requires **30 GB free** on the work
  volume (plus 4 GiB RAM minimum; lower `--jobs` under 8 GiB RAM). The
  check runs before any work directory is created and fails closed with
  the numbers. This is separate from the stock budgets (25 GB Android /
  20 GB iOS / 12 GB prepare cold builds).
- **Reruns:** a rerun reuses the layer when the Tricky disc, converter,
  audio mode and toolchain are unchanged. When they differ, the step
  refuses the existing `tricky/` directory instead of rebuilding over
  it — remove that directory or rerun with a fresh `--work` directory.
  Enabling audio tools after an audio-off build therefore rebuilds; it
  does not patch the old layer.
- **Audio:** `--tricky-audio on|off|auto` (default `auto`). `on` fails
  closed without local tools; `auto` builds the music/speech phases when
  local `ffmpeg` (>= 6.0) and `vgmstream-cli` (r1900+) probes pass and
  otherwise prints one line and falls back to SSX 3 audio on Tricky
  courses. Nothing is downloaded: on Linux install `ffmpeg` by apt and
  build `vgmstream-cli` from https://github.com/vgmstream/vgmstream when
  your distro does not ship it.
- **Keys:** Android signs every build with the per-user
  `ssx3-signing.keystore` in your work directory (`version-code.txt`
  beside it). Back both up before rebuilding anywhere else: a different
  key forces an uninstall, which wipes saves — including Tricky
  progress.
