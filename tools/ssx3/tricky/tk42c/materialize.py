#!/usr/bin/env python3
"""TK42c: materialize a PS2XCMP1 composite against the ISO (read-only on pinned inputs).

Usage: materialize.py COMPOSITE.BIG ISO OUT.BIG
Descriptor: 'PS2XCMP1', 'size N', 'image S', segs ('self OFF COUNT' byte-off/count
in 2048-B sectors of this file; 'iso LBN COUNT' sectors of the ISO), 'end'.
"""
import sys
SEC = 2048


def main():
    comp_p, iso_p, out_p = sys.argv[1:4]
    blob = open(comp_p, 'rb').read()
    text = blob[:SEC].split(b'\x00')[0].decode()
    lines = text.splitlines()
    assert lines[0] == 'PS2XCMP1', lines[0]
    size = int(lines[1].split()[1])
    segs = [l.split() for l in lines[3:] if l != 'end']
    out = bytearray()
    with open(iso_p, 'rb') as f:
        for k, x, n in segs:
            x, n = int(x), int(n)
            if k == 'self':
                out += blob[x:x + n * SEC]
            else:
                f.seek(x * SEC)
                out += f.read(n * SEC)
    out = bytes(out[:size])
    assert len(out) == size, (len(out), size)
    assert out[:4] == b'BIGF', out[:4]
    open(out_p, 'wb').write(out)
    print(f'materialized {len(out)} bytes -> {out_p}')


if __name__ == '__main__':
    main()
