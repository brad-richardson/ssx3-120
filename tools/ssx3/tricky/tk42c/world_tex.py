#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK42b: trace world scenery instance -> kind3 -> model -> kind0 materials -> kind9 textures.

Usage: world_tex.py WORLD.BIG GROUP INST [INST ...]
Prints the chain with texture WxH + distinct-pixel/palette counts (unswizzled).
"""
import struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tk12')); sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tk13'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'shared'))
from wlib import W
from ps2mdr import parse_model, model_chunks
from ps2swz import unswizzle8

def main():
    w = W(sys.argv[1])
    G = int(sys.argv[2])
    recs = w.records(G)
    k3 = {e['rid']: p for e, p in recs if e['kind'] == 3}
    k2 = {e['rid']: p for e, p in recs if e['kind'] == 2}
    k0 = {e['rid']: p for e, p in recs if e['kind'] == 0}
    # kind9 live in the texture group: scan all groups
    k9 = {}
    for g in w.sdb['groups']:
        for e, p in w.records(g['index']):
            if e['kind'] == 9:
                k9.setdefault(e['rid'], []).append((g['index'], p))
    print(f'group {G}: kind3={len(k3)} kind2={len(k2)} kind0={len(k0)} kind9_total={sum(map(len,k9.values()))}')
    for a in sys.argv[3:]:
        i = int(a)
        p = k3[i]
        oid_self, page, oid_model = struct.unpack_from('<3I', p, 120)
        model_rid = oid_model >> 8
        m = parse_model(k2[model_rid])
        print(f'--- world inst {i}: model_rid={model_rid} nmats={len(m["materials"])}')
        for k, mh in enumerate(m['objects'][0]['meshes']):
            mat_oid = m['materials'][mh['material']]
            mat_rid = mat_oid >> 8
            mp = k0[mat_rid]
            v = struct.unpack('<10h', mp[:20])
            tex = v[0]
            info = ''
            if tex in k9:
                for gi, tp in k9[tex][:1]:
                    nxt = int.from_bytes(tp[1:4], 'little'); ww, hh = struct.unpack_from('<HH', tp, 4)
                    pix = unswizzle8(tp[128:128+ww*hh], ww, hh)
                    pal = tp[nxt+128:nxt+128+1024]
                    info = (f' kind9 group={gi} {ww}x{hh} distinct_px={len(set(pix))} '
                            f'distinct_pal={len(set(pal[j:j+4] for j in range(0,1024,4)))}')
            else:
                info = ' KIND9 MISSING'
            print(f'    mesh{k} mat_rid={mat_rid} u1={mh["u1"]} hflag={mh["hflag"]} mat={v}{info}')

if __name__ == '__main__':
    main()
