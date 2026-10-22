# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""Android manifest placeholders resolve: every ${name} in
apps/android/app/src/main/AndroidManifest.xml must be provided by the
effective manifestPlaceholders map in apps/android/app/build.gradle.

Gradle map semantics matter: a later `manifestPlaceholders = [...]`
assignment *replaces* the whole map, so two separate assignments silently
drop the first map's keys (release-pb71-1: the F8 profileable assignment
clobbered the G7 app-label key and every public `./build android` failed
at :app:processReleaseMainManifest). This test replays the assignments in
order (`=` resets, `+=` merges) and requires the final map to cover every
manifest placeholder.

No network, no disc, no SDK: reads the two files from the checkout.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / 'apps' / 'android' / 'app' / 'src' / 'main' / 'AndroidManifest.xml'
GRADLE = ROOT / 'apps' / 'android' / 'app' / 'build.gradle'

PLACEHOLDER = re.compile(r'\$\{([A-Za-z0-9_]+)\}')
ASSIGN = re.compile(r'manifestPlaceholders\s*(\+?)=\s*\[(.*?)\]', re.DOTALL)
KEY = re.compile(r'([A-Za-z0-9_]+)\s*:')


def manifest_placeholders(manifest_text):
    return set(PLACEHOLDER.findall(manifest_text))


def effective_placeholders(gradle_text):
    """Replay manifestPlaceholders assignments in file order."""
    provided = {}
    for m in ASSIGN.finditer(gradle_text):
        keys = set(KEY.findall(m.group(2)))
        if m.group(1) == '+':
            provided.update({k: True for k in keys})
        else:
            provided = {k: True for k in keys}
    return set(provided)


def missing_placeholders(manifest_text, gradle_text):
    return manifest_placeholders(manifest_text) - effective_placeholders(gradle_text)


class ManifestPlaceholdersTest(unittest.TestCase):
    def test_both_placeholders_resolve(self):
        manifest_text = MANIFEST.read_text()
        gradle_text = GRADLE.read_text()
        needed = manifest_placeholders(manifest_text)
        self.assertIn('ps2xAppLabel', needed)
        self.assertIn('ps2xProfileable', needed)
        self.assertEqual(missing_placeholders(manifest_text, gradle_text), set())

    def test_single_assignment_map(self):
        # Two plain `=` assignments mean the first map is dead: flag it
        # even when the union happens to cover the manifest today.
        plain = [m for m in ASSIGN.finditer(GRADLE.read_text()) if m.group(1) != '+']
        self.assertLessEqual(len(plain), 1)


if __name__ == '__main__':
    unittest.main()
