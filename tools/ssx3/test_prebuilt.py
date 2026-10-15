#!/usr/bin/env python3
"""No-disc regressions for the prebuilt trust boundary and source fallback."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile
import prebuilt

STAMP = 'a' * 64


class PrebuiltTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cache = self.root / 'cache'
        self.bundle = self.cache / STAMP
        self.bundle.mkdir(parents=True)
        self.contract_patch = patch.object(prebuilt, 'contract', return_value=STAMP)
        self.contract_patch.start()
        paths = ['lib/' + x for x in prebuilt.ARCHIVES]
        paths += ['jniLibs/arm64-v8a/' + x for x in prebuilt.SHARED]
        paths += ['include/sse2neon.h', 'runtime-definitions.txt', 'notices/PS2Recomp-LICENSE', 'notices/SOURCES.json']
        for name in paths:
            p = self.bundle / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'synthetic test fixture, no game input\n')
        self.manifest = {'recipe': prebuilt.RECIPE, 'contract': STAMP, 'source_sha': 'b' * 40,
                         'files': {name: prebuilt.digest(self.bundle / name) for name in paths}}
        self.write_manifest()

    def tearDown(self):
        self.contract_patch.stop()
        self.tmp.cleanup()

    def write_manifest(self):
        (self.bundle / 'manifest.json').write_text(json.dumps(self.manifest))

    def test_offline_hit_never_contacts_network(self):
        with patch.object(prebuilt.urllib.request, 'urlopen', side_effect=AssertionError('network used')):
            self.assertEqual(prebuilt.fetch(self.cache), self.bundle)

    def test_off_mode_never_reads_cache_or_network(self):
        with patch.object(prebuilt, 'contract', side_effect=AssertionError('contract used')):
            self.assertIsNone(prebuilt.fetch(self.cache, 'off'))

    def test_corrupt_cache_falls_back_without_using_artifact(self):
        (self.bundle / 'lib/libps2_runtime.a').write_bytes(b'corrupt')
        self.assertIsNone(prebuilt.fetch(self.cache))

    def test_wrong_toolchain_and_source_contract_are_rejected(self):
        self.manifest['contract'] = 'c' * 64
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'contract mismatch'):
            prebuilt.verify(self.bundle)
        self.manifest['contract'] = STAMP
        self.manifest['recipe'] = dict(prebuilt.RECIPE, ndk='wrong')
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'contract mismatch'):
            prebuilt.verify(self.bundle)

    def test_unlisted_payload_and_missing_archive_are_rejected(self):
        extra = self.bundle / 'unexpected.bin'
        extra.write_bytes(b'unlisted')
        with self.assertRaisesRegex(ValueError, 'exactly'):
            prebuilt.verify(self.bundle)
        extra.unlink()
        del self.manifest['files']['lib/libps2_runtime.a']
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            prebuilt.verify(self.bundle)

    def test_manifest_cannot_authorize_game_payload(self):
        p = self.bundle / 'game.iso'
        p.write_bytes(b'synthetic, not a disc')
        self.manifest['files']['game.iso'] = prebuilt.digest(p)
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'unapproved'):
            prebuilt.verify(self.bundle)

    def test_archive_path_escape_duplicate_and_symlink_rejected(self):
        cases = ['../escape', '/absolute', 'resources/arcade/card.gzip']
        for name in cases:
            archive = self.root / 'unsafe.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr(name, b'fake')
            with self.assertRaises(ValueError):
                prebuilt.extract(archive, self.root / 'out')
        archive = self.root / 'symlink.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            entry = zipfile.ZipInfo('include/sse2neon.h')
            entry.create_system = 3
            entry.external_attr = 0o120777 << 16
            z.writestr(entry, '/outside')
        with self.assertRaises(ValueError):
            prebuilt.extract(archive, self.root / 'out')
        self.assertFalse((self.root / 'out/include/sse2neon.h').exists())

    def test_release_miss_falls_back(self):
        with patch.object(prebuilt.urllib.request, 'urlopen', side_effect=urllib.error.HTTPError('url', 404, 'missing', {}, None)):
            self.assertIsNone(prebuilt.fetch(self.root / 'empty-cache'))

    def test_download_checksum_mismatch_falls_back(self):
        asset = 'android-arm64-' + STAMP + '.zip'
        responses = [io.BytesIO(('0' * 64 + '  ' + asset + '\n').encode()), io.BytesIO(b'corrupt zip')]
        with patch.object(prebuilt.urllib.request, 'urlopen', side_effect=responses):
            self.assertIsNone(prebuilt.fetch(self.root / 'download-cache'))
        self.assertFalse((self.root / 'download-cache' / STAMP).exists())

    def test_valid_download_is_verified_then_cached(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, 'w') as z:
            for p in sorted(self.bundle.rglob('*')):
                if p.is_file():
                    z.write(p, p.relative_to(self.bundle).as_posix())
        data = payload.getvalue()
        sha = prebuilt.hashlib.sha256(data).hexdigest()
        asset = 'android-arm64-' + STAMP + '.zip'
        responses = [io.BytesIO((sha + '  ' + asset + '\n').encode()), io.BytesIO(data)]
        cache = self.root / 'download-cache'
        with patch.object(prebuilt.urllib.request, 'urlopen', side_effect=responses):
            self.assertEqual(prebuilt.fetch(cache), cache / STAMP)
        prebuilt.verify(cache / STAMP)


if __name__ == '__main__':
    unittest.main()
