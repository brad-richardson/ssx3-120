#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""Shared bounded boot: run the Mac runner on a route and collect receipts.

Canonical codegen pins, --route i26|fr1r1, --dump-ticks a,b,c
(PS2X_FRAME_DUMP_ONCE_TICKS, exactly 3), --wall. Default route fr1r1.

Modes:
  speed  diagnostics-off runner: PS2X_VSYNC_RATE_LOG=1 ([vsync-rate] every 5 s)
         + PS2X_UNPACED=1 (speed runs measure unpaced headroom).
         Stops at --stop-tick.
  det    det-hash runner (PS2X_ENABLE_DET_HASH_TAP=ON): PS2X_DET_HASH_EVERY=1 and
         continuous presentation-frame capture (PS2X_FRAME_DUMP_DIR) with a
         0.5 s snapshotter tagging upload-latest pairs by det-hash tick.
         Stops at --stop-tick + 3 s settle. No GS stream capture.

--backend external|cpu: external = GE1 on native Metal:
PS2X_GS_BACKEND=external + PS2X_GS_EXTERNAL_LIBRARY (a built dylib,
SHA-checked when --ge1-lib-sha is given, resources beside it) + a per-run
GE1_GS_DATA_DIR. cpu = the software GS (det-identical, 6-8x slower).

Both modes: PS2X_DETERMINISTIC=1, PS2X_SKIP_MOVIE=1 (dev-only), vsync pad
clock, empty mc0/mc1 under the run dir. Wall cap 500 s default (max 1800),
no-progress cap 120 s, log cap 64 MiB. A (wall, tick) trace is written every
poll for phase-rate analysis.

Profiles (--profile reference|play|full120|full120events|play120):
  reference  marker only (PS2X_PROFILE=reference): records intent in
             result.json and the [knobs] line.
  play       the guest-affecting play knobs (PS2X_PROFILE=play): microVU +
             MTVU(+LAG) + stock 60 + readback off + pcsx2 FINISH.
             The caller supplies PS2X_MICROVU_LIB (no absolute paths in
             code); explicit --env wins over the profile.
  full120    as play with PS2X_SSX3_SIM_MODE=stock, PS2X_SSX3_FULL120=1 and
             the full fix set (PS2X_SSX3_FULL120_FIX=all). Guest VBlanks run
             at 2x59.94 Hz, so stop/dump/hash ticks count 120 Hz ticks.
  full120events  as full120 with PS2X_SSX3_FULL120=events (stock 60 in
             boot/menus/loading/pause/results, full120 from race entry to
             exit), so ticks count 60 Hz outside races, 120 Hz inside.
  play120    the events profile plus the signed-off feel-test fix list and
             fast hooks (see PLAY120_ENV).

Save states (det mode): --save-at T --save-path F [--exit-after-save] writes
one state at the first saveable dispatcher point with vsync tick >= T;
--load F restores it after init; --strict also refuses a runner-SHA mismatch.
A loaded run's det-hash lines start at T+1. States hold game RAM: scratch
only.

Usage: ssx3_boot.py --mode speed|det [--backend external|cpu] --runner PATH
  --label NAME --out DIR --iso FILE --elf FILE --codegen DIR
  [--ge1-lib PATH] [--microvu-lib PATH] [--profile P] [--route R] ...
The lab build-slot lease (p_lane_lease) is used when importable; otherwise
the boot runs unleased (public default). SSX3_HELD_SLOT reuses a pre-held
slot without releasing it.
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import signal  # noqa: E402

try:
    from p_lane_lease import (  # lab build-slot lease; absent outside the lab
        LEASE_WAIT_S,
        claim,
        claim_wait,
        release,
        runner_pids,
    )
    HAVE_LEASE = True
except ImportError:
    HAVE_LEASE = False
    LEASE_WAIT_S = 30

    def claim(*args, **kwargs):
        return None

    def claim_wait(label, exclusive=False):
        return None

    def release(slot):
        return None

    def runner_pids(names):
        return []

# Host play profile: the guest-affecting play knobs. Exact by design,
# re-proven on-device by the play-build A/B gate. VU0_RECOMP + VU0_DIRECT are
# on here (the Android play env has them; without them the Mac interprets
# every VU0 microprogram); needs a runner built with PS2X_VU0_RECOMP_DIR.
# PS2X_MICROVU_LIB comes from the caller (--microvu-lib).
# Sun-glare readback off: the play default is the literal "0", matching the
# Android play env and the Android compiled default.
PLAY_ENV = {
    'PS2X_PROFILE': 'play',
    'PS2X_VU1_ENGINE': 'microvu',
    'PS2X_MTVU': '1',
    'PS2X_MTVU_LAG': '1',
    'PS2X_SSX3_SIM_MODE': 'stock',
    'PS2X_VIF1_REVERSE_DMA': '0',
    'PS2X_GS_FINISH_TIMING': 'pcsx2',
    'PS2X_VU0_RECOMP': '1',
    'PS2X_VU0_DIRECT': '1',
    # The VU1 GIF/VIF stages (transport only: both Mac keys stay IDENTICAL
    # with them on).
    'PS2X_MTVU_GIF_STAGE': '1',
    'PS2X_MTVU_VIF_STAGE': '1',
    # The microVU flag hack + the probe-drain pair (always together) + the
    # movie skip play default. All det IDENTICAL; mirrors the compiled
    # kDefaults / Mac table in the engine.
    'PS2X_MICROVU_FLAG_HACK': '1',
    'PS2X_MTVU_FINISH_EE': '1',
    'PS2X_MTVU_VIF1_STAT_FREE': '1',
    'PS2X_SKIP_MOVIE': '1',
    # Anisotropic 16x everywhere. Output-only; Mac play det IDENTICAL.
    'GE1_ANISO': '16',
    # Desktop joins the mobile play envs (signed-off play behaviour on both
    # devices): heap ceiling 0x01F31000. FULL120_EVENTS_ENV/PLAY120_ENV
    # inherit via dict(); reference keeps the exact path (no PLAY_ENV).
    # Guest-affecting: the three play-family keys re-mint.
    'PS2X_HLE_POOLS_LOW': '1',
    # F4 (appsec-fix-1): lab gate runs with the dev gate open so diagnostic
    # file knobs keep working; det keys are unaffected (no diag knob in this
    # env changes guest state).
    'PS2X_DEV': '1',
}

# FH1: the full-120 branch profile (PS2X_PROFILE stays play: the runtime has
# no full120 compiled defaults; the extra keys select the branch's mode).
FULL120_ENV = dict(PLAY_ENV, PS2X_SSX3_SIM_MODE='stock', PS2X_SSX3_FULL120='1',
                   PS2X_SSX3_FULL120_FIX='all', PS2X_VIF1_REVERSE_DMA='0')
FULL120_EVENTS_ENV = dict(FULL120_ENV, PS2X_SSX3_FULL120='events')
PLAY120_ENV = dict(FULL120_EVENTS_ENV,
                   PS2X_SSX3_FULL120_FIX='all,jcam,pid,jcam2,ground2,life2,inputchain,trails,query,vnret,shadowpub,groundstep,trailring,sel5,normcap,sel5b,boostfill,stuntchg,flare,asymk',
                   PS2X_SSX3_FULL120_FASTHOOKS='1', PS2X_VIF1_REVERSE_DMA='lagF')


# Supported input hashes (the user's own disc/ELF; see docs/ssx3-inputs.md).
# Generated-code SHAs are passed per-run (--codegen-register-sha from the key
# pins); the defaults below are the engine-pinned codegen under
# deps.lock.json's generator pin.
ISO_SHA = '3c2f8eb182c9c6208a6e8172a41e61c98f420abe3f42c845f6829aeb9761ebf5'
ELF_SHA = '1b49d05ca2793922180851b9e1ce9ae2291d61a7863565ac4e71f12e967af7bc'
# GE1 on native Metal (the adapter's Mac default is Vulkan, so the boot sets
# it; --env GE1_RENDERER=vk selects MoltenVK). Resources live beside the dylib.
GE1_RENDERER = 'metal'

# Menu-navigation pad scripts (vsync clock; race HUD at tick ~1714): short
# inline tap sequences, not recordings. fr1r1 rides the whole course
# untouched (the i26 tail hold is dropped).
ROUTE = ('10611:start:250,12780:cross:250,14749:cross:250,16567:cross:250,18336:cross:250,'
         '19770:cross:250,21205:down:150,21706:down:150,22306:cross:250,24025:cross:250,'
         '28512:cross:200,28763:down:700,29513:cross:200,29764:down:700,30514:cross:200,'
         '30765:down:700,31515:cross:200,31766:down:700,32516:cross:200,32767:down:700,'
         '33517:cross:200,33768:down:700,34518:cross:200,34769:down:700,35519:cross:200,'
         '35770:down:700,36520:cross:200,36771:down:700,37521:cross:200,37772:down:700,'
         '38522:down:30000')
# FR1-R1: I26-FAST minus the final down hold (the rider rides the whole course untouched).
ROUTE_FR1R1 = ROUTE.rsplit(',', 1)[0]

WALL_CAP_S = 500
PROGRESS_CAP_S = 120
LOG_CAP_BYTES = 64 * 1024 * 1024
FRAMES_CAP_BYTES = 1 * 1024 * 1024 * 1024
SNAP_PERIOD_S = 0.5
POLL_S = 0.5
PS_EVERY_S = 10
PS_START_TICK = 1750
PS_MAX_SAMPLES = 12

HASH_TICK = re.compile(rb'\[det-hash:v1\] tick=(\d+)\b')
HASH_ERROR_MARKERS = (b'[det-hash] null', b'[det-hash] line cap', b'[det-hash] invalid')
RATE_TICK = re.compile(rb'\[vsync-rate\] tick=(\d+)\b')
GS_PATH = re.compile(r'^\[gs-path\].*$', re.M)
GS_FATAL = re.compile(r'^\[gs:external\] GE1 .*failed.*$', re.M)


def sha_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def dir_bytes(path):
    total = 0
    for root, _ds, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def main():
    global LOG_CAP_BYTES, WALL_CAP_S
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=('speed', 'det'), required=True)
    ap.add_argument('--backend', choices=('external', 'cpu'), default='external')
    ap.add_argument('--ge1-lib', default='',
                    help='GE1 dylib for --backend external (resources = its dir/resources); '
                         'SHA-checked when --ge1-lib-sha is given')
    ap.add_argument('--ge1-lib-sha', default='',
                    help='expected SHA-256 of --ge1-lib (empty = record only)')
    ap.add_argument('--microvu-lib', default='',
                    help='microVU dylib (play profiles); SHA-checked when --microvu-lib-sha is given')
    ap.add_argument('--microvu-lib-sha', default='',
                    help='expected SHA-256 of --microvu-lib (empty = record only)')
    ap.add_argument('--runner', required=True)
    ap.add_argument('--label', required=True)
    ap.add_argument('--out', required=True, help='run dir (created; must not exist)')
    ap.add_argument('--iso', required=True, help='own supported USA disc image')
    ap.add_argument('--elf', required=True, help='boot ELF extracted from --iso (its dir is the cd root)')
    ap.add_argument('--codegen', required=True,
                    help='generated game-code dir (register_functions.cpp SHA-checked '
                         'when --codegen-register-sha is given)')
    ap.add_argument('--codegen-register-sha', default='',
                    help='expected SHA-256 of register_functions.cpp (empty = record only)')
    ap.add_argument('--stop-tick', type=int, default=2400)
    ap.add_argument('--sound', choices=('on', 'off'), default='on')
    ap.add_argument('--coverage-tick', type=int, default=2400)
    ap.add_argument('--unpaced', action='store_true',
                    help='set PS2X_UNPACED=1 in det mode; speed mode always sets it')
    ap.add_argument('--vu1-stats', action='store_true',
                    help='set PS2X_VU1_RECOMP_STATS=1 (prints the [vu0-recomp] line only)')
    ap.add_argument('--stack-kb', type=int, default=0,
                    help='set PS2X_GAME_THREAD_STACK_KB=N (flatness test)')
    ap.add_argument('--route', choices=('i26', 'fr1r1'), default='fr1r1')
    ap.add_argument('--dump-ticks', default='')
    ap.add_argument('--wall', type=int, default=500)
    ap.add_argument('--log-cap-mb', type=int, default=64,
                    help='boot.log cap in MiB (default 64)')
    ap.add_argument('--hash-every', type=int, default=1)
    ap.add_argument('--no-snap', action='store_true')
    ap.add_argument('--profile', choices=('reference', 'play', 'full120', 'full120events', 'play120'), default='',
                    help='reference marker or host play knobs')
    ap.add_argument('--env', action='append', default=[], help='extra K=V (wins over --profile)')
    ap.add_argument('--save-at', type=int, default=0, help='save a state at this vsync tick')
    ap.add_argument('--save-path', default='', help='state file for --save-at')
    ap.add_argument('--exit-after-save', action='store_true', help='stop the runner after the save')
    ap.add_argument('--load', default='', help='restore this state after init')
    ap.add_argument('--strict', action='store_true', help='refuse a runner-SHA mismatch on load')
    args = ap.parse_args()
    if args.log_cap_mb <= 0:
        ap.error('--log-cap-mb must be positive')
    LOG_CAP_BYTES = args.log_cap_mb * 1024 * 1024
    WALL_CAP_S = min(args.wall, 1800)

    runner = Path(args.runner).resolve()
    # Absolute: the runner's cwd is the run dir, so relative env paths would nest.
    lane = Path(args.out).resolve()
    if lane.exists():
        raise SystemExit('refusing to reuse %s' % lane)
    lane.mkdir(parents=True)
    (lane / 'mc0').mkdir()
    (lane / 'mc1').mkdir()
    frames_dir = lane / 'frames'
    snap_dir = frames_dir / 'snap'
    if args.mode == 'det':
        snap_dir.mkdir(parents=True)
    log = lane / 'boot.log'

    iso = Path(args.iso).resolve()
    elf = Path(args.elf).resolve()
    codegen = Path(args.codegen).resolve()
    register = codegen / 'register_functions.cpp'
    if not register.is_file():
        raise SystemExit('missing register_functions.cpp in --codegen %s' % codegen)
    pins = {str(iso): ISO_SHA, str(elf): ELF_SHA}
    big = [runner, iso, elf]
    lib_paths = []
    if args.codegen_register_sha:
        pins[str(register)] = args.codegen_register_sha
    ge1_lib = Path(args.ge1_lib).resolve() if args.ge1_lib else None
    if args.backend == 'external':
        if ge1_lib is None or not ge1_lib.is_file():
            raise SystemExit('--backend external needs --ge1-lib')
        big.append(ge1_lib)
        lib_paths.append(ge1_lib)
        if args.ge1_lib_sha:
            pins[str(ge1_lib)] = args.ge1_lib_sha
    microvu_lib = Path(args.microvu_lib).resolve() if args.microvu_lib else None
    if args.profile in ('play', 'full120', 'full120events', 'play120'):
        if microvu_lib is None or not microvu_lib.is_file():
            raise SystemExit('--profile %s needs --microvu-lib' % args.profile)
        big.append(microvu_lib)
        lib_paths.append(microvu_lib)
        if args.microvu_lib_sha:
            pins[str(microvu_lib)] = args.microvu_lib_sha
    reads = [{str(p): sha_of(p) for p in big} for _ in range(2)]
    if reads[0] != reads[1]:
        raise SystemExit('two SHA reads differ')
    for path, want in pins.items():
        if reads[0].get(path) != want:
            raise SystemExit('SHA mismatch for %s' % path)
    lib_shas = {str(p): reads[0][str(p)] for p in lib_paths}

    env = {k: v for k, v in os.environ.items() if not k.startswith('PS2X_')}
    env.update(PS2X_CD_IMAGE=str(iso), PS2X_SKIP_MOVIE='1', PS2X_DETERMINISTIC='1',
               PS2X_PAD_SCRIPT_CLOCK='vsync', PS2X_PAD_SCRIPT=(ROUTE_FR1R1 if args.route == 'fr1r1' else ROUTE),
               PS2X_MC_ROOT=str(lane / 'mc0'), PS2X_MISSING_FUNCTION_POLICY='stop',
               COPYFILE_DISABLE='1')
    if args.backend == 'external':
        # GE1S: GE1 on native Metal (the adapter's Mac default is Vulkan, so set it; back thread off).
        (lane / 'ge1-data').mkdir()
        env.update(PS2X_GS_BACKEND='external', PS2X_GS_EXTERNAL_LIBRARY=str(ge1_lib), GE1_RENDERER=GE1_RENDERER,
                   GE1_GS_RESOURCES_DIR=str(ge1_lib.parent / 'resources'),
                   GE1_GS_DATA_DIR=str(lane / 'ge1-data'))
    if args.mode == 'det':
        env.update(PS2X_DET_HASH_EVERY=str(args.hash_every), PS2X_FRAME_DUMP_DIR=str(frames_dir),
                   PS2X_SND_LOG=str(lane / 'snd.log'),
                   PS2X_COVERAGE_TICK=str(args.coverage_tick))
        if args.sound == 'on':
            env['PS2X_SOUND'] = '1'
        if args.unpaced:
            env['PS2X_UNPACED'] = '1'
        if args.vu1_stats:
            env['PS2X_VU1_RECOMP_STATS'] = '1'
        if args.stack_kb:
            env['PS2X_GAME_THREAD_STACK_KB'] = str(args.stack_kb)
        if args.dump_ticks:
            env['PS2X_FRAME_DUMP_ONCE_TICKS'] = args.dump_ticks
        tick_re = HASH_TICK
    else:
        env.update(PS2X_VSYNC_RATE_LOG='1', PS2X_SOUND='1',
                   PS2X_UNPACED='1')  # FP1: speed runs measure unpaced headroom
        tick_re = RATE_TICK
    if args.profile == 'reference':
        env['PS2X_PROFILE'] = 'reference'
    elif args.profile == 'play':
        env.update(PLAY_ENV)
    elif args.profile == 'full120':
        env.update(FULL120_ENV)
    elif args.profile == 'full120events':
        env.update(FULL120_EVENTS_ENV)
    elif args.profile == 'play120':
        env.update(PLAY120_ENV)
    if microvu_lib is not None and args.profile not in ('', 'reference'):
        env.setdefault('PS2X_MICROVU_LIB', str(microvu_lib))
    for kv in args.env:
        k, v = kv.split('=', 1)
        env[k] = v
    if (args.save_at or args.load) and args.mode != 'det':
        raise SystemExit('--save-at/--load need --mode det (PS2X_DETERMINISTIC=1)')
    if args.save_at:
        if not args.save_path:
            raise SystemExit('--save-at needs --save-path')
        env['PS2X_SAVESTATE_SAVE_AT'] = str(args.save_at)
        env['PS2X_SAVESTATE_PATH'] = str(Path(args.save_path).resolve())
        if args.exit_after_save:
            env['PS2X_SAVESTATE_EXIT_AFTER_SAVE'] = '1'
    if args.load:
        if not Path(args.load).exists():
            raise SystemExit('no such state %s' % args.load)
        env['PS2X_SAVESTATE_LOAD'] = str(Path(args.load).resolve())
    if args.strict:
        env['PS2X_SAVESTATE_STRICT'] = '1'

    exclusive = args.mode == 'speed'
    held = os.environ.get('SSX3_HELD_SLOT')
    if held:
        slot = held  # pre-held by the caller across back-to-back boots; no release here
        print(json.dumps({'event': 'using-held-slot', 'slot': slot}), flush=True)
    elif HAVE_LEASE:
        # Lab build-slot lease: serializes boots with other lanes on shared hosts.
        signal.signal(signal.SIGTERM, lambda sg, fr: sys.exit(128 + sg))
        slot = claim_wait('boot-' + args.label, exclusive=exclusive)
        if slot is None:
            return 3  # claim_wait printed INFRA-BLOCKED
    else:
        slot = None
        print(json.dumps({'event': 'unleased-boot', 'label': args.label}), flush=True)
        if exclusive:
            print('WARNING: speed mode without a lease cannot enforce exclusivity', flush=True)
    # A speed leg never starts beside another ps2EntryRunner.
    runner_check = None
    if args.mode == 'speed':
        names = ('ps2EntryRunner', runner.name)
        t_chk, others = time.monotonic(), runner_pids(names)
        while others and time.monotonic() - t_chk < LEASE_WAIT_S:
            print(json.dumps({'event': 'speed-runner-check', 'others': others, 'waiting': True}), flush=True)
            time.sleep(10)
            others = runner_pids(names)
        runner_check = {'others': others, 'waited_s': round(time.monotonic() - t_chk, 1)}
        print(json.dumps({'event': 'speed-runner-check', **runner_check}), flush=True)
        if others:
            print('INFRA-BLOCKED: speed leg %s refused: other ps2EntryRunner pids %s alive > %d s'
                  % (args.label, others, LEASE_WAIT_S), flush=True)
            if slot is not None and not held:
                release(slot)
            return 3

    result = {'label': args.label, 'mode': args.mode, 'backend': args.backend,
              'runner': str(runner), 'sha_reads': reads, 'lib_shas': lib_shas,
              'codegen_register_sha': sha_of(register),
              'stop_tick': args.stop_tick,
              'slot': slot, 'exclusive': exclusive, 'runner_check': runner_check, 'sound': args.sound,
              'env': {k: v for k, v in env.items()
                      if k.startswith('PS2X_') or k.startswith('GE1_')},
              'load_start': os.getloadavg()}
    proc = None
    stop_snap = threading.Event()
    shared = {'tick': 0}
    trace_path = lane / 'trace.jsonl'

    def snapshotter(t0):
        latest = frames_dir / 'upload-latest.png'
        latest_txt = frames_dir / 'upload-latest.txt'
        while not stop_snap.wait(SNAP_PERIOD_S):
            el = time.monotonic() - t0
            try:
                if latest.exists():
                    shutil.copyfile(latest, snap_dir / ('snap-%06dt-%07.2fs.png'
                                                        % (shared['tick'], el)))
                if latest_txt.exists():
                    shutil.copyfile(latest_txt, snap_dir / ('snap-%06dt-%07.2fs.txt'
                                                            % (shared['tick'], el)))
            except OSError:
                pass

    try:
        t0 = time.monotonic()
        trace = trace_path.open('w')
        with log.open('wb') as out:
            proc = subprocess.Popen([str(runner), str(elf)], cwd=lane, env=env,
                                    stdout=out, stderr=subprocess.STDOUT)
            result['runner_pid'] = proc.pid
            print(json.dumps({'event': 'boot', 'label': args.label, 'pid': proc.pid,
                              'slot': slot}), flush=True)
            th = None
            if args.mode == 'det' and not args.no_snap and not args.dump_ticks:
                th = threading.Thread(target=snapshotter, args=(t0,), daemon=True)
                th.start()
            last_tick, last_progress, bound = 0, t0, None
            ps_samples, last_ps = 0, 0.0
            try:
                while True:
                    now = time.monotonic()
                    if proc.poll() is not None:
                        bound = 'exit'
                        if args.exit_after_save and b'[savestate] saved' in log.read_bytes()[-65536:]:
                            bound = 'target'
                        break
                    if now - t0 >= WALL_CAP_S:
                        bound = 'wall_cap'
                        break
                    if log.stat().st_size > LOG_CAP_BYTES:
                        bound = 'log_cap'
                        break
                    if args.mode == 'det' and dir_bytes(frames_dir) > FRAMES_CAP_BYTES:
                        bound = 'frames_cap'
                        break
                    tail = log.read_bytes()[-65536:]
                    if args.mode == 'det' and any(m in tail for m in HASH_ERROR_MARKERS):
                        bound = 'hash_error'
                        break
                    m = tick_re.findall(tail)
                    if m and int(m[-1]) > last_tick:
                        last_tick = int(m[-1])
                        last_progress = now
                        shared['tick'] = last_tick
                    trace.write(json.dumps({'wall_s': round(now - t0, 3),
                                            'tick': last_tick}) + '\n')
                    trace.flush()
                    if now - last_progress >= PROGRESS_CAP_S:
                        bound = 'progress_cap'
                        break
                    if (args.mode == 'speed' and last_tick >= PS_START_TICK
                            and ps_samples < PS_MAX_SAMPLES and now - last_ps >= PS_EVERY_S):
                        last_ps = now
                        try:
                            ps = subprocess.run(['ps', '-M', str(proc.pid)],
                                                capture_output=True, timeout=15)
                            (lane / ('ps-race-%02d.txt' % ps_samples)).write_bytes(ps.stdout)
                            ps_samples += 1
                        except (OSError, subprocess.SubprocessError):
                            pass
                    if last_tick >= args.stop_tick:
                        bound = 'target'
                        if args.mode == 'det':
                            time.sleep(3)  # let the snapshotter grab final frames
                        break
                    time.sleep(POLL_S)
            finally:
                stop_snap.set()
                if th is not None:
                    th.join(timeout=10)
            if proc.poll() is None:
                proc.terminate()
                try:
                    result['runner_rc'] = proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    result['runner_rc'] = proc.wait(timeout=10)
                    bound += '+kill'
            else:
                result['runner_rc'] = proc.returncode
        trace.close()
        text = log.read_text(errors='replace')
        gs_path = GS_PATH.findall(text)
        gs_fatal = GS_FATAL.findall(text)
        hud_wall = None
        prev = None
        for line in trace_path.read_text().splitlines():
            row = json.loads(line)
            if row['tick'] >= 1714:
                if prev is not None and row['tick'] != prev['tick']:
                    f = (1714 - prev['tick']) / (row['tick'] - prev['tick'])
                    hud_wall = round(prev['wall_s'] + f * (row['wall_s'] - prev['wall_s']), 2)
                else:
                    hud_wall = row['wall_s']
                break
            prev = row
        result.update(bound=bound, elapsed_s=round(time.monotonic() - t0, 3),
                      last_tick=last_tick, log_bytes=log.stat().st_size,
                      hud_wall_s=hud_wall, ps_samples=ps_samples,
                      gs_path_line=gs_path[0] if gs_path else None,
                      gs_fatal=gs_fatal[0] if gs_fatal else None,
                      load_end=os.getloadavg())
        (lane / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({k: v for k, v in result.items()
                          if k not in ('env', 'sha_reads')}), flush=True)
        return 0 if bound == 'target' else 1
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        if slot is not None and not held:
            release(slot)


if __name__ == '__main__':
    sys.exit(main())
