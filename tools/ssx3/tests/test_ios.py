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
import shutil
import subprocess
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

    def test_profile_constrains_identity(self):
        # The profile's development cert is issued under another team
        # while the Distribution cert matches the team: the profile wins,
        # or the device refuses the app (0xe8008015).
        listing = ('  1) 295EFB42E6734599E5726A9A5EF47EBD1EFEF7B1 '
                   '"Apple Development: A User (ZZ99ZZ99ZZ)"\n'
                   '  2) 48230B072A367DF7635D2D5505467695754DEC3A '
                   '"Apple Distribution: A User (AB12CD34EF)"\n')
        with patch.object(build, 'probe', return_value=(True, listing)), \
             patch.object(build, 'profile_certificates',
                          return_value={'295EFB42E6734599E5726A9A5EF47EBD1EFEF7B1'}):
            self.assertEqual(
                build.signing_identity('AB12CD34EF', profile='p.mobileprovision'),
                '295EFB42E6734599E5726A9A5EF47EBD1EFEF7B1')

    def test_identity_outside_profile_fails(self):
        listing = '  1) DEADBEEF "Apple Development: A User (AB12CD34EF)"\n'
        with patch.object(build, 'probe', return_value=(True, listing)), \
             patch.object(build, 'profile_certificates', return_value={'UNKNOWN'}):
            with self.assertRaises(ValueError) as ctx:
                build.signing_identity('AB12CD34EF', profile='p.mobileprovision')
        self.assertIn('provisioning profile', str(ctx.exception))


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


class Om1GenerateTest(unittest.TestCase):
    def make_vendor(self, root):
        vendor = Path(root) / 'ios-vendor-om1replay'
        (vendor / 'pcsx2').mkdir(parents=True)
        (vendor / 'pcsx2' / 'Config.h').write_text('// vendor')
        stub = vendor / 'tests' / 'ctest' / 'core'
        stub.mkdir(parents=True)
        (stub / 'StubHost.cpp').write_text('// stub host')
        env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t',
                   GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
        subprocess.run(['git', 'init', '-q', str(vendor)], check=True)
        subprocess.run(['git', '-C', str(vendor), 'add', '.'], check=True, env=env)
        subprocess.run(['git', '-C', str(vendor), 'commit', '-qm', 'v'], check=True, env=env)
        head = subprocess.check_output(
            ['git', '-C', str(vendor), 'rev-parse', 'HEAD'], text=True).strip()
        return vendor, head

    def make_work(self, root, pin=None, elf=b'elf-bytes'):
        work = Path(root) / 'work'
        work.mkdir(parents=True, exist_ok=True)
        (work / 'SLUS_207.72').write_bytes(elf)
        core_dir = Path(root) / 'core'
        core_dir.mkdir(parents=True, exist_ok=True)
        for name in build.OM1_OFFLINE_CORE:
            (core_dir / name).write_text('// stub ' + name)
        vendor, head = self.make_vendor(work)
        stage = work / 'om1-stage'
        for name in build.OM1_STAGE_REQUIRED:
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('// stub')
        for area in build.OM1_AREAS:
            for suffix in ('.s', '_tables.c', '_tables.h'):
                (stage / (area + suffix)).write_text('// ' + area)
        link = stage / 'armsx2'
        if link.is_symlink() or link.exists():
            link.unlink() if link.is_symlink() else shutil.rmtree(link)
        link.symlink_to(vendor, target_is_directory=True)
        (work / 'om1-stage.json').write_text(json.dumps({
            'replay_pin': pin or head,
            'manifest_sha256': build.sha(build.OM1_MANIFEST),
            'elf_sha256': build.sha(work / 'SLUS_207.72'),
            'core_sha256': build.om1_core_hashes(core_dir)}))
        return work, core_dir, head

    def test_current_true(self):
        with tempfile.TemporaryDirectory() as temp:
            work, core_dir, head = self.make_work(temp)
            with patch.object(build, 'OM1_REPLAY_PIN', head):
                self.assertTrue(build.ios_om1_current(work, core_dir=core_dir))

    def test_current_false_on_pin_change(self):
        with tempfile.TemporaryDirectory() as temp:
            work, core_dir, head = self.make_work(temp, pin='0' * 40)
            self.assertFalse(build.ios_om1_current(work, core_dir=core_dir))

    def test_current_false_missing_area_file(self):
        with tempfile.TemporaryDirectory() as temp:
            work, core_dir, head = self.make_work(temp)
            (work / 'om1-stage' / 'menus.s').unlink()
            with patch.object(build, 'OM1_REPLAY_PIN', head):
                self.assertFalse(build.ios_om1_current(work, core_dir=core_dir))

    def test_current_false_on_link_outside_work(self):
        with tempfile.TemporaryDirectory() as temp:
            work, core_dir, head = self.make_work(temp)
            other = Path(temp) / 'other-vendor'
            other.mkdir()
            (work / 'om1-stage' / 'armsx2').unlink()
            (work / 'om1-stage' / 'armsx2').symlink_to(other, target_is_directory=True)
            with patch.object(build, 'OM1_REPLAY_PIN', head):
                self.assertFalse(build.ios_om1_current(work, core_dir=core_dir))

    def test_current_false_on_real_dir_link(self):
        with tempfile.TemporaryDirectory() as temp:
            work, core_dir, head = self.make_work(temp)
            (work / 'om1-stage' / 'armsx2').unlink()
            (work / 'om1-stage' / 'armsx2').mkdir()
            with patch.object(build, 'OM1_REPLAY_PIN', head):
                self.assertFalse(build.ios_om1_current(work, core_dir=core_dir))

    def test_current_false_on_vendor_head_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            work, core_dir, head = self.make_work(temp)
            vendor = work / 'ios-vendor-om1replay'
            (vendor / 'pcsx2' / 'Config.h').write_text('// moved on')
            env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t',
                       GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
            subprocess.run(['git', '-C', str(vendor), 'commit', '-qam', 'v2'],
                           check=True, env=env)
            with patch.object(build, 'OM1_REPLAY_PIN', head):
                self.assertFalse(build.ios_om1_current(work, core_dir=core_dir))

    def test_current_false_no_vendor(self):
        with tempfile.TemporaryDirectory() as temp:
            work, core_dir, head = self.make_work(temp)
            shutil.rmtree(work / 'ios-vendor-om1replay')
            with patch.object(build, 'OM1_REPLAY_PIN', head):
                self.assertFalse(build.ios_om1_current(work, core_dir=core_dir))

    def test_vendor_clean_check(self):
        with tempfile.TemporaryDirectory() as temp:
            vendor, _ = self.make_vendor(temp)
            self.assertIsNone(build.check_vendor_clean(vendor))
            (vendor / 'pcsx2' / 'Config.h').write_text('// dirty')
            with self.assertRaises(ValueError) as ctx:
                build.check_vendor_clean(vendor)
            self.assertIn('dirty', str(ctx.exception))

    def test_current_false_no_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp) / 'work'
            work.mkdir()
            self.assertFalse(build.ios_om1_current(work))

    def test_current_false_on_core_change(self):
        with tempfile.TemporaryDirectory() as temp:
            work, core_dir, head = self.make_work(temp)
            (core_dir / build.OM1_OFFLINE_CORE[0]).write_text('// changed')
            with patch.object(build, 'OM1_REPLAY_PIN', head):
                self.assertFalse(build.ios_om1_current(work, core_dir=core_dir))

    def test_current_false_on_core_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            work, core_dir, head = self.make_work(temp)
            (core_dir / build.OM1_OFFLINE_CORE[0]).unlink()
            with patch.object(build, 'OM1_REPLAY_PIN', head):
                self.assertFalse(build.ios_om1_current(work, core_dir=core_dir))

    def test_core_hashes_none_when_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(build.om1_core_hashes(Path(temp)))

    def test_ensure_uses_current_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            work, core_dir, head = self.make_work(temp)
            with patch.object(build, 'ios_om1', side_effect=AssertionError('must skip')), \
                 patch.object(build, 'OM1_OFFLINE_CORE_DIR', core_dir), \
                 patch.object(build, 'OM1_REPLAY_PIN', head):
                self.assertEqual(build.ensure_ios_om1_stage(args(work=work)), work / 'om1-stage')

    def test_ensure_generates_when_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp) / 'work'
            work.mkdir()
            with patch.object(build, 'ios_om1', return_value=work / 'om1-stage') as gen:
                self.assertEqual(build.ensure_ios_om1_stage(args(work=work)), work / 'om1-stage')
            gen.assert_called_once()

    def test_link_vendor_points_at_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            vendor = Path(temp) / 'vendor'
            for rel in ('pcsx2/Config.h', 'tests/ctest/core/StubHost.cpp',
                        'pcsx2/Achievements.h'):
                target = vendor / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('// ' + rel)
            stage = Path(temp) / 'stage'
            stage.mkdir()
            link = build.link_om1_vendor(stage, vendor)
            self.assertTrue(link.is_symlink())
            for rel in ('pcsx2/Config.h', 'tests/ctest/core/StubHost.cpp',
                        'pcsx2/Achievements.h'):
                self.assertTrue((stage / 'armsx2' / rel).is_file())

    def test_link_vendor_replaces_stale_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            vendor = Path(temp) / 'vendor'
            (vendor / 'pcsx2').mkdir(parents=True)
            (vendor / 'pcsx2' / 'Config.h').write_text('// new')
            stage = Path(temp) / 'stage'
            stale = stage / 'armsx2' / 'pcsx2'
            stale.mkdir(parents=True)
            (stale / 'Config.h').write_text('// stale')
            build.link_om1_vendor(stage, vendor)
            self.assertTrue((stage / 'armsx2').is_symlink())
            self.assertEqual((stage / 'armsx2' / 'pcsx2' / 'Config.h').read_text(), '// new')

    def test_ensure_mac_only(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(build.platform, 'system', return_value='Linux'):
                with self.assertRaises(ValueError) as ctx:
                    build.ensure_ios_om1_stage(args(work=Path(temp)))
            self.assertIn('--om1-stage explicitly', str(ctx.exception))


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


class DeviceTest(unittest.TestCase):
    ROW = '%-16s %-10s %-40s %-22s %s %s\n'
    HEADER = 'Name Hostname Identifier State Model Reality\n'

    def listing(self, rows):
        return self.HEADER + ''.join(
            self.ROW % (name, '', udid + ' (UDID)', state, model, reality)
            for name, udid, state, model, reality in rows)

    def test_connected_physical_wins_over_paired_and_sim(self):
        out = self.listing([
            ('Pad', '00008000-00000000000000A1', 'connected', 'iPad14,9', 'physical'),
            ('Phone', '00008000-00000000000000B2', 'available (paired)', 'iPhone17,2', 'physical'),
            ('Sim', 'AE710B2C-388C-42B3-8FC0-797DDDF07795', 'shutdown', 'iPhone18,3', 'simulated')])
        with patch.object(build, 'devicectl_devices', return_value=(True, out)):
            self.assertEqual(build.resolve_device(None, for_install=True),
                             '00008000-00000000000000A1')

    def test_explicit_device_hit(self):
        out = self.listing([
            ('Pad', '00008000-00000000000000A1', 'connected', 'iPad14,9', 'physical'),
            ('Phone', '00008000-00000000000000B2', 'available (paired)', 'iPhone17,2', 'physical')])
        with patch.object(build, 'devicectl_devices', return_value=(True, out)):
            self.assertEqual(build.resolve_device('00008000-00000000000000B2', for_install=True),
                             '00008000-00000000000000B2')

    def test_explicit_device_miss(self):
        out = self.listing([
            ('Pad', '00008000-00000000000000A1', 'connected', 'iPad14,9', 'physical')])
        with patch.object(build, 'devicectl_devices', return_value=(True, out)):
            with self.assertRaises(ValueError):
                build.resolve_device('00000000-0000000000000000', for_install=True)

    def test_two_connected_physical_needs_explicit(self):
        out = self.listing([
            ('Pad', '00008000-00000000000000A1', 'connected', 'iPad14,9', 'physical'),
            ('Phone', '00008000-00000000000000B2', 'connected', 'iPhone17,2', 'physical')])
        with patch.object(build, 'devicectl_devices', return_value=(True, out)):
            with self.assertRaises(ValueError):
                build.resolve_device(None, for_install=True)


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

    def test_team_wildcard_matches(self):
        with tempfile.TemporaryDirectory() as temp:
            self.write_profile(temp, 'wild.mobileprovision', 'AB12CD34EF.*', 300)
            with patch.object(build.subprocess, 'run',
                              side_effect=lambda cmd, **kw: self.fake_security(cmd[-1])):
                found = build.find_provisioning_profile(
                    'AB12CD34EF', 'com.ssx3recomp.ab12cd34ef', profiles_dir=temp)
            self.assertTrue(str(found).endswith('wild.mobileprovision'))

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
        # preset-parity-1: PS2X_ACHIEVEMENTS ships (inert with no
        # achievements-set file: "[ach] no set ... idle") and the Tricky
        # bundle rows ship (each fails closed without its files), so neither
        # is forbidden any more. SKIP_MOVIE/PAD_SCRIPT stay out (dev-only).
        for forbidden in ('/Users/', 'org.ps2x.ps2entryrunner', 'PS2X_SKIP_MOVIE=',
                          'PS2X_PAD_SCRIPT', 'APP_PACK', 'mobileprovision',
                          'PS2X_OM1_DENY='):
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
