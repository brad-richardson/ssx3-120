"""iOS builder flow: team/bundle validation, device resolution, OM1 stage
validation, dry-run listing, shipped preset hygiene, MoltenVK manifest.

No network, no disc, no Xcode, no device: devices and identities are
mocked; the preset and manifest are read from the checkout.
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

BUILD = Path(__file__).resolve().parents[1] / 'build.py'
SPEC = importlib.util.spec_from_file_location('ssx3_build', BUILD)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def args(**overrides):
    base = dict(team='LQ3V7772Q1', bundle_id=None, device=None, install=False,
                dry_run=False, om1_stage=None, jobs=8, stock_dump=None,
                skip_fonts=True, ssx3_iso=None, tricky_iso=None)
    base.update(overrides)
    return types.SimpleNamespace(**base)


class TeamTest(unittest.TestCase):
    def test_bundle_id_derives_from_team(self):
        self.assertEqual(build.default_bundle_id('AB12CD34EF'), 'com.ssx3recomp.ab12cd34ef')

    def test_team_required(self):
        with self.assertRaises(ValueError):
            build.check_team(args(team=None))

    def test_team_format(self):
        self.assertEqual(build.check_team(args(team='AB12CD34EF')), 'AB12CD34EF')
        with self.assertRaises(ValueError):
            build.check_team(args(team='short'))
        with self.assertRaises(ValueError):
            build.check_team(args(team='lowercase12'))

    def test_bundle_id_format(self):
        self.assertEqual(build.check_bundle_id('com.example.ssx3'), 'com.example.ssx3')
        with self.assertRaises(ValueError):
            build.check_bundle_id('not a bundle id')
        with self.assertRaises(ValueError):
            build.check_bundle_id('single')


class IdentityTest(unittest.TestCase):
    def test_parses_fingerprint_for_team(self):
        listing = ('  1) 295EFB42E6734599E5726A9A5EF47EBD1EFEF7B1 '
                   '"Apple Development: A User (AB12CD34EF)"\n')
        with patch.object(build, 'probe', return_value=(True, listing)):
            self.assertEqual(build.signing_identity('AB12CD34EF'),
                             '295EFB42E6734599E5726A9A5EF47EBD1EFEF7B1')

    def test_missing_team_fails_clearly(self):
        listing = '  1) DEADBEEF "Apple Development: A User (ZZ99ZZ99ZZ)"\n'
        with patch.object(build, 'probe', return_value=(True, listing)):
            with self.assertRaises(ValueError) as ctx:
                build.signing_identity('AB12CD34EF')
        self.assertIn('Xcode', str(ctx.exception))


class DeviceTest(unittest.TestCase):
    LISTING = ('Devices:\n'
               '  My iPad (00008112-001224302184A01E)\n'
               '  Other Phone (00008110-000A1B2C3D4E5F60)\n')

    def test_explicit_device_hit(self):
        with patch.object(build, 'devicectl_devices', return_value=(True, self.LISTING)):
            self.assertEqual(build.resolve_device('00008112-001224302184A01E'), '00008112-001224302184A01E')

    def test_explicit_device_miss(self):
        with patch.object(build, 'devicectl_devices', return_value=(True, self.LISTING)):
            with self.assertRaises(ValueError):
                build.resolve_device('00000000-0000000000000000')

    def test_single_device_auto_pick(self):
        one = 'Devices:\n  Solo iPad (00008112-001224302184A01E)\n'
        with patch.object(build, 'devicectl_devices', return_value=(True, one)):
            self.assertEqual(build.resolve_device(None, for_install=True), '00008112-001224302184A01E')

    def test_several_devices_need_flag(self):
        with patch.object(build, 'devicectl_devices', return_value=(True, self.LISTING)):
            with self.assertRaises(ValueError) as ctx:
                build.resolve_device(None, for_install=True)
        self.assertIn('--device', str(ctx.exception))


class Om1StageTest(unittest.TestCase):
    def make_stage(self, root, with_tables=True):
        stage = Path(root) / 'stage'
        for name in build.OM1_STAGE_REQUIRED:
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('// stub')
        if with_tables:
            (stage / 'sp1.s').write_text('// asm')
            (stage / 'sp1_tables.c').write_text('// tables')
        return stage

    def test_valid_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(build.validate_om1_stage(self.make_stage(temp)), self.make_stage(temp))

    def test_missing_dir(self):
        with self.assertRaises(ValueError) as ctx:
            build.validate_om1_stage('/no/such/stage')
        self.assertIn('offline-core', str(ctx.exception))

    def test_missing_tables(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError) as ctx:
                build.validate_om1_stage(self.make_stage(temp, with_tables=False))
        self.assertIn('recording recipe', str(ctx.exception))


class DryRunTest(unittest.TestCase):
    def test_listing_touches_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp) / 'work'
            work.mkdir()
            out = io.StringIO()
            with redirect_stdout(out):
                build.ios(args(work=work, dry_run=True))
            text = out.getvalue()
            self.assertIn('LQ3V7772Q1', text)
            self.assertIn('com.ssx3recomp.lq3v7772q1', text)
            self.assertIn('MISSING', text)
            self.assertFalse((work / 'ios-app-build').exists())
            self.assertFalse((work / 'ios-stage').exists())


class ExtractOnceTest(unittest.TestCase):
    def make_tar(self, root, name='top'):
        src = Path(root) / 'src' / name
        (src / 'sub').mkdir(parents=True)
        (src / 'marker.txt').write_text('x')
        archive = Path(root) / 'a.tar.gz'
        import tarfile
        with tarfile.open(archive, 'w:gz') as t:
            t.add(src, arcname=name)
        return archive

    def test_fresh_extract(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = build.extract_once(self.make_tar(temp), 'gz', Path(temp) / 'out', 'top/marker.txt')
            self.assertTrue((dest / 'top/marker.txt').is_file())

    def test_empty_dir_from_crashed_run_extracts(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'out'
            dest.mkdir()
            dest = build.extract_once(self.make_tar(temp), 'gz', dest, 'top/marker.txt')
            self.assertTrue((dest / 'top/marker.txt').is_file())

    def test_complete_tree_skips(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = self.make_tar(temp)
            dest = Path(temp) / 'out'
            build.extract_once(archive, 'gz', dest, 'top/marker.txt')
            sentinel = dest / 'top/sentinel.txt'
            sentinel.write_text('keep')
            build.extract_once(archive, 'gz', dest, 'top/marker.txt')
            self.assertTrue(sentinel.is_file())


class UnguardTest(unittest.TestCase):
    def test_applies_once_then_skips(self):
        with tempfile.TemporaryDirectory() as temp:
            cmake = Path(temp) / 'CMakeLists.txt'
            cmake.write_text('before\n' + build.JPEG_GUARD + '\n'
                             + build.JPEG_UNINSTALL + '\nafter\n')
            self.assertTrue(build.unguard_libjpeg_turbo(temp))
            text = cmake.read_text()
            self.assertIn(build.JPEG_GUARD_MARKER, text)
            self.assertIn(build.JPEG_UNINSTALL_SUBDIR, text)
            self.assertNotIn('FATAL_ERROR', text)
            self.assertFalse(build.unguard_libjpeg_turbo(temp))

    def test_drift_fails_loud(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'CMakeLists.txt').write_text('unrelated content\n')
            with self.assertRaises(ValueError):
                build.unguard_libjpeg_turbo(temp)


class BundleAssembleTest(unittest.TestCase):
    def make_vendor(self, root, with_tree=True):
        vendor = Path(root) / 'vendor'
        (vendor / '3rdparty').mkdir(parents=True)
        if with_tree:
            for name in build.IOS_DEPS_TREE:
                (vendor / '3rdparty' / name).mkdir()
        return vendor

    def make_deps_src(self, root):
        src = Path(root) / 'deps-src'
        for name, _, _, _ in build.IOS_DEPS:
            (src / name).mkdir(parents=True)
        return src

    def test_assembles_both_link_kinds(self):
        with tempfile.TemporaryDirectory() as temp:
            vendor = self.make_vendor(temp)
            bundle = build.assemble_bundle(Path(temp) / 'bundle', vendor,
                                           self.make_deps_src(temp))
            for name, _, _, _ in build.IOS_DEPS:
                self.assertTrue((bundle / name).is_symlink())
            for name in build.IOS_DEPS_TREE:
                self.assertEqual(os.readlink(bundle / name), str(vendor / '3rdparty' / name))

    def test_missing_tree_dep_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            vendor = self.make_vendor(temp, with_tree=False)
            with self.assertRaises(ValueError):
                build.assemble_bundle(Path(temp) / 'bundle', vendor,
                                      self.make_deps_src(temp))

    def test_idempotent_reassemble(self):
        with tempfile.TemporaryDirectory() as temp:
            vendor = self.make_vendor(temp)
            src = self.make_deps_src(temp)
            build.assemble_bundle(Path(temp) / 'bundle', vendor, src)
            build.assemble_bundle(Path(temp) / 'bundle', vendor, src)


class ArchiveEnvTest(unittest.TestCase):
    def test_pins_no_common(self):
        env = build.archive_build_env('/tmp/bundle')
        self.assertIn('-fno-common', env['CFLAGS'].split())
        self.assertIn('-fno-common', env['CXXFLAGS'].split())
        self.assertEqual(env['SSX3_IOS_DEPS'], '/tmp/bundle')

    def test_preserves_user_flags(self):
        with patch.dict('os.environ', {'CFLAGS': '-O2'}):
            env = build.archive_build_env('/tmp/bundle')
        self.assertIn('-O2', env['CFLAGS'].split())
        self.assertIn('-fno-common', env['CFLAGS'].split())

    def test_extra_cflags_opt_in(self):
        env = build.archive_build_env('/tmp/bundle', extra_cflags='-DPCSX2_RECOMPILER_TESTS')
        self.assertIn('-DPCSX2_RECOMPILER_TESTS', env['CFLAGS'].split())
        plain = build.archive_build_env('/tmp/bundle')
        self.assertNotIn('-DPCSX2_RECOMPILER_TESTS', plain['CFLAGS'].split())

    def test_vendor_pins_are_full_shas(self):
        for pin in (build.GE1_VENDOR_PIN, build.OM1_VENDOR_PIN):
            self.assertRegex(pin, r'^[0-9a-f]{40}$')
        self.assertNotEqual(build.GE1_VENDOR_PIN, build.OM1_VENDOR_PIN)


class PruneOm1Test(unittest.TestCase):
    def make_build(self, root):
        bld = Path(root) / 'bld'
        (bld / 'common').mkdir(parents=True)
        (bld / 'common' / 'libcommon.a').write_text('common')
        (bld / 'pcsx2').mkdir()
        (bld / 'pcsx2' / 'libPCSX2.a').write_text('pcsx2')
        (bld / 'ge1-adapter').mkdir()
        (bld / 'ge1-adapter' / 'libge1_gs.a').write_text('ge1')
        deep = bld / '3rdparty' / 'libwebp'
        deep.mkdir(parents=True)
        (deep / 'libwebp.a').write_text('webp')
        sdl = bld / '3rdparty' / 'sdl3'
        sdl.mkdir(parents=True)
        (sdl / 'libSDL3.a').write_text('sdl')
        return bld

    def test_prunes_adapter_and_sdl(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = build.prune_om1_copy(self.make_build(temp), Path(temp) / 'om1')
            self.assertTrue((dest / 'pcsx2/libPCSX2.a').is_file())
            self.assertTrue((dest / 'common/libcommon.a').is_file())
            self.assertTrue((dest / '3rdparty/libwebp/libwebp.a').is_file())
            self.assertFalse((dest / 'ge1-adapter').exists())
            self.assertFalse((dest / '3rdparty/sdl3').exists())

    def test_missing_core_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            bld = Path(temp) / 'bld'
            bld.mkdir()
            with self.assertRaises(ValueError):
                build.prune_om1_copy(bld, Path(temp) / 'om1')


class NewestBuildDirTest(unittest.TestCase):
    def test_picks_newest(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            (work / 'ios-build-aaa').mkdir()
            (work / 'ios-build-zzz').mkdir()
            (work / 'ios-asm-zzz').mkdir()
            self.assertEqual(build.newest_build_dir(work), work / 'ios-build-zzz')

    def test_empty_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                build.newest_build_dir(Path(temp))


class DoctorIosTest(unittest.TestCase):
    def test_reports_failures_through_check(self):
        seen = []
        with patch.object(build, 'probe', return_value=(False, 'missing')):
            with patch.object(build, 'signing_identity', side_effect=ValueError('nope')):
                build.doctor_ios(args(device=None), lambda *row: seen.append(row))
        labels = [row[0] for row in seen]
        self.assertIn('Xcode clang', labels)
        self.assertIn('cmake', labels)
        self.assertIn('signing identity', labels)
        self.assertTrue(all(row[1] is False for row in seen))


class ProfileTest(unittest.TestCase):
    def write_profile(self, home, name, app_id, days_valid):
        prof_dir = Path(home) / 'Library/MobileDevice/Provisioning Profiles'
        prof_dir.mkdir(parents=True, exist_ok=True)
        data = {'Entitlements': {'application-identifier': app_id},
                'Platform': ['iOS'],
                'ExpirationDate': datetime.now(timezone.utc) + timedelta(days=days_valid)}
        (prof_dir / name).write_bytes(plistlib.dumps(data))

    def fake_security(self, path):
        payload = Path(path).read_bytes()
        return types.SimpleNamespace(returncode=0, stdout=payload)

    def test_newest_valid_profile_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            self.write_profile(temp, 'old.mobileprovision', 'AB12CD34EF.com.ssx3recomp.ab12cd34ef', 3)
            self.write_profile(temp, 'new.mobileprovision', 'AB12CD34EF.com.ssx3recomp.ab12cd34ef', 30)
            self.write_profile(temp, 'other.mobileprovision', 'ZZ99ZZ99ZZ.com.other.app', 30)
            with patch.object(build.subprocess, 'run',
                              side_effect=lambda cmd, **kw: self.fake_security(cmd[-1])):
                found = build.find_provisioning_profile(
                    'AB12CD34EF', 'com.ssx3recomp.ab12cd34ef', profiles_dir=temp)
            self.assertTrue(str(found).endswith('new.mobileprovision'))

    def test_no_match_fails_clearly(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError) as ctx:
                build.find_provisioning_profile('AB12CD34EF', 'com.ssx3recomp.ab12cd34ef',
                                                profiles_dir=temp)
        self.assertIn('provisioning profile', str(ctx.exception))


class PresetHygieneTest(unittest.TestCase):
    def test_no_private_bits_in_shipped_preset(self):
        text = (build.ROOT / 'presets/ios.env').read_text()
        content = [line for line in text.splitlines()
                   if line and not line.startswith('#')]
        keys = [line.split('=', 1)[0] for line in content]
        joined = '\n'.join(content)
        for required in ('PS2X_OM1_LEAN', 'PS2X_OM1_SCAN', 'PS2X_PERF_LOG',
                         'PS2X_SSX3_NATIVE_WORLD', 'PS2X_VIF_UNPACK_FAST',
                         'PS2X_GS_GIF_BATCH', 'PS2X_GS_GIF_ARENA',
                         'GE1_ONDEVICE_UPSCALE', 'PS2X_NET', 'PS2X_NET_SERVER'):
            self.assertIn(required, keys)
        for forbidden in ('/Users/', 'org.ps2x.ps2entryrunner', 'PS2X_SKIP_MOVIE=',
                          'PS2X_PAD_SCRIPT', 'PS2X_ACHIEVEMENTS', 'tricky/',
                          'APP_PACK', 'mobileprovision'):
            self.assertNotIn(forbidden, joined)

    def test_play_values_are_explicit(self):
        values = {}
        for line in (build.ROOT / 'presets/ios.env').read_text().splitlines():
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key] = value
        self.assertEqual(values['GE1_UPSCALE'], '2')
        self.assertEqual(values['GE1_CAS'], '0.25')
        self.assertEqual(values['PS2X_GE1_EXPORT_SIZE'], 'display')
        self.assertEqual(values['PS2X_NET_SERVER'], 'ssx.bradr.dev')


class MoltenvkManifestTest(unittest.TestCase):
    def test_manifest_shape(self):
        mvk = json.loads((build.ROOT / 'tools/ssx3/moltenvk.json').read_text())
        for key in ('url', 'archive_sha256', 'archive_size', 'device_framework'):
            self.assertIn(key, mvk)
        self.assertRegex(mvk['archive_sha256'], r'^[0-9a-f]{64}$')
        self.assertTrue(mvk['url'].startswith('https://github.com/KhronosGroup/MoltenVK/'))


if __name__ == '__main__':
    unittest.main()
