"""Tricky builder step: disc identity, record/resume, stage/install wiring.

No network, no real discs: Tricky ISOs under test are synthetic
(ISO9660 structures only) and the converter driver is a stub honoring
the proposed driver contract. The stock-only path stays untouched:
with no Tricky layer, stage/install behave exactly as before.
"""
import importlib.util
import io
import json
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
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--tricky-iso')
p.add_argument('--ssx3-iso')
p.add_argument('--work')
p.add_argument('--jobs', default='8')
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
'''


def write_stub_driver(path):
    path = Path(path)
    path.write_text(STUB_DRIVER)
    path.chmod(0o755)
    return path


def tricky_args(work, **over):
    base = dict(work=work, jobs=2, app_id=build.PUBLIC_APP_ID,
                skip_fonts=True, stock_dump=None, step='tricky', iso=None,
                prebuilt=False)
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
        {'iso': str(ssx3), 'slim_iso': str(work / 'SSX3-slim.iso')}))
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
            subprocess.run([sys.executable, str(driver), '--work', str(work / 'tricky')],
                           check=True)
            with patch.dict(build.TRICKY_DISCS, pin_disc(iso), clear=True), \
                 patch.object(build, 'TRICKY_DRIVER', driver):
                self.assertTrue(build.tricky_current(work, iso))
                out = io.StringIO()
                with redirect_stdout(out):
                    build.build_tricky(args, iso)
                self.assertIn('skipping', out.getvalue())

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
            with patch.object(build, 'ensure_android_toolchain'), \
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
            with patch.object(build, 'ensure_android_toolchain'), \
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


if __name__ == '__main__':
    unittest.main()
