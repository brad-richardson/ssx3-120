#!/usr/bin/env python3
"""Build and verify game-free Android support bundles. No disc input is accepted."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
# Archive identity is engine-pinned (repo-split-2 step 2): the contract hashes
# the engine checkout's bytes under engine-relative paths, so the stamp is
# stable across the product move for the same engine pin and the product can
# consume fork-published archives. Product composition (product SHA + engine
# SHA) is recorded separately in deps.lock.json, not in the archive stamp.
ENGINE = Path(os.environ.get('SSX3_ENGINE_DIR', str(ROOT / 'deps/PS2Recomp')))


def _repo_for(rel):
    """Git dir holding a contract relpath (split-p1-prep-1).

    The SSX 3 game module is product-owned: its rows keep the same
    engine-relative spellings (and the stamp keeps the same keys), only
    the checkout they are read from moved.
    """
    return ROOT if rel.startswith('games/ssx3/module/') else ENGINE
NDK = '28.2.13676358'
# ARMSX2 source for the support bundle: the same tree android_libs()
# builds (build.py ANDROID_GE1_VENDOR_PIN) and deps.lock.json pins.
# Keep them equal (test_prebuilt.py VendorAgreementTest enforces it).
VENDOR = 'e8ed655ec66eeba12ae3029cc26b45927964b476'
RELEASES = 'https://github.com/brad-richardson/PS2Recomp/releases/download/'
FORMAT = 1
ARCHIVES = ('libps2EntryRunner.a', 'libps2_runtime.a', 'libps2_gs_shadow.a',
            'libps2_iop.a', 'libps2x_soundtouch.a', 'libraylib.a')
SHARED = ('libhardware.so', 'libge1_gs.so', 'libmv2_microvu.so')
RECIPE = {'format': FORMAT, 'ndk': NDK, 'api': 29, 'abi': 'arm64-v8a',
          'march': 'armv8.2-a+fp16+dotprod', 'config': 'RelWithDebInfo',
          'vendor': VENDOR, 'app_id': 'com.ssx3.recomp', 'codegen_abi': 2}


RELEASES_FILE = 'tools/ssx3/prebuilt-releases.json'


def release_pins():
    """Committed archive hashes by contract stamp ({} before the first release)."""
    try:
        pins = json.loads((ENGINE / RELEASES_FILE).read_text())
    except (OSError, ValueError):
        return {}
    return pins if isinstance(pins, dict) else {}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


CONTRACT_FILES = ['CMakeLists.txt', 'tools/ssx3/prebuilt.py',
                  'tools/ssx3/prebuilt-consumer.cmake', 'tools/ssx3/build.py',
                  'android/app/build.gradle']
# In-repo inputs to the archived prebuilt bytes (contract tables 1-2
# in the prebuilt design notes). Every in-repo
# TU linked into the seven static archives, libhardware.so, libge1_gs.so
# and libmv2_microvu.so (exact link edges of a configured Android export
# plus the android-libs build), the transitive in-repo #include closure of
# those TUs, the CMake recipe files selecting their sources/defines, and
# the license files archived under notices/. CONTRACT_GLOB_DIRS hash
# wholesale: CONFIGURE_DEPENDS globs pull new files in those trees into
# the build without a recipe change. Refresh this list with the REPORT
# method at each release re-cut (test_prebuilt.py pins it).
CONTRACT_EXPLICIT = (
    'LICENSE',
    'android/hwcompat/CMakeLists.txt',
    'android/hwcompat/hwcompat.c',
    'games/ssx3/module/CMakeLists.txt',
    'games/ssx3/module/include/Ssx3Movie.h',
    'games/ssx3/module/include/Ssx3VisNative.h',
    'games/ssx3/module/include/ps2_snd_spike.h',
    'games/ssx3/module/include/ps2_snd_spu.h',
    'games/ssx3/module/include/ps2_snd_spu_gauss.h',
    'games/ssx3/module/include/ps2_ssx3_module.h',
    'games/ssx3/module/src/movie.cpp',
    'games/ssx3/module/src/vis_native.cpp',
    'ps2xIOP/CMakeLists.txt',
    'ps2xIOP/include/ps2x/iop/iop_host.h',
    'ps2xIOP/include/ps2x/iop/iop_subsystem.h',
    'ps2xIOP/include/ps2x/iop/iop_types.h',
    'ps2xIOP/include/ps2x/iop/ps2_path.h',
    'ps2xIOP/include/ps2x/iop/slim_cd_image.h',
    'ps2xIOP/src/emulator/core/iop_cpu.cpp',
    'ps2xIOP/src/emulator/core/iop_cpu.h',
    'ps2xIOP/src/emulator/core/iop_kernel.cpp',
    'ps2xIOP/src/emulator/core/iop_kernel.h',
    'ps2xIOP/src/emulator/core/iop_memory.cpp',
    'ps2xIOP/src/emulator/core/iop_memory.h',
    'ps2xIOP/src/emulator/imports/iop_cdvd.cpp',
    'ps2xIOP/src/emulator/imports/iop_cdvd.h',
    'ps2xIOP/src/emulator/imports/iop_heaplib.cpp',
    'ps2xIOP/src/emulator/imports/iop_heaplib.h',
    'ps2xIOP/src/emulator/imports/iop_imports.cpp',
    'ps2xIOP/src/emulator/imports/iop_imports.h',
    'ps2xIOP/src/emulator/imports/iop_intrman.cpp',
    'ps2xIOP/src/emulator/imports/iop_intrman.h',
    'ps2xIOP/src/emulator/imports/iop_ioman.cpp',
    'ps2xIOP/src/emulator/imports/iop_ioman.h',
    'ps2xIOP/src/emulator/imports/iop_loadcore.cpp',
    'ps2xIOP/src/emulator/imports/iop_loadcore.h',
    'ps2xIOP/src/emulator/imports/iop_stdio.cpp',
    'ps2xIOP/src/emulator/imports/iop_stdio.h',
    'ps2xIOP/src/emulator/imports/iop_sysclib.cpp',
    'ps2xIOP/src/emulator/imports/iop_sysclib.h',
    'ps2xIOP/src/emulator/imports/iop_sysmem.cpp',
    'ps2xIOP/src/emulator/imports/iop_sysmem.h',
    'ps2xIOP/src/emulator/imports/iop_timrman.cpp',
    'ps2xIOP/src/emulator/imports/iop_timrman.h',
    'ps2xIOP/src/emulator/imports/iop_vblank.cpp',
    'ps2xIOP/src/emulator/imports/iop_vblank.h',
    'ps2xIOP/src/emulator/iop_emulator.cpp',
    'ps2xIOP/src/emulator/iop_emulator.h',
    'ps2xIOP/src/emulator/iop_emulator_const.h',
    'ps2xIOP/src/emulator/services/iop_module_loader.cpp',
    'ps2xIOP/src/emulator/services/iop_module_loader.h',
    'ps2xIOP/src/emulator/services/iop_rpc.cpp',
    'ps2xIOP/src/emulator/services/iop_rpc.h',
    'ps2xIOP/src/iop_module_manager.cpp',
    'ps2xIOP/src/iop_module_manager.h',
    'ps2xIOP/src/iop_service.h',
    'ps2xIOP/src/iop_subsystem.cpp',
    'ps2xIOP/src/module_factories.h',
    'ps2xIOP/src/modules/dbcman.cpp',
    'ps2xIOP/src/modules/libsd.cpp',
    'ps2xIOP/src/modules/mcserv.cpp',
    'ps2xIOP/src/ps2_path.cpp',
    'ps2xIOP/src/rpc_reply.h',
    'ps2xRuntime/CMakeLists.txt',
    'ps2xRuntime/cmake/GameModule.cmake',
    'ps2xRuntime/cmake/ReleaseMode.cmake',
    'ps2xRuntime/cmake/patch_raylib_android_egl_alpha.cmake',
    'ps2xRuntime/include/ThreadNaming.h',
    'ps2xRuntime/include/game_overrides.h',
    'ps2xRuntime/include/games_database.h',
    'ps2xRuntime/include/ps2_adpf.h',
    'ps2xRuntime/include/ps2_android_env.h',
    'ps2xRuntime/include/ps2_android_pause.h',
    'ps2xRuntime/include/ps2_android_toast.h',
    'ps2xRuntime/include/ps2_audio_stretch.h',
    'ps2xRuntime/include/ps2_build_id.h',
    'ps2xRuntime/include/ps2_call_list.h',
    'ps2xRuntime/include/ps2_cd_overlay.h',
    'ps2xRuntime/include/ps2_codegen_abi.h',
    'ps2xRuntime/include/ps2_debug_panel.h',
    'ps2xRuntime/include/ps2_e15.h',
    'ps2xRuntime/include/ps2_e41_trace.h',
    'ps2xRuntime/include/ps2_e7.h',
    'ps2xRuntime/include/ps2_fh1_fh35.h',
    'ps2xRuntime/include/ps2_fh1_fix.h',
    'ps2xRuntime/include/ps2_fh1_full120.h',
    'ps2xRuntime/include/ps2_fh1_ground2.h',
    'ps2xRuntime/include/ps2_fh1_input2.h',
    'ps2xRuntime/include/ps2_fh1_jcam2_scratch.h',
    'ps2xRuntime/include/ps2_fh1_life2.inl',
    'ps2xRuntime/include/ps2_fh1_normcap.h',
    'ps2xRuntime/include/ps2_fh1_pub.h',
    'ps2xRuntime/include/ps2_fh1_replay_rate.h',
    'ps2xRuntime/include/ps2_fh1_restamp.h',
    'ps2xRuntime/include/ps2_fpmode.h',
    'ps2xRuntime/include/ps2_hle_pools.h',
    'ps2xRuntime/include/ps2_host_backend.h',
    'ps2xRuntime/include/ps2_input_diag.h',
    'ps2xRuntime/include/ps2_ios_runtime.h',
    'ps2xRuntime/include/ps2_knobs.h',
    'ps2xRuntime/include/ps2_log.h',
    'ps2xRuntime/include/ps2_microvu.h',
    'ps2xRuntime/include/ps2_microvu_api.h',
    'ps2xRuntime/include/ps2_mtvu.h',
    'ps2xRuntime/include/ps2_native_world.h',
    'ps2xRuntime/include/ps2_net_dirtysock.h',
    'ps2xRuntime/include/ps2_net_link.h',
    'ps2xRuntime/include/ps2_net_nolink.h',
    'ps2xRuntime/include/ps2_net_protocol.h',
    'ps2xRuntime/include/ps2_np_rider.h',
    'ps2xRuntime/include/ps2_pad_latch.h',
    'ps2xRuntime/include/ps2_perf_log.h',
    'ps2xRuntime/include/ps2_present_fallback.h',
    'ps2xRuntime/include/ps2_present_geometry.h',
    'ps2xRuntime/include/ps2_public_tier.h',
    'ps2xRuntime/include/ps2_record_env.h',
    'ps2xRuntime/include/ps2_rpl3_nv.h',
    'ps2xRuntime/include/ps2_runtime.h',
    'ps2xRuntime/include/ps2_runtime_calls.h',
    'ps2xRuntime/include/ps2_runtime_macros.h',
    'ps2xRuntime/include/ps2_session_telemetry.h',
    'ps2xRuntime/include/ps2_settings_overlay.h',
    'ps2xRuntime/include/ps2_snd_audio_output.h',
    'ps2xRuntime/include/ps2_ssx3_course_manifest.h',
    'ps2xRuntime/include/ps2_ssx3_lod.h',
    'ps2xRuntime/include/ps2_ssx3_patch_grow_state.h',
    'ps2xRuntime/include/ps2_ssx3_tricky_gems.h',
    'ps2xRuntime/include/ps2_ssx3_tricky_hud.h',
    'ps2xRuntime/include/ps2_ssx3_tricky_layer.h',
    'ps2xRuntime/include/ps2_ssx3_tricky_list.h',
    'ps2xRuntime/include/ps2_ssx3_tricky_menu.h',
    'ps2xRuntime/include/ps2_ssx3_tricky_preview.h',
    'ps2xRuntime/include/ps2_ssx3_tricky_resetvol.h',
    'ps2xRuntime/include/ps2_ssx3_tricky_song.h',
    'ps2xRuntime/include/ps2_stubs.h',
    'ps2xRuntime/include/ps2_syscalls.h',
    'ps2xRuntime/include/ps2_telemetry.h',
    'ps2xRuntime/include/ps2_thread_affinity.h',
    'ps2xRuntime/include/ps2_touch_events.h',
    'ps2xRuntime/include/ps2_turbo_boot.h',
    'ps2xRuntime/include/ps2_ui_toast.h',
    'ps2xRuntime/include/ps2_vif_unpack_fast.h',
    'ps2xRuntime/include/ps2_virtual_pad.h',
    'ps2xRuntime/include/ps2_vsync_lock.h',
    'ps2xRuntime/include/ps2_vsync_pacer.h',
    'ps2xRuntime/include/runtime/ee_dispatch_fast.h',
    'ps2xRuntime/include/runtime/ee_guest_unwind.h',
    'ps2xRuntime/include/runtime/ee_scheduler.h',
    'ps2xRuntime/include/runtime/game_module.h',
    'ps2xRuntime/include/runtime/gs/ge1_gs_api.h',
    'ps2xRuntime/include/runtime/gs/gs_backend.h',
    'ps2xRuntime/include/runtime/gs/gs_cpu_backend.h',
    'ps2xRuntime/include/runtime/gs/gs_frontend.h',
    'ps2xRuntime/include/runtime/gs/gs_gif_arena.h',
    'ps2xRuntime/include/runtime/gs/gs_headless_backend.h',
    'ps2xRuntime/include/runtime/gs/gs_replay_core.h',
    'ps2xRuntime/include/runtime/gs/gs_serial_job_thread.h',
    'ps2xRuntime/include/runtime/gs/gs_stream_capture.h',
    'ps2xRuntime/include/runtime/gs/gs_texture_page_cache.h',
    'ps2xRuntime/include/runtime/gs/gs_types.h',
    'ps2xRuntime/include/runtime/gs/gs_worker.h',
    'ps2xRuntime/include/runtime/gs/ps2_gif_arbiter.h',
    'ps2xRuntime/include/runtime/gs/ps2_gs_common.h',
    'ps2xRuntime/include/runtime/gs/ps2_gs_external_backend.h',
    'ps2xRuntime/include/runtime/gs/ps2_gs_memory.h',
    'ps2xRuntime/include/runtime/gs/ps2_gs_psmct16.h',
    'ps2xRuntime/include/runtime/gs/ps2_gs_psmct32.h',
    'ps2xRuntime/include/runtime/gs/ps2_gs_psmt4.h',
    'ps2xRuntime/include/runtime/gs/ps2_gs_psmt8.h',
    'ps2xRuntime/include/runtime/gs/ps2_present_owner.h',
    'ps2xRuntime/include/runtime/gs/ps2_present_share.h',
    'ps2xRuntime/include/runtime/gs/ps2_present_vk.h',
    'ps2xRuntime/include/runtime/gs/ps2_present_vk_ledger.h',
    'ps2xRuntime/include/runtime/gs/ps2_tricky_hud_gl.h',
    'ps2xRuntime/include/runtime/ps2_address.h',
    'ps2xRuntime/include/runtime/ps2_audio.h',
    'ps2xRuntime/include/runtime/ps2_canonical_pages.h',
    'ps2xRuntime/include/runtime/ps2_memory.h',
    'ps2xRuntime/include/runtime/ps2_pad.h',
    'ps2xRuntime/include/runtime/ps2_pcm_ring.h',
    'ps2xRuntime/include/runtime/ps2_rom_device.h',
    'ps2xRuntime/include/runtime/ps2_savestate.h',
    'ps2xRuntime/include/runtime/ps2_vfs.h',
    'ps2xRuntime/include/runtime/ps2_vu0.h',
    'ps2xRuntime/include/runtime/ps2_vu1.h',
    'ps2xRuntime/include/runtime/ps2_vu_core.h',
    'ps2xRuntime/include/runtime/ps2_vu_state.h',
    'ps2xRuntime/include/runtime/third_party/xxhash.h',
    'ps2xRuntime/include/types.h',
    'ps2xRuntime/src/lib/Kernel/EeScheduler.cpp',
    'ps2xRuntime/src/lib/Kernel/EeSchedulerSavestate.cpp',
    'ps2xRuntime/src/lib/Kernel/Fh1Savestate.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/Audio.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/Audio.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/CD.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/CD.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/Common.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/Compatibility.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/Compatibility.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/DMA.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/DMA.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/Deci2.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/Deci2.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/FileIO.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/FileIO.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/Font.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/Font.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/GS.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/GS.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/Helpers/Support.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/IPU.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/IPU.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/LibC.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/LibC.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/MPEG.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/MPEG.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/MemoryCard.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/MemoryCard.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/Pad.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/Pad.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/RPC.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/RPC.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/SIF.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/SIF.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/System.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/System.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/TTY.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/TTY.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/Unimplemented.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/Unimplemented.h',
    'ps2xRuntime/src/lib/Kernel/Stubs/VU.cpp',
    'ps2xRuntime/src/lib/Kernel/Stubs/VU.h',
    'ps2xRuntime/src/lib/Kernel/Syscalls/Common.h',
    'ps2xRuntime/src/lib/Kernel/Syscalls/Deci2.cpp',
    'ps2xRuntime/src/lib/Kernel/Syscalls/Dispatcher.cpp',
    'ps2xRuntime/src/lib/Kernel/Syscalls/Dispatcher.h',
    'ps2xRuntime/src/lib/Kernel/Syscalls/FileIO.cpp',
    'ps2xRuntime/src/lib/Kernel/Syscalls/FileIO.h',
    'ps2xRuntime/src/lib/Kernel/Syscalls/Helpers/Loader.h',
    'ps2xRuntime/src/lib/Kernel/Syscalls/Helpers/Path.h',
    'ps2xRuntime/src/lib/Kernel/Syscalls/Helpers/Runtime.h',
    'ps2xRuntime/src/lib/Kernel/Syscalls/Helpers/State.h',
    'ps2xRuntime/src/lib/Kernel/Syscalls/Interrupt.cpp',
    'ps2xRuntime/src/lib/Kernel/Syscalls/Interrupt.h',
    'ps2xRuntime/src/lib/Kernel/Syscalls/RPC.cpp',
    'ps2xRuntime/src/lib/Kernel/Syscalls/RPC.h',
    'ps2xRuntime/src/lib/Kernel/Syscalls/Savestate.cpp',
    'ps2xRuntime/src/lib/Kernel/Syscalls/Sync.cpp',
    'ps2xRuntime/src/lib/Kernel/Syscalls/Sync.h',
    'ps2xRuntime/src/lib/Kernel/Syscalls/System.cpp',
    'ps2xRuntime/src/lib/Kernel/Syscalls/System.h',
    'ps2xRuntime/src/lib/Kernel/Syscalls/Thread.cpp',
    'ps2xRuntime/src/lib/Kernel/Syscalls/Thread.h',
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
    'ps2xRuntime/src/lib/ps2_iop_host.h',
    'ps2xRuntime/src/lib/ps2_iop_transport.h',
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
    'ps2xRuntime/src/lib/ps2_savestate_internal.h',
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
    'ps2xRuntime/src/lib/vu/ps2_vu_detail.h',
    'ps2xRuntime/src/lib/vu/ps2_vu_fmac_impl.h',
    'ps2xRuntime/src/lib/vu/ps2_vu_fmac_simd.h',
    'ps2xRuntime/src/lib/vu/ps2_vu_lower.cpp',
    'ps2xRuntime/src/lib/vu/ps2_vu_lower_impl.h',
    'ps2xRuntime/src/lib/vu/ps2_vu_recomp.cpp',
    'ps2xRuntime/src/lib/vu/ps2_vu_step_impl.h',
    'ps2xRuntime/src/lib/vu/ps2_vu_upper.cpp',
    'ps2xRuntime/src/lib/vu/ps2_vu_upper_impl.h',
    'ps2xRuntime/src/main.cpp',
    'ps2xRuntime/third_party/armsx2/ge1/CMakeLists.txt',
    'ps2xRuntime/third_party/armsx2/ge1/ge1_gs.cpp',
    'ps2xRuntime/third_party/armsx2/ge1/ge1_gs.h',
    'ps2xRuntime/third_party/armsx2/ge1/ge1_host.cpp',
    'ps2xRuntime/third_party/armsx2/ge1/tfx-prewarm-data.h',
    'ps2xRuntime/third_party/armsx2/mv2/CMakeLists.txt',
    'ps2xRuntime/third_party/armsx2/mv2/HeadlessAndroidStubs.cpp',
    'ps2xRuntime/third_party/armsx2/mv2/MicrovuBridge.cpp',
    'ps2xRuntime/third_party/armsx2/mv2/ps2_microvu_api.h',
    'ps2xRuntime/third_party/armsx2/platform/android/ax4b_android_stubs.cpp',
    'ps2xRuntime/third_party/soundtouch/CMakeLists.txt',
    'ps2xRuntime/third_party/soundtouch/COPYING.TXT',
    'ps2xRuntime/third_party/soundtouch/soundtouch/BPMDetect.h',
    'ps2xRuntime/third_party/soundtouch/soundtouch/FIFOSampleBuffer.h',
    'ps2xRuntime/third_party/soundtouch/soundtouch/FIFOSamplePipe.h',
    'ps2xRuntime/third_party/soundtouch/soundtouch/STTypes.h',
    'ps2xRuntime/third_party/soundtouch/soundtouch/SoundTouch.h',
    'ps2xRuntime/third_party/soundtouch/soundtouch/soundtouch_config.h',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/AAFilter.cpp',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/AAFilter.h',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/BPMDetect.cpp',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/FIFOSampleBuffer.cpp',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/FIRFilter.cpp',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/FIRFilter.h',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/InterpolateCubic.cpp',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/InterpolateCubic.h',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/InterpolateLinear.cpp',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/InterpolateLinear.h',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/InterpolateShannon.cpp',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/InterpolateShannon.h',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/PeakFinder.cpp',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/PeakFinder.h',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/RateTransposer.cpp',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/RateTransposer.h',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/SoundTouch.cpp',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/TDStretch.cpp',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/TDStretch.h',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/cpu_detect.h',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/cpu_detect_x86.cpp',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/mmx_optimized.cpp',
    'ps2xRuntime/third_party/soundtouch/source/SoundTouch/sse_optimized.cpp',
    'tools/ssx3/android-libs/CMakeLists.txt',
)
CONTRACT_GLOB_DIRS = ('ps2xRuntime/src/lib/Kernel', 'ps2xRuntime/src/runner')

# Names that never describe sources, filtered when no .git is available to
# enumerate the tracked set (Finder/Explorer droppings, editor backups).
JUNK_NAMES = frozenset({'.DS_Store', 'Thumbs.db', 'desktop.ini'})


def _is_junk(path):
    name = path.name
    return name.startswith('._') or name.endswith('~') or name in JUNK_NAMES


def _contract_paths():
    return [*CONTRACT_FILES, *CONTRACT_EXPLICIT, *CONTRACT_GLOB_DIRS]


def _ls_entries(paths):
    by_repo = {}
    for p in paths:
        by_repo.setdefault(_repo_for(p), []).append(p)
    for repo in by_repo:
        if not repo.is_dir():
            raise ValueError('contract repo missing: %s' % repo)
    entries = []
    for repo, rels in by_repo.items():
        out = subprocess.check_output(
            ['git', '-C', str(repo), 'ls-files', '-s', '--', *rels],
            stderr=subprocess.DEVNULL, text=True)
        for line in out.splitlines():
            head, _, path = line.partition('\t')
            fields = head.split()
            if len(fields) != 3:
                continue
            mode, blob, _ = fields
            if mode == '120000' or path == RELEASES_FILE or Path(path).name.startswith('._'):
                continue  # symlinks, the release-pin file and AppleDouble were never hashed
            entries.append((path, blob))
    return sorted(entries)


def _tracked_entries():
    """(relpath, blob) pairs from git, or None when .git is unavailable."""
    if not (ENGINE / '.git').exists() and not (ROOT / '.git').exists():
        return None
    try:
        entries = _ls_entries(_contract_paths())
    except (OSError, subprocess.CalledProcessError):
        return None
    # Fail closed: every explicit contract input must resolve. A silent drop
    # (e.g. the moved module unexpectedly untracked) would mint a wrong
    # support stamp instead of erroring.
    got = {p for p, _ in entries}
    missing = [p for p in CONTRACT_EXPLICIT if p not in got]
    if missing:
        raise ValueError('contract inputs untracked: %s' % ', '.join(missing))
    return entries


def contract_paths():
    """Every tracked path the stamp covers (tests and drift callers)."""
    entries = _tracked_entries()
    return {path for path, _ in entries} if entries is not None else set()


def _walked_entries():
    by_rel = {}
    for name in (*CONTRACT_FILES, *CONTRACT_EXPLICIT):
        p = _repo_for(name) / name
        if p.is_file() and not p.is_symlink() and not _is_junk(p):
            by_rel[name] = p
    for name in CONTRACT_GLOB_DIRS:
        for p in (ENGINE / name).rglob('*'):
            if not p.is_file() or p.is_symlink() or _is_junk(p):
                continue
            by_rel[p.relative_to(ENGINE).as_posix()] = p
    return sorted((rel, digest(p)) for rel, p in by_rel.items()
                  if rel != RELEASES_FILE)


def contract():
    """Content identity avoids commit/tag self-reference and rejects stale headers.

    With a .git checkout the tracked blob set is hashed, so Finder
    droppings, gitignored build outputs and other stray files cannot move
    the stamp. Without .git (a source bundle) the walked files are hashed
    with junk names filtered.
    """
    tracked = _tracked_entries()
    entries = tracked if tracked is not None else _walked_entries()
    return _stamp(entries)


def _stamp(entries):
    h = hashlib.sha256(json.dumps(RECIPE, sort_keys=True).encode())
    for rel, ident in entries:
        h.update(rel.encode() + b'\0')
        h.update(ident.encode() + b'\n')
    return h.hexdigest()


def contract_drift():
    """Working-tree paths that should move the build off the prebuilt.

    Tracked-set stamping is blind to working-tree edits and new files, so
    the consumer checks this separately: modified tracked files and new
    non-junk files under the contract paths fall back to the source build
    with these paths named. Junk droppings and gitignored outputs are
    ignored. Returns [] when git is unavailable.
    """
    by_repo = {}
    for p in _contract_paths():
        by_repo.setdefault(_repo_for(p), []).append(p)
    drift = []
    for repo, rels in by_repo.items():
        try:
            out = subprocess.check_output(
                ['git', '-C', str(repo), 'status', '--porcelain',
                 '--untracked-files=all', '--', *rels],
                stderr=subprocess.DEVNULL, text=True)
        except (OSError, subprocess.CalledProcessError):
            return []
        for line in out.splitlines():
            if len(line) < 4:
                continue
            # "XY <path>[ -> <new>]" — renames report the new path.
            path = line[3:].split(' -> ')[-1].strip('"')
            if line[:2] == '??' and _is_junk(Path(path)):
                continue
            drift.append(path)
    return drift


def safe_name(name):
    p = Path(name)
    return bool(name) and not p.is_absolute() and '\\' not in name and '..' not in p.parts


def allowed(name):
    if not safe_name(name):
        return False
    if name in ('manifest.json', 'runtime-definitions.txt', 'include/sse2neon.h'):
        return True
    if name in ['lib/' + x for x in ARCHIVES] + ['jniLibs/arm64-v8a/' + x for x in SHARED]:
        return True
    if name.startswith('notices/'):
        return Path(name).suffix.lower() not in ('.iso', '.elf', '.so', '.a', '.cpp', '.bin')
    # Renderer fonts and shader text only; no arcade cards, game database or UI art.
    if name.startswith('resources/fonts/'):
        return Path(name).suffix.lower() in ('.ttf', '.otf', '.txt') or 'license' in name.lower() or 'copyright' in name.lower()
    return name.startswith('resources/shaders/') and Path(name).suffix.lower() in ('.glsl', '.h', '.fx', '.vert', '.frag', '.geom', '.comp')


def verify(bundle, expected=None):
    bundle = Path(bundle)
    m = json.loads((bundle / 'manifest.json').read_text())
    if m.get('recipe') != RECIPE or m.get('contract') != (expected or contract()):
        raise ValueError('prebuilt source/toolchain contract mismatch')
    if not re.fullmatch('[0-9a-f]{40}', m.get('source_sha', '')):
        raise ValueError('missing matching source revision')
    files = m.get('files', {})
    required = {'lib/' + x for x in ARCHIVES} | {'jniLibs/arm64-v8a/' + x for x in SHARED}
    required |= {'include/sse2neon.h', 'runtime-definitions.txt', 'notices/PS2Recomp-LICENSE', 'notices/SOURCES.json'}
    if not required.issubset(files):
        raise ValueError('incomplete prebuilt bundle')
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file()}
    if actual != set(files) | {'manifest.json'}:
        raise ValueError('prebuilt manifest does not cover exactly the payload')
    if (bundle / 'manifest.json').is_symlink():
        raise ValueError('symlink manifest is not permitted')
    for name, sha in files.items():
        if not allowed(name) or not re.fullmatch('[0-9a-f]{64}', sha):
            raise ValueError('unapproved prebuilt file: ' + name)
        p = bundle / name
        if p.is_symlink() or any((bundle / parent).is_symlink() for parent in Path(name).parents):
            raise ValueError('symlinks are not permitted in bundles')
        if digest(p) != sha:
            raise ValueError('prebuilt SHA-256 mismatch: ' + name)
        if name.startswith('notices/'):
            if p.stat().st_size > 4 * 1024 * 1024 or b'\0' in p.read_bytes():
                raise ValueError('non-text or oversized notice: ' + name)
    return m


def extract(archive, destination):
    with zipfile.ZipFile(archive) as z:
        names = [i.filename for i in z.infolist()]
        if len(names) != len(set(names)) or sum(i.file_size for i in z.infolist()) > 3 * 1024**3:
            raise ValueError('duplicate names or oversized prebuilt archive')
        for item in z.infolist():
            if not allowed(item.filename) or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('unsafe/unapproved archive member: ' + item.filename)
            target = destination / item.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(item) as src, target.open('wb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)


def fetch(cache, mode='auto'):
    if mode == 'off':
        return None
    cache = Path(cache)
    stamp = contract()
    bundle = cache / stamp
    try:
        if bundle.is_dir():
            verify(bundle, stamp)
            drift = contract_drift()
            if drift:
                print('Prebuilt cache ignored: working-tree drift under the contract paths;',
                      file=sys.stderr)
                for path in drift:
                    print('  drift: ' + path, file=sys.stderr)
                print('Building from source.', file=sys.stderr)
                return None
            print('Verified offline prebuilt cache: ' + str(bundle))
            return bundle
        cache.mkdir(parents=True, exist_ok=True)
        tag = 'android-prebuilt-' + stamp
        asset = 'android-arm64-' + stamp + '.zip'
        base = RELEASES + tag + '/'
        with urllib.request.urlopen(base + 'SHA256SUMS', timeout=30) as response:
            sums = response.read(64 * 1024).decode('ascii')
        matches = [line.split() for line in sums.splitlines() if len(line.split()) == 2 and line.split()[1] == asset]
        if len(matches) != 1 or not re.fullmatch('[0-9a-f]{64}', matches[0][0]):
            raise ValueError('release checksum entry missing or ambiguous')
        with tempfile.TemporaryDirectory(prefix='download-', dir=cache) as scratch:
            tmp = Path(scratch)
            archive = tmp / asset
            with urllib.request.urlopen(base + asset, timeout=60) as response, archive.open('wb') as dst:
                shutil.copyfileobj(response, dst, 1024 * 1024)
            pin = release_pins().get(stamp)
            if not isinstance(pin, dict) or pin.get('tag') != tag or pin.get('asset') != asset:
                raise ValueError('no committed release pin for contract ' + stamp)
            if digest(archive) != pin.get('sha256'):
                raise ValueError('release asset does not match the committed pin')
            if digest(archive) != matches[0][0]:
                raise ValueError('release asset SHA-256 mismatch')
            unpacked = tmp / 'bundle'
            unpacked.mkdir()
            extract(archive, unpacked)
            verify(unpacked, stamp)
            unpacked.rename(bundle)
        print('Verified prebuilt release: ' + tag)
        return bundle
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print('No prebuilt release for contract ' + stamp + ' (' + tag + ').',
                  file=sys.stderr)
            for path in contract_drift():
                print('  contract drift: ' + path, file=sys.stderr)
        print('Prebuilt unavailable (' + str(e) + '); building from source.', file=sys.stderr)
        return None
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as e:
        print('Prebuilt unavailable (' + str(e) + '); building from source.', file=sys.stderr)
        return None


def run(*args):
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True)


def audit(bundle, nm, build_dir):
    """Fail closed on guest definitions/files; legitimate undefined table ABI is allowed."""
    verify(bundle)
    commands = json.loads((build_dir / 'compile_commands.json').read_text())
    for command in commands:
        path = Path(command['file'])
        if path.name == 'register_functions.cpp' or re.match(r'(sub_[0-9a-fA-F]+|vu0e?_[0-9a-fA-F]+)\.cpp$', path.name):
            raise ValueError('generated/stub registration source in export graph: ' + str(path))
    forbidden = re.compile(r'\b(?:sub_[0-9a-fA-F]{6,}|vu0e?_[0-9a-fA-F]{6,}|g_ps2RecompiledFunctionTable\w*|g_ps2xCodegenAbiVersion|_request_end_0x[0-9a-fA-F]+)\b')
    for p in sorted(bundle.rglob('*')):
        if p.suffix not in ('.a', '.so'):
            continue
        # Stream nm output: the unstripped renderer libraries can have many symbols.
        with tempfile.TemporaryFile(mode='w+t') as output:
            subprocess.run([str(nm), '--defined-only', '--demangle', str(p)], stdout=output, check=True)
            output.seek(0)
            for line in output:
                if forbidden.search(line):
                    raise ValueError('generated-code symbol in ' + p.name + ': ' + line.strip())
    print('PASS: no generated-code files or defined symbols in support artifacts')


def notices(root, destination, prefix):
    for p in sorted(root.rglob('*')):
        if not p.is_file() or p.is_symlink() or '.git' in p.parts:
            continue
        low = p.name.lower()
        if low.startswith(('license', 'licence', 'copying', 'notice', 'copyright')):
            target = destination / prefix / p.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)


def build(a):
    if os.environ.get('PS2X_GAME_CODEGEN_DIR') or os.environ.get('PS2X_VU0_RECOMP_DIR'):
        raise ValueError('CI/export environment must not contain generated-code inputs')
    if (a.work / 'inputs.json').exists() or (a.work / 'codegen').exists():
        raise ValueError('support builds require a separate game-free work directory')
    properties = (a.ndk / 'source.properties').read_text()
    if not re.search(r'Pkg.Revision\s*=\s*' + re.escape(NDK) + r'\s*$', properties, re.M):
        raise ValueError('prebuilt requires exact NDK ' + NDK)
    a.work.mkdir(parents=True, exist_ok=True)
    stamp = contract()
    spec = importlib.util.spec_from_file_location('ssx3_source_builder', ROOT / 'tools/ssx3/build.py')
    recipe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recipe)
    libraries = a.source_libraries.resolve() if a.source_libraries else a.work
    if a.source_libraries:
        # Local acceptance can reuse the freshly measured source libraries;
        # release CI never supplies this option. No disc or codegen is copied.
        vendor = libraries / 'android-assembly/vendor-pcsx2'
        actual = subprocess.check_output(['git', '-C', str(vendor), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != VENDOR:
            raise ValueError('reused library vendor pin mismatch: reused tree is at '
                             + actual + ' but the recipe builds ' + VENDOR)
        for directory, staged in [('ge1', 'adapter'), ('mv2', 'mv2-adapter')]:
            for source in (ENGINE / 'ps2xRuntime/third_party/armsx2' / directory).rglob('*'):
                if source.is_file():
                    copied = libraries / 'android-assembly' / staged / source.relative_to(ENGINE / 'ps2xRuntime/third_party/armsx2' / directory)
                    if not copied.is_file() or digest(source) != digest(copied):
                        raise ValueError('reused adapter source mismatch: ' + str(source))
        if digest(ENGINE / 'ps2xRuntime/third_party/armsx2/platform/android/ax4b_android_stubs.cpp') != digest(libraries / 'android-assembly/adapter/ax4b_android_stubs.cpp'):
            raise ValueError('reused Android stub source mismatch')
        for name in ('CMakeLists.txt',):
            if digest(ENGINE / 'tools/ssx3/android-libs' / name) != digest(libraries / 'android-assembly/android-platform' / name):
                raise ValueError('reused library recipe mismatch')
    else:
        recipe.android_libs(a)
    runtime = a.work / 'runtime-build'
    flags = ['-DCMAKE_BUILD_TYPE=RelWithDebInfo', '-DCMAKE_POSITION_INDEPENDENT_CODE=ON',
             '-DCMAKE_TOOLCHAIN_FILE=' + str(a.ndk / 'build/cmake/android.toolchain.cmake'),
             '-DANDROID_ABI=arm64-v8a', '-DANDROID_PLATFORM=android-29', '-DANDROID_STL=c++_static',
             '-DPS2X_PREBUILT_EXPORT=ON', '-DPS2X_PREBUILT_STAMP=' + stamp,
             '-DPS2X_GAME_CODEGEN_DIR=', '-DPS2X_VU0_RECOMP_DIR=', '-DPS2X_PGO_DATA=',
             '-DPS2X_DEFAULT_BOOT_ELF=/storage/emulated/0/Android/data/com.ssx3.recomp/files/SLUS_207.72',
             '-DPS2X_ANDROID_MARCH=armv8.2-a+fp16+dotprod', '-DPS2X_GAME_IPO=ON',
             '-DCMAKE_C_FLAGS_RELWITHDEBINFO=-O3 -g -DNDEBUG', '-DCMAKE_CXX_FLAGS_RELWITHDEBINFO=-O3 -g -DNDEBUG']
    flags += ['-D' + option + '=OFF' for option in ('PS2X_BUILD_RECOMP', 'PS2X_BUILD_ANALYZER', 'PS2X_BUILD_TEST',
              'PS2X_BUILD_STUDIO', 'PS2X_ENABLE_SCCACHE', 'PS2X_ENABLE_DIAG_TAPS', 'PS2X_ENABLE_DIAG_WATCH',
              'PS2X_ENABLE_TS2_DIAG', 'PS2X_ENABLE_RUNTIME_LOGS', 'PS2X_ENABLE_AGRESSIVE_LOGS',
              'PS2X_ENABLE_IOP_RPC_TRACE', 'PS2X_ENABLE_DEBUG_UI')]
    run(a.cmake, '-S', ENGINE, '-B', runtime, '-G', 'Ninja',
        '-DPS2X_GAME_MODULE_DIR=' + str(ROOT / 'games/ssx3/module'), *flags)
    run(a.cmake, '--build', runtime, '--parallel', a.jobs, '--target', 'ps2EntryRunner', 'hardware')
    bundle = a.work / 'bundle'
    if bundle.exists():
        raise ValueError('refusing existing output bundle; choose a fresh export directory')
    (bundle / 'lib').mkdir(parents=True)
    for name in (runtime / 'prebuilt-archives.txt').read_text().splitlines():
        src = Path(name)
        if src.name not in ARCHIVES:
            raise ValueError('unexpected support archive: ' + str(src))
        shutil.copy2(src, bundle / 'lib' / src.name)
    shutil.copytree(libraries / 'jniLibs', bundle / 'jniLibs')
    shutil.copy2(runtime / 'android/hwcompat/libhardware.so', bundle / 'jniLibs/arm64-v8a/libhardware.so')
    (bundle / 'include').mkdir()
    shutil.copy2(runtime / '_deps/sse2neon-src/sse2neon.h', bundle / 'include/sse2neon.h')
    shutil.copy2(runtime / 'prebuilt-definitions.txt', bundle / 'runtime-definitions.txt')
    for p in (libraries / 'android-resources').rglob('*'):
        name = 'resources/' + p.relative_to(libraries / 'android-resources').as_posix()
        if p.is_file() and allowed(name):
            target = bundle / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
    notice_dir = bundle / 'notices'
    notice_dir.mkdir()
    shutil.copy2(ROOT / 'LICENSE', notice_dir / 'PS2Recomp-LICENSE')
    notices(ENGINE / 'ps2xRuntime/third_party', notice_dir, 'runtime')
    notices(libraries / 'android-assembly/vendor-pcsx2', notice_dir, 'vendor')
    notices(libraries / 'deps-build', notice_dir, 'vendor-dependencies')
    notices(runtime / '_deps', notice_dir, 'runtime-dependencies')
    source_sha = subprocess.check_output(['git', '-C', str(ENGINE), 'rev-parse', 'HEAD'], text=True).strip()
    pins_file = libraries / 'deps-build' / 'android-dependency-pins.json'
    if not pins_file.is_file():
        raise ValueError('missing resolved dependency pins: ' + str(pins_file))
    pins = json.loads(pins_file.read_text())
    if ({n for n, _, _, _ in recipe.ANDROID_DEPS} != set(pins)
            or any(pins[n] != sha for n, _, _, sha in recipe.ANDROID_DEPS)):
        raise ValueError('resolved dependency pins do not match the recipe table')
    sources = {'PS2Recomp': 'https://github.com/brad-richardson/PS2Recomp/tree/' + source_sha,
               'armsx2': 'https://github.com/brad-richardson/armsx2/tree/' + VENDOR,
               'recipe_contract': stamp, 'ndk': NDK, 'dependencies': pins}
    (notice_dir / 'SOURCES.json').write_text(json.dumps(sources, indent=2) + '\n')
    files = {p.relative_to(bundle).as_posix(): digest(p) for p in sorted(bundle.rglob('*')) if p.is_file()}
    (bundle / 'manifest.json').write_text(json.dumps({'recipe': RECIPE, 'contract': stamp,
        'source_sha': source_sha, 'files': files}, indent=2, sort_keys=True) + '\n')
    tools = next(p for p in (a.ndk / 'toolchains/llvm/prebuilt').iterdir() if (p / 'bin/llvm-nm').is_file()) / 'bin'
    audit(bundle, tools / 'llvm-nm', runtime)
    output = a.work / 'release'
    output.mkdir(exist_ok=True)
    archive = output / ('android-arm64-' + stamp + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(bundle.rglob('*')):
            if p.is_file():
                z.write(p, p.relative_to(bundle).as_posix())
    (output / 'SHA256SUMS').write_text(digest(archive) + '  ' + archive.name + '\n')
    (output / 'SOURCE.json').write_text(json.dumps(sources, indent=2) + '\n')
    print('Release tag: android-prebuilt-' + stamp)
    print('Release assets: ' + str(output))
    print('Commit this pin to ' + RELEASES_FILE + ' before users can fetch the release:')
    print(json.dumps({stamp: {'tag': 'android-prebuilt-' + stamp,
                              'asset': archive.name,
                              'sha256': digest(archive)}}, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('contract')
    v = sub.add_parser('verify')
    v.add_argument('bundle', type=Path)
    b = sub.add_parser('build')
    b.add_argument('--work', type=Path, required=True)
    b.add_argument('--ndk', type=Path, required=True)
    b.add_argument('--jobs', type=int, default=4)
    b.add_argument('--cmake', default='cmake')
    b.add_argument('--source-libraries', type=Path, help='local validation only: reuse matching, audited source libraries; never used by release CI')
    # android_libs delegates only to the source recipe, never download itself.
    b.set_defaults(prebuilt='off', prebuilt_cache=None)
    a = p.parse_args()
    if a.command == 'contract':
        print(contract())
    elif a.command == 'verify':
        verify(a.bundle)
        print('Verified source contract and all prebuilt file hashes')
    else:
        a.work, a.ndk = a.work.resolve(), a.ndk.resolve()
        if a.work == ROOT or ROOT in a.work.parents or not 1 <= a.jobs <= 16:
            p.error('use an external private work directory and 1..16 jobs')
        build(a)


if __name__ == '__main__':
    main()
