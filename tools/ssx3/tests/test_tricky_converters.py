"""Synthetic tests for the Tricky audio/HUD/sidecar converters.

No game data, no discs, no ffmpeg: every test builds its inputs in
tempfile space. Full-pipeline golden runs (pinned ISO -> recorded SHAs)
are documented in tools/ssx3/tricky/README.md and run in-lane, not here.
"""
import importlib.util
import json
import math
import struct
import sys
import unittest
from pathlib import Path

TRICKY = Path(__file__).resolve().parents[1] / 'tricky'


def load(name):
    spec = importlib.util.spec_from_file_location(name, TRICKY / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


eaxa = load('speech_eaxa')
merge = load('speech_merge')
mpf = load('mpf3to4')
atlas = load('make_hud_atlas')
mapnames = load('mapnames')
slots = load('speech_slots')


class EaxaTest(unittest.TestCase):
    def test_clip_and_nibble(self):
        self.assertEqual(eaxa.clip16(70000), 32767)
        self.assertEqual(eaxa.clip16(-70000), -32768)
        self.assertEqual(eaxa.clip16(42), 42)
        self.assertEqual(eaxa.sext4(0xF), -1)
        self.assertEqual(eaxa.sext4(0x7), 7)

    def test_frame_shape_and_determinism(self):
        src = [int(10000 * math.sin(i / 3.0)) for i in range(28)]
        first = eaxa.encode_frame(src, 0, 0)
        second = eaxa.encode_frame(src, 0, 0)
        self.assertEqual(first, second)
        ctl, data, _, _, _ = first
        self.assertTrue(0 <= ctl <= 255)
        self.assertEqual(len(data), 14)

    def test_roundtrip_snr(self):
        pcm = [[int(12000 * math.sin(i / 5.0)) for i in range(280)]]
        splits, chb = eaxa.encode_blocks(pcm, 280)
        packets = eaxa.packetize(splits, chb)
        self.assertEqual(len(packets), 1)
        self.assertTrue(packets[0].startswith(b'SCDl'))
        # decode the packet back through the reference frame decoder
        payload = packets[0][8:]
        n = struct.unpack('<I', payload[:4])[0]
        self.assertEqual(n, 280)
        off = struct.unpack('<I', payload[4:8])[0]
        base = 8 + off
        got = []
        # re-derive frames from the packet body directly
        body = payload[base:]
        cur, prev = struct.unpack('<hh', body[:4])
        frames = body[4:]
        self.assertEqual(len(frames) % 15, 0)
        for f in range(len(frames) // 15):
            ctl = frames[f * 15]
            nib = []
            for b in frames[f * 15 + 1:f * 15 + 15]:
                nib += [(b >> 4) & 15, b & 15]
            signed = [x - 16 if x & 8 else x for x in nib]
            rec = eaxa._decode_frame(signed, (ctl >> 4) & 15, ctl & 15, cur, prev)
            got += rec
            cur, prev = rec[-1], rec[-2]
        self.assertEqual(len(got), n)
        se = sum((a - b) ** 2 for a, b in zip(pcm[0], got))
        ss = sum(a * a for a in pcm[0])
        snr = 10 * math.log10(ss / (se + 1e-9))
        self.assertGreater(snr, 20.0)


class CompositeFormatTest(unittest.TestCase):
    def test_parse_desc_roundtrip(self):
        blob = (b'PS2XCMP1\nsize 8192\nimage 100\n'
                b'self 2048 2\niso 500 3\nself 6144 1\nend\n')
        blob = blob + bytes(2048 - len(blob))
        size, segs = merge.parse_desc(blob)
        self.assertEqual(size, 8192)
        self.assertEqual(segs, [('self', 2048, 2), ('iso', 500, 3),
                                ('self', 6144, 1)])

    def test_big_members(self):
        d = b'BIGF' + b'\0' * 4 + struct.pack('>I', 2) + struct.pack('>I', 8 + 7 + 8 + 7)
        d += struct.pack('>II', 100, 50) + b'aa\0'
        d += struct.pack('>II', 200, 60) + b'bb\0'
        members = merge.big_members
        import io

        class F(io.BytesIO):
            pass

        f = F(d)
        out = members(f, 0)
        self.assertEqual(out, {'aa': (100, 50), 'bb': (200, 60)})


class MpfHelpersTest(unittest.TestCase):
    def test_int_readers(self):
        b = bytes([1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(mpf.u8(b, 2), 3)
        self.assertEqual(mpf.u16le(b, 0), 0x0201)
        self.assertEqual(mpf.u32le(b, 0), 0x04030201)
        self.assertEqual(mpf.u32be(b, 0), 0x01020304)
        self.assertEqual(mpf.align4(17), 20)
        self.assertEqual(mpf.align4(16), 16)


class AtlasOpsTest(unittest.TestCase):
    def test_crop_transpose(self):
        rgba = bytes(range(4 * 4 * 4))
        c = atlas.crop(rgba, 4, 1, 1, 2, 2)
        self.assertEqual(len(c), 2 * 2 * 4)
        self.assertEqual(c[:4], rgba[(1 * 4 + 1) * 4:(1 * 4 + 1) * 4 + 4])
        strip = b''.join(struct.pack('BBBB', i, i, i, 255) for i in range(4))
        t = atlas.transpose2(strip, 1, 4)
        self.assertEqual(len(t), 2 * 4 * 4)
        self.assertEqual(t[:16], t[16:])

    def test_desat_orangize_alpha_untouched(self):
        src = bytes([200, 100, 50, 128] * 3)
        d = atlas.desat(src)
        self.assertEqual(d[3::4], bytes([128, 128, 128]))
        self.assertEqual(d[0], d[1], )
        self.assertEqual(d[1], d[2])
        o = atlas.orangize(src)
        self.assertEqual(o[3::4], bytes([128, 128, 128]))
        self.assertTrue(o[0] >= o[1] >= o[2])

    def test_header_magic(self):
        blob = b'TKHUD2\x00\x00' + struct.pack('<III', 256, 256, 0)
        self.assertEqual(len(blob), 20)


class MapnamesTest(unittest.TestCase):
    def test_sections(self):
        text = ('### MODELS BEGIN\nGem_Red 12 0 abc\nGem_Blue 13 0 def\n'
                '### MODELS END\n### SPLINES BEGIN\nS1 3 0 x\n### SPLINES END\n')
        out = mapnames.map_names(text)
        self.assertEqual(out['MODELS'], {12: 'Gem_Red', 13: 'Gem_Blue'})
        self.assertEqual(out['SPLINES'], {3: 'S1'})


class SlotParseTest(unittest.TestCase):
    def _slot(self, channels, s_block, total):
        sch = bytearray(b'\x00' * 40)
        sch[0] = 0xFD
        if channels > 1:
            sch[1] = 0x82
            sch[2] = 1
            sch[3:3 + 1] = channels.to_bytes(1, 'big')
        sch += bytes([0x85, 2]) + total.to_bytes(2, 'big') + b'\0' * 8
        head = b'SCHl' + struct.pack('<I', 8 + len(sch)) + bytes(sch)
        scdl = b'SCDl' + struct.pack('<I', 12) + struct.pack('<I', 1)
        scel = b'SCEl' + struct.pack('<I', 8)
        return head + b'\0' * 12 + scdl + scel

    def test_two_slots(self):
        dat = self._slot(1, 28, 100) + self._slot(2, 28, 200)
        found = slots.parse_slots(dat)
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0]['channels'], 1)
        self.assertEqual(found[1]['channels'], 2)
        self.assertEqual(found[0]['bytes'], len(self._slot(1, 28, 100)))
        self.assertTrue(found[0]['template'].startswith(b'SCHl'))


class DefaultDataTest(unittest.TestCase):
    def test_pool_default(self):
        rows = [l.split('\t') for l in
                (TRICKY / 'music-pool-default.tsv').read_text().splitlines()
                if l and not l.startswith('#')]
        self.assertEqual(len(rows), 35)
        assign = load('music_assign')
        expect = {'B%d/%s' % (b, s) for (b, s, _, _, _) in assign.SLOTS}
        got = {r[0] for r in rows}
        self.assertEqual(got, expect)
        songs = {r[1] for r in rows}
        self.assertTrue(songs <= set(assign.SONGS))
        self.assertNotIn('STOCK', songs)
        # the three too-big songs stay out
        self.assertTrue({'downtime', 'ginandsin', 'kingbeat'} & songs == set())

    def test_resetvol_plans(self):
        plans = json.loads((TRICKY / 'resetvol-plans.json').read_text())
        self.assertEqual(len(plans), 9)
        need = {('gari', 'ARA1'), ('snow', 'BRA2'), ('mesa', 'CRA3'),
                ('merquer', 'DRA4'), ('alaska', 'DSS2'), ('pipe', 'BHP1'),
                ('aloha', 'ABC1'), ('megaple', 'DBC2'), ('elysium', 'EBC3')}
        self.assertEqual({(p['course'], p['host']) for p in plans}, need)
        for p in plans:
            for k in ('source_anchor', 'gate_centre', 'yaw', 'scale'):
                self.assertIn(k, p)
            self.assertTrue(all(math.isfinite(x)
                                for x in p['source_anchor'] + p['gate_centre']))
            self.assertGreater(p['scale'], 0)


if __name__ == '__main__':
    unittest.main()
