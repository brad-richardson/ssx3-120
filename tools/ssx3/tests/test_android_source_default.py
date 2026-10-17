"""Android libraries default to the source build; --prebuilt is the opt-in.

v0-defaults-1 (Brad 10-16): the Android APK uses the source libraries by
default; a verified prebuilt release is only consulted with `--prebuilt`.
These tests pin `support_bundle`'s selection so the default cannot silently
go back to downloading a release.
"""
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

BUILD = Path(__file__).resolve().parents[1] / 'build.py'
SPEC = importlib.util.spec_from_file_location('ssx3_build', BUILD)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def args(work, **over):
    base = dict(work=work, prebuilt=False, jnilibs=None,
                app_id=build.PUBLIC_APP_ID, prebuilt_cache=None)
    base.update(over)
    return types.SimpleNamespace(**base)


class AndroidSourceDefaultTest(unittest.TestCase):
    def test_default_builds_from_source_and_never_imports_prebuilt(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A None entry makes `import prebuilt` raise, so a regression that
            # touches the prebuilt path here fails loudly instead of touching
            # the network.
            with patch.dict(sys.modules, {'prebuilt': None}):
                self.assertIsNone(build.support_bundle(args(Path(tmp))))

    def test_explicit_jnilibs_beats_the_prebuilt_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = build.support_bundle(
                args(Path(tmp), prebuilt=True, jnilibs=Path(tmp) / 'jniLibs'))
            self.assertIsNone(got)

    def test_custom_app_id_uses_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(build.support_bundle(
                args(Path(tmp), prebuilt=True, app_id='com.example.other')))

    def test_opt_in_runs_the_prebuilt_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            fake = types.ModuleType('prebuilt')

            def fetch(cache, mode='auto'):
                calls.append((cache, mode))
                return Path(tmp) / 'bundle'

            fake.fetch = fetch
            with patch.dict(sys.modules, {'prebuilt': fake}):
                got = build.support_bundle(args(Path(tmp), prebuilt=True))
            self.assertEqual(got, Path(tmp) / 'bundle')
            self.assertEqual(calls, [(Path(tmp) / 'prebuilt-cache', 'auto')])


if __name__ == '__main__':
    unittest.main()
