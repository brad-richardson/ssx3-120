"""Tricky builder step: disc identity, record/resume, stage/install wiring.

No network, no real discs: Tricky ISOs under test are synthetic
(ISO9660 structures only) and the converter driver is a stub honoring
the proposed driver contract. The stock-only path stays untouched:
with no Tricky layer, stage/install behave exactly as before.
"""
import importlib.util
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

SPEC = importlib.util.spec_from_file_location('ssx3_build', HERE / 'build.py')
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)
# CI runners have ~13 GB free: stub the F15 disk preflight for flow tests
# (test_build_onecmd.CapacityTest covers the real check).
build.check_capacity = lambda *a, **k: True

SECTOR = 2048


def dir_record(extent, size, flags, name):
    rec = bytearray()
    rec.append(0)
    rec.append(0)
    rec += struct.pack('<I', extent) + struct.pack('>I', extent)
    rec += struct.pack('<I', size) + struct.pack('>I', size)
    rec += b'\x00' * 7
    rec.append(flags)
    rec.append(0)
    rec.append(0)
    rec += struct.pack('<H', 1) + struct.pack('>H', 1)
    rec.append(len(name))
    rec += name
    if len(rec) % 2:
        rec.append(0)
    rec[0] = len(rec)
    return bytes(rec)


def make_tricky_iso(path, elf=True, gari=True, music=True):
    """Minimal ISO9660 with a Tricky-shaped tree (24 sectors)."""
    image = bytearray(24 * SECTOR)
    image[16 * SECTOR] = 1
    image[16 * SECTOR + 1:16 * SECTOR + 6] = b'CD001'
    image[16 * SECTOR + 6] = 1
    root = dir_record(17, SECTOR, 2, b'\x00')
    image[16 * SECTOR + 156:16 * SECTOR + 156 + len(root)] = root

    def sector(n, records):
        blob = b''.join(records)
        image[n * SECTOR:n * SECTOR + len(blob)] = blob

    entries = []
    if elf:
        entries.append(dir_record(18, 512, 0, b'SLUS_203.26;1'))
        image[18 * SECTOR:18 * SECTOR + 3] = b'ELF'
    entries.append(dir_record(19, SECTOR, 2, b'DATA'))
    sector(17, entries)
    sector(19, [dir_record(20, SECTOR, 2, b'MODELS'),
                dir_record(21, SECTOR, 2, b'AUDIO')])
    models = []
    if gari:
        models.append(dir_record(22, 512, 0, b'GARI.BIG;1'))
        image[22 * SECTOR:22 * SECTOR + 4] = b'GARI'
    sector(20, models)
    audio = []
    if music:
        audio.append(dir_record(23, 512, 0, b'MUSIC.BIG;1'))
        image[23 * SECTOR:23 * SECTOR + 5] = b'MUSIC'
    sector(21, audio)
    Path(path).write_bytes(bytes(image))


def pin_disc(iso):
    """A TRICKY_DISCS table keyed by this synthetic image (patched in)."""
    digest = build.sha(iso)
    return {digest: {'serial': 'SLUS-20326', 'region': 'NTSC-U',
                     'bytes': iso.stat().st_size, 'md5': '0' * 32}}


STUB_DRIVER = '''#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--tricky-iso')
p.add_argument('--ssx3-iso')
p.add_argument('--work')
p.add_argument('--jobs', default='8')
p.add_argument('--vgmstream', default=None)
p.add_argument('--ffmpeg', default=None)
p.add_argument('--tricky-audio', default='auto')
a = p.parse_args()
work = Path(a.work)
(work / 'tricky-ov/DATA/WORLDS').mkdir(parents=True, exist_ok=True)
(work / 'tricky-ov/DATA/WORLDS/MEGA.BIG').write_bytes(b'mega-world-bytes')
for name in ('speech-tricky-big', 'music-tricky-big', 'music2-tricky-big',
             'trickyhud.bin', 'tricky-song.wav', 'tricky-gems.tsv',
             'tricky-resetvol.tsv'):
    (work / name).write_bytes(name.encode() + b'-payload')
for name in ('m-tricky.txt', 'tricky.env'):
    (work / name).write_text(
        'alias = /DATA/AUDIO/SPEECH.BIG:/storage/emulated/0/Android/data/'
        'com.ssx3.recomp/files/speech-tricky-big\\n'
        'overlay = /storage/emulated/0/Android/data/com.ps2x.runner/files/tricky-ov\\n')
ssx3 = Path(a.ssx3_iso)
(work / 'tools.json').write_text(json.dumps(
    {'pins': {}, 'toolchain': {'ffmpeg': None, 'vgmstream': None},
     'audio': 'off',
     'ssx3': {'sha256': hashlib.sha256(ssx3.read_bytes()).hexdigest()}}))
'''


def write_stub_driver(path):
    path = Path(path)
    path.write_text(STUB_DRIVER)
    path.chmod(0o755)
    return path


def tricky_args(work, **over):
    base = dict(work=work, jobs=2, app_id=build.PUBLIC_APP_ID,
                skip_fonts=True, stock_dump=None, step='tricky', iso=None,
                prebuilt=False, ffmpeg='ffmpeg-pinned', vgmstream='vgm-pinned',
                tricky_audio='auto')
    base.update(over)
    return types.SimpleNamespace(**base)


class DiscIdentityTest(unittest.TestCase):
    def test_full_identity_passes_on_a_pinned_disc(self):
        with tempfile.TemporaryDirectory() as tmp:
            iso = Path(tmp) / 'tricky.iso'
            make_tricky_iso(iso)
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
                identity = build.tricky_disc_identity(iso)
            self.assertEqual(identity['serial'], 'SLUS-20326')
            self.assertEqual(identity['bytes'], iso.stat().st_size)

    def test_unknown_sha_refuses_with_the_docs_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            iso = Path(tmp) / 'tricky.iso'
            make_tricky_iso(iso)
            with patch.dict(build.TRICKY_DISCS, {}, clear=True):
                with self.assertRaisesRegex(ValueError, 'docs/tricky-inputs.md'):
                    build.tricky_disc_identity(iso)

    def test_size_mismatch_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            iso = Path(tmp) / 'tricky.iso'
            make_tricky_iso(iso)
            table = pin_disc(iso)
            only = next(iter(table))
            table[only] = dict(table[only], bytes=1)
            with patch.dict(build.TRICKY_DISCS, table, clear=True):
                with self.assertRaisesRegex(ValueError, 'byte size mismatch'):
                    build.tricky_disc_identity(iso)

    def test_missing_elf_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            iso = Path(tmp) / 'tricky.iso'
            make_tricky_iso(iso, elf=False)
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
                with self.assertRaisesRegex(ValueError, 'SLUS_203.26'):
                    build.tricky_disc_identity(iso)

    def test_missing_marker_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            iso = Path(tmp) / 'tricky.iso'
            make_tricky_iso(iso, gari=False)
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
                with self.assertRaisesRegex(ValueError, 'marker missing.*GARI'):
                    build.tricky_disc_identity(iso)


def prepared_work(tmp, iso_bytes=b'ssx3-iso'):
    work = Path(tmp) / 'work'
    work.mkdir()
    ssx3 = Path(tmp) / 'SSX3.iso'
    ssx3.write_bytes(iso_bytes)
    (work / 'inputs.json').write_text(json.dumps(
        {'iso': str(ssx3), 'iso_sha256': build.sha(ssx3),
         'slim_iso': str(work / 'SSX3-slim.iso')}))
    return work, ssx3


class BuildTrickyTest(unittest.TestCase):
    def run_step(self, tmp):
        work, _ = prepared_work(tmp)
        iso = Path(tmp) / 'tricky.iso'
        make_tricky_iso(iso)
        driver = write_stub_driver(Path(tmp) / 'build_tricky.py')
        args = tricky_args(work, iso=iso)
        with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True), \
             patch.object(build, 'TRICKY_DRIVER', driver):
            out = io.StringIO()
            with redirect_stdout(out):
                result = build.build_tricky(args, iso)
        return work, iso, result, out.getvalue()

    def test_step_runs_driver_and_records_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, iso, result, log = self.run_step(tmp)
            self.assertIn('SLUS-20326', log)
            record = json.loads((work / 'tricky_inputs.json').read_text())
            self.assertEqual(record['tricky_iso_sha256'], build.sha(iso))
            self.assertEqual(record['tricky_serial'], 'SLUS-20326')
            self.assertIn('tricky-ov/DATA/WORLDS/MEGA.BIG', record['outputs'])
            self.assertIn('m-tricky.txt', record['outputs'])
            mega = work / 'tricky/tricky-ov/DATA/WORLDS/MEGA.BIG'
            self.assertEqual(record['outputs']['tricky-ov/DATA/WORLDS/MEGA.BIG']['sha256'],
                             build.sha(mega))
            self.assertEqual(Path(result), work / 'tricky_inputs.json')

    def test_second_run_skips_when_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, iso, _, _ = self.run_step(tmp)
            driver = write_stub_driver(Path(tmp) / 'build_tricky.py')
            args = tricky_args(work, iso=iso)
            (work / 'tricky/speech-tricky-big').unlink()
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True), \
                 patch.object(build, 'TRICKY_DRIVER', driver):
                self.assertFalse(build.tricky_current(work, iso))
            ssx3 = json.loads((work / 'inputs.json').read_text())['iso']
            subprocess.run([sys.executable, str(driver),
                            '--tricky-iso', str(iso), '--ssx3-iso', ssx3,
                            '--work', str(work / 'tricky')],
                           check=True)
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True), \
                 patch.object(build, 'TRICKY_DRIVER', driver):
                self.assertTrue(build.tricky_current(work, iso))
                out = io.StringIO()
                with redirect_stdout(out):
                    build.build_tricky(args, iso)
                self.assertIn('reusing outputs built', out.getvalue())

    def test_empty_record_is_not_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, iso, _, _ = self.run_step(tmp)
            record = json.loads((work / 'tricky_inputs.json').read_text())
            record['outputs'] = {}
            (work / 'tricky_inputs.json').write_text(json.dumps(record))
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
                self.assertFalse(build.tricky_current(work, iso))

    def test_different_disc_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, iso, _, _ = self.run_step(tmp)
            other = Path(tmp) / 'other.iso'
            make_tricky_iso(other)
            with other.open('ab') as f:
                f.write(b'\x00' * SECTOR)
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
                with self.assertRaisesRegex(ValueError, 'different Tricky disc'):
                    build.tricky_current(work, other)

    def test_unprepared_work_dir_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            work.mkdir()
            iso = Path(tmp) / 'tricky.iso'
            make_tricky_iso(iso)
            driver = write_stub_driver(Path(tmp) / 'build_tricky.py')
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True), \
                 patch.object(build, 'TRICKY_DRIVER', driver):
                with self.assertRaisesRegex(ValueError, 'prepared work directory'):
                    build.build_tricky(tricky_args(work, iso=iso), iso)

    def test_tampered_output_fails_reverification(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, iso, _, _ = self.run_step(tmp)
            (work / 'tricky/music-tricky-big').write_bytes(b'tampered')
            record = build.tricky_record(work)
            with self.assertRaisesRegex(ValueError, 'changed since build_tricky'):
                build.verify_tricky_outputs(work, record)

    def test_dry_run_validates_and_lists_without_building(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, _ = prepared_work(tmp)
            iso = Path(tmp) / 'tricky.iso'
            make_tricky_iso(iso)
            args = tricky_args(work, iso=iso, dry_run=True)
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
                out = io.StringIO()
                with redirect_stdout(out):
                    build.tricky_dry_run(args)
            text = out.getvalue()
            self.assertIn('SLUS-20326', text)
            self.assertIn('m-tricky.txt', text)
            self.assertIn('tricky-ov/', text)
            self.assertIn('Nothing was built', text)
            self.assertFalse((work / 'tricky').exists())


def staged_work(tmp, app_id='com.example.game'):
    """A prepared work dir with a built Tricky layer and matching record."""
    work, _, _, _ = BuildTrickyTest().run_step(tmp)
    (work / 'app-id.txt').write_text(app_id + '\n')
    (work / 'android-resources').mkdir()
    return work


class StageTrickyTest(unittest.TestCase):
    def test_stage_copies_layer_and_rewrites_app_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = staged_work(tmp)
            args = tricky_args(work, app_id='com.example.game', step='stage')
            files = build.stage(args)
            self.assertTrue((files / 'speech-tricky-big').is_file())
            self.assertTrue((files / 'tricky-ov/DATA/WORLDS/MEGA.BIG').is_file())
            manifest = (files / 'm-tricky.txt').read_text()
            self.assertIn('com.example.game', manifest)
            self.assertNotIn('com.ssx3.recomp', manifest)
            self.assertNotIn('com.ps2x.runner', manifest)
            env = (files / 'tricky.env').read_text()
            self.assertIn('com.example.game', env)

    def test_stage_without_a_layer_writes_no_tricky_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, _ = prepared_work(tmp)
            (work / 'app-id.txt').write_text('com.example.game\n')
            (work / 'android-resources').mkdir()
            args = tricky_args(work, app_id='com.example.game', step='stage')
            files = build.stage(args)
            names = [p.name for p in files.rglob('*')]
            self.assertFalse(any('tricky' in name for name in names))

    def test_stage_refuses_a_tampered_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = staged_work(tmp)
            (work / 'tricky/tricky-gems.tsv').write_bytes(b'tampered')
            args = tricky_args(work, app_id='com.example.game', step='stage')
            with self.assertRaisesRegex(ValueError, 'changed since build_tricky'):
                build.stage(args)


class InstallTrickyTest(unittest.TestCase):
    def disc_inputs(self, work):
        iso = work / 'SSX3.iso'
        iso.write_bytes(b'ssx3-image')
        elf = work / 'SLUS_207.72'
        elf.write_bytes(b'elf-bytes')
        slim = work / 'SSX3-slim.iso'
        slim.write_bytes(b'slim-bytes')
        table = Path(str(slim) + '.holes')
        table.write_bytes(b'holes-table')
        (work / 'inputs.json').write_text(json.dumps({
            'iso': str(iso), 'iso_sha256': build.sha(iso),
            'slim_iso': str(slim), 'slim_sha256': build.sha(slim),
            'slim_table_sha256': build.sha(table),
            'elf_sha256': build.sha(elf)}))

    def test_dry_run_lists_the_tricky_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = staged_work(tmp)
            self.disc_inputs(work)
            args = tricky_args(work, dry_run=True)
            out = io.StringIO()
            with redirect_stdout(out):
                build.install(args)
            text = out.getvalue()
            self.assertIn('speech-tricky-big', text)
            self.assertIn('tricky-ov/', text)
            self.assertRegex(text, r'tricky-ov \(1 files, \d+ bytes\)')

    def test_dry_run_without_a_layer_says_stock_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, _ = prepared_work(tmp)
            self.disc_inputs(work)
            args = tricky_args(work, dry_run=True)
            out = io.StringIO()
            with redirect_stdout(out):
                build.install(args)
            text = out.getvalue()
            self.assertIn('stock SSX 3 only', text)
            self.assertNotIn('tricky-ov', text)

    def test_tricky_device_sources_share_the_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = staged_work(tmp)
            args = tricky_args(work, app_id='com.example.game', step='stage')
            files = build.stage(args)
            record = build.tricky_record(work)
            staged, trees = build.tricky_device_sources(files, record)
            self.assertTrue(any(relative == 'speech-tricky-big' for _, relative in staged))
            self.assertIn('tricky-ov', trees)
            empty_files, empty_trees = build.tricky_device_sources(files, None)
            self.assertEqual((empty_files, empty_trees), ([], []))


class OneCommandTrickyTest(unittest.TestCase):
    def test_tricky_step_runs_between_prepare_and_android_libs(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            work.mkdir()
            iso = Path(tmp) / 'SSX3.iso'
            iso.write_bytes(b'ssx3')
            tricky = Path(tmp) / 'tricky.iso'
            tricky.write_bytes(b'tricky')
            args = tricky_args(work, ssx3_iso=iso, tricky_iso=tricky, install=False)
            calls = []
            with patch.object(build, 'validate_ssx3_iso', return_value=iso.resolve()), \
                 patch.object(build, 'preflight_host_tools', return_value=True), \
                 patch.object(build, 'ensure_android_toolchain'), \
                 patch.object(build, 'doctor'), \
                 patch.object(build, 'validate_tricky_iso', return_value=tricky), \
                 patch.object(build, 'prepare_current', return_value=False), \
                 patch.object(build, 'prepare', side_effect=lambda a: calls.append('prepare')), \
                 patch.object(build, 'android_libs_current', return_value=True), \
                 patch.object(build, 'build_tricky', side_effect=lambda a, i: calls.append('tricky')), \
                 patch.object(build, 'android', side_effect=lambda a: calls.append('android')):
                build.android_onecmd(args)
            self.assertEqual(calls, ['prepare', 'tricky', 'android'])

    def test_stock_build_skips_the_tricky_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            work.mkdir()
            iso = Path(tmp) / 'SSX3.iso'
            iso.write_bytes(b'ssx3')
            args = tricky_args(work, ssx3_iso=iso, tricky_iso=None, install=False)
            with patch.object(build, 'validate_ssx3_iso', return_value=iso.resolve()), \
                 patch.object(build, 'preflight_host_tools', return_value=True), \
                 patch.object(build, 'ensure_android_toolchain'), \
                 patch.object(build, 'doctor'), \
                 patch.object(build, 'prepare_current', return_value=True), \
                 patch.object(build, 'android_libs_current', return_value=True), \
                 patch.object(build, 'build_tricky') as tricky, \
                 patch.object(build, 'android'):
                build.android_onecmd(args)
            tricky.assert_not_called()


DRIVER_SPEC = importlib.util.spec_from_file_location(
    'tricky_driver', HERE / 'tricky' / 'build_tricky.py')
driver = importlib.util.module_from_spec(DRIVER_SPEC)
DRIVER_SPEC.loader.exec_module(driver)


class BuildTrickyDriverTest(unittest.TestCase):
    def test_every_course_has_a_golden(self):
        for c in driver.BUILD_COURSES:
            self.assertIn(c, set(driver.ROUTES_COURSES) | set(driver.S5_FINALS))

    def test_final_paths_split_routes_and_s5(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'w'
            work.mkdir()
            args = tricky_args(work)
            b = driver.Build.__new__(driver.Build)
            b.W, b.skipped = work, {}
            for c in driver.BUILD_COURSES:
                path, want = b.world_final_path(c)
                if c in driver.ROUTES_COURSES:
                    self.assertEqual(path, work / f'w-{c}-routes' / f'{c.upper()}.BIG')
                else:
                    self.assertEqual(path, work / f'w-{c}-s5' / f'{c.upper()}.BIG')
                self.assertRegex(want, r'^[0-9a-f]{64}$')

    def test_refuses_an_existing_work_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'w'
            work.mkdir()
            with self.assertRaisesRegex(SystemExit, 'refusing existing'):
                driver.Build(tricky_args(work))

    def test_layout_writes_manifest_env_and_skip_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'w'
            work.mkdir()
            built = {'worlds': {}, 'courspit': work / 'COURSPIT.BIG',
                     'speech-tricky-big': work / 'sp', 'music-tricky-big': work / 'm1',
                     'music2-tricky-big': work / 'm2', 'music-inf-tricky-big': work / 'mi',
                     'english-silent-big': None,
                     'trickyhud.bin': work / 'hud',
                     'tricky-song.wav': work / 'song', 'tricky-gems.tsv': work / 'gems',
                     'tricky-resetvol.tsv': work / 'reset'}
            for c in ('gari', 'snow'):
                final = work / f'{c.upper()}.BIG'
                final.write_bytes(c.encode())
                built['worlds'][c] = final
            for key in ('courspit', 'speech-tricky-big', 'music-tricky-big',
                        'music2-tricky-big', 'music-inf-tricky-big',
                        'trickyhud.bin', 'tricky-song.wav',
                        'tricky-gems.tsv', 'tricky-resetvol.tsv'):
                built[key].write_bytes(b'x')
            menu = work / 'menu' / 'DATA' / 'UI'
            menu.mkdir(parents=True)
            (menu / 'TPABC1.SSH').write_bytes(b'pane')
            args = tricky_args(work)
            b = driver.Build.__new__(driver.Build)
            b.W, b.skipped = work, {'mega': 'no ref', 'english': 'no base'}
            b.a = args
            lay = b.phase_layout(built)
            manifest = (lay / 'm-tricky.txt').read_text()
            self.assertIn('mode = Tricky', manifest)
            self.assertEqual(manifest.count('row = '), 9)
            self.assertNotIn('MEGA', manifest)
            self.assertIn('com.ssx3.recomp', manifest)
            self.assertNotIn('com.ps2x.runner', manifest)
            env = (lay / 'tricky.env').read_text()
            self.assertIn('PS2X_SSX3_COURSE_MANIFEST=/storage/emulated/0/Android/data/com.ssx3.recomp/files/m-tricky.txt', env)
            self.assertIn('PS2X_SSX3_TRICKY_MUSIC_INF=', env)
            self.assertIn('PS2X_SSX3_TRICKY_MC=1', env)
            for dead in ('PS2X_SSX3_TRICKY_COURSES', 'PS2X_SSX3_GEM_TSV',
                         'PS2X_ATRAC_NATIVE', 'PS2X_VAG_Q4',
                         'PS2X_CDVD_IOP_ASYNC', 'PS2X_CDVD_EXTRA_OPEN_SLACK'):
                self.assertNotIn(dead, env)
            self.assertIn('com.ssx3.recomp', env)
            record = (lay / 'SKIPPED.txt').read_text()
            self.assertIn('mega', record)
            self.assertTrue((lay / 'tricky-ov/DATA/WORLDS/GARI.BIG').is_file())

    def test_layout_adds_the_mega_row_when_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'w'
            work.mkdir()
            mega = work / 'MEGA.BIG'
            mega.write_bytes(b'm')
            built = {'worlds': {'mega': mega}, 'courspit': work / 'c',
                     'speech-tricky-big': work / 'a', 'music-tricky-big': work / 'b',
                     'music2-tricky-big': work / 'd', 'music-inf-tricky-big': work / 'i',
                     'english-silent-big': work / 'j',
                     'trickyhud.bin': work / 'e',
                     'tricky-song.wav': work / 'f', 'tricky-gems.tsv': work / 'g',
                     'tricky-resetvol.tsv': work / 'h'}
            for key in ('courspit', 'speech-tricky-big', 'music-tricky-big',
                        'music2-tricky-big', 'music-inf-tricky-big', 'english-silent-big',
                        'trickyhud.bin', 'tricky-song.wav',
                        'tricky-gems.tsv', 'tricky-resetvol.tsv'):
                built[key].write_bytes(b'x')
            (work / 'menu' / 'DATA' / 'UI').mkdir(parents=True)
            b = driver.Build.__new__(driver.Build)
            b.W, b.skipped, b.a = work, {}, tricky_args(work)
            lay = b.phase_layout(built)
            manifest = (lay / 'm-tricky.txt').read_text()
            self.assertEqual(manifest.count('row = '), 10)
            self.assertIn('row = 15:MEGA:Tokyo Megaplex', manifest)
            self.assertFalse((lay / 'SKIPPED.txt').exists())

    def test_speech_slots_get_per_target_work_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'w'
            work.mkdir()
            args = tricky_args(work, tricky_iso=work / 't.iso',
                               ssx3_iso=work / 's.iso',
                               vgmstream='vgmstream-cli')
            b = driver.Build.__new__(driver.Build)
            b.W, b.skipped, b.a = work, {}, args
            b.logs = work / 'logs'
            calls = []

            def fake_big_member(iso, big, want, out):
                out = Path(out)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b'x')
                return out

            def fake_run(logdir, name, *argv, cwd=None):
                calls.append((name, list(argv)))
                return Path(logdir) / f'{name}.log'

            def fake_copy(src, dst):
                dst = Path(dst)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(b'x')
                return dst

            with patch.object(driver, 'big_member', side_effect=fake_big_member), \
                 patch.object(driver, 'run', side_effect=fake_run), \
                 patch.object(driver, 'check', return_value=None), \
                 patch.object(driver.shutil, 'copy2', side_effect=fake_copy):
                out = b.phase_speech()
            sp = work / 'speech'
            slots = {name: argv for name, argv in calls
                     if name.startswith('speech-slot-')}
            self.assertEqual(len(slots), len(driver.SPEECH_TARGETS))
            for t in driver.SPEECH_TARGETS:
                argv = slots[f'speech-slot-{t}']
                self.assertIn('--work-dir', argv)
                self.assertEqual(
                    Path(argv[argv.index('--work-dir') + 1]),
                    sp / ('work-' + t))
                # speech_composite TARGETS carry the .dat extension.
                self.assertTrue((sp / 'members' / (t + '.dat')).is_file())
            self.assertEqual(out, work / 'speech-tricky-big')

    def test_resume_copies_finals_and_skips_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            reuse = Path(tmp) / 'reuse'
            (reuse / 'host-bam').mkdir(parents=True)
            (reuse / 'host-bam' / 'BAM.BIG').write_bytes(b'bam')
            (reuse / 'w-pipe-s5').mkdir(parents=True)
            (reuse / 'w-pipe-s5' / 'PIPE.BIG').write_bytes(b'pipe')
            (reuse / 'w-merq-routes').mkdir(parents=True)
            (reuse / 'w-merq-routes' / 'MERQ.BIG').write_bytes(b'merq')
            (reuse / 'w-alaska-b').mkdir(parents=True)
            (reuse / 'w-alaska-b' / 'scenery.json').write_bytes(b'{}')
            work = Path(tmp) / 'w'
            work.mkdir()
            b = driver.Build.__new__(driver.Build)
            b.W, b.skipped = work, {}
            with patch.object(driver, 'check') as check:
                b.resume_worlds(reuse)
            self.assertEqual(
                (work / 'host-bam' / 'BAM.BIG').read_bytes(), b'bam')
            self.assertEqual(
                (work / 'w-pipe-s5' / 'PIPE.BIG').read_bytes(), b'pipe')
            self.assertEqual(
                (work / 'w-merq-routes' / 'MERQ.BIG').read_bytes(), b'merq')
            self.assertEqual(check.call_count, 2)
            want = {}
            for c in driver.BUILD_COURSES:
                if c in ('pipe', 'merq'):
                    continue
                rel, _ = b.world_final_rel(c)
                key = 'mega' if c == 'mega' else f'resets-{c}'
                want[key] = f'reused worlds lack {rel}'
            self.assertEqual(b.skipped, want)

    def test_resume_layers_extra_dir_for_mega(self):
        with tempfile.TemporaryDirectory() as tmp:
            nine = Path(tmp) / 'nine'
            (nine / 'host-bam').mkdir(parents=True)
            (nine / 'host-bam' / 'BAM.BIG').write_bytes(b'bam')
            (nine / 'w-pipe-s5').mkdir(parents=True)
            (nine / 'w-pipe-s5' / 'PIPE.BIG').write_bytes(b'pipe-nine')
            (nine / 'w-alaska-b').mkdir(parents=True)
            (nine / 'w-alaska-b' / 'scenery.json').write_bytes(b'{}')
            mega = Path(tmp) / 'mega'
            (mega / 'w-mega-routes').mkdir(parents=True)
            (mega / 'w-mega-routes' / 'MEGA.BIG').write_bytes(b'mega')
            (mega / 'w-pipe-s5').mkdir(parents=True)
            (mega / 'w-pipe-s5' / 'PIPE.BIG').write_bytes(b'pipe-mega')
            work = Path(tmp) / 'w'
            work.mkdir()
            b = driver.Build.__new__(driver.Build)
            b.W, b.skipped = work, {}
            with patch.object(driver, 'check') as check:
                b.resume_worlds([nine, mega])
            self.assertEqual(
                (work / 'w-mega-routes' / 'MEGA.BIG').read_bytes(), b'mega')
            # first dir wins per file
            self.assertEqual(
                (work / 'w-pipe-s5' / 'PIPE.BIG').read_bytes(), b'pipe-nine')
            self.assertNotIn('mega', b.skipped)
            self.assertEqual(check.call_count, 2)

    def test_resume_carries_gems_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            reuse = Path(tmp) / 'reuse'
            (reuse / 'host-bam').mkdir(parents=True)
            (reuse / 'host-bam' / 'BAM.BIG').write_bytes(b'bam')
            (reuse / 'w-merq-routes').mkdir(parents=True)
            (reuse / 'w-merq-routes' / 'MERQ.BIG').write_bytes(b'merq')
            (reuse / 'w-merq-f11').mkdir(parents=True)
            (reuse / 'w-merq-f11' / 'MERQ.BIG').write_bytes(b'f11')
            (reuse / 'w-merq-b').mkdir(parents=True)
            (reuse / 'w-merq-b' / 'scenery.json').write_bytes(b'{}')
            (reuse / 'effects').mkdir(parents=True)
            (reuse / 'effects' / 'merquer.json').write_bytes(b'{}')
            (reuse / 'donor').mkdir(parents=True)
            (reuse / 'donor' / 'x').write_bytes(b'd')
            (reuse / 'w-alaska-b').mkdir(parents=True)
            (reuse / 'w-alaska-b' / 'scenery.json').write_bytes(b'{}')
            work = Path(tmp) / 'w'
            work.mkdir()
            b = driver.Build.__new__(driver.Build)
            b.W, b.skipped = work, {}
            with patch.object(driver, 'check', return_value=None):
                b.resume_worlds(reuse)
            self.assertEqual(
                (work / 'w-merq-f11' / 'MERQ.BIG').read_bytes(), b'f11')
            self.assertTrue((work / 'w-merq-b' / 'scenery.json').is_file())
            self.assertTrue((work / 'effects' / 'merquer.json').is_file())
            self.assertTrue((work / 'donor' / 'x').is_file())
            # skipped gem courses carry nothing; missing trees don't fail.
            self.assertFalse((work / 'w-gari-f11').exists())
            self.assertIn('resets-gari', b.skipped)

    def test_resume_needs_host_bam(self):
        with tempfile.TemporaryDirectory() as tmp:
            reuse = Path(tmp) / 'reuse'
            reuse.mkdir()
            work = Path(tmp) / 'w'
            work.mkdir()
            b = driver.Build.__new__(driver.Build)
            b.W, b.skipped = work, {}
            with self.assertRaisesRegex(SystemExit, 'host BAM'):
                b.resume_worlds(reuse)

    def test_resume_refuses_same_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'w'
            work.mkdir()
            b = driver.Build.__new__(driver.Build)
            b.W, b.skipped = work, {}
            with self.assertRaisesRegex(SystemExit, 'must differ'):
                b.resume_worlds(work)

    def test_from_phase_needs_reuse_worlds(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'w'
            with self.assertRaisesRegex(SystemExit, '--reuse-worlds'):
                driver.main(['--tricky-iso', 't.iso', '--ssx3-iso', 's.iso',
                             '--work', str(work), '--from-phase', 'speech'])
            self.assertFalse(work.exists())

    def test_music_convert_takes_staged_mus_and_offsets(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'w'
            work.mkdir()
            args = tricky_args(work, tricky_iso=work / 't.iso',
                               ssx3_iso=work / 's.iso')
            b = driver.Build.__new__(driver.Build)
            b.W, b.skipped, b.a = work, {}, args
            b.logs = work / 'logs'
            calls = []

            def fake_big_member(iso, big, want, out):
                out = Path(out)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b'SCHl' + b'\0' * 16 + b'SCHl' + b'\0' * 16)
                return out

            def fake_run(logdir, name, *argv, cwd=None):
                calls.append((name, list(argv)))
                return Path(logdir) / f'{name}.log'

            with patch.object(driver, 'big_member', side_effect=fake_big_member), \
                 patch.object(driver, 'run', side_effect=fake_run):
                pool = b.phase_music()
            self.assertEqual(pool, work / 'music' / 'pool')
            conv = [(n, a) for n, a in calls if n.startswith('music-convert-')]
            self.assertTrue(conv)
            for n, a in conv:
                song = n[len('music-convert-'):]
                ex, stage = work / 'music' / 'extract', work / 'music' / 'stage'
                self.assertEqual(
                    [str(x) for x in a[2:8]],
                    ['convert', str(ex / f'{song}.mpf'),
                     str(stage / f'{song}.mus'), str(stage / f'{song}.mpf'),
                     '--offsets', str(stage / f'{song}.tsv')])

    def test_gems_effects_use_recipe_stems(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'w'
            work.mkdir()
            args = tricky_args(work, tricky_iso=work / 't.iso',
                               ssx3_iso=work / 's.iso')
            b = driver.Build.__new__(driver.Build)
            b.W, b.a = work, args
            b.skipped = {f'resets-{c}': 'x'
                         for c in ('gari', 'snow', 'mesa', 'aloha')}
            b.logs = work / 'logs'
            for c in ('pipe', 'alaska', 'untrack'):
                d = work / f'w-{c}-s5'
                d.mkdir(parents=True)
                (d / f'{c.upper()}.BIG').write_bytes(b'x')
            calls = []

            def fake_run(logdir, name, *argv, cwd=None):
                calls.append((name, list(argv)))
                if '--out' in argv:
                    o = Path(argv[argv.index('--out') + 1])
                    if name == 'bake-gems':
                        # the real tool does not mkdir: the driver must
                        # have created gems/ or this write fails.
                        o.write_bytes(b'g')
                    else:
                        o.parent.mkdir(parents=True, exist_ok=True)
                        o.write_bytes(b'g')
                return Path(logdir) / f'{name}.log'

            with patch.object(driver, 'run', side_effect=fake_run):
                out = b.phase_gems()
            self.assertEqual(out, work / 'gems' / 'gems-483.tsv')
            specs = [a for n, a in calls if n == 'bake-gems'][0]
            courses = [specs[i + 1] for i, x in enumerate(specs)
                       if x == '--course']
            self.assertTrue(any('effects/merq.json' in s for s in courses))
            self.assertTrue(any('effects/elys.json' in s for s in courses))
            self.assertFalse(any('merquer.json' in s or 'elysium.json' in s
                                 for s in courses))
            gem_argv = [a for n, a in calls if n == 'gem-step'][0]
            db = gem_argv[gem_argv.index('--donor-bindings') + 1]
            self.assertTrue(str(db).endswith('donor-receipt.json'),
                            f'gem-step must read the JSON receipt, not the bindings dir: {db}')
            # F03: gem_step reads <donor>/<course>/(pbd|gsf|ssf) and
            # cross-checks their SHAs against the receipt: that is the
            # generated donor-bindings tree, not the adapter's W/donor
            # tree (pipe-ps2.gsf there has different bytes).
            donor = gem_argv[gem_argv.index('--donor') + 1]
            self.assertTrue(str(donor).endswith('donor-bindings'),
                            f'gem-step --donor must be the inventory tree: {donor}')
            # listdir, not exists(): this Mac's APFS is case-insensitive
            # but the gate container is not; the entry name must match.
            entries = os.listdir(work / 'gems' / 'worlds')
            for c in ('PIPE', 'ALASKA', 'UNTRACK'):
                self.assertIn(f'{c}.BIG', entries,
                              f'gem-step reads uppercase {c}.BIG from --worlds')

    def test_hud_atlas_reads_the_tricky_iso(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'w'
            work.mkdir()
            args = tricky_args(work, tricky_iso=work / 't.iso',
                               ssx3_iso=work / 's.iso')
            b = driver.Build.__new__(driver.Build)
            b.W, b.skipped, b.a = work, {}, args
            b.logs = work / 'logs'
            seen = []

            def fake_run(logdir, name, *argv, cwd=None):
                seen.append((name, list(argv)))
                atlas = Path(argv[-1])
                atlas.mkdir(parents=True, exist_ok=True)
                (atlas / 'trickyhud.bin').write_bytes(b'hud')
                return Path(logdir) / f'{name}.log'

            with patch.object(driver, 'run', side_effect=fake_run), \
                 patch.object(driver, 'check', return_value=None):
                out = b.phase_hud()
            self.assertEqual(out, work / 'hud' / 'atlas' / 'trickyhud.bin')
            self.assertEqual(len(seen), 1)
            self.assertEqual(Path(seen[0][1][2]), work / 't.iso')

    def test_speech_decisions_cover_all_targets(self):
        decisions = driver.T / 'data' / 'speech-decisions'
        for t in driver.SPEECH_TARGETS:
            self.assertTrue((decisions / (t + '.dat.decisions.tsv')).is_file())

    def _layout_built(self, work, english=True):
        # Mix of already-in-layout outputs (top of --work) and nested
        # intermediates, mirroring main()'s built dict.
        (work / 'speech-tricky-big').write_bytes(b'speech')
        (work / 'music-inf-tricky-big').write_bytes(b'musicinf')
        (work / 'tricky-song.wav').write_bytes(b'song')
        (work / 'tricky-resetvol.tsv').write_bytes(b'resetvol')
        pool = work / 'pool'
        pool.mkdir()
        (pool / 'music1-tkp4b').write_bytes(b'music1')
        (pool / 'music2-tkp4b').write_bytes(b'music2')
        hud = work / 'hud' / 'atlas'
        hud.mkdir(parents=True)
        (hud / 'trickyhud.bin').write_bytes(b'hud')
        gems = work / 'gems'
        gems.mkdir()
        (gems / 'gems-483.tsv').write_bytes(b'gems')
        (work / 'cp').mkdir()
        (work / 'cp' / 'COURSPIT.BIG').write_bytes(b'courspit')
        (work / 'menu' / 'DATA' / 'UI').mkdir(parents=True)
        eng = None
        if english:
            (work / 'english-silent-big').write_bytes(b'english')
            eng = work / 'english-silent-big'
        return {'worlds': {}, 'courspit': work / 'cp' / 'COURSPIT.BIG',
                'speech-tricky-big': work / 'speech-tricky-big',
                'music-tricky-big': pool / 'music1-tkp4b',
                'music2-tricky-big': pool / 'music2-tkp4b',
                'music-inf-tricky-big': work / 'music-inf-tricky-big',
                'english-silent-big': eng,
                'trickyhud.bin': hud / 'trickyhud.bin',
                'tricky-song.wav': work / 'tricky-song.wav',
                'tricky-gems.tsv': gems / 'gems-483.tsv',
                'tricky-resetvol.tsv': work / 'tricky-resetvol.tsv'}

    def _manifest_alias_targets(self, work):
        text = (work / 'm-tricky.txt').read_text()
        return [line.rsplit('/files/', 1)[1]
                for line in text.splitlines()
                if line.startswith('alias = ')]

    def test_layout_skips_same_file_copies(self):
        # F02: phases return paths already at the top of --work; copying
        # them onto themselves raised SameFileError. Small fixtures here.
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'w'
            work.mkdir()
            b = driver.Build.__new__(driver.Build)
            b.W, b.skipped, b.a = work, {}, None
            built = self._layout_built(work)
            lay = b.phase_layout(built)
            self.assertEqual(lay, work)
            # nested intermediates land beside the in-place payloads
            self.assertEqual((work / 'music-tricky-big').read_bytes(), b'music1')
            self.assertEqual((work / 'music2-tricky-big').read_bytes(), b'music2')
            self.assertEqual((work / 'trickyhud.bin').read_bytes(), b'hud')
            self.assertEqual((work / 'tricky-gems.tsv').read_bytes(), b'gems')
            # in-place payloads survive with their bytes
            self.assertEqual((work / 'speech-tricky-big').read_bytes(), b'speech')
            self.assertTrue((work / 'm-tricky.txt').is_file())
            self.assertTrue((work / 'tricky.env').is_file())

    def test_layout_english_alias_matches_payload(self):
        # F07: the ENGLISH alias is emitted only when english-silent-big
        # was built; otherwise an explicit omission names the gap.
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'w'
            work.mkdir()
            b = driver.Build.__new__(driver.Build)
            b.W, b.skipped, b.a = work, {}, None
            b.phase_layout(self._layout_built(work, english=True))
            self.assertIn('english-silent-big',
                          self._manifest_alias_targets(work))
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'w'
            work.mkdir()
            b = driver.Build.__new__(driver.Build)
            b.W, b.skipped, b.a = work, {}, None
            b.phase_layout(self._layout_built(work, english=False))
            targets = self._manifest_alias_targets(work)
            self.assertNotIn('english-silent-big', targets)
            text = (work / 'm-tricky.txt').read_text()
            self.assertIn('ENGLISH.BIG omitted', text)

    def test_layout_aliases_are_staged_payloads(self):
        # F07 shared schema: every manifest alias target must be a file
        # the stage step actually installs (TRICKY_DEVICE_FILES).
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'w'
            work.mkdir()
            b = driver.Build.__new__(driver.Build)
            b.W, b.skipped, b.a = work, {}, None
            b.phase_layout(self._layout_built(work, english=True))
            for target in self._manifest_alias_targets(work):
                self.assertIn(target, build.TRICKY_DEVICE_FILES,
                              f'manifest alias {target} is never staged')

    def test_resume_requires_alaska_scenery(self):
        # F04: gem_step always runs and reads w-alaska-b/scenery.json,
        # which is outside GEM_COURSES. Resume copies it, or fails fast.
        with tempfile.TemporaryDirectory() as tmp:
            reuse = Path(tmp) / 'reuse'
            (reuse / 'host-bam').mkdir(parents=True)
            (reuse / 'host-bam' / 'BAM.BIG').write_bytes(b'bam')
            work = Path(tmp) / 'w'
            work.mkdir()
            b = driver.Build.__new__(driver.Build)
            b.W, b.skipped, b.a = work, {}, None
            b.logs = work / 'logs'
            for c in driver.BUILD_COURSES:
                rel, _want = b.world_final_rel(c)
                src = reuse / rel
                src.parent.mkdir(parents=True, exist_ok=True)
                src.write_bytes(b'final-' + c.encode())
            for tree in ('effects', 'donor'):
                (reuse / tree).mkdir()
            (reuse / 'w-alaska-b').mkdir()
            (reuse / 'w-alaska-b' / 'scenery.json').write_text('{"alaska": 1}')
            with patch.object(driver, 'check', return_value=None):
                b.resume_worlds(reuse)
            self.assertEqual(
                (work / 'w-alaska-b' / 'scenery.json').read_text(),
                '{"alaska": 1}')
            # without Alaska rung B the resume refuses up front
            b2 = driver.Build.__new__(driver.Build)
            work2 = Path(tmp) / 'w2'
            work2.mkdir()
            b2.W, b2.skipped, b2.a = work2, {}, None
            b2.logs = work2 / 'logs'
            reuse2 = Path(tmp) / 'reuse2'
            (reuse2 / 'host-bam').mkdir(parents=True)
            (reuse2 / 'host-bam' / 'BAM.BIG').write_bytes(b'bam')
            for c in driver.BUILD_COURSES:
                rel, _want = b2.world_final_rel(c)
                src = reuse2 / rel
                src.parent.mkdir(parents=True, exist_ok=True)
                src.write_bytes(b'x')
            with patch.object(driver, 'check', return_value=None):
                with self.assertRaises(SystemExit) as ctx:
                    b2.resume_worlds(reuse2)
            self.assertIn('w-alaska-b', str(ctx.exception))

    def test_malformed_pins_fail_fast(self):
        # F01: a truncated pin can never match; reject before world work.
        with patch.object(driver, 'GEMS_345_SHA', 'c8407f43'):
            with self.assertRaises(SystemExit) as ctx:
                driver.validate_pins()
            self.assertIn('gems-345.tsv', str(ctx.exception))
        with patch.dict(driver.AUDIO_SHA, {'bogus': 'zzzz'}):
            with self.assertRaises(SystemExit):
                driver.validate_pins()

    def test_current_pins_validate(self):
        # F01: every exact-equality pin in the tree is a full 64-hex SHA.
        self.assertTrue(driver.validate_pins())


class ConverterVersionTest(unittest.TestCase):
    def fake_root(self, tmp):
        root = Path(tmp) / 'root'
        tricky = root / 'tools' / 'ssx3' / 'tricky'
        (tricky / 'tests').mkdir(parents=True)
        (tricky / '__pycache__').mkdir(parents=True)
        (tricky / 'a.py').write_text('print(1)\n')
        (tricky / 'data.tsv').write_text('a\tb\n')
        (tricky / 'tests' / 'b.py').write_text('test\n')
        (tricky / '__pycache__' / 'c.pyc').write_bytes(b'\x00')
        (root / 'tools' / 'ssx3' / 'build.py').write_text('build\n')
        return root

    def test_table_includes_code_and_data_but_not_tests_or_pycache(self):
        with tempfile.TemporaryDirectory() as tmp:
            table = dict(build.tricky_converter_table(self.fake_root(tmp)))
            self.assertIn('tools/ssx3/tricky/a.py', table)
            self.assertIn('tools/ssx3/tricky/data.tsv', table)
            self.assertIn('tools/ssx3/build.py', table)
            self.assertNotIn('tools/ssx3/tricky/tests/b.py', table)
            self.assertFalse([k for k in table if '__pycache__' in k])
            for digest in table.values():
                self.assertRegex(digest, r'^[0-9a-f]{64}$')

    def test_table_is_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            table = build.tricky_converter_table(self.fake_root(tmp))
            self.assertEqual([r for r, _ in table], sorted(r for r, _ in table))

    def test_version_stable_and_edit_sensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.fake_root(tmp)
            before = build.tricky_converter_version(root)
            self.assertRegex(before, r'^[0-9a-f]{64}$')
            self.assertEqual(before, build.tricky_converter_version(root))
            (root / 'tools' / 'ssx3' / 'tricky' / 'a.py').write_text('print(2)\n')
            edited = build.tricky_converter_version(root)
            self.assertNotEqual(before, edited)
            (root / 'tools' / 'ssx3' / 'tricky' / 'tests' / 'b.py').write_text('changed\n')
            self.assertEqual(edited, build.tricky_converter_version(root))

    def test_live_tree_hashes(self):
        table = build.tricky_converter_table()
        self.assertTrue(table)
        rels = [r for r, _ in table]
        self.assertEqual(rels, sorted(rels))
        self.assertIn('tools/ssx3/build.py', rels)
        self.assertTrue(any(r.startswith('tools/ssx3/tricky/') for r in rels))
        self.assertFalse([r for r in rels if '/tests/' in r or '__pycache__' in r])
        self.assertRegex(build.tricky_converter_version(), r'^[0-9a-f]{64}$')


class SkipKeysTest(unittest.TestCase):
    def fresh_run(self, tmp):
        return BuildTrickyTest().run_step(tmp)[:2]

    def toolchain(self, version='v1', digest='a' * 64):
        return {'ffmpeg': {'path': '/x/ffmpeg', 'version': version,
                           'sha256': digest},
                'vgmstream': None}

    def test_reuse_line_and_manifest_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, _ = self.fresh_run(tmp)
            record = json.loads((work / 'tricky_inputs.json').read_text())
            for key in ('builder_version', 'converter_version', 'host',
                        'jobs', 'wall_seconds', 'audio', 'pins', 'toolchain'):
                self.assertIn(key, record, key)
            self.assertEqual(set(record['host']), {'system', 'arch', 'cpus'})
            self.assertRegex(record['builder_version'], r'^[0-9a-f]{40}(-dirty)?$')
            line = build.tricky_reuse_line(record)
            self.assertRegex(
                line, r'^tricky: reusing outputs built \S+ by [0-9a-f]{12} '
                      r'\(converter [0-9a-f]{12}, \S+ min\)$')
            self.assertIn(record['converter_version'][:12], line)

    def test_converter_miss_prints_old_and_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, iso = self.fresh_run(tmp)
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True), \
                 patch.object(build, 'tricky_converter_version',
                              return_value='0' * 64):
                out = io.StringIO()
                with redirect_stdout(out):
                    current = build.tricky_current(work, iso, audio_on=False,
                                                   toolchain=self.toolchain())
                self.assertFalse(current)
                record = json.loads((work / 'tricky_inputs.json').read_text())
                self.assertIn('tricky: converter changed since %s (%s -> %s), rebuilding' % (
                    record['created_utc'], record['converter_version'][:12],
                    '0' * 12), out.getvalue())

    def test_pins_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, iso = self.fresh_run(tmp)
            record = json.loads((work / 'tricky_inputs.json').read_text())
            record['pins'] = {'changed': True}
            (work / 'tricky_inputs.json').write_text(json.dumps(record))
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
                self.assertFalse(build.tricky_current(work, iso, audio_on=False,
                                                      toolchain=self.toolchain()))

    def test_audio_mode_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, iso = self.fresh_run(tmp)
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
                out = io.StringIO()
                with redirect_stdout(out):
                    current = build.tricky_current(work, iso, audio_on=True,
                                                   toolchain=self.toolchain())
                self.assertFalse(current)
                self.assertIn('audio mode changed', out.getvalue())

    def test_toolchain_miss_and_audio_off_ignores_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, iso = self.fresh_run(tmp)
            record = json.loads((work / 'tricky_inputs.json').read_text())
            record['audio'] = 'on'
            record['toolchain'] = self.toolchain(version='v1')
            (work / 'tricky_inputs.json').write_text(json.dumps(record))
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
                out = io.StringIO()
                with redirect_stdout(out):
                    changed = build.tricky_current(
                        work, iso, audio_on=True,
                        toolchain=self.toolchain(version='v2', digest='b' * 64))
                self.assertFalse(changed)
                self.assertIn('toolchain changed (ffmpeg)', out.getvalue())
                same = build.tricky_current(work, iso, audio_on=True,
                                            toolchain=self.toolchain(version='v1'))
                self.assertTrue(same)
                # With audio off the tools are ignored.
                ignored = build.tricky_current(
                    work, iso, audio_on=False,
                    toolchain=self.toolchain(version='v2', digest='b' * 64))
                self.assertFalse(ignored)  # audio mode differs (on vs off)
            record['audio'] = 'off'
            record['toolchain'] = self.toolchain(version='v1')
            (work / 'tricky_inputs.json').write_text(json.dumps(record))
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
                self.assertTrue(build.tricky_current(
                    work, iso, audio_on=False,
                    toolchain=self.toolchain(version='v9', digest='c' * 64)))

    def test_toolchain_key_ignores_path(self):
        self.assertEqual(
            build.tricky_toolchain_key({'path': '/a', 'version': 'v',
                                        'sha256': 's', 'extra': 1}),
            build.tricky_toolchain_key({'path': '/b', 'version': 'v',
                                        'sha256': 's'}))
        self.assertIsNone(build.tricky_toolchain_key(None))


class ImportTrickyTest(unittest.TestCase):
    def source_dir(self, tmp, iso_digest, audio='off', converter=None,
                   wall_seconds=456.0):
        src = Path(tmp) / 'src'
        src.mkdir()
        for name in build.TRICKY_DEVICE_FILES:
            (src / name).write_bytes(name.encode() + b'-device')
        (src / 'tricky-ov/DATA/WORLDS').mkdir(parents=True)
        (src / 'tricky-ov/DATA/WORLDS/GARI.BIG').write_bytes(b'gari-world')
        for name in ('build-gari', 'donor', 'w-pipe-s5', 'logs', 'effects'):
            (src / name).mkdir()
            (src / name / 'intermediate.bin').write_bytes(b'x' * 100)
        (src / 'tools.json').write_text(json.dumps({
            'tricky': {'sha256': iso_digest}, 'ssx3': {'sha256': 's' * 64},
            'audio': audio, 'pins': {'p': 1},
            'toolchain': {'ffmpeg': None, 'vgmstream': None},
            'converter_version': converter, 'jobs': 12,
            'wall_seconds': wall_seconds,
            'course_walls': {'pipe': 10.0}, 'phase_walls': {'menu': 1.0}}))
        return src

    def import_args(self, work, **over):
        base = dict(work=work, jobs=2, app_id=build.PUBLIC_APP_ID,
                    tricky_audio='auto', ffmpeg='ffmpeg-pinned',
                    vgmstream='vgm-pinned')
        base.update(over)
        return types.SimpleNamespace(**base)

    def test_accept_copies_only_device_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, ssx3 = prepared_work(tmp)
            iso = Path(tmp) / 'tricky.iso'
            make_tricky_iso(iso)
            src = self.source_dir(tmp, build.sha(iso),
                                  converter=build.tricky_converter_version())
            args = self.import_args(work)
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
                out = io.StringIO()
                with redirect_stdout(out):
                    result = build.import_tricky(args, iso, src)
                self.assertIn('imported', out.getvalue())
            self.assertEqual(Path(result), work / 'tricky_inputs.json')
            dest = work / 'tricky'
            for name in build.TRICKY_DEVICE_FILES:
                self.assertTrue((dest / name).is_file(), name)
            self.assertTrue((dest / 'tricky-ov/DATA/WORLDS/GARI.BIG').is_file())
            for name in ('build-gari', 'donor', 'w-pipe-s5', 'logs', 'effects'):
                self.assertFalse((dest / name).exists(), name)
            record = json.loads((work / 'tricky_inputs.json').read_text())
            self.assertEqual(record['converter_version'],
                             build.tricky_converter_version())
            self.assertEqual(record['audio'], 'off')
            self.assertEqual(record['jobs'], 2)
            self.assertEqual(record['wall_seconds'], 456.0)
            self.assertEqual(record['course_walls'], {'pipe': 10.0})
            self.assertEqual(record['phase_walls'], {'menu': 1.0})
            self.assertEqual(set(record['host']), {'system', 'arch', 'cpus'})
            self.assertRegex(record['builder_version'], r'^[0-9a-f]{40}(-dirty)?$')
            # The imported layer verifies and skips.
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
                self.assertTrue(build.tricky_current(
                    work, iso, audio_on=False,
                    toolchain={'ffmpeg': None, 'vgmstream': None}))
                out = io.StringIO()
                with redirect_stdout(out):
                    build.build_tricky(self.import_args(work), iso)
                self.assertIn('reusing outputs built', out.getvalue())

    def test_refuse_disc_mismatch_names_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, _ = prepared_work(tmp)
            iso = Path(tmp) / 'tricky.iso'
            make_tricky_iso(iso)
            src = self.source_dir(tmp, '0' * 64,
                                  converter=build.tricky_converter_version())
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
                with self.assertRaisesRegex(ValueError,
                                            r'tools\.json:tricky\.sha256'):
                    build.import_tricky(self.import_args(work), iso, src)

    def test_refuse_converter_mismatch_names_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, _ = prepared_work(tmp)
            iso = Path(tmp) / 'tricky.iso'
            make_tricky_iso(iso)
            src = self.source_dir(tmp, build.sha(iso), converter='0' * 64)
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
                with self.assertRaisesRegex(ValueError,
                                            r'tools\.json:converter_version'):
                    build.import_tricky(self.import_args(work), iso, src)

    def test_refuse_audio_mismatch_names_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, _ = prepared_work(tmp)
            iso = Path(tmp) / 'tricky.iso'
            make_tricky_iso(iso)
            src = self.source_dir(tmp, build.sha(iso), audio='on',
                                  converter=build.tricky_converter_version())
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
                with self.assertRaisesRegex(ValueError, r'tools\.json:audio'):
                    build.import_tricky(self.import_args(work), iso, src)

    def test_refuse_missing_receipt_and_existing_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, _ = prepared_work(tmp)
            iso = Path(tmp) / 'tricky.iso'
            make_tricky_iso(iso)
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
                with self.assertRaisesRegex(ValueError, 'no tools.json'):
                    build.import_tricky(self.import_args(work), iso,
                                        Path(tmp) / 'absent')
            src = self.source_dir(tmp, build.sha(iso),
                                  converter=build.tricky_converter_version())
            (work / 'tricky').mkdir()
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
                with self.assertRaisesRegex(ValueError, 'existing tricky dir'):
                    build.import_tricky(self.import_args(work), iso, src)


class ImportDispatchTest(unittest.TestCase):
    def test_android_tricky_from_imports_instead_of_building(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            work.mkdir()
            iso = Path(tmp) / 'SSX3.iso'
            iso.write_bytes(b'ssx3')
            tricky = Path(tmp) / 'tricky.iso'
            tricky.write_bytes(b'tricky')
            src = Path(tmp) / 'src'
            src.mkdir()
            args = tricky_args(work, ssx3_iso=iso, tricky_iso=tricky,
                               install=False, tricky_from=src)
            calls = []
            with patch.object(build, 'ensure_android_toolchain'), \
                 patch.object(build, 'doctor'), \
                 patch.object(build, 'validate_tricky_iso', return_value=tricky), \
                 patch.object(build, 'prepare_current', return_value=False), \
                 patch.object(build, 'prepare', side_effect=lambda a: calls.append('prepare')), \
                 patch.object(build, 'android_libs_current', return_value=True), \
                 patch.object(build, 'import_tricky',
                              side_effect=lambda a, i, s: calls.append('import')), \
                 patch.object(build, 'build_tricky',
                              side_effect=AssertionError('must not rebuild')), \
                 patch.object(build, 'android', side_effect=lambda a: calls.append('android')):
                build.android_onecmd(args)
            self.assertEqual(calls, ['prepare', 'import', 'android'])

    def test_android_without_tricky_from_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            work.mkdir()
            iso = Path(tmp) / 'SSX3.iso'
            iso.write_bytes(b'ssx3')
            tricky = Path(tmp) / 'tricky.iso'
            tricky.write_bytes(b'tricky')
            args = tricky_args(work, ssx3_iso=iso, tricky_iso=tricky,
                               install=False)
            calls = []
            with patch.object(build, 'ensure_android_toolchain'), \
                 patch.object(build, 'doctor'), \
                 patch.object(build, 'validate_tricky_iso', return_value=tricky), \
                 patch.object(build, 'prepare_current', return_value=False), \
                 patch.object(build, 'prepare', side_effect=lambda a: calls.append('prepare')), \
                 patch.object(build, 'android_libs_current', return_value=True), \
                 patch.object(build, 'build_tricky',
                              side_effect=lambda a, i: calls.append('tricky')), \
                 patch.object(build, 'android', side_effect=lambda a: calls.append('android')):
                build.android_onecmd(args)
            self.assertEqual(calls, ['prepare', 'tricky', 'android'])


class ActivateTrickyEnvTest(unittest.TestCase):
    DRIVER_ENV = ('# build_tricky.py: Tricky env layer (Tricky only; layered over ps2x.env).\n'
                  'PS2X_CD_OVERLAY=/storage/emulated/0/Android/data/com.ssx3.recomp/files/tricky-ov\n'
                  'PS2X_SSX3_COURSE_MANIFEST=/storage/emulated/0/Android/data/com.ssx3.recomp/files/m-tricky.txt\n'
                  'PS2X_SSX3_COURSE_PICKER=1\n'
                  'PS2X_SSX3_TRICKY_MENU=1\n'
                  'PS2X_SSX3_TRICKY_HUD=1\n'
                  'PS2X_SSX3_TRICKY_HUD_ART=/storage/emulated/0/Android/data/com.ssx3.recomp/files/trickyhud.bin\n'
                  'PS2X_SSX3_TRICKY_MUSIC_INF=/storage/emulated/0/Android/data/com.ssx3.recomp/files/music-inf-tricky-big\n'
                  'PS2X_SSX3_TRICKY_SONG=/storage/emulated/0/Android/data/com.ssx3.recomp/files/tricky-song.wav\n'
                  'PS2X_SSX3_TRICKY_GEMS=1\n'
                  'PS2X_SSX3_TRICKY_GEMS_TABLE=/storage/emulated/0/Android/data/com.ssx3.recomp/files/tricky-gems.tsv\n'
                  'PS2X_SSX3_TRICKY_RESETVOL=1\n'
                  'PS2X_SSX3_TRICKY_RESETVOL_TABLE=/storage/emulated/0/Android/data/com.ssx3.recomp/files/tricky-resetvol.tsv\n'
                  'PS2X_SSX3_MEGA_LAPS=1\n'
                  'PS2X_SSX3_TRICKY_DJ_GATE=1\n'
                  'PS2X_SSX3_TRICKY_MC=1\n')

    LAYER_KEYS = ('PS2X_CD_OVERLAY=', 'PS2X_SSX3_COURSE_MANIFEST=',
                  'PS2X_SSX3_COURSE_PICKER=', 'PS2X_SSX3_TRICKY_MENU=',
                  'PS2X_SSX3_TRICKY_HUD_ART=', 'PS2X_SSX3_TRICKY_MUSIC_INF=',
                  'PS2X_SSX3_TRICKY_SONG=', 'PS2X_SSX3_TRICKY_GEMS_TABLE=',
                  'PS2X_SSX3_TRICKY_RESETVOL_TABLE=', 'PS2X_SSX3_MEGA_LAPS=',
                  'PS2X_SSX3_TRICKY_DJ_GATE=', 'PS2X_SSX3_TRICKY_MC=')

    def test_merge_appends_layer_rows_with_picker_and_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / 'ps2x.env'
            base.write_text('PS2X_SOUND=1\n')
            staged = Path(tmp) / 'tricky.env'
            staged.write_text(self.DRIVER_ENV)
            self.assertTrue(build.activate_tricky_env(base, staged))
            text = base.read_text()
            self.assertIn('PS2X_SOUND=1', text)
            self.assertIn('PS2X_SSX3_COURSE_PICKER=1', text)
            self.assertIn('PS2X_SSX3_TRICKY_MENU=1', text)
            self.assertIn('PS2X_SSX3_TRICKY_SONG=', text)
            self.assertIn('PS2X_SSX3_TRICKY_MUSIC_INF=', text)
            self.assertIn('# Tricky layer', text)

    def test_merge_audio_off_layer_has_no_audio_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / 'ps2x.env'
            base.write_text('PS2X_SOUND=1\n')
            staged = Path(tmp) / 'tricky.env'
            staged.write_text(''.join(
                line for line in self.DRIVER_ENV.splitlines(keepends=True)
                if 'MUSIC_INF=' not in line and 'TRICKY_SONG=' not in line))
            self.assertTrue(build.activate_tricky_env(base, staged))
            text = base.read_text()
            self.assertIn('PS2X_SSX3_COURSE_PICKER=1', text)
            self.assertNotIn('TRICKY_SONG', text)
            self.assertNotIn('TRICKY_MUSIC_INF', text)

    def test_merge_without_layer_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / 'ps2x.env'
            base.write_text('PS2X_SOUND=1\n')
            self.assertFalse(build.activate_tricky_env(base, Path(tmp) / 'absent.env'))
            self.assertEqual(base.read_text(), 'PS2X_SOUND=1\n')


class StageActivateTest(unittest.TestCase):
    def driver_layer(self, work):
        """Replace the stub layer with driver-form content + fix the record."""
        tricky = work / 'tricky'
        (tricky / 'm-tricky.txt').write_text(
            '# build_tricky.py: course rows for the built courses only.\n'
            'mode = Tricky\n'
            'alias = /DATA/AUDIO/SPEECH.BIG:/storage/emulated/0/Android/data/'
            'com.ssx3.recomp/files/speech-tricky-big\n'
            'row = 11:PIPE:Pipedream\n'
            'poke = 0x47BDB0:data/ui/courspic.big:data/ui/courspit.big\n')
        (tricky / 'tricky.env').write_text(ActivateTrickyEnvTest.DRIVER_ENV)
        record = json.loads((work / 'tricky_inputs.json').read_text())
        for name in ('m-tricky.txt', 'tricky.env'):
            candidate = tricky / name
            record['outputs'][name] = {'sha256': build.sha2(candidate),
                                       'bytes': candidate.stat().st_size}
        (work / 'tricky_inputs.json').write_text(json.dumps(record))

    def test_stage_merges_tricky_env_with_picker_and_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = staged_work(tmp)
            self.driver_layer(work)
            args = tricky_args(work, app_id='com.example.game', step='stage')
            files = build.stage(args)
            env = (files / 'ps2x.env').read_text()
            for key in ActivateTrickyEnvTest.LAYER_KEYS:
                self.assertIn(key, env)
            self.assertIn('com.example.game', env)
            self.assertNotIn('com.ssx3.recomp', env)

    def test_stage_without_layer_has_no_tricky_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, _ = prepared_work(tmp)
            (work / 'app-id.txt').write_text('com.example.game\n')
            (work / 'android-resources').mkdir()
            args = tricky_args(work, app_id='com.example.game', step='stage')
            files = build.stage(args)
            env = (files / 'ps2x.env').read_text()
            for key in ActivateTrickyEnvTest.LAYER_KEYS:
                self.assertNotIn(key, env)


class StageIosTrickyTest(unittest.TestCase):
    def ios_layer_source(self, tmp, iso_digest, audio='off'):
        src = Path(tmp) / 'src'
        src.mkdir()
        manifest = ('# build_tricky.py: course rows for the built courses only.\n'
                    'mode = Tricky\n'
                    'alias = /DATA/AUDIO/SPEECH.BIG:/storage/emulated/0/Android/data/'
                    'com.ssx3.recomp/files/speech-tricky-big\n'
                    'row = 11:PIPE:Pipedream\n'
                    'poke = 0x47BDB0:data/ui/courspic.big:data/ui/courspit.big\n')
        env = ''.join(
            line for line in ActivateTrickyEnvTest.DRIVER_ENV.splitlines(keepends=True)
            if audio == 'on' or ('MUSIC_INF=' not in line and 'TRICKY_SONG=' not in line))
        payloads = {'m-tricky.txt': manifest, 'tricky.env': env}
        for name in build.TRICKY_DEVICE_FILES:
            (src / name).write_text(payloads.get(name, name + '-device'))
        (src / 'tricky-ov/DATA/WORLDS').mkdir(parents=True)
        (src / 'tricky-ov/DATA/WORLDS/PIPE.BIG').write_bytes(b'pipe-world')
        (src / 'tools.json').write_text(json.dumps({
            'tricky': {'sha256': iso_digest}, 'ssx3': {'sha256': 's' * 64},
            'audio': audio, 'pins': {}, 'toolchain': {'ffmpeg': None, 'vgmstream': None},
            'converter_version': build.tricky_converter_version(), 'jobs': 12}))
        return src

    def import_ios_layer(self, tmp, audio='off'):
        work, _ = prepared_work(tmp)
        iso = Path(tmp) / 'tricky.iso'
        make_tricky_iso(iso)
        src = self.ios_layer_source(tmp, build.sha(iso), audio=audio)
        args = types.SimpleNamespace(work=work, jobs=2, tricky_audio='auto',
                                     ffmpeg='ffmpeg-pinned', vgmstream='vgm-pinned')
        with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True):
            build.import_tricky(args, iso, src)
        return work

    def test_ios_stage_uses_bundle_paths_and_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = self.import_ios_layer(tmp)
            app = Path(tmp) / 'app'
            app.mkdir()
            (app / 'ps2x.env').write_text('PS2X_SOUND=1\n')
            args = types.SimpleNamespace(work=work)
            staged, trees = build.stage_ios_tricky(args, app)
            self.assertTrue((app / 'tricky/tricky-ov/DATA/WORLDS/PIPE.BIG').is_file())
            manifest = (app / 'tricky/m-tricky.txt').read_text()
            self.assertIn('alias = /DATA/AUDIO/SPEECH.BIG:speech-tricky-big', manifest)
            self.assertNotIn('/storage/emulated', manifest)
            env = (app / 'tricky/tricky.env').read_text()
            self.assertIn('PS2X_CD_OVERLAY=${BUNDLE}/tricky/tricky-ov', env)
            self.assertIn('PS2X_SSX3_COURSE_MANIFEST=${BUNDLE}/tricky/m-tricky.txt', env)
            self.assertNotIn('/storage/emulated', env)
            self.assertTrue(build.activate_tricky_env(app / 'ps2x.env',
                                                      app / 'tricky/tricky.env'))
            merged = (app / 'ps2x.env').read_text()
            self.assertIn('PS2X_SSX3_COURSE_PICKER=1', merged)
            self.assertIn('${BUNDLE}/tricky/', merged)
            self.assertEqual([name for _, name in staged if name == 'm-tricky.txt'],
                             ['m-tricky.txt'])
            self.assertIn('tricky-ov', trees)
            # Tampering with the built source fails closed.
            (work / 'tricky/tricky-gems.tsv').write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'changed since build_tricky'):
                build.stage_ios_tricky(args, app)

    def test_ios_stage_without_layer_is_stock(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, _ = prepared_work(tmp)
            app = Path(tmp) / 'app'
            app.mkdir()
            args = types.SimpleNamespace(work=work)
            self.assertEqual(build.stage_ios_tricky(args, app), ([], []))
            self.assertFalse((app / 'tricky').exists())


class CheckTrickyCliTest(unittest.TestCase):
    def check(self, **over):
        base = dict(step='android', from_dir=None, tricky_from=None, tricky_iso=None)
        base.update(over)
        failures = []
        build.check_tricky_cli(types.SimpleNamespace(**base), failures.append)
        return failures

    def test_tricky_step_takes_from_not_tricky_from(self):
        self.assertEqual(self.check(step='tricky'), [])
        self.assertEqual(self.check(step='tricky', from_dir=Path('/x')), [])
        self.assertTrue(self.check(step='tricky', tricky_from=Path('/x')))

    def test_android_ios_take_tricky_from_not_from(self):
        self.assertEqual(self.check(step='android', tricky_from=Path('/x'),
                                    tricky_iso=Path('/i')), [])
        self.assertEqual(self.check(step='ios', tricky_from=Path('/x'),
                                    tricky_iso=Path('/i')), [])
        self.assertTrue(self.check(step='android', from_dir=Path('/x')))
        self.assertTrue(self.check(step='ios', from_dir=Path('/x')))
        self.assertTrue(self.check(step='prepare', from_dir=Path('/x')))
        self.assertTrue(self.check(step='prepare', tricky_from=Path('/x'),
                                   tricky_iso=Path('/i')))

    def test_tricky_from_needs_the_disc(self):
        self.assertTrue(self.check(step='android', tricky_from=Path('/x')))


class IosOneCommandTrickyTest(unittest.TestCase):
    def onecmd_args(self, work, **over):
        base = dict(work=work, ssx3_iso=None, tricky_iso=None, tricky_from=None,
                    install=False, team='T' * 10, jobs=2, app_id=build.PUBLIC_APP_ID,
                    tricky_audio='auto', dry_run=False)
        base.update(over)
        return types.SimpleNamespace(**base)

    def run_onecmd(self, tmp, calls, **over):
        work = Path(tmp) / 'work'
        work.mkdir()
        iso = Path(tmp) / 'SSX3.iso'
        iso.write_bytes(b'ssx3')
        tricky = Path(tmp) / 'tricky.iso'
        tricky.write_bytes(b'tricky')
        args = self.onecmd_args(work, ssx3_iso=iso, tricky_iso=tricky, **over)
        with patch.object(build, 'check_team'), \
             patch.object(build, 'doctor'), \
             patch.object(build, 'validate_tricky_iso', return_value=tricky), \
             patch.object(build, 'prepare_current', return_value=False), \
             patch.object(build, 'prepare', side_effect=lambda a: calls.append('prepare')), \
             patch.object(build, 'ios_libs_current', return_value=True), \
             patch.object(build, 'import_tricky',
                          side_effect=lambda a, i, s: calls.append('import')), \
             patch.object(build, 'build_tricky',
                          side_effect=lambda a, i: calls.append('tricky')), \
             patch.object(build, 'ios', side_effect=lambda a: calls.append('ios')):
            build.ios_onecmd(args)
        return calls

    def test_ios_tricky_iso_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_onecmd(tmp, []), ['prepare', 'tricky', 'ios'])

    def test_ios_tricky_from_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'src'
            src.mkdir()
            self.assertEqual(self.run_onecmd(tmp, [], tricky_from=src),
                             ['prepare', 'import', 'ios'])


if __name__ == '__main__':
    unittest.main()
