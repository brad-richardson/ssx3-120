#!/usr/bin/env python3
"""TK42c: map flagged donor textures -> preset-world kind-9/kind-0 records.

Usage: map_blend.py WORLD.BIG DONOR_SSH CENSUS.json EFFECTS.json HOSTLOC
HOSTLOC = host location (texture namespaces are per-location).
- Parses every kind-9 in every group; matches palettes (CSM1->natural) + pixels
  against the flagged donor textures from the census.
- Reports world kind-0 records referencing matched kind-9 rids (group, rid, value).
- Names flagged donor instances from the effects JSON.
"""
import json, struct, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tk12'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tk13'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'shared'))
from wlib import W
from ps2swz import unswizzle8, csm1


def ssh_entries(ssh):
    n = struct.unpack_from('<I', ssh, 8)[0]
    return [struct.unpack_from('<I', ssh, 20 + 8 * j)[0] for j in range(n)]


def donor_tex(ssh, offs, t):
    o = offs[t]
    nxt = int.from_bytes(ssh[o + 1:o + 4], 'little')
    w, h = struct.unpack_from('<HH', ssh, o + 4)
    pix = bytes(ssh[o + 16:o + 16 + w * h])
    pal = bytes(ssh[o + nxt + 16:o + nxt + 16 + 1024])
    return w, h, pix, pal


def world_k9(payload):
    """Parse kind-9 payload -> (w,h,pix_linear,pal_natural) or None."""
    try:
        if payload[0] != 2:
            return None
        nxt = int.from_bytes(payload[1:4], 'little')
        w, h = struct.unpack_from('<HH', payload, 4)
        if w <= 0 or h <= 0 or w > 1024 or h > 1024:
            return None
        if 128 + w * h > len(payload) or nxt + 128 + 1024 > len(payload):
            return None
        pix = unswizzle8(payload[128:128 + w * h], w, h)
        csm = payload[nxt + 128:nxt + 128 + 1024]
        nat = bytearray(1024)
        for i in range(256):
            nat[4 * i:4 * i + 4] = csm[4 * csm1(i):4 * csm1(i) + 4]
        return w, h, bytes(pix), bytes(nat)
    except Exception:
        return None


def main():
    world_p, ssh_p, census_p, fx_p, host = sys.argv[1:6]
    census = json.load(open(census_p))
    ssh = open(ssh_p, 'rb').read()
    offs = ssh_entries(ssh)
    flagged_tex = sorted({r['texidx'] for r in census['rows'] if r['ints'][5] == '0x35008'})
    donor = {t: donor_tex(ssh, offs, t) for t in flagged_tex}
    print(f'# flagged donor tex: {flagged_tex}')

    w = W(world_p)
    host_groups = [g for g in w.sdb['groups'] if g['location'] == host]
    print(f"# host {host}: groups {[g['index'] for g in host_groups]}")
    # index host-location kind-9 by natural palette
    pal_index = {}
    n_k9 = 0
    for g in host_groups:
        for e, p in w.records(g['index']):
            if e['kind'] != 9:
                continue
            n_k9 += 1
            parsed = world_k9(p)
            if parsed is None:
                continue
            ww, hh, wpix, wpal = parsed
            pal_index.setdefault(wpal, []).append((g['index'], e['rid'], ww, hh, wpix))
    print(f'# world kind-9 total={n_k9} parsed-palette-keys={len(pal_index)}')

    matched_rids = {}  # donor tex -> list of (group, rid, w, h, pixmatch)
    for t, (dw, dh, dpix, dpal) in donor.items():
        hits = pal_index.get(dpal, [])
        # downsample-aware pixel confirm: world pix must equal donor pix or its halves
        cands = [dpix]
        cw, ch, cp = dw, dh, dpix
        while cw % 32 == 0 and ch % 8 == 0 and cw > 16 and ch > 4:
            cp = b''.join(cp[r * cw:(r + 1) * cw:2] for r in range(0, ch, 2))
            cw, ch = cw // 2, ch // 2
            cands.append(cp)
        ok = []
        for gi, rid, ww, hh, wpix in hits:
            ok.append((gi, rid, ww, hh, wpix in cands))
        matched_rids[t] = ok
        print(f'# donor tex {t} ({dw}x{dh}): {len(hits)} palette hits -> {ok}')

    # kind-0 records referencing matched kind-9 rids
    want = {rid for v in matched_rids.values() for _, rid, _, _, _ in v}
    print(f'# matched kind-9 rids: {sorted(want)}')
    k0 = []
    for g in host_groups:
        for e, p in w.records(g['index']):
            if e['kind'] != 0:
                continue
            v = struct.unpack('<10h', p[:20])
            if v[0] in want:
                k0.append(dict(group=g['index'], rid=e['rid'], mat=list(v)))
    print(f'# kind-0 referencing matched tex: {len(k0)}')
    for r in k0:
        print(f"  group={r['group']} rid={r['rid']} mat={r['mat']}")

    # name flagged donor instances
    fx = json.load(open(fx_p)) if fx_p != 'none' else {'instances': []}
    insts = {i['instance']: i for i in fx['instances']}
    for r in census['rows']:
        if r['ints'][5] != '0x35008':
            continue
        names = [(i, insts.get(i, {}).get('name', '?'),
                  insts.get(i, {}).get('visible', '?')) for i in r['insts']]
        # full instance list may exceed the 12 stored; note count
        print(f"# mat {r['mat']} tex {r['texidx']} ninst={r['ninst']}: {names}")


if __name__ == '__main__':
    main()
