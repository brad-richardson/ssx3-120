# Offline VU1 host machinery

These four handwritten source files are the runtime dispatch/bridge and
host stub from the OBE1 iOS stage. They contain host algorithms and refer to
external table declarations; no recorded guest programs or tables are supplied.
The algorithms derive from PCSX2/ARMSX2 microVU and retain GPL-3.0-or-later.

An iOS staging recipe can copy these sources into its own external OM1 stage
alongside **the user's locally generated** `sp1`, `mid`, `full`, `menus`,
`late`, `sv1`, `grav`, `rb`, and `jx` assembly and table/header files. The
runtime currently consumes that flat stage through `PS2X_OM1_STAGE` and
`PS2X_OM1_LIB`; exporting host source does not generate the recordings or
certify their coverage. The public portable recording/generation and archive
recipe is still incomplete. This directory is not compiled by the Mac recipe,
which uses the public JIT bridge instead.

The source matches the locally pinned OBE1 host core except for source-only
licence/status comments. No runtime defaults or private pinned files changed.
