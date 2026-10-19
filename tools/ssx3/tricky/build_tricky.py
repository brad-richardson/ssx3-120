#!/usr/bin/env python3
"""Public Tricky-layer builder: two discs in, <work>/tricky device layout out.

Contract (tk-build-1; called by tools/ssx3/build.py `tricky` step):
  build_tricky.py --tricky-iso <Tricky.iso> --ssx3-iso <SSX3.iso>
      --work <work>/tricky [--jobs N] [--routes-ref-dir DIR]
      [--vgmstream VGMSTREAM-CLI] [--ffmpeg FFMPEG]

Everything is derived from the user's own two discs plus in-repo recipe
text; nothing game-derived ships. uv is the only hard requirement:
ffmpeg + vgmstream are optional local installs for the Tricky audio
phases (tool_paths.py probes them; --tricky-audio on|off|auto, default
auto). Without them the driver skips speech/music/song/music-inf with
one clear message and stages no audio aliases, so the runtime falls
back to SSX 3 audio; worlds, HUD, menus and gems build regardless.
Single ISO members extract through the in-repo reader. A preflight
gates discs, audio-tool identity, and capacity before any work dir is
created. Refuses an existing --work dir. Fails closed: every stage output is structure-checked (WAV
shape, non-empty, phase acceptance) with its SHA recorded; the numeric
audio gate is cross-host SNR, not exact hashes.

Known skips (loud + recorded, not silent): mega world routes without a
verified --routes-ref-dir (pending mega-h/u2/c tail), the 9-member speech
merge (TK43b meter builders lost), the ENGLISH composite (lane-private
tk-audio-2f base). The manifest then carries the built courses only.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
T = Path(__file__).resolve().parent
sys.path.insert(0, str(T / 'shared'))
sys.path.insert(0, str(T))
from inspect_disc import Region, iso_files  # noqa: E402
from iso_extract import extract_member, big_member  # noqa: E402
import tool_paths  # noqa: E402
from run_chain import COURSES, TKP5_FINALS  # noqa: E402


def _ssx3_build():
    """tools/ssx3/build.py loaded by path (disc identity + SSX3 pins).

    The driver consumes the wrapper's pinned disc table instead of
    carrying its own copy, so the two can never drift apart.
    """
    global _SSX3_BUILD
    try:
        return _SSX3_BUILD
    except NameError:
        spec = importlib.util.spec_from_file_location(
            'ssx3_build', T.parent / 'build.py')
        _SSX3_BUILD = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_SSX3_BUILD)
        return _SSX3_BUILD

# -- course plan ------------------------------------------------------------
# aloha runs before mesa (mesa's s3 rung borrows aloha-b's receipt);
# mega runs last (its routes rung needs the reference input).
BUILD_COURSES = ['pipe', 'untrack', 'snow', 'gari', 'aloha',
                 'mesa', 'merq', 'elys', 'alaska', 'mega']


# Pool submit weights: measured bradflix world-leg walls (s), longest
# first — r13 queued mesa (aloha-gated) last and it started at ~81 min.
# Walls: gari 3236, merq 2714, aloha 2397, elys 1987, snow 1953,
# alaska 1132, untrack 674, mega 657 (search+capture; replay faster),
# pipe 390. mega was 260 (skip) before tk-mega-1 landed its world.
# Scheduling only: the serial path keeps BUILD_COURSES order, outputs are
# identical either way (courses are disjoint; Part B proved 1-vs-2
# worker byte equality). [tk-speed-1]
SUBMIT_WEIGHTS = {'gari': 3236, 'merq': 2714, 'aloha': 2397,
                  'elys': 1987, 'snow': 1953, 'alaska': 1132,
                  'untrack': 674, 'mega': 657, 'pipe': 390}


def course_submit_order():
    """Pool submit order, longest first (mesa excluded: aloha-gated)."""
    return sorted((c for c in BUILD_COURSES if c != 'mesa'),
                  key=lambda c: -SUBMIT_WEIGHTS.get(c, 0))
# World finals, imported from the driver's recorded pins (never
# transcribed): routes finals from the course recipes, s5 finals from the
# TKP5 table (mega excluded: it never reaches s5). Oracle note (orch
# 10-18): MERQ publishes the post-routes final a23d529a (tk-routes-2
# wall-2819 fix, shipped in PB70/IPB40); the TKP5 s5 pin bcbaf32d is
# stale for MERQ and is never gated (merq has an oracle_routes entry).
ROUTES_COURSES = {c: r['oracle_routes'] for c, r in COURSES.items()
                  if r.get('oracle_routes')}
S5_FINALS = {c: TKP5_FINALS[c] for c in TKP5_FINALS if c != 'mega'}
# Device payload goldens (tk-port-2 REPORT section 2 + TKP5 section 1).
# Preview is a recorded 12-char pin (tk-port-1 REPORT section 6).
PREVIEW_PIN = '3332b0af708d'
# Oracle changes (orch 10-18): HUD pin -> a7ccacdabb2c (two independent
# builds agree byte-for-byte; old pin stale). Music pins -> our pool
# outputs 26125d2a/de73a5b7 (play == public; arrangement/events match
# staging exactly, only remus audio differs; quality receipt REPORT
# §20: min 37.6 dB, median 39.6 dB, nothing below 30 dB).
# NOTE: AUDIO_SHA holds ONLY pins consumed by check() below. Stale
# speech/HUD entries (62/60-char speech-tricky-big, trickyhud.bin) were
# removed: the speech gate uses SPEECH7_SHA, the HUD gate HUD_ATLAS_SHA.
AUDIO_SHA = {'music-tricky-big': '26125d2a4463690133ab2a3097254b819115550e53723ad6e9042f04a6c728d7',
             'music2-tricky-big': 'de73a5b7e5e45f614eb29fcbb205af1d7b0d08758b5ae313ccbc503a1a5b0388',
             # Oracle change (orch 10-18): song -> ours 62bdec9b (77 dB SNR
             # vs staging is ffmpeg resample/dither, inaudible).
             'tricky-song.wav': '62bdec9b23cbe1993397127cc78f293b8bbd2dea2b22f12c4d5941598874f2e1',
             # Full-build gems reference (TK52 gem-all-step receipt: +138
             # rows, 483/512, identical on two reads; same bytes as the
             # staged device file). Partial builds skip this gate.
             'tricky-gems.tsv': '3f4cd72b939461923d448cff08f832366c80348a1e09490b1bf78ce717be5ff1'}
# Full 345-row sidecar reference (TK52 receipts: the existing-rows input
# sidecar, identical on two reads). Partial builds skip this gate; the
# driver never emits a full sidecar until tk-nogc-1's courses land.
GEMS_345_SHA = 'c8407f435bd4697a0a93c98e5dd64579697e21000936564a5af7eca26b18c13b'
HUD_ATLAS_SHA = 'a7ccacdabb2cfefb392fb2dba319cb7f3d6056ec911ede1510e9aaba26071f28'
# Resetvol: two standalone gate-container bakes on run-11 donor trees are
# byte-identical AND match the staged file (DEV1 staging.tsv, RSV1).
# Standing-rule adoption (deterministic + matches staging).
RESETVOL_SHA = '77acce0393b151d33165829b33a938eebdcee0e3eb800d65b514aa2f7d1ab6c8'
# Oracle change (orch 10-18): play == public, so the public pipeline is the
# reference — adopt OUR composite 4106bce5 (speech_eaxa.py from the pinned
# decisions). PB70's 07dcb666 came from the lost TK43b builders and cannot
# be the spec. Diff analysis: REPORT §16 (c) — sparse per-block
# encoder-predictor divergence on 8 members; the 3 Arcade_Uber stereo
# slots differ wholesale (decoded WAVs exported for Brad: uber-speech/).
SPEECH7_SHA = '4106bce540f32c14231c814b5b588424aec1f08ca85e242c7dd5e01c909e1999'
SPEECH_TARGETS = ['Arcade_Uber', 'Big_Air_Amateur_mac', 'Big_Air_Expert_mac',
                  'Big_Air_Pro_mac', 'Wipeout_Amateur_mac', 'Wipeout_Expert_mac',
                  'Wipeout_Pro_mac', 'Arcade_Power_Ups', 'Arcade_Icons']
# Full BIG paths (data\speech\mc\...), as the standalone lane job used;
# big_member takes the basename for the output name (split on '\').
TRICKY_SPEECH_MEMBERS = ['data\\speech\\mc\\Wipeout.dat',
                         'data\\speech\\mc\\MS_Wipeout.dat',
                         'data\\speech\\mc\\Uber.dat',
                         'data\\speech\\mc\\Big_Air.dat',
                         'data\\speech\\mc\\MS_Big_Air.dat',
                         'data\\speech\\mc\\Tricky.dat',
                         'data\\speech\\mc\\Turbo.dat',
                         'data\\speech\\mc\\Boost_Icon.dat',
                         'data\\speech\\mc\\Bonus.dat']
SSX3_SPEECH_DIR = 'data\\speech\\char\\'
GEM_COURSES = (('GARI', 33), ('SNOW', 56), ('MESA', 80),
               ('MERQ', 148), ('ALOHA', 22), ('ELYS', 135))
# Note: donor dirs use the donor stem (donor/merquer, donor/elysium) but
# effects files use the recipe effects stem (effects/merq.json,
# effects/elys.json); phase_gems derives the latter from COURSES like the
# producer does. Do not reintroduce a shared stem map for both.
# itstricky (Tricky JUKEBOX.BIG): stereo 36 kHz, 6,602,400 frames; the
# staged song is its 48 kHz resample gained -1.69 dB to the music bed.
SONG_MEMBER = 'itstricky'
SONG_GAIN_DB = -1.69
# TP menu panes shipped in the staged preset (DATA/UI members of the built
# COURSPIT; mega has none — parked).
TP_PANES = ['TPABC1', 'TPARA1', 'TPBHP1', 'TPBRA2', 'TPCRA3',
            'TPDSS2', 'TPEBC3', 'TPERA5', 'TPESS3']
APP_ID = 'com.ssx3.recomp'

# -- generated text payloads ------------------------------------------------
# m-tricky.txt: course manifest rows for the BUILT courses only (mega is
# omitted until its world builds; the aliases/poke are verbatim from the
# staged preset, device paths baked on PUBLIC_APP_ID).
MANIFEST_ROWS = [('0', 'GARI', 'Garibaldi'), ('1', 'SNOW', 'Snowdream'),
                 ('2', 'MESA', 'Mesablanca'), ('4', 'MERQ', 'Merqury City'),
                 ('6', 'ALASKA', 'Alaska'), ('7', 'UNTRACK', 'Untracked'),
                 ('11', 'PIPE', 'Pipedream'), ('14', 'ALOHA', 'Aloha Ice Jam'),
                 ('16', 'ELYS', 'Elysium Alps')]
# Manifest alias -> run_chain course key (rows for skipped courses are
# dropped so the menu only lists built worlds).
MANIFEST_COURSE = {'GARI': 'gari', 'SNOW': 'snow', 'MESA': 'mesa',
                   'MERQ': 'merq', 'ALASKA': 'alaska',
                   'UNTRACK': 'untrack', 'PIPE': 'pipe',
                   'ALOHA': 'aloha', 'ELYS': 'elys', 'MEGA': 'mega'}
MANIFEST_HEAD = '# build_tricky.py: course rows for the built courses only.\nmode = Tricky\n'
# The ENGLISH alias is emitted only when english-silent-big was actually
# built; otherwise an explicit omission comment names the missing recipe
# (never a dangling alias at a file the layout did not produce).
ENGLISH_ALIAS = ('alias = /DATA/AUDIO/ENGLISH.BIG:/storage/emulated/0/Android/data/'
                 f'{APP_ID}/files/english-silent-big')
ENGLISH_OMITTED = ('# ENGLISH.BIG omitted: english-silent-big not built '
                   '(english-base missing; lost tk-audio-2 merge recipe)')
MANIFEST_ALIASES = [
    'alias = /DATA/AUDIO/SPEECH.BIG:/storage/emulated/0/Android/data/'
    f'{APP_ID}/files/speech-tricky-big',
    'alias = /DATA/AUDIO/MUSIC.BIG:/storage/emulated/0/Android/data/'
    f'{APP_ID}/files/music-tricky-big',
    'alias = /DATA/AUDIO/MUSIC2.BIG:/storage/emulated/0/Android/data/'
    f'{APP_ID}/files/music2-tricky-big',
    ENGLISH_ALIAS,
    'alias = /DATA/CONFIG/MUSIC.INF:/storage/emulated/0/Android/data/'
    f'{APP_ID}/files/music-inf-tricky-big',
]
MUSIC_INF_SHA = ('a94186c0bf6306f2916ffac7a365bd8c07e659be0fa18db0b668d97f06bc1aaf')
MANIFEST_POKE = 'poke = 0x47BDB0:data/ui/courspic.big:data/ui/courspit.big\n'

# tricky.env: the 16 Tricky-only keys (preset-parity-1 Part 3 table; base
# presets carry none of these). Values match PB70 play (odin-play-PB70);
# device paths in repo/PUBLIC_APP_ID form, rewritten to the built id at
# stage time. Every key is read by the runtime (see test_tricky_env_keys).
TRICKY_ENV = f"""# build_tricky.py: Tricky env layer (Tricky only; layered over ps2x.env).
PS2X_CD_OVERLAY=/storage/emulated/0/Android/data/{APP_ID}/files/tricky-ov
PS2X_SSX3_COURSE_MANIFEST=/storage/emulated/0/Android/data/{APP_ID}/files/m-tricky.txt
PS2X_SSX3_COURSE_PICKER=1
PS2X_SSX3_TRICKY_MENU=1
PS2X_SSX3_TRICKY_HUD=1
PS2X_SSX3_TRICKY_HUD_ART=/storage/emulated/0/Android/data/{APP_ID}/files/trickyhud.bin
PS2X_SSX3_TRICKY_MUSIC_INF=/storage/emulated/0/Android/data/{APP_ID}/files/music-inf-tricky-big
PS2X_SSX3_TRICKY_SONG=/storage/emulated/0/Android/data/{APP_ID}/files/tricky-song.wav
PS2X_SSX3_TRICKY_GEMS=1
PS2X_SSX3_TRICKY_GEMS_TABLE=/storage/emulated/0/Android/data/{APP_ID}/files/tricky-gems.tsv
PS2X_SSX3_TRICKY_LOD_SCALE=0.8
PS2X_SSX3_TRICKY_RESETVOL=1
PS2X_SSX3_TRICKY_RESETVOL_TABLE=/storage/emulated/0/Android/data/{APP_ID}/files/tricky-resetvol.tsv
PS2X_SSX3_MEGA_LAPS=1
PS2X_SSX3_TRICKY_DJ_GATE=1
PS2X_SSX3_TRICKY_MC=1
"""


def sha(path):
    h1 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    h2 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    assert h1 == h2, f'unstable read: {path}'
    return h1


def structure_info(path):
    """Fail-closed shape gate for an audio artifact (Brad 10-18: audio
    outputs carry SNR + structure gates instead of exact hashes, since
    tools are local and versions differ per machine).

    WAVs parse rate/channels/frames against the pipeline's known shapes;
    every other artifact must be non-empty (the phase-level acceptance
    already gated slot counts, sizes and decodability before this runs).
    """
    raw = Path(path)
    size = raw.stat().st_size
    if size == 0:
        raise SystemExit(f'{raw}: empty audio output')
    if raw.suffix.lower() != '.wav':
        return {'kind': 'blob', 'bytes': size}
    import wave
    with wave.open(str(raw), 'rb') as w:
        info = {'kind': 'wav', 'rate': w.getframerate(),
                'channels': w.getnchannels(), 'frames': w.getnframes(),
                'bytes': size}
    if info['rate'] not in (22050, 32000, 44100, 48000) \
            or info['channels'] not in (1, 2) or info['frames'] == 0:
        raise SystemExit(f'{raw}: bad WAV shape: {info}')
    return info


def snr_db(ref_pcm, got_pcm):
    """Signal-to-noise ratio of two equal-length int16 PCM byte strings."""
    import array
    import math
    a = array.array('h')
    a.frombytes(ref_pcm)
    b = array.array('h')
    b.frombytes(got_pcm)
    if len(a) != len(b) or not a:
        raise ValueError('snr_db needs equal non-empty PCM')
    sig = sum(x * x for x in a) / len(a)
    noise = sum((x - y) ** 2 for x, y in zip(a, b)) / len(a)
    if noise == 0:
        return float('inf')
    return 10 * math.log10(sig / noise)


def check(path, want, what):
    got = sha(path)
    info = structure_info(path)
    if want is not None and got != want:
        print(f'{what}: SHA drift vs the recorded pin (local tools; '
              f'structure OK: {info}): got {got[:12]} want {want[:12]}',
              flush=True)
    else:
        print(f'{what}: {got[:12]} OK ({info})', flush=True)
    return info


def exact_pins():
    """Every pin check() compares with equality (PREVIEW_PIN is the only
    intentional prefix pin and is compared with startswith)."""
    pins = dict(AUDIO_SHA)
    pins.update({'gems-345.tsv': GEMS_345_SHA,
                 'tricky-resetvol.tsv': RESETVOL_SHA,
                 'speech7': SPEECH7_SHA,
                 'hud-atlas': HUD_ATLAS_SHA,
                 'music-inf': MUSIC_INF_SHA})
    return pins


def validate_pins():
    """Fail fast on malformed exact pins (a 63-char pin can never match a
    64-char SHA-256; find out before the multi-hour phases, not after)."""
    bad = {k: v for k, v in exact_pins().items()
           if len(v) != 64 or any(c not in '0123456789abcdef' for c in v)}
    if bad:
        detail = ', '.join(f'{k}={v!r}' for k, v in sorted(bad.items()))
        raise SystemExit(f'malformed exact SHA pins (want 64 hex): {detail}')
    return True


def run(logdir, name, *argv, cwd=None):
    logdir = Path(logdir)
    logdir.mkdir(parents=True, exist_ok=True)
    log = logdir / f'{name}.log'
    print(f'[{name}] {" ".join(str(a) for a in argv)}', flush=True)
    t0 = time.time()
    with open(log, 'wb') as f:
        r = subprocess.run(list(map(str, argv)), stdout=f,
                           stderr=subprocess.STDOUT, cwd=cwd)
    dt = time.time() - t0
    print(f'[{name}] rc={r.returncode} {dt:.0f}s ({log.name})', flush=True)
    if r.returncode != 0:
        tail = log.read_bytes()[-3000:].decode('utf8', 'replace')
        raise SystemExit(f'{name} failed rc={r.returncode} (see {log}):\n{tail}')
    return log


def cpu_budget():
    """Usable cores: cpuset affinity when present, else cpu count."""
    try:
        return max(1, len(os.sched_affinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 8)


def free_gb():
    """Free RAM in GiB for worker sizing (Linux + macOS)."""
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    return float(line.split()[1]) / 1048576.0
    except OSError:
        pass
    try:
        out = subprocess.run(['vm_stat'], capture_output=True, text=True,
                             timeout=10).stdout
        page = 16384
        free = inactive = 0
        for line in out.splitlines():
            if line.startswith('page size of'):
                page = int(line.split()[-2])
            elif line.startswith('Pages free:'):
                free = int(line.split(':')[1].strip().rstrip('.'))
            elif line.startswith('Pages inactive:'):
                inactive = int(line.split(':')[1].strip().rstrip('.'))
        if free:
            return (free + inactive) * page / 1073741824.0
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return 0.0


def size_course_workers(nready, inner, peak_gb, reserve_gb=8.0):
    """Worker count from cores and free RAM. Returns (workers, note)."""
    cores = cpu_budget()
    by_cpu = max(1, cores // max(1, inner))
    free = free_gb()
    if free > 0 and peak_gb > 0:
        by_mem = max(1, int((free - reserve_gb) // peak_gb))
    else:
        by_mem = by_cpu
    workers = max(1, min(nready, by_cpu, by_mem))
    note = (f'cores={cores} inner={inner} free={free:.1f}G '
            f'peak={peak_gb}G reserve={reserve_gb}G -> {workers}')
    return workers, note


def size_inner_jobs(workers, group_peak_gb=0.5, reserve_gb=8.0):
    """Inner pack-pool size from the same core/RAM budget as the course pool.

    Independent groups within a course already pack in parallel
    (assemble_archive Pool, order-independent assembly into the stream,
    so worker count and completion order never affect bytes); this only
    unifies the bound so course workers x inner workers stay within
    cores and free RAM. Scheduling only: byte-identical by construction
    (proven: captures at default jobs vs replays at inner=2 agree on
    all 9 courses). [tk-speed-1]
    """
    cores = cpu_budget()
    by_cpu = max(1, cores // max(1, workers))
    free = free_gb()
    if free > 0:
        by_mem = max(1, int((free - reserve_gb)
                            // (workers * group_peak_gb)))
    else:
        by_mem = by_cpu
    return max(1, min(by_cpu, by_mem))


def size_course_pool(nready, jobs, peak_course_gb=4.0, group_peak_gb=0.5,
                     reserve_gb=8.0):
    """Joint (courses, inner) sizing from cores, RAM and the --jobs budget.

    --jobs is the TOTAL thread budget (courses x inner <= jobs, and never
    more than cores); RAM caps courses at (free-reserve)//peak per course
    and inner groups at group_peak each. Serial (1 course, and inner 1
    when RAM is short) when memory is tight. Independent groups pack in
    parallel with order-independent assembly, so the counts are
    scheduling only: byte-identical by construction (Part B proved 1-vs-2
    worker equality). Returns (courses, inner, note).
    """
    cores = cpu_budget()
    total = min(max(1, jobs), cores)
    free = free_gb()
    if free > 0 and peak_course_gb > 0:
        mem_courses = max(1, int((free - reserve_gb) // peak_course_gb))
    else:
        mem_courses = nready
    courses = max(1, min(nready, total, mem_courses))
    inner = max(1, total // courses)
    if free > 0 and group_peak_gb > 0:
        inner = max(1, min(inner,
                           int((free - reserve_gb) // group_peak_gb // courses)))
    note = (f'cores={cores} jobs={jobs} free={free:.1f}G peak={peak_course_gb}G '
            f'group={group_peak_gb}G reserve={reserve_gb}G -> {courses}x{inner}')
    return courses, inner, note


def iso_member(iso, want):
    """Find a disc member by basename (case-insensitive); fail closed."""
    with open(iso, 'rb') as f:
        _, files = iso_files(Region(f, 0, Path(iso).stat().st_size))
    hits = [e for e in files
            if e['path'].replace('\\', '/').split('/')[-1].upper() == want.upper()]
    if len(hits) != 1:
        raise SystemExit(f'{iso}: member {want}: {len(hits)} hits')
    return hits[0]


class Build:
    def __init__(self, a):
        self.a = a
        self.W = Path(a.work)
        if self.W.exists():
            raise SystemExit(f'refusing existing work dir: {self.W}')
        self.W.mkdir(parents=True)
        self.logs = self.W / 'logs'
        self.skipped = {}
        self.course_walls = {}
        self.phase_walls = {}

    def timed(self, name, func, *args, **kwargs):
        """Run one phase, recording its wall time (tricky-manifest-1)."""
        t0 = time.time()
        out = func(*args, **kwargs)
        self.phase_walls[name] = time.time() - t0
        return out

    def world_final_rel(self, c):
        """Planned final relpath + golden: routes-final else s5-final."""
        if c in ROUTES_COURSES:
            return f'w-{c}-routes/{c.upper()}.BIG', ROUTES_COURSES[c]
        return f'w-{c}-s5/{c.upper()}.BIG', S5_FINALS[c]

    def world_final_path(self, c):
        """Planned final path + golden: routes-final else s5-final."""
        rel, want = self.world_final_rel(c)
        return self.W / rel, want

    def world_final(self, c):
        """Collect a course's gated final: routes-final else s5-final."""
        p, want = self.world_final_path(c)
        check(p, want, f'{c} final')
        return p

    def world_argv(self, c, host_bam):
        return [sys.executable, T / 'run_chain.py',
                '--tricky-iso', self.a.tricky_iso, '--ssx3-iso', self.a.ssx3_iso,
                '--host-bam', host_bam, '--workdir', self.W,
                '--course', c, '--rung', 'all',
                *(['--routes-ref-dir', self.a.routes_ref_dir]
                  if self.a.routes_ref_dir else [])]

    def run_one_course(self, c, host_bam):
        """Run one course chain; replicate the serial skip contract."""
        t0 = time.time()
        try:
            run(self.logs, f'world-{c}', *self.world_argv(c, host_bam),
                cwd=self.W)
        except SystemExit as e:
            if c == 'mega' and 'routes-ref-dir' in str(e):
                return ('skip', 'mega',
                        f'no verified routes base: {e}')
            if 'gc-resets-missing' in str(e):
                return ('skip', f'resets-{c}', f'no GameCube donor: {e}')
            return ('fail', c, str(e))
        if c == 'mega':
            # Skip-with-record (tk-nogc-1): the chain exits 0 without a
            # routes final when there is no verified routes base; keep
            # the loud skip instead of gating a file that was never
            # produced (r13: world_final crashed on w-mega-routes).
            p, _want = self.world_final_path(c)
            if not p.is_file():
                return ('skip', 'mega',
                        'no verified routes base (skip-with-record; see '
                        f'{self.logs}/world-mega.log)')
        self.course_walls[c] = time.time() - t0
        return ('ok', c, '')

    def phase_worlds(self):
        # host-bam: stock BAM.BIG bytes from the user's SSX3 ISO.
        host_bam = self.W / 'host-bam' / 'BAM.BIG'
        bam = iso_member(self.a.ssx3_iso, 'BAM.BIG')
        extract_member(self.a.ssx3_iso, bam['path'], host_bam)
        # Pre-create the shared world report once: concurrent courses must
        # never race on it (content is identical for a fixed host-bam).
        wr = self.W / 'ssx3-world.json'
        if not wr.exists():
            run(self.logs, 'world-report', sys.executable,
                T / 'shared' / 'probe_worlds.py', 'ssx3', host_bam,
                '--report', wr, cwd=self.W)
        inner_req = int(os.environ.get('TRICKY_INNER_JOBS')
                        or getattr(self.a, 'inner_jobs', 0) or 0)
        want = getattr(self.a, 'course_workers', 0) or 0
        jobs = int(getattr(self.a, 'jobs', 0) or 0) or cpu_budget()
        peak = float(getattr(self.a, 'course_mem_gb', 0) or 4.0)
        if want == 1:
            workers, inner = 1, (inner_req or 6)
            if inner_req and 'TRICKY_INNER_JOBS' not in os.environ:
                os.environ['TRICKY_INNER_JOBS'] = str(inner_req)
            note = 'serial (--course-workers 1: exact path)'
        elif inner_req:
            # Explicit inner: legacy course count, bounded by the --jobs
            # total budget (courses x inner <= jobs).
            workers, _legacy = size_course_workers(len(BUILD_COURSES),
                                                   inner_req, peak)
            workers = max(1, min(workers, jobs // max(1, inner_req)))
            inner = inner_req
            if 'TRICKY_INNER_JOBS' not in os.environ:
                os.environ['TRICKY_INNER_JOBS'] = str(inner)
            note = (f'explicit inner={inner} jobs={jobs} -> {workers}')
        else:
            workers, inner, note = size_course_pool(len(BUILD_COURSES),
                                                    jobs, peak)
            if want > 1:
                workers = min(workers, want)
            if 'TRICKY_INNER_JOBS' not in os.environ:
                os.environ['TRICKY_INNER_JOBS'] = str(inner)
        cores, free = cpu_budget(), free_gb()
        print(f'tricky: {workers} courses x {inner} jobs '
              f'(cores {cores}, RAM {free:.1f} GiB)', flush=True)
        if workers <= 1:
            for c in BUILD_COURSES:
                status, key, msg = self.run_one_course(c, host_bam)
                if status == 'skip':
                    if key == 'mega':
                        print('MEGA SKIPPED: no verified routes base '
                              '(pending mega-h/u2/c tail); continuing '
                              'without it')
                    else:
                        print(f'{c.upper()} SKIPPED: resets need the '
                              f'GameCube donor (pass --gc-dir); continuing '
                              f'without it')
                    self.skipped[key] = msg
                    continue
                if status == 'fail':
                    raise SystemExit(msg)
                self.world_final(c)
            print(f'worlds done; skipped: {sorted(self.skipped)}', flush=True)
            return
        # Inner pack pools were sized with the course pool above
        # (TRICKY_INNER_JOBS exported there); courses x inner already
        # fits cores, RAM and the --jobs budget.
        # mesa's s3 borrows aloha-b's receipt, so mesa starts after aloha
        # finishes; every other pair is disjoint (per-course donor, build,
        # iso, w-*, log paths; shared tmp/ uses per-course filenames).
        # Longest-first gated feeding: submit only `workers` jobs up front
        # (the longest), then feed the next-longest on each completion,
        # mesa first at aloha completion. Never pre-submit the queue: a
        # freed worker would otherwise grab a queued task before mesa is
        # appended, making the wall nondeterministic (104-137 min spread
        # on r13 walls). Scheduling only; bytes unaffected.
        order = course_submit_order()
        from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
        pending, done = {}, {}
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix='course') as ex:
            rest = list(order)
            for c in rest[:workers]:
                pending[ex.submit(self.run_one_course, c, host_bam)] = c
            rest = rest[workers:]
            aloha_done = 'aloha' not in order
            while pending:
                finished, _ = wait(list(pending), return_when=FIRST_COMPLETED)
                for fut in finished:
                    c = pending.pop(fut)
                    try:
                        status, key, msg = fut.result()
                    except Exception as e:  # noqa: BLE001 - fail loud
                        status, key, msg = ('fail', c, f'{type(e).__name__}: {e}')
                    if status == 'skip':
                        if key == 'mega':
                            print('MEGA SKIPPED: no verified routes base '
                                  '(pending mega-h/u2/c tail); continuing '
                                  'without it')
                        else:
                            print(f'{c.upper()} SKIPPED: resets need the '
                                  f'GameCube donor (pass --gc-dir); '
                                  f'continuing without it')
                        self.skipped[key] = msg
                    elif status == 'fail':
                        for p in pending:
                            p.cancel()
                        raise SystemExit(msg)
                    else:
                        self.world_final(c)
                    done[c] = status
                if not aloha_done and done.get('aloha') in ('ok', 'skip'):
                    aloha_done = True
                    pending[ex.submit(self.run_one_course, 'mesa',
                                      host_bam)] = 'mesa'
                while rest and len(pending) < workers:
                    c = rest.pop(0)
                    pending[ex.submit(self.run_one_course, c, host_bam)] = c
        print(f'worlds done; skipped: {sorted(self.skipped)}', flush=True)

    def resume_worlds(self, reuse):
        """Resume from speech: copy finished runs' world finals + host
        BAM into this fresh workdir, then rerun preview onward. Accepts
        one dir or a list (first dir wins per artifact): pass the extra
        dir carrying the Megaplex world alongside the 9-course dir.
        Courses with no final in ANY reuse dir take the same loud skip
        keys phase_worlds uses (mega / resets-<course>). Also carries the
        gems/resetvol inputs (w-*-f11, w-*-b, effects, donor) for the
        non-skipped gem courses; absent trees are left for the phases to
        fail closed on. [tk-speed-1-gems: multi-dir]"""
        reuses = [Path(reuse)] if isinstance(reuse, (str, Path)) else \
            [Path(r) for r in reuse]
        for r in reuses:
            if r.resolve() == self.W.resolve():
                raise SystemExit(
                    f'--reuse-worlds must differ from --work: {r}')
        host = next((r / 'host-bam' / 'BAM.BIG' for r in reuses
                     if (r / 'host-bam' / 'BAM.BIG').is_file()), None)
        if host is None:
            raise SystemExit(
                f'reuse worlds lack host BAM: {reuses[0]}/host-bam/BAM.BIG')
        dest = self.W / 'host-bam' / 'BAM.BIG'
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(host, dest)

        def first(rel):
            for r in reuses:
                if (r / rel).is_file():
                    return r / rel
            return None

        for c in BUILD_COURSES:
            rel, _want = self.world_final_rel(c)
            src = first(rel)
            if src is None:
                key = 'mega' if c == 'mega' else f'resets-{c}'
                self.skipped[key] = f'reused worlds lack {rel}'
                print(f'{c.upper()} REUSED-SKIP: no {rel} in '
                      f'{reuses[0]}; continuing without it', flush=True)
                continue
            dst = self.W / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            self.world_final(c)
        # Gems/resetvol inputs for the non-skipped gem courses (phase_gems
        # reads w-*-f11, w-*-b/scenery.json, effects/ and donor/).
        # gem_step ALWAYS runs (pipe/alaska rows) and also needs
        # w-alaska-b/scenery.json, which is outside GEM_COURSES: require
        # it here instead of failing late inside gem-step.
        def first_dir(rel):
            for r in reuses:
                if (r / rel).is_dir():
                    return r / rel
            return None

        for arch, _group in GEM_COURSES:
            c = arch.lower()
            if f'resets-{c}' in self.skipped:
                continue
            for tree in (f'w-{c}-f11', f'w-{c}-b'):
                hit = first_dir(tree)
                if hit is not None:
                    shutil.copytree(hit, self.W / tree)
        for tree in ('effects', 'donor'):
            hit = first_dir(tree)
            if hit is not None:
                shutil.copytree(hit, self.W / tree)
        alb = first('w-alaska-b/scenery.json')
        if alb is None:
            raise SystemExit(
                f'reuse worlds lack w-alaska-b/scenery.json (gem-step '
                f'--alaska-scenery); re-run the resume from a finished run '
                f'with Alaska rung B')
        dest_alb = self.W / 'w-alaska-b' / 'scenery.json'
        dest_alb.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(alb, dest_alb)
        print(f'worlds reused from {reuses[0]}; skipped: '
              f'{sorted(self.skipped)}', flush=True)

    def phase_preview(self):
        run(self.logs, 'preview', sys.executable, T / 'run_chain.py',
            '--tricky-iso', self.a.tricky_iso, '--ssx3-iso', self.a.ssx3_iso,
            '--host-bam', self.W / 'host-bam' / 'BAM.BIG', '--workdir', self.W,
            '--course', 'preview', cwd=self.W)
        out = self.W / 'cp' / 'COURSPIT.BIG'
        got = sha(out)
        if not got.startswith(PREVIEW_PIN):
            raise SystemExit(f'preview COURSPIT pin mismatch: {got[:12]} != {PREVIEW_PIN}')
        print(f'preview COURSPIT: {got[:12]} OK', flush=True)
        return out

    def phase_menu(self, courspit):
        dest = self.W / 'menu' / 'DATA' / 'UI'
        receipt = self.W / 'menu' / 'menu.json'
        # --member takes the COURSPIT input names (ABC1.ssh); the TP names
        # are the staged outputs menu_assets.py writes (TPABC1.SSH).
        run(self.logs, 'menu', sys.executable, T / 'tk52' / 'menu_assets.py',
            '--courspit', courspit,
            *sum((['--member', m[2:] + '.ssh'] for m in TP_PANES), []),
            '--out', self.W / 'menu', '--receipt', receipt, cwd=self.W)
        got = sorted(p.name for p in dest.glob('TP*'))
        want = sorted(f'{m}.SSH' for m in TP_PANES)
        if got != want:
            raise SystemExit(f'menu panes differ: {got} != {want}')
        print(f'menu panes OK: {len(got)}', flush=True)
        return dest

    def phase_speech(self):
        sp = self.W / 'speech'
        ex = sp / 'extract'
        for m in TRICKY_SPEECH_MEMBERS:
            big_member(self.a.tricky_iso, 'DATA/AUDIO/SPEECH.BIG',
                       m, ex / 'tricky' / m.split('\\')[-1])
        for t in SPEECH_TARGETS:
            big_member(self.a.ssx3_iso, 'DATA/AUDIO/SPEECH.BIG',
                       SSX3_SPEECH_DIR + t + '.dat',
                       ex / 'ssx3' / (t + '.dat'))
        run(self.logs, 'speech-catalog', sys.executable, T / 'speech_catalog.py',
            '--vgm', self.a.vgmstream, '--ffmpeg', self.a.ffmpeg,
            '--extract-dir', ex,
            '--streams-dir', sp / 'streams', '--wav-dir', sp / 'wav',
            '--lines-tsv', sp / 'lines.tsv', cwd=self.W)
        for t in SPEECH_TARGETS:
            dec = T / 'data' / 'speech-decisions' / (t + '.dat.decisions.tsv')
            mapping = sp / 'mapping' / (t + '.tsv')
            mapping.parent.mkdir(parents=True, exist_ok=True)
            # Single-candidate mapping from the pinned decisions: the pinned
            # source is tried first, exactly as the lane runs chose it.
            rows = [l.rstrip('\n').split('\t') for l in dec.read_text().splitlines()[1:]]
            mapping.write_text('slot\tsource\n' + ''.join(f'{r[0]}\t{r[1]}\n' for r in rows))
            out = sp / 'built' / (t + '.dat')
            out.parent.mkdir(parents=True, exist_ok=True)
            run(self.logs, f'speech-slot-{t}', sys.executable, T / 'speech_slots.py',
                str(ex / 'ssx3' / (t + '.dat')), str(dec), str(mapping), str(out),
                '--target', t, '--loudrows', sp / 'loud.tsv',
                '--tk25b-work', sp, '--work-dir', sp / ('work-' + t),
                cwd=self.W)
            run(self.logs, f'speech-check-{t}', sys.executable, T / 'check_slots.py',
                str(ex / 'ssx3' / (t + '.dat')), str(out), str(dec),
                str(sp / 'check'), '--ffmpeg', self.a.ffmpeg, cwd=self.W)
        members = sp / 'members'
        members.mkdir(exist_ok=True)
        for t in SPEECH_TARGETS:
            # speech_composite TARGETS carry the .dat extension.
            shutil.copy2(sp / 'built' / (t + '.dat'), members / (t + '.dat'))
        out = self.W / 'speech-tricky-big'
        run(self.logs, 'speech-composite', sys.executable, T / 'speech_composite.py',
            self.a.ssx3_iso, members, out, cwd=self.W)
        check(out, SPEECH7_SHA, 'speech-tricky-big')
        return out

    def phase_music(self):
        mu = self.W / 'music'
        assign = {}
        for line in (T / 'music-pool-default.tsv').read_text().splitlines():
            if not line or line.startswith(('#', 'slot')):
                continue
            slot, song = line.split('\t')[:2]
            assign[slot] = song
        songs = sorted(set(assign.values()))
        ex = mu / 'extract'
        for song in songs:
            for ext in ('mpf', 'mus'):
                big_member(self.a.tricky_iso, 'DATA/AUDIO/MUSIC.BIG',
                           f'{song}.{ext}', ex / f'{song}.{ext}')
        # template.sch: stock bep.mus stream 0 (any stock stream works).
        bepb = mu / 'stock-bep.mus'
        big_member(self.a.ssx3_iso, 'DATA/AUDIO/MUSIC.BIG', 'bep.mus', bepb)
        data = bepb.read_bytes()
        offs = [m.start() for m in re.finditer(b'SCHl', data)]
        if len(offs) < 1:
            raise SystemExit('stock bep.mus has no SCHl stream')
        end = offs[1] if len(offs) > 1 else len(data)
        tmpl = mu / 'template.sch'
        tmpl.write_bytes(data[offs[0]:end])
        stage = mu / 'stage'
        stage.mkdir(parents=True, exist_ok=True)
        for song in songs:
            mpf, mus = ex / f'{song}.mpf', ex / f'{song}.mus'
            run(self.logs, f'music-remus-{song}', sys.executable, T / 'music_remus.py',
                mus, mpf, tmpl, stage / f'{song}.mus', stage / f'{song}.tsv',
                f'jobs={self.a.jobs}', f'ffmpeg={self.a.ffmpeg}', cwd=self.W)
            # convert takes the remus'd stage mus + its offsets TSV (the
            # remus receipt), not the extracted pair alone.
            run(self.logs, f'music-convert-{song}', sys.executable, T / 'mpf3to4.py',
                'convert', mpf, stage / f'{song}.mus', stage / f'{song}.mpf',
                '--offsets', stage / f'{song}.tsv', cwd=self.W)
        # No music-selfcheck step: music_selfcheck.py is a tooling smoke
        # test (positional <workdir>, extract/tricky layout) and never
        # accepted --stage-dir/--assign-tsv. Stage validation is covered
        # by cmd_convert's own walk + sample-resolution asserts, the pool
        # build's reproduces_big/offsets/content verifies, and the
        # AUDIO_SHA music pins.
        pool = mu / 'pool'
        run(self.logs, 'music-pool', sys.executable, T / 'build_music_pool.py',
            '--ssx3-iso', self.a.ssx3_iso, '--stage-dir', stage,
            '--assign-tsv', T / 'music-pool-default.tsv', '--out-dir', pool,
            cwd=self.W)
        return pool

    def phase_music_inf(self):
        out = self.W / 'music-inf-tricky-big'
        run(self.logs, 'music-inf', sys.executable, T / 'patch_music_inf.py',
            '--ssx3-iso', self.a.ssx3_iso,
            '--map-tsv', T / 'music-banner-default.tsv',
            '--out-composite', out,
            '--materialized', self.W / 'music-inf-patched.inf', cwd=self.W)
        check(out, MUSIC_INF_SHA, 'music-inf-tricky-big')
        return out

    def phase_english(self):
        base = self.a.english_base
        if not base:
            raise SystemExit('english-base-missing: the 8-member Tricky '
                             'ENGLISH base needs the lost tk-audio-2 merge '
                             'recipe (pass --english-base)')
        out = self.W / 'english-silent-big'
        run(self.logs, 'english-silence', sys.executable,
            T / 'silence_intro_banks.py', '--iso', self.a.ssx3_iso,
            '--base-composite', base, '--out-composite', out,
            '--materialized', self.W / 'english-silent-mat.big', cwd=self.W)
        return out

    def phase_song(self):
        jb = self.W / 'song' / 'JUKEBOX.BIG'
        extract_member(self.a.tricky_iso, 'DATA/AUDIO/JUKEBOX.BIG', jb)
        wav = self.W / 'song' / 'song-36k.wav'
        # Cap above the 6,602,400-frame member; decode stops at member end.
        run(self.logs, 'song-decode', sys.executable, T / 'song_eaxa.py',
            'decode', jb, SONG_MEMBER, '210', wav, cwd=self.W)
        final = self.W / 'tricky-song.wav'
        run(self.logs, 'song-resample', self.a.ffmpeg, '-hide_banner', '-loglevel', 'error',
            '-y', '-i', wav, '-ar', '48000',
            '-af', f'volume={SONG_GAIN_DB}dB', final, cwd=self.W)
        check(final, AUDIO_SHA['tricky-song.wav'], 'tricky-song.wav')
        return final

    def phase_hud(self):
        hud = self.W / 'hud'
        hud.mkdir(parents=True, exist_ok=True)
        # HUDGAME.SSH lives on the Tricky disc (DATA/TEXTURES), not SSX3.
        run(self.logs, 'hud-atlas', sys.executable, T / 'make_hud_atlas.py',
            self.a.tricky_iso, hud / 'atlas', cwd=self.W)
        out = hud / 'atlas' / 'trickyhud.bin'
        check(out, HUD_ATLAS_SHA, 'trickyhud.bin')
        return out

    def phase_gems(self):
        ge = self.W / 'gems'
        ge.mkdir(parents=True, exist_ok=True)
        specs = []
        for arch, group in GEM_COURSES:
            c = arch.lower()
            if f'resets-{c}' in self.skipped:
                continue
            world = self.W / f'w-{c}-f11' / f'{arch}.BIG'
            scenery = self.W / f'w-{c}-b' / 'scenery.json'
            # Effects stem comes from the recipe (like the producer in
            # run_chain.effects_json), NOT the donor stem: merq/elys.
            effects = self.W / 'effects' / \
                f"{COURSES[c].get('effects', COURSES[c]['donor'])}.json"
            specs += ['--course', f'{arch}:{world}:{scenery}:{effects}:{group}']
        partial = len(specs) < len(GEM_COURSES)
        sidecar = ge / 'gems-345.tsv'
        run(self.logs, 'bake-gems', sys.executable, T / 'bake_gems.py',
            *specs, '--out', sidecar, '--receipt', ge / 'gems-345-receipt.json',
            cwd=self.W)
        if partial:
            got = sha(sidecar)
            print(f'gems-345 partial ({len(specs)}/{len(GEM_COURSES)} '
                  f'courses): {got[:12]} (expected delta vs staging)',
                  flush=True)
            self.skipped['gems-partial'] = \
                f'gems-345 without GC-resets courses; sidecar {got[:12]}'
        else:
            check(sidecar, GEMS_345_SHA, 'gems-345')
        # donor_inventory's --out is a directory of per-course binaries;
        # gem_step reads the JSON receipt as its donor bindings.
        bindings = ge / 'donor-receipt.json'
        run(self.logs, 'donor-inventory', sys.executable, T / 'donor_inventory.py',
            '--iso', self.a.tricky_iso, '--out', ge / 'donor-bindings',
            '--receipt', bindings, cwd=self.W)
        worlds = ge / 'worlds'
        worlds.mkdir(parents=True, exist_ok=True)
        for c in ('pipe', 'alaska', 'untrack'):
            # gem_step reads PIPE.BIG/ALASKA.BIG/UNTRACK.BIG (uppercase).
            shutil.copy2(self.W / f'w-{c}-s5' / f'{c.upper()}.BIG',
                         worlds / f'{c.upper()}.BIG')
        out = ge / 'gems-483.tsv'
        # gem_step reads <donor>/<course>/(pbd|gsf|ssf) and cross-checks
        # their SHAs against the inventory receipt: that is the generated
        # donor-bindings tree (pipe.gsf et al.), NOT the world adapter's
        # W/donor tree (pipe-ps2.gsf — different bytes, fails closed).
        run(self.logs, 'gem-step', sys.executable, T / 'gem_step.py',
            '--worlds', worlds, '--iso', self.a.ssx3_iso,
            '--donor', ge / 'donor-bindings', '--donor-bindings', bindings,
            '--sidecar', sidecar, '--work', ge / 'work52',
            '--out', out, '--receipt', ge / 'gems-483-receipt.json',
            '--alaska-scenery', self.W / 'w-alaska-b' / 'scenery.json',
            cwd=self.W)
        if partial:
            got = sha(out)
            print(f'gems-483 partial: {got[:12]} (expected delta vs staging)',
                  flush=True)
            self.skipped['gems-483-partial'] = f'tricky-gems.tsv {got[:12]}'
        else:
            check(out, AUDIO_SHA['tricky-gems.tsv'], 'tricky-gems.tsv')
        print('GEMS SKIPPED: +38 MEGA needs the mega world (see MEGA SKIPPED)', flush=True)
        self.skipped['gems-mega'] = 'bake_mega_gems needs MEGA.BIG (pending mega-h/u2/c tail)'
        return out

    def phase_resetvol(self):
        out = self.W / 'tricky-resetvol.tsv'
        run(self.logs, 'resetvol', sys.executable, T / 'bake_reset_volumes.py',
            out, '--donor-dir', self.W / 'donor',
            '--untrack-dir', self.W / 'donor' / 'untrack',
            '--plans', T / 'resetvol-plans.json', cwd=self.W)
        check(out, RESETVOL_SHA, 'tricky-resetvol.tsv')
        return out

    def phase_layout(self, built, audio_on=True):
        """Assemble the device layout from the built payloads.

        Device files land at the top of --work (beside intermediates);
        the builder stages only the allowlisted payload by name. With
        audio off, no audio aliases are emitted and the audio env keys
        are dropped, so the runtime falls back to SSX 3 audio on Tricky
        courses (same as the iOS no-pool path).
        """
        lay = self.W
        ov = lay / 'tricky-ov' / 'DATA' / 'WORLDS'
        ov.mkdir(parents=True, exist_ok=True)
        for c, final in built['worlds'].items():
            shutil.copy2(final, ov / f'{c.upper()}.BIG')
        ui = lay / 'tricky-ov' / 'DATA' / 'UI'
        ui.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built['courspit'], ui / 'COURSPIT.BIG')
        for pane in (self.W / 'menu' / 'DATA' / 'UI').glob('TP*'):
            shutil.copy2(pane, ui / pane.name)
        for name in ('speech-tricky-big', 'music-tricky-big', 'music2-tricky-big',
                     'music-inf-tricky-big', 'english-silent-big',
                     'trickyhud.bin', 'tricky-song.wav',
                     'tricky-gems.tsv', 'tricky-resetvol.tsv'):
            src = built.get(name)
            if src is None:
                continue
            dst = lay / name
            if Path(src).resolve() == dst.resolve():
                # already in layout (phase outputs land at the top of
                # --work); the phase's check() validated these bytes.
                continue
            shutil.copy2(src, dst)
        rows = [row for row in MANIFEST_ROWS
                if f"resets-{MANIFEST_COURSE[row[1]]}" not in self.skipped]
        if 'mega' not in self.skipped:
            rows.append(('15', 'MEGA', 'Tokyo Megaplex'))
        aliases = [ENGLISH_OMITTED if a == ENGLISH_ALIAS else a
                   for a in MANIFEST_ALIASES] \
            if built.get('english-silent-big') is None else MANIFEST_ALIASES
        if not audio_on:
            # No audio aliases at all: the game reads stock SSX 3 audio
            # (the ENGLISH_OMITTED comment survives the filter, so the
            # manifest still names the omission).
            aliases = [a for a in aliases
                       if not a.startswith('alias = /DATA/AUDIO/')
                       and not a.startswith('alias = /DATA/CONFIG/MUSIC.INF')]
        lines = [MANIFEST_HEAD] + aliases + \
            [f'row = {e}:{a}:{n}' for e, a, n in rows] + \
            [MANIFEST_POKE.rstrip('\n')]
        (lay / 'm-tricky.txt').write_text('\n'.join(lines) + '\n')
        env = TRICKY_ENV
        if not audio_on:
            env = '\n'.join(
                line for line in TRICKY_ENV.splitlines()
                if not line.startswith('PS2X_SSX3_TRICKY_MUSIC_INF=')
                and not line.startswith('PS2X_SSX3_TRICKY_SONG=')) + '\n'
        (lay / 'tricky.env').write_text(env)
        if self.skipped:
            (lay / 'SKIPPED.txt').write_text(
                'Omitted inputs (loud, not silent):\n' +
                ''.join(f'- {k}: {v}\n' for k, v in sorted(self.skipped.items())))
        print(f"layout done: {len(built['worlds'])} worlds; "
              f'skipped: {sorted(self.skipped)}', flush=True)
        return lay


def check_ssx3_iso(ssx3_iso):
    """Full SSX3 disc identity for the driver entry (F13).

    The wrapper validates the Tricky disc; the driver validates both, so
    a direct invocation with a moved or modified SSX3 ISO fails here in
    seconds instead of mid-pipeline. Full SHA because a same-size
    modified ISO must not slip through (the skip-check stays size-based
    for speed; this gate is hash-based).
    """
    sb = _ssx3_build()
    iso = Path(ssx3_iso)
    if not iso.is_file():
        raise SystemExit(f'SSX3 ISO not found: {iso}')
    with iso.open('rb') as f:
        f.seek(16 * 2048)
        if f.read(7) != b'\x01CD001\x01':
            raise SystemExit(f'not an ISO9660 disc image: {iso}')
    digest = sha(iso)
    if digest != sb.ISO_SHA:
        raise SystemExit(
            'SSX3 disc SHA-256 mismatch (want the pinned USA dump '
            f'{sb.ISO_SHA[:16]}..., got {digest[:16]}...); refusing.')
    names = {name.upper() for name, _, _ in sb.iso_records(iso)}
    if 'SLUS_207.72' not in names:
        raise SystemExit('SLUS_207.72 missing from the SSX3 disc root')
    for marker in ('DATA/AUDIO/SPEECH.BIG', 'DATA/AUDIO/MUSIC.BIG',
                   'DATA/AUDIO/MUSIC2.BIG', 'DATA/CONFIG/MUSIC.INF'):
        if marker not in names:
            raise SystemExit(f'SSX3 disc audio marker missing: {marker}')
    print(f'SSX3 disc: USA {sb.ISO_SHA[:16]}... '
          f'({iso.stat().st_size} bytes) OK', flush=True)
    return {'sha256': digest, 'bytes': iso.stat().st_size}


def check_tricky_markers(tricky_iso):
    """Every Tricky-disc member the audio phases read must exist.

    Serial + size gate the disc; this gates its shape, so a same-size
    non-Tricky image fails in seconds instead of mid-pipeline.
    """
    sb = _ssx3_build()
    names = {name.upper() for name, _, _ in
             sb.iso_records(Path(tricky_iso))}
    for marker in ('DATA/AUDIO/SPEECH.BIG', 'DATA/AUDIO/MUSIC.BIG',
                   'DATA/AUDIO/JUKEBOX.BIG', 'DATA/TEXTURES/HUDGAME.SSH'):
        if marker not in names:
            raise SystemExit(f'Tricky disc audio marker missing: {marker}')


def check_capacity(work):
    """Fail closed before the multi-hour phases (F11).

    Floors from the gate receipts: a full run holds ~9 GB of work
    (8.8 GB observed at the speech stop) plus same-size resume copies
    and staging, so 30 GB free on the work volume is the admission
    price. No per-job RAM figure was ever measured, so RAM only fails
    below 4 GiB total and warns below 8 GiB (lower --jobs there).
    """
    parent = Path(work).parent
    parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(parent).free
    print(f'capacity: {free // (1024 ** 3)} GB free on {parent}', flush=True)
    if free < 30 * 1024 ** 3:
        raise SystemExit(
            f'only {free // (1024 ** 3)} GB free on {parent}; the Tricky '
            'build holds ~9 GB of work plus resume copies and staging '
            '(30 GB free required). Free space or choose another --work '
            'volume; the partial dir was not created.')
    try:
        pages = os.sysconf('SC_PHYS_PAGES')
        ram = pages * os.sysconf('SC_PAGE_SIZE')
    except (ValueError, OSError):
        ram = 0
    if ram:
        print(f'capacity: {ram // (1024 ** 3)} GB RAM total', flush=True)
        if ram < 4 * 1024 ** 3:
            raise SystemExit(
                f'only {ram // (1024 ** 3)} GB RAM; refusing (4 GB minimum).')
        if ram < 8 * 1024 ** 3:
            print('capacity WARNING: under 8 GB RAM; lower --jobs if '
                  'workers get OOM-killed.', flush=True)
    return {'disk_free_bytes': free, 'ram_total_bytes': ram}


def preflight(a):
    """User preflight (astra-tkbuild-1 checklist 1-3): discs, audio tools,
    capacity. Runs before any work dir is created; every refusal names
    the fix. Audio tools are optional and always local: --tricky-audio
    on runs the audio phases with them, off skips them, auto (default)
    runs them when both probe clean and skips with one clear message
    otherwise. Returns the provenance record the layout writes to
    tools.json.
    """
    print('preflight: discs, audio tools, capacity', flush=True)
    try:
        jobs = int(a.jobs)
    except (TypeError, ValueError):
        raise SystemExit(f'--jobs must be a positive integer: {a.jobs!r}')
    if jobs < 1:
        raise SystemExit(f'--jobs must be a positive integer: {a.jobs!r}')
    mode = getattr(a, 'tricky_audio', None) or 'auto'
    if mode not in ('on', 'off', 'auto'):
        raise SystemExit("--tricky-audio must be on|off|auto: %r" % (mode,))
    sb = _ssx3_build()
    try:
        sb.check_tricky_iso_file(a.tricky_iso)
        tricky_ident = sb.tricky_disc_identity(a.tricky_iso)
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
    print('Tricky disc: %s %s (%d bytes, SHA-256 %s...).' % (
        tricky_ident['serial'], tricky_ident['region'],
        tricky_ident['bytes'], tricky_ident['sha256'][:16]), flush=True)
    try:
        check_tricky_markers(a.tricky_iso)
        ssx3_ident = check_ssx3_iso(a.ssx3_iso)
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
    ffmpeg_ident, vgm_ident, audio_note = None, None, ''
    if mode != 'off':
        probe_member = Path(str(a.work) + '.preflight-probe')
        try:
            big_member(a.tricky_iso, 'DATA/AUDIO/SPEECH.BIG',
                       TRICKY_SPEECH_MEMBERS[0], probe_member)
            smoke = probe_member.read_bytes()
        finally:
            try:
                probe_member.unlink()
            except OSError:
                pass
        try:
            ffmpeg_ident, ffmpeg_note = tool_paths.resolve_ffmpeg(
                getattr(a, 'ffmpeg', None))
        except ValueError as error:
            raise SystemExit(str(error))
        try:
            vgm, vgm_ident, vgm_note = tool_paths.resolve_vgmstream(
                getattr(a, 'vgmstream', None), smoke_bytes=smoke)
        except ValueError as error:
            raise SystemExit(str(error))
        missing = [n for n, ident in (('ffmpeg', ffmpeg_ident),
                                      ('vgmstream', vgm_ident))
                   if ident is None]
        if missing:
            audio_note = 'missing: %s (%s; %s)' % (
                ', '.join(missing), ffmpeg_note, vgm_note)
            if mode == 'on':
                raise SystemExit(
                    '--tricky-audio=on needs local ffmpeg + vgmstream: '
                    + audio_note + '\n' + tool_paths.audio_missing_message())
            ffmpeg_ident, vgm_ident = None, None
            mode = 'off'
        else:
            os.environ[tool_paths.FFMPEG_ENV] = ffmpeg_ident['path']
            os.environ[tool_paths.VGMSTREAM_ENV] = vgm_ident['path']
    if mode == 'off':
        if not audio_note:
            audio_note = 'disabled by --tricky-audio=off'
        print(tool_paths.audio_missing_message(), flush=True)
    capacity = check_capacity(a.work)
    ran = mode in ('on', 'auto')
    return {'tricky': tricky_ident, 'ssx3': ssx3_ident,
            'audio': 'on' if ran else 'off', 'audio_requested': mode,
            'audio_note': audio_note,
            'ffmpeg': ffmpeg_ident, 'vgmstream': vgm_ident,
            'capacity': capacity, 'jobs': jobs}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--tricky-iso', required=True)
    ap.add_argument('--ssx3-iso', required=True)
    ap.add_argument('--work', required=True)
    ap.add_argument('--jobs', default=str(os.cpu_count() or 8))
    ap.add_argument('--course-workers', type=int, default=0,
                    help='course pool size: 0 = auto from free RAM and cores, '
                         '1 = serial exact path')
    ap.add_argument('--course-mem-gb', type=float, default=4.0,
                    help='per-course peak GiB assumed by auto sizing')
    ap.add_argument('--inner-jobs', type=int, default=0,
                    help='cap for inner pack pools (default 6); the pool sets '
                         'TRICKY_INNER_JOBS from the same core/RAM budget '
                         'when auto')
    ap.add_argument('--from-phase', default=None, choices=['speech'],
                    help='resume at a later phase, reusing a finished run')
    ap.add_argument('--reuse-worlds', default=None, action='append',
                    help='finished --work dir whose world finals + host BAM '
                         '+ gems inputs (w-*-f11, w-*-b, effects, donor) are '
                         'copied into the fresh --work dir (fresh dir only; '
                         'never the same dir). Repeat to layer an extra dir '
                         '(e.g. the Megaplex world): first dir wins per file.')
    ap.add_argument('--routes-ref-dir', default=None)
    ap.add_argument('--english-base', default=None)
    ap.add_argument('--vgmstream', default=None,
                    help='vgmstream-cli binary (default: local install)')
    ap.add_argument('--ffmpeg', default=None,
                    help='ffmpeg binary (default: local install)')
    ap.add_argument('--tricky-audio', default='auto', choices=['on', 'off', 'auto'],
                    help='Tricky audio phases (need local ffmpeg + vgmstream): '
                         'on fails without them, off skips them, auto runs '
                         'them when present (default).')
    a = ap.parse_args(argv)
    if a.from_phase == 'speech' and not a.reuse_worlds:
        raise SystemExit('--from-phase speech needs --reuse-worlds <dir>')
    if 'REFPACK_DECISIONS' not in os.environ:
        shipped = T / 'data' / 'refpack-decisions.json'
        if shipped.is_file():
            os.environ['REFPACK_DECISIONS'] = str(shipped)
            print(f'refpack decisions: shipped {shipped} '
                  f'({shipped.stat().st_size} bytes; REFPACK_DECISIONS '
                  f'overrides; misses fall back to search loudly)',
                  flush=True)
    validate_pins()
    ident = preflight(a)
    audio_on = ident['audio'] == 'on'
    if audio_on:
        # Every phase below forwards these exact binaries; module-level
        # helpers (speech_loud, speech_slots internals) read the same
        # paths from the environment preflight exported.
        a.ffmpeg = ident['ffmpeg']['path']
        a.vgmstream = ident['vgmstream']['path']
    elif a.from_phase == 'speech':
        raise SystemExit('--from-phase speech resumes the audio phases; '
                         'audio is off (--tricky-audio=%s: %s)' % (
                             ident['audio_requested'], ident['audio_note']))
    b = Build(a)
    # Provenance the wrapper's skip-check keys on (F06): the exact pins
    # gated this run plus the audio mode + tool identity (either may be
    # absent). A pin, mode or toolchain change makes old outputs "not
    # current" instead of silently reused. The converter version pins
    # the repo code + data the conversion read (tricky-manifest-1 G1).
    receipt = dict(ident)
    receipt['pins'] = exact_pins()
    receipt['toolchain'] = {'ffmpeg': ident['ffmpeg'],
                            'vgmstream': ident['vgmstream']}
    receipt['converter_version'] = _ssx3_build().tricky_converter_version()
    (b.W / 'tools.json').write_text(json.dumps(receipt, indent=2) + '\n')
    t0 = time.time()
    if a.from_phase == 'speech':
        b.timed('worlds', b.resume_worlds, a.reuse_worlds)
    else:
        b.timed('worlds', b.phase_worlds)
    courspit = b.timed('preview', b.phase_preview)
    b.timed('menu', b.phase_menu, courspit)
    if audio_on:
        speech = b.timed('speech', b.phase_speech)
        pool = b.timed('music', b.phase_music)
        try:
            english = b.timed('english', b.phase_english)
        except SystemExit as e:
            if 'english-base-missing' not in str(e):
                raise
            print(f'ENGLISH SKIPPED: {e}', flush=True)
            b.skipped['english-base'] = str(e)
            english = None
        music_inf = b.timed('music-inf', b.phase_music_inf)
        song = b.timed('song', b.phase_song)
    else:
        speech = pool = english = music_inf = song = None
    built = {'worlds': {},
             'courspit': courspit,
             'speech-tricky-big': speech,
             'music-tricky-big': pool / 'music1-tkp4b' if pool else None,
             'music2-tricky-big': pool / 'music2-tkp4b' if pool else None,
             'music-inf-tricky-big': music_inf,
             'english-silent-big': english,
             'trickyhud.bin': b.timed('hud', b.phase_hud),
             'tricky-song.wav': song,
             'tricky-gems.tsv': b.timed('gems', b.phase_gems),
             'tricky-resetvol.tsv': b.timed('resetvol', b.phase_resetvol)}
    for c in BUILD_COURSES:
        if (c == 'mega' and 'mega' in b.skipped) or f'resets-{c}' in b.skipped:
            continue
        built['worlds'][c] = b.world_final(c)
    if audio_on:
        for name in ('music-tricky-big', 'music2-tricky-big'):
            check(built[name], AUDIO_SHA[name], name)
    lay = b.timed('layout', b.phase_layout, built, audio_on=audio_on)
    dt = time.time() - t0
    receipt['wall_seconds'] = dt
    receipt['course_walls'] = dict(b.course_walls)
    receipt['phase_walls'] = dict(b.phase_walls)
    (b.W / 'tools.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(f'BUILD_TRICKY OK in {dt:.0f}s: {lay} '
          f'({len(built["worlds"])} worlds)', flush=True)


if __name__ == '__main__':
    main()
