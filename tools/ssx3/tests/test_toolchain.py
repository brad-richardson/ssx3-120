"""Project-local JDK/SDK/NDK bootstrap: pins, discovery order, fetch trust.

No network: downloads use file:// URLs, sdkmanager/licence flows are
covered through their pure predicates, and host install scans are stubbed.
"""
import os
import stat
import sys
import tarfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import toolchain


def make_java(home, version='17.0.20'):
    java = home / 'bin/java'
    java.parent.mkdir(parents=True, exist_ok=True)
    java.write_text('#!/bin/sh\necho "openjdk version \\"%s\\" 2025-01-01"\n' % version)
    java.chmod(java.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return home


def make_sdk(sdk):
    (sdk / 'platforms/android-34').mkdir(parents=True, exist_ok=True)
    (sdk / 'platforms/android-34/android.jar').write_bytes(b'jar')
    (sdk / 'build-tools/34.0.0').mkdir(parents=True, exist_ok=True)
    (sdk / 'build-tools/34.0.0/aapt2').write_bytes(b'aapt2')
    return sdk


def make_ndk(sdk):
    ndk = sdk / 'ndk' / toolchain.NDK_VERSION
    ndk.mkdir(parents=True, exist_ok=True)
    (ndk / 'source.properties').write_text('Pkg.Revision = %s\n' % toolchain.NDK_VERSION)
    return ndk


CLEAN_ENV = {'PATH': '/usr/bin:/bin'}


class PinAgreementTest(unittest.TestCase):
    def test_sdk_constants_match_build_py(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('ssx3_build', HERE / 'build.py')
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        self.assertEqual(toolchain.NDK_VERSION, build.NDK_VERSION)
        self.assertEqual(toolchain.SDK_PACKAGES, build.SDK_PACKAGES)

    @unittest.skipUnless((HERE.parents[1] / 'deps.lock.json').is_file(), 'product lock file')
    def test_bootstrap_pins_match_deps_lock(self):
        import json
        lock = json.loads((HERE.parents[1] / 'deps.lock.json').read_text())
        boot = lock['toolchains']['bootstrap']
        self.assertEqual(toolchain.JDK_RELEASE, boot['jdk_release'])
        for key, (url, sha) in toolchain.JDK_URLS.items():
            self.assertEqual(url, boot['jdk'][key]['url'])
            self.assertEqual(sha, boot['jdk'][key]['sha256'])
        for key, (url, sha, size) in toolchain.CMDLINE_URLS.items():
            self.assertEqual(url, boot['cmdline_tools'][key]['url'])
            self.assertEqual(sha, boot['cmdline_tools'][key]['sha256'])
            self.assertEqual(size, boot['cmdline_tools'][key]['bytes'])

    def test_toolchain_pins_are_complete(self):
        self.assertEqual(toolchain.JDK_RELEASE, '17.0.20.1+1')
        self.assertEqual(set(toolchain.JDK_URLS), {'linux-x64', 'linux-aarch64', 'mac-aarch64'})
        for key, (url, sha) in toolchain.JDK_URLS.items():
            self.assertTrue(url.startswith('https://github.com/adoptium/'), key)
            self.assertRegex(sha, r'^[0-9a-f]{64}$')
        self.assertEqual(set(toolchain.CMDLINE_URLS), {'linux', 'mac'})
        for key, (url, sha, size) in toolchain.CMDLINE_URLS.items():
            self.assertTrue(url.startswith('https://dl.google.com/'), key)
            self.assertRegex(sha, r'^[0-9a-f]{64}$')
            self.assertGreater(size, 10 ** 6)


class DiscoveryTest(unittest.TestCase):
    def test_jdk_predicate_accepts_only_17(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            good = make_java(Path(tmp) / 'jdk17')
            bad = make_java(Path(tmp) / 'jdk21', '21.0.1')
            self.assertTrue(toolchain.jdk_ok(good))
            self.assertFalse(toolchain.jdk_ok(bad))
            self.assertFalse(toolchain.jdk_ok(Path(tmp) / 'missing'))

    def test_sdk_and_ndk_predicates(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(toolchain.sdk_ok(root / 'sdk'))
            self.assertTrue(toolchain.sdk_ok(make_sdk(root / 'sdk')))
            self.assertFalse(toolchain.ndk_ok(root / ('sdk/ndk/%s' % toolchain.NDK_VERSION)))
            self.assertTrue(toolchain.ndk_ok(make_ndk(root / 'sdk')))

    def test_explicit_jdk_beats_java_home(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            explicit = make_java(Path(tmp) / 'explicit')
            env_home = make_java(Path(tmp) / 'env')
            with patch.dict(os.environ, {'JAVA_HOME': str(env_home)}):
                self.assertEqual(toolchain.find_jdk(explicit), explicit)

    def test_search_order_env_then_common_then_managed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            managed = make_java(work / 'toolchain' / 'jdk-test')
            env_home = make_java(Path(tmp) / 'env')
            with patch.dict(os.environ, CLEAN_ENV, clear=True), \
                 patch.object(toolchain, '_common_jdk_dirs', return_value=[]):
                self.assertEqual(toolchain.discover(work)[0], managed)
                with patch.dict(os.environ, {'JAVA_HOME': str(env_home)}):
                    self.assertEqual(toolchain.discover(work)[0], env_home)

    def test_no_auto_download_errors_name_the_fix(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            with patch.dict(os.environ, CLEAN_ENV, clear=True), \
                 patch.object(toolchain, '_common_jdk_dirs', return_value=[]), \
                 patch.object(toolchain, '_common_sdk_dirs', return_value=[]):
                with self.assertRaisesRegex(ValueError, r'missing JDK 17.*JAVA_HOME.*--no-auto-download'):
                    toolchain.ensure_jdk(work, auto_download=False)
                with self.assertRaisesRegex(ValueError, r'missing Android SDK.*ANDROID_HOME.*--no-auto-download'):
                    toolchain.ensure_sdk(work, Path(tmp), auto_download=False)
                with self.assertRaisesRegex(ValueError, r'missing NDK.*--ndk.*--no-auto-download'):
                    toolchain.ensure_ndk(Path(tmp) / 'sdk', auto_download=False)


class FetchTrustTest(unittest.TestCase):
    def test_fetch_accepts_exact_bytes_over_file_url(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'src.bin'
            src.write_bytes(b'pinned-bytes')
            dest = Path(tmp) / 'out.bin'
            got = toolchain.fetch(src.as_uri(), toolchain.sha256_file(src), dest)
            self.assertEqual(got.read_bytes(), b'pinned-bytes')

    def test_fetch_fails_closed_on_sha_mismatch(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'src.bin'
            src.write_bytes(b'pinned-bytes')
            with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                toolchain.fetch(src.as_uri(), '0' * 64, Path(tmp) / 'out.bin')
            self.assertFalse((Path(tmp) / 'out.bin').exists())

    def test_archives_reject_escaping_paths(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            evil_zip = Path(tmp) / 'evil.zip'
            with zipfile.ZipFile(evil_zip, 'w') as z:
                z.writestr('../escape', b'x')
            with self.assertRaisesRegex(ValueError, 'unsafe archive path'):
                toolchain.unpack_cmdline(evil_zip, Path(tmp) / 'sdk')
            evil_tgz = Path(tmp) / 'evil.tgz'
            with tarfile.open(evil_tgz, 'w:gz') as t:
                info = tarfile.TarInfo('/absolute')
                info.size = 1
                import io
                t.addfile(info, io.BytesIO(b'x'))
            with self.assertRaisesRegex(ValueError, 'unsafe archive path'):
                toolchain.unpack_jdk(evil_tgz, Path(tmp) / 'jdk')

    def test_cmdline_unpack_strips_the_top_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'tools.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('cmdline-tools/bin/sdkmanager', b'manager')
            target = toolchain.unpack_cmdline(archive, Path(tmp) / 'sdk')
            self.assertTrue((target / 'bin/sdkmanager').is_file())

    def test_cmdline_unpack_restores_executable_bits(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'tools.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                info = zipfile.ZipInfo('cmdline-tools/bin/sdkmanager')
                info.external_attr = (0o755 & 0xFFFF) << 16
                z.writestr(info, b'manager')
            target = toolchain.unpack_cmdline(archive, Path(tmp) / 'sdk')
            mode = (target / 'bin/sdkmanager').stat().st_mode
            self.assertTrue(mode & 0o111, oct(mode))


class LicenceGateTest(unittest.TestCase):
    def test_managed_sdk_without_flag_fails_with_the_documented_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            sdk = make_sdk(Path(tmp) / 'sdk')
            with patch('sys.stdin') as stdin:
                stdin.isatty.return_value = False
                with self.assertRaisesRegex(ValueError, r'--accept-android-licenses'):
                    toolchain.require_licenses(sdk, False, Path(tmp))

    def test_recorded_acceptance_needs_no_flag(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            sdk = make_sdk(Path(tmp) / 'sdk')
            (sdk / 'licenses').mkdir()
            (sdk / 'licenses' / 'android-sdk-license').write_text('hash\n')
            toolchain.require_licenses(sdk, False, Path(tmp))  # must not raise


if __name__ == '__main__':
    unittest.main()
