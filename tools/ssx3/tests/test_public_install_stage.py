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
            apk = root / 'android/app/build/outputs/apk/release/app-release.apk'
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
                                         'com.ssx3.recomp/android.app.NativeActivity')),
                            next(i for i, call in enumerate(calls) if call[0] == 'push'))
            self.assertIn(('shell', 'chmod', '-R', 'o+rX',
                           '/sdcard/Android/data/com.ssx3.recomp/files/ge1-resources'), calls)
            self.assertEqual(directory_checks, {
                '/sdcard/Android/data/com.ssx3.recomp/files/ge1-data': 2,
                '/sdcard/Android/data/com.ssx3.recomp/files/mc0': 2,
            })


if __name__ == '__main__':
    unittest.main()
