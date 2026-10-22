#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK21: dump Tricky PS2 .pbd per-object animation (layout: SSX-Library PBDHandler.cs ObjectAnimation, read 10-01).
    anim_dump.py DONOR.pbd MODEL[,MODEL...]
Per animated object: U1..U6 (initial pos/rot?), action bitmask (1 X, 2 Y, 4 Z pos; 8/16/32 X/Y/Z rot), entries of 6-float rows."""
import struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tk13'))
from tricky_pbd import PBD
def curves(d, p, mi):
    """{object: {channel bit: [rows]}} with rows (a, b, c, d, t0, t1): value = a t^3 + b t^2 + c t + d on [t0, t1] (verified
    continuous at the knots on model 292). Entries map to set action bits in ascending order."""
    ptr = struct.unpack_from('<I', d, p.offs['model_pointer'] + 4 * mi)[0]; b = p.offs['model'] + ptr
    nobj, objoff = struct.unpack_from('<2I', d, b + 4)
    out = {}
    for a in range(nobj):
        o = b + objoff + 24 * a
        an = struct.unpack_from('<i', d, o + 16)[0]
        if an == 0: continue
        q = o + an
        act, cnt = struct.unpack_from('<2I', d, q + 24)
        bits = [1 << k for k in range(6) if act >> k & 1]
        offs = struct.unpack_from(f'<{cnt}I', d, q + 36)
        ch = {}
        for bit, eo in zip(bits, offs):
            mc = struct.unpack_from('<I', d, q + eo)[0]
            ch[bit] = [struct.unpack_from('<6f', d, q + eo + 8 + 24 * c) for c in range(mc)]
        out[a] = ch
    return out


def value(rows, t):
    for a, b, c, dd, t0, t1 in rows:
        if t0 <= t <= t1: return ((a * t + b) * t + c) * t + dd
    return None


if __name__ == '__main__':
    d = open(sys.argv[1], 'rb').read(); p = PBD(d)
    for mi in map(int, sys.argv[2].split(',')):
        ptr = struct.unpack_from('<I', d, p.offs['model_pointer'] + 4 * mi)[0]; b = p.offs['model'] + ptr
        total, nobj, objoff = struct.unpack_from('<3I', d, b)
        print(f'model {mi}: {nobj} objects, anim_time {struct.unpack_from("<f", d, b + 20)[0]:.3f}, scale {struct.unpack_from("<3f", d, b + 24)}')
        for a in range(nobj):
            o = b + objoff + 24 * a
            par, hi, me, lo, an, mx = struct.unpack_from('<6i', d, o)
            bbox = struct.unpack_from('<6f', d, o + hi + 4) if hi not in (0, -1) else None
            print(f'  obj {a} parent {par} mesh {"Y" if bbox else "N"} bbox {[round(x,1) for x in bbox] if bbox else ""}')
            if an == 0: continue
            q = o + an
            u = struct.unpack_from('<6f', d, q); act, cnt, st = struct.unpack_from('<3I', d, q + 24)
            offs = struct.unpack_from(f'<{cnt}I', d, q + 36)
            print(f'    U {[round(x,3) for x in u]} action {act:#x} entries {cnt}')
            for k, eo in enumerate(offs):
                mc, mo = struct.unpack_from('<2I', d, q + eo)
                rows = [struct.unpack_from('<6f', d, q + eo + 8 + 24 * c) for c in range(mc)]
                print(f'    entry {k}: {mc} rows')
                for r in rows[:12]: print('      ', [round(x, 3) for x in r])
                if mc > 12: print('       ...', [round(x, 3) for x in rows[-1]])
