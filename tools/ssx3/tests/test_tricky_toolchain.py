"""Tricky audio toolchain: optional local tools, --tricky-audio modes.

No network, no real discs: local tools are stub shell scripts, ISOs are
synthetic, and the preflight's disc/tool lookups are stubbed. The only
real binaries touched are the ones under test via PATH faking.
"""
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import types
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / 'tricky'))

SPEC = importlib.util.spec_from_file_location('ssx3_build', HERE / 'build.py')
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)

import tool_paths
from test_tricky_step import make_tricky_iso


ROOT = HERE.parents[1]


def load_driver():
    spec = importlib.util.spec_from_file_location(
        'tricky_driver', HERE / 'tricky' / 'build_tricky.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_exe(path, text='#!/bin/sh\necho stub\n'):
    path = Path(path)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


FFMPEG_STUB = """#!/bin/sh
if [ "$1" = "-version" ]; then
  echo "ffmpeg version 9.9-test Copyright (c) 2000-2026 the FFmpeg developers"
  exit 0
fi
exit 0
"""

VGMSTREAM_STUB = """#!/bin/sh
if [ "$1" = "-h" ]; then
  echo "vgmstream CLI decoder r2117 test"
  exit 0
fi
if [ "$1" = "-o" ]; then
  printf 'RIFF....WAVEjunk' > "$2"
  exit 0
fi
exit 0
"""

OLD_FFMPEG_STUB = """#!/bin/sh
echo "ffmpeg version 5.1-ancient"
exit 0
"""


def scrub_env():
    """PATH + tool env scrubbed AND well-known prefixes neutralized, so
    tests never see the machine's real brew/apt tools."""
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch.dict(os.environ, {tool_paths.VGMSTREAM_ENV: '',
                                                 tool_paths.FFMPEG_ENV: '',
                                                 'PATH': '/nonexistent-tk-test'}))
    stack.enter_context(patch.object(tool_paths, 'LOCAL_PREFIXES', ()))
    return stack


class Pep723LockTest(unittest.TestCase):
    def header(self):
        import re
        text = (HERE / 'build.py').read_text()
        requires = re.search(r'# requires-python = "([^"]+)"', text).group(1)
        deps = re.search(r'# dependencies = \[(.*?)\]', text,
                         re.S).group(1)
        return requires, re.findall(r'"([^"]+)"', deps)

    def test_pins_are_exact(self):
        import re
        requires, deps = self.header()
        self.assertEqual(requires, '>=3.12,<3.13')
        self.assertEqual(sorted(deps), [
            'Pillow==13.0.0',
            'numpy==2.5.4',
        ])

    def test_no_fetched_audio_tools_in_the_lock(self):
        _, deps = self.header()
        self.assertFalse([d for d in deps if 'ffmpeg' in d.lower()],
                         'ffmpeg must be local-only, never a wheel pin')

    def test_python_version_pin_satisfies_requires_python(self):
        version = (ROOT / '.python-version').read_text().strip()
        self.assertRegex(version, r'^\d+\.\d+\.\d+$')
        major, minor, _ = (int(x) for x in version.split('.'))
        self.assertEqual((major, minor), (3, 12))

    def test_lock_matches_header_with_hashes(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest('tomllib needs Python 3.11+')
        _, deps = self.header()
        lock = tomllib.loads((HERE / 'build.py.lock').read_text())
        manifest = {r['name']: r.get('specifier', '') for r in
                    lock['manifest']['requirements']}
        for dep in deps:
            name, pin = dep.split('==')
            self.assertEqual(manifest[name.lower()], '==' + pin)
        packages = {p['name']: p for p in lock['package']}
        for dep in deps:
            name = dep.split('==')[0].lower()
            package = packages[name]
            self.assertEqual(package['version'], dep.split('==')[1])
            artifacts = (([package['sdist']] if 'sdist' in package else [])
                         + package.get('wheels', []))
            self.assertTrue(artifacts, name)
            for artifact in artifacts:
                self.assertRegex(artifact['hash'], r'^sha256:[0-9a-f]{64}$')


class DepsLockAgreementTest(unittest.TestCase):
    def test_tool_paths_agrees_with_deps_lock(self):
        lock = json.loads((ROOT / 'deps.lock.json').read_text())
        section = lock['tricky_tools']
        self.assertIn('locally installed', section['policy'])
        self.assertNotIn('vgmstream_bins', section)
        self.assertEqual(section['ffmpeg']['min_version'],
                         '%d.%d' % tool_paths.FFMPEG_MIN_VERSION)
        self.assertEqual(section['vgmstream']['min_version'],
                         'r%d' % tool_paths.VGMSTREAM_MIN_R)
        self.assertTrue(section['ffmpeg']['install'])
        self.assertTrue(section['vgmstream']['install'])


class ResolveTest(unittest.TestCase):
    def test_explicit_beats_env_beats_path(self):
        with tempfile.TemporaryDirectory() as tmp, scrub_env():
            bindir = Path(tmp) / 'bin'
            bindir.mkdir()
            path_exe = make_exe(bindir / 'ffmpeg', FFMPEG_STUB)
            explicit = make_exe(Path(tmp) / 'explicit-ffmpeg', FFMPEG_STUB)
            env_exe = make_exe(Path(tmp) / 'env-ffmpeg', FFMPEG_STUB)
            with patch.dict(os.environ, {'PATH': str(bindir),
                                          tool_paths.FFMPEG_ENV: str(env_exe)}):
                ident, _ = tool_paths.resolve_ffmpeg(str(explicit))
                self.assertEqual(ident['path'], str(explicit))
                ident, _ = tool_paths.resolve_ffmpeg()
                self.assertEqual(ident['path'], str(env_exe))
            with patch.dict(os.environ, {'PATH': str(bindir)}):
                ident, _ = tool_paths.resolve_ffmpeg()
                self.assertEqual(ident['path'], str(path_exe))

    def test_missing_explicit_fails_with_the_fix(self):
        with tempfile.TemporaryDirectory() as tmp, scrub_env():
            with self.assertRaisesRegex(ValueError, 'install ffmpeg'):
                tool_paths.resolve_ffmpeg(str(Path(tmp) / 'missing'))

    def test_old_local_ffmpeg_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, scrub_env():
            old = make_exe(Path(tmp) / 'ffmpeg', OLD_FFMPEG_STUB)
            with patch.dict(os.environ, {'PATH': tmp}):
                ident, reason = tool_paths.resolve_ffmpeg()
                self.assertIsNone(ident)
                self.assertIn('too old', reason)

    def test_absent_tools_return_none_not_raise(self):
        with scrub_env():
            ident, reason = tool_paths.resolve_ffmpeg()
            self.assertIsNone(ident)
            self.assertIn('no ffmpeg', reason)
            path, vident, reason = tool_paths.resolve_vgmstream()
            self.assertIsNone(path)
            self.assertIsNone(vident)
            self.assertIn('no vgmstream', reason)

    def test_ffmpeg_path_helper_raises_when_off(self):
        with scrub_env():
            with self.assertRaisesRegex(ValueError, 'no ffmpeg'):
                tool_paths.ffmpeg_path()

    def test_explicit_vgmstream_is_probed(self):
        with tempfile.TemporaryDirectory() as tmp, scrub_env():
            exe = make_exe(Path(tmp) / 'vgmstream-cli', VGMSTREAM_STUB)
            path, ident, _ = tool_paths.resolve_vgmstream(str(exe))
            self.assertEqual(path, exe)
            self.assertIn('r2117', ident['version'])

    def test_old_vgmstream_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, scrub_env():
            old = make_exe(Path(tmp) / 'vgm', '#!/bin/sh\necho "vgmstream CLI decoder r100"\n')
            with self.assertRaisesRegex(ValueError, 'too old'):
                tool_paths.probe_vgmstream(old)


class NoHostToolLiteralsTest(unittest.TestCase):
    def argv0_literals(self, path):
        """String literals used as a subprocess argv[0]."""
        import ast
        tree = ast.parse(Path(path).read_text())
        found = []

        class Visitor(ast.NodeVisitor):
            def visit_Call(self, node):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in (
                        'run', 'call', 'check_output', 'check_call', 'Popen'):
                    if node.args and isinstance(node.args[0], ast.List) \
                            and node.args[0].elts \
                            and isinstance(node.args[0].elts[0], ast.Constant):
                        found.append(node.args[0].elts[0].value)
                self.generic_visit(node)

        Visitor().visit(tree)
        return found

    def test_no_host_tool_argv0_in_tricky(self):
        banned = {'ffmpeg', 'ffprobe', 'bsdtar', 'vgmstream-cli'}
        # shared/verify_inputs.py is a lab-only cross-check that shells
        # bsdtar BY DESIGN (it diffs our reader against it); the driver
        # and builder never invoke it. tool_paths.which lookups are not
        # subprocess argv[0] and are covered by ResolveTest instead.
        exempt = {'tricky/shared/verify_inputs.py'}
        offenders = {}
        for path in sorted((HERE / 'tricky').rglob('*.py')):
            rel = str(path.relative_to(HERE))
            if rel in exempt:
                continue
            hits = banned & set(self.argv0_literals(path))
            if hits:
                offenders[rel] = sorted(hits)
        self.assertEqual(offenders, {})


class WavInfoTest(unittest.TestCase):
    def test_durations_come_from_the_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / 'probe.wav'
            with wave.open(str(wav), 'wb') as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes(b'\x00\x01' * 2 * 100)
            spec = importlib.util.spec_from_file_location(
                'speech_catalog', HERE / 'tricky' / 'speech_catalog.py')
            catalog = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(catalog)
            with patch.object(catalog.subprocess, 'run',
                               side_effect=AssertionError('no subprocess')):
                info = catalog.wav_info(str(wav))
        self.assertEqual(
            (info['samples'], info['sample_rate'], info['channels']),
            (100, 22050, 2))


class StructureCheckTest(unittest.TestCase):
    def test_good_wav_passes_and_drift_is_a_note_not_a_raise(self):
        driver = load_driver()
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / 'song.wav'
            with wave.open(str(wav), 'wb') as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(48000)
                w.writeframes(b'\x00\x01' * 2 * 100)
            out = io.StringIO()
            with redirect_stdout(out):
                info = driver.check(wav, '0' * 64, 'song')
            self.assertEqual(
                (info['rate'], info['channels'], info['frames']),
                (48000, 2, 100))
            self.assertIn('drift', out.getvalue())

    def test_matching_sha_is_quiet_ok(self):
        driver = load_driver()
        with tempfile.TemporaryDirectory() as tmp:
            blob = Path(tmp) / 'x.big'
            blob.write_bytes(b'data')
            out = io.StringIO()
            with redirect_stdout(out):
                driver.check(blob, driver.sha(blob), 'x')
            self.assertIn('OK', out.getvalue())

    def test_empty_output_fails_closed(self):
        driver = load_driver()
        with tempfile.TemporaryDirectory() as tmp:
            blob = Path(tmp) / 'x.big'
            blob.write_bytes(b'')
            with self.assertRaises(SystemExit):
                driver.check(blob, '0' * 64, 'x')

    def test_snr_math(self):
        driver = load_driver()
        import array
        ref = array.array('h', [1000] * 1000).tobytes()
        same = array.array('h', [1000] * 1000).tobytes()
        self.assertEqual(driver.snr_db(ref, same), float('inf'))
        off1 = array.array('h', [1001] * 1000).tobytes()
        self.assertAlmostEqual(driver.snr_db(ref, off1), 60.0, places=1)
        off10 = array.array('h', [1010] * 1000).tobytes()
        self.assertAlmostEqual(driver.snr_db(ref, off10), 40.0, places=1)
        with self.assertRaises(ValueError):
            driver.snr_db(ref, ref[:-2])


class FakeSB:
    """Stub for the wrapper's disc table (preflight audio-mode tests)."""

    def __init__(self, tricky_sha, ssx3_sha):
        self.tricky_sha = tricky_sha
        self.ssx3_sha = ssx3_sha
        self.ISO_SHA = ssx3_sha

    def check_tricky_iso_file(self, iso):
        return iso

    def tricky_disc_identity(self, iso):
        return {'sha256': self.tricky_sha, 'serial': 'S', 'region': 'R',
                'bytes': Path(iso).stat().st_size, 'md5': '0' * 32}

    def iso_records(self, iso):
        del iso
        return [('SLUS_207.72', 0, 0),
                ('DATA/AUDIO/SPEECH.BIG', 0, 0),
                ('DATA/AUDIO/MUSIC.BIG', 0, 0),
                ('DATA/AUDIO/MUSIC2.BIG', 0, 0),
                ('DATA/CONFIG/MUSIC.INF', 0, 0),
                ('DATA/AUDIO/JUKEBOX.BIG', 0, 0),
                ('DATA/TEXTURES/HUDGAME.SSH', 0, 0)]


def fake_iso(path, size=16 * 2048 + 16):
    data = bytearray(b'\x00' * size)
    data[16 * 2048:16 * 2048 + 7] = b'\x01CD001\x01'
    Path(path).write_bytes(bytes(data))
    return path


class AudioModeTest(unittest.TestCase):
    def args(self, tmp, tricky, ssx3, **over):
        base = dict(tricky_iso=str(tricky), ssx3_iso=str(ssx3),
                    work=str(Path(tmp) / 'w'), jobs='2',
                    tricky_audio='auto', vgmstream=None, ffmpeg=None)
        base.update(over)
        return types.SimpleNamespace(**base)

    def run_preflight(self, tmp, **over):
        driver = load_driver()
        tricky = fake_iso(Path(tmp) / 'tricky.iso')
        ssx3 = fake_iso(Path(tmp) / 'ssx3.iso')
        tricky_sha = driver.sha(tricky)
        sb = FakeSB(tricky_sha, driver.sha(ssx3))
        a = self.args(tmp, tricky, ssx3, **over)
        with patch.object(driver, '_SSX3_BUILD', sb, create=True), \
             patch.object(driver, 'check_tricky_markers',
                          lambda iso: None), \
             patch.object(driver, 'check_capacity',
                          lambda work: {'disk_free_bytes': 10 ** 12,
                                        'ram_total_bytes': 10 ** 12}), \
             patch.object(driver, 'big_member',
                          lambda *args: Path(args[3]).write_bytes(b'SCHl')):
            out = io.StringIO()
            with redirect_stdout(out):
                ident = driver.preflight(a)
        return ident, out.getvalue()

    def test_auto_with_tools_runs_audio(self):
        with tempfile.TemporaryDirectory() as tmp, scrub_env():
            bindir = Path(tmp) / 'bin'
            bindir.mkdir()
            make_exe(bindir / 'ffmpeg', FFMPEG_STUB)
            make_exe(bindir / 'vgmstream-cli', VGMSTREAM_STUB)
            with patch.dict(os.environ, {'PATH': str(bindir)}), \
                 patch.object(tool_paths, '_smoke_vgmstream',
                              return_value=100):
                ident, _ = self.run_preflight(tmp)
        self.assertEqual(ident['audio'], 'on')
        self.assertIsNotNone(ident['ffmpeg'])
        self.assertIsNotNone(ident['vgmstream'])

    def test_auto_without_tools_skips_with_the_message(self):
        driver = load_driver()
        with tempfile.TemporaryDirectory() as tmp, scrub_env():
            ident, text = self.run_preflight(tmp)
        self.assertEqual(ident['audio'], 'off')
        self.assertIsNone(ident['ffmpeg'])
        self.assertIn('SSX 3 music and speech', text)

    def test_on_without_tools_fails_with_the_fix(self):
        with tempfile.TemporaryDirectory() as tmp, scrub_env():
            with self.assertRaisesRegex(SystemExit, 'install ffmpeg'):
                self.run_preflight(tmp, tricky_audio='on')

    def test_off_skips_without_touching_tools(self):
        with tempfile.TemporaryDirectory() as tmp, scrub_env():
            bindir = Path(tmp) / 'bin'
            bindir.mkdir()
            make_exe(bindir / 'ffmpeg', FFMPEG_STUB)
            with patch.dict(os.environ, {'PATH': str(bindir)}), \
                 patch.object(tool_paths, 'resolve_ffmpeg',
                              side_effect=AssertionError('must not probe')):
                ident, _ = self.run_preflight(tmp, tricky_audio='off')
        self.assertEqual(ident['audio'], 'off')

    def test_layout_off_mode_stages_no_audio_aliases(self):
        driver = load_driver()
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'w'
            b = driver.Build(types.SimpleNamespace(work=str(work)))
            courspit = work / 'courspit.big'
            courspit.write_bytes(b'x')
            built = {'worlds': {}, 'courspit': courspit,
                     'speech-tricky-big': None, 'music-tricky-big': None,
                     'music2-tricky-big': None, 'music-inf-tricky-big': None,
                     'english-silent-big': None, 'trickyhud.bin': None,
                     'tricky-song.wav': None, 'tricky-gems.tsv': None,
                     'tricky-resetvol.tsv': None}
            out = io.StringIO()
            with redirect_stdout(out):
                lay = b.phase_layout(built, audio_on=False)
            manifest = (lay / 'm-tricky.txt').read_text()
            self.assertNotIn('alias = ', manifest)
            self.assertIn('ENGLISH.BIG omitted', manifest)
            env = (lay / 'tricky.env').read_text()
            self.assertNotIn('TRICKY_MUSIC_INF', env)
            self.assertNotIn('TRICKY_SONG', env)
            self.assertIn('PS2X_SSX3_TRICKY_MENU=1', env)

    def test_layout_on_mode_keeps_audio_aliases(self):
        driver = load_driver()
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'w'
            b = driver.Build(types.SimpleNamespace(work=str(work)))
            courspit = work / 'courspit.big'
            courspit.write_bytes(b'x')
            built = {'worlds': {}, 'courspit': courspit,
                     'speech-tricky-big': None, 'music-tricky-big': None,
                     'music2-tricky-big': None, 'music-inf-tricky-big': None,
                     'english-silent-big': None, 'trickyhud.bin': None,
                     'tricky-song.wav': None, 'tricky-gems.tsv': None,
                     'tricky-resetvol.tsv': None}
            out = io.StringIO()
            with redirect_stdout(out):
                lay = b.phase_layout(built, audio_on=True)
            manifest = (lay / 'm-tricky.txt').read_text()
            self.assertIn('alias = /DATA/AUDIO/SPEECH.BIG:', manifest)
            self.assertIn('alias = /DATA/CONFIG/MUSIC.INF:', manifest)
            env = (lay / 'tricky.env').read_text()
            self.assertIn('PS2X_SSX3_TRICKY_SONG=', env)


class PreflightTest(unittest.TestCase):
    def test_bad_jobs_fails_before_touching_discs(self):
        driver = load_driver()
        with tempfile.TemporaryDirectory() as tmp:
            argv = ['--tricky-iso', str(Path(tmp) / 'no.iso'),
                    '--ssx3-iso', str(Path(tmp) / 'no.iso'),
                    '--work', str(Path(tmp) / 'w'), '--jobs', '0']
            with self.assertRaisesRegex(SystemExit, '--jobs'):
                driver.main(argv)
            self.assertFalse((Path(tmp) / 'w').exists())

    def test_bad_audio_mode_fails_fast(self):
        driver = load_driver()
        with tempfile.TemporaryDirectory() as tmp:
            argv = ['--tricky-iso', str(Path(tmp) / 'no.iso'),
                    '--ssx3-iso', str(Path(tmp) / 'no.iso'),
                    '--work', str(Path(tmp) / 'w'),
                    '--tricky-audio', 'sometimes']
            with self.assertRaises(SystemExit) as ctx:
                with redirect_stderr(io.StringIO()):
                    driver.main(argv)
            self.assertEqual(ctx.exception.code, 2)

    def test_missing_iso_fails_with_the_fix(self):
        driver = load_driver()
        with tempfile.TemporaryDirectory() as tmp:
            argv = ['--tricky-iso', str(Path(tmp) / 'no.iso'),
                    '--ssx3-iso', str(Path(tmp) / 'no.iso'),
                    '--work', str(Path(tmp) / 'w')]
            with self.assertRaisesRegex(SystemExit, 'tricky-inputs|not found'):
                driver.main(argv)

    def test_wrong_shaped_disc_fails_on_markers(self):
        driver = load_driver()
        with tempfile.TemporaryDirectory() as tmp:
            iso = Path(tmp) / 'wrong.iso'
            make_tricky_iso(iso)
            with self.assertRaisesRegex(SystemExit, 'marker missing'):
                driver.check_tricky_markers(iso)

    def test_tight_disk_refuses(self):
        driver = load_driver()
        from collections import namedtuple
        usage = namedtuple('usage', 'total used free')
        with tempfile.TemporaryDirectory() as tmp:
            small = usage(10 ** 12, 10 ** 12 - 1, 1)
            with patch.object(driver.shutil, 'disk_usage',
                               return_value=small):
                with self.assertRaisesRegex(SystemExit, 'GB free'):
                    driver.check_capacity(Path(tmp) / 'w')

    def test_toolchain_or_pin_change_breaks_the_skip(self):
        def toolchain(version='v1', digest='a' * 64):
            return {'ffmpeg': {'version': version, 'sha256': digest},
                    'vgmstream': None}

        with tempfile.TemporaryDirectory() as tmp:
            iso = Path(tmp) / 'tricky.iso'
            iso.write_bytes(b'x' * 100)
            digest = build.sha(iso)
            table = {digest: {'serial': 'S', 'region': 'R',
                              'bytes': iso.stat().st_size, 'md5': '0' * 32}}
            work = Path(tmp) / 'work'
            tricky = work / 'tricky'
            tricky.mkdir(parents=True)
            payload = tricky / 'speech-tricky-big'
            payload.write_bytes(b'data')
            receipt = {'pins': {'a': 'b'},
                       'toolchain': toolchain(),
                       'ssx3': {'sha256': 's' * 64},
                       'audio': 'on'}
            (tricky / 'tools.json').write_text(json.dumps(receipt))
            record = {'tricky_iso_sha256': digest,
                      'tricky_iso_bytes': iso.stat().st_size,
                      'created_utc': '2026-10-18T00:00:00Z',
                      'outputs': {'speech-tricky-big':
                                  {'sha256': build.sha2(payload)}},
                      'pins': dict(receipt['pins']),
                      'toolchain': toolchain(),
                      'audio': 'on',
                      'converter_version': build.tricky_converter_version(),
                      'ssx3_iso_sha256': 's' * 64}
            (work / 'tricky_inputs.json').write_text(json.dumps(record))
            with patch.dict(build.TRICKY_DISCS, table, clear=True):
                self.assertTrue(build.tricky_current(
                    work, iso, audio_on=True, toolchain=toolchain()))
                # A pin change breaks the skip.
                record['pins'] = {'a': 'CHANGED'}
                (work / 'tricky_inputs.json').write_text(json.dumps(record))
                self.assertFalse(build.tricky_current(
                    work, iso, audio_on=True, toolchain=toolchain()))
                # A toolchain change breaks the skip; audio-off ignores it.
                record['pins'] = {'a': 'b'}
                record['toolchain'] = toolchain(version='v2', digest='b' * 64)
                (work / 'tricky_inputs.json').write_text(json.dumps(record))
                self.assertFalse(build.tricky_current(
                    work, iso, audio_on=True, toolchain=toolchain()))
                record['audio'] = 'off'
                (work / 'tricky_inputs.json').write_text(json.dumps(record))
                self.assertTrue(build.tricky_current(
                    work, iso, audio_on=False, toolchain=toolchain()))
                # A converter change breaks the skip.
                record['audio'] = 'on'
                record['toolchain'] = toolchain()
                record['converter_version'] = '0' * 64
                (work / 'tricky_inputs.json').write_text(json.dumps(record))
                self.assertFalse(build.tricky_current(
                    work, iso, audio_on=True, toolchain=toolchain()))


if __name__ == '__main__':
    unittest.main()
