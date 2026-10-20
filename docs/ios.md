# iOS build and play notes

Start with the one line in [README](../README.md) (`./build ios
--ssx3-iso ~/SSX3.iso --team <TEAM10>`), or hand the repo to an agent via
[agent-setup.md](agent-setup.md). This page holds the iOS-specific
quirks. Where the builder already prints the fix, that is noted instead of
repeated.

## Host setup

iOS (macOS Apple silicon host with Xcode and its Metal toolchain: if
`doctor` reports `unable to find utility "metallib"`, run
`xcodebuild -downloadComponent MetalToolchain` once; iPhone/iPad target)
needs your Apple Development team id; the offline-VU1 stage is generated
from your disc into `work/om1-stage` by the builder.

Install the host toolchain **before** cloning (a clean machine has no git
yet): macOS: Xcode command-line tools (provides git), then
`brew install cmake ninja llvm python pkg-config autoconf automake libtool bison`.
Then install [`uv`](https://docs.astral.sh/uv/) — the `./build` wrapper
runs the builder through it, provisioning Python plus the font-pack
libraries, so no system Python setup — and clone with
`git clone --recurse-submodules https://github.com/brad-richardson/ssx3-120.git ssx3-120`.

`./build doctor --target ios --work <dir>` re-checks the prerequisites and
prints the exact install line when something is missing. Run it before
anything heavy.

## Team id, certificates and signing

Find the 10-character id in Xcode > Settings > Accounts after signing in
with your Apple ID (free personal teams work but must re-sign weekly).
Before running the builder, confirm Xcode has an Apple Development
certificate and its private key for that team — create one in Xcode
first if needed; doctor requires it before the automatic-signing build
runs. Use Apple Development, not Distribution: a Distribution certificate
installs but refuses to launch. (Tool-handled: doctor requires the
certificate and private key before the automatic-signing build runs.)

```sh
./build ios --ssx3-iso ~/SSX3.iso --team <TEAM10>
```

The app id is `com.ssx3recomp.<team-lower>`, signed with the Apple
Development certificate from Xcode's automatic signing for that team.

## Install without wiping saves

Add `--install` to push to the connected device (plain install; saves
are kept — never uninstall the app): connect over USB, trust the
computer, enable Developer Mode (Settings > Privacy & Security), and
pass `--device <udid>` from `xcrun devicectl list devices` when more
than one device is attached. (Tool-handled: the install names the
missing piece — USB connection, computer trust, Developer Mode, or the
`--device` UDID — before touching the device; the install itself is a
plain install that never uninstalls, so saves survive.)

On iPhone/iPad, never uninstall the app — that wipes saves too. Upgrade
with a plain install. iOS needs a Mac + Xcode + an Apple ID. Upgrade an
installed app with a plain install — never uninstall it (that wipes
saves).

There is one app icon ("SSX 3"). The 60/120 Mode lives in settings and
sticks between launches (default 120); 60 is the plain stock game.
Rebuilding and reinstalling keeps your saves: `--install` installs over
the current app (plain install on iOS) and never removes cards or saves.
Keep the work directory outside this checkout and private: it holds your
disc image, generated game code, cards and signing keys.

## Display

The game renders at your screen's resolution or above. Capable devices
(iOS, or Android with 5+ GiB RAM) use panel-native rendering on a 1080p
panel, otherwise 2× with light sharpening; smaller devices use 1× with no
sharpening. The in-game Resolution setting overrides the default. Lower
resolutions exist for performance testing only. (Below-native rendering is
bench-only; CAS sharpening is approved, FXAA and ShadeBoost are out — see
[facts.md](facts.md).)

## Disk budgets

Keep **20 GB free** for iOS (iOS work dir 13 GB with several timestamped
build dirs). The builder checks this before downloading anything and fails
with the numbers when the work filesystem is too full. With
`--tricky-iso`, before anything heavy the builder requires **30 GB free**
on the work volume (4 GiB RAM minimum). RAM: 8+ GB recommended; explicit
`--jobs` always wins, and the builder logs the effective values. Low RAM
is a warning, a full disk is an error.
