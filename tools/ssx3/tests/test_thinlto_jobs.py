"""Sizing tests for the link-mem-1 low-RAM ThinLTO cap.

The Android game link peaks at ~8.39 GiB with 18 ThinLTO backends and scales
~0.25 GiB/job down to 4.13 GiB at 1 job (byte-identical outputs). Hosts with
8 GB or less build with jobs=1 and a 2 GB Gradle heap; above that the cap
leaves a 6 GB reserve (4 GB heap + OS/headroom) and never exceeds cores.
"""
import importlib.util
from pathlib import Path
import unittest


BUILD = Path(__file__).resolve().parents[1] / 'build.py'
SPEC = importlib.util.spec_from_file_location('ssx3_build', BUILD)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class ThinLtoJobsTest(unittest.TestCase):
    def test_small_hosts_always_get_one(self):
        self.assertEqual(build.thinlto_jobs(8, 4), 1)
        self.assertEqual(build.thinlto_jobs(8, 18), 1)
        self.assertEqual(build.thinlto_jobs(7.5, 2), 1)
        self.assertEqual(build.thinlto_jobs(4, 16), 1)

    def test_never_exceeds_cores(self):
        self.assertEqual(build.thinlto_jobs(16, 8), 8)
        self.assertEqual(build.thinlto_jobs(64, 18), 18)
        self.assertEqual(build.thinlto_jobs(10, 4), 4)

    def test_reserve_before_per_job(self):
        # (9 - 6) / 0.25 = 12 on a many-core host.
        self.assertEqual(build.thinlto_jobs(9, 32), 12)
        # (16 - 6) / 0.25 = 40, capped by cores.
        self.assertEqual(build.thinlto_jobs(16, 18), 18)

    def test_minimum_one_above_reserve(self):
        self.assertEqual(build.thinlto_jobs(8.1, 64), 8)
        self.assertGreaterEqual(build.thinlto_jobs(100, 1), 1)


class EffectiveJobsTest(unittest.TestCase):
    def test_small_hosts_default_to_two(self):
        self.assertEqual(build.effective_jobs(None, 8), 2)
        self.assertEqual(build.effective_jobs(None, 4), 2)

    def test_default_eight_otherwise(self):
        self.assertEqual(build.effective_jobs(None, 8.1), 8)
        self.assertEqual(build.effective_jobs(None, 64), 8)
        self.assertEqual(build.effective_jobs(None, None), 8)

    def test_explicit_always_wins(self):
        self.assertEqual(build.effective_jobs(1, 8), 1)
        self.assertEqual(build.effective_jobs(8, 8), 8)
        self.assertEqual(build.effective_jobs(16, 64), 16)


class GradleHeapTest(unittest.TestCase):
    def test_small_hosts_get_two_gb(self):
        self.assertEqual(build.gradle_heap_gb(8), 2)
        self.assertEqual(build.gradle_heap_gb(4), 2)

    def test_default_four_above(self):
        self.assertEqual(build.gradle_heap_gb(8.1), 4)
        self.assertEqual(build.gradle_heap_gb(64), 4)


if __name__ == '__main__':
    unittest.main()
