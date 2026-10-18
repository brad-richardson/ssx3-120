"""One-command builder flow: wrapper, PEP 723, tricky validation, resume.

No network, no disc: the ISO under test is synthetic (ISO9660 magic only),
heavy steps are stubbed, and licence prompts run non-interactive.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

SPEC = importlib.util.spec_from_file_location('ssx3_build', HERE / 'build.py')
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)

WRAPPER = ROOT / 'build'


def make_iso(path, magic=True):
    data = bytearray(18 * 2048)
    if magic:
        data[16 * 2048:16 * 2048 + 7] = b'\x01CD001\x01'
    Path(path).write_bytes(bytes(data))


def onecmd_args(work, **over):
    base = dict(work=work, ssx3_iso=None, tricky_iso=None, install=False,
                prebuilt=False, jnilibs=None, prebuilt_cache=None, ndk=None,
                jdk=None, no_auto_download=False, accept_android_licenses=False,
                app_id=build.PUBLIC_APP_ID, jobs=2, target='android',
                tools_only=False, symbol_names=None, iso=None)
    base.update(over)
    return types.SimpleNamespace(**base)


class WrapperTest(unittest.TestCase):
    @unittest.skipUnless((ROOT / 'build').is_file(), 'product ./build wrapper')
    def test_wrapper_is_posix_shell_over_uv(self):
        text = WRAPPER.read_text()
        self.assertTrue(os.access(WRAPPER, os.X_OK))
        self.assertTrue(text.startswith('#!/bin/sh'))
        self.assertIn('exec uv run --locked --script', text)
        self.assertIn('UV_PYTHON_PREFERENCE', text)
        self.assertIn('only-managed', text)
        self.assertIn('tools/ssx3/build.py', text)
        self.assertIn('https://astral.sh/uv/install.sh', text)
        probe = subprocess.run(['sh', '-n', str(WRAPPER)], capture_output=True)
        self.assertEqual(probe.returncode, 0)

    def test_build_py_carries_pep723_metadata(self):
        head = (HERE / 'build.py').read_text().split('"""')[0]
        self.assertIn('# /// script', head)
        self.assertIn('requires-python', head)
        self.assertIn('numpy', head)
        self.assertIn('Pillow', head)
        self.assertNotIn('imageio-ffmpeg', head)

    @unittest.skipUnless((ROOT / 'Makefile').is_file(), 'product Makefile')
    def test_makefile_targets_delegate_to_the_wrapper(self):
        text = (ROOT / 'Makefile').read_text()
        self.assertIn('./build', text)
        self.assertIn('--ssx3-iso', text)
        for target in ('android:', 'doctor:', 'install:'):
            self.assertIn(target, text)


class TrickyIsoTest(unittest.TestCase):
    def test_tricky_supported_with_the_driver_in_checkout(self):
        self.assertTrue(build.tricky_supported())

    def test_missing_driver_reports_the_later_version_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            iso = Path(tmp) / 'tricky.iso'
            iso.write_bytes(b'\x00' * (16 * 2048) + b'\x01CD001\x01')
            with patch.object(build, 'TRICKY_DRIVER', Path(tmp) / 'missing.py'):
                self.assertFalse(build.tricky_supported())
                with self.assertRaisesRegex(ValueError, 'later version'):
                    build.validate_tricky_iso(iso)

    def test_missing_tricky_iso_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'not found'):
                build.validate_tricky_iso(Path(tmp) / 'nope.iso')

    def test_non_iso_tricky_file_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            iso = Path(tmp) / 'fake.iso'
            make_iso(iso, magic=False)
            with self.assertRaisesRegex(ValueError, 'not an ISO9660'):
                build.validate_tricky_iso(iso)

    def test_valid_iso_passes_cheap_validation_with_the_driver(self):
        with tempfile.TemporaryDirectory() as tmp:
            iso = Path(tmp) / 'tricky.iso'
            make_iso(iso)
            self.assertEqual(build.validate_tricky_iso(iso), iso)


class ResumeTest(unittest.TestCase):
    def test_empty_work_dir_runs_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.assertFalse(build.prepare_current(work))
            self.assertFalse(build.android_libs_current(work))

    def test_prepare_from_another_disc_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / 'inputs.json').write_text(json.dumps({'iso_sha256': '0' * 64}))
            with self.assertRaisesRegex(ValueError, 'different disc'):
                build.prepare_current(work)

    def test_current_markers_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            slim = work / 'SSX3.iso'
            slim.write_bytes(b'slim')
            (work / 'SLUS_207.72').write_bytes(b'elf')
            (work / 'codegen').mkdir()
            (work / 'inputs.json').write_text(json.dumps(
                {'iso_sha256': build.ISO_SHA, 'slim_iso': str(slim)}))
            self.assertTrue(build.prepare_current(work))
            jni = work / 'jniLibs/arm64-v8a'
            jni.mkdir(parents=True)
            (jni / 'libge1_gs.so').write_bytes(b'gs')
            (jni / 'libmv2_microvu.so').write_bytes(b'mv2')
            (work / 'android-resources').mkdir()
            self.assertTrue(build.android_libs_current(work))


class AppLabelTest(unittest.TestCase):
    """G7 (public-parity-2): --app-label sets the home-screen name on both
    platforms; product ids default to 'SSX3 120', test ids to 'SSX3 120 Test'."""

    def test_product_ids_default_to_ssx3_120(self):
        self.assertEqual(build.default_app_label(build.PUBLIC_APP_ID), 'SSX3 120')
        self.assertEqual(build.default_app_label(build.PLAY_APP_ID), 'SSX3 120')

    def test_test_id_defaults_to_distinguishable_label(self):
        self.assertEqual(build.default_app_label('com.ssx3.test'), 'SSX3 120 Test')

    def test_explicit_label_wins_over_default(self):
        args = types.SimpleNamespace(app_id=build.PUBLIC_APP_ID, app_label='Custom Name')
        self.assertEqual(build.resolve_app_label(args), 'Custom Name')
        args = types.SimpleNamespace(app_id='com.ssx3.test', app_label=None)
        self.assertEqual(build.resolve_app_label(args), 'SSX3 120 Test')

    def test_android_shell_carries_label_placeholder(self):
        manifest = (ROOT / 'apps/android/app/src/main/AndroidManifest.xml').read_text()
        self.assertIn('${ps2xAppLabel}', manifest)
        gradle = (ROOT / 'apps/android/app/build.gradle').read_text()
        self.assertIn("def ps2xAppLabel = project.findProperty('ps2xAppLabel')", gradle)
        self.assertIn("'SSX3 120'", gradle)
        self.assertIn('manifestPlaceholders = [ps2xAppLabel: ps2xAppLabel]', gradle)

    def test_builder_passes_label_to_both_platforms(self):
        source = (HERE / 'build.py').read_text()
        self.assertIn("'-Pps2xAppLabel='", source)
        self.assertIn("'-DPS2X_APP_DISPLAY_NAME='", source)
        self.assertIn('--app-label', source)


class AndroidReceiptTest(unittest.TestCase):
    """G6 (public-parity-2): the Android build writes a receipt recording the
    effective microVU/GE1 library SHAs, Turnip inclusion, PGO profile and
    preset SHAs."""

    def test_receipt_records_effective_inputs(self):
        import json as jsonlib
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            jni = work / 'jniLibs'
            (jni / 'arm64-v8a').mkdir(parents=True)
            (jni / 'arm64-v8a' / 'libmv2_microvu.so').write_bytes(b'mv2')
            (jni / 'arm64-v8a' / 'libge1_gs.so').write_bytes(b'gs')
            (work / 'android-resources').mkdir()
            a = types.SimpleNamespace(work=work, jnilibs=jni, ndk=None,
                                      prebuilt=False, prebuilt_cache=None,
                                      no_turnip=True, app_id=build.PUBLIC_APP_ID,
                                      app_label=None, jobs=2, pgo_profile=None,
                                      no_pgo=True)
            seen = {}

            def fake_run(*args, **kwargs):
                seen['args'] = list(map(str, args))
            with patch.object(build, 'ensure_android_toolchain'), \
                 patch.object(build, 'support_bundle', return_value=None), \
                 patch.object(build, 'android_libs'), \
                 patch.object(build, 'ensure_keystore', return_value=work / 'k'), \
                 patch.object(build, 'next_version_code', return_value=1), \
                 patch.object(build, 'resolve_vu0_stage', return_value=str(work / 'vu0')), \
                 patch.object(build, 'run', side_effect=fake_run):
                build.android(a)
            receipt = jsonlib.loads((work / 'android-app.json').read_text())
            self.assertEqual(receipt['app_id'], build.PUBLIC_APP_ID)
            self.assertEqual(receipt['app_label'], 'SSX3 120')
            self.assertEqual(receipt['microvu_lib_sha256'], build.sha(
                jni / 'arm64-v8a' / 'libmv2_microvu.so'))
            self.assertEqual(receipt['ge1_lib_sha256'], build.sha(
                jni / 'arm64-v8a' / 'libge1_gs.so'))
            self.assertFalse(receipt['turnip_included'])
            self.assertIsNone(receipt['turnip_binary_sha256'])
            self.assertIsNone(receipt['pgo_profile'])
            self.assertEqual(receipt['preset_sha256'],
                             build.sha(build.ROOT / 'presets/ps2x.env'))
            self.assertEqual(receipt['ge1_vendor'], build.ANDROID_GE1_VENDOR_PIN)
            gradle_args = seen['args']
            self.assertIn('-Pps2xAppLabel=SSX3 120', gradle_args)


class OneCommandOrderTest(unittest.TestCase):
    def run_onecmd(self, current, install=False):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            work.mkdir()
            iso = Path(tmp) / 'SSX3.iso'
            make_iso(iso)
            args = onecmd_args(work, ssx3_iso=iso, install=install)
            calls = []
            with patch.object(build, 'validate_ssx3_iso',
                              side_effect=lambda p: calls.append('iso') or iso.resolve()), \
                 patch.object(build, 'preflight_host_tools',
                              side_effect=lambda: calls.append('preflight') or True), \
                 patch.object(build, 'ensure_android_toolchain',
                              side_effect=lambda a: calls.append('toolchain') or setattr(a, 'ndk', work)), \
                 patch.object(build, 'doctor', side_effect=lambda a: calls.append('doctor')), \
                 patch.object(build, 'prepare', side_effect=lambda a: calls.append('prepare')), \
                 patch.object(build, 'android_libs', side_effect=lambda a: calls.append('android-libs')), \
                 patch.object(build, 'android', side_effect=lambda a: calls.append('android')), \
                 patch.object(build, 'install', side_effect=lambda a: calls.append('install')), \
                 patch.object(build, 'prepare_current', return_value=current), \
                 patch.object(build, 'android_libs_current', return_value=current):
                build.android_onecmd(args)
            return calls, args

    def test_fresh_work_runs_every_step_in_order(self):
        calls, args = self.run_onecmd(False)
        # F12: disc identity + host preflight precede any toolchain download.
        self.assertEqual(calls, ['iso', 'preflight', 'toolchain', 'doctor', 'prepare', 'android-libs', 'android'])
        self.assertEqual(args.iso, args.ssx3_iso.resolve())
        self.assertEqual(args.jnilibs, args.work / 'jniLibs')

    def test_current_outputs_skip_to_android(self):
        calls, _ = self.run_onecmd(True)
        self.assertEqual(calls, ['iso', 'preflight', 'toolchain', 'doctor', 'android'])

    def test_install_flag_runs_install_last(self):
        calls, _ = self.run_onecmd(True, install=True)
        self.assertEqual(calls, ['iso', 'preflight', 'toolchain', 'doctor', 'android', 'install'])

    def test_prebuilt_leaves_bundle_selection_to_android(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            work.mkdir()
            args = onecmd_args(work, ssx3_iso=Path(tmp) / 'x.iso', prebuilt=True)
            with patch.object(build, 'validate_ssx3_iso', return_value=Path(tmp) / 'x.iso'), \
                 patch.object(build, 'preflight_host_tools', return_value=True), \
                 patch.object(build, 'ensure_android_toolchain'), \
                 patch.object(build, 'doctor'), \
                 patch.object(build, 'prepare'), \
                 patch.object(build, 'android_libs'), \
                 patch.object(build, 'android'), \
                 patch.object(build, 'prepare_current', return_value=True), \
                 patch.object(build, 'android_libs_current', return_value=True):
                build.android_onecmd(args)
            self.assertIsNone(args.jnilibs)


class DryRunGateTest(unittest.TestCase):
    """F06: the first iOS dry-run works before prepare."""

    def test_needs_prepare_truth_table(self):
        self.assertFalse(build.needs_prepare('prepare', False))
        self.assertFalse(build.needs_prepare('tricky', False))
        self.assertFalse(build.needs_prepare('ios', True))
        self.assertTrue(build.needs_prepare('ios', False))
        self.assertTrue(build.needs_prepare('install', True))
        self.assertTrue(build.needs_prepare('android', False))

    def test_ios_dry_run_needs_no_inputs_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'fresh-work'
            work.mkdir()
            args = onecmd_args(work, team='ABCDE12345', device=None,
                               install=False, dry_run=True,
                               bundle_id=None, app_id=build.PUBLIC_APP_ID,
                               app_label=None, om1_stage=None,
                               no_pgo=True, pgo_profile=None)
            build.ios(args)  # must not raise on missing prepare outputs

    def test_documented_first_command_is_one_command_dry_run(self):
        text = (ROOT / 'docs/agent-setup.md').read_text()
        self.assertIn('./build ios --ssx3-iso ~/SSX3.iso --dry-run --team <TEAM10> --work ~/ssx3-work', text)


class CapacityTest(unittest.TestCase):
    """F15: disk preflight with documented free-space numbers."""

    def test_full_disk_fails_with_numbers(self):
        import collections
        usage = collections.namedtuple('usage', 'total used free')
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            work.mkdir()
            with patch.object(build.shutil, 'disk_usage',
                              return_value=usage(100, 99, 1 * 1024**3)):
                with self.assertRaisesRegex(ValueError, r'only 1.0 GB free.*need ~25 GB.*android'):
                    build.check_capacity(work, 'android')

    def test_roomy_disk_passes(self):
        import collections
        usage = collections.namedtuple('usage', 'total used free')
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            work.mkdir()
            with patch.object(build.shutil, 'disk_usage',
                              return_value=usage(500 * 1024**3, 100 * 1024**3, 400 * 1024**3)):
                self.assertTrue(build.check_capacity(work, 'android'))
                self.assertTrue(build.check_capacity(work, 'other-target'))

    def test_memory_probe_never_raises(self):
        self.assertTrue(build.host_memory_bytes() is None or build.host_memory_bytes() > 0)

    def test_budgets_are_documented(self):
        text = (ROOT / 'README.md').read_text()
        self.assertIn('25 GB free', text)
        self.assertIn('20 GB free', text)
        self.assertIn('3.4 GB', text)


class InstallDeviceTest(unittest.TestCase):
    """F14: --install discovers its target in the doctor, not after the build."""

    def collect(self, func, *args):
        seen = []
        func(*args, lambda label, ok, detail, fix: seen.append((label, ok, detail)))
        return {label: (ok, detail) for label, ok, detail in seen}

    def android_args(self, **over):
        base = dict(work=None, serial=None, install=True)
        base.update(over)
        return types.SimpleNamespace(**base)

    def test_single_ready_device_passes(self):
        args = self.android_args()
        listing = 'List of devices attached\nXYZ123\tdevice\n'
        with patch.object(build, 'resolve_adb', return_value='/sdk/adb'), \
             patch.object(build, 'probe', return_value=(True, listing)):
            got = self.collect(build.check_install_device, args)
        self.assertEqual(got['Android device'], (True, 'XYZ123'))

    def test_no_device_fails_with_the_fix(self):
        args = self.android_args()
        with patch.object(build, 'resolve_adb', return_value='/sdk/adb'), \
             patch.object(build, 'probe', return_value=(True, 'List of devices attached\n\n')):
            got = self.collect(build.check_install_device, args)
        self.assertFalse(got['Android device'][0])
        self.assertIn('no Android device', got['Android device'][1])

    def test_unauthorized_device_names_trust(self):
        args = self.android_args()
        listing = 'List of devices attached\nXYZ123\tunauthorized\n'
        with patch.object(build, 'resolve_adb', return_value='/sdk/adb'), \
             patch.object(build, 'probe', return_value=(True, listing)):
            got = self.collect(build.check_install_device, args)
        self.assertFalse(got['Android device'][0])
        self.assertIn('unauthorized', got['Android device'][1])

    def test_several_devices_require_serial(self):
        args = self.android_args()
        listing = 'List of devices attached\nAAA\tdevice\nBBB\tdevice\n'
        with patch.object(build, 'resolve_adb', return_value='/sdk/adb'), \
             patch.object(build, 'probe', return_value=(True, listing)):
            got = self.collect(build.check_install_device, args)
        self.assertFalse(got['Android device'][0])
        self.assertIn('several', got['Android device'][1])

    def test_named_serial_must_be_connected(self):
        args = self.android_args(serial='ZZZ')
        listing = 'List of devices attached\nAAA\tdevice\n'
        with patch.object(build, 'resolve_adb', return_value='/sdk/adb'), \
             patch.object(build, 'probe', return_value=(True, listing)):
            got = self.collect(build.check_install_device, args)
        self.assertFalse(got['Android device'][0])
        self.assertIn('ZZZ', got['Android device'][1])

    def test_ios_install_resolves_the_default_device(self):
        listing = '* 00008110-001A2B3C4D5E6F70 (UDID) connected physical\n'
        args = types.SimpleNamespace(device=None, install=True, team=None)
        with patch.object(build, 'devicectl_devices', return_value=(True, listing)):
            got = self.collect(build.doctor_ios, args)
        self.assertTrue(got['iOS device'][0])

    def test_ios_install_without_device_fails_early(self):
        args = types.SimpleNamespace(device=None, install=True, team=None)
        with patch.object(build, 'devicectl_devices', return_value=(True, 'no devices\n')):
            got = self.collect(build.doctor_ios, args)
        self.assertFalse(got['iOS device'][0])


class IsoPreflightTest(unittest.TestCase):
    """F12: disc identity and host tools precede any toolchain download."""

    def test_missing_iso_names_the_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'SSX 3 ISO not found.*ssx3-inputs'):
                build.validate_ssx3_iso(Path(tmp) / 'nope.iso')

    def test_non_iso_file_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            iso = Path(tmp) / 'fake.iso'
            make_iso(iso, magic=False)
            with self.assertRaisesRegex(ValueError, 'not an ISO9660'):
                build.validate_ssx3_iso(iso)

    def test_wrong_disc_fails_before_any_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            iso = Path(tmp) / 'other.iso'
            make_iso(iso)
            with self.assertRaisesRegex(ValueError, 'unsupported disc SHA256.*SLUS-207.72'):
                build.validate_ssx3_iso(iso)

    def test_preflight_passes_on_this_host(self):
        self.assertTrue(build.preflight_host_tools())

    def test_preflight_names_missing_tools(self):
        with patch.object(build, 'probe', return_value=(False, 'missing')), \
             patch.object(build, 'compiler', side_effect=ValueError('no compiler')):
            with self.assertRaisesRegex(ValueError, 'host preflight: missing.*git.*install with'):
                build.preflight_host_tools()


class DeviceFlagTest(unittest.TestCase):
    """F11: --serial is Android-only, --device is iOS-only."""

    def test_helper_truth_table(self):
        self.assertIsNone(build.check_device_flags('android', None, 'XYZ'))
        self.assertIsNone(build.check_device_flags('ios', 'udid', None))
        self.assertIn('--serial', build.check_device_flags('android', 'udid', None))
        self.assertIn('--device', build.check_device_flags('ios', None, 'XYZ'))
        self.assertIsNone(build.check_device_flags('doctor', 'udid', None))

    def test_parser_rejects_swapped_selectors(self):
        for step, device, serial, hint in (
                ('android', 'udid', None, '--serial'),
                ('ios', None, 'XYZ', '--device')):
            argv = [step, '--work', '/tmp/w', '--ssx3-iso', '/tmp/x.iso']
            if device:
                argv += ['--device', device]
            if serial:
                argv += ['--serial', serial]
            proc = subprocess.run([sys.executable, HERE / 'build.py'] + argv,
                                  capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(hint, proc.stderr)

    def test_guide_names_serial_for_android(self):
        text = (ROOT / 'docs/agent-setup.md').read_text()
        self.assertIn('--serial <adb-serial>', text)
        self.assertNotIn('--device <serial>', text)


class WorkDirCreationTest(unittest.TestCase):
    """F03: one-command paths create WORK before any step writes into it."""

    def test_android_onecmd_creates_missing_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'new-work'
            iso = Path(tmp) / 'SSX3.iso'
            make_iso(iso)
            args = onecmd_args(work, ssx3_iso=iso)
            with patch.object(build, 'validate_ssx3_iso', return_value=iso.resolve()), \
                 patch.object(build, 'preflight_host_tools', return_value=True), \
                 patch.object(build, 'ensure_android_toolchain'), \
                 patch.object(build, 'doctor'), \
                 patch.object(build, 'prepare'), \
                 patch.object(build, 'android_libs'), \
                 patch.object(build, 'android'), \
                 patch.object(build, 'prepare_current', return_value=True), \
                 patch.object(build, 'android_libs_current', return_value=True):
                build.android_onecmd(args)
            self.assertTrue(work.is_dir())

    def test_ios_onecmd_creates_missing_work_before_doctor(self):
        created_before_doctor = {}
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'new-work'
            iso = Path(tmp) / 'SSX3.iso'
            make_iso(iso)
            args = onecmd_args(work, ssx3_iso=iso, team='ABCDE12345',
                               device=None, install=False, dry_run=True)
            def fake_doctor(a):
                created_before_doctor['work_existed'] = work.is_dir()
            with patch.object(build, 'validate_ssx3_iso', return_value=iso.resolve()), \
                 patch.object(build, 'preflight_host_tools', return_value=True), \
                 patch.object(build, 'doctor', side_effect=fake_doctor), \
                 patch.object(build, 'ios') as ios_step:
                build.ios_onecmd(args)
            self.assertTrue(created_before_doctor.get('work_existed'), 'WORK must exist when doctor runs')
            self.assertTrue(work.is_dir())
            ios_step.assert_called_once()


class AdbResolutionTest(unittest.TestCase):
    """F01: install shells the selected SDK's adb, and the doctor checks it."""

    def make_managed_adb(self, work):
        import stat
        adb = work / 'toolchain' / 'android-sdk' / 'platform-tools' / 'adb'
        adb.parent.mkdir(parents=True, exist_ok=True)
        adb.write_text('#!/bin/sh\necho "Android Debug Bridge version 1.0.41"\n')
        adb.chmod(adb.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return adb

    def test_adb_shells_the_managed_binary_with_serial(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            managed = self.make_managed_adb(work)
            args = onecmd_args(work, serial='XYZ123')
            seen = {}
            with patch.object(build, 'run',
                              side_effect=lambda *a, **k: seen.setdefault('cmd', list(map(str, a)))):
                build.adb(args, 'install', '-r', 'app.apk')
            self.assertEqual(seen['cmd'][:4], [str(managed), '-s', 'XYZ123', 'install'])

    def test_adb_without_serial_omits_selector(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            managed = self.make_managed_adb(work)
            args = onecmd_args(work, serial=None)
            seen = {}
            with patch.object(build, 'subprocess') as proc:
                proc.check_output.return_value = 'package:/x\n'
                out = build.adb(args, 'shell', 'pm', 'path', 'com.ssx3.recomp', capture=True)
            self.assertEqual(out, 'package:/x')
            cmd = list(map(str, proc.check_output.call_args[0][0]))
            self.assertEqual(cmd[:2], [str(managed), 'shell'])

    def test_adb_missing_fails_closed(self):
        import toolchain
        with tempfile.TemporaryDirectory() as tmp:
            args = onecmd_args(Path(tmp) / 'work', serial=None)
            with patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}, clear=True), \
                 patch.object(toolchain, '_common_sdk_dirs', return_value=[]):
                with self.assertRaisesRegex(ValueError, 'adb not found'):
                    build.adb(args, 'version')

    def test_doctor_reports_missing_adb(self):
        import toolchain
        with tempfile.TemporaryDirectory() as tmp:
            args = onecmd_args(Path(tmp) / 'work')
            with patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}, clear=True), \
                 patch.object(toolchain, '_common_jdk_dirs', return_value=[]), \
                 patch.object(toolchain, '_common_sdk_dirs', return_value=[]), \
                 patch.object(toolchain, 'find_adb', return_value=None):
                with self.assertRaisesRegex(ValueError, 'adb'):
                    build.doctor(args)


class ManagedLicenceInInstallTest(unittest.TestCase):
    def test_managed_sdk_without_flag_fails_with_the_documented_path(self):
        import stat
        import toolchain
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            sdk = work / 'toolchain' / 'android-sdk'
            (sdk / 'platforms/android-34').mkdir(parents=True)
            (sdk / 'platforms/android-34/android.jar').write_bytes(b'jar')
            (sdk / 'build-tools/34.0.0').mkdir(parents=True)
            (sdk / 'build-tools/34.0.0/aapt2').write_bytes(b'aapt2')
            java = work / 'toolchain' / 'jdk-test' / 'bin/java'
            java.parent.mkdir(parents=True)
            java.write_text('#!/bin/sh\necho "openjdk version \\"17.0.20\\" 2025-01-01"\n')
            java.chmod(java.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            args = types.SimpleNamespace(work=work, jdk=None, ndk=None,
                                         accept_android_licenses=False)
            with patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}, clear=True), \
                 patch.object(toolchain, '_common_jdk_dirs', return_value=[]), \
                 patch.object(toolchain, '_common_sdk_dirs', return_value=[]), \
                 patch('sys.stdin') as stdin:
                stdin.isatty.return_value = False
                with self.assertRaisesRegex(ValueError, r'--accept-android-licenses'):
                    build.ensure_install_toolchain(args)


if __name__ == '__main__':
    unittest.main()
