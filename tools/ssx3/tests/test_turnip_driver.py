# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""verified_driver fetches a Vulkan driver .so from inside a release archive.

Covers the community-zip path (StevenMXZ Gen8 V37, Part 4): a good archive
stages the exact member bytes; a wrong archive SHA, a wrong inner SHA, or a
missing member each fail closed with ValueError.
"""
import hashlib
import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

BUILD = Path(__file__).resolve().parents[1] / 'build.py'
SPEC = importlib.util.spec_from_file_location('ssx3_build', BUILD)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)

DRIVER = b'\x7fELF-FAKE-TURNIP-DRIVER'
MEMBER = 'libvulkan_freedreno.so'


def sha(b):
    return hashlib.sha256(b).hexdigest()


def make_zip(directory, files):
    zpath = Path(directory) / 'driver.zip'
    with zipfile.ZipFile(zpath, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    return zpath


def entry(zpath, **over):
    e = {
        'url': zpath.as_uri(),
        'archive_sha256': sha(zpath.read_bytes()),
        'library': MEMBER,
        'sha256': sha(DRIVER),
    }
    e.update(over)
    return e


def cached(tmp):
    c = Path(tmp) / 'cache'
    c.mkdir(exist_ok=True)
    return c

class VerifiedDriverTest(unittest.TestCase):
    def test_good_archive_stages_member_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            zpath = make_zip(tmp, {MEMBER: DRIVER, 'meta.json': b'{}'})
            got = build.verified_driver('libvulkan_freedreno.so',
                                        entry(zpath), cached(tmp))
            self.assertEqual(Path(got).read_bytes(), DRIVER)
            self.assertEqual(got.name, 'libvulkan_freedreno.so')

    def test_bad_archive_sha_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            zpath = make_zip(tmp, {MEMBER: DRIVER})
            with self.assertRaises(ValueError):
                build.verified_driver('libvulkan_freedreno.so',
                                      entry(zpath, archive_sha256='0' * 64),
                                      cached(tmp))

    def test_bad_inner_sha_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            zpath = make_zip(tmp, {MEMBER: DRIVER})
            with self.assertRaises(ValueError):
                build.verified_driver('libvulkan_freedreno.so',
                                      entry(zpath, sha256='0' * 64),
                                      cached(tmp))

    def test_missing_member_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            zpath = make_zip(tmp, {'meta.json': b'{}'})
            with self.assertRaises(ValueError):
                build.verified_driver('libvulkan_freedreno.so',
                                      entry(zpath), cached(tmp))


if __name__ == '__main__':
    unittest.main()
