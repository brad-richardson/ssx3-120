"""PGO profile resolution: freshness gate, escape hatch, explicit override.

No network, no disc, no NDK: the bundled compressed profiles are read from
the checkout; codegen fixtures are synthetic.
"""
import gzip
import importlib.util
import io
import json
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

BUILD = Path(__file__).resolve().parents[1] / 'build.py'
SPEC = importlib.util.spec_from_file_location('ssx3_build_pgo', BUILD)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def make_work(tmp, register_text='register-fresh'):
    work = Path(tmp) / 'work'
    codegen = work / 'codegen'
    codegen.mkdir(parents=True)
    (codegen / 'register_functions.cpp').write_text(register_text)
    return work


class ResolvePgoTest(unittest.TestCase):
    def test_no_pgo_flag_builds_without_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = make_work(tmp)
            out = io.StringIO()
            with redirect_stdout(out):
                result = build.resolve_pgo(work, 'android', disabled=True)
            self.assertEqual(result, '')
            self.assertIn('--no-pgo', out.getvalue())

    def test_explicit_profile_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = make_work(tmp)
            prof = Path(tmp) / 'custom.profdata'
            prof.write_bytes(b'custom-profile')
            out = io.StringIO()
            with redirect_stdout(out):
                result = build.resolve_pgo(work, 'android', explicit=prof)
            self.assertEqual(result, str(prof.resolve()))
            self.assertIn('explicit', out.getvalue())

    def test_missing_explicit_profile_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = make_work(tmp)
            with self.assertRaises(ValueError):
                build.resolve_pgo(work, 'android', explicit=Path(tmp) / 'nope.profdata')

    def test_fresh_bundled_profile_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = {'trained_codegen_register_sha256': 'abc',
                        'profile_sha256': 'def'}
            work = make_work(tmp)
            with patch.object(build, 'pgo_manifest', return_value=manifest), \
                 patch.object(build, 'codegen_register_sha', return_value='abc'), \
                 patch.object(build, 'sha', return_value='def'):
                out = io.StringIO()
                with redirect_stdout(out):
                    with patch.object(build.gzip, 'open') as gz:
                        # Simulate a decompress that writes expected bytes.
                        def fake_open(path, mode):
                            self.assertIn('profile.profdata.gz', str(path))
                            raise AssertionError('should not decompress when dest is fresh')
                        # Pre-create a fresh dest so no decompress happens.
                        dest = work / 'pgo-android.profdata'
                        dest.write_bytes(b'x')
                        result = build.resolve_pgo(work, 'android')
                self.assertEqual(result, str(dest))
                self.assertIn('PGO profile applied', out.getvalue())

    def test_stale_codegen_builds_without_pgo(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = {'trained_codegen_register_sha256': 'trained123',
                        'profile_sha256': 'def'}
            work = make_work(tmp)
            with patch.object(build, 'pgo_manifest', return_value=manifest), \
                 patch.object(build, 'codegen_register_sha', return_value='user456'):
                out = io.StringIO()
                with redirect_stdout(out):
                    result = build.resolve_pgo(work, 'android')
            self.assertEqual(result, '')
            self.assertIn('stale', out.getvalue().lower())

    def test_missing_codegen_builds_without_pgo(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            work.mkdir()
            manifest = {'trained_codegen_register_sha256': 'abc'}
            with patch.object(build, 'pgo_manifest', return_value=manifest):
                out = io.StringIO()
                with redirect_stdout(out):
                    result = build.resolve_pgo(work, 'android')
            self.assertEqual(result, '')
            self.assertIn('without PGO', out.getvalue())

    def test_bundled_manifests_match_archives(self):
        for platform in ('android', 'ios'):
            manifest = build.pgo_manifest(platform)
            self.assertIsNotNone(manifest, platform)
            archive = build.PGO_DIR / platform / 'profile.profdata.gz'
            self.assertTrue(archive.is_file(), platform)
            self.assertRegex(manifest.get('profile_sha256', ''), r'^[0-9a-f]{64}$')
            self.assertRegex(manifest.get('trained_codegen_register_sha256', ''), r'^[0-9a-f]{64}$')


class AndroidPgoWiringTest(unittest.TestCase):
    def test_android_passes_resolved_pgo_to_gradle(self):
        seen = {}

        def fake_run(*args, **kwargs):
            seen['args'] = list(map(str, args))
        with tempfile.TemporaryDirectory() as tmp:
            work = make_work(tmp)
            jni = work / 'jniLibs'
            (jni / 'arm64-v8a').mkdir(parents=True)
            a = types.SimpleNamespace(work=work, jnilibs=jni, ndk=None, prebuilt=False,
                                      no_turnip=True, app_id=build.PUBLIC_APP_ID, jobs=2,
                                      pgo_profile=None, no_pgo=True)
            with patch.object(build, 'ensure_android_toolchain'), \
                 patch.object(build, 'support_bundle', return_value=None), \
                 patch.object(build, 'android_libs'), \
                 patch.object(build, 'ensure_keystore', return_value=work / 'k'), \
                 patch.object(build, 'next_version_code', return_value=1), \
                 patch.object(build, 'run', side_effect=fake_run):
                build.android(a)
        pgo_args = [x for x in seen['args'] if x.startswith('-Pps2xPgoData=')]
        self.assertEqual(len(pgo_args), 1)
        self.assertEqual(pgo_args[0], '-Pps2xPgoData=')


if __name__ == '__main__':
    unittest.main()
