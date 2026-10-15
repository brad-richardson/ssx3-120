# What the recomp gives over an emulated PS2 (running list)

Brad (10-14): "keep a running list of the things the recomp gives over an emulated ps2 experience." Each row states its **evidence status**: *shipped* (in a play build, with receipts), *in progress* (a lane exists), *planned*, or *unmeasured* (a claim we haven't proven). Update this list when a lane lands. Never upgrade a row's status without a receipt.

| # | Advantage | Status | Evidence / caveat |
|---|---|---|---|
| 1 | **True 120 Hz simulation:** physics, tricks and rider update at 120/s at normal speed. An emulator's 2× speedhack is fast-forward, not 120. | shipped (Odin PB63, iOS "Target 120") | `docs/status.md`; FIX groups; BEN1 `rr120` holds about 119.9 vs/s on the Odin |
| 2 | **Performance vs emulators** | **unmeasured; may not be an advantage** | NetherSX2 on the Odin ran a Snow Jam race uncapped at 1× native at about 4.2–4.4× real time (about 254 stock updates/s; ledger 09-18). We haven't measured our uncapped headroom on the same course and resolution; BEN1's uncapped mode was masked by the panel lock. **Needs a matched comparison** before claiming anything. |
| 3 | **Runs on iPhone/iPad.** Emulators that need JIT aren't practical on iOS; the recomp is native code. | shipped (IPB36/IPB37) | iPhone 16 Pro / iPad tested; Brad's min spec |
| 4 | **Remastered UI on device:** crisp HUD font and word art, icons/stars, menu art and signs, from the user's own disc, toggled at runtime | in progress (REM3 P10–P13) | fonts det-identical on the Mac; classes 3–5 Lanczos |
| 5 | **Upscaling** (native panel resolution on the Odin, 2× on iOS) with CAS sharpening | shipped | emulators also upscale; this is parity, not an edge. The edge is #4. |
| 6 | **Easy online:** one tap from the main menu to Quick Matchup, auto login with a stable generated name, our own lobby + relay (hides IPs, works behind strict NAT/CGNAT), no DNAS or network-config setup | in progress (ONL11 landed on the fork; ONL12 iOS crash fix; LOB2 hardening) | PCSX2 online needs a DNAS bypass, a network config and community servers |
| 7 | **Netplay at 120** (server-referee lockstep, up to 6 riders in race mode) | planned (SRVP1; NP0A/NP0B phase 0) | post-v0 |
| 8 | **Tricky courses inside SSX 3** (SSX 3 handling) | in progress, Brad's builds; off for the v0 public build | `docs/decisions.md` (Tricky mode) |
| 9 | **Quick boot and no shader stutter:** intro movies skipped, unpaced boot to the first input screen, TFX prewarm (no gondola/race-exit compile stalls), slim 1.8 GB image | shipped (TURBO_BOOT, PRW1 prewarm in PB63, ISO2 slim) | measure cold boot-to-menu seconds vs NetherSX2/PCSX2 |
| 10 | **Resume where you left off:** auto-snapshot on background/lock, silent resume under 10 min | in progress (ASV1: Mac done, Odin pending) | PCSX2 has manual save states only |
| 11 | **Widescreen by default** with the 4:3 HUD widened inside anamorphic | shipped | emulators need patches/cheats |
| 12 | **In-game settings panel** (Select+Start) and one-app install from your own disc (public builder) | shipped (panel); public builder in progress | MNT2 fixed the Android 15 install path |
| 13 | **Game-feel fixes at 120** (FIX groups: landing, cameras, flare, etc.) and targeted game-bug fixes | shipped | FAL1 showed pipe wipeouts are stock behaviour, not ours |

## To measure (owners TBD)

- #2: a matched uncapped speed comparison on the Odin (same course, resolution and modes) vs NetherSX2/ARMSX2, plus Mac vs PCSX2.
- #9: cold boot-to-menu and course-load seconds vs the emulators.
- Battery and thermals per race-minute vs NetherSX2 on the Odin.
