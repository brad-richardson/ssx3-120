#!/usr/bin/env python3
"""Compare [det-hash:v1] lines between two det runs.

Provenance: verbatim logic from the lab gate's GB8 gb8_hashdiff.py
(ssx3-lab local/research/GB8/gb8_hashdiff.py); only the module docstring
changed. tests/det/compare.py ports the same logic for shipped keys.
"""

import argparse
import re
import sys
from pathlib import Path

# Unanchored, and stops at the next '[': the runtime's stderr writes can interleave (a [frame:dump]
# line without its newline before a det-hash line, SS1/MD1), which otherwise reads as a missing tick.
HASH_LINE = re.compile(rb'\[det-hash:v1\] tick=(\d+)\b[^\n\[]*')
# Other stderr writers (e.g. 'INFO: FILEIO: ...', no '[') can also land mid-line; everything from the
# marker on is not hash content. Such a line is kept as a truncated prefix and matches when it is a
# strict prefix of the other side's line (SQ1 verified 12/12 such cases by hand; RB1 09-27).
INTERLEAVE = re.compile(rb'(INFO:|WARN:|ERROR:)')


def hash_lines(run):
    data = (Path(run) / 'boot.log').read_bytes()
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--cand', required=True)
    ap.add_argument('--min-tick', type=int, default=2400)
    # FH2: keys minted with PS2X_DET_HASH_EVERY=N hash only ticks N, 2N, ...
    ap.add_argument('--hash-every', type=int, default=1)
    args = ap.parse_args()
    hb, hc = hash_lines(args.base), hash_lines(args.cand)
    step = max(1, args.hash_every)
    want = range(step, args.min_tick + 1, step)
    missing_b = [t for t in want if t not in hb]
    missing_c = [t for t in want if t not in hc]
    first_diff = next((t for t in want if t in hb and t in hc and not same(hb[t], hc[t])), None)
    truncated = sum(1 for t in want if (t in hb and hb[t][1]) or (t in hc and hc[t][1]))
    good = not missing_b and not missing_c and first_diff is None
    print('hash %s  ticks 1..%d: base=%s lines=%d cand=%s lines=%d missing=%d/%d first_diff=%s truncated_prefix_ok=%d'
          % ('IDENTICAL' if good else 'DIFFER', args.min_tick, args.base, len(hb),
             args.cand, len(hc), len(missing_b), len(missing_c), first_diff, truncated))
    if missing_b[:5] or missing_c[:5]:
        print('  missing base[:5]=%s cand[:5]=%s' % (missing_b[:5], missing_c[:5]))
    if first_diff is not None:
        print('  base: %s' % hb[first_diff][0].decode(errors='replace'))
        print('  cand: %s' % hc[first_diff][0].decode(errors='replace'))
    return 0 if good else 1


if __name__ == '__main__':
    sys.exit(main())
