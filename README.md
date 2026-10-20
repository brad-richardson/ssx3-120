# ssx3-120

Fan project, not affiliated with or endorsed by Electronic Arts. Bring your
own disc; no game data is distributed.

Upscaled, 120 fps, Tricky-capable SSX 3 with rudimentary online — built from
**your own disc**. A few commands from this repo give an installable Android
APK; Mac hosts also get a local runner for testing. No game data, art,
BIOS, or derived assets are published here or in any release: bring your own
USA disc (`SLUS_207.72`).

Gameplay is **120-only**: physics, tricks and rider update at 120/s at
normal wall-clock speed. The 60 option is plain stock 60.

> **Fastest way: let a coding agent do it.** Send the repo link and paste this:
>
> ```text
> Here's https://github.com/brad-richardson/ssx3-120 — first check it doesn't do anything malicious, then follow docs/agent-setup.md to build and install SSX 3 from my disc at <path-to-your-SSX3.iso>.
> ```
>
> The agent guide ([docs/agent-setup.md](docs/agent-setup.md)) covers the
> host toolchain, the engine submodule, doctor, and build + install.

## Build from your own disc

```sh
./build android --ssx3-iso ~/SSX3.iso --accept-android-licenses
./build ios --ssx3-iso ~/SSX3.iso --team <TEAM10>
```

Add `--tricky-iso <Tricky.iso>` to validate your Tricky disc and build the
Tricky layer from it with the in-tree converter driver. The Tricky build
takes longer because every course is converted into SSX 3's engine. Without
it the app boots stock SSX 3 only. Platform setup lives in [docs/android.md](docs/android.md)
and [docs/ios.md](docs/ios.md); the builder prints the exact install line
when something is missing (`./build doctor --target android --work <dir>`).

## What you need

- Your USA SSX 3 disc as an ISO (`SLUS_207.72`); your USA SSX Tricky disc too for the Tricky courses.
- A build host: macOS Apple silicon, or Linux x86_64/arm64 (Windows users: Ubuntu in WSL2).
- A play target: an Android arm64 phone, or an iPhone/iPad (iOS builds need a Mac with Xcode).
- Disk: keep **25 GB free** for an Android cold build, **20 GB free** for iOS, 30 GB free with `--tricky-iso` (full Android SDK 3.4 GB; the builder checks before downloading anything).
- Host tools + [`uv`](https://docs.astral.sh/uv/): see [docs/android.md](docs/android.md) or [docs/ios.md](docs/ios.md) for the install lines, then clone with `git clone --recurse-submodules https://github.com/brad-richardson/ssx3-120.git ssx3-120`.

## Controls

Use a controller if you can: pair it in your phone's settings before launching.
If no controller is connected you get touch controls instead — they appear on their own and hide while a controller is connected (Touch=On in settings forces them on).
Buttons follow the PlayStation layout: face buttons are Cross/Circle/Square/Triangle, shoulders are L1/R1/L2/R2, and Menu/View act as Start/Select.

## Tricky (Alpha)

Build it with `--tricky-iso <Tricky.iso>` (supported discs: [docs/tricky-inputs.md](docs/tricky-inputs.md)); without it the app boots stock SSX 3 only.
Tricky courses run inside SSX 3's engine, so SSX 3 handling, tricks and scoring apply on Tricky blocks and gems.
See [docs/known-issues.md](docs/known-issues.md) for what is still rough.

## Online Quick Play (Alpha)

Quick Matchup connects to a lobby server you (or a friend) operate — match + UDP relay, no direct peer IPs. The default server is `ssx.bradr.dev` (single definition: `presets/online.env`).
Quick Play needs another player searching at the same time — there is no bot fallback. To play offline, just don't pick Online in the menu.

## Known issues

Tricky and Online are Alpha and still rough around the edges. Before filing,
check [docs/known-issues.md](docs/known-issues.md) for current Alpha rough edges.

## Contributing

Contributing / using a coding agent: see [AGENTS.md](AGENTS.md).

## Legal

You supply the discs: USA SSX 3 (`SLUS_207.72`), plus USA SSX Tricky for
the Tricky courses. No game data, art, BIOS, derived assets, or
game-bearing binaries are published here or in any release. This is a fan
project, not affiliated with or endorsed by Electronic Arts. GPL-3.0
(`LICENSE`). `SOURCE_PROVENANCE.md` + `SOURCE_MANIFEST.tsv` record every
file's pinned source and bytes; `THIRD_PARTY_NOTICES.md` names every
vendored component.
