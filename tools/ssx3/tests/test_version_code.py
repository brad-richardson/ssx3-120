# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""versionCode must always increase across work dirs (vcode-fix-1).

A rebuild in a fresh --work dir used to restart the per-dir counter at 2,
so `adb install -r` over an already-installed app failed with
INSTALL_FAILED_VERSION_DOWNGRADE. The code is derived from UTC time and
never drops below the work-dir counter (max of both).
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

BUILD = Path(__file__).resolve().parents[1] / 'build.py'
SPEC = importlib.util.spec_from_file_location('ssx3_build', BUILD)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)

EPOCH = build.VERSION_CODE_EPOCH
T0 = EPOCH + 421000 * 60  # a fixed clock near the real one


class VersionCodeTest(unittest.TestCase):
    def test_fresh_work_dir_after_older_one_gets_higher_code(self):
        with tempfile.TemporaryDirectory() as temp:
            older = Path(temp) / 'older'
            older.mkdir()
            first = build.next_version_code(older, now=T0)
            fresh = Path(temp) / 'fresh'
            fresh.mkdir()
            # A later build in a fresh dir (no counter state) must beat the
            # already-installed code, or `adb install -r` refuses it.
            second = build.next_version_code(fresh, now=T0 + 120)
            self.assertGreater(second, first)

    def test_repeated_builds_increase(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            codes = [build.next_version_code(work, now=T0) for _ in range(3)]
            self.assertEqual(codes, sorted(codes))
            self.assertEqual(len(set(codes)), 3)
            stored = int((work / 'version-code.txt').read_text().strip())
            self.assertEqual(stored, codes[-1])

    def test_never_drops_below_work_dir_counter(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            # A legacy counter ahead of the clock still wins (max of both).
            ahead = build.version_code_for_now(T0) + 1000
            (work / 'version-code.txt').write_text(str(ahead) + '\n')
            self.assertEqual(build.next_version_code(work, now=T0), ahead + 1)
            # A legacy small counter (starts at 2) loses to the clock.
            (work / 'version-code.txt').write_text('2\n')
            self.assertEqual(build.next_version_code(work, now=T0),
                             build.version_code_for_now(T0))

    def test_value_stays_under_android_max(self):
        code = build.next_version_code(Path(tempfile.mkdtemp()))
        self.assertLess(code, build.ANDROID_MAX_VERSION_CODE)
        self.assertLess(code, 2100000000)
        # The time derivation stays under the cap for millennia.
        far_future = EPOCH + 1000 * 365 * 24 * 3600
        self.assertLess(build.version_code_for_now(far_future),
                        build.ANDROID_MAX_VERSION_CODE)


if __name__ == '__main__':
    unittest.main()
