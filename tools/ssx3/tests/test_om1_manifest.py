"""OM1 manifest hygiene: tools/ssx3/ios/om1-manifest.json holds offsets,
hashes and pState only. It must never carry image bytes (bring your own
disc: the builder rebuilds every image from the user's ELF at build time).

No network, no disc, no Xcode: reads the manifest from the checkout.
"""
import json
import unittest
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[1] / 'ios' / 'om1-manifest.json'
AREAS = ('sp1', 'mid', 'full', 'menus', 'late', 'sv1', 'grav', 'rb', 'jx')
HEX_RE = set('0123456789abcdef')


def load():
    return json.loads(MANIFEST.read_text())


def walk(node, path='$'):
    """Yield (key_path, key, value) for every node in the manifest."""
    if isinstance(node, dict):
        for key, value in node.items():
            child = path + '.' + str(key)
            yield child, key, value
            yield from walk(value, child)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from walk(value, '%s[%d]' % (path, i))
    elif isinstance(node, str):
        yield path, None, node


class ManifestTest(unittest.TestCase):
    def test_schema(self):
        m = load()
        self.assertEqual(set(m['areas']), set(AREAS))
        self.assertRegex(m['elf']['sha256'], r'^[0-9a-f]{64}$')
        self.assertGreater(m['elf']['size'], 0)
        for area in AREAS:
            entry = m['areas'][area]
            self.assertTrue(entry['images'], area)
            self.assertTrue(entry['blocks'], area)
            for img in entry['images']:
                self.assertTrue(img['ranges'], area)
                for piece in img['pieces']:
                    self.assertEqual(len(piece), 3)
                    self.assertTrue(all(isinstance(v, int) for v in piece))
            for blk in entry['blocks']:
                self.assertRegex(blk['pState_hex'], r'^[0-9a-f]{192}$')

    def test_no_image_bytes(self):
        """No hex payload longer than one 96-byte pState; no code arrays."""
        m = load()
        for path, key, value in walk(m):
            self.assertNotIn('_code', str(key), path)
            if isinstance(value, str) and set(value) <= HEX_RE and len(value) > 64:
                self.assertEqual(len(value), 192, path)
                self.assertTrue(path.endswith('pState_hex'), path)


if __name__ == '__main__':
    unittest.main()
