#!/usr/bin/env python3
"""No-disc regressions for the prebuilt trust boundary and source fallback."""
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile
import prebuilt

STAMP = 'a' * 64


class PrebuiltTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cache = self.root / 'cache'
        self.bundle = self.cache / STAMP
        self.bundle.mkdir(parents=True)
        self.contract_patch = patch.object(prebuilt, 'contract', return_value=STAMP)
        self.contract_patch.start()
        self.drift_patch = patch.object(prebuilt, 'contract_drift', return_value=[])
        self.drift_patch.start()
        self.pin_entry = {'tag': 'android-prebuilt-' + STAMP,
                          'asset': 'android-arm64-' + STAMP + '.zip',
                          'sha256': '0' * 64}
        self.pins_patch = patch.object(prebuilt, 'release_pins',
                                       return_value={STAMP: self.pin_entry})
        self.pins_patch.start()
        paths = ['lib/' + x for x in prebuilt.ARCHIVES]
        paths += ['jniLibs/arm64-v8a/' + x for x in prebuilt.SHARED]
        paths += ['include/sse2neon.h', 'runtime-definitions.txt', 'notices/PS2Recomp-LICENSE', 'notices/SOURCES.json']
        for name in paths:
            p = self.bundle / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'synthetic test fixture, no game input\n')
        self.manifest = {'recipe': prebuilt.RECIPE, 'contract': STAMP, 'source_sha': 'b' * 40,
                         'files': {name: prebuilt.digest(self.bundle / name) for name in paths}}
        self.write_manifest()

    def tearDown(self):
        self.contract_patch.stop()
        self.drift_patch.stop()
        self.pins_patch.stop()
        self.tmp.cleanup()

    def write_manifest(self):
        (self.bundle / 'manifest.json').write_text(json.dumps(self.manifest))

    def test_offline_hit_never_contacts_network(self):
        with patch.object(prebuilt.urllib.request, 'urlopen', side_effect=AssertionError('network used')):
            self.assertEqual(prebuilt.fetch(self.cache), self.bundle)

    def test_off_mode_never_reads_cache_or_network(self):
        with patch.object(prebuilt, 'contract', side_effect=AssertionError('contract used')):
            self.assertIsNone(prebuilt.fetch(self.cache, 'off'))

    def test_corrupt_cache_falls_back_without_using_artifact(self):
        (self.bundle / 'lib/libps2_runtime.a').write_bytes(b'corrupt')
        self.assertIsNone(prebuilt.fetch(self.cache))

    def test_wrong_toolchain_and_source_contract_are_rejected(self):
        self.manifest['contract'] = 'c' * 64
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'contract mismatch'):
            prebuilt.verify(self.bundle)
        self.manifest['contract'] = STAMP
        self.manifest['recipe'] = dict(prebuilt.RECIPE, ndk='wrong')
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'contract mismatch'):
            prebuilt.verify(self.bundle)

    def test_unlisted_payload_and_missing_archive_are_rejected(self):
        extra = self.bundle / 'unexpected.bin'
        extra.write_bytes(b'unlisted')
        with self.assertRaisesRegex(ValueError, 'exactly'):
            prebuilt.verify(self.bundle)
        extra.unlink()
        del self.manifest['files']['lib/libps2_runtime.a']
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            prebuilt.verify(self.bundle)

    def test_manifest_cannot_authorize_game_payload(self):
        p = self.bundle / 'game.iso'
        p.write_bytes(b'synthetic, not a disc')
        self.manifest['files']['game.iso'] = prebuilt.digest(p)
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'unapproved'):
            prebuilt.verify(self.bundle)

    def test_archive_path_escape_duplicate_and_symlink_rejected(self):
        cases = ['../escape', '/absolute', 'resources/arcade/card.gzip']
        for name in cases:
            archive = self.root / 'unsafe.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr(name, b'fake')
            with self.assertRaises(ValueError):
                prebuilt.extract(archive, self.root / 'out')
        archive = self.root / 'symlink.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            entry = zipfile.ZipInfo('include/sse2neon.h')
            entry.create_system = 3
            entry.external_attr = 0o120777 << 16
            z.writestr(entry, '/outside')
        with self.assertRaises(ValueError):
            prebuilt.extract(archive, self.root / 'out')
        self.assertFalse((self.root / 'out/include/sse2neon.h').exists())

    def test_release_miss_falls_back(self):
        with patch.object(prebuilt.urllib.request, 'urlopen', side_effect=urllib.error.HTTPError('url', 404, 'missing', {}, None)):
            self.assertIsNone(prebuilt.fetch(self.root / 'empty-cache'))

    def test_download_checksum_mismatch_falls_back(self):
        asset = 'android-arm64-' + STAMP + '.zip'
        responses = [io.BytesIO(('0' * 64 + '  ' + asset + '\n').encode()), io.BytesIO(b'corrupt zip')]
        with patch.object(prebuilt.urllib.request, 'urlopen', side_effect=responses):
            self.assertIsNone(prebuilt.fetch(self.root / 'download-cache'))
        self.assertFalse((self.root / 'download-cache' / STAMP).exists())

    def test_valid_download_is_verified_then_cached(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, 'w') as z:
            for p in sorted(self.bundle.rglob('*')):
                if p.is_file():
                    z.write(p, p.relative_to(self.bundle).as_posix())
        data = payload.getvalue()
        sha = prebuilt.hashlib.sha256(data).hexdigest()
        asset = 'android-arm64-' + STAMP + '.zip'
        responses = [io.BytesIO((sha + '  ' + asset + '\n').encode()), io.BytesIO(data)]
        cache = self.root / 'download-cache'
        pins = {STAMP: dict(self.pin_entry, sha256=sha)}
        with patch.object(prebuilt.urllib.request, 'urlopen', side_effect=responses), \
             patch.object(prebuilt, 'release_pins', return_value=pins):
            self.assertEqual(prebuilt.fetch(cache), cache / STAMP)
        prebuilt.verify(cache / STAMP)

    def _bundle_payload(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, 'w') as z:
            for p in sorted(self.bundle.rglob('*')):
                if p.is_file():
                    z.write(p, p.relative_to(self.bundle).as_posix())
        return payload.getvalue()

    def test_unpinned_release_refuses_download(self):
        data = self._bundle_payload()
        sha = prebuilt.hashlib.sha256(data).hexdigest()
        asset = 'android-arm64-' + STAMP + '.zip'
        responses = [io.BytesIO((sha + '  ' + asset + '\n').encode()), io.BytesIO(data)]
        cache = self.root / 'unpinned-cache'
        with patch.object(prebuilt.urllib.request, 'urlopen', side_effect=responses), \
             patch.object(prebuilt, 'release_pins', return_value={}):
            self.assertIsNone(prebuilt.fetch(cache))
        self.assertFalse((cache / STAMP).exists())

    def test_pin_mismatch_refuses_download(self):
        data = self._bundle_payload()
        sha = prebuilt.hashlib.sha256(data).hexdigest()
        asset = 'android-arm64-' + STAMP + '.zip'
        responses = [io.BytesIO((sha + '  ' + asset + '\n').encode()), io.BytesIO(data)]
        cache = self.root / 'mismatch-cache'
        pins = {STAMP: dict(self.pin_entry, sha256='f' * 64)}
        with patch.object(prebuilt.urllib.request, 'urlopen', side_effect=responses), \
             patch.object(prebuilt, 'release_pins', return_value=pins):
            self.assertIsNone(prebuilt.fetch(cache))
        self.assertFalse((cache / STAMP).exists())

    def test_junk_names_are_filtered(self):
        for name in ('.DS_Store', 'Thumbs.db', 'desktop.ini', '._AppleDouble', 'notes~'):
            self.assertTrue(prebuilt._is_junk(Path(name)), name)
        for name in ('hwcompat.c', 'CMakeLists.txt', 'libps2_runtime.a'):
            self.assertFalse(prebuilt._is_junk(Path(name)), name)

    def test_stray_files_do_not_move_contract(self):
        # M3: Finder droppings and editor backups under the contract paths
        # must not move the stamp or appear as drift.
        self.contract_patch.stop()
        self.drift_patch.stop()
        try:
            before = prebuilt.contract()
            strays = [prebuilt.ENGINE / 'ps2xRuntime/.DS_Store',
                      prebuilt.ENGINE / 'ps2xRuntime/._AppleDouble',
                      prebuilt.ENGINE / 'ps2xIOP/junk~']
            for p in strays:
                p.write_bytes(b'synthetic stray, no game input\n')
            try:
                self.assertEqual(prebuilt.contract(), before)
                drift = prebuilt.contract_drift()
                for p in strays:
                    self.assertNotIn(p.relative_to(prebuilt.ENGINE).as_posix(), drift)
            finally:
                for p in strays:
                    p.unlink(missing_ok=True)
        finally:
            self.contract_patch.start()
            self.drift_patch.start()

    def test_release_miss_names_contract_and_drift(self):
        import contextlib
        err = io.StringIO()
        with patch.object(prebuilt.urllib.request, 'urlopen',
                          side_effect=urllib.error.HTTPError('url', 404, 'missing', {}, None)), \
             patch.object(prebuilt, 'contract_drift',
                          return_value=['ps2xRuntime/edited.cpp']), \
             contextlib.redirect_stderr(err):
            self.assertIsNone(prebuilt.fetch(self.root / 'miss-cache'))
        output = err.getvalue()
        self.assertIn(STAMP, output)
        self.assertIn('ps2xRuntime/edited.cpp', output)

    def test_cache_drift_falls_back_without_network(self):
        with patch.object(prebuilt, 'contract_drift',
                          return_value=['ps2xRuntime/edited.cpp']), \
             patch.object(prebuilt.urllib.request, 'urlopen',
                          side_effect=AssertionError('network used')):
            self.assertIsNone(prebuilt.fetch(self.cache))



class ContractInputsTests(unittest.TestCase):
    """prebuilt-contract-1: the stamp covers exactly the archived-output inputs.

    TU table: exact link edges of the Android export configure at 902a7645
    (configure-only probe; see REPORT tables 1-2) plus the five
    android-libs adapter TUs mapped back to their in-repo origins.
    """

    # Every in-repo source linked into the shipped archives and shims.
    ARCHIVED_TUS = frozenset((
        'android/hwcompat/hwcompat.c',
        'ps2xIOP/src/emulator/core/iop_cpu.cpp',
        'ps2xIOP/src/emulator/core/iop_kernel.cpp',
        'ps2xIOP/src/emulator/core/iop_memory.cpp',
        'ps2xIOP/src/emulator/imports/iop_cdvd.cpp',
        'ps2xIOP/src/emulator/imports/iop_heaplib.cpp',
        'ps2xIOP/src/emulator/imports/iop_imports.cpp',
        'ps2xIOP/src/emulator/imports/iop_intrman.cpp',
        'ps2xIOP/src/emulator/imports/iop_ioman.cpp',
        'ps2xIOP/src/emulator/imports/iop_loadcore.cpp',
        'ps2xIOP/src/emulator/imports/iop_stdio.cpp',
        'ps2xIOP/src/emulator/imports/iop_sysclib.cpp',
        'ps2xIOP/src/emulator/imports/iop_sysmem.cpp',
        'ps2xIOP/src/emulator/imports/iop_timrman.cpp',
        'ps2xIOP/src/emulator/imports/iop_vblank.cpp',
        'ps2xIOP/src/emulator/iop_emulator.cpp',
        'ps2xIOP/src/emulator/services/iop_module_loader.cpp',
        'ps2xIOP/src/emulator/services/iop_rpc.cpp',
        'ps2xIOP/src/iop_module_manager.cpp',
        'ps2xIOP/src/iop_subsystem.cpp',
        'ps2xIOP/src/modules/dbcman.cpp',
        'ps2xIOP/src/modules/libsd.cpp',
        'ps2xIOP/src/modules/mcserv.cpp',
        'ps2xIOP/src/ps2_path.cpp',
        'ps2xRuntime/src/lib/Kernel/EeScheduler.cpp',
        'ps2xRuntime/src/lib/Kernel/EeSchedulerSavestate.cpp',
        'ps2xRuntime/src/lib/Kernel/Fh1Savestate.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/Audio.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/CD.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/Compatibility.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/DMA.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/Deci2.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/FileIO.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/Font.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/GS.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/IPU.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/LibC.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/MPEG.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/MemoryCard.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/Pad.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/RPC.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/SIF.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/System.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/TTY.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/Unimplemented.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/VU.cpp',
        'ps2xRuntime/src/lib/Kernel/Syscalls/Deci2.cpp',
        'ps2xRuntime/src/lib/Kernel/Syscalls/Dispatcher.cpp',
        'ps2xRuntime/src/lib/Kernel/Syscalls/FileIO.cpp',
        'ps2xRuntime/src/lib/Kernel/Syscalls/Interrupt.cpp',
        'ps2xRuntime/src/lib/Kernel/Syscalls/RPC.cpp',
        'ps2xRuntime/src/lib/Kernel/Syscalls/Savestate.cpp',
        'ps2xRuntime/src/lib/Kernel/Syscalls/Sync.cpp',
        'ps2xRuntime/src/lib/Kernel/Syscalls/System.cpp',
        'ps2xRuntime/src/lib/Kernel/Syscalls/Thread.cpp',
        'ps2xRuntime/src/lib/game_module_none.cpp',
        'ps2xRuntime/src/lib/game_overrides.cpp',
        'ps2xRuntime/src/lib/games_database.cpp',
        'ps2xRuntime/src/lib/gs/gs_cpu_backend.cpp',
        'ps2xRuntime/src/lib/gs/gs_frontend.cpp',
        'ps2xRuntime/src/lib/gs/gs_headless_backend.cpp',
        'ps2xRuntime/src/lib/gs/gs_replay_core.cpp',
        'ps2xRuntime/src/lib/gs/gs_stream_capture.cpp',
        'ps2xRuntime/src/lib/gs/gs_worker.cpp',
        'ps2xRuntime/src/lib/gs/ps2_gif_arbiter.cpp',
        'ps2xRuntime/src/lib/gs/ps2_gs_external_backend.cpp',
        'ps2xRuntime/src/lib/gs/ps2_gs_memory.cpp',
        'ps2xRuntime/src/lib/gs/ps2_present_owner.cpp',
        'ps2xRuntime/src/lib/gs/ps2_present_share.cpp',
        'ps2xRuntime/src/lib/gs/ps2_present_vk_android.cpp',
        'ps2xRuntime/src/lib/gs/ps2_present_vk_ledger.cpp',
        'ps2xRuntime/src/lib/ps2_adpf.cpp',
        'ps2xRuntime/src/lib/ps2_android_runtime.cpp',
        'ps2xRuntime/src/lib/ps2_android_toast.cpp',
        'ps2xRuntime/src/lib/ps2_audio.cpp',
        'ps2xRuntime/src/lib/ps2_audio_vag.cpp',
        'ps2xRuntime/src/lib/ps2_build_id.cpp',
        'ps2xRuntime/src/lib/ps2_codegen_abi_guard.cpp',
        'ps2xRuntime/src/lib/ps2_input_diag.cpp',
        'ps2xRuntime/src/lib/ps2_iop_host.cpp',
        'ps2xRuntime/src/lib/ps2_memory.cpp',
        'ps2xRuntime/src/lib/ps2_net_dirtysock.cpp',
        'ps2xRuntime/src/lib/ps2_net_link.cpp',
        'ps2xRuntime/src/lib/ps2_net_nolink.cpp',
        'ps2xRuntime/src/lib/ps2_pad.cpp',
        'ps2xRuntime/src/lib/ps2_perf_log.cpp',
        'ps2xRuntime/src/lib/ps2_prebuilt_abi_guard.cpp',
        'ps2xRuntime/src/lib/ps2_rom_device.cpp',
        'ps2xRuntime/src/lib/ps2_runtime.cpp',
        'ps2xRuntime/src/lib/ps2_savestate.cpp',
        'ps2xRuntime/src/lib/ps2_session_telemetry.cpp',
        'ps2xRuntime/src/lib/ps2_snd_audio_output.cpp',
        'ps2xRuntime/src/lib/ps2_telemetry.cpp',
        'ps2xRuntime/src/lib/ps2_vfs.cpp',
        'ps2xRuntime/src/lib/ps2_vif1_interpreter.cpp',
        'ps2xRuntime/src/lib/vu/ps2_microvu.cpp',
        'ps2xRuntime/src/lib/vu/ps2_native_world.cpp',
        'ps2xRuntime/src/lib/vu/ps2_vu0.cpp',
        'ps2xRuntime/src/lib/vu/ps2_vu1.cpp',
        'ps2xRuntime/src/lib/vu/ps2_vu_core.cpp',
        'ps2xRuntime/src/lib/vu/ps2_vu_lower.cpp',
        'ps2xRuntime/src/lib/vu/ps2_vu_recomp.cpp',
        'ps2xRuntime/src/lib/vu/ps2_vu_upper.cpp',
        'ps2xRuntime/src/main.cpp',
        'ps2xRuntime/third_party/soundtouch/source/SoundTouch/AAFilter.cpp',
        'ps2xRuntime/third_party/soundtouch/source/SoundTouch/BPMDetect.cpp',
        'ps2xRuntime/third_party/soundtouch/source/SoundTouch/FIFOSampleBuffer.cpp',
        'ps2xRuntime/third_party/soundtouch/source/SoundTouch/FIRFilter.cpp',
        'ps2xRuntime/third_party/soundtouch/source/SoundTouch/InterpolateCubic.cpp',
        'ps2xRuntime/third_party/soundtouch/source/SoundTouch/InterpolateLinear.cpp',
        'ps2xRuntime/third_party/soundtouch/source/SoundTouch/InterpolateShannon.cpp',
        'ps2xRuntime/third_party/soundtouch/source/SoundTouch/PeakFinder.cpp',
        'ps2xRuntime/third_party/soundtouch/source/SoundTouch/RateTransposer.cpp',
        'ps2xRuntime/third_party/soundtouch/source/SoundTouch/SoundTouch.cpp',
        'ps2xRuntime/third_party/soundtouch/source/SoundTouch/TDStretch.cpp',
        'ps2xRuntime/third_party/soundtouch/source/SoundTouch/cpu_detect_x86.cpp',
        'ps2xRuntime/third_party/soundtouch/source/SoundTouch/mmx_optimized.cpp',
        'ps2xRuntime/third_party/soundtouch/source/SoundTouch/sse_optimized.cpp',
    ))

    # android-libs adapter TUs: in-repo origins of the assembly copies.
    ADAPTER_ORIGINS = frozenset((
        'ps2xRuntime/third_party/armsx2/ge1/ge1_gs.cpp',
        'ps2xRuntime/third_party/armsx2/ge1/ge1_host.cpp',
        'ps2xRuntime/third_party/armsx2/platform/android/ax4b_android_stubs.cpp',
        'ps2xRuntime/third_party/armsx2/mv2/HeadlessAndroidStubs.cpp',
        'ps2xRuntime/third_party/armsx2/mv2/MicrovuBridge.cpp',
    ))

    # Recipe/toolchain files selecting archived sources and defines.
    RECIPE_INPUTS = frozenset((
        'ps2xRuntime/CMakeLists.txt',
        'ps2xRuntime/cmake/ReleaseMode.cmake',
        'ps2xRuntime/cmake/GameModule.cmake',
        'ps2xRuntime/cmake/patch_raylib_android_egl_alpha.cmake',
        'ps2xRuntime/third_party/soundtouch/CMakeLists.txt',
        'ps2xRuntime/third_party/armsx2/ge1/CMakeLists.txt',
        'ps2xRuntime/third_party/armsx2/mv2/CMakeLists.txt',
        'games/ssx3/module/CMakeLists.txt',
        'games/ssx3/module/include/ps2_ssx3_module.h',
        'ps2xIOP/CMakeLists.txt',
        'android/hwcompat/CMakeLists.txt',
        'tools/ssx3/android-libs/CMakeLists.txt',
        'LICENSE',
        'ps2xRuntime/third_party/soundtouch/COPYING.TXT',
    ))

    # f1180764..902a7645 in-repo changes the narrowed stamp must move on:
    # every one is compiled into an archive or included by a compiled TU.
    PIN_RANGE_SENSITIVE = frozenset((
        'ps2xRuntime/include/ps2_ios_runtime.h',
        'ps2xRuntime/include/ps2_knobs.h',
        'ps2xRuntime/include/ps2_settings_overlay.h',
        'ps2xRuntime/include/ps2_ssx3_tricky_list.h',
        'ps2xRuntime/include/ps2_ssx3_tricky_menu.h',
        'ps2xRuntime/include/runtime/gs/gs_frontend.h',
        'ps2xRuntime/include/runtime/gs/ps2_present_vk.h',
        'ps2xRuntime/include/runtime/ps2_canonical_pages.h',
        'ps2xRuntime/include/runtime/ps2_memory.h',
        'ps2xRuntime/include/runtime/ps2_savestate.h',
        'ps2xRuntime/src/lib/Kernel/EeScheduler.cpp',
        'ps2xRuntime/src/lib/Kernel/Stubs/Pad.cpp',
        'ps2xRuntime/src/lib/gs/gs_frontend.cpp',
        'ps2xRuntime/src/lib/gs/ps2_present_vk_android.cpp',
        'ps2xRuntime/src/lib/ps2_runtime.cpp',
        'ps2xRuntime/src/lib/ps2_savestate.cpp',
        'ps2xRuntime/src/lib/ps2_savestate_internal.h',
    ))

    def test_archived_tus_are_contract_inputs(self):
        covered = prebuilt.contract_paths()
        self.assertEqual([p for p in self.ARCHIVED_TUS if p not in covered], [])
        self.assertEqual([p for p in self.ADAPTER_ORIGINS if p not in covered], [])

    def test_ios_only_source_is_not_a_contract_input(self):
        self.assertNotIn('ps2xRuntime/src/lib/ps2_ios_runtime.mm',
                         prebuilt.contract_paths())

    def test_recipe_and_notice_inputs_are_covered(self):
        covered = prebuilt.contract_paths()
        self.assertEqual([p for p in self.RECIPE_INPUTS if p not in covered], [])

    def test_glob_dirs_cover_configured_kernel_and_runner(self):
        self.assertIn('ps2xRuntime/src/lib/Kernel', prebuilt.CONTRACT_GLOB_DIRS)
        self.assertIn('ps2xRuntime/src/runner', prebuilt.CONTRACT_GLOB_DIRS)

    def test_explicit_inputs_all_tracked(self):
        # split-p1-prep-1: module rows live in this repo (prebuilt._repo_for);
        # the count assertion is unchanged, only the checkout differs.
        by_repo = {}
        for p in prebuilt.CONTRACT_EXPLICIT:
            by_repo.setdefault(str(prebuilt._repo_for(p)), []).append(p)
        total = 0
        for repo, rels in by_repo.items():
            out = subprocess.check_output(
                ['git', '-C', repo, 'ls-files', '--', *rels],
                text=True).splitlines()
            total += len(out)
        self.assertEqual(total, len(prebuilt.CONTRACT_EXPLICIT))

    # Historical engine revisions the narrowed-stamp contract is measured
    # against (f1180764 = prebuilt-release-1, 902a7645 = ci-flake-1).
    PIN_RANGE_REVS = ('f1180764', '902a7645')

    def skip_if_engine_history_missing(self):
        """Skip when the engine checkout lacks the pin-range revisions.

        CI checks the engine submodule out shallow, so the historical
        revisions test_pin_range_sensitivity diffs against are absent. Skip
        with a reason instead of erroring; product-checks deepens the
        submodule so the check still runs there.
        """
        missing = [rev for rev in self.PIN_RANGE_REVS
                   if subprocess.run(
                       ['git', '-C', str(prebuilt.ENGINE), 'cat-file', '-e',
                        rev + '^{commit}'], check=False,
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL).returncode]
        if missing:
            self.skipTest('engine checkout lacks revision(s) ' + ', '.join(missing) +
                          '; the pin-range contract check needs engine history '
                          '(CI deepens the submodule, see product-checks)')

    def test_pin_range_sensitivity(self):
        self.skip_if_engine_history_missing()
        live = set(subprocess.check_output(
            ['git', '-C', str(prebuilt.ENGINE), 'diff', '--name-only',
             'f1180764', '902a7645', '--', *sorted(prebuilt.contract_paths())],
            text=True).split())
        self.assertEqual(live, set(self.PIN_RANGE_SENSITIVE))
        # The one in-range change the stamp ignores really did change.
        mm = subprocess.run(
            ['git', '-C', str(prebuilt.ENGINE), 'diff', '--quiet',
             'f1180764', '902a7645', '--', 'ps2xRuntime/src/lib/ps2_ios_runtime.mm'])
        self.assertNotEqual(mm.returncode, 0)

    def test_stamp_moves_on_listed_blob_change(self):
        before = [('a', '0' * 40), ('b', '1' * 40)]
        self.assertNotEqual(prebuilt._stamp(before), prebuilt._stamp([('a', '0' * 40)]))
        self.assertNotEqual(prebuilt._stamp(before), prebuilt._stamp([('a', '0' * 40), ('b', '2' * 40)]))
        self.assertEqual(prebuilt._stamp(before), prebuilt._stamp(list(before)))


class VendorAgreementTest(unittest.TestCase):
    """The support-bundle ARMSX2 source is the recipe's shipped pin.

    Exporter metadata (SOURCES.json armsx2 URL), the recipe table and the
    old-pin reuse check all derive from prebuilt.VENDOR; it must equal the
    tree android_libs() actually builds (build.py ANDROID_GE1_VENDOR_PIN)
    and the deps.lock.json pin.
    """

    def test_exporter_vendor_is_the_recipe_pin(self):
        import importlib.util
        here = Path(__file__).resolve().parent
        spec = importlib.util.spec_from_file_location('ssx3_build_pins', here / 'build.py')
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        self.assertEqual(prebuilt.VENDOR, build.ANDROID_GE1_VENDOR_PIN)
        self.assertEqual(prebuilt.RECIPE['vendor'], build.ANDROID_GE1_VENDOR_PIN)
        root = here.parents[1]
        lock = json.loads((root / 'deps.lock.json').read_text())
        self.assertEqual(lock['vendor']['armsx2']['pin'], build.ANDROID_GE1_VENDOR_PIN)


if __name__ == '__main__':
    unittest.main()
