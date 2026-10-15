#!/usr/bin/env python3
"""Print the replay env for a padrec recording; refuse on mismatch.

The output is ps2x.env-compatible (KEY=VALUE lines, '#' comments): the
guest-affecting knobs from the recording header, PS2X_PAD_SCRIPT (+ the vsync
clock) and PS2X_MC_ROOT pointing at the recording's per-session snapshot, so
any session replays standalone.

Usage:
  padrec_replay_env.py <recording> [--apk APK] [--env-file FILE]
                       [--push-to android|mac] [--app-id ID] [--serial SER]
                       [--inline]
  padrec_replay_env.py --self-test

The replay env prints PS2X_PAD_SCRIPT=@<path> (RP2: the runtime loads the
script from the file). --push-to android adb-pushes the recording into
the app's files/ dir and prints the device path (needs --app-id; --serial
selects the device, else adb's default); --push-to mac prints the absolute
local path (no copy needed).
--inline prints the recording body as the value (the AR1 workaround).

Checks (a refusal prints REFUSED: ... to stderr and exits 1):
  --apk APK        extract the PS2X-BUILD-ID marker from the APK's runner
                   .so (streamed, never fully extracted) and refuse when it
                   differs from the header's build=.
  --env-file FILE  hash FILE's raw bytes (FNV-1a 64, the runtime's scheme)
                   and refuse when it differs from the header's env_sha;
                   refuse when any of the six guest knobs in FILE differs
                   from the header. (A recording with env_sha=none had no
                   env file at record time, so any --env-file refuses.)
  snapshot         when the header names a snapshot dir, refuse when the
                   sibling dir is missing or its hash/file count differs.
                   Recordings without a snapshot (mcsnap=none, or pre-PL1
                   headers) print PS2X_MC_ROOT=<record-time root> with a
                   stderr warning instead of refusing: the card must be
                   seeded by hand.
"""
import os
import re
import subprocess
import sys
import zipfile

FNV_OFFSET = 14695981039346656037
FNV_PRIME = 1099511628211
MASK64 = (1 << 64) - 1

# Header knob -> env var (the mapping in Pad.cpp's header writer).
KNOB_ENV = [
    ('SIM_MODE', 'PS2X_SSX3_SIM_MODE'),
    ('VU1_ENGINE', 'PS2X_VU1_ENGINE'),
    ('MTVU', 'PS2X_MTVU'),
    ('FINISH_TIMING', 'PS2X_GS_FINISH_TIMING'),
    ('DETERMINISTIC', 'PS2X_DETERMINISTIC'),
    ('VU_FLOAT', 'PS2X_VU_FLOAT'),
    ('VIF1_REVERSE_DMA', 'PS2X_VIF1_REVERSE_DMA'),
]

BUILD_MARKER = b'PS2X-BUILD-ID:'


def fnv1a_hex(data):
    h = FNV_OFFSET
    for b in data:
        h = ((h ^ b) * FNV_PRIME) & MASK64
    return '%016x' % h


def split_kv(line):
    """Split 'k1=v1 k2=v2 ...' where values may hold spaces: keys are
    [A-Za-z_][A-Za-z0-9_]* followed by '='; each value runs to the next
    key (trailing whitespace stripped)."""
    keys = [(m.group(1), m.start(), m.end()) for m in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)=', line)]
    if not keys or keys[0][1] != 0:
        raise ValueError('bad kv line: %r' % line)
    out = {}
    for i, (k, _, end) in enumerate(keys):
        stop = keys[i + 1][1] - 1 if i + 1 < len(keys) else len(line)
        out[k] = line[end:stop].rstrip()
    return out


def parse_header(text):
    """Parse the padrec '#' header block into a dict. Missing PL1-era keys
    (pre-PL1 recordings) read as their defaults, never an error."""
    h = {'build': 'unavailable', 'env_sha': 'none', 'start_utc': 'unknown',
         'knobs': {}, 'mcroot': 'unset', 'mcsave': 'unreadable', 'mcfiles': '0',
         'mcsnap': None, 'mcsrc': 'none', 'mcsnap_sha': 'none', 'mcsnap_files': '0'}
    for raw in text.split('\n'):
        if not raw.startswith('#'):
            break
        line = raw[1:].strip()
        if line.startswith('build='):
            h['build'] = line[len('build='):]
        elif line.startswith('env_sha='):
            h['env_sha'] = line[len('env_sha='):]
        elif line.startswith('start_utc='):
            h['start_utc'] = line[len('start_utc='):]
        elif line.startswith('knobs '):
            h['knobs'] = split_kv(line[len('knobs '):])
        elif line.startswith('mcroot='):
            d = split_kv(line)
            h['mcroot'] = d.get('mcroot', 'unset')
            h['mcsave'] = d.get('mcsave', 'unreadable')
            h['mcfiles'] = d.get('mcfiles', '0')
        elif line.startswith('mcsnap='):
            d = split_kv(line)
            h['mcsnap'] = d.get('mcsnap', 'none')
            h['mcsrc'] = d.get('mcsrc', 'none')
            h['mcsnap_sha'] = d.get('mcsnap_sha', 'none')
            h['mcsnap_files'] = d.get('mcsnap_files', '0')
    return h


def parse_env_file(text):
    """The ps2x.env rules (parseEnvFileContent): '#' comments, blank lines
    skipped, split at the first '=', keys/values trimmed."""
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        eq = line.find('=')
        if eq < 0:
            continue
        key = line[:eq].strip()
        if key:
            out[key] = line[eq + 1:].strip()
    return out


def snapshot_hash(root):
    """FNV-1a 64 over sorted relpaths (+ NUL) and bytes, the runtime's
    snapshot scheme. Returns (hex, file_count)."""
    rels = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if os.path.isfile(full) and not os.path.islink(full):
                rels.append(os.path.relpath(full, root))
    # os.walk order is arbitrary; the runtime sorts.
    rels.sort()
    h = FNV_OFFSET
    total = 0
    for rel in rels:
        for b in rel.encode('utf-8'):
            h = ((h ^ b) * FNV_PRIME) & MASK64
        h = ((h ^ 0) * FNV_PRIME) & MASK64
        with open(os.path.join(root, rel), 'rb') as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > 64 << 20:
                    raise ValueError('snapshot over the 64 MiB tool cap')
                for b in chunk:
                    h = ((h ^ b) * FNV_PRIME) & MASK64
    return '%016x' % h, len(rels)


def apk_build_id(path):
    """Stream the APK's runner .so for the PS2X-BUILD-ID marker; return the
    id ('' when the marker is absent). Never extracts the ~200 MB .so."""
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        cands = [n for n in names if n.endswith('libps2EntryRunner.so')]
        if not cands:
            cands = [n for n in names if n.startswith('lib/') and n.endswith('.so')]
        if not cands:
            raise ValueError('no .so in %s' % path)
        with zf.open(cands[0]) as f:
            tail = b''
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    return ''
                buf = tail + chunk
                at = buf.find(BUILD_MARKER)
                if at >= 0:
                    start = at + len(BUILD_MARKER)
                    ident = buf[start:start + 64].split(b'\0', 1)[0]
                    if b'\0' in buf[start:start + 64]:
                        return ident.decode('ascii', 'replace')
                    # Marker straddles the 64 B window: keep scanning.
                    tail = buf[start:]
                    continue
                tail = buf[-(len(BUILD_MARKER) - 1):] if len(buf) >= len(BUILD_MARKER) else buf
    return ''


def check_apk(header, apk_path):
    found = apk_build_id(apk_path)
    want = header['build']
    if not found:
        return 'APK has no PS2X-BUILD-ID marker (pre-PL1 build); recording wants build=%s' % want
    if found != want:
        return 'build mismatch: recording build=%s, APK build=%s' % (want, found)
    return ''


def check_env_file(header, env_path):
    with open(env_path, 'rb') as f:
        raw = f.read()
    if header['env_sha'] == 'none':
        return 'recording has env_sha=none (no env file at record time); --env-file cannot match'
    if fnv1a_hex(raw) != header['env_sha']:
        return 'env_sha mismatch: recording env_sha=%s, --env-file hashes to %s' % (
            header['env_sha'], fnv1a_hex(raw))
    try:
        entries = parse_env_file(raw.decode('utf-8', 'replace'))
    except ValueError as e:
        return 'cannot parse --env-file: %s' % e
    for knob, env in KNOB_ENV:
        want = header['knobs'].get(knob, 'unset')
        got = entries.get(env, '')
        got = got if got else 'unset'
        if got != want:
            return 'knob mismatch: %s=%s in --env-file, recording has %s' % (env, got, want)
    return ''


def check_snapshot(header, rec_path):
    """Returns (mc_root, warning, refusal)."""
    snap = header['mcsnap']
    if snap in (None, 'none'):
        root = header['mcroot']
        if root in ('unset', 'unreadable', ''):
            return '', '', 'no snapshot and no record-time card root; seed PS2X_MC_ROOT by hand'
        return root, 'no per-session snapshot; PS2X_MC_ROOT=%s is the record-time path' % root, ''
    if snap in ('too-large', 'unreadable'):
        root = header['mcsrc'] if header['mcsrc'] not in ('none', '') else header['mcroot']
        if root in ('unset', 'unreadable', 'none', ''):
            return '', '', 'snapshot %s and no record-time root; seed PS2X_MC_ROOT by hand' % snap
        return root, 'snapshot %s at record time; PS2X_MC_ROOT=%s is the record-time path' % (snap, root), ''
    snap_dir = os.path.join(os.path.dirname(os.path.abspath(rec_path)), snap)
    if not os.path.isdir(snap_dir):
        return '', '', 'snapshot dir missing: %s' % snap_dir
    try:
        hexval, count = snapshot_hash(snap_dir)
    except (OSError, ValueError) as e:
        return '', '', 'cannot hash snapshot dir: %s' % e
    if hexval != header['mcsnap_sha'] or str(count) != header['mcsnap_files']:
        return '', '', 'snapshot mismatch: dir hashes to %s/%d, header has %s/%s' % (
            hexval, count, header['mcsnap_sha'], header['mcsnap_files'])
    return snap_dir, '', ''


def android_files_dir(app_id):
    return '/storage/emulated/0/Android/data/%s/files' % app_id


def android_device_path(app_id, rec_path):
    return '%s/%s' % (android_files_dir(app_id), os.path.basename(rec_path))


def local_pad_value(rec_path, text, inline):
    """The PS2X_PAD_SCRIPT value without a device push: '@'+abspath by
    default, the recording body with --inline (trailing newline stripped;
    the parser skips '#' headers and blank lines either way)."""
    if inline:
        return text.rstrip('\r\n')
    return '@' + os.path.abspath(rec_path)


def adb_push_to_android(rec_path, app_id, serial):
    """Push the recording into the app's files/ dir; return the device path."""
    dest = android_device_path(app_id, rec_path)
    cmd = ['adb']
    if serial:
        cmd += ['-s', serial]
    cmd += ['push', os.path.abspath(rec_path), dest]
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except OSError as e:
        raise RuntimeError('adb not runnable: %s' % e)
    if p.returncode != 0:
        raise RuntimeError('adb push failed: %s' % (p.stdout or '').strip()[-500:])
    return dest


def replay_env(header, rec_path, mc_root, pad_value):
    lines = ['# replay env for %s (start_utc=%s build=%s)' % (rec_path, header['start_utc'], header['build'])]
    for knob, env in KNOB_ENV:
        val = header['knobs'].get(knob, 'unset')
        if val == 'unset':
            lines.append('# %s was unset at record time' % env)
        else:
            lines.append('%s=%s' % (env, val))
    lines.append('PS2X_PAD_SCRIPT=%s' % pad_value)
    lines.append('PS2X_PAD_SCRIPT_CLOCK=vsync')
    lines.append('PS2X_MC_ROOT=%s' % mc_root)
    return '\n'.join(lines) + '\n'


def self_test():
    import tempfile
    # 1. Header parse incl. spaces in paths.
    text = ('# padrec v1\n# start_utc=2026-09-28T10:00:00Z\n# build=abc123def456\n'
            '# env_sha=0011223344556677\n'
            '# knobs SIM_MODE=split120 VU1_ENGINE=microvu MTVU=1 FINISH_TIMING=split '
            'DETERMINISTIC=1 VU_FLOAT=vf1 VIF1_REVERSE_DMA=lagV\n'
            '# mcroot=/my dir/mc0 mcsave=8899aabbccddeeff mcfiles=2\n'
            '# mcsnap=padrec-20260928-100000.mc mcsrc=/my dir/mc0 mcsnap_sha=8899aabbccddeeff mcsnap_files=2\n'
            '100:start:50\n')
    h = parse_header(text)
    assert h['build'] == 'abc123def456', h
    assert h['env_sha'] == '0011223344556677', h
    assert h['knobs']['MTVU'] == '1' and h['knobs']['VU_FLOAT'] == 'vf1', h
    assert h['knobs']['VIF1_REVERSE_DMA'] == 'lagV', h
    assert h['mcroot'] == '/my dir/mc0', h
    assert h['mcsnap'] == 'padrec-20260928-100000.mc' and h['mcsrc'] == '/my dir/mc0', h
    # 2. Pre-PL1 header defaults.
    old = parse_header('# padrec v1\n# start_utc=x\n# build=unavailable\n# knobs SIM_MODE=unset VU1_ENGINE=unset '
                       'MTVU=unset FINISH_TIMING=unset DETERMINISTIC=unset VU_FLOAT=unset\n'
                       '# mcroot=unset mcsave=unreadable mcfiles=0\n0:idle:16\n')
    assert old['env_sha'] == 'none' and old['mcsnap'] is None, old
    # 3. Snapshot hash matches the runtime scheme (FNV over sorted rels+NUL+bytes).
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, 'snap', 'BASLUS-20772'))
        with open(os.path.join(tmp, 'snap', 'BASLUS-20772', 'save.bin'), 'wb') as f:
            f.write(b'card-bytes-1')
        with open(os.path.join(tmp, 'snap', 'top.dat'), 'wb') as f:
            f.write(b'top-bytes')
        hexval, count = snapshot_hash(os.path.join(tmp, 'snap'))
        assert count == 2, (hexval, count)
        # Golden for this exact input (same bytes the pad_input_tests
        # snapshot case recomputes in C++; cross-checked by hand).
        assert hexval == '83c73a32e8b7ed34', hexval
        # Deterministic across walks.
        assert snapshot_hash(os.path.join(tmp, 'snap')) == (hexval, 2)
    # 4. APK marker scan on a fake APK.
    with tempfile.TemporaryDirectory() as tmp:
        apk = os.path.join(tmp, 'fake.apk')
        blob = b'\0' * 3000000 + b'PS2X-BUILD-ID:abc123def456\0' + b'\xff' * 1000000
        with zipfile.ZipFile(apk, 'w', zipfile.ZIP_STORED) as zf:
            zf.writestr('lib/arm64-v8a/libps2EntryRunner.so', blob)
        assert apk_build_id(apk) == 'abc123def456'
        assert check_apk(h, apk) == ''
        h2 = dict(h, build='other')
        assert 'mismatch' in check_apk(h2, apk), check_apk(h2, apk)
    # 5. Env-file hash + knob checks.
    with tempfile.TemporaryDirectory() as tmp:
        envf = os.path.join(tmp, 'ps2x.env')
        body = b'PS2X_MTVU=1\nPS2X_DETERMINISTIC=1\n'
        with open(envf, 'wb') as f:
            f.write(body)
        h3 = dict(h, env_sha=fnv1a_hex(body),
                  knobs={'SIM_MODE': 'unset', 'VU1_ENGINE': 'unset', 'MTVU': '1',
                         'FINISH_TIMING': 'unset', 'DETERMINISTIC': '1', 'VU_FLOAT': 'unset'})
        assert check_env_file(h3, envf) == ''
        with open(envf, 'wb') as f:
            f.write(b'PS2X_MTVU=0\nPS2X_DETERMINISTIC=1\n')
        assert 'mismatch' in check_env_file(h3, envf)
        assert 'env_sha=none' in check_env_file(dict(h3, env_sha='none'), envf)
    # 6. Snapshot presence/hash gating.
    with tempfile.TemporaryDirectory() as tmp:
        rec = os.path.join(tmp, 'padrec-x.txt')
        with open(rec, 'w') as f:
            f.write('# padrec v1\n0:idle:16\n')
        snap = os.path.join(tmp, 'padrec-x.mc')
        os.makedirs(os.path.join(snap, 'BASLUS-20772'))
        with open(os.path.join(snap, 'BASLUS-20772', 's'), 'wb') as f:
            f.write(b'data')
        hexval, count = snapshot_hash(snap)
        h4 = dict(h, mcsnap='padrec-x.mc', mcsnap_sha=hexval, mcsnap_files=str(count))
        mc, warn, ref = check_snapshot(h4, rec)
        assert ref == '' and warn == '' and mc == snap, (mc, warn, ref)
        h5 = dict(h4, mcsnap_sha='0' * 16)
        assert 'mismatch' in check_snapshot(h5, rec)[2]
        h6 = dict(h4, mcsnap='padrec-gone.mc')
        assert 'missing' in check_snapshot(h6, rec)[2]
        h7 = dict(h, mcsnap='none', mcroot='/rec/time/mc0')
        mc, warn, ref = check_snapshot(h7, rec)
        assert ref == '' and mc == '/rec/time/mc0' and 'no per-session snapshot' in warn
    # 7. Replay env shape (RP2: '@' file form by default).
    env = replay_env(h, '/tmp/r/padrec-x.txt', '/tmp/r/padrec-x.mc', '@/tmp/r/padrec-x.txt')
    assert 'PS2X_MTVU=1\n' in env and 'PS2X_PAD_SCRIPT_CLOCK=vsync\n' in env, env
    assert 'PS2X_MC_ROOT=/tmp/r/padrec-x.mc\n' in env, env
    assert 'PS2X_PAD_SCRIPT=@/tmp/r/padrec-x.txt\n' in env, env
    assert 'PS2X_VIF1_REVERSE_DMA=lagV\n' in env, env
    # 8. RP2 pad value forms.
    assert local_pad_value('/tmp/r/padrec-x.txt', '# h\n0:idle:16\n', False) == '@/tmp/r/padrec-x.txt'
    assert local_pad_value('/tmp/r/padrec-x.txt', '# h\n0:idle:16\n', True) == '# h\n0:idle:16'
    assert local_pad_value('/tmp/r/padrec-x.txt', '0:idle:16\r\n', True) == '0:idle:16'
    assert android_device_path('com.x', '/tmp/r/padrec-x.txt') == \
        '/storage/emulated/0/Android/data/com.x/files/padrec-x.txt'
    assert android_device_path('com.x', 'padrec-x.txt') == \
        '/storage/emulated/0/Android/data/com.x/files/padrec-x.txt'
    print('padrec_replay_env self-test: 8/8 OK')


def main(argv):
    if len(argv) == 2 and argv[1] == '--self-test':
        self_test()
        return 0
    apk, envf, rec, push_to = None, None, None, None
    app_id, serial = None, ''
    inline = False
    args = argv[1:]
    i = 0
    usage = ('usage: padrec_replay_env.py <recording> [--apk APK] [--env-file FILE]\n'
             '                               [--push-to android|mac] [--app-id ID] [--serial SER] [--inline]\n')
    while i < len(args):
        if args[i] == '--apk' and i + 1 < len(args):
            apk, i = args[i + 1], i + 2
        elif args[i] == '--env-file' and i + 1 < len(args):
            envf, i = args[i + 1], i + 2
        elif args[i] == '--push-to' and i + 1 < len(args) and args[i + 1] in ('android', 'mac'):
            push_to, i = args[i + 1], i + 2
        elif args[i] == '--app-id' and i + 1 < len(args):
            app_id, i = args[i + 1], i + 2
        elif args[i] == '--serial' and i + 1 < len(args):
            serial, i = args[i + 1], i + 2
        elif args[i] == '--inline':
            inline, i = True, i + 1
        elif args[i].startswith('--') or rec is not None:
            sys.stderr.write(usage)
            return 2
        else:
            rec, i = args[i], i + 1
    if rec is None:
        sys.stderr.write(usage)
        return 2
    if inline and push_to is not None:
        sys.stderr.write('--inline and --push-to are mutually exclusive\n')
        return 2
    try:
        with open(rec) as f:
            text = f.read()
        header = parse_header(text)
    except OSError as e:
        sys.stderr.write('cannot read %s: %s\n' % (rec, e))
        return 2
    except ValueError as e:
        sys.stderr.write('bad recording header: %s\n' % e)
        return 2
    if apk is not None:
        try:
            refusal = check_apk(header, apk)
        except (OSError, ValueError, zipfile.BadZipFile) as e:
            sys.stderr.write('cannot check APK: %s\n' % e)
            return 2
        if refusal:
            sys.stderr.write('REFUSED: %s\n' % refusal)
            return 1
    if envf is not None:
        try:
            refusal = check_env_file(header, envf)
        except OSError as e:
            sys.stderr.write('cannot check env file: %s\n' % e)
            return 2
        if refusal:
            sys.stderr.write('REFUSED: %s\n' % refusal)
            return 1
    mc_root, warning, refusal = check_snapshot(header, rec)
    if refusal:
        sys.stderr.write('REFUSED: %s\n' % refusal)
        return 1
    if warning:
        sys.stderr.write('warning: %s\n' % warning)
    if push_to == 'android':
        if app_id is None:
            sys.stderr.write('--push-to android needs --app-id\n')
            return 2
        try:
            dest = adb_push_to_android(rec, app_id, serial)
        except RuntimeError as e:
            sys.stderr.write('cannot stage on the device: %s\n' % e)
            return 2
        sys.stderr.write('staged %s -> %s\n' % (rec, dest))
        pad_value = '@' + dest
    else:
        pad_value = local_pad_value(rec, text, inline)
    sys.stdout.write(replay_env(header, rec, mc_root, pad_value))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
