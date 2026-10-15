#!/usr/bin/env python3
"""Public det-key comparator: shipped key vs candidate run dir.

Key dir (tests/det/keys/<key>/): manifest.json (pins), hashes.bin (raw
[det-hash:v1] lines), coverage.txt ([coverage:*] lines), snd.txt
("vsync\\trow" host-noise-stripped tick rows).

Candidate run dir: boot.log + snd.log from tests/det/boot/ssx3_boot.py.

The three comparisons below are logic-verbatim ports of the canonical lab
gate (provenance: ssx3-lab local/tooling/boot/baseline.py
parse_snd_log/snd_bound_ok/coverage_lines and GB8 gb8_hashdiff.py
HASH_LINE/INTERLEAVE/same), so a public IDENTICAL means the canonical gate
would say IDENTICAL on the same bytes. Prints a final
`compare <key> vs <cand>: IDENTICAL|DIFFER` line.
Usage: compare.py --key DIR --cand DIR
"""
import argparse
import re
import sys
from pathlib import Path

# --- canonical: gb8_hashdiff.py ---
HASH_LINE = re.compile(rb'\[det-hash:v1\] tick=(\d+)\b[^\n\[]*')
INTERLEAVE = re.compile(rb'(INFO:|WARN:|ERROR:)')


def hash_lines_blob(data):
    out = {}
    for m in HASH_LINE.finditer(data):
        line = m.group(0)
        cut = INTERLEAVE.search(line)
        out[int(m.group(1))] = (line[:cut.start()], True) if cut else (line, False)
    return out


def same(b, c):
    (lb, tb), (lc, tc) = b, c
    if not tb and not tc:
        return lb == lc
    short, full = (lb, lc) if len(lb) <= len(lc) else (lc, lb)
    return len(short) > 30 and full.startswith(short)


# --- canonical: baseline.py ---
COVERAGE_LINE = re.compile(r'^\[coverage:[^\]]+\].*$', re.M)
RAYLIB_TRACE = r'(?:TRACE|DEBUG|INFO|WARNING|ERROR|FATAL): '
COVERAGE_SPLICE = re.compile(
    r'^(\[coverage:[^\]]+\][^\n]*?)' + RAYLIB_TRACE + r'[^\n]*\n(?!\[|' + RAYLIB_TRACE + r')([^\n]*)$',
    re.M)
SND_TICK = re.compile(r'^tick .*cid0=.*$', re.M)
SND_HOST_NOISE = re.compile(r' underruns=\d+ overflows=\d+')
SND_SETDMA = re.compile(r'^setdma vsync=(\d+)')


def coverage_lines(boot_text):
    while True:
        fixed = COVERAGE_SPLICE.sub(lambda m: m.group(1) + m.group(2), boot_text, count=1)
        if fixed == boot_text:
            break
        boot_text = fixed
    return COVERAGE_LINE.findall(boot_text)


def parse_snd_log_text(data):
    """Canonical parse_snd_log over decoded text: only newline-terminated
    lines enter the rows; a trailing partial line is dropped."""
    if not data:
        return ['(no tick lines)'], [], False, ''
    truncated = not data.endswith('\n')
    lines = data.splitlines()
    fragment = ''
    if truncated and lines:
        fragment = lines[-1]
        lines = lines[:-1]
    return list(parse_snd_rows(lines)) + [truncated, fragment]


def parse_snd_rows(lines):
    ticks, vsyncs, last_vsync = [], [], 0
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
    return ticks, vsyncs


def snd_bound_count(vsyncs, target):
    n = 0
    for v in vsyncs:
        if v <= target:
            n += 1
        else:
            break
    return n


def snd_bound_ok(snd_b, vs_b, snd_c, vs_c, target):
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
# --- end canonical ports ---


def main():
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument('--key', required=True)
    ap.add_argument('--cand', required=True)
    args = ap.parse_args()
    key, cand = Path(args.key), Path(args.cand)
    man = json.loads((key / 'manifest.json').read_text())
    pins = man['pins']
    target = pins['stop_tick']
    step = max(1, pins.get('hash_every', 1))

    base_hash = {}
    for line in (key / 'hashes.bin').read_bytes().split(b'\n'):
        if not line:
            continue
        m = HASH_LINE.match(line)
        if m:
            base_hash[int(m.group(1))] = (line, False)
    cand_data = (cand / 'boot.log').read_bytes()
    cand_hash = hash_lines_blob(cand_data)
    want = range(step, target + 1, step)
    missing_b = [t for t in want if t not in base_hash]
    missing_c = [t for t in want if t not in cand_hash]
    first_diff = next((t for t in want
                       if t in base_hash and t in cand_hash
                       and not same(base_hash[t], cand_hash[t])), None)
    hash_ok = not missing_b and not missing_c and first_diff is None
    print('hash %s ticks %d..%d step %d: base=%d cand=%d missing=%d/%d first_diff=%s'
          % ('IDENTICAL' if hash_ok else 'DIFFER', step, target, step,
             len(base_hash), len(cand_hash),
             len(missing_b), len(missing_c), first_diff))

    cov_b = (key / 'coverage.txt').read_text().splitlines()
    cov_c = coverage_lines(cand_data.decode(errors='replace'))
    coverage_ok = sorted(cov_b) == sorted(cov_c)

    snd_b, vs_b = [], []
    for line in (key / 'snd.txt').read_text().splitlines():
        if not line:
            continue
        v, s = line.split('\t', 1)
        vs_b.append(int(v))
        snd_b.append(s)
    snd_cp = cand / 'snd.log'
    if snd_cp.exists():
        snd_c, vs_c, _tr, _fr = parse_snd_log_text(snd_cp.read_bytes().decode(errors='replace'))
    else:
        snd_c, vs_c = ['(no snd.log)'], []
    snd_ok, n_common, nb, nc, first = snd_bound_ok(snd_b, vs_b, snd_c, vs_c, target)
    snd_cov_ok = coverage_ok and snd_ok
    print('snd/coverage: %s' % ('IDENTICAL' if snd_cov_ok else 'DIFFER'))
    if not snd_cov_ok:
        if not coverage_ok:
            print('  coverage base: %s' % cov_b)
            print('  coverage cand: %s' % cov_c)
        if not snd_ok:
            print('  snd lines base=%d cand=%d bound_base=%d bound_cand=%d '
                  'common=%d first_diff_line=%s (target t%d)'
                  % (len(snd_b), len(snd_c), nb, nc, n_common, first, target))
    print('compare %s vs %s: %s' % (man['key'], cand,
                                    'IDENTICAL' if hash_ok and snd_cov_ok else 'DIFFER'))
    return 0 if hash_ok and snd_cov_ok else 1


if __name__ == '__main__':
    sys.exit(main())
