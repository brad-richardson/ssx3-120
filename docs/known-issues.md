# Known issues (v0)

Tricky and Online are **Alpha**. This page lists the rough edges that are already known so bug reports can stay focused on what is new. If what you see is not listed here, please file an issue (see the templates).

## Tricky (Alpha)

- **Tokyo Megaplex has no gems yet.** The gem tables cover the other courses; the Megaplex gem pass is still pending, so its gems are skipped at build time.
- **Tricky gem spin is off for now.** Collectible gems don't spin: wrong-object spins (trees, fences) still showed on device, so asset animation is disabled for v0 and returns afterwards.
- **Tricky course launch is still being polished.**
- **Tricky DJ intros depend on the audio choice.** With Tricky audio on (local `ffmpeg` + `vgmstream` present), the build silences the stock SSX 3 DJ intros on Tricky courses. With Tricky audio off, the layer builds without the audio aliases and the stock intros can still be heard there.
- **Tricky audio needs local tools.** Tricky music/speech build only when local `ffmpeg` (>= 6.0) + `vgmstream-cli` (r1900+) installs are present (probed, never fetched). Without them the Tricky layer builds without the audio aliases and those courses play SSX 3's soundtrack.
- **Tricky courses still show work-in-progress symptoms (mostly Elysium and Merqury):** oversaturated lighting, missing building collisions, rail gaps, animated assets standing still, see-through terrain patches, and the Mesablanca bridge vanishing at some angles.

## Online (Alpha)

- **Quick Play needs another player searching.** Matchmaking pairs two real players in the queue on the same lobby server (default `ssx.bradr.dev`); with nobody else searching, no match forms. There is no bot fallback. To play offline, just don't pick Online in the menu; the in-game settings panel has no Online row.
- **Session resume is for offline play.** Backgrounding requests a bounded session save; paired/racing online sessions are excluded. Networking being enabled by itself does not prevent an offline save. A kill before the save finishes, or a changed build/settings combination, may leave no usable recent session. Cold launches still show the Continue prompt, but there may be no session to continue.

## Build times

- **Build time and memory.** Reported Android timings on a MacBook Air (M4, 16 GB) are about 14 minutes, or about 1 h 10 min with Tricky. Smaller machines take longer (8 GB machines get conservative job counts automatically). Fully cached reruns require 5 GiB free; cold Android builds require 25 GiB and a Tricky conversion requires 30 GiB.

## iOS

- **iOS needs a Mac + Xcode + an Apple ID.** iPhone/iPad builds run on macOS Apple silicon with Xcode (including its Metal toolchain) and need your 10-character Apple Development Team ID (`--team`); signing uses the Apple Development certificate from Xcode's automatic signing for that team (free personal teams must re-sign weekly). Upgrade an installed app with a plain install — never uninstall it (that wipes saves). For `--install`, connect over USB, trust the computer, enable Developer Mode (Settings > Privacy & Security), and pass `--device <udid>` from `xcrun devicectl list devices` when more than one device is attached.
