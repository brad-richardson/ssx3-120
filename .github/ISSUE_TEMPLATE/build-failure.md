---
name: Build failure
description: Report a failed local build (the builder prints this template's URL on failure)
labels: [build-failure]
---

<!-- Paste the builder's failure block here: the `BUILD FAILED at <step>; log: <path>` line plus the resume line it printed. -->

## Failing step

<!-- e.g. prepare, tricky, android-libs, android, ios-libs, ios, install -->

## Command

<!-- The exact command you ran, with disc paths replaced by their basenames, e.g. `./build android --ssx3-iso SSX3.iso --work ~/ssx3-work`. Never paste full local paths, device serials, or UDIDs. -->

```
...
```

## Failure block

<!-- Paste the full failure block the builder printed (it already redacts personal paths and serials). -->

```
...
```

## Support bundle

<!-- Attach the `support-bundle-*.txt` file the builder wrote next to your --work directory (personal paths and device serials are redacted). -->

## Host

<!-- macOS Apple silicon / Linux x86_64 / Linux arm64; CPU count; RAM; free disk under --work. The support bundle already records most of this. -->

## Discs

<!-- SSX 3 USA SLUS-20772: yes/no. Tricky USA SLUS-20326: yes/no/not used. Never upload disc images or derived game bytes. -->
