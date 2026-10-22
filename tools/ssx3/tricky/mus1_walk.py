#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""mus1_walk.py: simulate SSX 3's PathFinder v4 node walk on an .mpf (MUS1).

Models the game's own code (codegen of the pinned SLUS_207.72):
  event table  0x3D2350 open: slot+0x40 = event index table; entry = mpf + 4*u16
  event sub    0x3CFC30 executor: cmd = byte 9 (4 = PATHACTION_BRANCHTO),
               track mask = low bytes of word 0, node = s16 at +0xC
  walk         0x3D3900: from target node, while header s16 sample <= 0
               (0 = head, -1 = link) pick the next node with 0x3D37A0
  pick         0x3D37A0: subentry [lo:s8, hi:s8, node:s16]; first with
               lo <= v <= hi wins (v = track intensity byte +1, set by
               0x3D0D70; links with a loop nibble use the loop counter)
  play         0x3D41A8: sample s (1-based) -> entry slot+0x50 + 8*(s-1)

usage: mus1_walk.py <file.mpf> <event> [v ...]   (default v = 0..127 summary)
"""
import struct
import sys


def load(path):
    b = open(path, "rb").read()
    assert b[0:4] == b"xDFP" and b[4] == 4, "not a v4 mpf"
    u16 = lambda o: struct.unpack_from("<H", b, o)[0]
    u32 = lambda o: struct.unpack_from("<I", b, o)[0]
    nn, ne = u16(0x12), b[0x0F]
    nodes = [u16(0x20 + 2 * i) * 4 for i in range(nn)]
    last = nodes[-1]
    cnt = (u32(last + 4) >> 12) & 0x1F
    evt = (last + cnt * 4 + 0x13) & ~3                       # 0x3d24c8..cc
    events = [struct.unpack_from("<h", b, evt + 2 * i)[0] * 4 for i in range(ne)]
    return b, nodes, events


def node(b, nodes, i):
    o = nodes[i]
    s = struct.unpack_from("<h", b, o)[0]
    w0 = struct.unpack_from("<I", b, o)[0]
    w1 = struct.unpack_from("<I", b, o + 4)[0]
    c = (w1 >> 12) & 0x1F
    subs = [struct.unpack_from("<bbh", b, o + 16 + 4 * k) for k in range(c)]
    return s, w0, w1, subs


def pick(subs, v):
    for lo, hi, nxt in subs:                                  # 0x3d3840..3874
        if lo <= v <= hi and nxt >= 0:
            return nxt
    if not subs:
        return -1
    best, bd = subs[0][2], 0x7F                               # nearest range
    for lo, hi, nxt in subs:
        d = min(abs(lo - v), abs(hi - v))
        if d < bd:
            bd, best = d, nxt
    return best


def walk(b, nodes, start, v, limit=64):
    path, cur = [start], start
    for _ in range(limit):
        if not (0 <= cur < len(nodes)):
            return path, "bad-node"
        s, w0, w1, subs = node(b, nodes, cur)
        if s > 0:
            return path, f"play s={s}"
        lv = v
        if s == -1 and (w0 >> 28) & 0xF:                      # loop nibble
            lv = ((w0 >> 28) ^ 8) - 8 & 0x7F if (w0 >> 28) == 0xF else v
        cur = pick(subs, lv)
        path.append(cur)
        if cur in path[:-1] and node(b, nodes, cur)[0] <= 0:
            return path, "SPIN (cycle of non-playing nodes)"
    return path, "limit"


def event_targets(b, events, e):
    o = events[e]
    ns = struct.unpack_from("<H", b, o + 0xE)[0] & 0x3F
    out = []
    for k in range(ns):
        sub = b[o + 16 + 16 * k:o + 32 + 16 * k]
        if sub[9] == 4:
            out.append((sub[0], struct.unpack_from("<h", sub, 12)[0]))
    return out


def main():
    path, e = sys.argv[1], int(sys.argv[2])
    vs = [int(x, 0) for x in sys.argv[3:]] or list(range(128))
    b, nodes, events = load(path)
    tg = event_targets(b, events, e)
    print(f"{path}: event {e} BRANCHTO (mask, node) = {tg}")
    for mask, t in tg:
        res = {}
        for v in vs:
            p, r = walk(b, nodes, t, v)
            key = (r, tuple(p[:8]))
            res.setdefault(key, []).append(v)
        for (r, p), vv in res.items():
            rng = f"v={vv[0]}..{vv[-1]}" if len(vv) > 1 else f"v={vv[0]}"
            print(f"  mask {mask:#x} node {t}: {rng:12s} -> {r:36s} path {list(p)}")


if __name__ == "__main__":
    main()
