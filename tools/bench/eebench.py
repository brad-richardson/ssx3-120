#!/usr/bin/env python3
"""EBN1: fixed-workload EE/GameThread replay bench (Mac).

Mint:  boot from tick 0 on a fixed pad script, save a state at T0, exit.
Run:   load the state, replay the same script to T1, save + exit, measure.
AB:    interleaved A/B run pairs with median/MAD/CV + bootstrap 95% CI.

Usage:
  eebench.py mint --runner BIN --iso DISC.ISO --elf SLUS_... --ge1-lib LIB \
      --microvu-lib LIB --pad PAD --out DIR --save-at T0 [--card DIR] [--env K=V ...]
  eebench.py run  --runner BIN --iso ... --pad PAD --out DIR --state FILE \
      --load-tick T0 --save-at T1 [--card DIR] [--env K=V ...]
  eebench.py ab   --runner BIN --iso ... --pad PAD --out DIR --state FILE \
      --load-tick T0 --save-at T1 --runs K --a "K=V,..." --b "K=V,..."

Inputs default under --pinned DIR (standard pinned layout); --work DIR holds
the sampler build (default ~/ssx3-work/eebench). Every invocation takes the
lab p-lane lease when importable, otherwise runs unleased (public default:
noisier on shared hosts). No push, no devices. Wall cap 600 s per run,
no-progress cap 120 s, log cap 16 MiB.

Metrics per run (measure window = [save_tick_of_load + SKIP, T1_actual]):
  gthread_ms_per_upd  GameThread thread-CPU (Mach thread_info, user+sys)
                      delta across the window / window ticks (thread clock)
  ins_per_upd         process retired instructions (proc_pid_rusage V4) / ticks
  cyc_per_upd         process cycles / ticks
  end_hash            det-hash `combined` at the final tick (identical => same guest state)
"""
import argparse
import hashlib
import json
import math
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent

try:
    from p_lane_lease import (  # lab build-slot lease; absent outside the lab
        claim_wait,
        release,
        runner_pids,
    )
    HAVE_LEASE = True
except ImportError:
    HAVE_LEASE = False

    def claim_wait(label, exclusive=False):
        return None

    def release(slot):
        return None

    def runner_pids(names):
        return []

# Pinned-input SHAs (game disc + backend libs); override with --*-sha.
ISO_SHA = '3c2f8eb182c9c6208a6e8172a41e61c98f420abe3f42c845f6829aeb9761ebf5'
ELF_SHA = '1b49d05ca2793922180851b9e1ce9ae2291d61a7863565ac4e71f12e967af7bc'
GE1_LIB_SHA = '37a81b4d7a200e882bb1f3c5a2fc939f023275a59f35043a50187284596d45cc'

# Standard pinned layout resolved under --pinned.
PINNED_ISO = 'game/ssx3-usa-3c2f8eb1/SSX 3 (USA).iso'
PINNED_ELF = 'game/ssx3-usa-3c2f8eb1/cd/SLUS_207.72'
PINNED_GE1 = 'mac/ge1-dylib-37a81b4d/libge1_gs.dylib'
PINNED_MICROVU = 'mac/microvu-dylib-ac245573/libmv2_microvu.dylib'


def resolve_paths(args):
    """Operator inputs: explicit flags win; --pinned fills the rest."""
    def pick(explicit, rel):
        if explicit:
            return Path(explicit).expanduser()
        if args.pinned:
            return Path(args.pinned).expanduser() / rel
        return None
    paths = {
        'iso': pick(args.iso, PINNED_ISO),
        'elf': pick(args.elf, PINNED_ELF),
        'ge1': pick(args.ge1_lib, PINNED_GE1),
        'microvu': pick(args.microvu_lib, PINNED_MICROVU),
        'card': Path(args.card).expanduser() if args.card else None,
        'pad': Path(args.pad).expanduser(),
        'work': Path(args.work).expanduser(),
    }
    missing = [n for n, k in (('--iso', 'iso'), ('--elf', 'elf'),
                              ('--ge1-lib', 'ge1'), ('--microvu-lib', 'microvu'))
               if not paths[k]]
    if missing:
        raise SystemExit('%s required (or pass --pinned)' % ', '.join(missing))
    if not paths['pad'].is_file():
        raise SystemExit('pad not found: %s' % paths['pad'])
    return paths


def hold_lease(label):
    """Exclusive bench lease when the lab module is importable, else None."""
    if HAVE_LEASE:
        slot = claim_wait(label, exclusive=True)
        if slot is None:
            raise SystemExit(3)
        return slot
    print('WARNING: no p-lane lease; running unleased (noisier on shared hosts)',
          flush=True)
    return None

# full120events play env (ssx3_boot.py FULL120_EVENTS_ENV) + GT3 FASTHOOKS
# (Odin default set; VBK1 ABBA recipe) + Mac Metal GE1 defaults.
BASE_ENV = {
    'PS2X_PROFILE': 'play',
    'PS2X_VU1_ENGINE': 'microvu',
    'PS2X_MTVU': '1',
    'PS2X_MTVU_LAG': '1',
    'PS2X_SSX3_SIM_MODE': 'stock',
    'PS2X_SSX3_FULL120': 'events',
    'PS2X_SSX3_FULL120_FIX': 'all',
    'PS2X_SSX3_FULL120_FASTHOOKS': '1',
    'PS2X_VIF1_REVERSE_DMA': '0',
    'PS2X_GS_FINISH_TIMING': 'pcsx2',
    'PS2X_VU0_RECOMP': '1',
    'PS2X_VU0_DIRECT': '1',
    'PS2X_MTVU_GIF_STAGE': '1',
    'PS2X_MTVU_VIF_STAGE': '1',
    'PS2X_MICROVU_FLAG_HACK': '1',
    'PS2X_MTVU_FINISH_EE': '1',
    'PS2X_MTVU_VIF1_STAT_FREE': '1',
    'PS2X_SKIP_MOVIE': '1',
    'GE1_ANISO': '16',
}

WALL_CAP_S = 600
PROGRESS_CAP_S = 120
LOG_CAP_BYTES = 16 * 1024 * 1024
POLL_S = 0.05
SKIP_TICKS = 200  # post-load transient excluded from the measure window

DET_RE = re.compile(rb'\[det-hash:v1\] tick=(\d+) .*combined=([0-9a-f]+) count=(\d+)')
RATE_RE = re.compile(rb'\[vsync-rate\] tick=(\d+) ')
THREADCPU_RE = re.compile(r'\[thread-cpu\] tick=(\d+)(.*)')
SAVED_RE = re.compile(r'\[savestate\] saved tick=(\d+) .* path=(\S+)')
HASH_ERROR = (b'[det-hash] null', b'[det-hash] line cap', b'[det-hash] invalid')
FATAL_RE = re.compile(r'FATAL|failed|refuse', re.I)


def sha_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_pins(runner, paths, args):
    """Two matching SHA reads; disc/lib inputs must match the expected SHAs."""
    big = [Path(runner), paths['iso'], paths['elf'], paths['ge1'],
           paths['microvu']]
    reads = [{str(p): sha_of(p) for p in big} for _ in range(2)]
    if reads[0] != reads[1]:
        raise SystemExit('two SHA reads differ')
    for key, want in (('iso', args.iso_sha), ('elf', args.elf_sha),
                      ('ge1', args.ge1_sha)):
        if reads[0][str(paths[key])] != want:
            raise SystemExit('SHA mismatch for %s' % paths[key])
    return reads[0]


def ensure_sampler(work):
    exe = work / 'ebn1_sample'
    src = TOOL_DIR / 'ebn1_sample.c'
    if not exe.exists() or exe.stat().st_mtime < src.stat().st_mtime:
        work.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(['cc', '-O2', '-o', str(exe), str(src)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit('sampler build failed: %s' % r.stderr[-2000:])
    return exe


def build_env(args, extra, out_dir, paths, save_at=None, save_name=None,
              load=None, hash_every=1, unpaced=True):
    env = {k: v for k, v in os.environ.items() if not k.startswith('PS2X_')}
    env.update(PS2X_CD_IMAGE=str(paths['iso']), PS2X_SKIP_MOVIE='1',
               PS2X_DETERMINISTIC='1', PS2X_PAD_SCRIPT_CLOCK='vsync',
               PS2X_PAD_SCRIPT='@' + str(paths['pad'].resolve()),
               PS2X_MC_ROOT=str(out_dir / 'mc0'),
               PS2X_MISSING_FUNCTION_POLICY='stop', COPYFILE_DISABLE='1',
               PS2X_GS_BACKEND='external',
               PS2X_GS_EXTERNAL_LIBRARY=str(paths['ge1']),
               GE1_RENDERER='metal',
               GE1_GS_RESOURCES_DIR=str(paths['ge1'].parent / 'resources'),
               GE1_GS_DATA_DIR=str(out_dir / 'ge1-data'),
               PS2X_MICROVU_LIB=str(paths['microvu']),
               PS2X_SOUND='1',
               PS2X_VSYNC_RATE_LOG='1', PS2X_VSYNC_RATE_INTERVAL_S='1',
               PS2X_THREAD_CPU_LOG='1',
               PS2X_DET_HASH_EVERY=str(hash_every),
               PS2X_COVERAGE_TICK='1000000000')
    if unpaced:
        # FP1: unpaced headroom for replays. Mint runs paced (default) so the
        # end save fires: unpaced + external GS never quiesces transfers and
        # the save defers forever ("GS backend transfer active").
        env['PS2X_UNPACED'] = '1'
    env.update(BASE_ENV)
    for kv in extra:
        k, v = kv.split('=', 1)
        env[k] = v
    if save_at:
        if not save_name:
            raise SystemExit('--save-at needs a save file name')
        env['PS2X_SAVESTATE_SAVE_AT'] = str(save_at)
        env['PS2X_SAVESTATE_PATH'] = str((out_dir / save_name).resolve())
        env['PS2X_SAVESTATE_EXIT_AFTER_SAVE'] = '1'
    if load:
        env['PS2X_SAVESTATE_LOAD'] = str(Path(load).resolve())
    return env


def spawn_run(runner, elf, out_dir, env, label, end_tick=0, card=None,
              work=None):
    """Launch one run; return dict with parsed timeline + metrics inputs.

    end_tick: when > 0, terminate the child once a parsed tick >= end_tick
    is observed (replays: the env save would defer forever unpaced, so the
    end det-hash line is the stop signal and the hash record).
    """
    out_dir.mkdir(parents=True)
    (out_dir / 'mc0').mkdir(exist_ok=True)
    (out_dir / 'mc1').mkdir(exist_ok=True)
    (out_dir / 'ge1-data').mkdir(exist_ok=True)
    # Fresh card every run (post-load the state defines RAM; keep pre-load identical).
    # The card is a directory tree (BASLUS-.../...) -- copy it whole.
    if card is not None and card.is_dir():
        for child in (out_dir / 'mc0').iterdir():
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
        for src in card.iterdir():
            dst = out_dir / 'mc0' / src.name
            if src.is_dir():
                shutil.copytree(src, dst)
            elif src.is_file():
                shutil.copy2(src, dst)
        n = sum(1 for _ in (out_dir / 'mc0').rglob('*'))
        if n == 0:
            raise SystemExit('card seed empty: %s' % card)
    log_path = out_dir / 'boot.log'
    sampler_exe = ensure_sampler(work)
    proc = subprocess.Popen([str(runner), str(elf)], cwd=out_dir, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    sampler = subprocess.Popen([str(sampler_exe), str(proc.pid), '0.1'],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True)
    ticks = []      # (tick, wall, combined, count)
    rates = []      # (tick, wall) from [vsync-rate] (progress past the hash cap)
    threadcpu = []  # (tick, wall, gamems, allms)
    saved = []      # (tick, path)
    errors = []
    samp_rows = []  # drained live (pipe buffer would stall the sampler)
    stop = threading.Event()
    reached = [0]

    def samp_reader():
        sout = sampler.stdout
        assert sout is not None
        for line in sout:
            try:
                samp_rows.append(json.loads(line))
            except ValueError:
                pass
            if stop.is_set():
                break

    def reader():
        err = proc.stderr
        assert err is not None
        for raw in err:
                w = time.monotonic() - t0
                try:
                    line = raw.decode('utf-8', 'replace')
                except Exception:
                    continue
                m = DET_RE.search(raw)
                if m:
                    ticks.append((int(m.group(1)), w, m.group(2).decode(),
                                  int(m.group(3))))
                    if end_tick and int(m.group(1)) >= end_tick:
                        reached[0] = int(m.group(1))
                mr = RATE_RE.search(raw)
                if mr:
                    rates.append((int(mr.group(1)), w))
                m2 = THREADCPU_RE.search(line)
                if m2:
                    tot = game = None
                    parts = re.findall(r'(\S+)=(\d+)', m2.group(2))
                    allms = sum(int(v) for _, v in parts)
                    for name, v in parts:
                        if name.startswith('GameThread'):
                            game = int(v)
                    threadcpu.append((int(m2.group(1)), w, game, allms))
                m3 = SAVED_RE.search(line)
                if m3:
                    saved.append((int(m3.group(1)), m3.group(2)))
                if 'FATAL' in line or 'refused' in line:
                    errors.append(line.strip()[-300:])
                if len(raw) + log_size[0] > LOG_CAP_BYTES:
                    pass
                log_f.write(raw)
                log_size[0] += len(raw)
                if stop.is_set():
                    break

    log_size = [0]
    t0 = time.monotonic()
    with open(log_path, 'wb') as log_f:
        th = threading.Thread(target=reader, daemon=True)
        th.start()
        ths = threading.Thread(target=samp_reader, daemon=True)
        ths.start()
        bound, last_tick, last_progress = None, 0, t0
        rc = None
        while True:
            now = time.monotonic()
            rc = proc.poll()
            if ticks and ticks[-1][0] > last_tick:
                last_tick = ticks[-1][0]
                last_progress = now
            if rates and rates[-1][0] > last_tick:
                last_tick = rates[-1][0]
                last_progress = now
            if rc is not None:
                bound = 'exit rc=%d' % rc
                break
            if end_tick and reached[0]:
                bound = 'target tick=%d' % reached[0]
                break
            if now - t0 >= WALL_CAP_S:
                bound = 'wall_cap'
                break
            if log_size[0] > LOG_CAP_BYTES:
                bound = 'log_cap'
                break
            if now - last_progress >= PROGRESS_CAP_S:
                bound = 'progress_cap'
                break
            time.sleep(POLL_S)
        stop.set()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
            bound += '+kill' if bound else 'kill'
        th.join(timeout=10)
    # Sampler exits itself once the child is gone; join the drain thread.
    try:
        sampler.wait(timeout=10)
    except subprocess.TimeoutExpired:
        sampler.kill()
    ths.join(timeout=10)
    return {'bound': bound, 'elapsed_s': round(time.monotonic() - t0, 3),
            'ticks': ticks, 'rates': rates, 'threadcpu': threadcpu,
            'saved': saved, 'errors': errors, 'samples': samp_rows,
            'pid': proc.pid, 'log_bytes': log_size[0],
            'n_hash_lines': len(ticks), 'n_rate_lines': len(rates),
            'n_threadcpu_lines': len(threadcpu),
            'n_samples': len(samp_rows)}


def interp(xs, ys, x):
    """Linear interpolate; xs ascending."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    import bisect
    i = bisect.bisect_right(xs, x)
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def game_cpu_at(samples, wall):
    """Cumulative GameThread user+sys ns at wall (match by pthread name)."""
    xs, ys = [], []
    for r in samples:
        tot = None
        for t in r.get('threads', []):
            if t.get('name') == 'GameThread':
                tot = (t.get('user_ns') or 0) + (t.get('sys_ns') or 0)
                break
        if tot is None:
            continue
        xs.append(r['wall_s'] + 0.0)
        ys.append(float(tot))
    if len(xs) < 2:
        return None
    # Sampler wall_s is sampler-relative; align by run start offset below.
    return xs, ys


def measure(run, t_load, t_end, label):
    """Metrics over tick window [t_load, t_end]; returns dict."""
    tick_wall = {}
    for tick, w, combined, count in run['ticks']:
        tick_wall[tick] = w  # last arrival wins (ticks ascend)
    diag = {'n_hash': len(run['ticks']), 'n_samples': run.get('n_samples', 0),
            'n_threadcpu': run.get('n_threadcpu_lines', 0)}
    if run['samples']:
        diag['samp_wall_first'] = run['samples'][0]['wall_s']
        diag['samp_wall_last'] = run['samples'][-1]['wall_s']
        diag['smap_ins_first'] = run['samples'][0].get('ri_instructions')
        diag['smap_ins_last'] = run['samples'][-1].get('ri_instructions')
        names = sorted(set(t.get('name', '') for r in run['samples']
                           for t in r.get('threads', [])))
        diag['thread_names'] = names
    if t_load not in tick_wall or t_end not in tick_wall:
        diag.update(ok=False, label=label,
                    why='window ticks missing (have %d hash lines, ticks %s..%s)'
                    % (len(run['ticks']),
                       run['ticks'][0][0] if run['ticks'] else '?',
                       run['ticks'][-1][0] if run['ticks'] else '?'))
        return diag
    w0, w1 = tick_wall[t_load], tick_wall[t_end]
    n = t_end - t_load
    diag.update(t_load=t_load, t_end=t_end, w0=w0, w1=w1)
    # Sampler clock starts at sampler spawn, reader clock at pre-spawn; both
    # monotonic, offset <= ~50 ms and constant, so evaluating the sampler
    # series at (w0, w1) directly errs only by rate_variation * offset << 0.1%.
    sxs = [r['wall_s'] for r in run['samples']]
    ins = [float(r.get('ri_instructions') or 0) for r in run['samples']]
    cyc = [float(r.get('ri_cycles') or 0) for r in run['samples']]
    gt = game_cpu_at(run['samples'], 0)
    if len(sxs) < 2 or gt is None:
        diag.update(ok=False, label=label, why='sampler rows missing')
        return diag
    gxs, gys = gt
    diag['gt_first'] = gys[0]
    diag['gt_last'] = gys[-1]
    ins0, ins1 = interp(sxs, ins, w0), interp(sxs, ins, w1)
    cyc0, cyc1 = interp(sxs, cyc, w0), interp(sxs, cyc, w1)
    g0, g1 = interp(gxs, gys, w0), interp(gxs, gys, w1)
    end_hash = [c for t, w, c, _ in run['ticks'] if t == t_end]
    full_seq = [(t, c) for t, w, c, _ in run['ticks']
                if t_load <= t <= t_end]
    diag.update(label=label, ok=True, n_ticks=n,
                gthread_ms_per_upd=(g1 - g0) / 1e6 / n,
                ins_per_upd=(ins1 - ins0) / n,
                cyc_per_upd=(cyc1 - cyc0) / n,
                end_hash=end_hash[0] if end_hash else None,
                seq=full_seq, gthread_ms_total=(g1 - g0) / 1e6,
                elapsed_s=run['elapsed_s'], bound=run['bound'])
    return diag


def cmd_mint(args):
    runner = Path(args.runner).resolve()
    paths = resolve_paths(args)
    verify_pins(runner, paths, args)
    out = Path(args.out).resolve()
    if out.exists():
        raise SystemExit('refusing to reuse %s' % out)
    slot = hold_lease('ebn1-mint')
    try:
        others = runner_pids(('ps2EntryRunner', Path(runner).name))
        if others:
            print('INFRA-BLOCKED: mint refused: other ps2EntryRunner pids %s alive'
                  % others, flush=True)
            return 3
        env = build_env(args, args.env, out, paths, save_at=args.save_at,
                        save_name='state-T%d.bin' % args.save_at, hash_every=100,
                        unpaced=args.unpaced)
        run = spawn_run(runner, paths['elf'], out, env, 'mint',
                        card=paths['card'], work=paths['work'])
        print(json.dumps({k: v for k, v in run.items()
                          if k not in ('ticks', 'threadcpu', 'samples')},
                         indent=2))
        (out / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if k != 'samples'}, indent=2) + '\n')
        if not run['saved']:
            print('MINT-FAIL: no state saved', flush=True)
            return 1
        print('MINT-OK saved_tick=%d' % run['saved'][-1][0], flush=True)
        return 0
    finally:
        release(slot)


def cmd_run(args):
    runner = Path(args.runner).resolve()
    paths = resolve_paths(args)
    verify_pins(runner, paths, args)
    out = Path(args.out).resolve()
    if out.exists():
        raise SystemExit('refusing to reuse %s' % out)
    slot = hold_lease('ebn1-run')
    try:
        # Replays stop by watching for the end tick: the env save would defer
        # forever unpaced (GS transfers never quiesce), so no save is armed.
        env = build_env(args, args.env, out, paths, load=args.state,
                        hash_every=1)
        run = spawn_run(runner, paths['elf'], out, env, 'run',
                        end_tick=args.save_at, card=paths['card'],
                        work=paths['work'])
        load_tick = args.load_tick
        t0 = load_tick + SKIP_TICKS
        # End = the watched tick actually observed (else last hash tick).
        t1 = args.save_at
        if t1 not in [t for t, _, _, _ in run['ticks']]:
            t1 = run['ticks'][-1][0] if run['ticks'] else None
        m = measure(run, t0, t1, 'run') if t1 else {'ok': False}
        with open(out / 'samples.jsonl', 'w') as f:
            for r in run['samples']:
                f.write(json.dumps(r) + '\n')
        print(json.dumps(m if not m.get('seq') else
                         {k: v for k, v in m.items() if k != 'seq'}, indent=2))
        (out / 'metrics.json').write_text(json.dumps(m, indent=2) + '\n')
        return 0 if m.get('ok') else 1
    finally:
        release(slot)


def median(xs):
    s = sorted(xs)
    n = len(s)
    return (s[n // 2] + s[(n - 1) // 2]) / 2 if n else float('nan')


def mad(xs):
    m = median(xs)
    return median([abs(x - m) for x in xs])


def boot_one(runner, out, pad, env_extra, state, save_at, load_tick, label,
             paths):
    e = build_env(argparse.Namespace(pad=pad, env=[]), env_extra, out, paths,
                  load=state, hash_every=1)
    run = spawn_run(runner, paths['elf'], out, e, label, end_tick=save_at,
                    card=paths['card'], work=paths['work'])
    t1 = save_at
    if t1 not in [t for t, _, _, _ in run['ticks']]:
        t1 = run['ticks'][-1][0] if run['ticks'] else None
    m = measure(run, load_tick + SKIP_TICKS, t1, label) if t1 else {'ok': False}
    (out / 'metrics.json').write_text(json.dumps(m, indent=2) + '\n')
    with open(out / 'samples.jsonl', 'w') as f:
        for r in run['samples']:
            f.write(json.dumps(r) + '\n')
    return m


def cmd_ab(args):
    runner = Path(args.runner).resolve()
    paths = resolve_paths(args)
    shas = verify_pins(runner, paths, args)
    out = Path(args.out).resolve()
    if out.exists():
        raise SystemExit('refusing to reuse %s' % out)
    out.mkdir(parents=True)
    (out / 'shas.json').write_text(json.dumps(
        {'runner': str(runner), 'sha': shas[str(runner)]}, indent=2) + '\n')
    a_env = args.a.split(',') if args.a else []
    b_env = args.b.split(',') if args.b else []
    slot = hold_lease('ebn1-ab')
    try:
        others = runner_pids(('ps2EntryRunner', Path(runner).name))
        if others:
            print('INFRA-BLOCKED: ab refused: other ps2EntryRunner pids %s alive'
                  % others, flush=True)
            return 3
        results = []
        t_start = time.monotonic()
        for w in range(args.warmup):
            leg = out / ('warm%d' % w)
            mw = boot_one(runner, leg, args.pad, a_env, args.state,
                          args.save_at, args.load_tick, 'warm%d' % w, paths)
            print(json.dumps({k: v for k, v in mw.items() if k != 'seq'}),
                  flush=True)
        for i in range(args.runs):
            # Alternate starting arm per pair to balance drift.
            order = [('A', a_env), ('B', b_env)] if i % 2 == 0 else \
                [('B', b_env), ('A', a_env)]
            for arm, env_extra in order:
                leg = out / ('pair%d-%s' % (i, arm))
                m = {'arm': arm, 'pair': i,
                     **boot_one(runner, leg, args.pad, env_extra, args.state,
                                args.save_at, args.load_tick,
                                'pair%d-%s' % (i, arm), paths)}
                results.append(m)
                print(json.dumps({k: v for k, v in m.items() if k != 'seq'}),
                      flush=True)
                if not m.get('ok'):
                    print('AB-FAIL leg pair%d-%s' % (i, arm), flush=True)
                    (out / 'results.json').write_text(
                        json.dumps(results, indent=2) + '\n')
                    return 1
        elapsed = time.monotonic() - t_start
        table = summarize(results)
        table['elapsed_s'] = round(elapsed, 1)
        table['runs'] = args.runs
        table['warmup'] = args.warmup
        (out / 'results.json').write_text(json.dumps(
            {'legs': [{k: v for k, v in m.items() if k != 'seq'}
                       for m in results],
             'arms': {'A': a_env, 'B': b_env},
             'state': str(Path(args.state).resolve()),
             'save_at': args.save_at, 'load_tick': args.load_tick,
             'table': table}, indent=2) + '\n')
        print(json.dumps(table, indent=2))
        return 0
    finally:
        release(slot)


def summarize(results):
    tab = {}
    for metric in ('gthread_ms_per_upd', 'ins_per_upd', 'cyc_per_upd'):
        a = [m[metric] for m in results if m['arm'] == 'A']
        b = [m[metric] for m in results if m['arm'] == 'B']
        ma, mb = median(a), median(b)
        delta = (mb - ma) / ma if ma else float('nan')
        lo, hi = bootstrap_ci(a, b)
        tab[metric] = {'A_median': ma, 'B_median': mb,
                       'A_mad': mad(a), 'B_mad': mad(b),
                       'A_cv': (mad(a) / ma if ma else float('nan')),
                       'B_cv': (mad(b) / mb if mb else float('nan')),
                       'delta_frac': delta, 'ci95': [lo, hi],
                       'A': a, 'B': b}
    hashes = sorted(set(m.get('end_hash') for m in results))
    tab['end_hash_identical'] = len(hashes) == 1
    tab['end_hashes'] = hashes
    seqs = set(tuple(c for _, c in m.get('seq', [])) for m in results)
    tab['full_seq_identical'] = len(seqs) == 1
    return tab


def bootstrap_ci(a, b, iters=10000):
    rng = random.Random(12345)
    ma = median(a)
    if not ma or math.isnan(ma):
        return (float('nan'), float('nan'))
    ds = []
    for _ in range(iters):
        sa = [a[rng.randrange(len(a))] for _ in a]
        sb = [b[rng.randrange(len(b))] for _ in b]
        mb, ma2 = median(sb), median(sa)
        ds.append((mb - ma2) / ma2 if ma2 else float('nan'))
    ds = sorted(d for d in ds if not math.isnan(d))
    if not ds:
        return (float('nan'), float('nan'))
    return (ds[int(0.025 * len(ds))], ds[int(0.975 * (len(ds) - 1))])


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('mint', 'run', 'ab'):
        p = sub.add_parser(name)
        p.add_argument('--runner', required=True)
        p.add_argument('--out', required=True)
        p.add_argument('--pad', required=True,
                       help='pad-script file (recordings are yours, never shipped)')
        p.add_argument('--iso', default=None, help='game disc image')
        p.add_argument('--elf', default=None, help='boot ELF from your own disc')
        p.add_argument('--ge1-lib', default=None, help='external GS backend dylib')
        p.add_argument('--microvu-lib', default=None, help='microVU dylib')
        p.add_argument('--pinned', default=None,
                       help='pinned-inputs base; fills --iso/--elf/--ge1-lib/'
                            '--microvu-lib from the standard layout')
        p.add_argument('--iso-sha', default=ISO_SHA)
        p.add_argument('--elf-sha', default=ELF_SHA)
        p.add_argument('--ge1-sha', default=GE1_LIB_SHA)
        p.add_argument('--card', default=None,
                       help='seed memory-card dir (copied fresh per run)')
        p.add_argument('--work', default='~/ssx3-work/eebench',
                       help='sampler build dir (default ~/ssx3-work/eebench)')
        p.add_argument('--env', action='append', default=[])
        if name == 'mint':
            p.add_argument('--unpaced', action='store_true',
                           help='mint unpaced (default paced: the end save '
                                'only fires when GS transfers quiesce)')
        if name in ('mint', 'run', 'ab'):
            p.add_argument('--save-at', type=int, required=True)
        if name in ('run', 'ab'):
            p.add_argument('--state', required=True)
            p.add_argument('--load-tick', type=int, required=True)
        if name == 'ab':
            p.add_argument('--runs', type=int, required=True)
            p.add_argument('--a', default='')
            p.add_argument('--b', default='')
            p.add_argument('--warmup', type=int, default=1,
                           help='unrecorded warm-up legs before the set')
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, lambda sg, fr: sys.exit(128 + sg))
    if args.cmd == 'mint':
        return cmd_mint(args)
    if args.cmd == 'run':
        return cmd_run(args)
    return cmd_ab(args)


if __name__ == '__main__':
    sys.exit(main())
