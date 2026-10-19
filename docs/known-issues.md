# Known issues (v0)

Tricky and Online are **Alpha**. This page lists the rough edges that are already known so bug reports can stay focused on what is new. If what you see is not listed here, please file an issue (see the templates).

## Tricky (Alpha)

- **Tokyo Megaplex has no gems yet.** The gem tables cover the other courses; the Megaplex gem pass is still pending, so its gems are skipped at build time.
- **Tricky DJ intros depend on the audio choice.** With Tricky audio on (local `ffmpeg` + `vgmstream` present), the build silences the stock SSX 3 DJ intros on Tricky courses. With Tricky audio off, the layer builds without the audio aliases and the stock intros can still be heard there.
- **Tricky audio needs local tools.** Tricky music/speech build only when local `ffmpeg` (>= 6.0) + `vgmstream-cli` (r1900+) installs are present (probed, never fetched). Without them the Tricky layer builds without the audio aliases and those courses play SSX 3's soundtrack.
- **Tricky courses still show work-in-progress symptoms (mostly Elysium and Merqury):** oversaturated lighting, missing building collisions, rail gaps, animated assets standing still, see-through terrain patches, and the Mesablanca bridge vanishing at some angles.

## Online (Alpha)

- **Quick Play pairs two players; there is no bot fallback.** Matchmaking needs two real players in the queue on the same lobby server (default `ssx.bradr.dev`). To play offline, just don't pick Online in the menu; the in-game settings panel has no Online row.
- **Session resume is limited on default builds.** The build stages the lobby env, and lifecycle session save/resume refuse while networking is enabled — even if you never pick Online. Cold launches still show the Continue prompt, but there may be no session to continue.

## Build times

- **Full end-to-end build time range, measured on <machine>: TBD from the Air run.** The v0 end-to-end timing pass on a clean MacBook Air clone (Android, iOS, then `--tricky-iso`, with wall time and peak disk/RAM per step) has not run yet. Per-stage numbers from a Mac mini (M5 Pro, 4 jobs, empty caches) are in the README (prepare ~80 s, Android libraries ~600 s, APK link+package ~260 s resumed); your machine will differ and `build.py` prints per-stage timing.

## iOS

- **iOS needs a Mac + Xcode + an Apple ID.** iPhone/iPad builds run on macOS Apple silicon with Xcode (including its Metal toolchain) and need your 10-character Apple Development Team ID (`--team`); signing uses the Apple Development certificate from Xcode's automatic signing for that team (free personal teams must re-sign weekly). Upgrade an installed app with a plain install — never uninstall it (that wipes saves). For `--install`, connect over USB, trust the computer, enable Developer Mode (Settings > Privacy & Security), and pass `--device <udid>` from `xcrun devicectl list devices` when more than one device is attached.
