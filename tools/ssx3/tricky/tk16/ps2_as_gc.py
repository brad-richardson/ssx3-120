#!/usr/bin/env python3
"""TK16: rebuild the GameCube-layout gameplay file (.gsf) and a stub .nbd from Tricky PS2 course files.

    ps2_as_gc.py DONOR.pbd DONOR.ssf OUT.gsf OUT.nbd [--check-gsf GC.gsf --check-nbd GC.nbd]

Why: TK13's make_scenery.py / make_rails.py read the donor's gameplay rows, collision meshes, visibility
program and rails from the GameCube .gsf/.nbd. Merqury City has no GameCube files; the PS2 course has the
same data in its .ssf (gameplay) and .pbd (instances, splines).

Layout (measured on gari/snow/mesa, where both exist): the PS2 .ssf has the GC .gsf's size and offsets and is
its 32-bit word byte-swap, except
  - the function-name strings (function table +8..+24) are bytes, not words;
  - the property records (24 B: f32 u0, f32 bounce, u16 pair, u32, 4 x i16): the PS2 u16 pair is (u2, flags),
    the GC pair (flags, u2); the 4 x i16 keep their order;
  - header words 1 and 2 (not read by any tool).
Splines: the .pbd spline headers (40 B) are the word-swapped GC NBD headers; segments (128 B) likewise except the
last word (+124, unread). The stub .nbd carries only what the TK13 tools read: header words 3 (instance count),
8/9 (spline counts), 18 (instance table, 140-B rows with the model index at +64), 23/24/25 (spline tables).
"""
import argparse, struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] /'tk13'))
from tricky_pbd import PBD

NBD_MAGIC = None


def wswap(b):
    assert len(b) % 4 == 0
    return b''.join(b[i:i + 4][::-1] for i in range(0, len(b), 4))


def ssf_to_gsf(ssf):
    if ssf[:4] != b'\x00\x02\x1e\x00':
        raise ValueError('not a Tricky PS2 SSF')
    g = bytearray(wswap(ssf[:len(ssf) // 4 * 4]) + ssf[len(ssf) // 4 * 4:])
    g[0:12] = ssf[0:4] + wswap(ssf[4:12])  # magic is already the GC byte order
    effects, effect_table, functions, function_table, _, end = struct.unpack_from('<6I', ssf, 36)
    for i in range(functions):
        o = function_table + 24 * i
        g[o + 8:o + 24] = ssf[o + 8:o + 24]
    count, start = struct.unpack_from('<2I', ssf, 52)
    for i in range(count):
        o = start + 24 * i
        u0, bounce, u2, flags, w, a, b, c, d = struct.unpack_from('<2f2HI4h', ssf, o)
        struct.pack_into('>2f2HI4h', g, o, u0, bounce, flags, u2, w, a, b, c, d)
    return bytes(g)


def stub_nbd(pbd_bytes, magic):
    p = PBD(pbd_bytes)
    n = len(p.instances)
    ns, nseg = p.counts['splines'], p.counts['spline_segments']
    inst_at = 160
    spl_at = inst_at + 140 * n
    spl_at += -spl_at % 16
    seg_at = spl_at + 40 * ns
    seg_at += -seg_at % 16
    end = seg_at + 128 * nseg
    h = [0] * 40
    h[0] = magic
    h[3] = n
    h[8], h[9] = ns, nseg
    h[18] = inst_at
    h[23], h[24], h[25] = spl_at, seg_at, end
    out = bytearray(end)
    struct.pack_into('>40I', out, 0, *h)
    for i, inst in enumerate(p.instances):
        struct.pack_into('>I', out, inst_at + 140 * i + 64, inst['model'])
    d = p.d
    out[spl_at:spl_at + 40 * ns] = wswap(d[p.offs['spline']:p.offs['spline'] + 40 * ns])
    segs = bytearray(wswap(d[p.offs['spline_segment']:p.offs['spline_segment'] + 128 * nseg]))
    for j in range(nseg):
        segs[128 * j + 124:128 * j + 128] = bytes(4)  # unread word; PS2 and GC differ here
    out[seg_at:end] = segs
    return bytes(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pbd'); ap.add_argument('ssf'); ap.add_argument('out_gsf'); ap.add_argument('out_nbd')
    ap.add_argument('--check-gsf'); ap.add_argument('--check-nbd')
    a = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] /'shared'))
    from gamecube_splines import read_tricky_splines
    from gamecube_scenery import NBD_MAGICS, instance_gameplay, post_countdown_hidden
    from gamecube_collision import read_tricky_collision
    pbd = Path(a.pbd).read_bytes()
    gsf = ssf_to_gsf(Path(a.ssf).read_bytes())
    nbd = stub_nbd(pbd, sorted(NBD_MAGICS)[0])
    Path(a.out_gsf).write_bytes(gsf); Path(a.out_nbd).write_bytes(nbd)
    n = len(PBD(pbd).instances)
    rows = instance_gameplay(gsf, n); hidden = post_countdown_hidden(gsf, n)
    col = read_tricky_collision(gsf); spl = read_tricky_splines(nbd, gsf)
    print(f'instances {n} hidden {len(hidden)} collision meshes {len(col)} rails {len(spl)}')
    if a.check_gsf:
        ref = Path(a.check_gsf).read_bytes()
        diff = [i for i in range(0, min(len(ref), len(gsf)), 4) if ref[i:i + 4] != gsf[i:i + 4]]
        print(f'gsf vs GC: size {len(gsf)} / {len(ref)}; differing words {len(diff)} at {diff[:8]}')
        print('  gameplay rows equal', instance_gameplay(ref, n) == rows,
              '| hidden equal', post_countdown_hidden(ref, n) == hidden,
              '| collision equal', read_tricky_collision(ref) == col)
    if a.check_nbd:
        ref = Path(a.check_nbd).read_bytes()
        gs = read_tricky_splines(ref, Path(a.check_gsf).read_bytes() if a.check_gsf else gsf)
        print('  splines equal', gs == spl, len(gs))
        h = struct.unpack_from('>40I', ref)
        print('  instance models equal', [struct.unpack_from('>I', ref, h[18] + 140 * i + 64)[0] for i in range(h[3])] ==
              [struct.unpack_from('>I', nbd, 160 + 140 * i + 64)[0] for i in range(n)])


if __name__ == '__main__':
    main()
