"""Synthetic tests for the public raster-edge font builder."""
import importlib.util
import struct
import sys
import tempfile
import unittest
from pathlib import Path

BUILD = Path(__file__).resolve().parents[1] / 'remaster_font.py'
SPEC = importlib.util.spec_from_file_location('remaster_font', BUILD)
remaster_font = importlib.util.module_from_spec(SPEC)
try:
    SPEC.loader.exec_module(remaster_font)
    import numpy as np
    from PIL import Image
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False
    np = None
    Image = None

if not HAVE_DEPS:
    raise unittest.SkipTest('remaster_font needs numpy+Pillow; '
                            'see tools/ssx3 README font-pack section')


def make_sfn(entries):
    """Minimal FNTS blob. entries: (codepoint, w, h, x, y)."""
    blob = bytearray(0x80 + len(entries) * 12)
    blob[0:4] = b'FNTS'
    struct.pack_into('<I', blob, 4, len(blob))
    struct.pack_into('<H', blob, 10, len(entries))
    struct.pack_into('<I', blob, 0x18, 0x80 + len(entries) * 12)
    for n, (code, w, h, x, y) in enumerate(entries):
        struct.pack_into('<HBBHHBbbb', blob, 0x80 + n * 12, code, w, h,
                         x, y, w, 0, 0, 0)
    return bytes(blob)


def dir_record(sector, size, name):
    name_b = name.encode('ascii')
    length = 33 + len(name_b)
    rec = bytearray(length)
    rec[0] = length
    struct.pack_into('<I', rec, 2, sector)
    struct.pack_into('<I', rec, 10, size)
    rec[32] = len(name_b)
    rec[33:33 + len(name_b)] = name_b
    return bytes(rec)


def make_iso(files):
    """Minimal ISO9660. files: {path: bytes}. Returns image bytes."""
    img = bytearray(64 * 2048)
    sectors = {'': 20}
    nxt = [21]

    def take():
        s = nxt[0]
        nxt[0] += 1
        return s

    dirs = set()
    for path in files:
        parts = path.split('/')[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add('/'.join(parts[:i]))
    for d in sorted(dirs):
        sectors[d] = take()
    for path in sorted(files):
        sectors[path] = take()
    children = {}
    for key in list(dirs) + list(files):
        parent = '/'.join(key.split('/')[:-1])
        children.setdefault(parent, []).append(key)
    for d, sector in sectors.items():
        if d in files:
            continue
        recs = b''.join(
            dir_record(sectors[c], len(files[c]) if c in files else 2048,
                       c.split('/')[-1] + (';1' if c in files else ''))
            for c in sorted(children.get(d, [])))
        img[sector * 2048:sector * 2048 + len(recs)] = recs
    for path, data in files.items():
        sec = sectors[path]
        img[sec * 2048:sec * 2048 + len(data)] = data
    pvd = bytearray(2048)
    pvd[0] = 1
    pvd[1:6] = b'CD001'
    root = dir_record(20, 2048, '\x00')
    pvd[156:156 + len(root)] = root
    img[16 * 2048:17 * 2048] = pvd
    return bytes(img)


class SfnTest(unittest.TestCase):
    def test_parse_sfn_roundtrip(self):
        rows = remaster_font.parse_sfn(make_sfn([(65, 4, 4, 2, 2),
                                                 (66, 3, 3, 10, 2)]))
        self.assertEqual([(r['glyph'], r['x'], r['y'], r['width'],
                           r['height']) for r in rows],
                         [('A', 2, 2, 4, 4), ('B', 10, 2, 3, 3)])

    def test_parse_sfn_rejects_header(self):
        with self.assertRaises(ValueError):
            remaster_font.parse_sfn(b'NOPE' + bytes(100))

    def test_iso_read_nested(self):
        files = {'DATA/FONTS/FEFONT.SFN': make_sfn([(65, 4, 4, 2, 2)]),
                 'DATA/FONTS/HUDFONT.SFN': make_sfn([(66, 3, 3, 10, 2)])}
        with tempfile.TemporaryDirectory() as temp:
            iso = Path(temp) / 't.iso'
            iso.write_bytes(make_iso(files))
            rows = remaster_font.parse_sfn(
                remaster_font.iso_read(iso, 'DATA/FONTS/FEFONT.SFN'))
            self.assertEqual([r['glyph'] for r in rows], ['A'])
            rows = remaster_font.parse_sfn(
                remaster_font.iso_read(iso, 'DATA/FONTS/HUDFONT.SFN'))
            self.assertEqual([r['glyph'] for r in rows], ['B'])
            with self.assertRaises(ValueError):
                remaster_font.iso_read(iso, 'DATA/FONTS/MISSING.SFN')

    def test_runs(self):
        self.assertEqual(remaster_font.runs([False, True, True, False,
                                             True, False]),
                         [(1, 3), (4, 5)])


@unittest.skipUnless(HAVE_DEPS, 'needs numpy+Pillow')
class RecipeTest(unittest.TestCase):
    def paint_word(self, tile, color, spans, top, height):
        for start, end in spans:
            tile[top:top + height, start:end, :3] = {
                'orange70': (220, 80, 30), 'green70': (160, 200, 20),
                'white': (250, 250, 250), 'green80': (100, 220, 10),
                'orange80': (200, 100, 20)}[color]
            tile[top:top + height, start:end, 3] = 128

    def test_end_to_end_synthetic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sfn = {'DATA/FONTS/FEFONT.SFN': make_sfn([(65, 6, 6, 4, 4)]),
                   'DATA/FONTS/HUDFONT.SFN': make_sfn([(66, 5, 5, 4, 4)])}
            iso = root / 't.iso'
            iso.write_bytes(make_iso(sfn))
            dump = root / 'dump'
            dump.mkdir()
            stock = {}
            for atlas in (2, 72, 70, 80):
                tile = np.zeros((32, 32, 4), np.uint8)
                tile[4:12, 4:12, 3] = 128
                tile[4:12, 4:12, :3] = 200
                stock[atlas] = Image.fromarray(tile)
                stock[atlas].save(dump / remaster_font.NAMES[atlas])
            # Word tiles that segment into their labels.
            w70 = np.zeros((256, 256, 4), np.uint8)
            def bars(tile, y, color, n, x0=4, step=12, wd=8, ht=10):
                for i in range(n):
                    self.paint_word(tile, color, [(x0 + i * step,
                                                  x0 + i * step + wd)],
                                    y, ht)
            bars(w70, 4, 'orange70', 7)
            bars(w70, 33, 'green70', 10)
            bars(w70, 60, 'green70', 9)
            bars(w70, 88, 'white', 5)
            bars(w70, 180, 'white', 1, x0=214)
            stock[70] = Image.fromarray(w70)
            stock[70].save(dump / remaster_font.NAMES[70])
            w80 = np.zeros((256, 256, 4), np.uint8)
            bars(w80, 4, 'green80', 3, x0=139)
            bars(w80, 172, 'orange80', 3, x0=164)
            bars(w80, 194, 'orange80', 6, x0=149)
            stock[80] = Image.fromarray(w80)
            stock[80].save(dump / remaster_font.NAMES[80])
            out = root / 'out'
            rc = remaster_font.main(['--iso', str(iso), '--stock-dump',
                                     str(dump), '--out', str(out)])
            self.assertEqual(rc, 0)
            for atlas in (2, 72, 70, 80):
                got = Image.open(out / remaster_font.NAMES[atlas])
                self.assertEqual(got.size, (128, 128) if atlas in (2, 72)
                                 else (1024, 1024))
                a = np.asarray(got)
                self.assertEqual(a[:, :, 3].max() <= 128, True)
                rgb = np.asarray(stock[atlas].convert('RGB').resize(
                    got.size, Image.Resampling.BILINEAR))
                self.assertTrue((a[:, :, :3] == rgb).all())
            # Deterministic: second run is byte-identical.
            out2 = root / 'out2'
            remaster_font.main(['--iso', str(iso), '--stock-dump',
                                str(dump), '--out', str(out2)])
            for atlas in (2, 72, 70, 80):
                self.assertEqual(
                    (out / remaster_font.NAMES[atlas]).read_bytes(),
                    (out2 / remaster_font.NAMES[atlas]).read_bytes())


if __name__ == '__main__':
    unittest.main()
