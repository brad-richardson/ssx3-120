# Offline VU1 host machinery

These four handwritten source files are the runtime dispatch/bridge and
host stub from the OBE1 iOS stage. They contain host algorithms and refer to
external table declarations; no recorded guest programs or tables are supplied.
The algorithms derive from PCSX2/ARMSX2 microVU and retain GPL-3.0-or-later.

The `ios-om1` builder step copies these sources into the generated OM1 stage
alongside the manifest-driven `sp1`, `mid`, `full`, `menus`, `late`, `sv1`,
`grav`, `rb`, and `jx` assembly and table/header files (rebuilt from the
user's disc via `tools/ssx3/ios/om1-manifest.json`; bring your own disc, no
game data ships). The runtime consumes that flat stage through
`PS2X_OM1_STAGE` and `PS2X_OM1_LIB`. This directory is not compiled by the
Mac recipe, which uses the public JIT bridge instead.

The source matches the locally pinned OBE1 host core except for source-only
licence/status comments. No runtime defaults or private pinned files changed.
