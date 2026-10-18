"""iOS compiler selection (D05) and CPU tuning (D04) in the public builder.

No network, no Xcode, no device: xcrun/version probes and the cmake driver
are stubbed; fixtures are synthetic.
"""
import importlib.util
import io
import json
import os
import plistlib
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

BUILD = Path(__file__).resolve().parents[1] / 'build.py'
SPEC = importlib.util.spec_from_file_location('ssx3_build_ios_flags', BUILD)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def ios_args(work, **over):
    base = dict(work=work, jobs=2, team='AB12CD34EF', bundle_id=None,
                device=None, install=False, dry_run=False, om1_stage=None,
                pgo_profile=None, no_pgo=False, stock_dump=None,
                skip_fonts=True)
    base.update(over)
    return types.SimpleNamespace(**base)


def make_stage(root):
    stage = Path(root) / 'stage'
    for name in build.OM1_STAGE_REQUIRED:
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('// stub')
    (stage / 'sp1.s').write_text('// asm')
    (stage / 'sp1_tables.c').write_text('// tables')
    return stage


def make_work(root, register_text='register-fresh'):
    work = Path(root) / 'work'
    (work / 'codegen').mkdir(parents=True)
    (work / 'codegen' / 'register_functions.cpp').write_text(register_text)
    iso = work / 'disc.iso'
    iso.write_bytes(b'iso-bytes')
    slim = work / 'SSX3.iso'
    slim.write_bytes(b'slim-bytes')
    table = Path(str(slim) + '.holes')
    table.write_bytes(b'holes-bytes')
    elf = work / 'SLUS_207.72'
    elf.write_bytes(b'elf-bytes')
    (work / 'inputs.json').write_text(json.dumps({
        'iso': str(iso), 'iso_sha256': build.sha(iso),
        'slim_iso': str(slim), 'slim_sha256': build.sha(slim),
        'slim_table_sha256': build.sha(table),
        'elf_sha256': build.sha(elf)}))
    libs = work / 'ios-libs'
    for name in ('libge1_gs.a', 'libPCSX2.a', 'libcommon.a', 'libSDL2.a'):
        (libs / name).parent.mkdir(parents=True, exist_ok=True)
        (libs / name).write_bytes(b'a')
    (libs / 'om1/pcsx2').mkdir(parents=True)
    (libs / 'om1/pcsx2/libPCSX2.a').write_bytes(b'a')
    (libs / 'MoltenVK.framework').mkdir(parents=True)
    (work / 'ios-ffmpeg/lib').mkdir(parents=True)
    (work / 'ios-ffmpeg/lib/libavcodec.a').write_bytes(b'a')
    ge1 = work / 'ge1-build'
    ge1.mkdir()
    vendor = work / 'ios-vendor'
    vendor.mkdir()
    om1 = work / 'om1-lib'
    om1.mkdir()
    (work / 'ios-libs.json').write_text(json.dumps(
        {'ge1_dir': str(ge1), 'om1_dir': str(om1),
         'ge1_vendor': build.GE1_VENDOR_PIN, 'om1_vendor': build.OM1_VENDOR_PIN,
         'sdl2_tarball_sha256': build.SDL2_TARBALL_SHA,
         'ffmpeg_tarball_sha256': build.FFMPEG_TARBALL_SHA,
         'ios_dep_pins': {n: c for n, _, _, c in build.IOS_DEPS}}))
    return work


class ReleaseFlagsTest(unittest.TestCase):
    def test_mcpu_always_present(self):
        flags = build.ios_release_flags('')
        self.assertEqual(len(flags), 2)
        for flag in flags:
            self.assertIn('-mcpu=apple-a15', flag)
            self.assertIn('-O3 -DNDEBUG', flag)
            self.assertNotIn('fprofile-use', flag)

    def test_pgo_use_appends(self):
        flags = build.ios_release_flags('/w/pgo-ios.profdata')
        self.assertEqual(len(flags), 3)
        self.assertIn('-fprofile-use=/w/pgo-ios.profdata', flags[0])
        self.assertIn('-fprofile-use=/w/pgo-ios.profdata', flags[1])
        self.assertEqual(flags[2],
                         '-DCMAKE_EXE_LINKER_FLAGS_RELEASE=-fprofile-use=/w/pgo-ios.profdata')


class IosCompilerPlumbingTest(unittest.TestCase):
    def run_ios(self, tmp, **over):
        work = make_work(tmp)
        stage = make_stage(tmp)
        a = ios_args(work, om1_stage=stage, **over)
        calls = []
        seen_env = []
        real_run = build.run

        def fake_run(*args, **kwargs):
            calls.append(list(map(str, args)))
            seen_env.append(('CC' in os.environ, 'CXX' in os.environ))
            # The TFX coverage writer is stdlib-only: let it run for real so
            # the staged bundle under test carries the file the preset knob
            # points at (same pass-through as the VU0/TFX lane tests use).
            if any('write_prewarm.py' in str(x) for x in args):
                return real_run(*args, **kwargs)
            if args[0] == 'cmake' and args[1] == '--build':
                built = work / 'ios-app-build/ps2xRuntime/Release-iphoneos/ps2EntryRunner.app'
                built.mkdir(parents=True)
                (built / 'ps2EntryRunner').write_bytes(b'bin')
                with open(built / 'Info.plist', 'wb') as f:
                    plistlib.dump({'CFBundleIdentifier': 'com.example.x'}, f)

        out = io.StringIO()
        with patch.object(build, 'run', side_effect=fake_run), \
             patch.object(build, 'sign_staged_app', return_value='fake-id'), \
             patch.object(build, 'font_pack', return_value=None), \
             patch.object(build, 'xcrun_llvm',
                          return_value=('/fake/clang', '/fake/clang++')), \
             patch.object(build, 'ios_build_versions', return_value={
                 'c_compiler': '/fake/clang', 'cxx_compiler': '/fake/clang++',
                 'c_compiler_version': 'Apple clang 21', 'xcode_version': 'Xcode 27.0',
                 'iphoneos_sdk_version': '27.0'}), \
             patch.dict(os.environ, {'CC': 'bogus-cc', 'CXX': 'bogus-cxx'}), \
             redirect_stdout(out):
            build.ios(a)
            restored = (os.environ.get('CC'), os.environ.get('CXX'))
        return work, calls, seen_env, out.getvalue(), restored

    def test_configure_uses_xcrun_compilers_and_mcpu(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, calls, seen_env, text, restored = self.run_ios(tmp, no_pgo=True)
            configure = calls[0]
            self.assertIn('-DCMAKE_C_COMPILER=/fake/clang', configure)
            self.assertIn('-DCMAKE_CXX_COMPILER=/fake/clang++', configure)
            self.assertTrue(any('-mcpu=apple-a15' in c for c in configure))
            self.assertFalse(any('fprofile-use' in c for c in configure))
            self.assertIn('iOS compilers: /fake/clang / /fake/clang++', text)
            # CC/CXX cleared for configure AND build, restored after
            # (the TFX coverage writer runs under the ambient env; only the
            # cmake driver calls carry the cleared-env contract).
            cmake_env = [e for c, e in zip(calls, seen_env) if c[0] == 'cmake']
            self.assertEqual(cmake_env, [(False, False), (False, False)])
            self.assertEqual(restored, ('bogus-cc', 'bogus-cxx'))

    def test_receipt_records_versions_and_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, calls, seen_env, text, restored = self.run_ios(tmp, no_pgo=True)
            receipt = json.loads((work / 'ios-app.json').read_text())
            self.assertEqual(receipt['xcode_version'], 'Xcode 27.0')
            self.assertEqual(receipt['iphoneos_sdk_version'], '27.0')
            self.assertEqual(receipt['c_compiler'], '/fake/clang')
            self.assertEqual(receipt['cxx_compiler'], '/fake/clang++')
            self.assertIn('-mcpu=apple-a15', receipt['c_flags_release'])
            self.assertIsNone(receipt['pgo_profile'])

    def test_fresh_pgo_flows_into_flags_and_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = make_work(tmp)
            manifest = {'trained_codegen_register_sha256': build.sha(
                work / 'codegen/register_functions.cpp'),
                'profile_sha256': '0' * 64}
            stage = make_stage(tmp)
            prof = Path(tmp) / 'own.profdata'
            prof.write_bytes(b'p')
            a = ios_args(work, om1_stage=stage, pgo_profile=prof)
            real_run = build.run
            with patch.object(build, 'pgo_manifest', return_value=manifest), \
                 patch.object(build, 'run') as run_mock, \
                 patch.object(build, 'sign_staged_app', return_value='fake-id'), \
                 patch.object(build, 'font_pack', return_value=None), \
                 patch.object(build, 'xcrun_llvm',
                              return_value=('/fake/clang', '/fake/clang++')), \
                 patch.object(build, 'ios_build_versions', return_value={
                     'c_compiler': '/fake/clang', 'cxx_compiler': '/fake/clang++',
                     'c_compiler_version': 'Apple clang 21', 'xcode_version': 'Xcode 27.0',
                     'iphoneos_sdk_version': '27.0'}):
                def fake_run(*args, **kwargs):
                    if any('write_prewarm.py' in str(x) for x in args):
                        return real_run(*args, **kwargs)
                    if args[0] == 'cmake' and args[1] == '--build':
                        built = work / 'ios-app-build/ps2xRuntime/Release-iphoneos/ps2EntryRunner.app'
                        built.mkdir(parents=True, exist_ok=True)
                        (built / 'ps2EntryRunner').write_bytes(b'bin')
                        with open(built / 'Info.plist', 'wb') as f:
                            plistlib.dump({'CFBundleIdentifier': 'x'}, f)
                run_mock.side_effect = fake_run
                build.ios(a)
            configure = list(map(str, run_mock.call_args_list[0][0]))
            self.assertTrue(any('fprofile-use=' + str(prof.resolve()) in c
                                for c in configure))
            receipt = json.loads((work / 'ios-app.json').read_text())
            self.assertEqual(receipt['pgo_profile'], str(prof.resolve()))


if __name__ == '__main__':
    unittest.main()
