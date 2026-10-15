#!/usr/bin/env python3
"""Det baselines: store one control run per engine pin, boot only candidates.

Store: <store>/<key>/ with manifest.json (pins + per-file
SHAs). Shipped keys live in tests/det/keys (comparison artifacts only:
manifest + extracted hash/coverage/snd lines); full key dirs with runner
binaries and frames stay in the operator's own store. Key = readable prefix + short hash of the canonical pins JSON, e.g.
a3efbfe-det-fr1r1-t2400-snd1-1x-9f2c41ab. Key derivation is deterministic from
the pins file, so `get --pins` finds what `put --pins` stored.

CF3 profiles: pins carry knobs (profile name: reference|play), ge1_lib
(lib SHA or null), vu1_engine (static|microvu|offline + vu1_lib SHA or
null) and sim_mode. Pins without them are legacy reference keys (the
a3efbfe x86 and aa74ca4 arm64 keys bind to PS2X_PROFILE=reference).
Play keys use build=play-<platform>, e.g.
<fork7>-play-native-arm64-fr1r1-t2400-snd1-1x-<h8>. `key-for` selects the
stored key by profile; `make` boots it via ssx3_boot.py --profile.

Usage:
  baseline.py put --runner R --run DIR --pins PINS [--extra-frames DIR]
  baseline.py get <key|--pins PINS>
  baseline.py key-for --profile P --build B [--fork SHA] [--tip WORKTREE]
  baseline.py list
  baseline.py compare --key K --cand RUNDIR [--min-tick N]
  baseline.py make --pins PINS --runner R [--run DIR] [--microvu-lib PATH]
  baseline.py pins --fork SHA --codegen DIR --vu1 DIR [...] [--out F]
  baseline.py put-state --state F --pins PINS --tick T [--note TEXT]
  baseline.py get-state <key|--pins PINS --tick T>
  baseline.py list-states
SS1 save states live in <store>/states/<key>/ (state.bin + manifest.json with
the state's header lines). The state key uses only the guest-relevant pins
(STATE_PINS) + the save tick, so any candidate build with matching guest code,
route and backend finds it; SSAA/hi-res are in the key but only warn on load.
Global: [--store DIR] (default $SSX3_DET_KEYS or tests/det/keys).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
# OMF1 lane hook: find the repo root by walking up (the lane copy sits one
# level deeper than the landed path, so parents[3] is wrong for one of them).
ROOT = HERE.parent.parent.parent  # tests/det/boot -> repo root
BOOT_DRIVER = HERE.parent / 'ssx3_boot.py'
HASHDIFF = ROOT / 'tests' / 'det' / 'hashdiff.py'
DEFAULT_STORE = Path(os.environ.get('SSX3_DET_KEYS', str(ROOT / 'tests' / 'det' / 'keys')))

# CN2b: parallel_gs/granite left REQUIRED_PINS with paraLLEl-GS (keys that have them keep them).
# backend=external keys pin the GE1 dylib's SHA-256 in ge1_lib.
REQUIRED_PINS = ('fork', 'codegen_register', 'codegen_changed', 'vu1_images',
                 'build', 'route', 'stop_tick', 'sound',
                 'backend', 'ssaa', 'hires', 'pipeline', 'iso_sha', 'elf_sha')
# CF3: optional profile pins. Absent means legacy reference (knobs=reference,
# ge1_lib=null, vu1_engine=static, vu1_lib=null, sim_mode=stock); legacy keys
# keep their names because key_of hashes only the pins present.
KNOB_PROFILES = ('reference', 'play', 'full120', 'full120events', 'play120')  # KEY2: shipped 120 line
VU1_ENGINES = ('static', 'microvu', 'offline')
TEXT_RECEIPTS = ('boot.log', 'snd.log', 'result.json', 'trace.jsonl')
COVERAGE_LINE = re.compile(r'^\[coverage:[^\]]+\].*$', re.M)
# LG1: raylib's TraceLog (stdout) used to interleave with a coverage line
# written as several cerr fragments: "[coverage:..] sid=0x80000006INFO: FILEIO:
# [...]\n function=0xff hits=1". The runtime now writes whole lines, but old
# receipts and other hosts still carry splices: drop the glued raylib message
# and rejoin the coverage line's tail from the next line.
RAYLIB_TRACE = r'(?:TRACE|DEBUG|INFO|WARNING|ERROR|FATAL): '
COVERAGE_SPLICE = re.compile(
    r'^(\[coverage:[^\]]+\][^\n]*?)' + RAYLIB_TRACE + r'[^\n]*\n(?!\[|' + RAYLIB_TRACE + r')([^\n]*)$',
    re.M)


def coverage_lines(boot_text):
    """[coverage:*] lines of a boot log, with mid-line raylib splices repaired."""
    while True:
        fixed = COVERAGE_SPLICE.sub(lambda m: m.group(1) + m.group(2), boot_text, count=1)
        if fixed == boot_text:
            break
        boot_text = fixed
    return COVERAGE_LINE.findall(boot_text)


SND_TICK = re.compile(r'^tick .*cid0=.*$', re.M)
# Host-side counters: wall-clock audio under/overruns vary run to run even for
# guest-identical boots, so they are stripped before the snd comparison.
SND_HOST_NOISE = re.compile(r' underruns=\d+ overflows=\d+')


def sha_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_pins(path):
    pins = json.loads(Path(path).read_text())
    missing = [k for k in REQUIRED_PINS if k not in pins]
    if missing:
        raise SystemExit('pins %s missing: %s' % (path, ','.join(missing)))
    return pins


def canonical(pins):
    return json.dumps(pins, sort_keys=True, separators=(',', ':')).encode()


def key_of(pins):
    h8 = hashlib.sha256(canonical(pins)).hexdigest()[:8]
    prefix = '%s-%s-%s-t%d-snd%d-%dx' % (
        pins['fork'][:7], pins['build'], pins['route'], pins['stop_tick'],
        1 if pins['sound'] == 'on' else 0, pins['ssaa'])
    if pins['hires']:
        prefix += '-hires'
    if pins['pipeline']:
        prefix += '-pp'
    return '%s-%s' % (prefix, h8)


def dir_bytes(path):
    total = 0
    for root, _ds, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def verify(dest):
    """Check every stored file against the manifest. Returns (ok, message)."""
    man = json.loads((dest / 'manifest.json').read_text())
    for rel, want in man['files'].items():
        p = dest / rel
        if not p.exists():
            return False, 'CORRUPT %s %s (missing)' % (man['key'], rel)
        reads = [sha_of(p)]
        if rel == 'runner':
            reads.append(sha_of(p))  # two reads for the runner binary
        if any(r != want for r in reads):
            return False, 'CORRUPT %s %s' % (man['key'], rel)
    return True, ''


def cmd_put(args):
    pins = load_pins(args.pins)
    key = key_of(pins)
    store = Path(args.store)
    dest = store / key
    if dest.exists():
        raise SystemExit('EXISTS %s %s' % (key, dest))
    run = Path(args.run)
    boot = run / 'boot.log'
    result = run / 'result.json'
    if not boot.exists() or not result.exists():
        raise SystemExit('run %s lacks boot.log and/or result.json' % run)
    runner = Path(args.runner)
    if not runner.exists():
        raise SystemExit('no such runner %s' % runner)

    dest.mkdir(parents=True)
    (dest / 'frames').mkdir()
    shutil.copy2(runner, dest / 'runner')
    if sha_of(dest / 'runner') != sha_of(runner):
        raise SystemExit('runner copy mismatch')
    for name in TEXT_RECEIPTS:
        src = run / name
        if src.exists():
            shutil.copyfile(src, dest / name)
    frames = run / 'frames'
    if frames.is_dir():
        for f in sorted(frames.iterdir()):
            if f.is_file() and f.suffix in ('.png', '.txt'):
                shutil.copyfile(f, dest / 'frames' / f.name)
    if args.extra_frames:
        extra = Path(args.extra_frames)
        tag = extra.parent.name.lower().replace(' ', '_') + '-'
        for f in sorted(extra.iterdir()):
            if f.is_file() and f.suffix in ('.png', '.txt'):
                shutil.copyfile(f, dest / 'frames' / (tag + f.name))

    files = {}
    for root, _ds, fs in os.walk(dest):
        for f in fs:
            if f == 'manifest.json':
                continue
            p = Path(root) / f
            files[str(p.relative_to(dest))] = sha_of(p)
    man = {'key': key, 'pins': pins,
           'pins_canonical_sha256': hashlib.sha256(canonical(pins)).hexdigest(),
           'files': files, 'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
           'source_runner': str(runner), 'source_run': str(run),
           'extra_frames': str(Path(args.extra_frames)) if args.extra_frames else None,
           'store_bytes': None}
    (dest / 'manifest.json').write_text(json.dumps(man, indent=2) + '\n')
    man['store_bytes'] = dir_bytes(dest)
    (dest / 'manifest.json').write_text(json.dumps(man, indent=2) + '\n')
    print('%s %s' % (key, dest))
    return 0


def resolve_key(args):
    if args.pins:
        return key_of(load_pins(args.pins))
    return args.key


def cmd_get(args):
    key = resolve_key(args)
    dest = Path(args.store) / key
    if not dest.is_dir() or not (dest / 'manifest.json').exists():
        print('MISSING %s' % key)
        return 2
    ok, msg = verify(dest)
    if not ok:
        print(msg)
        return 1
    print(dest)
    return 0


def cmd_list(args):
    store = Path(args.store)
    rows = []
    total = 0
    if store.is_dir():
        for d in sorted(store.iterdir()):
            man_p = d / 'manifest.json'
            if not man_p.exists():
                continue
            man = json.loads(man_p.read_text())
            pins = man['pins']
            n = dir_bytes(d)
            total += n
            rows.append((man['key'], man['created_utc'], n, pins['fork'][:7],
                         pins['build'], pins['route'], pins['stop_tick'], pins['sound'],
                         pins['backend'], pins['ssaa'], pins['hires'], pins['pipeline'],
                         pins.get('knobs', 'reference'), pins.get('vu1_engine', 'static'),
                         pins.get('sim_mode', 'stock')))
    print('%-46s %-20s %9s %7s %-16s %-6s %5s %-3s %-8s %-8s %-9s %-7s %s'
          % ('KEY', 'CREATED', 'BYTES', 'FORK', 'BUILD', 'ROUTE', 'STOP', 'SND',
             'BACKEND', 'SSAA/HR/PP', 'KNOBS', 'ENGINE', 'SIM'))
    for r in rows:
        print('%-46s %-20s %9d %7s %-16s %-6s %5d %-3s %-8s %dx/%d/%d %-9s %-7s %s' % r)
    print('%d baseline(s), %d bytes in %s' % (len(rows), total, store))
    return 0


def cmd_key_for(args):
    """Select the stored key by profile + build (CF3: the boot tooling
    selects the key by profile). Pins without knobs bind to reference.
    CN2a: newest manifest wins; --tip DIR keeps only keys whose fork commit
    is an ancestor of DIR's HEAD (the mac_gate.sh rule)."""
    if args.profile not in KNOB_PROFILES:
        raise SystemExit('key-for: want profile %s, got %r' % ('|'.join(KNOB_PROFILES), args.profile))
    store = Path(args.store)
    hits = []
    if store.is_dir():
        for d in sorted(store.iterdir()):
            man_p = d / 'manifest.json'
            if not man_p.exists():
                continue
            man = json.loads(man_p.read_text())
            pins = man['pins']
            if pins.get('knobs', 'reference') != args.profile or pins.get('build') != args.build:
                continue
            if args.fork and not pins.get('fork', '').startswith(args.fork):
                continue
            # CN2a (mac_gate.sh rule): with --tip, only keys whose fork commit is in the tested tip.
            if args.tip and pins.get('fork') and subprocess.run(
                    ['git', '-C', args.tip, 'merge-base', '--is-ancestor', pins['fork'], 'HEAD'],
                    capture_output=True).returncode != 0:
                print('skip (fork %s not in tip) %s' % (pins['fork'][:9], man['key']), file=sys.stderr)
                continue
            hits.append((man.get('created_utc', ''), man['key']))
    if not hits:
        print('MISSING profile=%s build=%s' % (args.profile, args.build))
        return 2
    # CN2a: lanes re-mint on deliberate change (e.g. backend=cpu), so the newest manifest wins,
    # as in mac_gate.sh; every candidate goes to stderr.
    hits.sort()
    for created, key in hits:
        print('candidate %s %s' % (created, key), file=sys.stderr)
    print(hits[-1][1])
    return 0


STATE_PINS = ('fork', 'codegen_register', 'codegen_changed', 'vu1_images',
              'route', 'backend', 'ssaa', 'hires', 'iso_sha', 'elf_sha')
# CN2b: the GS library is in the state key when the pins carry it (old paraLLEl keys hash
# parallel_gs as before; GE1 keys hash ge1_lib).
STATE_PINS_OPT = ('parallel_gs', 'ge1_lib')


def state_key_of(pins, tick):
    sub = {k: pins[k] for k in STATE_PINS}
    sub.update({k: pins[k] for k in STATE_PINS_OPT if pins.get(k)})
    sub['tick'] = int(tick)
    h8 = hashlib.sha256(canonical(sub)).hexdigest()[:8]
    prefix = '%s-%s-t%d-%s-%dx' % (pins['fork'][:7], pins['route'], int(tick), pins['backend'], pins['ssaa'])
    if pins['hires']:
        prefix += '-hires'
    return '%s-%s' % (prefix, h8), sub


def state_header(path):
    """Header key=value lines from an SS1 state file (format 1)."""
    import struct
    with open(path, 'rb') as f:
        head = f.read(1 << 16)
    if head[:8] != b'PS2XSAVE':
        raise SystemExit('%s is not a ps2x save state' % path)
    pos = 12
    (klen,) = struct.unpack_from('<I', head, pos)
    pos += 4
    if head[pos:pos + klen] != b'header':
        raise SystemExit('%s: first section is not the header' % path)
    pos += klen + 4 + 8
    (tlen,) = struct.unpack_from('<Q', head, pos)
    pos += 8
    text = head[pos:pos + tlen].decode()
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def cmd_put_state(args):
    pins = json.loads(Path(args.pins).read_text())
    missing = [k for k in STATE_PINS if k not in pins]
    if missing:
        raise SystemExit('pins %s missing: %s' % (args.pins, ','.join(missing)))
    key, sub = state_key_of(pins, args.tick)
    header = state_header(args.state)
    if int(header.get('save_tick', -1)) != args.tick:
        raise SystemExit('state saved at tick %s, not %d' % (header.get('save_tick'), args.tick))
    dest = Path(args.store) / 'states' / key
    if dest.exists():
        raise SystemExit('EXISTS %s %s' % (key, dest))
    dest.mkdir(parents=True)
    shutil.copyfile(args.state, dest / 'state.bin')
    reads = [sha_of(args.state), sha_of(dest / 'state.bin')]
    if reads[0] != reads[1]:
        shutil.rmtree(dest)
        raise SystemExit('copy SHA mismatch')
    man = {'key': key, 'kind': 'ss1-state', 'pins': pins, 'key_pins': sub, 'tick': args.tick,
           'header': header, 'note': args.note, 'source': str(Path(args.state).resolve()),
           'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
           'files': {'state.bin': reads[0]}, 'bytes': (dest / 'state.bin').stat().st_size}
    (dest / 'manifest.json').write_text(json.dumps(man, indent=2, sort_keys=True) + '\n')
    print(dest)
    return 0


def cmd_get_state(args):
    if args.key:
        key = args.key
    else:
        if not args.pins or args.tick is None:
            raise SystemExit('get-state needs a key or --pins + --tick')
        key, _ = state_key_of(json.loads(Path(args.pins).read_text()), args.tick)
    dest = Path(args.store) / 'states' / key
    if not (dest / 'manifest.json').exists():
        print('MISSING %s' % key)
        return 2
    man = json.loads((dest / 'manifest.json').read_text())
    want = man['files']['state.bin']
    if any(sha_of(dest / 'state.bin') != want for _ in range(2)):
        print('CORRUPT %s state.bin' % key)
        return 3
    print(dest / 'state.bin')
    return 0


def cmd_list_states(args):
    root = Path(args.store) / 'states'
    rows = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            m = d / 'manifest.json'
            if m.exists():
                man = json.loads(m.read_text())
                rows.append((man['key'], man['created_utc'], man['bytes'], man['header'].get('runner_sha', '')[:12],
                             man['note']))
    print('%-52s %-20s %9s %-12s %s' % ('KEY', 'CREATED', 'BYTES', 'RUNNER', 'NOTE'))
    for r in rows:
        print('%-52s %-20s %9d %-12s %s' % r)
    print('%d state(s) in %s' % (len(rows), root))
    return 0


SND_SETDMA = re.compile(r'^setdma vsync=(\d+)')

# TLB3: bound the snd comparison by the key's tick target. Each tick row is
# stamped with the last setdma vsync seen before it (0 when the log carries no
# setdma lines, e.g. synthetic fixtures). Rows with vsync > stop_tick are past
# the target: the host-timed stop lets the baseline run a few vsyncs long, so
# they are reported as informational and never fail the gate.


def parse_snd_log(snd_p):
    """Parse snd.log into (tick_texts, vsyncs, truncated, fragment).

    tick_texts are host-noise-stripped complete tick lines; vsyncs[i] is the
    last setdma vsync before tick i (0 if none). Only newline-terminated lines
    are parsed: a trailing partial line (stop-kill mid-write) is dropped from
    the rows and reported via truncated/fragment instead.
    """
    data = Path(snd_p).read_bytes()
    if not data:
        return ['(no tick lines)'], [], False, ''
    truncated = not data.endswith(b'\n')
    text = data.decode(errors='replace')
    lines = text.splitlines()
    fragment = ''
    if truncated and lines:
        fragment = lines[-1]
        lines = lines[:-1]
    ticks = []
    vsyncs = []
    last_vsync = 0
    for line in lines:
        m = SND_SETDMA.match(line)
        if m:
            try:
                last_vsync = int(m.group(1))
            except ValueError:
                pass
            continue
        if SND_TICK.match(line + '\n'):
            ticks.append(SND_HOST_NOISE.sub('', line))
            vsyncs.append(last_vsync)
    if not ticks:
        return ['(no tick lines)'], [], truncated, fragment
    return ticks, vsyncs, truncated, fragment


def snd_bound_count(vsyncs, target):
    """Leading snd rows with vsync <= target: the row index the run reached at
    the target tick. Vsyncs are monotonic in real logs; synthetic logs without
    setdma stamp every row 0, so the whole log stays in bound."""
    n = 0
    for v in vsyncs:
        if v <= target:
            n += 1
        else:
            break
    return n


def snd_bound_ok(snd_b, vs_b, snd_c, vs_c, target):
    """Compare snd rows with vsync <= target, keeping strict-prefix and
    identical-row checks. Returns (ok, n_common, nb, nc, first_diff). The
    bound replaces the old two-row tail allowance: rows past the target are
    ignored, while rows inside the bound must match exactly (same count and
    every common row identical). Past-target rows are ignored by the caller."""
    nb = snd_bound_count(vs_b, target)
    nc = snd_bound_count(vs_c, target)
    # Marker lists ('(no snd.log)'/'(no tick lines)') carry no vsyncs: fall
    # back to the unbound prefix rule so a missing receipt still fails.
    if not vs_b or not vs_c:
        if snd_b[:1] == ['(no snd.log)'] or snd_c[:1] == ['(no snd.log)'] \
                or snd_b[:1] == ['(no tick lines)'] or snd_c[:1] == ['(no tick lines)']:
            n = min(len(snd_b), len(snd_c))
            ok = n >= max(1, len(snd_b) - 2) and snd_b[:n] == snd_c[:n]
            first = next((i for i in range(n) if snd_b[i] != snd_c[i]), None)
            return ok, n, len(snd_b), len(snd_c), first
        nb, nc = len(snd_b), len(snd_c)
    if nb == 0 and nc == 0:
        return True, 0, nb, nc, None
    n = min(nb, nc)
    first = next((i for i in range(n) if snd_b[i] != snd_c[i]), None)
    ok = (nb == nc) and (first is None)
    return ok, n, nb, nc, first


def snd_coverage_summary(run):
    boot = (Path(run) / 'boot.log').read_bytes().decode(errors='replace')
    cov = coverage_lines(boot)
    snd_p = Path(run) / 'snd.log'
    if snd_p.exists():
        snd, _vs, _trunc, _frag = parse_snd_log(snd_p)
    else:
        snd = ['(no snd.log)']
    return cov, snd


def snd_prefix_ok(snd_b, snd_c):
    """Compare the snd tick lines both runs printed (host noise stripped).
    Runs stop a few ticks apart (runner stop is host-timed), so the last line
    can differ in a guest-identical pair (HP1: 2405 vs 2410): compare the common
    prefix, and require it to cover all but the last two base lines."""
    n = min(len(snd_b), len(snd_c))
    return n >= max(1, len(snd_b) - 2) and snd_b[:n] == snd_c[:n], n


def cmd_compare(args):
    dest = Path(args.store) / args.key
    if not dest.is_dir() or not (dest / 'manifest.json').exists():
        print('MISSING %s' % args.key)
        return 2
    ok, msg = verify(dest)
    if not ok:
        print(msg)
        return 1
    man = json.loads((dest / 'manifest.json').read_text())
    min_tick = args.min_tick if args.min_tick else man['pins']['stop_tick']
    if not HASHDIFF.exists():
        raise SystemExit('missing %s' % HASHDIFF)
    r = subprocess.run([sys.executable, str(HASHDIFF), '--base', str(dest),
                        '--cand', args.cand, '--min-tick', str(min_tick),
                        '--hash-every', str(man['pins'].get('hash_every', 1))],
                       capture_output=True, text=True)
    print(r.stdout, end='')
    if r.stderr:
        print(r.stderr, end='', file=sys.stderr)
    hash_ok = r.returncode == 0
    target = man['pins']['stop_tick']
    # TLB3: parse with per-row vsyncs so rows past the tick target stay
    # informational. Only complete lines enter the row lists; a trailing
    # partial (stop-kill mid-write) is reported, never compared.
    boot_b = (Path(dest) / 'boot.log').read_bytes().decode(errors='replace')
    boot_c = (Path(args.cand) / 'boot.log').read_bytes().decode(errors='replace') \
        if (Path(args.cand) / 'boot.log').exists() else ''
    cov_b = coverage_lines(boot_b)
    cov_c = coverage_lines(boot_c)
    snd_bp, snd_cp = Path(dest) / 'snd.log', Path(args.cand) / 'snd.log'
    if snd_bp.exists():
        snd_b, vs_b, trunc_b, frag_b = parse_snd_log(snd_bp)
    else:
        snd_b, vs_b, trunc_b, frag_b = ['(no snd.log)'], [], False, ''
    if snd_cp.exists():
        snd_c, vs_c, trunc_c, frag_c = parse_snd_log(snd_cp)
    else:
        snd_c, vs_c, trunc_c, frag_c = ['(no snd.log)'], [], False, ''
    # HS1: coverage per-entry lines print in std::unordered_map iteration
    # order, which differs across STL implementations (Mac libc++ vs Linux
    # libstdc++) while the content is guest-identical — so compare coverage
    # as a multiset. Sorting cannot mask a real difference (same multiset
    # <=> same sorted list) and is a no-op verdict-wise for same-host
    # compares, where the order is stable run to run (F5 B1-B5).
    snd_ok, n_common, nb, nc, first = snd_bound_ok(snd_b, vs_b, snd_c, vs_c, target)
    coverage_ok = sorted(cov_b) == sorted(cov_c)
    past_b, past_c = len(snd_b) - nb, len(snd_c) - nc
    # Rows past the target are informational only (TLB3): the baseline's
    # host-timed stop runs a few vsyncs long (full120events rows at vsyncs
    # 4911/5032/5152 past t4800; reference rows past t2400). They never fail.
    trunc_note = ''
    if trunc_b or trunc_c:
        trunc_note = ' (trailing partial past target ignored: base=%r cand=%r)' % (frag_b[-40:], frag_c[-40:])
    snd_cov_ok = coverage_ok and snd_ok
    print('snd/coverage: %s' % ('IDENTICAL' if snd_cov_ok else 'DIFFER'))
    if not snd_cov_ok:
        if not coverage_ok:
            print('  coverage base: %s' % cov_b)
            print('  coverage cand: %s' % cov_c)
        if not snd_ok:
            # A missing/partial receipt inside the bound is an incomplete
            # harness receipt, not a guest difference (TLB1 rule, kept for
            # the bound region only).
            if trunc_b or trunc_c:
                print('HARNESS ERROR: snd rows inside tick target t%d incomplete: '
                      'base=%d cand=%d bound_base=%d bound_cand=%d common=%d%s'
                      % (target, len(snd_b), len(snd_c), nb, nc, n_common, trunc_note))
                print('compare %s vs %s: HARNESS ERROR' % (args.key, args.cand))
                return 2
            print('  snd lines base=%d cand=%d bound_base=%d bound_cand=%d '
                  'common=%d first_diff_line=%s (target t%d)'
                  % (len(snd_b), len(snd_c), nb, nc, n_common, first, target))
            if first is not None:
                print('  snd base: %s' % snd_b[first])
                print('  snd cand: %s' % snd_c[first])
    else:
        print('  coverage: %s' % (' | '.join(cov_b[:4]) if cov_b else '(none)'))
        if nb > 0:
            print('  snd: %d bound tick lines (target t%d), last %s' % (n_common, target, snd_b[n_common - 1]))
        else:
            print('  snd: no bound rows (target t%d)' % target)
        if past_b or past_c or trunc_b or trunc_c:
            print('  snd past-target (informational, ignored): base=%d cand=%d%s'
                  % (past_b, past_c, trunc_note))
    print('compare %s vs %s: %s' % (args.key, args.cand,
                                    'IDENTICAL' if hash_ok and snd_cov_ok else 'DIFFER'))
    return 0 if hash_ok and snd_cov_ok else 1


def sha40(value, name):
    if not re.fullmatch(r'[0-9a-f]{40}', value or ''):
        raise SystemExit('%s: want full 40-hex, got %r' % (name, value))
    return value


def sha64(value, name):
    if not re.fullmatch(r'[0-9a-f]{64}', value or ''):
        raise SystemExit('%s: want full 64-hex SHA-256, got %r' % (name, value))
    return value


def vu1_images_of(vu1_dir):
    """RS2 vu1_images: sha256 over the LC_ALL=C-sorted `sha256sum vu1_*.cpp`
    lines (two-space text-mode format), i.e. `sha256sum vu1_*.cpp | sort |
    sha256sum` run inside the dir. This is the pins-file value; the
    build scripts' vu1gen-<12> dirname scheme hashes the same lines in
    glob (filename) order instead — same content, different string, and
    the two must never be mixed (HS1 G5)."""
    files = sorted(Path(vu1_dir).glob('vu1_*.cpp'))
    if not files:
        raise SystemExit('no vu1_*.cpp in %s' % vu1_dir)
    lines = sorted(('%s  %s\n' % (sha_of(f), f.name)).encode() for f in files)
    return hashlib.sha256(b''.join(lines)).hexdigest()


def cmd_pins(args):
    if bool(args.fork) == bool(args.fork_wt):
        raise SystemExit('pins needs exactly one of --fork SHA / --fork-wt DIR')
    if args.fork_wt:
        r = subprocess.run(['git', '-C', args.fork_wt, 'rev-parse', 'HEAD'],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit('rev-parse failed in %s' % args.fork_wt)
        fork = r.stdout.strip()
    else:
        fork = args.fork
    sha40(fork, '--fork')
    codegen = Path(args.codegen)
    reg = codegen / 'register_functions.cpp'
    changed = codegen / args.changed_file
    if not reg.is_file():
        raise SystemExit('missing %s' % reg)
    if not changed.is_file():
        raise SystemExit('missing %s' % changed)
    if args.sound not in ('on', 'off'):
        raise SystemExit("--sound: want on/off, got %r" % args.sound)
    if args.knobs not in KNOB_PROFILES:
        raise SystemExit("--knobs: want %s, got %r" % ('|'.join(KNOB_PROFILES), args.knobs))
    if args.vu1_engine not in VU1_ENGINES:
        raise SystemExit("--vu1-engine: want %s, got %r" % ('|'.join(VU1_ENGINES), args.vu1_engine))
    if args.vu1_lib and not Path(args.vu1_lib).is_file():
        raise SystemExit('no such --vu1-lib %s' % args.vu1_lib)
    vu1_lib = sha_of(args.vu1_lib) if args.vu1_lib else None
    # CN2b: --ge1-lib is the GE1 dylib path; the pin is its SHA-256.
    ge1_lib = None
    if args.ge1_lib:
        if not Path(args.ge1_lib).is_file():
            raise SystemExit('no such --ge1-lib %s' % args.ge1_lib)
        ge1_lib = sha_of(args.ge1_lib)
    if (args.backend == 'external') != bool(ge1_lib):
        raise SystemExit('--ge1-lib is required for (and only for) --backend external')
    if args.ge1_renderer not in ('metal', 'vk'):
        raise SystemExit("--ge1-renderer: want metal|vk, got %r" % args.ge1_renderer)
    pins = {
        'backend': args.backend,
        'build': args.build,
        'codegen_changed': sha_of(changed),
        'codegen_changed_file': args.changed_file,
        'codegen_register': sha_of(reg),
        'elf_sha': sha_of(args.elf),
        'fork': fork,
        'ge1_lib': ge1_lib,
        'hires': args.hires,
        'iso_sha': sha_of(args.iso),
        'knobs': args.knobs,
        'pipeline': args.pipeline,
        'route': args.route,
        'sim_mode': args.sim_mode,
        'sound': args.sound,
        'ssaa': args.ssaa,
        'stop_tick': args.stop_tick,
        'vu1_engine': args.vu1_engine,
        'vu1_images': vu1_images_of(args.vu1),
        'vu1_lib': vu1_lib,
    }
    if args.dump_ticks:
        pins['dump_ticks'] = args.dump_ticks
    if args.hash_every:
        pins['hash_every'] = args.hash_every
    if args.coverage_tick is not None:
        pins['coverage_tick'] = args.coverage_tick
    if args.vu1_stats:
        pins['vu1_stats'] = True
    if args.note:
        pins['note'] = args.note
    if args.backend == 'external':
        pins['ge1_renderer'] = args.ge1_renderer  # GE1S; absent on CN2b keys = vk (MoltenVK)
    text = json.dumps(pins, indent=2, sort_keys=True) + '\n'
    if args.out:
        Path(args.out).write_text(text)
        print('%s %s' % (key_of(pins), args.out))
    else:
        print(text, end='')
    return 0


def cmd_make(args):
    pins = load_pins(args.pins)
    key = key_of(pins)
    store = Path(args.store)
    dest = store / key
    if dest.is_dir():
        ok, msg = verify(dest)
        if not ok:
            print(msg)
            return 1
        print('EXISTS %s %s' % (key, dest))
        return 0
    if not pins['build'].startswith(('det', 'play', 'full120', 'reference')):
        raise SystemExit('make supports det/play controls only (pins build=%s); '
                         'speed baselines need the exclusive lease' % pins['build'])
    profile = pins.get('knobs', 'reference')
    if profile not in KNOB_PROFILES:
        raise SystemExit('pins knobs: want %s, got %r' % ('|'.join(KNOB_PROFILES), profile))
    if args.run:
        rundir = Path(args.run)
    else:
        rundir = store / '.tmp' / ('%s-%s' % (
            key, time.strftime('%Y%m%dT%H%M%S', time.gmtime())))
    if pins.get('vu1_engine', 'static') != 'static' and not args.microvu_lib:
        raise SystemExit('make needs --microvu-lib PATH for vu1_engine=%s' % pins['vu1_engine'])
    if args.microvu_lib:
        if not Path(args.microvu_lib).is_file():
            raise SystemExit('no such --microvu-lib %s' % args.microvu_lib)
        if pins.get('vu1_lib') and sha_of(args.microvu_lib) != pins['vu1_lib']:
            raise SystemExit('--microvu-lib SHA does not match pins vu1_lib')
    if pins['backend'] == 'external':
        if not args.ge1_lib:
            raise SystemExit('make needs --ge1-lib PATH for backend=external')
        ge1 = args.ge1_lib
        if sha_of(ge1) != sha64(pins.get('ge1_lib') or '', 'pins ge1_lib'):
            raise SystemExit('GE1 lib %s SHA does not match pins ge1_lib' % ge1)
    if pins['backend'] not in ('external', 'cpu'):
        raise SystemExit('backend=%s: paraLLEl-GS is retired (CN2b); mint on external' % pins['backend'])
    if pins['ssaa'] != 1 or pins['hires'] or pins['pipeline']:
        raise SystemExit('ssaa/hires/pipeline are paraLLEl-GS pins; want 1/0/0')
    label = key[:32]
    for flag in ('iso', 'elf', 'codegen'):
        if not getattr(args, flag, None):
            raise SystemExit('make needs --%s PATH (boot inputs)' % flag)
    cmd = [sys.executable, str(BOOT_DRIVER), '--mode', 'det',
           '--backend', pins['backend'], '--runner', args.runner,
           '--label', label, '--out', str(rundir),
           '--iso', str(args.iso), '--elf', str(args.elf),
           '--codegen', str(args.codegen),
           '--stop-tick', str(pins['stop_tick']), '--sound', pins['sound'],
           '--route', pins['route'], '--profile', profile,
           '--coverage-tick', str(pins.get('coverage_tick', pins['stop_tick'])),
           '--hash-every', str(pins.get('hash_every', 1))]
    if pins['backend'] == 'cpu':
        cmd += ['--wall', '590']  # the CPU raster is ~3x slower in races; stay under 600 s
    if args.microvu_lib:
        cmd += ['--microvu-lib', str(args.microvu_lib)]
    if pins['backend'] == 'external':
        cmd += ['--ge1-lib', ge1, '--env', 'GE1_RENDERER=%s' % pins.get('ge1_renderer', 'vk')]
        cmd += ['--env', 'GE1_GS_RESOURCES_DIR=%s' % args.resources_dir] if args.resources_dir else []
    if profile == 'reference' and pins.get('vu1_engine', 'static') == 'microvu':
        # The reference profile is a marker only (no compiled defaults), so the microVU
        # engine is selected explicitly; the play profiles get it from PLAY_ENV. microVU refuses
        # to start without PS2X_MTVU=1 (no LAG, no stages: the minimum the engine needs).
        cmd += ['--env', 'PS2X_VU1_ENGINE=microvu', '--env', 'PS2X_MTVU=1']
    if pins.get('dump_ticks'):
        cmd += ['--dump-ticks', pins['dump_ticks']]
    if pins.get('vu1_stats'):
        cmd += ['--vu1-stats']
    print('+ %s' % ' '.join(cmd), flush=True)
    # Lab build-slot lease when available (shared hosts); otherwise unleased.
    try:
        from p_lane_lease import hold
        have_lease = True
    except ImportError:
        have_lease = False
    if not have_lease:
        if args.dry_run:
            print('DRY-RUN %s (unleased)' % key)
            return 0
        proc = subprocess.Popen(cmd)
        old = signal.signal(signal.SIGTERM, lambda sg, fr: (proc.terminate(), sys.exit(128 + sg)))
        try:
            proc.wait()
        finally:
            signal.signal(signal.SIGTERM, old)
            if proc.poll() is None:
                proc.terminate()
                proc.wait()
        r = proc
    else:
        with hold('make-' + label) as slot:
            if slot is None:
                return 3  # INFRA-BLOCKED printed
            print('make: holding slot %s' % slot, flush=True)
            if args.dry_run:
                print('DRY-RUN %s (slot %s claimed, releasing)' % (key, slot))
                return 0
            proc = subprocess.Popen(cmd, env=dict(os.environ, SSX3_HELD_SLOT=str(slot)))
            old = signal.signal(signal.SIGTERM, lambda sg, fr: (proc.terminate(), sys.exit(128 + sg)))
            try:
                proc.wait()
            finally:
                signal.signal(signal.SIGTERM, old)
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait()
        r = proc
    if r.returncode != 0:
        print('BOOT-FAILED %s rc=%d run=%s' % (key, r.returncode, rundir))
        return 1
    args.runner, args.run = args.runner, str(rundir)
    args.extra_frames = None
    return cmd_put(args)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--store', default=str(DEFAULT_STORE))
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('put')
    p.add_argument('--runner', required=True)
    p.add_argument('--run', required=True)
    p.add_argument('--pins', required=True)
    p.add_argument('--extra-frames', default='')
    g = sub.add_parser('get')
    g.add_argument('key', nargs='?')
    g.add_argument('--pins', default='')
    kf = sub.add_parser('key-for')
    kf.add_argument('--profile', required=True)
    kf.add_argument('--build', required=True)
    kf.add_argument('--fork', default='',
                    help='fork SHA prefix when several keys share profile+build')
    kf.add_argument('--tip', default='',
                    help='CN2a: fork worktree; skip keys whose fork commit is not in its HEAD')
    sub.add_parser('list')
    c = sub.add_parser('compare')
    c.add_argument('--key', required=True)
    c.add_argument('--cand', required=True)
    c.add_argument('--min-tick', type=int, default=0)
    m = sub.add_parser('make')
    m.add_argument('--dry-run', action='store_true',
                   help='claim + release the boot slot, print the boot command, run nothing')
    m.add_argument('--pins', required=True)
    m.add_argument('--runner', required=True)
    m.add_argument('--run', default='')
    m.add_argument('--microvu-lib', default='')
    m.add_argument('--ge1-lib', default='',
                   help='GE1 dylib for backend=external (SHA must match pins)')
    m.add_argument('--iso', default='', help='own supported USA disc image')
    m.add_argument('--elf', default='', help='boot ELF extracted from --iso')
    m.add_argument('--codegen', default='', help='generated game-code dir')
    m.add_argument('--resources-dir', default='',
                   help='GE1 resources dir (default: beside --ge1-lib)')
    pn = sub.add_parser('pins')
    pn.add_argument('--fork', default='')
    pn.add_argument('--fork-wt', default='')
    pn.add_argument('--codegen', required=True)
    pn.add_argument('--changed-file', default='sub_003FE828_0x3fe828.cpp')
    pn.add_argument('--vu1', required=True)
    pn.add_argument('--build', required=True)
    pn.add_argument('--route', required=True)
    pn.add_argument('--stop-tick', type=int, required=True)
    pn.add_argument('--sound', required=True)
    pn.add_argument('--backend', required=True)
    pn.add_argument('--ssaa', type=int, required=True)
    pn.add_argument('--hires', type=int, default=0)
    pn.add_argument('--pipeline', type=int, default=0)
    pn.add_argument('--iso', required=True)
    pn.add_argument('--elf', required=True)
    pn.add_argument('--dump-ticks', default='')
    pn.add_argument('--hash-every', type=int, default=0)
    pn.add_argument('--coverage-tick', type=int, default=None)
    pn.add_argument('--vu1-stats', action='store_true')
    pn.add_argument('--knobs', default='reference')
    pn.add_argument('--ge1-lib', default='')
    pn.add_argument('--ge1-renderer', default='metal', help='GE1S: GE1_RENDERER for backend=external keys')
    pn.add_argument('--vu1-engine', default='static')
    pn.add_argument('--vu1-lib', default='')
    pn.add_argument('--sim-mode', default='stock')
    pn.add_argument('--note', default='')
    pn.add_argument('--out', default='')
    ps = sub.add_parser('put-state')
    ps.add_argument('--state', required=True)
    ps.add_argument('--pins', required=True)
    ps.add_argument('--tick', type=int, required=True)
    ps.add_argument('--note', default='')
    gs = sub.add_parser('get-state')
    gs.add_argument('key', nargs='?')
    gs.add_argument('--pins', default='')
    gs.add_argument('--tick', type=int, default=None)
    sub.add_parser('list-states')
    args = ap.parse_args()
    if args.cmd == 'get' and not args.key and not args.pins:
        raise SystemExit('get needs a key or --pins')
    return {'put': cmd_put, 'get': cmd_get, 'key-for': cmd_key_for, 'list': cmd_list,
            'compare': cmd_compare, 'make': cmd_make, 'pins': cmd_pins,
            'put-state': cmd_put_state,
            'get-state': cmd_get_state, 'list-states': cmd_list_states}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
