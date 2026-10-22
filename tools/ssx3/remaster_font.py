# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""Remaster the SSX3 font atlases with the raster-edge recipe (lane REM3).

Build-time step. It runs on the user's own USA disc and their own
texture dump and writes four replacement PNGs carrying the game's own
texture-hash filenames:

    python3 tools/ssx3/remaster_font.py --iso SSX3.iso \\
        --stock-dump stock-dump/ --out font-pack/

Source statement: stock atlas *pixels* come from the user's own texture
dump (--stock-dump, the four hash-named PNGs dumped from their disc at
runtime); glyph *metrics* for atlases 2/72 come from the SFN tables in
the user's own ISO image (--iso). Region boxes and fill predicates for
the baked menu sprites (70/80) are measurements, not art. No game data,
glyph images, outlines or atlases ship in this file.

Part-7 ISO survey (lane REM3): the ISO does not carry dump-equal atlas
pixels anywhere byte-contiguous. FEFONT/HUDFONT.SFN hold the glyph
metric table plus kerning/toolchain tables only (head decodes as
codepoint/adjust kerning pairs, then a "Buy ERTS" tag; value histogram
and packed-bit searches rule out 1bpp/AA rasters, and no 64-byte run
from any stock atlas appears anywhere in the 3 GB image). The baked
word sprites (70/80) match no texture in any UI/TEXTURES/FONTS pack
(28 256x256 SHPS decoded; exact, alpha, mask and superset probes all
miss) and no byte run on the disc. All four atlases therefore need the
user's own dump; the ISO-alone path stays a clean skip, never a guess.

Recipe (defaults reproduce the shipped pack): stock atlas RGBA is
upscaled to pack resolution (RGB bilinear 4x, alpha box 4x) and only
the ink-cell alpha is replaced — EDGE x8 upsample, smoothstep tone map
(r_sharp = edge width in pack output px), box downsample to 4x, scaled
to the stock 0..128 alpha range and clipped to the stock mask. RGB is
untouched everywhere, so the A/B between packs is pure alpha.

Dependencies: Python 3.9+ standard library, numpy, Pillow. Peak RSS is
a few hundred MB (one cell at a time, gc between cells).

Adapted from lanes REM2 (SFN/WORDS pattern: iso_read, parse_sfn,
NAMES, WORDS, fill_mask, runs) and REM3 (raster-edge math:
upsample_edge, raster_ship_alpha, clip-and-paste driver).
"""

import argparse
import gc
import hashlib
import math
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

R_SHARP_DEFAULT = 1.0
R_THRESH_DEFAULT = 64
ALPHA_MAX = 128

NAMES = {2: '3be92b30fd7ab98a-372720106ade32f4-00002254.png',
         72: 'b7e032dcd63ad8db-372720106ade32f4-00001e14.png',
         70: '51446d7052987c01-e5708d0189ccd302-0000221b.png',
         80: 'fb36e14323437580-2f302f4bc33356f5-0000221b.png'}
SFN_FILES = {2: 'DATA/FONTS/FEFONT.SFN', 72: 'DATA/FONTS/HUDFONT.SFN'}
# Baked menu-sprite words: (label, region, fill-class). Regions are
# measured atlas coordinates, not art. Values match the lane builder
# (REM2 fonts.py, rem3 branch) so public output equals the shipped pack.
WORDS = {
    70: [('FINISH!', (0, 0, 133, 26), 'orange70'),
         ('CHECKPOINT', (0, 29, 212, 55), 'green70'),
         ('TIMEBONUS', (0, 56, 199, 81), 'green70'),
         ('12345', (0, 84, 220, 125), 'white'),
         ('6', (210, 176, 256, 215), 'white')],
    80: [('GO!', (135, 0, 256, 51), 'green80'),
         ('OFF', (160, 168, 225, 192), 'orange80'),
         ('LIMITS', (145, 190, 245, 215), 'orange80')],
}


def fill_mask(a, kind):
    r, g, b, alpha = (a[:, :, n].astype(np.int16) for n in range(4))
    choices = {
        'orange70': (r > 200) & (g >= 40) & (g < 120) & (b < 60),
        'green70': (r > 140) & (g > 180) & (b < 40),
        'white': (r > 240) & (g > 240) & (b > 240),
        'green80': (r > 90) & (g > 200) & (b < 20),
        'orange80': (r > 180) & (g > 80) & (g < 150) & (b < 40),
    }
    return choices[kind] & (alpha >= 64)


def runs(values):
    """Contiguous True runs of a 1D boolean array as (start, end)."""
    spans = []
    start = None
    for x, active in enumerate(list(values) + [False]):
        if active and start is None:
            start = x
        elif not active and start is not None:
            spans.append((start, x))
            start = None
    return spans


def iso_read(path, name):
    """Read a named file from a standard 2048-byte-sector ISO9660 image."""
    with path.open('rb') as iso:
        iso.seek(16 * 2048)
        pvd = iso.read(2048)
        if pvd[1:6] != b'CD001' or pvd[0] != 1:
            raise ValueError('Not an ISO9660 primary volume descriptor')

        def contents(record):
            sector = struct.unpack_from('<I', record, 2)[0]
            size = struct.unpack_from('<I', record, 10)[0]
            iso.seek(sector * 2048)
            return iso.read(size)

        directory = contents(pvd[156:156 + pvd[156]])
        parts = [p.upper() for p in name.split('/')]
        for wanted in parts:
            found, offset = None, 0
            while offset < len(directory):
                length = directory[offset]
                if length == 0:
                    offset = (offset // 2048 + 1) * 2048
                    continue
                record = directory[offset:offset + length]
                label = record[33:33 + record[32]].decode('ascii', 'replace')
                if label.split(';')[0].upper() == wanted:
                    found = record
                    break
                offset += length
            if found is None:
                raise ValueError(f'{name} not found in image')
            directory = contents(found)
        return directory


def parse_sfn(data):
    """Parse an EA Sports FNTS table into glyph dicts (x/y/width/height)."""
    if data[:4] != b'FNTS' or struct.unpack_from('<I', data, 4)[0] != len(data):
        raise ValueError('Unexpected SFN header')
    count = struct.unpack_from('<H', data, 10)[0]
    end = struct.unpack_from('<I', data, 0x18)[0]
    if 0x80 + count * 12 != end:
        raise ValueError('SFN glyph table length mismatch')
    result = []
    for n in range(count):
        code, width, height, x, y, advance, x_offset, y_offset, flags = \
            struct.unpack_from('<HBBHHBbbb', data, 0x80 + n * 12)
        result.append({'codepoint': code, 'glyph': chr(code), 'x': x, 'y': y,
                       'width': width, 'height': height, 'advance': advance,
                       'x_offset': x_offset, 'y_offset': y_offset,
                       'flags': flags})
    return result


def _bilinear_grid(a, x, y):
    """Bilinear sample of 2D float array a at float coord grids x, y."""
    h, w = a.shape
    x0 = np.clip(np.floor(x).astype(np.int64), 0, w - 1)
    y0 = np.clip(np.floor(y).astype(np.int64), 0, h - 1)
    x1 = np.clip(x0 + 1, 0, w - 1)
    y1 = np.clip(y0 + 1, 0, h - 1)
    fx = np.clip(x - x0, 0.0, 1.0)
    fy = np.clip(y - y0, 0.0, 1.0)
    return (a[y0, x0] * (1 - fx) * (1 - fy) + a[y0, x1] * fx * (1 - fy)
            + a[y1, x0] * (1 - fx) * fy + a[y1, x1] * fx * fy)


def _box_blur(m):
    p = np.pad(m, 1, mode='edge')
    return (p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:] + p[1:-1, :-2]
            + p[1:-1, 1:-1] + p[1:-1, 2:] + p[2:, :-2] + p[2:, 1:-1]
            + p[2:, 2:]) / 9.0


def upsample_edge(a1, w, h, up):
    """Edge-directed xup upscale of a 1x alpha crop.

    Structure-tensor orientation per pixel; where the tensor is coherent,
    interpolate with 1D Catmull-Rom along the edge through the sample
    point, else plain bilinear. Sharp across edges, smooth along them.
    """
    a = np.asarray(a1, dtype=np.float32)
    gx = np.zeros_like(a)
    gy = np.zeros_like(a)
    gx[:, 1:-1] = (a[:, 2:] - a[:, :-2]) / 2.0
    gy[1:-1, :] = (a[2:, :] - a[:-2, :]) / 2.0
    jxx = _box_blur(gx * gx)
    jyy = _box_blur(gy * gy)
    jxy = _box_blur(gx * gy)
    oy, ox = np.mgrid[0:h * up, 0:w * up].astype(np.float32)
    x = (ox + 0.5) / up - 0.5
    y = (oy + 0.5) / up - 0.5
    txx = _bilinear_grid(jxx, x, y)
    tyy = _bilinear_grid(jyy, x, y)
    txy = _bilinear_grid(jxy, x, y)
    tr = txx - tyy
    disc = np.sqrt(tr * tr + 4.0 * txy * txy)
    coh = disc / np.maximum(txx + tyy, 1e-6)
    phi = 0.5 * np.arctan2(2.0 * txy, tr) + math.pi / 2.0
    ex, ey = np.cos(phi), np.sin(phi)
    use = coh > 0.25
    base = _bilinear_grid(a, x, y)
    taps = [_bilinear_grid(a, x + t * ex, y + t * ey)
            for t in (-1.5, -0.5, 0.5, 1.5)]
    along = (-taps[0] + 9.0 * taps[1] + 9.0 * taps[2] - taps[3]) / 16.0
    out = np.where(use, np.clip(along, 0.0, 255.0), base)
    return Image.fromarray(out.astype(np.uint8))


def raster_ship_alpha(a1, sharp_out_px, thresh, mode='smoothstep'):
    """EDGE x8 -> tone map -> BOX to 4x. Returns PIL L 0..255.

    a1: 1x source — stock alpha (0..128) for atlases 2/72, binary ink
    mask (0/255) for baked 70/80 sprites. sharp_out_px: edge width in
    pack output px (default 1.0). thresh: coverage cutoff in
    stock-alpha units (default 64). mode: 'smoothstep' (thresholded
    S-curve) or 'contrast' (logistic, no threshold).
    """
    up = upsample_edge(a1, a1.width, a1.height, 8)
    g = np.clip(np.asarray(up.convert('L')).astype(np.float32) / 128.0,
                0.0, 1.0)
    th = thresh / 128.0
    w01 = max(0.02, sharp_out_px * 0.125)
    if mode == 'contrast':
        s = max(0.005, w01 / 4.4)
        c = 0.75 * th
        a = 1.0 / (1.0 + np.exp(-(g - c) / s))
    else:
        t = np.clip((g - th + w01 / 2.0) / w01, 0.0, 1.0)
        a = t * t * (3.0 - 2.0 * t)
    out = Image.fromarray((a * 255.0).astype(np.uint8))
    small = out.resize((a1.width * 4, a1.height * 4),
                       Image.Resampling.BOX)
    del up, g, a, out
    gc.collect()
    return small


def _word_tiles(atlas, stock):
    """label -> region/tile, validating the glyph segmentation."""
    source = np.asarray(stock)
    out = {}
    for label, region, color in WORDS[atlas]:
        x0, y0, x1, y1 = region
        tile = source[y0:y1, x0:x1]
        mask = fill_mask(tile, color)
        if len(runs(mask.any(axis=0))) != len(label):
            raise ValueError(f'{atlas} {label}: segment count')
        out[label] = {'region': region, 'tile': tile}
    return out


def build_atlas(atlas, stock, rows_2_72, sharp, thresh, mode, stat):
    """Full pack canvas for one atlas. RGB upscaled stock, alpha replaced
    in ink cells only, clipped to the stock mask, 0..128 range."""
    W, H = stock.size
    rgb4 = np.asarray(stock.convert('RGB').resize(
        (W * 4, H * 4), Image.Resampling.BILINEAR), dtype=np.uint8)
    a4 = np.asarray(stock.getchannel('A').resize(
        (W * 4, H * 4), Image.Resampling.BOX), dtype=np.float32)
    if atlas in (70, 80):
        info = _word_tiles(atlas, stock)
        for label in [label for label, _r, _c in WORDS[atlas]]:
            x0, y0, x1, y1 = info[label]['region']
            b4 = (x0 * 4, y0 * 4, x1 * 4, y1 * 4)
            tile = info[label]['tile']
            ink = (tile[:, :, 3] >= 64).astype(np.uint8) * 255
            a = raster_ship_alpha(Image.fromarray(ink), sharp, thresh,
                                  mode)
            stat['regions'] += 1
            if np.asarray(a).sum() == 0:
                stat['empty_kept_stock'] += 1
                continue
            ship = np.minimum(
                np.rint(np.asarray(a, dtype=np.float32) * 128.0 / 255.0),
                a4[b4[1]:b4[3], b4[0]:b4[2]]).astype(np.uint8)
            a4[b4[1]:b4[3], b4[0]:b4[2]] = ship
            del a, ship
            gc.collect()
    else:
        seen = set()
        base = np.asarray(stock.getchannel('A'))
        for row in rows_2_72:
            w, h, x, y = (int(row[k]) for k in
                          ('width', 'height', 'x', 'y'))
            key = (row['glyph'], x, y, w, h)
            if key in seen:
                continue
            seen.add(key)
            stat['cells'] += 1
            if w * h < 4:
                stat['tiny_kept_stock'] += 1
                continue
            a1 = Image.fromarray(base[y:y + h, x:x + w].astype(np.uint8))
            a = raster_ship_alpha(a1, sharp, thresh, mode)
            if np.asarray(a).sum() == 0:
                stat['empty_kept_stock'] += 1
                del a1, a
                gc.collect()
                continue
            b4 = (x * 4, y * 4, (x + w) * 4, (y + h) * 4)
            ship = np.minimum(
                np.rint(np.asarray(a, dtype=np.float32) * 128.0 / 255.0),
                a4[b4[1]:b4[3], b4[0]:b4[2]]).astype(np.uint8)
            a4[b4[1]:b4[3], b4[0]:b4[2]] = ship
            del a1, a, ship
            gc.collect()
    return Image.fromarray(np.dstack([rgb4, a4.astype(np.uint8)]))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iso', type=Path, required=True,
                   help='own supported USA disc image (SFN metrics)')
    p.add_argument('--stock-dump', type=Path, required=True,
                   help='own texture dump holding the four stock atlases')
    p.add_argument('--out', type=Path, required=True,
                   help='output dir for the four replacement PNGs')
    p.add_argument('--r-sharp', type=float, default=R_SHARP_DEFAULT)
    p.add_argument('--r-thresh', type=int, default=R_THRESH_DEFAULT)
    p.add_argument('--r-mode', default='smoothstep',
                   choices=('smoothstep', 'contrast'))
    a = p.parse_args(argv)
    if a.r_sharp < 0.25 or a.r_sharp > 16.0:
        p.error('--r-sharp 0.25..16')
    a.out.mkdir(parents=True, exist_ok=True)
    for atlas in (2, 72, 70, 80):
        stat = {'cells': 0, 'regions': 0, 'tiny_kept_stock': 0,
                'empty_kept_stock': 0}
        stock = Image.open(a.stock_dump / NAMES[atlas]).convert('RGBA')
        rows = [dict(r) for r in parse_sfn(
            iso_read(a.iso, SFN_FILES[atlas]))] if atlas in (2, 72) \
            else None
        # parse_sfn dicts carry codepoint/glyph/x/y/width/height keys.
        img = build_atlas(atlas, stock, rows, a.r_sharp, a.r_thresh,
                          a.r_mode, stat)
        dest = a.out / NAMES[atlas]
        img.save(dest)
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        print(f'atlas {atlas}: {stat} sha256 {digest}', flush=True)
        del img, stock
        gc.collect()
    return 0


if __name__ == '__main__':
    sys.exit(main())
