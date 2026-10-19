"""Synthetic tests for the Tricky audio/HUD/sidecar converters.

No game data, no discs, no ffmpeg: every test builds its inputs in
tempfile space. Full-pipeline golden runs (pinned ISO -> recorded SHAs)
are documented in tools/ssx3/tricky/README.md and run in-lane, not here.
"""
import importlib.util
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
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
eng = load('english_base')
sil = load('silence_intro_banks')
song = load('song_eaxa')
banner = load('patch_music_inf')


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


class SilenceTest(unittest.TestCase):
    def _member(self, amp=12000):
        pcm = [[int(amp * math.sin(i / 5.0)) for i in range(280)]]
        splits, chb = eaxa.encode_blocks(pcm, 280)
        scdl = eaxa.packetize(splits, chb)
        pt = bytes([0xFD, 0x82, 0x01, 0x01, 0x85, 0x01, 0x00, 0xFF])
        sch = b'SCHl' + struct.pack('<I', 12 + len(pt)) + bytes(4) + pt
        scel = b'SCEl' + struct.pack('<I', 8)
        return sch + b'SCCl' + struct.pack('<I', 12) + struct.pack('<I', 1) + scdl[0] + scel

    def _peak(self, blob):
        _, _, pcm = song.decode(blob + b'\0' * 65536, 3600 * 32000)
        n = min(len(x) for x in pcm)
        return max(max(abs(v) for v in x[:n]) for x in pcm) if n else 0

    def test_loud_member_decodes_loud(self):
        self.assertGreater(self._peak(self._member()), 1000)

    def test_silence_keeps_structure_kills_audio(self):
        member = self._member()
        quiet, takes, skipped = sil.silence_member(member)
        self.assertEqual(len(quiet), len(member))
        self.assertEqual(takes, 1)
        self.assertEqual(skipped, [])
        self.assertEqual(self._peak(quiet), 0)
        # headers, counts, offsets and markers survive byte-for-byte
        sch_len = struct.unpack('<I', member[4:8])[0]
        self.assertEqual(quiet[:sch_len], member[:sch_len])
        self.assertEqual(quiet[-8:], member[-8:])
        qi = quiet.index(b'SCDl')
        oi = member.index(b'SCDl')
        self.assertEqual(qi, oi)
        self.assertEqual(quiet[qi:qi + 16], member[oi:oi + 16])
        self.assertTrue(all(b == 0 for b in quiet[qi + 16:qi + struct.unpack('<I', member[oi + 4:oi + 8])[0]]))

    def test_no_takes_fails(self):
        with self.assertRaises(AssertionError):
            sil.silence_member(b'\0' * 64)


class BannerTest(unittest.TestCase):
    FAKE = ('# synth config\r\n'
            '\r\n#### First Song\r\n[First]\r\n'
            '    ADDTOFE = 1\r\n    TITLE = "First Song"\r\n'
            '    ARTIST = "First Artist"\r\n    ALBUM = "First Album"\r\n'
            '    PATHDATA = "first.mpf"\r\n    MUSDATA = "first.mus"\r\n'
            '    SONGBIG = 2\r\n\r\n\r\n#### Menu Bed\r\n[MenuBed]\r\n'
            '    ADDTOFE = 0\r\n    ARTIST = ""\r\n    ALBUM = "Menu Album"\r\n'
            '    PATHDATA = "menubed.mpf"\r\n    MUSDATA = "menubed.mus"\r\n'
            '    SONGBIG = 1\r\n\r\n')

    def test_patch_retitles_only_pool_slot(self):
        mapping = [('first.mus', "Top Bomb", "")]
        patched = banner.patch_inf(self.FAKE.encode('latin1'), mapping)
        text = patched.decode('latin1')
        self.assertIn('TITLE = "Top Bomb"', text)
        self.assertIn('ARTIST = ""', text)
        self.assertNotIn('TITLE = "First Song"', text)
        self.assertNotIn('ARTIST = "First Artist"', text)
        self.assertNotIn('ALBUM = "First Album"', text)
        # menu section untouched (no TITLE key added, ARTIST/ALBUM kept)
        self.assertNotIn('MenuBed"\r\n    TITLE', text)
        self.assertIn('ALBUM = "Menu Album"', text)
        self.assertIn('MUSDATA = "menubed.mus"', text)

    def test_patch_missing_key_fails(self):
        with self.assertRaises(AssertionError):
            banner.patch_inf(self.FAKE.encode('latin1'), [('nosuch.mus', 'X', 'Y')])

    def test_pad_exact_size(self):
        short = banner.pad_exact(b'ab\r\n', 256)
        self.assertEqual(len(short), 256)
        self.assertTrue(short.endswith(b'\r\n'))
        with self.assertRaises(AssertionError):
            banner.pad_exact(b'x' * 257, 256)

    def test_banner_default_map(self):
        rows = [l.split('\t') for l in
                (TRICKY / 'music-banner-default.tsv').read_text().splitlines()
                if l and not l.startswith('#')]
        self.assertEqual(len(rows), 35)
        pool = {r[0]: r[1] for l in
                (TRICKY / 'music-pool-default.tsv').read_text().splitlines()
                if (l and not l.startswith('#'))
                for r in [l.split('\t')]}
        self.assertEqual({r[0] for r in rows}, set(pool))
        self.assertEqual({(r[0], r[1]) for r in rows},
                         {(s, pool[s]) for s in pool})
        for r in rows:
            title = r[2]
            self.assertTrue(title and '"' not in title)


class RemusScratchTest(unittest.TestCase):
    # music_remus must never write predictable /tmp/tk41_* names (two
    # builds under one account share them; symlinks redirect them).
    # Segments live in a private per-invocation dir under the output dir.
    def _one_sample_mpf(self):
        b = bytearray(64)
        b[0:4] = b'xDFP'
        b[4] = 3
        b[5] = 1
        struct.pack_into('<H', b, 0x12, 1)      # N = 1 node
        struct.pack_into('<H', b, 0x24, 10)      # node at 10*4 = 40
        # node at 40: 12 zero hdr bytes, n = b[51] = 0 (no play/nxt)
        struct.pack_into('<I', b, 52, 14)        # tptr -> st = 56
        struct.pack_into('<I', b, 56, 0)         # sample 0: mus offset 0
        struct.pack_into('<I', b, 60, 1234)      # sample 0: dur 1234 ms
        return bytes(b)

    def test_segments_stay_out_of_tmp(self):
        import glob
        with tempfile.TemporaryDirectory() as tmp:
            mus = bytes(range(256)) * 2
            (Path(tmp) / 's.mus').write_bytes(mus)
            (Path(tmp) / 's.mpf').write_bytes(self._one_sample_mpf())
            (Path(tmp) / 't.sch').write_bytes(b'template')
            out, tsv = Path(tmp) / 'o.mus', Path(tmp) / 'o.tsv'
            before = set(glob.glob('/tmp/tk41_*'))
            r = subprocess.run(
                [sys.executable, str(TRICKY / 'music_remus.py'),
                 str(Path(tmp) / 's.mus'), str(Path(tmp) / 's.mpf'),
                 str(Path(tmp) / 't.sch'), str(out), str(tsv),
                 'only=0', 'jobs=1'],
                cwd=tmp, capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            after = set(glob.glob('/tmp/tk41_*'))
            self.assertEqual(after - before, set(),
                             'segment files escaped to /tmp')
            # only=0 copies the source segment verbatim at offset 0
            self.assertEqual(out.read_bytes(), mus)
            self.assertEqual(tsv.read_text().splitlines(),
                             ['i\toff\tdur', '0\t0\t1234'])
            # default scratch cleaned itself: only inputs + outputs remain
            names = sorted(p.name for p in Path(tmp).iterdir())
            self.assertEqual(names, ['o.mus', 'o.tsv', 's.mpf', 's.mus',
                                     't.sch'])

    def test_seg_scratch_shapes(self):
        remus = load('music_remus')
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'o.mus'
            out.write_bytes(b'x')
            with remus.seg_scratch(str(out)) as d:
                self.assertEqual(os.path.dirname(os.path.abspath(d)),
                                 os.path.abspath(tmp))
                Path(d, 'probe').write_bytes(b'p')
            self.assertFalse(os.path.exists(d), 'default scratch not cleaned')
            custom = os.path.join(tmp, 'mine')
            with remus.seg_scratch(str(out), custom) as d2:
                self.assertEqual(d2, os.path.abspath(custom))
            with self.assertRaises(SystemExit):
                with remus.seg_scratch(str(out), '/tmp'):
                    pass


class GemToolImportTest(unittest.TestCase):
    # bake_gems/gem_step import wlib, which lives in tricky/tk12 — not the
    # script dir. The driver runs tools with cwd=<workdir> and a stripped
    # PYTHONPATH, so each tool must put tk12 on sys.path itself. Run every
    # gem tool's --help from a bare temp dir: import failure fails here.
    # bake_reset_volumes has the same constraint (tricky_pbd in tk13).
    TOOLS = ('bake_gems.py', 'gem_step.py', 'bake_mega_gems.py',
             'bake_reset_volumes.py')

    def test_gem_tools_import_away_from_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            for tool in self.TOOLS:
                with self.subTest(tool=tool):
                    r = subprocess.run(
                        [sys.executable, str(TRICKY / tool), '--help'],
                        cwd=tmp, capture_output=True, text=True, timeout=120)
                    self.assertEqual(r.returncode, 0, r.stderr[-500:])


class EnglishBaseTest(unittest.TestCase):
    def _take(self, amp=9000, seed=0):
        pcm = [[int(amp * math.sin((i + seed) / 4.0)) for i in range(56)]]
        splits, chb = eaxa.encode_blocks(pcm, 56)
        scdl = eaxa.packetize(splits, chb)
        pt = bytes([0xFD, 0x82, 0x01, 0x02, 0x85, 0x01, 0x00, 0xFF])
        sch = b'SCHl' + struct.pack('<I', 12 + len(pt)) + bytes(4) + pt
        scel = b'SCEl' + struct.pack('<I', 8)
        return sch + b'SCCl' + struct.pack('<I', 12) + struct.pack('<I', 1) + scdl[0] + scel

    def _member(self):
        return self._take(seed=0) + self._take(seed=40)

    def _wav(self, path, frames=22050, channels=2):
        import wave
        with wave.open(str(path), 'wb') as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(struct.pack('<' + 'h' * (frames * channels),
                                      *([1000] * (frames * channels))))

    def test_decisions_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            tsv = Path(tmp) / 'd.tsv'
            tsv.write_text('slot\tsource\tgain_db\n'
                           '0\ttricky-Knockdown-5\t+2.50\n'
                           '3\tKEEP-ORIGINAL\t\n')
            rows = eng.load_decisions(tsv)
            self.assertEqual(rows, [(0, 'Knockdown', 5, 2.5),
                                   (3, None, None, None)])

    def test_decisions_bad_rows_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / 'b.tsv'
            bad.write_text('slot\tsource\tgain_db\n0\tKnockdown-5\t+2.50\n')
            with self.assertRaises(SystemExit):
                eng.load_decisions(bad)
            bad.write_text('slot\tsource\tgain_db\n0\ttricky-Knockdown-5\t\n')
            with self.assertRaises(SystemExit):
                eng.load_decisions(bad)

    def test_bank_member_name(self):
        self.assertEqual(eng.bank_member_name('Knockdown'),
                         'data\\speech\\mc\\Knockdown.dat')
        self.assertEqual(eng.bank_member_name('Pre_Race_Overlay_01'),
                         'data\\speech\\narr\\Pre_Race_Overlay_01.dat')

    def test_fill_all_slots(self):
        member = self._member()
        take = self._take(seed=99)[:100]
        filled, n = eng.fill_all_slots(member, take, 'synth.dat')
        self.assertEqual(n, 2)
        self.assertEqual(len(filled), len(member))
        bounds = eng.slot_bounds(member)
        for s, size in bounds:
            self.assertEqual(filled[s:s + len(take)], take)
            self.assertTrue(all(b == 0 for b in filled[s + len(take):s + size]))

    def test_fill_oversize_take_fails(self):
        member = self._member()
        with self.assertRaises(SystemExit):
            eng.fill_all_slots(member, b'\1' * (len(member) + 1), 'synth.dat')

    def test_slot_bounds_no_takes_fails(self):
        with self.assertRaises(SystemExit):
            eng.slot_bounds(b'\0' * 64)

    def test_take_determinism_and_fit(self):
        take = self._take()
        template = take[:take.index(b'SCEl') + 8]
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / 'rs.wav'
            self._wav(wav, frames=56)
            first = eng.build_take('ffmpeg', wav, 0.0, template, 56, len(take) + 64, 's')
            second = eng.build_take('ffmpeg', wav, 0.0, template, 56, len(take) + 64, 's')
            self.assertEqual(first, second)
            self.assertTrue(first.startswith(b'SCHl'))
            with self.assertRaises(SystemExit):
                eng.build_take('ffmpeg', wav, 0.0, template, 56, 8, 's')

    def test_shipped_decisions_schema(self):
        decdir = Path(__file__).resolve().parents[1] / 'tricky' / 'data' / 'english-decisions'
        names = ['DJ_Artist_Intro_2_eng.dat.decisions.tsv',
                 'PA_Medal_Run_Intro_eng.dat.decisions.tsv',
                 'PA_Rider_Intro_eng.dat.decisions.tsv',
                 'PA_Rider_Race_Intro_eng.dat.decisions.tsv',
                 'PA_Rider_Postition_eng.dat.decisions.tsv']
        for name in names:
            rows = eng.load_decisions(decdir / name)
            self.assertTrue(rows, name)
            seen = set()
            for slot, bank, idx, gain in rows:
                self.assertGreaterEqual(slot, 0)
                self.assertNotIn(slot, seen)
                seen.add(slot)
                if bank is None:
                    continue
                self.assertGreaterEqual(idx, 0)
                self.assertIsInstance(gain, float)
        artist = eng.load_decisions(decdir / names[0])
        self.assertEqual(len(artist), 9)
        for slot, bank, idx, gain in artist:
            self.assertEqual((bank, idx, gain), ('VenueIntro', 20, 1.3))


if __name__ == '__main__':
    unittest.main()
