# Map — disc layouts and file formats the tools read

Two supported discs, exact bytes only. Bring your own discs; no game data,
art, BIOS or derived assets are published here or in any release.

- **SSX 3 (USA)** SLUS-20772 — see `docs/ssx3-inputs.md` for the exact
  identity bytes.
- **SSX Tricky (USA)** SLUS-20326 — see `docs/tricky-inputs.md` for the
  exact identity bytes (a filename change or another pressing is
  unsupported).

## 1. Container: ISO9660

- Both discs are single-extent ISO9660, 2048-byte sectors
  (`tools/ssx3/tricky/shared/inspect_disc.py`). The reader validates the PVD
  at sector 16, then walks the directory tree; it rejects
  extended/interleaved/multi-extent records.
- The game itself walks the same tree at boot and indexes paths by a
  backwards `h*33+c` hash (seed `0x1505`); max 500 files, 163 present
  (`docs/facts.md`). SSX 3 USA = 163 files + 18 dirs.
- Loose host files are invisible: new files need directory records, added by
  the runtime `PS2X_CD_OVERLAY` as pseudo-LBNs past the image
  (`docs/facts.md`). This is how the Tricky world overlay works.

## 2. Container: EA BIG archives

Header sniff in `big_members()` (`tools/ssx3/tricky/shared/inspect_disc.py`):

| Signature | Layout | Offset field |
| --- | --- | --- |
| `BIGF` / `BIG4` | `>II` count+dir-end at byte 8; 8-byte offsets, NUL-terminated names | big-endian 32-bit |
| `C0FB` | `>HH` dir-end+count at byte 2; 6-byte entries | big-endian 24-bit |

- Every tool that reads a BIG goes through this reader: world extraction and
  `build_tricky.big_member()`/`extract_member()`.
- **RefPack** (`10 FB` stream) compresses member payloads; the chain's
  encoder is `shared/refpack_encode.py` (deterministic) with
  `shared/refpack_optimal.py` (optimal parse). Packing funnels through
  `shared/relayout_stream.pack_group`.

## 3. SSX 3 USA ISO — paths the tools use

| Path | What | Read/write by |
| --- | --- | --- |
| `SLUS_207.72` | EE executable (codegen input) | `build.py prepare`, Recomp |
| `DATA/MODELS/BAM.BIG` | stock world archive / host BAM | `phase_worlds` |
| `DATA/AUDIO/MUSIC.BIG` | stock music BIG; member `bep.mus` = remus template | `phase_music` |
| `DATA/AUDIO/SPEECH.BIG` | stock speech; targets `data\speech\char\<t>.dat` | `phase_speech` |
| `DATA/CONFIG/MUSIC.INF` | music info; patched composite | `patch_music_inf.py` |
| `DATA/CONFIG/SLUSOVF.BIG` | BIGF of `overlay.dat` (relocatable EE code overlay) + `config.dat` | runtime (facts) |
| `DATA/UI/COURSPIC.BIG` | 17 × 256×256 8-bit SSH `<CODE>.ssh` | `run_chain.run_preview`; facts |
| `DATA/CONFIG/SLUSOVF.BIG`, `SCDAT.BIG` | overlay + script members | runtime |

## 4. SSX Tricky USA ISO — paths the tools use

Overlay aliases name **SSX3** paths (they patch SSX3's BIGs); the Tricky disc
supplies donor bytes (`tools/ssx3/tricky/README.md`). Alias targets:
`/DATA/AUDIO/{SPEECH,MUSIC,MUSIC2,ENGLISH}.BIG`, `/DATA/CONFIG/MUSIC.INF`.

| Path | What | Read by |
| --- | --- | --- |
| `DATA/MODELS/<C>.BIG` (`PIPE.BIG`, `GARI.BIG`, …) | per-course world archive (donor) | `run_chain.rung_extract` |
| `DATA/AUDIO/MUSIC.BIG` | members `{song}.mpf/.mus` (17 songs), `bep.mus` template source | `phase_music` |
| `DATA/AUDIO/SPEECH.BIG` | speech banks + `data\speech\mc\*.dat` donors | `phase_speech` |
| `DATA/AUDIO/JUKEBOX.BIG` | member `itstricky` = menu song | `phase_song` |
| `DATA/TEXTURES/HUDGAME.SSH` | HUD atlas source | `make_hud_atlas.py` |

There is no Tricky `ENGLISH.BIG` on disc derivable by tools; the English
base needs a merge input. `HUDGAME.SSH` is Tricky-only.

## 5. World-donor member formats

Extracting `DATA/MODELS/<C>.BIG` yields members the world adapter reads:

| Ext | Role | Consumer |
| --- | --- | --- |
| `.pbd` | patch/geometry data (terrain records) | world builder (`--pbd`) |
| `.ssf` | surface/scenery file | world adapter |
| `.map` | member map/dictionary for effect census | effects tool |
| `.aip` | AI path container (`.aip`/`.sop` share the container) | race-world builder, lap/start tool |
| `_L.ssh` | lightmap texture archive member | terrain builder |
| `.ssh` | SSH texture member | HUD atlas, menu assets |

Adapter outputs are `{d}-ps2.gsf` and `{d}-ps2.nbd`. Only role + consumer
are documented here, not byte-level record layouts.

## 6. Audio formats

| Codec / format | Where | Tool |
| --- | --- | --- |
| **EA-XA** | SSX 3 music (EE-decoded, 36 kHz stereo) | `song_eaxa.py`, `speech_eaxa.py` |
| **MicroTalk** | Tricky speech `.dat` donors | `vgmstream-cli` only |
| `.mpf`/`.mus` v3→v4 | Tricky music pairs | `music_remus.py` + `mpf3to4.py` |
| EA `SCHl` stream | stock `bep.mus` template slice | builder music phase |
| `MUSIC.INF` | music info text/binary | `patch_music_inf.py` |
| Composite `PS2XCMP1` | built BIGs (speech/music/music2/english) | speech/music composite builders |

## 7. Slim-ISO notes (SSX 3 only)

- Shipped slim image `SSX3.iso` (magic `PS2XSLM2`) drops whole pad regions +
  movie tails, keeping a 512-sector (1 MiB) prefix each — about 40 % smaller.
- Writer/reader in `tools/ssx3/build.py`: `SLIM_MAGIC`, sidecar `.holes` +
  ISO go to device storage, not the APK.
- **iOS still stages the plain ISO** via APFS clone with no slim path, so it
  carries the full size vs Android.
- The game reads by true ISO LBA (its own ISO9660 parse + LBA tables), so the
  same LBAs serve slim-image behavior.
- **Tricky is never shipped as an ISO**: no raw Tricky data ships anywhere;
  the device layer is converted outputs only.
