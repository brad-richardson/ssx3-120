"""Course-pool scheduling tests (tk-speed-1). No game data, no discs."""
import importlib.util
import sys
import unittest
from pathlib import Path

TRICKY = Path(__file__).resolve().parents[1] / 'tricky'
SHARED = TRICKY / 'shared'


def load_build_tricky():
    sys.path.insert(0, str(SHARED))
    sys.path.insert(0, str(TRICKY))
    try:
        path = TRICKY / 'build_tricky.py'
        spec = importlib.util.spec_from_file_location(
            'tk_speed_build_tricky', path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules['tk_speed_build_tricky'] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(TRICKY))
        sys.path.remove(str(SHARED))


bt = load_build_tricky()


class SubmitOrderTest(unittest.TestCase):
    def test_longest_first(self):
        self.assertEqual(bt.course_submit_order(),
                         ['gari', 'merq', 'aloha', 'elys', 'snow',
                          'alaska', 'untrack', 'mega', 'pipe'])

    def test_covers_all_unblocked(self):
        order = bt.course_submit_order()
        self.assertEqual(set(order), set(bt.BUILD_COURSES) - {'mesa'})
        self.assertNotIn('mesa', order)

    def test_descending_weights(self):
        order = bt.course_submit_order()
        weights = [bt.SUBMIT_WEIGHTS[c] for c in order]
        self.assertEqual(weights, sorted(weights, reverse=True))

    def test_initial_trio_is_longest(self):
        # Gated feeding submits workers-many jobs up front; the first
        # trio must be the three longest so no worker idles early.
        self.assertEqual(bt.course_submit_order()[:3],
                         ['gari', 'merq', 'aloha'])

    def test_serial_path_keeps_canonical_order(self):
        # The serial exact path still iterates BUILD_COURSES itself;
        # only the pool branch calls course_submit_order().
        import inspect
        src = inspect.getsource(bt.Build.phase_worlds)
        self.assertIn('course_submit_order()', src)


class MegaSkipTest(unittest.TestCase):
    def _build(self, tmp):
        import argparse
        import os
        a = argparse.Namespace(
            tricky_iso='/none/Tricky.iso', ssx3_iso='/none/SSX3.iso',
            work=os.path.join(tmp, 'w'), routes_ref_dir=None)
        return bt.Build(a)

    def test_mega_missing_final_is_skip(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            b = self._build(tmp)
            with mock.patch.object(bt, 'run', return_value=None):
                status, key, _msg = b.run_one_course(
                    'mega', b.W / 'host-bam' / 'BAM.BIG')
                self.assertEqual((status, key), ('skip', 'mega'))

    def test_mega_present_final_is_ok(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            b = self._build(tmp)
            p, _want = b.world_final_path('mega')
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'fake-final')
            with mock.patch.object(bt, 'run', return_value=None):
                self.assertEqual(
                    b.run_one_course('mega', b.W / 'host-bam' / 'BAM.BIG'),
                    ('ok', 'mega', ''))


class BudgetTest(unittest.TestCase):
    def test_inner_within_cores(self):
        import os
        cores = os.cpu_count() or 8
        for workers in (1, 2, 3, 4, 10):
            with self.subTest(workers=workers):
                inner = bt.size_inner_jobs(workers)
                self.assertGreaterEqual(inner, 1)
                self.assertLessEqual(workers * inner, cores + workers)

    def test_course_workers_bounded(self):
        workers, _note = bt.size_course_workers(10, 6, 4.0)
        self.assertGreaterEqual(workers, 1)
        self.assertLessEqual(workers, 10)


if __name__ == '__main__':
    unittest.main()
