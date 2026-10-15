# What the recomp gives over an emulated PS2 (running list)

Each row states its **evidence status**: *shipped* (in a play build, with
receipts), *in progress*, *planned*, or *unmeasured*. Never upgrade a row's
status without a receipt.

| # | Advantage | Status | Evidence / caveat |
|---|---|---|---|
| 1 | **True 120 Hz simulation:** physics, tricks and rider update at 120/s at normal speed. An emulator's 2× speedhack is fast-forward, not 120. | shipped | `FIX` groups (`docs/facts.md`); race holds ~119.9 guest vs/s on Adreno play builds |
| 2 | **Performance vs emulators** | **unmeasured; may not be an advantage** | An emulator ran a Snow Jam race uncapped at 1× native at about 4.2–4.4× real time. No matched uncapped comparison on the same course and resolution yet. **Needs a matched comparison** before claiming anything. |
| 3 | **Runs on iPhone/iPad.** Emulators that need JIT aren't practical on iOS; the recomp is native code. | shipped | tested on iPhone/iPad play builds |
| 4 | **Remastered UI on device:** crisp HUD font and word art from the user's own disc, toggled at runtime | in progress | fonts det-identical on the Mac host |
| 5 | **Upscaling** (native panel resolution on Adreno, 2× on iOS) with CAS sharpening | shipped | emulators also upscale; this is parity, not an edge. The edge is #4. |
| 6 | **Easy online:** one tap from the main menu to Quick Matchup, our own lobby + relay (hides IPs, works behind strict NAT/CGNAT), no network-config setup | in progress | lobby server in its own repo; `infra/` here is the operator side |
| 7 | **Netplay at 120** (server-referee lockstep) | planned | post-v0 |
| 8 | **Tricky courses inside SSX 3** (SSX 3 handling) | in progress, off for the v0 public build | course picker and overlay default off |
| 9 | **Quick boot and no shader stutter:** intro movies skipped, unpaced boot to the first input screen, TFX prewarm, slim ~1.8 GB image | shipped | measure cold boot-to-menu seconds vs emulators |
| 10 | **Resume where you left off:** auto-snapshot on background/lock, silent resume under 10 min | in progress | emulators have manual save states only |
| 11 | **Widescreen by default** with the 4:3 HUD widened inside anamorphic | shipped | emulators need patches/cheats |
| 12 | **In-game settings panel** (Select+Start) and one-app install from your own disc (public builder) | shipped (panel); builder shipped here | see `README.md` |
| 13 | **Game-feel fixes at 120** (FIX groups: landing, cameras, flare, etc.) and targeted game-bug fixes | shipped | pipe wipeouts verified as stock behaviour, not a regression |

## To measure

- #2: a matched uncapped speed comparison (same course, resolution and modes) vs emulators, plus Mac host vs PCSX2.
- #9: cold boot-to-menu and course-load seconds vs the emulators.
- Battery and thermals per race-minute vs emulators on the same device.
