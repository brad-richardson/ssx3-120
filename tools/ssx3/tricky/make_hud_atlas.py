#!/usr/bin/env python3
"""TK43c (lane copy of TK43a's tool): stage the Tricky meter sprite atlas
from the Tricky ISO, plus the lit-red letter row.

Reads (read-only): the Tricky ISO (HUDGAME.SSH member).
Writes (scratch only, never git): the raw RGBA atlas + a visual check PNG.
Game art is never committed; the runtime loads the staged file via
PS2X_SSX3_TRICKY_HUD_ART.

Atlas: 20-byte header ("TKHUD2\\0\\0" + u32le w,h,reserved) + 256*256 RGBA.
Sprite rects mirror ps2_ssx3_tricky_hud.h (ringRect/silverRingRect/poleRect/
letterRect/litLetterRect/jewel/pill/snowflake).

Usage: make_trickyhud_atlas.py <tricky-iso> <out-dir>
"""
import hashlib
import json
import os
import struct
import subprocess
import sys

SSH_MD5 = {
    "HUDGAME.SSH": "86d12a2dff17d7dccda5a57be84c0183",
}

# (name, member, x, y, w, h) in HUDGAME.SSH texture pixels.
# TK43a2: boxes re-derived on the correct decode (linear CLUT, see
# decode_member) as alpha connected components; each one template-matches
# Brad's recording at 1 texel = 1 buffer px (REPORT.md TK43a2).
SPRITES = [
    ("ring_red", "map4", 0, 208, 49, 12),
    ("ring_orange", "map4", 0, 220, 49, 12),
    ("ring_gold", "map4", 0, 232, 49, 12),
    ("ring_silver", "map4", 0, 244, 49, 12),  # real silver art (unlit coils)
    ("pole", "map4", 245, 219, 1, 31),  # cylinder shading profile, used across the pole
    ("lt_t", "map4", 50, 229, 35, 25),  # letters are pre-rotated for the arc
    ("lt_r", "map4", 89, 228, 19, 27),
    ("lt_i", "map4", 111, 223, 17, 31),
    ("lt_c", "map4", 90, 204, 19, 23),
    ("lt_k", "map4", 134, 220, 25, 35),
    ("lt_y", "map4", 166, 223, 30, 30),
    ("jewel", "map4", 201, 221, 35, 32),  # silver ball in a rim
    ("jewel_red", "map1", 137, 134, 35, 32),  # red ball (full state)
    ("pill", "map4", 208, 92, 22, 18),  # red S hexagon
    ("snowflake", "map1", 166, 179, 38, 32),  # first-full splash (flake at x179-203)
    # TK43c: lit-red letter set, same graffiti shapes (TK43a2 REPORT 8.2).
    ("ltR_t", "map1", 1, 187, 35, 24),
    ("ltR_r", "map1", 40, 185, 19, 27),
    ("ltR_i", "map1", 227, 187, 17, 31),
    ("ltR_c", "map1", 74, 188, 18, 23),
    ("ltR_k", "map1", 138, 176, 25, 34),
    ("ltR_y", "map1", 225, 224, 30, 30),
]

# (atlas-name, src-sprite, dst-x, dst-y, dst-w, dst-h, op)
# op: raw | transpose2 | desat | orangize
ATLAS = [
    ("ring_red", "ring_red", 0, 0, 49, 12, "raw"),
    ("ring_orange", "ring_orange", 49, 0, 49, 12, "raw"),
    ("ring_gold", "ring_gold", 98, 0, 49, 12, "raw"),
    ("ring_silver", "ring_silver", 147, 0, 49, 12, "raw"),
    ("pole", "pole", 200, 0, 31, 2, "transpose2"),
    ("lt_t", "lt_t", 0, 16, 35, 25, "raw"),
    ("lt_r", "lt_r", 36, 16, 19, 27, "raw"),
    ("lt_i", "lt_i", 56, 16, 17, 31, "raw"),
    ("lt_c", "lt_c", 74, 16, 19, 23, "raw"),
    ("lt_k", "lt_k", 94, 16, 25, 35, "raw"),
    ("lt_y", "lt_y", 120, 16, 30, 30, "raw"),
    ("jewel_grey", "jewel", 152, 16, 35, 32, "raw"),
    ("jewel_red", "jewel_red", 188, 16, 35, 32, "raw"),
    ("pill", "pill", 0, 56, 22, 18, "raw"),
    ("pill_grey", "pill", 24, 56, 22, 18, "desat"),
    ("snowflake", "snowflake", 48, 56, 38, 32, "orangize"),
    # TK43c: lit-red letters (mirror litLetterRect in the header).
    ("ltR_t", "ltR_t", 0, 96, 35, 24, "raw"),
    ("ltR_r", "ltR_r", 36, 96, 19, 27, "raw"),
    ("ltR_i", "ltR_i", 56, 96, 17, 31, "raw"),
    ("ltR_c", "ltR_c", 74, 96, 18, 23, "raw"),
    ("ltR_k", "ltR_k", 94, 96, 25, 34, "raw"),
    ("ltR_y", "ltR_y", 120, 96, 30, 30, "raw"),
]

AW, AH = 256, 256


def ssh_images(m):
    cnt = struct.unpack("<I", m[8:12])[0]
    res = []
    for j in range(cnt):
        nm = m[16 + 8 * j:20 + 8 * j].decode("latin1")
        o = struct.unpack("<I", m[20 + 8 * j:24 + 8 * j])[0]
        res.append((nm, o))
    return res


def decode(m, o):
    typ = m[o]
    nxt = int.from_bytes(m[o + 1:o + 4], "little")
    w, h = struct.unpack("<HH", m[o + 4:o + 8])
    pix = m[o + 16:o + 16 + w * h] if typ == 2 else None
    po = o + nxt
    pw, ph = struct.unpack("<HH", m[po + 4:po + 8])
    pal = m[po + 16:po + 16 + pw * ph * 4]
    return w, h, pix, pal


def decode_member(ssh, name):
    """8-bit indexed texel -> RGBA. TK43a2: HUDGAME.SSH palettes are stored
    linear (no CSM1 8-15/16-23 entry swap) and the texels unswizzled; the
    swap the TK8 lineage applied scrambled every sprite (decode_audit.jpg)."""
    for nm, o in ssh_images(ssh):
        if nm == name:
            w, h, pix, pal = decode(ssh, o)
            rgba = bytearray(w * h * 4)
            for i, b in enumerate(pix):
                q = pal[b * 4:b * 4 + 4]
                rgba[4 * i:4 * i + 4] = bytes((q[0], q[1], q[2], min(255, q[3] * 2)))
            return w, h, bytes(rgba)
    raise KeyError(name)


def crop(rgba, sw, x, y, w, h):
    out = bytearray(w * h * 4)
    for r in range(h):
        s = ((y + r) * sw + x) * 4
        out[r * w * 4:(r + 1) * w * 4] = rgba[s:s + w * 4]
    return bytes(out)


def transpose2(src, sw, sh):
    """1-wide vertical strip -> 2 identical rows (the pole is drawn across
    its width with this profile; 2 rows keep bilinear in-cell)."""
    assert sw == 1
    row = b"".join(src[r * 4:r * 4 + 4] for r in range(sh))
    return row + row


def desat(src):
    out = bytearray(len(src))
    for i in range(0, len(src), 4):
        l = (src[i] * 299 + src[i + 1] * 587 + src[i + 2] * 114) // 1000
        out[i:i + 4] = bytes((l, l, l, src[i + 3]))
    return bytes(out)


def orangize(src):
    """Map the white map1 flake to the recording's splash orange (TK43a Part
    3: tile-1 median (218,123,31)). Luminance-preserving so AA edges shade
    darker; alpha untouched."""
    out = bytearray(len(src))
    for i in range(0, len(src), 4):
        l = max(src[i], src[i + 1], src[i + 2])
        out[i:i + 4] = bytes((l * 232 // 255, l * 130 // 255, l * 36 // 255, src[i + 3]))
    return bytes(out)


def main():
    iso, outdir = sys.argv[1], sys.argv[2]
    os.makedirs(outdir, exist_ok=True)
    tmp = os.path.join(outdir, "ssh")
    os.makedirs(tmp, exist_ok=True)
    # Read-only extract of the one HUD file we use.
    subprocess.run(["bsdtar", "-xf", iso, "-C", tmp, "DATA/TEXTURES/HUDGAME.SSH"],
                   check=True)
    raw = open(os.path.join(tmp, "DATA/TEXTURES/HUDGAME.SSH"), "rb").read()
    md5 = hashlib.md5(raw).hexdigest()
    assert md5 == SSH_MD5["HUDGAME.SSH"], md5
    tex = {}
    for name in ("map1", "map4"):
        tex[name] = decode_member(raw, name)
    cut = {}
    for name, member, x, y, w, h in SPRITES:
        sw, sh, rgba = tex[member]
        cut[name] = (crop(rgba, sw, x, y, w, h), w, h)
    atlas = bytearray(AW * AH * 4)
    rects = {}
    for aname, sname, dx, dy, dw, dh, op in ATLAS:
        src, sw, sh = cut[sname]
        if op == "raw":
            assert (sw, sh) == (dw, dh)
            cell = src
        elif op == "transpose2":
            assert (sh, 2) == (dw, dh)
            cell = transpose2(src, sw, sh)
        elif op == "desat":
            assert (sw, sh) == (dw, dh)
            cell = desat(src)
        elif op == "orangize":
            assert (sw, sh) == (dw, dh)
            cell = orangize(src)
        else:
            raise AssertionError(op)
        for r in range(dh):
            d = ((dy + r) * AW + dx) * 4
            atlas[d:d + dw * 4] = cell[r * dw * 4:(r + 1) * dw * 4]
        rects[aname] = [dx, dy, dw, dh]
    blob = b"TKHUD2\x00\x00" + struct.pack("<III", AW, AH, 0) + bytes(atlas)
    apath = os.path.join(outdir, "trickyhud.bin")
    open(apath, "wb").write(blob)
    receipt = {
        "iso": iso,
        "ssh_md5": md5,
        "sprites": [list(s) for s in SPRITES],
        "atlas_rects": rects,
        "out": apath,
        "out_sha256": hashlib.sha256(blob).hexdigest(),
        "out_bytes": len(blob),
    }
    open(os.path.join(outdir, "trickyhud-receipt.json"), "w").write(json.dumps(receipt, indent=1))
    print("wrote %s (%d bytes, sha256 %s)" % (apath, len(blob), receipt["out_sha256"]))


if __name__ == "__main__":
    main()
