#!/usr/bin/env python3
"""odin_run.py — one canonical Odin leg (OB1).

Replaces the ~47 per-lane copies of run1.sh/launch.py. One leg: cool-down
(screen|final), lease, keyguard + AC + battery checks, install +
installed-SHA check, env overlay on the play env, I26-FAST unpaced run to a
stop tick with the full sampler set, force-stop, play-state restore,
receipts + summary in one dir.

Usage:
    python3 local/tooling/odin_run.py --label OB1-A1 --out local/research/OB1/logs/OB1-A1 \\
        --apk ~/dev/ssx3-work/odin-play/app-release.apk --apk-sha 24c01ee1... \\
        [--mode screen|final] [--env K=V ...]

Offline re-summary of any existing leg dir (no device; same code the batch
collator reads):
    python3 local/tooling/odin_run.py --summarize-only <leg dir>

Provenance (best current version of each piece, per the OB1 brief):
  base driver + samplers  local/research/TN1/launch.py (newest lineage: TN1 =
                          F9 + ps2x-game present-layer hitch sampler)
  cooldown-before-claim + lease-race retry  local/research/F9/run1.sh
  schedstat three-way (run/runnable/blocked)  TN1 write_sched (CP2/F9 method)
  present-latency stitching  local/research/TN1/presentstat.py
  gpu_clock_stats residency sampling  local/research/CP2/gpu_residency.py
  devfreq/kgsl one-shots  local/research/GG1/run-gg1.sh (device.txt)
  Granite logcat tag  local/tooling/odin_profile.py
  race-rate / per-thread row  local/research/F9/legsum.py + ticks.py
  phases table  local/research/F9/phases.py (= TN1/FS2/F8 phases.py)
  lease / cooldown / restore  local/tooling/odin_lease.sh,
    odin_cooldown.py, odin_restore_play.sh (shell out, not copied)
  fast present poller (CP3): the 2 s main-loop polls overrun the 127-frame
    SF ring whenever a loop iteration runs long (screencap, thermal+dumpsys),
    forging 1-3 s stitching "stalls". A background thread polls --latency
    every --fast-poll seconds into present-latency-fast.txt (preferred by
    the summary); the main-loop file is kept as a control.

Fixes vs the copies: the lease label is a flag (TN1 gap: copies hardcode
their lane's label, e.g. TN1's T1-void died on the F8 label); OUT/scratch
dirs are flags (GG1 had to mv another lane's dir); task_sched has a crash
guard (TN1 P5 gap); env overlays the play env file instead of replicating
its knobs per leg (replication drifts when the play env changes).
"""
import argparse
import atexit
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import defaultdict

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(TOOL_DIR))  # local/tooling -> repo root
HOME = os.path.expanduser('~')
ODIN_SERIAL_FILE = os.path.join(REPO, 'local/odin-serial')
PKG = 'com.ps2x.runner'
FILES = f'/storage/emulated/0/Android/data/{PKG}/files'
DSCRAP = '/data/local/tmp/odinrun'
LEASESH = os.path.join(TOOL_DIR, 'odin_lease.sh')
COOLDOWN = os.path.join(TOOL_DIR, 'odin_cooldown.py')
RESTORE = os.path.join(TOOL_DIR, 'odin_restore_play.sh')
PLAYDIR = os.path.join(HOME, 'dev/ssx3-work/odin-play')
PLAY_ENV = os.path.join(PLAYDIR, 'ps2x.env')
PLAY_SUMS = os.path.join(PLAYDIR, 'SHA256SUMS')
SCRATCH_ROOT = os.path.join(HOME, 'dev/ssx3-work/odinrun')

# I31 odin-verify.txt full SHAs (read-only intactness check, never written).
MC0_PINS = {
    'BASLUS-20772-GAM0001/BASLUS-20772-GAM0001': '4bdaee79a3bdaceef898bb44b6bcf62058e882a24237f8c81e46953a5067b78e',
    'BASLUS-20772-GAM0001/icon.sys': 'eab225745ef6695109743410261609e0569c14edb1555bc8941a965b3890a49c',
    'BASLUS-20772-GAM0001/ssx1.ico': '5f8b5a9252f868d2fbc03378846b84ed601a2c872f47028711de67a4a714fc0b',
    'BASLUS-20772-SET0001/BASLUS-20772-SET0001': '4a31a2d7e095e277edceae2243002055aa830019b3a1be64f126aee9129002f1',
    'BASLUS-20772-SET0001/icon.sys': 'dddf2d9c81a1c8bac2fe2036e1771dfa63635e3ae676760551defec2da67ee13',
    'BASLUS-20772-SET0001/ssx1.ico': '5f8b5a9252f868d2fbc03378846b84ed601a2c872f47028711de67a4a714fc0b',
}
# I26-FAST pad route (vsync clock; race HUD at tick ~1714). local/research/I26/ROUTES.md
ROUTE = (
    '10611:start:250,12780:cross:250,14749:cross:250,16567:cross:250,18336:cross:250,19770:cross:250,21205:down:150,21706:down:150,22306:cross:250,24025:cross:250,28512:cross:200,28763:down:700,29513:cross:200,29764:down:700,30514:cross:200,30765:down:700,31515:cross:200,31766:down:700,32516:cross:200,32767:down:700,33517:cross:200,33768:down:700,34518:cross:200,34769:down:700,35519:cross:200,35770:down:700,36520:cross:200,36771:down:700,37521:cross:200,37772:down:700,38522:down:30000'
)
# Phase tick boundaries (phases.py / I26-FAST anchors).
PHASES = [('title/startup', 0, 636), ('main menu', 636, 766),
          ('Select Character', 766, 884), ('Setup Character / Peak', 884, 1099),
          ('Select Mode / Event / My Rules', 1099, 1440),
          ('loading + Rival card', 1440, 1714), ('race', 1714, 10 ** 9)]
RACE_TICK = 1714
# legsum.py / ticks.py fixed windows for cross-leg comparison.
FIXED_WINDOWS = [(1714, 2400), (2400, 3000), (3000, 4000), (4000, 4500), (1714, 4500)]

VSYNC_RE = re.compile(r'^\s*(\d+\.\d+).*\[vsync-rate\] tick=(\d+)')
COMPARE_RE = re.compile(r'\[present-vk\] compare tick=(\d+).*?diff_px=(\d+)')
MTVU_RE = re.compile(r'\[mtvu\] jobs=(\d+) violations=(\d+)')
# PT2 per-second per-stage tails (ps2_perf_log.h formatStageLine).
STAGE_LINE_RE = re.compile(
    r'\[perf-stage\] tick0=(\d+) tick1=(\d+) stage=([\w.]+) n=(\d+) '
    r'mean=([-\d.]+) p50=([-\d.]+) p95=([-\d.]+) p99=([-\d.]+) max=([-\d.]+) hist="([^"]*)"')
STAGES = ('ee.busy', 'ee.cpu', 'ee.wait', 'gs.busy', 'mtvu.busy', 'gpu.busy', 'gsback.busy')
HIST_BUCKET_MS = 0.25
HIST_MAX_MS = 20.0
# Guest frame at 59.94 Hz. The runbook quotes Odin stage times as util% x this.
MS_PER_60FPS_FRAME = 16.68


# ---------------------------------------------------------------------------
# Pure analysis (device-free; also used by --summarize-only and odin_batch.py)
# ---------------------------------------------------------------------------

# DSP3 item 2: SF latch detector. latched= (distinct SF latch timestamps per
# ~1 s perf window) drops to ~60/s when SF's vsync tracker is stuck at 60
# (DSP2); a healthy 120 leg reads >= 118. The --latency ring cross-check uses
# DSP2's latscan.py rule (> 30% of within-dump intervals < 12.4 ms = 120).
DSP3_SHORT_MS = 12.4
DSP3_ENGAGED_VS = 100.0  # [perf] vsyncs_per_s at/above this = events-engaged window
DSP3_LATCH_FAIL = 110    # median latched below this on a posting 120 leg = stuck
DSP3_POSTS_MIN = 115     # per-window presents needed for the window to count
DSP3_RING_HEALTHY = 0.30
DSP3_MIN_WINDOWS = 3


def parse_perf_present_windows(perf_dir):
    """[(tick, vsyncs_per_s, presents, latched|None)] from perf/*.log.

    Pairs each [perf-present] line with its window's [perf] vsyncs_per_s by
    tick. Pre-DSP3 APKs (no latched=) yield latched=None."""
    vs_by_tick = {}
    rows = []
    try:
        names = sorted(os.listdir(perf_dir))
    except OSError:
        return []
    for name in names:
        if not name.endswith('.log'):
            continue
        try:
            with open(os.path.join(perf_dir, name), errors='replace') as f:
                text = f.read()
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith('[perf] ') and 'tick=' in line:
                m = re.search(r'tick=(\d+) vsyncs_per_s=([\d.]+)', line)
                if m:
                    vs_by_tick[int(m.group(1))] = float(m.group(2))
            elif line.startswith('[perf-present]'):
                m = re.search(r'tick=(\d+)', line)
                p = re.search(r'presents=(\d+)', line)
                lt = re.search(r'latched=(\d+)', line)
                if m and p:
                    rows.append((int(m.group(1)), int(p.group(1)),
                                 int(lt.group(1)) if lt else None))
    return [(t, vs_by_tick.get(t, -1.0), pr, la) for t, pr, la in rows]


def ring_short_frac(present_latency_text):
    """(n, frac<12.4ms|None): DSP2 latscan.py over within-dump actual-present
    intervals. (0, None) when fewer than 50 intervals."""
    ints = []
    prev = None
    for line in (present_latency_text or '').splitlines():
        f = line.split('\t')
        if line.startswith('POLL') or len(f) != 3:
            prev = None
            continue
        try:
            a = int(f[1])
        except ValueError:
            prev = None
            continue
        if a <= 0 or a >= PENDING_TS:
            prev = None
            continue
        if prev is not None and a > prev:
            ints.append((a - prev) / 1e6)
        prev = a
    if len(ints) < 50:
        return 0, None
    return len(ints), sum(1 for x in ints if x < DSP3_SHORT_MS) / len(ints)


def sf_latch_summary(perf_dir, present_latency_text):
    """DSP3 item 2 inputs: engaged-window latched stats + ring cross-check."""
    rows = parse_perf_present_windows(perf_dir)
    engaged = [r for r in rows if r[1] >= DSP3_ENGAGED_VS and r[3] is not None]
    qual = [r for r in engaged if r[2] >= DSP3_POSTS_MIN]
    lat = sorted(r[3] for r in qual)
    prs = sorted(r[2] for r in qual)
    n_ring, frac = ring_short_frac(present_latency_text)
    return {
        'present_windows': len(rows),
        'latched_windows': sum(1 for r in rows if r[3] is not None),
        'engaged_windows': len(engaged),
        'qual_windows': len(qual),
        'latched_median': lat[len(lat) // 2] if lat else None,
        'presents_median': prs[len(prs) // 2] if prs else None,
        'ring_intervals': n_ring,
        'ring_frac_short': round(frac, 3) if frac is not None else None,
    }


def is_120_leg(meta):
    """A leg targeting 120: the 120 launcher alias or a full120 preset."""
    if 'Launch120' in str(meta.get('activity', '')):
        return True
    return any(e.split('=', 1) == ['PS2X_SSX3_FULL120', 'events']
               for e in meta.get('env', []) if '=' in e)


def parse_vsync_pts(logcat_text):
    """[(wall_epoch, tick)] from [vsync-rate] lines (phases.py / legsum.py method)."""
    pts = []
    for line in logcat_text.splitlines():
        m = VSYNC_RE.search(line)
        if m:
            pts.append((float(m.group(1)), int(m.group(2))))
    return pts


def wall_at(pts, tick):
    """Interpolated wall epoch for a guest tick (None if outside samples)."""
    for (w0, t0), (w1, t1) in zip(pts, pts[1:]):
        if t0 <= tick <= t1 and t1 > t0:
            return w0 + (w1 - w0) * (tick - t0) / (t1 - t0)
    return None


def window_rate(pts, a, b):
    """(vs/s, wall_s) over identical ticks a->b, or (None, None) if uncovered."""
    wa, wb = wall_at(pts, a), wall_at(pts, b)
    if wa is None or wb is None or wb <= wa:
        return None, None
    return (b - a) / (wb - wa), wb - wa


def per_5s_buckets(pts, lo=RACE_TICK):
    """Guest vs/s per [vsync-rate] interval at/after tick lo (phases.py tail)."""
    out = []
    for (w0, t0), (w1, t1) in zip(pts, pts[1:]):
        if t0 >= lo and w1 > w0:
            out.append(round((t1 - t0) / (w1 - w0), 1))
    return out


def hitch_phase(tick):
    """presentstat.py phases."""
    if tick < 636:
        return 'title'
    if tick < 1440:
        return 'menus'
    if tick < 1714:
        return 'loading'
    return 'race'


PENDING_TS = 9223372036854775807  # SF Fence::SIGNAL_TIME_PENDING: produced, not presented


def present_hitches(present_latency_text):
    """Per-present interval stats per phase (TN1 presentstat.py method).

    Stitches overlapping 2 s --latency POLLs on the present layer into one
    actual_present_time series, deduped. Returns {phase: stats} + ALL.
    CP3: pending-fence rows (INT64_MAX, not presents) are dropped, and the
    return adds within-dump (overrun-immune) stats + poll-boundary jumps so
    sampler overrun can't forge stalls: a stitched hole far above the
    within-dump max with a matching slow poll iteration is the sampler.
    """
    polls = []
    tick, cur = 0, None
    for raw in present_latency_text.splitlines():
        line = raw.rstrip('\n')
        if line.startswith('POLL'):
            parts = dict(p.split('=') for p in line.split()[1:] if '=' in p)
            try:
                tick = int(parts.get('tick', tick))
            except ValueError:
                pass
            cur = []
            polls.append((tick, cur))
        elif cur is not None and '\t' in line:
            p = line.split('\t')
            if len(p) >= 2 and p[1].strip().isdigit():
                ns = int(p[1])
                if 0 < ns < PENDING_TS:
                    cur.append((ns, tick))
    within, jumps, prev_last = [], [], None
    for ptick, c in polls:
        xs = sorted(ns for ns, _ in c)
        for a2, b2 in zip(xs, xs[1:]):
            dt = (b2 - a2) / 1e6
            if 0 < dt <= 10000:
                within.append((dt, hitch_phase(ptick)))
        if xs:
            if prev_last is not None:
                jumps.append((xs[0] - prev_last) / 1e6)
            prev_last = xs[-1]
    pts = sorted({ns: tick for _, c in polls for ns, tick in c}.items())
    if len(pts) < 2:
        return {'presents': len(pts), 'intervals': 0, 'by_phase': {},
                'npolls': len(polls), 'within_by_phase': {},
                'top_boundary_jumps_ms': []}
    ivs = []
    for (t0, _), (t1, tick1) in zip(pts, pts[1:]):
        dt = (t1 - t0) / 1e6
        if 0 < dt <= 10000:
            ivs.append((dt, hitch_phase(tick1)))

    def tabulate(pairs):
        tab = {}
        for ph in ('title', 'menus', 'loading', 'race', 'ALL'):
            xs = sorted(d for d, p in pairs if ph == 'ALL' or p == ph)
            if not xs:
                tab[ph] = {'n': 0}
                continue
            tab[ph] = {
                'n': len(xs), 'mean_ms': round(sum(xs) / len(xs), 1),
                'p99_ms': round(xs[min(len(xs) - 1, math.ceil(0.99 * len(xs)) - 1)], 1),
                'max_ms': round(max(xs), 1),
                'gt50': sum(1 for x in xs if x > 50),
                'gt100': sum(1 for x in xs if x > 100),
            }
        return tab

    return {'presents': len(pts), 'intervals': len(ivs),
            'by_phase': tabulate(ivs), 'npolls': len(polls),
            'within_by_phase': tabulate(within),
            'top_boundary_jumps_ms': sorted((j for j in jumps if j > 0), reverse=True)[:5]}


def gpu_summary(gpubusy_text, race_tick=RACE_TICK):
    """Mean kgsl busy% over race-tick samples (+ overall). gpubusy.txt lines:
    t=.. tick=.. busy=.. total=.. pct=.. gpuclk=.."""
    all_p, race_p, clks = [], [], defaultdict(int)
    n = 0
    for line in gpubusy_text.splitlines():
        m = re.search(r'tick=(\d+).*?pct=([\d.]+)', line)
        if not m:
            continue
        n += 1
        tick, pct = int(m.group(1)), float(m.group(2))
        if pct < 0:  # empty window marker from the sampler
            continue
        all_p.append(pct)
        if tick >= race_tick:
            race_p.append(pct)
        mc = re.search(r'gpuclk=(\S+)', line)
        if mc:
            clks[mc.group(1)] += 1
    def stats(xs):
        return {'n': len(xs), 'mean_pct': round(sum(xs) / len(xs), 1) if xs else None,
                'max_pct': round(max(xs), 1) if xs else None}
    top_clk = max(clks, key=clks.get) if clks else None
    return {'samples': n, 'all': stats(all_p), 'race': stats(race_p),
            'top_gpuclk': top_clk}


def parse_stage_lines(text):
    """PT2 [perf-stage] lines -> list of dicts (hist as {edge: count}, ovf separate)."""
    out = []
    for m in STAGE_LINE_RE.finditer(text):
        t0, t1, stage, n = int(m.group(1)), int(m.group(2)), m.group(3), int(m.group(4))
        hist, ovf = {}, 0
        for pair in m.group(10).split():
            edge, _, count = pair.partition(':')
            if not count.isdigit():
                continue
            if edge == 'ovf':
                ovf += int(count)
            else:
                try:
                    hist[float(edge)] = hist.get(float(edge), 0) + int(count)
                except ValueError:
                    pass
        out.append({'tick0': t0, 'tick1': t1, 'stage': stage, 'n': n,
                    'mean': float(m.group(5)), 'p50': float(m.group(6)),
                    'p95': float(m.group(7)), 'p99': float(m.group(8)),
                    'max': float(m.group(9)), 'hist': hist, 'ovf': ovf})
    return out


def tails_summary(stage_lines, win):
    """PT2: merge per-second stage hists over tick window [a, b] (exact max).

    Lines whose [tick0, tick1] overlaps the window merge whole (1 s
    granularity at the edges). Quantiles come from the merged 0.25 ms
    histogram (bucket midpoints, +/-0.125 ms); max is the max of line maxes
    (exact). A quantile rank landing in overflow reads the window max.
    Returns None when no stage line overlaps the window.
    """
    if not win:
        return None
    a, b = win
    per = {}
    for ln in stage_lines:
        if ln['n'] <= 0 or ln['tick1'] < a or ln['tick0'] > b:
            continue
        d = per.setdefault(ln['stage'], {'n': 0, 'max': -1.0, 'hist': {}, 'ovf': 0, 'lines': 0})
        d['n'] += ln['n']
        d['max'] = max(d['max'], ln['max'])
        d['lines'] += 1
        for edge, c in ln['hist'].items():
            d['hist'][edge] = d['hist'].get(edge, 0) + c
        d['ovf'] += ln['ovf']
    if not per:
        return None

    def quantile(hist, ovf, n, q, winmax):
        rank = math.ceil(q * n)
        acc = 0
        for edge in sorted(hist):
            acc += hist[edge]
            if acc >= rank:
                return round(edge + HIST_BUCKET_MS / 2, 3)
        return round(winmax, 3)  # rank in overflow (or empty): the exact max

    stages = {}
    for name, d in per.items():
        stages[name] = {'n': d['n'], 'lines': d['lines'],
                        'p50': quantile(d['hist'], d['ovf'], d['n'], 0.50, d['max']),
                        'p95': quantile(d['hist'], d['ovf'], d['n'], 0.95, d['max']),
                        'p99': quantile(d['hist'], d['ovf'], d['n'], 0.99, d['max']),
                        'max': round(d['max'], 3)}
    return {'window': [a, b], 'stages': stages}


def sched_summary(schedstat_text, pts=None):
    """legsum.py sched(): MTVU/GameThread + GS worker (most timeslices).

    OB5/PT2 frame-span fix: the header's tick span is quantized to 5 s
    vsync-rate lines while its wall comes from fresh polls, so the nominal
    frames misstate the span by the staleness (OB5 SP1: ~8 % high on 600-frame
    windows). Recompute frames as wall_s x the logcat rate over the window and
    rescale the per-thread ms to it (blk recomputed from the fixed frame
    length). ms60 = util% x 16.68 ms is the runbook's quoted stage time
    (rate-free; exact match at 60.0 vs/s). Without usable pts the nominal
    frames stand (frames_fixed == frames); ms60 needs only util.
    """
    rows = schedstat_text.splitlines()
    if len(rows) < 3:
        return None
    m = re.search(r'\((\d+) frames\)', rows[0])
    frames = int(m.group(1)) if m else 0
    wm = re.search(r'window ticks (\d+)->(\d+)', rows[0])
    win = [int(wm.group(1)), int(wm.group(2))] if wm else None
    wallm = re.search(r'wall ([\d.]+) s', rows[0])
    wall = float(wallm.group(1)) if wallm else 0.0
    vs, frames_fixed = None, frames
    if pts and win and wall > 0 and frames > 0:
        vs, _ = window_rate(pts, win[0], win[1])
        if vs and vs > 0:
            frames_fixed = round(wall * vs, 1)
    k = frames / frames_fixed if frames_fixed > 0 else 1.0
    th, gs = {}, []
    for r in rows[2:]:
        f = r.split()
        if len(f) < 9:
            continue
        comm = ' '.join(f[1:-7])
        run_raw, wait_raw = float(f[-6]), float(f[-5])
        v = {'run': run_raw * k, 'wait': wait_raw * k,
             'proc': float(f[-3]) * k, 'slices': int(f[-2]), 'cpu': f[-7],
             'util': float(f[-1]), 'run_raw': run_raw}
        if wall > 0 and frames_fixed > 0:
            v['blk'] = 1000 * wall / frames_fixed - v['run'] - v['wait']
        else:
            v['blk'] = float(f[-4])
        v['ms60'] = round(v['util'] / 100 * MS_PER_60FPS_FRAME, 2)
        if comm == 'GsWorker':
            gs.append(v)
        elif comm in ('MTVU', 'GameThread'):
            th[comm] = v
    if 'MTVU' not in th or 'GameThread' not in th or not gs:
        return None
    th['GS'] = max(gs, key=lambda v: v['slices'])
    th['GS_rows'] = len(gs)
    return {'frames': frames, 'frames_fixed': frames_fixed, 'wall_s': wall,
            'vs_per_s': round(vs, 2) if vs else None, 'window': win, 'threads': th}


def phases_summary(logcat_text, sf_latency_text, t0):
    """phases.py table as data: per-phase guest vs/s + SF presents/s."""
    pts = [(0.0, 0)] + [(w - t0, t) for w, t in parse_vsync_pts(logcat_text)]
    polls, cur = [], None
    for line in sf_latency_text.splitlines():
        if line.startswith('POLL'):
            m = re.match(r'POLL t=([\d.]+) tick=(\d+)', line)
            if m:
                cur = [float(m.group(1)), int(m.group(2)), set()]
                polls.append(cur)
        elif cur is not None and '\t' in line:
            try:
                v = int(line.split('\t')[1])
                if 0 < v < 2 ** 62:
                    cur[2].add(v)
            except (ValueError, IndexError):
                pass

    def wat(tick):
        for (w0, t0_), (w1, t1) in zip(pts, pts[1:]):
            if t0_ <= tick <= t1 and t1 > t0_:
                return w0 + (w1 - w0) * (tick - t0) / (t1 - t0)
        return None

    last_tick = pts[-1][1]
    out = []
    for name, a, b in PHASES:
        b = min(b, last_tick)
        wa, wb = wat(a), wat(b)
        if wa is None or wb is None or wb <= wa:
            continue
        r = (b - a) / (wb - wa)
        pr = []
        for pw, pt, ts in polls:
            if a <= pt < b and len(ts) > 2:
                s = sorted(ts)
                span = (s[-1] - s[0]) / 1e9
                if span > 0:
                    pr.append((len(s) - 1) / span)
        out.append({'phase': name, 'ticks': [a, b], 'wall_s': round(wb - wa, 1),
                    'vs_per_s': round(r, 2), 'ratio': round(r / 59.94, 3),
                    'sf_presents_per_s': round(sum(pr) / len(pr), 1) if pr else None,
                    'sf_n': len(pr)})
    return out


def summarize_leg(outdir, extra_meta=None):
    """Build summary.json content from a leg's receipts (host-side only)."""
    def read(name):
        p = os.path.join(outdir, name)
        try:
            with open(p, errors='replace') as f:
                return f.read()
        except OSError:
            return ''

    meta = {}
    try:
        meta = json.load(open(os.path.join(outdir, 'meta.json')))
    except (OSError, ValueError):
        pass
    if extra_meta:
        meta.update(extra_meta)
    logcat = read('logcat.txt')
    pts = parse_vsync_pts(logcat)
    last_tick = pts[-1][1] if pts else 0
    stop_tick = meta.get('stop_tick', 0)
    stop_reason = meta.get('stop_reason', '')

    s = {'leg': meta.get('leg', os.path.basename(outdir.rstrip('/'))),
         'mode': meta.get('mode', ''), 'stop_tick': stop_tick,
         'last_tick': last_tick, 'stop_reason': stop_reason,
         'vsync_lines': len(pts)}
    # Race over identical ticks 1714->last (legs overshoot STOP; legsum method).
    if last_tick > RACE_TICK and len(pts) >= 2:
        r, wall = window_rate(pts, RACE_TICK, last_tick)
        s['race'] = {'window': [RACE_TICK, last_tick],
                     'vs_per_s': round(r, 2) if r else None,
                     'ratio': round(r / 59.94, 3) if r else None,
                     'wall_s': round(wall, 1) if wall else None}
    else:
        s['race'] = {'window': [RACE_TICK, last_tick], 'vs_per_s': None,
                     'ratio': None, 'wall_s': None}
    s['buckets_5s'] = per_5s_buckets(pts)
    s['fixed_windows'] = []
    for a, b in FIXED_WINDOWS:
        if last_tick >= b:
            r, wall = window_rate(pts, a, b)
            s['fixed_windows'].append(
                {'window': [a, b], 'vs_per_s': round(r, 2) if r else None,
                 'ratio': round(r / 59.94, 3) if r else None})
    t0 = meta.get('T0')
    s['phases'] = phases_summary(logcat, read('sf-latency.txt'), t0) if t0 else []
    s['threads'] = sched_summary(read('schedstat.txt'), pts)
    s['gpu'] = gpu_summary(read('gpubusy.txt'))
    # PT2 per-stage tails over the leg's CPU window (schedstat ticks when the
    # window landed, else the race window). perf/*.log holds this leg's files
    # only (see the pull: files new since before the launch).
    stage_lines = []
    try:
        pdir = os.path.join(outdir, 'perf')
        for name in sorted(os.listdir(pdir)):
            if name.endswith('.log'):
                try:
                    with open(os.path.join(pdir, name), errors='replace') as f:
                        stage_lines += parse_stage_lines(f.read())
                except OSError:
                    pass
    except OSError:
        pass
    cpu_win = (s['threads'] or {}).get('window')
    if not cpu_win and last_tick > RACE_TICK:
        cpu_win = [RACE_TICK, last_tick]
    s['tails'] = tails_summary(stage_lines, cpu_win)
    s['tails_files'] = len([n for n in os.listdir(os.path.join(outdir, 'perf'))
                            if n.endswith('.log')]) if os.path.isdir(os.path.join(outdir, 'perf')) else 0
    pl_fast, pl = read('present-latency-fast.txt'), read('present-latency.txt')
    s['hitches'] = present_hitches(pl_fast or pl) if (pl_fast or pl) else None
    if s['hitches'] is not None:
        s['hitches']['source'] = ('present-latency-fast.txt' if pl_fast
                                  else 'present-latency.txt')
    # DSP3 item 2: SF latch detector (in-app latched= + ring cross-check).
    s['sf_latch'] = sf_latch_summary(os.path.join(outdir, 'perf'), pl_fast or pl)
    s['diff_px'] = {int(t): int(px) for t, px in COMPARE_RE.findall(logcat)}
    mt = MTVU_RE.findall(logcat)
    s['mtvu_last'] = {'jobs': int(mt[-1][0]), 'violations': int(mt[-1][1])} if mt else None
    s['fatal'] = len(re.findall(r'FATAL|Fatal signal', logcat))
    try:
        s['disconnects'] = sum(1 for _ in open(os.path.join(outdir, 'disconnects.txt')))
    except OSError:
        s['disconnects'] = 0
    s['apk_sha'] = meta.get('apk_sha', '')
    s['env_sha'] = meta.get('env_sha', '')
    s['restore_rc'] = meta.get('restore_rc')

    # Verdict (OB1 follow-up split, orchestrator gate): final-mode legs need
    # >= 15 [vsync-rate] lines; screening legs need >= 10 lines + steady
    # cadence (no gap > 2x the median interval) + no transport gap. Legs whose
    # meta predates the mode field (old lane logs) are judged as final.
    mode = meta.get('mode') or 'final'
    s['mode'] = mode
    gaps = [w1 - w0 for (w0, _), (w1, _) in zip(pts, pts[1:])]
    med = sorted(gaps)[len(gaps) // 2] if gaps else 0.0
    max_gap = max(gaps) if gaps else 0.0
    reasons, warnings = [], []
    if mode == 'screen':
        if len(pts) < 10:
            reasons.append(f'screening: only {len(pts)} vsync-rate lines (< 10)')
        if gaps and max_gap > 2 * med:
            reasons.append(f'screening: unsteady cadence '
                           f'(max gap {max_gap:.1f}s > 2x median {med:.1f}s)')
        if s['disconnects']:
            reasons.append(f"screening: {s['disconnects']} transport events "
                           f'(see disconnects.txt)')
    else:
        if len(pts) < 15:
            reasons.append(f'final: only {len(pts)} vsync-rate lines (< 15)')
        if s['disconnects']:
            warnings.append(f"{s['disconnects']} transport events (see disconnects.txt)")
    if s['fatal']:
        reasons.append(f'{s["fatal"]} FATAL lines')
    # DSP3 item 2: a 120 leg posting >= 115 presents/window but latching a
    # median < 110/s ran on an SF compositor stuck at 60 (DSP2). The
    # ring share is the cross-check (healthy legs read > 0.30).
    if is_120_leg(meta):
        sf = s['sf_latch']
        if (sf['qual_windows'] >= DSP3_MIN_WINDOWS and sf['latched_median'] is not None
                and sf['latched_median'] < DSP3_LATCH_FAIL):
            ring = ('ring n/a' if sf['ring_frac_short'] is None
                    else 'ring frac<12.4=%.3f (n=%d)' % (sf['ring_frac_short'],
                                                         sf['ring_intervals']))
            contra = (sf['ring_frac_short'] is not None
                      and sf['ring_frac_short'] >= DSP3_RING_HEALTHY)
            reasons.append(
                'SF-LATCH-60: 120 leg latched med=%d/s over %d engaged windows '
                '(presents med=%s) while posting >= 115; %s%s'
                % (sf['latched_median'], sf['qual_windows'], sf['presents_median'], ring,
                   ' RING-CONTRADICTS (in-app counter suspect)' if contra else ''))
    if stop_tick and last_tick < stop_tick and stop_reason not in (
            'wall-cap', 'profile-done', 'title-timeout'):
        reasons.append(f'ended at tick {last_tick} before stop {stop_tick} ({stop_reason})')
    if s['mtvu_last'] and s['mtvu_last']['violations']:
        warnings.append(f"mtvu violations={s['mtvu_last']['violations']}")
    if s['threads'] is None:
        warnings.append('no schedstat window (cpu-window missed)')
    if s['tails'] is None:
        warnings.append('no perf-stage tails (knob off, old APK, or window missed)')
    if s['hitches'] is None:
        warnings.append('no present-latency (hitch sampler missed)')
    s['verdict'] = 'VOID' if reasons else 'VALID'
    s['reasons'] = reasons
    s['warnings'] = warnings
    s['validity'] = {
        'rule': ('screen: >=10 lines + steady cadence + no transport gap'
                 if mode == 'screen' else 'final: >=15 lines'),
        'lines': len(pts), 'median_gap_s': round(med, 2),
        'max_gap_s': round(max_gap, 2), 'transport_events': s['disconnects'],
        'detail': (f"{mode}: {len(pts)} lines, "
                   f'max_gap {max_gap:.1f}s vs 2x_median {2 * med:.1f}s, '
                   f"{s['disconnects']} transport events -> {s['verdict']}"),
    }
    return s


def summary_row_text(s):
    """One legsum-style row for the batch table."""
    r = s['race']
    race = f"{r['vs_per_s']:.2f} {r['ratio']:.3f}x" if r['vs_per_s'] else '—'
    t = s.get('threads') or {}
    th = t.get('threads', {}) if isinstance(t, dict) else {}
    def tw(k):
        v = th.get(k)
        return f"{v['run']:.1f}/{v['wait']:.1f}/{v['blk']:.1f}" if v else '—'
    g = (s.get('gpu') or {}).get('race') or {}
    gpu = f"{g['mean_pct']:.0f}% n={g['n']}" if g.get('mean_pct') is not None else '—'
    h = ((s.get('hitches') or {}).get('by_phase') or {}).get('race') or {}
    hitch = (f"p99 {h['p99_ms']:.0f} max {h['max_ms']:.0f} >50 {h['gt50']} >100 {h['gt100']}"
             if h.get('n') else '—')
    dx = ','.join(f"t{t}={p}" for t, p in sorted((s.get('diff_px') or {}).items())) or '—'
    tl = (s.get('tails') or {}).get('stages', {})

    def tp(k):
        v = tl.get(k)
        return f"{v['p99']:.1f}/{v['max']:.1f}" if v else '—'
    tails = ' | '.join(tp(k) for k in STAGES)

    def tm(k):
        v = th.get(k)
        return f"{v['ms60']:.1f}" if v and 'ms60' in v else '—'
    ms60 = ' | '.join(tm(k) for k in ('MTVU', 'GameThread', 'GS'))
    return (f"| {s['leg']} | {race} ({r['window'][0]}→{r['window'][1]}) | {s['vsync_lines']} | "
            f"{s['verdict']} | {tw('MTVU')} | {tw('GameThread')} | {tw('GS')} | {gpu} | {hitch} | {dx} | "
            f"{tails} | {ms60} |")


SUMMARY_HEADER = ('| Leg | race vs/s × | vsync | verdict | MTVU run/runnable/blk | '
                  'GameThread run/runnable/blk | GS run/runnable/blk | GPU busy race | '
                  'race hitches | diff_px | ee.busy p99/max | ee.cpu p99/max | ee.wait p99/max | '
                  'gs.busy p99/max | mtvu.busy p99/max | gpu.busy p99/max | gsback.busy p99/max | '
                  'MTVU ms@60 | GameThread ms@60 | GS ms@60 |')
SUMMARY_NCOLS = 20


# ---------------------------------------------------------------------------
# Device leg
# ---------------------------------------------------------------------------

def build_arg_parser():
    ap = argparse.ArgumentParser(description='One canonical Odin leg (OB1).')
    ap.add_argument('--label', default='',
                    help='leg label (receipts, screencap names; also the default lease tag)')
    ap.add_argument('--lease', default='',
                    help='lease holder tag (default: --label)')
    ap.add_argument('--out', default='', help='receipts dir (required unless --summarize-only)')
    ap.add_argument('--scratch', default='',
                    help='pulled PNGs/.data dir (default ~/dev/ssx3-work/odinrun/<label>)')
    ap.add_argument('--apk', default='', help='APK to install (always installed)')
    ap.add_argument('--apk-sha', default='')
    ap.add_argument('--mode', choices=('screen', 'final'), default='screen')
    ap.add_argument('--stop-tick', type=int, default=0,
                    help='0 = by mode (screen 3000, final 4500)')
    ap.add_argument('--env', action='append', default=[],
                    help='ps2x.env overlay line K=V, applied after the play env (repeatable)')
    ap.add_argument('--variant', choices=('A', 'B', 'VA', 'VB'), default='A',
                    help='V* = + PS2X_PRESENT_VULKAN=1 (CN2b: A/B differed only in paraLLEl-GS keys; now the same)')
    ap.add_argument('--cpu-window', default='1900,2500',
                    help='schedstat three-way window S,E (empty = skip)')
    ap.add_argument('--compare-ticks', default='')
    ap.add_argument('--lifecycle', type=int, default=0)
    ap.add_argument('--sf-dump-tick', default='')
    ap.add_argument('--activity', default='android.app.NativeActivity',
                    help='component to am start (TG1: .Launch60 / .Launch120 launcher aliases)')
    ap.add_argument('--aspect', choices=('', '4:3', '16:9', 'native'), default='')
    ap.add_argument('--vk', choices=('', '0', '1'), default='')
    ap.add_argument('--pad-probe', action='store_true')
    ap.add_argument('--pin-cpu7', action='store_true')
    ap.add_argument('--probe-at-end', action='store_true')
    ap.add_argument('--game-cpus', default='')
    ap.add_argument('--profile-after-tick', type=int, default=0)
    ap.add_argument('--profile-secs', type=int, default=30)
    ap.add_argument('--offcpu', action='store_true')
    ap.add_argument('--scap-ticks', default='2100,3000,4000')
    ap.add_argument('--fast-poll', type=float, default=1.0,
                    help='bg-thread present-latency poll period in s into '
                    'present-latency-fast.txt (0 = disable, legacy file only)')
    ap.add_argument('--scap-every', type=float, default=9999.0)
    ap.add_argument('--wall', type=float, default=600.0)
    ap.add_argument('--min-battery', type=int, default=40)  # 10-14: was 20; back-to-back legs drained the Odin to 15 % (reboot)
    ap.add_argument('--fan', choices=('smart', 'leave', 'performance'), default='smart',
                    help='smart (default, Brad 10-15): fan_mode=4 during the leg, Brad\'s value restored after; '
                         'performance: fan_mode=5 for a planned back-to-back batch only')
    ap.add_argument('--cooldown-max-temp', type=float, default=42.0)
    ap.add_argument('--cooldown-wait', type=float, default=180.0)
    ap.add_argument('--cooldown-cap-min', type=float, default=20.0)
    ap.add_argument('--no-cooldown', action='store_true')
    ap.add_argument('--lease-tries', type=int, default=30)
    ap.add_argument('--lease-sleep', type=float, default=30.0)
    ap.add_argument('--keep-fan', action='store_true',
                    help='KEEP_FAN=1 on the restore (batch legs except the last)')
    ap.add_argument('--no-restore', action='store_true')
    ap.add_argument('--restore-label', default='')
    ap.add_argument('--play-env', default=PLAY_ENV)
    ap.add_argument('--play-sums', default=PLAY_SUMS)
    ap.add_argument('--summarize-only', default='',
                    help='no device: (re)build summary files for an existing leg dir')
    return ap


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def play_env_sha(sums_path):
    """Want-env SHA from the canonical SHA256SUMS (never hard-coded)."""
    for line in open(sums_path):
        parts = line.split()
        if len(parts) == 2 and parts[1] == 'ps2x.env':
            return parts[0]
    raise SystemExit(f'no ps2x.env pin in {sums_path}')


def run_leg(a):
    D = open(ODIN_SERIAL_FILE).read().strip()
    lease_tag = a.lease or a.label
    OUT, SCR = a.out, a.scratch or os.path.join(SCRATCH_ROOT, a.label)
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(SCR, exist_ok=True)
    stop_tick = a.stop_tick or (3000 if a.mode == 'screen' else 4500)
    T0 = [None]
    CLAIMED, PUSHED, LAUNCHED = {'v': False}, {'v': False}, {'v': False}
    DISCONNECTS = {'n': 0}

    def adb(*args, check=False, timeout=60):
        try:
            r = subprocess.run(['adb', '-s', D, *args], capture_output=True,
                               text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            DISCONNECTS['n'] += 1
            with open(f'{OUT}/disconnects.txt', 'a') as f:
                f.write(f't={time.time() - (T0[0] or time.time()):.1f} TIMEOUT adb {args}\n')
            if check:
                raise SystemExit(f'adb {args} timed out')
            return ''
        if r.returncode != 0 and ('device' in r.stderr
                                  and ('not found' in r.stderr or 'offline' in r.stderr)):
            DISCONNECTS['n'] += 1
            with open(f'{OUT}/disconnects.txt', 'a') as f:
                f.write(f't={time.time() - (T0[0] or time.time()):.1f}'
                        f' TRANSPORT adb {args}: {r.stderr.strip()[:160]}\n')
        if check and r.returncode != 0:
            raise SystemExit(f'adb {args} failed: {r.stderr}')
        return r.stdout

    def sh(cmd, timeout=60):
        return adb('shell', cmd, timeout=timeout)

    def log(msg):
        t = '' if T0[0] is None else f' t+{time.time() - T0[0]:7.1f}'
        line = f'[{time.strftime("%H:%M:%S")}{t}] {msg}'
        print(line, flush=True)
        with open(f'{OUT}/driver.log', 'a') as f:
            f.write(line + '\n')

    def lease_release():
        subprocess.run(['bash', LEASESH, 'release', lease_tag],
                       capture_output=True, timeout=60)

    def cleanup():
        # Gated: only undo what this run did (never touch a session we didn't start).
        try:
            if PUSHED['v']:
                subprocess.run(['adb', '-s', D, 'push', f'{OUT}/ps2x.env.brad',
                                f'{FILES}/ps2x.env'], capture_output=True, timeout=60)
        except Exception:
            pass
        try:
            if LAUNCHED['v']:
                subprocess.run(['adb', '-s', D, 'shell', 'am', 'force-stop', PKG],
                               capture_output=True, timeout=30)
        except Exception:
            pass
        try:
            if CLAIMED['v']:
                subprocess.run(['bash', LEASESH, 'release', lease_tag],
                               capture_output=True, timeout=60)
        except Exception:
            pass

    atexit.register(cleanup)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit('SIGTERM'))

    def cooldown():
        cmd = [sys.executable, COOLDOWN, '--out', OUT, '--mode', a.mode,
               '--max-temp-c', str(a.cooldown_max_temp), '--wait-s', str(a.cooldown_wait),
               '--cap-min', str(a.cooldown_cap_min), '--fan', a.fan]
        log('+ ' + ' '.join(cmd))
        r = subprocess.run(cmd)
        if r.returncode != 0:
            raise SystemExit('cool-down refused (cap reached): operator decides')

    # Fail-fast pre-check (read-only, no lease): don't cool down a locked/
    # unplugged device for 20 min.
    kg0 = sh('dumpsys window policy | grep -m1 showing').strip()
    bat0 = sh('dumpsys battery')
    m0 = re.search(r'level: (\d+)', bat0)
    ac0 = re.search(r'AC powered: (\w+)', bat0)
    log(f'PRE0 keyguard="{kg0}" battery={m0.group(1) if m0 else "?"}% '
        f'ac={ac0.group(1) if ac0 else "?"}')
    if 'showing=false' not in kg0:
        sys.exit('keyguard showing: ask Brad to unlock')
    if not m0 or int(m0.group(1)) < a.min_battery or not ac0 or ac0.group(1) != 'true':
        sys.exit('battery/AC gate failed (need >= %d%% and AC powered, status ignored)'
                 % a.min_battery)

    # F9 run1.sh order: cool down WITHOUT the lease, then claim; if the race
    # is lost, wait for FREE and cool down again so the launch always follows
    # a completed cool-down with no other lane's run in between.
    if not a.no_cooldown:
        for attempt in range(1, a.lease_tries + 1):
            while True:
                cur = subprocess.run(['bash', LEASESH, 'status'], capture_output=True,
                                     text=True, timeout=60).stdout.strip()
                if cur.startswith('LEASE_FREE') or cur.startswith(lease_tag + ' '):
                    break
                log(f'lease busy: {cur} (wait {a.lease_sleep:.0f}s)')
                time.sleep(a.lease_sleep)
            cooldown()
            r = subprocess.run(['bash', LEASESH, 'claim', lease_tag],
                               capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                CLAIMED['v'] = True
                log(f'LEASE claimed after cool-down: {r.stdout.strip()}')
                break
            log(f'lost the lease race after cool-down ({r.stdout.strip()}); again')
            if attempt >= a.lease_tries:
                sys.exit('lease race lost too often; giving up')
    else:
        r = subprocess.run(['bash', LEASESH, 'claim', lease_tag],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            sys.exit(f'atomic claim failed: {r.stdout.strip()} {r.stderr.strip()}')
        CLAIMED['v'] = True

    # --- preconditions (holding the lease) ---
    lease = sh('cat /data/local/tmp/mg/LEASE').strip()
    kg = sh('dumpsys window policy | grep -m1 showing').strip()
    bat = sh('dumpsys battery')
    level = int(re.search(r'level: (\d+)', bat).group(1))
    acm = re.search(r'AC powered: (\w+)', bat)
    ac = acm.group(1) if acm else 'unknown'
    pid0 = sh(f'pidof {PKG}').strip()
    dev_env_sha = sh(f'sha256sum {FILES}/ps2x.env').split()[0]
    sh(f'mkdir -p {FILES}/mc0-test')
    mc0t = sh(f'ls -A {FILES}/mc0-test').strip()
    mc0_bad = [f for f, want in MC0_PINS.items()
               if sh(f'sha256sum {FILES}/mc0/{f}').split()[0] != want]
    want_env = play_env_sha(a.play_sums)
    log(f'PRE lease="{lease}" keyguard="{kg}" battery={level}% ac={ac} pid0={pid0 or "none"}')
    log(f'PRE device-env={dev_env_sha} mc0-test={mc0t!r} mc0-bad={mc0_bad}')
    if 'showing=false' not in kg:
        sys.exit('keyguard showing: ask Brad to unlock')
    if level < a.min_battery or ac != 'true':
        sys.exit(f'battery {level}% ac={ac}: need >= {a.min_battery}% and AC powered (status ignored)')
    if pid0:
        sys.exit(f'app already running (pid {pid0}): stop, do not disturb')
    if dev_env_sha != want_env:
        sys.exit(f'device env is NOT the play env: {dev_env_sha} (refusing to touch)')
    if mc0_bad:
        sys.exit(f'Brad mc0 mismatch: {mc0_bad} (refusing to run)')
    if mc0t:
        sys.exit(f'mc0-test not empty: {mc0t!r}')

    # DSP3 item 3: pre-leg SF vsync-tracker sanity (read-only). A 120 leg on a
    # tracker stuck at 60 (DSP2) can latch at most 60/s no matter what the app
    # posts; refuse the leg instead of burning it. Recorded for every leg.
    sf_tracker = sh("dumpsys SurfaceFlinger | grep -aE 'mDisplayModePtr|mPeriodConfirmationInProgress'")
    open(f'{OUT}/sf-tracker-pre.txt', 'w').write(sf_tracker)
    log(f"SFTRACKER pre: {sf_tracker.strip().replace(chr(10), ' | ')[:300]}")
    is_120_pre = ('Launch120' in (a.activity or '')) or any(
        e.split('=', 1) == ['PS2X_SSX3_FULL120', 'events'] for e in (a.env or []) if '=' in e)
    if is_120_pre and ('vsyncRate=60.00' in sf_tracker or 'mPeriodConfirmationInProgress=1' in sf_tracker):
        sys.exit('INFRA-BLOCKED: SF vsync tracker stuck at 60 (see sf-tracker-pre.txt; '
                 'needs Brad: screen power cycle or reboot)')

    # GG1 device.txt: pre-run device state + devfreq/kgsl one-shots.
    with open(f'{OUT}/device.txt', 'w') as f:
        f.write(f'keyguard: {kg}\nbattery: {level}% ac={ac}\n')
        for n in ('available_frequencies', 'max_freq', 'governor', 'cur_freq'):
            f.write(f'{n}: ' + sh(f'cat /sys/class/devfreq/*gpu*/{n} 2>/dev/null || '
                                  f'cat /sys/class/kgsl/kgsl-3d0/{n} 2>/dev/null || echo UNREADABLE'))
        f.write('gpu_available_frequencies: '
                + sh('cat /sys/class/kgsl/kgsl-3d0/gpu_available_frequencies'))
        f.write('gpu_clock_stats_pre: ' + sh('cat /sys/class/kgsl/kgsl-3d0/gpu_clock_stats'))
        f.write('devfreq_available: '
                + sh('cat /sys/class/devfreq/*/available_frequencies 2>/dev/null'))
        f.write('devfreq_governor: ' + sh('cat /sys/class/devfreq/*/governor 2>/dev/null'))

    adb('pull', f'{FILES}/ps2x.env', f'{OUT}/ps2x.env.brad')
    shutil.copy(f'{OUT}/ps2x.env.brad', f'{SCR}/ps2x.env.brad-device')
    if not a.apk:
        sys.exit('needs --apk <path> (always installed, even if present)')
    h1, h2 = sha256_file(a.apk), sha256_file(a.apk)
    log(f'APK local sha x2 {h1} {h2}')
    if h1 != h2 or (a.apk_sha and h1 != a.apk_sha):
        sys.exit(f'APK SHA mismatch: {h1} {h2} want {a.apk_sha}')
    log('INSTALL ' + adb('install', '-r', a.apk, timeout=600).strip().replace('\n', ' '))
    bp = sh(f'pm path {PKG}').strip().split(':', 1)[-1]
    bsha = sh(f'sha256sum {bp}').split()[0]
    log(f'INSTALLED {bp} sha={bsha} match={bsha == h1}')
    if bsha != h1:
        sys.exit('installed base.apk does not match')

    # Env: the PLAY ENV FILE as the base (never edited), then run keys, then
    # --env overlays last. The app applies setenv in file order with
    # overwrite=1, so later lines override earlier ones (TN1 P4).
    base = [l.rstrip('\n') for l in open(a.play_env)
            if l.strip() and not l.startswith('#')]
    env = [f'# odin_run {a.label} mode={a.mode} variant={a.variant}',
           f'# play base {a.play_env} sha={want_env}'] + base
    env += [f'PS2X_MC_ROOT={FILES}/mc0-test',
            f'PS2X_PAD_SCRIPT={ROUTE}', 'PS2X_PAD_SCRIPT_CLOCK=vsync',
            'PS2X_VSYNC_RATE_LOG=1', 'PS2X_UNPACED=1',
            # ACH3 (10-05): the play base enables achievements; test legs must never
            # unlock into Brad's files/achievements-state.json or toast.
            'PS2X_ACHIEVEMENTS=0'] + [
            # 10-06: the play base writes playthrough telemetry to files/telemetry; PB31/PB32
            # bench legs filled Brad's dir with 45 test sessions. Redirect (keeps the cost).
            f'{k}=/storage/emulated/0/Android/data/com.ps2x.runner/files/test-telemetry'
            for k in ('PS2X_VU_TELEMETRY', 'PS2X_GS_TELEMETRY', 'PS2X_COVERAGE_OUT', 'PS2X_SESSION_LOG')]
    if a.vk:
        env.append(f'PS2X_PRESENT_VULKAN={a.vk}')
    elif a.variant.startswith('V'):
        env.append('PS2X_PRESENT_VULKAN=1')
    if a.aspect:
        env.append(f'PS2X_ASPECT={a.aspect}')
    DDUMP = f'{FILES}/fs2dump'
    if a.pad_probe:
        env.append('PS2X_VIRTUAL_PAD=1')
    if a.compare_ticks:
        env.append(f'PS2X_PRESENT_VK_COMPARE_TICKS={a.compare_ticks}')
        env.append(f'PS2X_PRESENT_VK_DUMP_DIR={DDUMP}')
    if a.game_cpus:
        env.append(f'PS2X_GAME_THREAD_CPUS={a.game_cpus}')
    for e in (a.env or []):
        if '=' not in e:
            sys.exit(f'bad --env (want K=V): {e!r}')
        env.append(e)
    # RPX1 (10-14): padrec replay uses the cycle-only event baseline even when the
    # recording's live session had DET unset (4/4 EXACT with DET=1 vs host-timed
    # A legs). Applied after caller overlays so a stray DET=0 can't reintroduce
    # host-timed NV decisions.
    pad_script = next((e.split('=', 1)[1] for e in reversed(env)
                       if e.startswith('PS2X_PAD_SCRIPT=')), '')
    if (pad_script.startswith('@') and
            os.path.basename(pad_script[1:]).startswith('padrec-')):
        env.append('PS2X_DETERMINISTIC=1')
        log('PADREC-REPLAY cycle-only scheduler PS2X_DETERMINISTIC=1')
    # 10-14: lane legs never record into Brad's padrec folder (the play env's
    # KEEP=10 rotation would push his own session recordings out). Redirect to a
    # per-label lane dir instead of disabling: input markers write next to the
    # record dir and stay off without one (GRY2). A caller that explicitly passes
    # PS2X_PAD_RECORD_DIR keeps its value. Lanes remove FILES/lane-rec/<label>.
    if not any(e.startswith('PS2X_PAD_RECORD_DIR=') for e in (a.env or [])):
        env.append(f'PS2X_PAD_RECORD_DIR={FILES}/lane-rec/{a.label or "unlabeled"}/padrec')
    # GE5: the GS-stream record keys are exempt: they write a replayable capture,
    # not a present/video dump, and they force MTVU off in the runtime, so a
    # capture leg is diagnostic-only by construction (never a speed number).
    # AU12: the audio stretch-trace key is exempt too: a small bounded CSV of
    # per-callback controller state, not a present/video dump; legs using it
    # record WAVs and are diagnostic-only by construction (never a speed number).
    # TU1: Mesa u_trace keys (Turnip per-render-pass GPU timestamps to a CSV) are exempt too: they only
    # apply when GE1 runs on Turnip (GE1_VK_TURNIP=1), and legs using them are diagnostic-only (never a speed number).
    _diag_env = [e for e in env if e.split('=', 1)[0] not in
                 ('PS2X_GS_CAPTURE', 'PS2X_GS_CAPTURE_STOP_TICK', 'PS2X_STRETCH_TRACE',
                  'MESA_GPU_TRACES', 'MESA_GPU_TRACEFILE', 'TU_GPU_TRACEPOINT')]
    assert a.compare_ticks or not any(x in e for e in _diag_env for x in ('DUMP', 'TRACE', 'CAPTURE', 'ORACLE')), \
        'dump/trace key in env'
    open(f'{OUT}/ps2x.env', 'w').write('\n'.join(env) + '\n')
    sh(f'mkdir -p {DSCRAP}; rm -f {DSCRAP}/*.png {DSCRAP}/*.data; rm -rf {DDUMP}; mkdir -p {DDUMP}')
    adb('push', f'{OUT}/ps2x.env', f'{FILES}/ps2x.env', check=True)
    PUSHED['v'] = True
    env_sha = sh(f'sha256sum {FILES}/ps2x.env').split()[0]
    log(f'ENV {env_sha}')
    sh(f'am force-stop {PKG}')
    adb('logcat', '-c')
    # PT2: snapshot the on-device perf ring so the post-stop pull takes only
    # this leg's files (legs repeat the same ticks; merging an older leg's
    # file would corrupt the window tails).
    perf_before = set(sh(f'ls {FILES}/perf 2>/dev/null').split())
    lc = open(f'{OUT}/logcat.txt', 'w')
    lcp = subprocess.Popen(['adb', '-s', D, 'logcat', '-v', 'epoch', '-b', 'main', '-b', 'crash',
                            '-s', 'ps2x', 'raylib', 'DEBUG', 'libc', 'AndroidRuntime',
                            'Granite', 'MESA'],  # Granite: paraLLEl/Granite LOGI; MESA: ir3/Turnip logs (GC1)
                           stdout=lc, stderr=subprocess.STDOUT)
    T0[0] = time.time()

    log('AM ' + sh(f'am start -n {PKG}/{a.activity}').strip().replace('\n', ' | '))
    LAUNCHED['v'] = True
    time.sleep(6)
    focus = sh('dumpsys window | grep -m1 mCurrentFocus').strip()
    if PKG not in focus:
        sh('input keyevent 4')
        time.sleep(2)
        focus2 = sh('dumpsys window | grep -m1 mCurrentFocus').strip()
        log(f'BACK sent (was covered: {focus[:100]!r} now: {focus2[:100]!r})')
    else:
        log(f'FOCUS app-foreground, no BACK ({focus[:100]!r})')
    time.sleep(2)

    def game_tid():
        # NB: `ps -T -o tid,...` prints nothing on this ROM; top -H is the
        # proven thread lister.
        pid = sh(f'pidof {PKG}').strip()
        if not pid:
            return '', ''
        for line in sh(f'top -H -b -n1 -p {pid}').splitlines():
            if 'GameThread' in line:
                return line.split()[0], pid
        return '', pid

    if a.pin_cpu7:
        ok = False
        for i in range(45):
            tid, pid = game_tid()
            if tid:
                before = sh(f'taskset -p {tid}').strip().replace('\n', ' ')
                r = subprocess.run(['adb', '-s', D, 'shell', f'taskset -p 80 {tid}'],
                                   capture_output=True, text=True, timeout=30)
                after = sh(f'taskset -p {tid}').strip().replace('\n', ' ')
                log(f'PIN attempt={i} tid={tid} rc={r.returncode} out={r.stdout.strip()!r} '
                    f'err={r.stderr.strip()!r} before={before!r} after={after!r}')
                open(f'{OUT}/pin.txt', 'w').write(
                    f'tid={tid} rc={r.returncode} out={r.stdout} err={r.stderr} '
                    f'before={before} after={after}\n')
                ok = (r.returncode == 0)
                break
            time.sleep(2)
        if not ok:
            log('PIN FAILED or GameThread never appeared (run continues unpinned; see pin.txt)')

    def pick_layer():
        cands = []
        for l in sh('dumpsys SurfaceFlinger --list').splitlines():
            if PKG not in l or any(x in l for x in ('leash', 'InputSink', 'Background')):
                continue
            cands += re.findall(r'(com\.ps2x\.runner/android\.app\.NativeActivity#\d+)', l)
            m = re.search(r'name=(\S+ )?(com\.ps2\.runner/[^)]*)\)', l)
            if m:
                cands.append(m.group(2))
            cands.append(l.strip())
        seen = []
        for c in cands:
            if c in seen:
                continue
            seen.append(c)
            rows = [r for r in sh(f"dumpsys SurfaceFlinger --latency '{c}'").splitlines() if '\t' in r]
            if len(rows) > 2:
                return c, seen
        return '', seen

    def pick_present_layer():
        # TN1 Part 2: the app presents to a 'ps2x-game' SurfaceControl layer,
        # not the NativeActivity container (whose --latency is all zeros on
        # this ROM). Take the first candidate with nonzero actual presents.
        cands = []
        for l in sh('dumpsys SurfaceFlinger --list').splitlines():
            if 'ps2x-game' not in l:
                continue
            m = re.search(r'(ps2x-game#\d+)', l)
            if m and m.group(1) not in cands:
                cands.append(m.group(1))
        for c in cands:
            try:
                out = sh(f"dumpsys SurfaceFlinger --latency '{c}'")
            except Exception:
                continue
            for r in out.splitlines():
                p = r.split('\t')
                if len(p) >= 2 and p[1].strip().isdigit() and int(p[1]) > 0:
                    return c
        return ''

    layer, tried = pick_layer()
    log(f'LAYER {layer!r} tried={tried}')
    transport = subprocess.run(['adb', 'devices', '-l'], capture_output=True, text=True,
                               timeout=30).stdout
    open(f'{OUT}/transport-start.txt', 'w').write(f'serial={D}\n{transport}')
    json.dump({'T0': T0[0], 'layer': layer, 'env': env, 'variant': a.variant, 'serial': D,
               'leg': a.label, 'mode': a.mode, 'stop_tick': stop_tick,
               'apk': a.apk, 'apk_sha': h1, 'env_sha': env_sha, 'activity': a.activity,
               'fan': a.fan, 'lease': lease_tag, 'fast_poll': a.fast_poll},
              open(f'{OUT}/meta.json', 'w'), indent=1)

    rate_re = re.compile(r'\[vsync-rate\] tick=(\d+) rate=([\d.]+)')
    cpu_win = [int(x) for x in a.cpu_window.split(',')] if a.cpu_window else []
    cpu_snap = {}
    lifecycle_done = False
    sf_ticks = [int(x) for x in a.sf_dump_tick.split(',') if x]

    def task_cpu(pid):
        # tid -> (comm, utime+stime clock ticks); CLK_TCK = 100 on this ROM.
        out = sh(f'for t in /proc/{pid}/task/*; do cat $t/stat; echo; done', timeout=60)
        res = {}
        for line in out.splitlines():
            if ')' not in line:
                continue
            head, rest = line.split(')', 1)
            tid, comm = head.split('(', 1)
            f = rest.split()
            res[tid.strip()] = (comm, int(f[11]) + int(f[12]))
        return res

    def task_sched(pid):
        # tid -> [comm, cpu_ticks, processor, run_ns, wait_ns, slices].
        # schedstat: run = ns on CPU, wait = ns runnable-but-waiting;
        # blocked = wall - run - wait.
        out = sh(f'for t in /proc/{pid}/task/*; do echo "== ${{t##*/}} $(cat $t/comm)"; '
                 f'cat $t/stat; cat $t/schedstat; done', timeout=90)
        res, tid, comm = {}, '', ''
        for line in out.splitlines():
            line = line.strip()
            if line.startswith('=='):
                try:
                    _, tid, comm = line.split(None, 2)
                except ValueError:
                    # Exiting task with an empty comm (TN1 P5 crash): skip it.
                    tid = ''
            elif ')' in line and tid and tid not in res:
                head, rest = line.split(')', 1)
                f = rest.split()
                try:
                    res[tid] = [comm, int(f[11]) + int(f[12]), f[36], 0, 0, 0]
                except (ValueError, IndexError):
                    tid = ''
            elif tid in res and res[tid][3] == 0 and line and line[0].isdigit():
                p = line.split()
                if len(p) >= 3:
                    try:
                        res[tid][3], res[tid][4], res[tid][5] = int(p[0]), int(p[1]), int(p[2])
                    except ValueError:
                        pass
                tid = ''
        return {k: v for k, v in res.items() if v[5] > 0 or v[3] > 0}

    def residency_snap():
        out = {}
        for c in range(8):
            out[f'cpu{c}'] = sh(f'cat /sys/devices/system/cpu/cpu{c}/cpufreq/stats/time_in_state 2>&1',
                                timeout=30)
        out['gpu_clock_stats'] = sh('cat /sys/class/kgsl/kgsl-3d0/gpu_clock_stats 2>&1', timeout=30)
        out['gpu_freqs'] = sh('cat /sys/class/kgsl/kgsl-3d0/gpu_available_frequencies 2>&1', timeout=30)
        return out

    def write_sched(tag0, tag1):
        (w0, k0, s0), (w1, k1, s1) = cpu_snap[tag0], cpu_snap[tag1]
        frames = max(1, k1 - k0)
        wall = w1 - w0
        with open(f'{OUT}/schedstat.txt', 'w') as f:
            f.write(f'window ticks {k0}->{k1} ({frames} frames) wall {wall:.2f} s '
                    f'= {1000 * wall / frames:.2f} ms/frame, {frames / wall:.2f} vs/s\n')
            f.write('tid comm cpu run_ms_per_frame wait_ms_per_frame blocked_ms_per_frame '
                    'cpu_ms_per_frame(proc) slices util_pct\n')
            for tid, row in sorted(s1.items(), key=lambda kv: -(kv[1][3] - s0.get(kv[0], kv[1])[3])):
                comm, ticks1, cpu, run1, wait1, sl1 = row
                _, ticks0, _, run0, wait0, sl0 = s0.get(tid, row)
                run = (run1 - run0) / 1e6 / frames
                wait = (wait1 - wait0) / 1e6 / frames
                blk = 1000 * wall / frames - run - wait
                f.write(f'{tid} {comm} {cpu} {run:.2f} {wait:.2f} {blk:.2f} '
                        f'{10 * (ticks1 - ticks0) / frames:.2f} {sl1 - sl0} '
                        f'{100 * (run1 - run0) / 1e9 / wall:.1f}\n')
        with open(f'{OUT}/residency.txt', 'w') as f:
            f.write(f'window ticks {k0}->{k1} wall {wall:.2f} s (units: 10 ms per state)\n')
            r0, r1 = cpu_snap[tag0 + '_res'], cpu_snap[tag1 + '_res']
            for c in range(8):
                f.write(f'--- cpu{c} freq_Hz delta_10ms\n')
                d0 = dict(l.split() for l in r0[f'cpu{c}'].splitlines() if len(l.split()) == 2)
                d1 = dict(l.split() for l in r1[f'cpu{c}'].splitlines() if len(l.split()) == 2)
                for fr in sorted(set(d0) | set(d1), key=int):
                    try:
                        f.write(f'{fr} {int(d1.get(fr, 0)) - int(d0.get(fr, 0))}\n')
                    except ValueError:
                        f.write(f'{fr} UNREADABLE\n')
            f.write(f"gpu_freqs: {r1['gpu_freqs'].strip()}\n")
            try:
                g0 = [int(x) for x in r0['gpu_clock_stats'].split()]
                g1 = [int(x) for x in r1['gpu_clock_stats'].split()]
                f.write('gpu_clock_stats_delta: ' + ' '.join(str(b - a) for a, b in zip(g0, g1)) + '\n')
            except ValueError:
                f.write(f"gpu_clock_stats UNREADABLE: {r1['gpu_clock_stats'][:200]!r}\n")

    def write_cpu():
        (w0, k0, c0), (w1, k1, c1) = cpu_snap['start'], cpu_snap['end']
        frames = max(1, k1 - k0)
        wall = w1 - w0
        rows = []
        for tid, (comm, t1) in c1.items():
            t0 = c0.get(tid, (comm, 0))[1]
            rows.append((t1 - t0, tid, comm))
        rows.sort(reverse=True)
        with open(f'{OUT}/threadcpu.txt', 'w') as f:
            f.write(f'window ticks {k0}->{k1} ({frames} frames) wall {wall:.2f} s '
                    f'= {1000 * wall / frames:.2f} ms/frame, {frames / wall:.2f} vs/s\n')
            f.write('tid comm cpu_s cpu_ms_per_frame util_pct\n')
            for d, tid, comm in rows:
                if d <= 0:
                    continue
                f.write(f'{tid} {comm} {d / 100:.2f} {10 * d / frames:.2f} {100 * (d / 100) / wall:.1f}\n')

    def run_lifecycle():
        log('LIFECYCLE start: HOME')
        layers0 = [l for l in sh('dumpsys SurfaceFlinger --list').splitlines() if 'ps2x' in l.lower() or PKG in l]
        log(f'LAYERS before {layers0}')
        sh('input keyevent 3')
        time.sleep(8)
        scap('bg')
        log(f'BG focus={sh("dumpsys window | grep -m1 mCurrentFocus").strip()[:120]!r} pid={sh(f"pidof {PKG}").strip()}')
        log('FG ' + sh(f'am start -n {PKG}/android.app.NativeActivity').strip().replace('\n', ' | '))
        time.sleep(8)
        scap('fg')
        layers1 = [l for l in sh('dumpsys SurfaceFlinger --list').splitlines() if 'ps2x' in l.lower() or PKG in l]
        log(f'LAYERS after-fg {layers1} focus={sh("dumpsys window | grep -m1 mCurrentFocus").strip()[:120]!r}')
        rot0 = sh('cmd window user-rotation').strip()
        log(f'ROTATION saved {rot0!r}')
        sh('cmd window user-rotation lock 3')
        time.sleep(6)
        scap('rot3')
        log(f'ROTATION lock3 display={sh("dumpsys window displays | grep -m2 -E \'mCurrentRotation|cur=\'").strip()[:200]!r}')
        m = re.search(r'(free|lock)\w*\D*(\d)?', rot0)
        if rot0.startswith('free'):
            sh('cmd window user-rotation free')
        elif m and m.group(2):
            sh(f'cmd window user-rotation lock {m.group(2)}')
        else:
            sh('cmd window user-rotation free')
        time.sleep(4)
        log(f'ROTATION restored -> {sh("cmd window user-rotation").strip()!r}')
        scap('rot-restored')

    scap_ticks = [int(x) for x in a.scap_ticks.split(',') if x]
    n_scap = 0
    last_scap = 0.0
    last_lat = 0.0
    last_gpu = 0.0
    last_plat = 0.0
    last_res = 0.0
    player = ''
    diag_layers = 0
    profiled = False
    tick = 0
    stop_reason = ''

    def scap(tag):
        nonlocal n_scap
        n_scap += 1
        name = f'sc{n_scap:02d}-{tag}-t{int(time.time() - T0[0])}.png'
        sh(f'screencap -p {DSCRAP}/{name}')
        log(f'SCAP {name} tick~{tick}')

    # CP3 fast present poller: the main loop's 2 s polls overrun the 127-row
    # SF ring (~2.1 s at 60 Hz) whenever an iteration runs long, forging
    # 1-3 s stitching "stalls". This thread does nothing but --latency, so
    # its poll spacing stays under the ring span; tpre/tpost bound the true
    # dump time (POLL t= is loop-top time and lags by seconds on adb).
    PLAYER, FAST_N = {'name': ''}, {'n': 0}
    FAST_STOP = threading.Event()

    def fast_poller():
        try:
            with open(f'{OUT}/present-latency-fast.txt', 'w') as f:
                while not FAST_STOP.is_set():
                    layer = PLAYER['name']
                    if not layer:
                        if FAST_STOP.wait(0.5):
                            break
                        continue
                    t_sched = time.time() - (T0[0] or time.time())
                    try:
                        ltxt = open(f'{OUT}/logcat.txt', errors='replace').read()
                    except OSError:
                        ltxt = ''
                    mm = rate_re.findall(ltxt)
                    tick_now = int(mm[-1][0]) if mm else 0
                    tpre = time.time()
                    try:
                        dump = sh(f"dumpsys SurfaceFlinger --latency '{layer}'",
                                  timeout=30)
                    except Exception as e:
                        dump = ''
                        f.write(f'# POLL-ERROR t={t_sched:.1f} {e}\n')
                        f.flush()
                    tpost = time.time()
                    if dump:
                        FAST_N['n'] += 1
                        f.write(f'POLL t={t_sched:.1f} tpre={tpre:.3f} '
                                f'tpost={tpost:.3f} tick={tick_now}\n')
                        f.write(dump if dump.endswith('\n') else dump + '\n')
                        f.flush()
                    FAST_STOP.wait(a.fast_poll)
        except Exception as e:
            try:
                log(f'FASTPOLL died: {e}')
            except Exception:
                pass

    fast_thr = None
    if a.fast_poll > 0:
        fast_thr = threading.Thread(target=fast_poller, daemon=True)
        fast_thr.start()
        log(f'FASTPOLL start period={a.fast_poll}s')

    while True:
        el = time.time() - T0[0]
        try:
            txt = open(f'{OUT}/logcat.txt', errors='replace').read()
        except OSError:
            txt = ''
        m = rate_re.findall(txt)
        if m:
            tick = int(m[-1][0])
        if 'FATAL' in txt or 'Fatal signal' in txt:
            log('FATAL seen in logcat')
            stop_reason = 'fatal'
            break
        pid = sh(f'pidof {PKG}').strip()
        if not pid:
            log('process gone')
            stop_reason = 'process-gone'
            break
        if el - last_lat >= 10:
            last_lat = el
            if not layer:
                layer, tried = pick_layer()
                log(f'LAYER retry {layer!r} tried={tried}')
            therm = sh('echo "$(cat /sys/devices/system/cpu/cpu5/cpufreq/scaling_cur_freq) '
                       '$(cat /sys/devices/system/cpu/cpu6/cpufreq/scaling_cur_freq) '
                       '$(cat /sys/devices/system/cpu/cpu7/cpufreq/scaling_cur_freq) '
                       '$(for z in /sys/class/thermal/thermal_zone*; do [ "$(cat $z/type)" = cpu-1-1-1 ] && cat $z/temp; done) '
                       '$(dumpsys thermalservice | grep -m1 "Thermal Status" | tr -dc 0-9)"').split()
            gtcpu = '?'
            try:
                tid2, _ = game_tid()
                if tid2:
                    st = sh(f'cat /proc/{pid}/task/{tid2}/stat').strip()
                    if st and ')' in st:
                        gtcpu = st.split(')', 1)[1].split()[36]  # field 39 = processor
            except Exception as e:
                gtcpu = f'err:{e}'[:40]
            therm.append(f'gtcpu={gtcpu}')
            with open(f'{OUT}/thermal.txt', 'a') as f:
                f.write(f't={el:.1f} tick={tick} ' + ' '.join(therm) + '\n')
            with open(f'{OUT}/sf-latency.txt', 'a') as f:
                f.write(f'POLL t={el:.1f} tick={tick}\n')
                f.write(sh(f"dumpsys SurfaceFlinger --latency '{layer}'"))
        if el - last_plat >= 2:
            # TN1 Part 2 hitch sampler: 2 s --latency polls on the present
            # layer (the ring holds 127 presents; polls overlap so offline
            # stitching loses nothing).
            last_plat = el
            if not player:
                player = pick_present_layer()
                PLAYER['name'] = player
                log(f'PLAYER {player!r}')
                if diag_layers < 3:
                    diag_layers += 1
                    with open(f'{OUT}/present-layers.txt', 'a') as f:
                        f.write(f'POLL t={el:.1f} tick={tick} player={player!r}\n')
                        f.write(sh('dumpsys SurfaceFlinger --list'))
                        f.write('---\n')
            if player:
                with open(f'{OUT}/present-latency.txt', 'a') as f:
                    f.write(f'POLL t={el:.1f} tick={tick}\n')
                    f.write(sh(f"dumpsys SurfaceFlinger --latency '{player}'"))
        if el - last_gpu >= 5:
            last_gpu = el
            gl = sh('cat /sys/class/kgsl/kgsl-3d0/gpubusy; cat /sys/class/kgsl/kgsl-3d0/gpuclk').splitlines()
            g = (gl[0].split() if gl else [])
            clk = gl[1].strip() if len(gl) > 1 else '?'
            try:
                # The two counters are NOT monotonic across reads; each read
                # is a self-contained busy/total window, so the ratio is
                # per-sample.
                busy, total = int(g[0]), int(g[1])
                pct = 100.0 * busy / total if total > 0 else -1.0
                with open(f'{OUT}/gpubusy.txt', 'a') as f:
                    f.write(f't={el:.1f} tick={tick} busy={busy} total={total} pct={pct:.1f} gpuclk={clk}\n')
            except (ValueError, IndexError):
                with open(f'{OUT}/gpubusy.txt', 'a') as f:
                    f.write(f't={el:.1f} tick={tick} raw={" ".join(g)} gpuclk={clk}\n')
        if el - last_res >= 30:
            # CP2 gpu_residency.py, folded in: cumulative gpu_clock_stats with
            # an epoch stamp so samples align to the race window via logcat.
            last_res = el
            with open(f'{OUT}/gpu-residency.txt', 'a') as f:
                f.write(f"t={time.time():.3f} tick={tick} clock_stats: "
                        + sh('cat /sys/class/kgsl/kgsl-3d0/gpu_clock_stats').strip().replace('\n', ' | ') + '\n')
        while scap_ticks and tick >= scap_ticks[0]:
            scap(f'tick{scap_ticks.pop(0)}')
            last_scap = el
        if el - last_scap >= a.scap_every:
            scap('periodic')
            last_scap = el
        if cpu_win and 'start' not in cpu_snap and tick >= cpu_win[0]:
            t_edge = time.time()
            cpu_snap['start'] = (t_edge, tick, task_cpu(pid))
            cpu_snap['sched_start'] = (t_edge, tick, task_sched(pid))
            cpu_snap['sched_start_res'] = residency_snap()
            log(f'CPUWIN start tick={tick}')
        if cpu_win and 'start' in cpu_snap and 'end' not in cpu_snap and tick >= cpu_win[1]:
            t_edge = time.time()
            cpu_snap['end'] = (t_edge, tick, task_cpu(pid))
            cpu_snap['sched_end'] = (t_edge, tick, task_sched(pid))
            cpu_snap['sched_end_res'] = residency_snap()
            log(f'CPUWIN end tick={tick}')
            write_cpu()
            write_sched('sched_start', 'sched_end')
        if sf_ticks and tick >= sf_ticks[0]:
            sf_ticks.pop(0)
            full = sh('dumpsys SurfaceFlinger', timeout=90)
            keep, on = [], False
            for ln in full.splitlines():
                if 'HWC layers' in ln:
                    on = True
                if on:
                    keep.append(ln)
                    if len(keep) > 60 or (keep and ln.strip() == '' and len(keep) > 3):
                        on = False
            keep += [ln for ln in full.splitlines() if 'ps2x-game' in ln][:40]
            with open(f'{OUT}/sf-hwc.txt', 'a') as f:
                f.write(f'tick={tick}\n' + '\n'.join(keep) + '\n')
            log(f'SFDUMP tick={tick} lines={len(keep)}')
        if a.lifecycle and not lifecycle_done and tick >= a.lifecycle:
            lifecycle_done = True
            run_lifecycle()
        if a.profile_after_tick and not profiled and tick >= a.profile_after_tick:
            profiled = True
            log(f'PROFILE start tick={tick}')
            prof_base = f'simpleperf record -g --app {PKG} -o {DSCRAP}/perf-{a.label}.data --duration {a.profile_secs}'
            # NB: --trace-offcpu requires -e cpu-clock; the default cpu-cycles
            # event is refused.
            prof_cmd = (f'simpleperf record -g -e cpu-clock --trace-offcpu --app {PKG} '
                        f'-o {DSCRAP}/perf-{a.label}.data --duration {a.profile_secs}') if a.offcpu else prof_base
            p = subprocess.Popen(['adb', '-s', D, 'shell', prof_cmd],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            samp = open(f'{OUT}/profile-samples.txt', 'w')
            t_prof0 = time.time()
            while p.poll() is None:
                try:
                    txt2 = open(f'{OUT}/logcat.txt', errors='replace').read()
                except OSError:
                    txt2 = ''
                m2 = rate_re.findall(txt2)
                tick = int(m2[-1][0]) if m2 else tick
                # CP3: the main loop is parked during the profile, so take
                # cpu-window edges here too (else 'end' lands post-profile
                # and the window wall is diluted ~3x).
                if cpu_win and 'start' not in cpu_snap and tick >= cpu_win[0]:
                    t_edge = time.time()
                    cpu_snap['start'] = (t_edge, tick, task_cpu(pid))
                    cpu_snap['sched_start'] = (t_edge, tick, task_sched(pid))
                    cpu_snap['sched_start_res'] = residency_snap()
                    log(f'CPUWIN start tick={tick} (in profile)')
                if cpu_win and 'start' in cpu_snap and 'end' not in cpu_snap and tick >= cpu_win[1]:
                    t_edge = time.time()
                    cpu_snap['end'] = (t_edge, tick, task_cpu(pid))
                    cpu_snap['sched_end'] = (t_edge, tick, task_sched(pid))
                    cpu_snap['sched_end_res'] = residency_snap()
                    log(f'CPUWIN end tick={tick} (in profile)')
                    write_cpu()
                    write_sched('sched_start', 'sched_end')
                el2 = time.time() - T0[0]
                s = sh('echo THERM cpu6=$(cat /sys/devices/system/cpu/cpu6/cpufreq/scaling_cur_freq) '
                       'cpu7=$(cat /sys/devices/system/cpu/cpu7/cpufreq/scaling_cur_freq) '
                       'status=$(dumpsys thermalservice | grep -m1 "Thermal Status" | tr -dc 0-9) '
                       'GPU busy=$(cat /sys/class/kgsl/kgsl-3d0/gpubusy) '
                       'gpuclk=$(cat /sys/class/kgsl/kgsl-3d0/gpuclk)', timeout=60)
                samp.write(f't={el2:.1f} tick={tick} {s.strip()}\n')
                samp.flush()
                try:
                    snap = task_sched(pid)
                    for tid2, row in sorted(snap.items()):
                        samp.write(f'  sched {tid2} {row[0]} cpu={row[2]} ticks={row[1]} '
                                   f'run_ns={row[3]} wait_ns={row[4]} slices={row[5]}\n')
                    samp.flush()
                except Exception as e:
                    samp.write(f'  sched ERROR {e}\n')
                time.sleep(2)
            prof_out = p.stdout.read()
            open(f'{OUT}/simpleperf-record.txt', 'w').write(prof_out)
            samp.write(f'PROFILE wall={time.time() - t_prof0:.1f}s end-tick~{tick}\n')
            samp.close()
            log(f'PROFILE end wall={time.time() - t_prof0:.1f}s tick~{tick}')
            log(f'PROFILE out: {prof_out.strip()[:300]!r}')
            perf_ls = sh(f'ls -l {DSCRAP}/perf-{a.label}.data 2>&1').strip()
            log(f'PROFILE data: {perf_ls[:160]!r}')
            try:
                perf_sz = int(perf_ls.split()[4]) if perf_ls.startswith('-') else -1
            except (ValueError, IndexError):
                perf_sz = -1
            if a.offcpu and perf_sz < 1024:
                log('PROFILE offcpu refused; falling back to plain -g')
                out2 = sh(prof_base, timeout=a.profile_secs + 120)
                open(f'{OUT}/simpleperf-record-fallback.txt', 'w').write(out2)
                try:
                    txt2 = open(f'{OUT}/logcat.txt', errors='replace').read()
                except OSError:
                    txt2 = ''
                m2 = rate_re.findall(txt2)
                tick = int(m2[-1][0]) if m2 else tick
                log(f'PROFILE fallback end tick~{tick}')
                log(f'PROFILE fallback data: {sh(f"ls -l {DSCRAP}/perf-{a.label}.data 2>&1").strip()[:160]!r}')
            scap('postprofile')
            stop_reason = 'profile-done'
            break
        if tick >= stop_tick and not a.profile_after_tick:
            log(f'STOP tick {tick} >= {stop_tick}')
            stop_reason = 'stop-tick'
            break
        if el >= 180 and tick < 570:
            log('STOP title not reached by 180s')
            stop_reason = 'title-timeout'
            break
        if el >= a.wall:
            log(f'STOP wall cap {a.wall}s tick={tick}')
            stop_reason = 'wall-cap'
            break
        time.sleep(2)

    FAST_STOP.set()
    if fast_thr is not None:
        fast_thr.join(timeout=60)
        log(f"FASTPOLL joined polls={FAST_N['n']}")
    scap('final')
    time.sleep(1)
    if a.pad_probe and sh(f'pidof {PKG}').strip():
        sh('input gamepad keyevent --longpress KEYCODE_BUTTON_A')
        time.sleep(3)
        vp = [l.strip()[-160:] for l in open(f'{OUT}/logcat.txt', errors='replace') if '[vpad]' in l]
        log(f'PADPROBE vpad lines: {vp[-3:]}')
    log('THREADS\n' + sh(f'top -H -b -n1 -p $(pidof {PKG}) | head -20'))
    log('BATTERY ' + ' '.join(l.strip() for l in sh('dumpsys battery').splitlines() if 'level' in l or 'status' in l))

    if a.probe_at_end and sh(f'pidof {PKG}').strip():
        log('PROBE simpleperf 5s start')
        r = subprocess.run(['adb', '-s', D, 'shell',
                            f'simpleperf record -g --app {PKG} -o {DSCRAP}/perf-probe.data --duration 5'],
                           capture_output=True, text=True, timeout=60)
        open(f'{OUT}/probe-simpleperf.txt', 'w').write(f'rc={r.returncode}\nOUT:\n{r.stdout}\nERR:\n{r.stderr}\n')
        log(f'PROBE simpleperf rc={r.returncode} err={r.stderr.strip()[:200]!r}')
        pid_p = sh(f'pidof {PKG}').strip()
        if pid_p:
            log('PROBE simpleperf -p 3s start')
            r0 = subprocess.run(['adb', '-s', D, 'shell',
                                 f'simpleperf record -g -p {pid_p} -o {DSCRAP}/perf-probe-p.data --duration 3'],
                                capture_output=True, text=True, timeout=60)
            open(f'{OUT}/probe-simpleperf-p.txt', 'w').write(f'rc={r0.returncode}\nOUT:\n{r0.stdout}\nERR:\n{r0.stderr}\n')
            log(f'PROBE simpleperf -p rc={r0.returncode} err={r0.stderr.strip()[:200]!r}')
        tid, pid = game_tid()
        if tid:
            b = sh(f'taskset -p {tid}').strip().replace('\n', ' ')
            r2 = subprocess.run(['adb', '-s', D, 'shell', f'taskset -p 80 {tid}'],
                                capture_output=True, text=True, timeout=30)
            aft = sh(f'taskset -p {tid}').strip().replace('\n', ' ')
            open(f'{OUT}/probe-taskset.txt', 'w').write(
                f'tid={tid} rc={r2.returncode} out={r2.stdout} err={r2.stderr} before={b} after={aft}\n')
            log(f'PROBE taskset tid={tid} rc={r2.returncode} before={b!r} after={aft!r}')
        else:
            open(f'{OUT}/probe-taskset.txt', 'w').write('GameThread not found\n')
            log('PROBE taskset: GameThread not found')

    sh(f'am force-stop {PKG}')
    LAUNCHED['v'] = False
    time.sleep(2)
    lcp.terminate()
    if a.compare_ticks:
        for name in sh(f'ls {DDUMP}').split():
            adb('pull', f'{DDUMP}/{name}', f'{SCR}/{name}')
    sh(f'rm -rf {DDUMP}')
    for name in sh(f'ls {DSCRAP}').split():
        if name.endswith('.png') or name.endswith('.data'):
            adb('pull', f'{DSCRAP}/{name}', f'{SCR}/{name}')
    # PT2: pull this leg's perf logs (new since before the launch) for the
    # per-stage tails. Missing dir / no new file = knob off or an old APK
    # (the summary warns; the leg still stands on its schedstat means).
    perf_new = [n for n in sh(f'ls {FILES}/perf 2>/dev/null').split()
                if n.endswith('.log') and n not in perf_before]
    if perf_new:
        os.makedirs(f'{OUT}/perf', exist_ok=True)
        for name in sorted(perf_new):
            adb('pull', f'{FILES}/perf/{name}', f'{OUT}/perf/{name}')
    log(f"PERF {'pulled ' + ' '.join(sorted(perf_new)) if perf_new else 'no new perf log (knob off or old APK)'}")
    with open(f'{OUT}/device.txt', 'a') as f:
        f.write('gpu_clock_stats_post: ' + sh('cat /sys/class/kgsl/kgsl-3d0/gpu_clock_stats'))
    adb('push', f'{OUT}/ps2x.env.brad', f'{FILES}/ps2x.env', check=True)
    PUSHED['v'] = False
    rest = sh(f'sha256sum {FILES}/ps2x.env').split()[0]
    log(f'RESTORE {rest} match={rest == want_env}')
    mc0_bad2 = [f for f, want in MC0_PINS.items()
                if sh(f'sha256sum {FILES}/mc0/{f}').split()[0] != want]
    log(f'MC0-AFTER bad={mc0_bad2}')
    with open(f'{OUT}/scap-sha.txt', 'w') as f:
        for name in sorted(os.listdir(SCR)):
            if name.endswith('.png') or name.endswith('.data') or name.endswith('.ppm'):
                h = sha256_file(os.path.join(SCR, name))
                f.write(f'{h}  {name}\n')
    transport_end = subprocess.run(['adb', 'devices', '-l'], capture_output=True, text=True,
                                   timeout=30).stdout
    open(f'{OUT}/transport-end.txt', 'w').write(f'serial={D}\ndisconnects={DISCONNECTS["n"]}\n{transport_end}')
    log(f'END pid-after={sh(f"pidof {PKG}").strip() or "none"} disconnects={DISCONNECTS["n"]}')
    lease_release()
    CLAIMED['v'] = False

    # Full play-state restore (own lease; never a lane copy of an old APK).
    restore_rc = 0
    if not a.no_restore:
        env = dict(os.environ)
        if a.keep_fan:
            env['KEEP_FAN'] = '1'
        cmd = ['bash', RESTORE, a.restore_label or a.label]
        log('+ ' + ' '.join(cmd) + (' KEEP_FAN=1' if a.keep_fan else ''))
        restore_rc = subprocess.run(cmd, timeout=900, env=env).returncode
        log(f'RESTORE_RC={restore_rc}')

    # Host-side summary (no device).
    meta = json.load(open(f'{OUT}/meta.json'))
    meta.update({'stop_reason': stop_reason, 'restore_rc': restore_rc,
                 'disconnects': DISCONNECTS['n']})
    json.dump(meta, open(f'{OUT}/meta.json', 'w'), indent=1)
    s = write_summaries(OUT, meta)
    if restore_rc != 0:
        raise SystemExit(f'leg ran but restore failed (rc={restore_rc})')
    # DSP3 item 2: a stuck-tracker leg fails loudly (after restore, so the
    # bench stops instead of burning more legs on a 60 Hz compositor).
    for r in s.get('reasons', []):
        if 'SF-LATCH-60' in r:
            raise SystemExit(r)


def write_summaries(outdir, extra_meta=None):
    """summary.json + summary.txt + hitches.txt + phases.txt for a leg dir."""
    s = summarize_leg(outdir, extra_meta)
    json.dump(s, open(os.path.join(outdir, 'summary.json'), 'w'), indent=1)
    with open(os.path.join(outdir, 'summary.txt'), 'w') as f:
        f.write(SUMMARY_HEADER + '\n')
        f.write('|' + ' --- |' * SUMMARY_NCOLS + '\n')
        f.write(summary_row_text(s) + '\n')
        f.write(f"stop: {s['stop_reason']} last_tick={s['last_tick']}\n")
        f.write(f"buckets_5s: {' '.join(str(b) for b in s['buckets_5s'])}\n")
        for w in s['fixed_windows']:
            f.write(f"window {w['window'][0]}->{w['window'][1]}: "
                    f"{w['vs_per_s']}/s {w['ratio']}x\n")
        if s.get('mtvu_last'):
            f.write(f"mtvu_last: jobs={s['mtvu_last']['jobs']} "
                    f"violations={s['mtvu_last']['violations']}\n")
        th = s.get('threads')
        if th and th.get('window'):
            f.write(f"schedstat window {th['window'][0]}->{th['window'][1]}: "
                    f"frames {th['frames']} -> {th['frames_fixed']} "
                    f"(wall {th['wall_s']} s x {th['vs_per_s']}/s)\n")
        if s.get('tails'):
            t = s['tails']
            f.write(f"tails window {t['window'][0]}->{t['window'][1]} "
                    f"(perf/*.log, {s.get('tails_files', '?')} files):\n")
            for name in STAGES:
                v = t['stages'].get(name)
                if not v:
                    f.write(f'  {name}: —\n')
                else:
                    f.write(f"  {name}: n={v['n']} p50={v['p50']} p95={v['p95']} "
                            f"p99={v['p99']} max={v['max']}\n")
        else:
            f.write(f"tails: none ({s.get('tails_files', 0)} perf files)\n")
        sf = s.get('sf_latch') or {}
        f.write(f"sf-latch: present_windows={sf.get('present_windows', 0)} "
                f"latched_windows={sf.get('latched_windows', 0)} "
                f"engaged={sf.get('engaged_windows', 0)} qual={sf.get('qual_windows', 0)} "
                f"latched_med={sf.get('latched_median', '—')} "
                f"ring_frac_short={sf.get('ring_frac_short', '—')} "
                f"(n={sf.get('ring_intervals', 0)})\n")
        f.write(f"fatal={s['fatal']} disconnects={s['disconnects']} "
                f"restore_rc={s['restore_rc']}\n")
        f.write(f"validity: {s['validity']['detail']} [{s['validity']['rule']}]\n")
        for r in s['reasons']:
            f.write(f'VOID: {r}\n')
        for w in s['warnings']:
            f.write(f'WARN: {w}\n')
    h = s.get('hitches')
    with open(os.path.join(outdir, 'hitches.txt'), 'w') as f:
        if not h:
            f.write('no present-latency.txt\n')
        else:
            f.write(f"source={h.get('source', 'present-latency.txt')} "
                    f"presents={h['presents']} intervals={h['intervals']} "
                    f"npolls={h.get('npolls', '?')}\n")
            f.write('| Phase | n | mean | p99 | max | >50ms | >100ms |\n')
            f.write('| --- | ---: | ---: | ---: | ---: | ---: | ---: |\n')
            for ph in ('title', 'menus', 'loading', 'race', 'ALL'):
                v = h['by_phase'].get(ph, {})
                if not v or not v.get('n'):
                    f.write(f'| {ph} | 0 | — | — | — | — | — |\n')
                else:
                    f.write(f"| {ph} | {v['n']} | {v['mean_ms']} | {v['p99_ms']} | "
                            f"{v['max_ms']} | {v['gt50']} | {v['gt100']} |\n")
            f.write('within-dump intervals (overrun-immune; the true hitch record):\n')
            for ph in ('title', 'menus', 'loading', 'race', 'ALL'):
                v = (h.get('within_by_phase') or {}).get(ph, {})
                if not v or not v.get('n'):
                    f.write(f'| {ph} | 0 | — | — | — | — | — |\n')
                else:
                    f.write(f"| {ph} | {v['n']} | {v['mean_ms']} | {v['p99_ms']} | "
                            f"{v['max_ms']} | {v['gt50']} | {v['gt100']} |\n")
            f.write(f"top_boundary_jumps_ms={h.get('top_boundary_jumps_ms', [])}\n")
    with open(os.path.join(outdir, 'phases.txt'), 'w') as f:
        if not s['phases']:
            f.write('no phases (missing T0 or vsync lines)\n')
        else:
            f.write('| Phase | Ticks | Wall (s) | Guest vsyncs/s | Ratio (÷59.94) | '
                    'SF presents/s (n) |\n')
            f.write('| --- | --- | ---: | ---: | ---: | ---: |\n')
            for p in s['phases']:
                sf = f"{p['sf_presents_per_s']} (n={p['sf_n']})" if p['sf_presents_per_s'] else '—'
                f.write(f"| {p['phase']} | {p['ticks'][0]}→{p['ticks'][1]} | {p['wall_s']} | "
                        f"{p['vs_per_s']} | {p['ratio']}× | {sf} |\n")
    print(SUMMARY_HEADER)
    print(summary_row_text(s))
    print(f"verdict={s['verdict']} reasons={s['reasons']} warnings={s['warnings']}")
    return s


def main():
    a = build_arg_parser().parse_args()
    if a.summarize_only:
        write_summaries(a.summarize_only)
        return
    if not a.label or not a.out:
        raise SystemExit('needs --label and --out (or --summarize-only DIR)')
    run_leg(a)


if __name__ == '__main__':
    main()


