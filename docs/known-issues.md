# Known issues (v0)

Tricky and Online are **Alpha**. This page lists the rough edges that are already known so bug reports can stay focused on what is new. If what you see is not listed here, please file an issue (see the templates).

## Tricky (Alpha)

- **Tokyo Megaplex has no gems yet.** The gem tables cover the other courses; the Megaplex gem pass is still pending, so its gems are skipped at build time.
- **SSX 3 DJ intros may play on Tricky courses.** The silent-ENGLISH recipe (`english-recipe-1`) is not landed yet, so without it the stock SSX 3 radio/artist intros can still be heard where the Tricky layer omits the replacement.
- **Tricky audio needs local tools.** Tricky music/speech build only when local `ffmpeg` + `vgmstream` installs are present (probed, never fetched). Without them the Tricky layer builds without the audio aliases and those courses play SSX 3's soundtrack.

## Online (Alpha)

- **Quick Play pairs two players; there is no bot fallback.** Matchmaking needs two real players in the queue on the same lobby server (default `ssx.bradr.dev`). To play offline, just don't pick Online in the menu; the in-game settings panel has no Online row.

## Build times

- **Full end-to-end build time range: TBD from the Air run.** The v0 end-to-end timing pass on a clean MacBook Air clone (Android, iOS, then `--tricky-iso`, with wall time and peak disk/RAM per step) has not run yet. Per-stage numbers from a Mac mini (M5 Pro, 4 jobs, empty caches) are in the README (prepare ~80 s, Android libraries ~600 s, APK link+package ~260 s resumed); your machine will differ and `build.py` prints per-stage timing.

## iOS

- **iOS needs a Mac + Xcode + an Apple ID.** iPhone/iPad builds run on macOS Apple silicon with Xcode (Metal toolchain) and need your 10-character Apple Development Team ID (`--team`); signing uses the Apple Development certificate from Xcode's automatic signing for that team. Upgrade a installed app with a plain install — never uninstall it (that wipes saves).
