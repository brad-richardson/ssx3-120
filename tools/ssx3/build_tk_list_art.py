#!/usr/bin/env python3
"""tk-menu-2 overlay art: build the disc-only art atlas (no font, no REM3).

Reads (read-only): the user's own SSX 3 ISO (DATA/UI/FE_1.SSH, mountain +
chrome) and the user's own Tricky UI (TP*.SSH previews, from the Tricky
ISO DATA/UI/ or an extracted overlay dir). Writes (scratch only, never
git): the art-only atlas binary. Game art is never committed; the runtime
loads the staged file via PS2X_SSX3_TRICKY_LIST_ART (missing file = plain
shapes + Neuropol TTF, still no bitmap font).

Atlas: 8-byte magic ("TKLIST1\\0") + u32le w,h,nrects, then per rect:
char name[24] + u32le x,y,w,h, then w*h RGBA. Art only: moun + prevN.
All titles/rows/legends render at runtime via Neuropol-CC0 OTF (raylib
TTF), never baked.

Usage:
  build_tk_list_art.py --ssx3-iso SSX3.iso --tricky-ui DIR_OR_ISO \
      --codes codes.tsv --out out-dir

  --tricky-ui: either a directory holding TP*.SSH, or a Tricky ISO image
    (DATA/UI/TP*.SSH are read from it).
  --codes: TSV event=CODE lines (e.g. 0=ARA1), mapping SSX 3 event id to
    the TP file code. Megaplex (event 15) has no preview and is skipped.

No game data ships in this file: SHPS decode + ISO9660 read only.
"""

import argparse
import struct
import sys
from pathlib import Path

from PIL import Image


def iso_read(path, name):
    """Read a named file from a standard 2048-byte-sector ISO9660 image."""
    with path.open("rb") as iso:
        iso.seek(16 * 2048)
        pvd = iso.read(2048)
        if pvd[1:6] != b"CD001" or pvd[0] != 1:
            raise ValueError("Not an ISO9660 primary volume descriptor")

        def contents(record):
            sector = struct.unpack_from("<I", record, 2)[0]
            size = struct.unpack_from("<I", record, 10)[0]
            iso.seek(sector * 2048)
            return iso.read(size)

        directory = contents(pvd[156 : 156 + pvd[156]])
        parts = [p.upper() for p in name.split("/")]
        for wanted in parts:
            found, offset = None, 0
            while offset < len(directory):
                length = directory[offset]
                if length == 0:
                    offset = (offset // 2048 + 1) * 2048
                    continue
                record = directory[offset : offset + length]
                label = record[33 : 33 + record[32]].decode("ascii", "replace")
                if label.split(";")[0].upper() == wanted:
                    found = record
                    break
                offset += length
            if found is None:
                raise ValueError(f"{name} not found in image")
            directory = contents(found)
        return directory


def csm1(i):
    return (i & 0xE7) | ((i & 0x10) >> 1) | ((i & 0x08) << 1)


def a8(c):
    return bytes((c[0], c[1], c[2], min(255, c[3] * 2)))


def decode_ssh(data):
    d = data
    n = struct.unpack_from("<I", d, 8)[0]
    out = {}
    for k in range(n):
        name = d[16 + k * 8 : 20 + k * 8].decode("latin1").strip()
        o = struct.unpack_from("<I", d, 20 + k * 8)[0]
        t = d[o]
        nxt = int.from_bytes(d[o + 1 : o + 4], "little")
        w, h = struct.unpack_from("<HH", d, o + 4)
        px = o + 16
        if t == 5:
            img = Image.frombytes(
                "RGBA", (w, h),
                b"".join(a8(d[px + 4 * i : px + 4 * i + 4]) for i in range(w * h)),
            )
        elif t in (1, 2):
            pal = o + nxt
            pw, ph = struct.unpack_from("<HH", d, pal + 4)
            pc = pw * ph
            cols = [a8(d[pal + 16 + 4 * i : pal + 20 + 4 * i]) for i in range(pc)]
            if t == 2:
                idx = [csm1(i) if pc == 256 else i for i in d[px : px + w * h]]
            else:
                raw = d[px : px + (w * h + 1) // 2]
                idx = [v for b in raw for v in (b & 15, b >> 4)][: w * h]
            img = Image.frombytes(
                "RGBA", (w, h),
                b"".join(cols[i] if i < pc else b"\0\0\0\0" for i in idx),
            )
        else:
            continue
        out[name] = img
    return out


# Course order == Tricky order (ps2_ssx3_tricky_list.h kCourses).
# Events per course index: Garibaldi 0, Snowdream 1, Elysium 16,
# Mesablanca 2, Merqury 4, Aloha 14, Pipedream 11, Untracked 7,
# Megaplex 15 (no preview), Alaska 6.
EVENTS = [0, 1, 16, 2, 4, 14, 11, 7, 15, 6]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ssx3-iso", required=True)
    ap.add_argument("--tricky-ui", required=True)
    ap.add_argument("--codes", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    codes = {}
    for line in Path(a.codes).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            ev, code = line.split("=", 1)
            codes[int(ev)] = code.strip()

    # Mountain + chrome from the user's SSX 3 disc.
    fe1 = decode_ssh(iso_read(Path(a.ssx3_iso), "DATA/UI/FE_1.SSH"))
    if "moun" not in fe1:
        raise SystemExit("FE_1.SSH has no 'moun' image")
    sprites = {"moun": fe1["moun"].convert("RGBA")}

    # Previews from the user's Tricky UI (dir or ISO).
    tui = Path(a.tricky_ui)
    if tui.is_dir():
        def tp_bytes(code):
            return (tui / f"TP{code}.SSH").read_bytes()
    else:
        iso_data = tui
        def tp_bytes(code):
            return iso_read(iso_data, f"DATA/UI/TP{code}.SSH")

    for i, ev in enumerate(EVENTS):
        if i == 8 or ev not in codes:
            continue  # Megaplex: text card (no preview on disc)
        members = decode_ssh(tp_bytes(codes[ev]))
        if codes[ev] in members:
            sprites[f"prev{i}"] = members[codes[ev]].convert("RGBA").resize(
                (160, 160), Image.LANCZOS
            )

    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    AW, AH = 1024, 1024
    atlas = Image.new("RGBA", (AW, AH), (0, 0, 0, 0))
    rects = {}
    x, y, shelf_h = 0, 0, 0
    for name in sorted(sprites):
        img = sprites[name]
        if x + img.width > AW:
            x, y = 0, y + shelf_h + 4
            shelf_h = 0
        assert y + img.height <= AH, f"atlas overflow at {name}"
        atlas.alpha_composite(img, (x, y))
        rects[name] = [x, y, img.width, img.height]
        x += img.width + 4
        shelf_h = max(shelf_h, img.height)
    blob = atlas.tobytes()
    with open(outdir / "tklist-art.bin", "wb") as f:
        f.write(b"TKLIST1\0" + struct.pack("<III", AW, AH, len(rects)))
        for name in sorted(rects):
            nm = name.encode("ascii")[:23] + b"\0" * (24 - min(24, len(name)))
            rx, ry, rw, rh = rects[name]
            f.write(nm + struct.pack("<IIII", rx, ry, rw, rh))
        f.write(blob)
    print(f"baked {len(rects)} art sprites -> {outdir / 'tklist-art.bin'}")


main()
