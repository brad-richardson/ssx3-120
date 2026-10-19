#!/usr/bin/env python3
"""build-ux-1: estimate scaling, banner formatting, failure help, non-TTY.

No network, no disc, no toolchain: estimate math and printers are pure,
run_step failures are raised from a stub, and the one dry-run test stubs
every heavy builder step.
"""
import importlib.util
import io
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import build_ux

SPEC = importlib.util.spec_from_file_location('ssx3_build_ux', HERE / 'build.py')
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class BannerTest(unittest.TestCase):
    def test_banner_format_is_exact(self):
        self.assertEqual(
            build_ux.step_banner(2, 6, 'prepare', 1, 2, color=False),
            '[step 2/6] prepare (est. 1-2 min on this machine)')

    def test_banner_collapses_equal_bounds(self):
        self.assertEqual(
            build_ux.step_banner(1, 1, 'doctor', 0, 0, color=False),
            '[step 1/1] doctor (est. 0 min on this machine)')

    def test_done_line_carries_elapsed(self):
        self.assertEqual(build_ux.step_done('prepare', 42, color=False),
                         'prepare done in 0:42')
        self.assertEqual(build_ux.step_done('android', 3723, color=False),
                         'android done in 1:02:03')


class EstimateScalingTest(unittest.TestCase):
    def test_plan_order_android_without_tricky(self):
        names = [n for n, _lo, _hi in build_ux.estimate_steps(
            target='android', tricky=False, cores=10, ram_gb=64, jobs=8)]
        self.assertEqual(names, ['doctor', 'prepare', 'android-libs', 'android'])

    def test_plan_order_ios_with_tricky_and_install(self):
        names = [n for n, _lo, _hi in build_ux.estimate_steps(
            target='ios', tricky=True, cores=10, ram_gb=64, jobs=8,
            install=True)]
        self.assertEqual(names, ['doctor', 'prepare', 'tricky', 'ios-libs',
                                 'ios', 'install'])

    def test_more_workers_shrink_the_tricky_range(self):
        serial = [row for row in build_ux.estimate_steps(
            target='android', tricky=True, cores=3, ram_gb=64, jobs=8)
            if row[0] == 'tricky'][0]
        pooled = [row for row in build_ux.estimate_steps(
            target='android', tricky=True, cores=12, ram_gb=64, jobs=8)
            if row[0] == 'tricky'][0]
        self.assertLess(pooled[2], serial[2])
        self.assertLessEqual(pooled[1], serial[1])

    def test_tiny_ram_forces_the_serial_ceiling(self):
        workers, _note = build_ux.course_workers(32, 9.0, jobs=16)
        self.assertEqual(workers, 1)
        workers, _note = build_ux.course_workers(12, 64.0, jobs=8)
        self.assertGreater(workers, 1)

    def test_table_labels_estimates_and_cites_sources(self):
        text = build_ux.estimate_table_text(target='android', tricky=True,
                                            cores=10, ram_gb=64, jobs=8)
        self.assertIn('ranges, not promises', text)
        self.assertIn('tk-speed-1 r13', text)
        self.assertIn('astra-buildtime-1', text)
        self.assertIn('release-pb71-1', text)

    def test_estimates_never_raise_without_host_facts(self):
        with patch.object(build_ux, 'host_facts', return_value=(0, None)):
            steps = build_ux.estimate_steps(target='ios', tricky=True)
        self.assertTrue(all(hi >= lo for _n, lo, hi in steps))


class FailureMessageTest(unittest.TestCase):
    def test_failure_block_names_step_log_resume_and_issue(self):
        lines = build_ux.failure_lines(
            'android-libs', Path('/w/build-ux.log'),
            build_ux.RESUME_NOTES['android-libs'],
            bundle_path=Path('/w/support-bundle-x.txt'))
        self.assertTrue(lines[0].startswith('BUILD FAILED at android-libs'))
        self.assertIn('/w/build-ux.log', lines[0])
        self.assertIn('re-run the same command to resume', lines[1])
        self.assertIn('open an issue with the build-failure template: '
                      'https://github.com/brad-richardson/ssx3-120/issues/new'
                      '?template=build-failure.md', lines[2])
        self.assertIn('/w/support-bundle-x.txt', lines[3])

    def test_tricky_resume_note_says_no_mid_step_resume(self):
        self.assertIn('does NOT resume mid-step',
                      build_ux.RESUME_NOTES['tricky'])

    def test_run_step_failure_prints_help_and_reraises(self):
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sys, 'stdout', stream):
                with self.assertRaisesRegex(ValueError, 'boom'):
                    build_ux.run_step(Path(tmp), 3, 5, 'android-libs', 3, 6,
                                      lambda: (_ for _ in ()).throw(
                                          ValueError('boom')))
            bundles = list(Path(tmp).glob('support-bundle-*.txt'))
        out = stream.getvalue()
        self.assertIn('[step 3/5] android-libs (est. 3-6 min on this machine)', out)
        self.assertIn('BUILD FAILED at android-libs', out)
        self.assertIn('build-failure.md', out)
        self.assertTrue(bundles)

    def test_run_step_success_prints_banner_and_elapsed(self):
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sys, 'stdout', stream):
                build_ux.run_step(Path(tmp), 1, 4, 'doctor', 0, 1,
                                  lambda: 'ok')
        out = stream.getvalue()
        self.assertIn('[step 1/4] doctor (est. 0-1 min on this machine)', out)
        self.assertIn('doctor done in ', out)
        self.assertNotIn('BUILD FAILED', out)


class FinalSummaryTest(unittest.TestCase):
    def test_summary_covers_time_artifact_install_keystore_tricky_rerun(self):
        stream = io.StringIO()
        with patch.object(sys, 'stdout', stream):
            build_ux.print_final_summary(
                total_seconds=754, artifact='/w/app-release.apk',
                install_cmd='adb install -r /w/app-release.apk',
                tricky=True, audio='off', audio_why='auto resolved off',
                skips=['prepare reused (inputs.json current for this disc)'],
                color=False)
        out = stream.getvalue()
        self.assertIn('Build finished in 12:34.', out)
        self.assertIn('Artifact: /w/app-release.apk', out)
        self.assertIn('Next: adb install -r /w/app-release.apk', out)
        self.assertIn('Back up your keystore', out)
        self.assertIn('Tricky: yes (audio off', out)
        self.assertIn('Rerun: prepare reused', out)

    def test_ios_signing_line_replaces_keystore(self):
        stream = io.StringIO()
        with patch.object(sys, 'stdout', stream):
            build_ux.print_final_summary(
                total_seconds=60, artifact='/w/ios-stage/x.app',
                tricky=False, color=False, signing='Signed with team T.')
        out = stream.getvalue()
        self.assertIn('Signed with team T.', out)
        self.assertNotIn('keystore', out)
        self.assertIn('Tricky: no (stock SSX 3 only', out)


class NonTTYTest(unittest.TestCase):
    def test_no_color_disables_styling(self):
        with patch.dict(os.environ, {'NO_COLOR': '1'}):
            self.assertFalse(build_ux.use_color(stream=io.StringIO()))
        self.assertEqual(build_ux.style('x', '32', False), 'x')

    def test_color_enabled_wraps_ansi(self):
        self.assertEqual(build_ux.style('x', '32', True), '\033[32mx\033[0m')

    def test_progress_uses_plain_lines_only(self):
        stream = io.StringIO()
        clock = [1000.0]
        prog = build_ux.TrickyProgress(10, stream=stream,
                                       time_fn=lambda: clock[0])
        prog.course_started('gari')
        prog.course_started('merq')
        clock[0] += 42
        prog.course_finished('gari')
        out = stream.getvalue()
        self.assertIn('tricky: worlds 1/10', out)
        self.assertIn('elapsed 0:42', out)
        self.assertNotIn('\r', out)
        self.assertNotIn('\033', out)

    def test_heartbeat_fires_after_a_quiet_minute(self):
        stream = io.StringIO()
        clock = [2000.0]
        prog = build_ux.TrickyProgress(10, stream=stream, heartbeat_s=60,
                                       time_fn=lambda: clock[0])
        prog.course_started('gari')
        clock[0] += 30
        prog.maybe_heartbeat()
        self.assertEqual(stream.getvalue(), '')
        clock[0] += 31
        prog.maybe_heartbeat()
        self.assertIn('tricky: worlds 0/10', stream.getvalue())


class SupportBundleTest(unittest.TestCase):
    def test_bundle_redacts_home_serial_and_udid(self):
        home = str(Path.home())
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            work.mkdir()
            (work / 'inputs.json').write_text(
                '{"iso": "%s/SSX3.iso", "iso_sha256": "abc"}' % home)
            (work / 'build-ux.log').write_text(
                '[step 5/5] install started\nadb -s XYZ123 shell pm path x\n'
                'device 00008110-001A2B3C4D5E6F70 lost\n')
            dest = build_ux.collect_support_bundle(
                work, builder_version='test-sha',
                steps=[('install', 'FAILED')])
            self.assertIsNotNone(dest)
            text = dest.read_text()
        self.assertNotIn(home, text)
        self.assertNotIn('XYZ123', text)
        self.assertNotIn('00008110-001A2B3C4D5E6F70', text)
        self.assertIn('<serial>', text)
        self.assertIn('<udid>', text)
        self.assertIn('iso_basename', text)
        self.assertIn('SSX3.iso', text)

    def test_redact_text_keeps_shas_and_versions(self):
        keep = 'sha256 abc123 vendor e8ed655ec6 versionCode 5'
        self.assertIn('abc123', build_ux.redact_text(keep))


class DryRunTest(unittest.TestCase):
    def test_android_dry_run_touches_nothing_heavy(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            args = types.SimpleNamespace(
                work=work, ssx3_iso=None, tricky_iso=None, install=False,
                prebuilt=False, jnilibs=None, prebuilt_cache=None, ndk=None,
                jdk=None, no_auto_download=False,
                accept_android_licenses=False, app_id=build.PUBLIC_APP_ID,
                jobs=2, target='android', tools_only=False,
                symbol_names=None, iso=None, dry_run=True)
            calls = []
            stream = io.StringIO()
            with patch.object(build, 'ensure_android_toolchain',
                              side_effect=lambda a: calls.append('toolchain')), \
                 patch.object(build, 'prepare',
                              side_effect=lambda a: calls.append('prepare')), \
                 patch.object(build, 'android_libs',
                              side_effect=lambda a: calls.append('android-libs')), \
                 patch.object(build, 'android',
                              side_effect=lambda a: calls.append('android')), \
                 patch.object(build, 'doctor',
                              side_effect=lambda a: calls.append('doctor')), \
                 patch.object(sys, 'stdout', stream):
                build.onecmd_dry_run(args, 'android')
            self.assertEqual(calls, ['doctor'])
            self.assertFalse(work.exists())
            out = stream.getvalue()
            self.assertIn('ranges, not promises', out)
            self.assertIn('[dry-run] prepare: MISSING (would run)', out)
            self.assertIn('[dry-run] nothing was built', out)


if __name__ == '__main__':
    unittest.main()
