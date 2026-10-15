"""Synthetic sector test for the public slim writer."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


BUILD = Path(__file__).resolve().parents[1] / 'build.py'
SPEC = importlib.util.spec_from_file_location('ssx3_build', BUILD)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class SlimWriterTest(unittest.TestCase):
    def test_keeps_lba_order_and_writes_versioned_table(self):
        with tempfile.TemporaryDirectory(prefix='ps2x-slim-writer-') as temp:
            root = Path(temp)
            source = root / 'source.iso'
            source.write_bytes(b''.join(bytes([i]) * build.SECTOR for i in range(8)))
            output = root / 'SSX3.iso'
            build.write_slim(source, output, [(1, 2, 1), (5, 1, 3), (6, 1, 2)], build.ISO_SHA)
            self.assertEqual(output.read_bytes(),
                             b''.join(bytes([i]) * build.SECTOR for i in (0, 3, 4, 5, 7)))
            table = Path(str(output) + '.holes').read_bytes()
            self.assertEqual(table[:8], build.SLIM_MAGIC)
            self.assertEqual(table[8:72], build.ISO_SHA.encode('ascii'))
            self.assertEqual(len(table), 120)


if __name__ == '__main__':
    unittest.main()
