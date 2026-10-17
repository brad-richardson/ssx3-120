#!/opt/homebrew/bin/python3.14
"""tk-menu-2 staged FEFONT: bake the game's own menu font for the overlay.

Reads (read-only): the user's own SSX 3 ISO (DATA/FONTS/FEFONT.SFN glyph
metrics) and the user's own stock atlas-2 dump (the 512x256 RGBA the guest
uploads, hash name 3be92b30fd7ab98a-372720106ade32f4-00002254.png, as made
by GE1_TEX_DUMP). The 4x cells reuse the public raster-edge recipe
(remaster_font.build_atlas: the same pixels the stock menus show with
on-device upscale on); the 1x cells are the stock raster (upscale off).
Writes (scratch only, never git): tkfont.bin, baked into the same dir as
the ART atlas. No game pixel, glyph image or metric table is committed;
the runtime finds it beside PS2X_SSX3_TRICKY_LIST_ART (missing file =
Neuropol fallback).

TKFONT1 layout: magic "TKFONT1\\0" + u32le w1,h1,w4,h4,nglyphs, then per
glyph: u16 codepoint, u16 x,y,w,h (1x cell), i16 xoff,yoff, u16 advance
(16 bytes), then w1*h1 RGBA (stock 1x), then w4*h4 RGBA (raster-edge 4x).

Usage:
  build_tk_font.py --ssx3-iso SSX3.iso --stock-atlas2 <png-or-dumpdir> \\
      --out out-dir

Dependencies: numpy + Pillow (same as remaster_font.py).
"""

import argparse
import hashlib
import struct
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import remaster_font


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ssx3-iso", required=True)
    ap.add_argument("--stock-atlas2", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    stock_path = Path(a.stock_atlas2)
    if stock_path.is_dir():
        stock_path = stock_path / remaster_font.NAMES[2]
    stock = Image.open(stock_path).convert("RGBA")
    rows = remaster_font.parse_sfn(
        remaster_font.iso_read(Path(a.ssx3_iso), "DATA/FONTS/FEFONT.SFN")
    )
    stat = {"cells": 0, "regions": 0, "tiny_kept_stock": 0, "empty_kept_stock": 0}
    big = remaster_font.build_atlas(
        2, stock, [dict(r) for r in rows], remaster_font.R_SHARP_DEFAULT,
        remaster_font.R_THRESH_DEFAULT, "smoothstep", stat,
    )
    w1, h1 = stock.size
    w4, h4 = big.size
    assert (w4, h4) == (w1 * 4, h1 * 4), (stock.size, big.size)

    # The guest uploads mid-grey texels (128,128,128, alpha <= 128) but the
    # stock menus show near-white glyphs (sampled D t21250: selected-row face
    # pixels > 225): the game's text pipeline maps this grey ramp to white
    # on screen. Normalize here so the overlay matches stock's output: every
    # inked texel is exactly 128 grey, so no shape is lost; white face with
    # the coverage doubled (the game's max alpha 128 = full ink).
    def whiten(im):
        import numpy as np

        a = np.array(im.convert("RGBA"))
        ink = a[:, :, 3] > 0
        a[ink, 0] = 255
        a[ink, 1] = 255
        a[ink, 2] = 255
        a[ink, 3] = np.minimum(255, a[ink, 3].astype(np.uint16) * 2).astype(np.uint8)
        return Image.fromarray(a)

    stock = whiten(stock)
    big = whiten(big)

    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / "tkfont.bin"
    with open(dest, "wb") as f:
        f.write(b"TKFONT1\0" + struct.pack("<IIIII", w1, h1, w4, h4, len(rows)))
        for r in rows:
            f.write(struct.pack("<HHHHHhhH", r["codepoint"], r["x"], r["y"],
                                r["width"], r["height"], r["x_offset"],
                                r["y_offset"], r["advance"]))
        f.write(stock.tobytes())
        f.write(big.tobytes())
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    print(f"glyphs {len(rows)} {w1}x{h1} -> {w4}x{h4} stat={stat}")
    print(f"wrote {dest} sha256 {digest}")


main()
