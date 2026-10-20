"""Public install must let the app own writable directories on Android 15."""
import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

BUILD = Path(__file__).resolve().parents[1] / 'build.py'
SPEC = importlib.util.spec_from_file_location('ssx3_build', BUILD)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class PublicInstallStageTest(unittest.TestCase):
    def test_bootstraps_app_directories_and_repairs_pushed_resources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work = root / 'work'
            files = work / 'stage/files'
            resources = files / 'ge1-resources/shaders'
            resources.mkdir(parents=True)
            (resources / 'shader.bin').write_bytes(b'shader')
            for name in ('ps2x.env', 'full120.env'):
                (files / name).write_text(name)
            (work / 'inputs.json').write_text(json.dumps({}))
            apk = root / 'apps/android/app/build/outputs/apk/release/app-release.apk'
            apk.parent.mkdir(parents=True)
            apk.write_bytes(b'apk')
            elf = work / 'SLUS_207.72'
            elf.write_bytes(b'elf')
            calls = []
            directory_checks = {}
            def fake_adb(_args, *parts, capture=False):
                calls.append(parts)
                if parts[:3] == ('shell', 'pm', 'path'):
                    return 'package:/data/app/base.apk'
                if parts[:3] == ('shell', 'am', 'start'):
                    return 'Starting: Intent { cmp=' + parts[-1] + ' }'
                if parts[:2] == ('shell', 'sha256sum'):
                    remote = parts[2]
                    local = (resources / 'shader.bin') if remote.endswith('shader.bin') else \
                            (files / remote.rsplit('/', 1)[-1]) if remote.endswith('.env') else elf
                    return build.sha(local) + '  ' + remote
                return ''
            def fake_run(command, **_kwargs):
                if command[-3:-1] == ['test', '-d']:
                    remote = command[-1]
                    directory_checks[remote] = directory_checks.get(remote, 0) + 1
                    return types.SimpleNamespace(returncode=directory_checks[remote] == 1)
                return types.SimpleNamespace(returncode=1)
            args = types.SimpleNamespace(work=work, app_id='com.ssx3.recomp',
                                         dry_run=False, serial='test')
            with patch.object(build, 'ROOT', root), \
                 patch.object(build, 'stage', return_value=files), \
                 patch.object(build, 'verified_disc_payload', return_value=[(elf, 'SLUS_207.72')]), \
                 patch.object(build, 'apk_package', return_value=args.app_id), \
                 patch.object(build, 'adb', side_effect=fake_adb), \
                 patch.object(build.subprocess, 'run', side_effect=fake_run), \
                 patch.object(build.time, 'sleep'):
                build.install(args)
            self.assertFalse(any('mkdir' in str(part) for call in calls for part in call))
            self.assertLess(calls.index(('shell', 'am', 'start', '-n',
                                         'com.ssx3.recomp/com.ps2x.runner.LaunchAdb')),
                            next(i for i, call in enumerate(calls) if call[0] == 'push'))
            self.assertIn(('shell', 'chmod', '-R', 'o+rX',
                           '/sdcard/Android/data/com.ssx3.recomp/files/ge1-resources'), calls)
            self.assertEqual(directory_checks, {
                '/sdcard/Android/data/com.ssx3.recomp/files/ge1-data': 2,
                '/sdcard/Android/data/com.ssx3.recomp/files/mc0': 2,
            })


class LauncherAliasTest(unittest.TestCase):
    """10-19 Odin: '<app_id>/.Launch60' resolved to com.ssx3.recomp.Launch60,
    which does not exist (aliases live in the com.ps2x.runner namespace), so
    every public './build android --install' failed at the bootstrap.
    v0-builder-fix-1 S2: the always-enabled bootstrap alias (.LaunchAdb,
    no launcher icon) is tried first so custom --app-id builds with hidden
    launcher icons still bootstrap. Item 5: one launcher icon (.Launch120);
    the 60/120 mode persists in files/launch-hz, not in the alias name."""

    def test_components_are_fully_qualified_in_the_java_namespace(self):
        self.assertEqual(build.launcher_components('com.ssx3.recomp'),
                         ['com.ssx3.recomp/com.ps2x.runner.LaunchAdb',
                          'com.ssx3.recomp/com.ps2x.runner.Launch120'])

    def test_bootstrap_alias_is_tried_first(self):
        args = types.SimpleNamespace(app_id='com.example.test', serial='test')
        seen = []
        def ok_first(_a, *parts, capture=False):
            seen.append(parts[-1])
            return 'Starting: Intent { cmp=' + parts[-1] + ' }'
        with patch.object(build, 'adb', side_effect=ok_first):
            self.assertEqual(build.bootstrap_app(args),
                             'com.example.test/com.ps2x.runner.LaunchAdb')
        self.assertEqual(seen, ['com.example.test/com.ps2x.runner.LaunchAdb'])

    def test_falls_back_to_the_next_alias_and_fails_loudly(self):
        args = types.SimpleNamespace(app_id='com.ssx3.recomp', serial='test')
        seen = []
        def missing_adb(_a, *parts, capture=False):
            seen.append(parts[-1])
            if parts[-1].endswith('LaunchAdb'):
                return 'Error type 3\nError: Activity class {' + parts[-1] + '} does not exist.'
            return 'Starting: Intent { cmp=' + parts[-1] + ' }'
        with patch.object(build, 'adb', side_effect=missing_adb):
            self.assertEqual(build.bootstrap_app(args), 'com.ssx3.recomp/com.ps2x.runner.Launch120')
        with patch.object(build, 'adb', return_value='Error: Activity class does not exist.'):
            with self.assertRaises(ValueError):
                build.bootstrap_app(args)


class ManifestBootstrapAliasTest(unittest.TestCase):
    """S2: the manifest carries the always-enabled bootstrap alias.
    Item 5: one launcher icon (.Launch120); .Launch60 is gone."""

    def aliases(self):
        import xml.etree.ElementTree as ET
        manifest = Path(build.ROOT / 'apps/android/app/src/main/AndroidManifest.xml')
        tree = ET.parse(str(manifest))
        return {e.get('{http://schemas.android.com/apk/res/android}name'): e
                for e in tree.getroot().iter('activity-alias')}

    def test_launch_adb_is_exported_enabled_and_has_no_launcher_entry(self):
        adb = self.aliases().get('.LaunchAdb')
        self.assertIsNotNone(adb, 'missing .LaunchAdb bootstrap alias')
        self.assertEqual(adb.get('{http://schemas.android.com/apk/res/android}enabled'), 'true')
        self.assertEqual(adb.get('{http://schemas.android.com/apk/res/android}exported'), 'true')
        self.assertEqual(adb.get('{http://schemas.android.com/apk/res/android}targetActivity'),
                         '.LaunchActivity')
        self.assertEqual(list(adb.iter('intent-filter')), [])

    def test_single_launcher_icon_with_the_plain_app_label(self):
        aliases = self.aliases()
        self.assertNotIn('.Launch60', aliases)
        launcher = aliases.get('.Launch120')
        self.assertIsNotNone(launcher, 'missing .Launch120 single launcher alias')
        self.assertEqual(launcher.get('{http://schemas.android.com/apk/res/android}label'),
                         '${ps2xAppLabel}')
        self.assertEqual(len(list(launcher.iter('intent-filter'))), 1)


class LaunchModeFileTest(unittest.TestCase):
    """Item 5: the 60/120 mode persists in files/launch-hz (default 120);
    LaunchActivity reads the file, Set2Relaunch writes it and relaunches
    the single alias."""

    def test_launch_activity_reads_the_mode_file_with_a_120_default(self):
        src = (Path(build.ROOT / 'apps/android/app/src/main/java/com/ps2x/runner'
                     ) / 'LaunchActivity.java').read_text()
        self.assertIn('launch-hz', src)
        self.assertIn('HZ_120', src)
        self.assertNotIn('.Launch60', src)
        self.assertNotIn('targetFor', src)

    def test_set2_relaunch_writes_the_mode_file_and_targets_the_single_alias(self):
        src = (Path(build.ROOT / 'apps/android/app/src/main/java/com/ps2x/runner'
                     ) / 'Set2Relaunch.java').read_text()
        self.assertIn('launch-hz', src)
        self.assertIn('.Launch120', src)
        self.assertNotIn('.Launch60', src)


if __name__ == '__main__':
    unittest.main()
