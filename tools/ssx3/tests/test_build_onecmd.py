"""One-command builder flow: wrapper, PEP 723, tricky validation, resume.

No network, no disc: the ISO under test is synthetic (ISO9660 magic only),
heavy steps are stubbed, and licence prompts run non-interactive.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

SPEC = importlib.util.spec_from_file_location('ssx3_build', HERE / 'build.py')
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)

WRAPPER = ROOT / 'build'


def make_iso(path, magic=True):
    data = bytearray(18 * 2048)
    if magic:
        data[16 * 2048:16 * 2048 + 7] = b'\x01CD001\x01'
    Path(path).write_bytes(bytes(data))


def onecmd_args(work, **over):
    base = dict(work=work, ssx3_iso=None, tricky_iso=None, install=False,
                prebuilt=False, jnilibs=None, prebuilt_cache=None, ndk=None,
                jdk=None, no_auto_download=False, accept_android_licenses=False,
                app_id=build.PUBLIC_APP_ID, jobs=2, target='android',
                tools_only=False, symbol_names=None, iso=None)
    base.update(over)
    return types.SimpleNamespace(**base)


class WrapperTest(unittest.TestCase):
    @unittest.skipUnless((ROOT / 'build').is_file(), 'product ./build wrapper')
    def test_wrapper_is_posix_shell_over_uv(self):
        text = WRAPPER.read_text()
        self.assertTrue(os.access(WRAPPER, os.X_OK))
        self.assertTrue(text.startswith('#!/bin/sh'))
        self.assertIn('exec uv run --script', text)
        self.assertIn('tools/ssx3/build.py', text)
        self.assertIn('https://astral.sh/uv/install.sh', text)
        probe = subprocess.run(['sh', '-n', str(WRAPPER)], capture_output=True)
        self.assertEqual(probe.returncode, 0)

    def test_build_py_carries_pep723_metadata(self):
        head = (HERE / 'build.py').read_text().split('"""')[0]
        self.assertIn('# /// script', head)
        self.assertIn('requires-python', head)
        self.assertIn('numpy', head)
        self.assertIn('Pillow', head)

    @unittest.skipUnless((ROOT / 'Makefile').is_file(), 'product Makefile')
    def test_makefile_targets_delegate_to_the_wrapper(self):
        text = (ROOT / 'Makefile').read_text()
        self.assertIn('./build', text)
        self.assertIn('--ssx3-iso', text)
        for target in ('android:', 'doctor:', 'install:'):
            self.assertIn(target, text)


class TrickyIsoTest(unittest.TestCase):
    def test_tricky_unsupported_in_this_checkout(self):
        self.assertFalse(build.tricky_supported())

    def test_missing_tricky_iso_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'not found'):
                build.validate_tricky_iso(Path(tmp) / 'nope.iso')

    def test_non_iso_tricky_file_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            iso = Path(tmp) / 'fake.iso'
            make_iso(iso, magic=False)
            with self.assertRaisesRegex(ValueError, 'not an ISO9660'):
                build.validate_tricky_iso(iso)

    def test_valid_iso_fails_with_the_later_version_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            iso = Path(tmp) / 'tricky.iso'
            make_iso(iso)
            with self.assertRaisesRegex(ValueError, 'later version'):
                build.validate_tricky_iso(iso)


class ResumeTest(unittest.TestCase):
    def test_empty_work_dir_runs_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.assertFalse(build.prepare_current(work))
            self.assertFalse(build.android_libs_current(work))

    def test_prepare_from_another_disc_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / 'inputs.json').write_text(json.dumps({'iso_sha256': '0' * 64}))
            with self.assertRaisesRegex(ValueError, 'different disc'):
                build.prepare_current(work)

    def test_current_markers_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            slim = work / 'SSX3.iso'
            slim.write_bytes(b'slim')
            (work / 'SLUS_207.72').write_bytes(b'elf')
            (work / 'codegen').mkdir()
            (work / 'inputs.json').write_text(json.dumps(
                {'iso_sha256': build.ISO_SHA, 'slim_iso': str(slim)}))
            self.assertTrue(build.prepare_current(work))
            jni = work / 'jniLibs/arm64-v8a'
            jni.mkdir(parents=True)
            (jni / 'libge1_gs.so').write_bytes(b'gs')
            (jni / 'libmv2_microvu.so').write_bytes(b'mv2')
            (work / 'android-resources').mkdir()
            self.assertTrue(build.android_libs_current(work))


class OneCommandOrderTest(unittest.TestCase):
    def run_onecmd(self, current, install=False):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            work.mkdir()
            iso = Path(tmp) / 'SSX3.iso'
            make_iso(iso)
            args = onecmd_args(work, ssx3_iso=iso, install=install)
            calls = []
            with patch.object(build, 'ensure_android_toolchain',
                              side_effect=lambda a: calls.append('toolchain') or setattr(a, 'ndk', work)), \
                 patch.object(build, 'doctor', side_effect=lambda a: calls.append('doctor')), \
                 patch.object(build, 'prepare', side_effect=lambda a: calls.append('prepare')), \
                 patch.object(build, 'android_libs', side_effect=lambda a: calls.append('android-libs')), \
                 patch.object(build, 'android', side_effect=lambda a: calls.append('android')), \
                 patch.object(build, 'install', side_effect=lambda a: calls.append('install')), \
                 patch.object(build, 'prepare_current', return_value=current), \
                 patch.object(build, 'android_libs_current', return_value=current):
                build.android_onecmd(args)
            return calls, args

    def test_fresh_work_runs_every_step_in_order(self):
        calls, args = self.run_onecmd(False)
        self.assertEqual(calls, ['toolchain', 'doctor', 'prepare', 'android-libs', 'android'])
        self.assertEqual(args.iso, args.ssx3_iso.resolve())
        self.assertEqual(args.jnilibs, args.work / 'jniLibs')

    def test_current_outputs_skip_to_android(self):
        calls, _ = self.run_onecmd(True)
        self.assertEqual(calls, ['toolchain', 'doctor', 'android'])

    def test_install_flag_runs_install_last(self):
        calls, _ = self.run_onecmd(True, install=True)
        self.assertEqual(calls, ['toolchain', 'doctor', 'android', 'install'])

    def test_prebuilt_leaves_bundle_selection_to_android(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            work.mkdir()
            args = onecmd_args(work, ssx3_iso=Path(tmp) / 'x.iso', prebuilt=True)
            with patch.object(build, 'ensure_android_toolchain'), \
                 patch.object(build, 'doctor'), \
                 patch.object(build, 'prepare'), \
                 patch.object(build, 'android_libs'), \
                 patch.object(build, 'android'), \
                 patch.object(build, 'prepare_current', return_value=True), \
                 patch.object(build, 'android_libs_current', return_value=True):
                build.android_onecmd(args)
            self.assertIsNone(args.jnilibs)


class ManagedLicenceInInstallTest(unittest.TestCase):
    def test_managed_sdk_without_flag_fails_with_the_documented_path(self):
        import stat
        import toolchain
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            sdk = work / 'toolchain' / 'android-sdk'
            (sdk / 'platforms/android-34').mkdir(parents=True)
            (sdk / 'platforms/android-34/android.jar').write_bytes(b'jar')
            (sdk / 'build-tools/34.0.0').mkdir(parents=True)
            (sdk / 'build-tools/34.0.0/aapt2').write_bytes(b'aapt2')
            java = work / 'toolchain' / 'jdk-test' / 'bin/java'
            java.parent.mkdir(parents=True)
            java.write_text('#!/bin/sh\necho "openjdk version \\"17.0.20\\" 2025-01-01"\n')
            java.chmod(java.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            args = types.SimpleNamespace(work=work, jdk=None, ndk=None,
                                         accept_android_licenses=False)
            with patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}, clear=True), \
                 patch.object(toolchain, '_common_jdk_dirs', return_value=[]), \
                 patch.object(toolchain, '_common_sdk_dirs', return_value=[]), \
                 patch('sys.stdin') as stdin:
                stdin.isatty.return_value = False
                with self.assertRaisesRegex(ValueError, r'--accept-android-licenses'):
                    build.ensure_install_toolchain(args)


if __name__ == '__main__':
    unittest.main()
