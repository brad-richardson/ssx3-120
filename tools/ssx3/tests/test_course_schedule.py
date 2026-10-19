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


class SizeCoursePoolTest(unittest.TestCase):
    """Joint (courses, inner) sizing: --jobs is the total budget.

    Table: cores x RAM (jobs=cores, 10 ready courses). Patches the host
    probes so the table is deterministic on any machine.
    """

    def sized(self, cores, ram_gb, jobs=None, nready=10):
        from unittest import mock
        with mock.patch.object(bt, 'cpu_budget', return_value=cores), \
             mock.patch.object(bt, 'free_gb', return_value=float(ram_gb)):
            return bt.size_course_pool(nready, jobs if jobs is not None else cores)

    def test_table(self):
        table = {(8, 8): (1, 1), (8, 16): (2, 4), (8, 32): (6, 1),
                 (10, 8): (1, 1), (10, 16): (2, 5), (10, 32): (6, 1),
                 (12, 8): (1, 1), (12, 16): (2, 6), (12, 32): (6, 2),
                 (16, 8): (1, 1), (16, 16): (2, 8), (16, 32): (6, 2)}
        for (cores, ram), (courses, inner) in table.items():
            with self.subTest(cores=cores, ram=ram):
                got_courses, got_inner, _note = self.sized(cores, ram)
                self.assertEqual((got_courses, got_inner), (courses, inner))

    def test_jobs_is_the_total_budget(self):
        courses, inner, _note = self.sized(12, 32, jobs=4)
        self.assertEqual((courses, inner), (4, 1))
        self.assertLessEqual(courses * inner, 4)

    def test_group_ram_caps_inner(self):
        # 16 threads of budget, 2 GiB usable: 1 course, groups cap inner.
        courses, inner, _note = self.sized(16, 10, jobs=16)
        self.assertEqual((courses, inner), (1, 4))

    def test_ram_short_stays_serial(self):
        for cores in (8, 10, 12, 16):
            with self.subTest(cores=cores):
                courses, _inner, _note = self.sized(cores, 8)
                self.assertEqual(courses, 1)

    def test_ready_courses_cap(self):
        courses, inner, _note = self.sized(16, 32, nready=3)
        self.assertEqual((courses, inner), (3, 5))

    def test_unknown_ram_falls_back_to_budget(self):
        from unittest import mock
        with mock.patch.object(bt, 'cpu_budget', return_value=12), \
             mock.patch.object(bt, 'free_gb', return_value=0.0):
            courses, inner, _note = bt.size_course_pool(10, 12)
        self.assertEqual((courses, inner), (10, 1))


if __name__ == '__main__':
    unittest.main()
