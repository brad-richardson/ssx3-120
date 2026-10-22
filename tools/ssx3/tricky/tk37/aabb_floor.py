#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK37: floor each imported instance's world AABB so it lands in a level-L spatial cell (default 11), in place.

    aabb_floor.py WORLD.BIG OUT.BIG [--group G] [--level 11] [--receipt R.json]

Why (TK37 REPORT): SSX 3 inserts every kind-3 instance into its location's spatial tree at
level = exponent(max_extent * 0.7142857) (func_328F28, capped at 11). The camera visibility walk
(sub_0022A830) appends every straddling node to a 512-slot list on the stack with no bound check, so a
dense Tricky import (all 2,893 Elysium instances in one location) overflows it and corrupts the query
(guest JALR to garbage). Lifting small instances to level-11 cells halves the straddling node count.

Rule: only the longest axis of the AABB at +96/+108 is widened, symmetric about its centre, to
2^L / 0.7142857 (x 1.0001) when shorter; the other axes, the bound centre/radius (+80), the matrix and
the tail stay as they are. Record sizes do not change. Bind/def records are untouched.
"""
import argparse, io, json, struct, sys
from collections import Counter
from pathlib import Path

R = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(R /'tk12')); sys.path.insert(0, str(R / 'shared'))
from wlib import W, sha
from probe_worlds import probe_ssx3
from relayout_stream import assemble_archive
from inspect_disc import Region
import gamecube_game_dir as gdir
gdir.WORLD_EXTENSIONS = ("sdb", "ssb", "phm", "psm")

K = 0.7142857313156128  # [gp-0x2DF8], read from SLUS_207.72


def level_of(ext):
    v = max(ext) * K
    return min((struct.unpack('<I', struct.pack('<f', v))[0] >> 23 & 0xff) - 0x7f, 11)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('world'); ap.add_argument('out')
    ap.add_argument('--group', type=int, required=True, help='scenery group from the build receipt (the stock groups also hold kind-3)')
    ap.add_argument('--level', type=int, default=11)
    ap.add_argument('--receipt')
    a = ap.parse_args()
    E = 2.0 ** a.level / K * 1.0001

    base_bytes = Path(a.world).read_bytes()
    stem = W(a.world).stem
    bam_bytes = gdir.rename_world_members(base_bytes, 'bam')
    tmp = Path(a.out).with_suffix('.bam.tmp'); tmp.parent.mkdir(parents=True, exist_ok=True); tmp.write_bytes(bam_bytes)
    world = W(tmp)
    groups = world.sdb['groups']
    G = a.group
    recs = world.records(G)
    before, after = Counter(), Counter()
    changed = 0
    out = []
    for e, p in recs:
        if e['kind'] == 3:
            p = bytearray(p)
            lo = list(struct.unpack_from('<3f', p, 96)); hi = list(struct.unpack_from('<3f', p, 108))
            ext = [h - l for l, h in zip(lo, hi)]
            before[level_of(ext)] += 1
            k = ext.index(max(ext))
            if ext[k] < E:
                c = (lo[k] + hi[k]) / 2
                lo[k], hi[k] = c - E / 2, c + E / 2
                struct.pack_into('<3f3f', p, 96, *lo, *hi)
                changed += 1
            after[level_of([h - l for l, h in zip(*(struct.unpack_from('<3f', p, 96), struct.unpack_from('<3f', p, 108)))])] += 1
            p = bytes(p)
        out.append((e, p))
    raw = b''.join(bytes([e['kind']]) + len(p).to_bytes(3, 'little') + bytes([e['track']]) + e['rid'].to_bytes(3, 'little') + p
                   for e, p in out)
    raws = {g['index']: world.raw(g['index']) for g in groups}
    assert len(raw) == len(raws[G])
    raws[G] = raw
    report = probe_ssx3(Region(io.BytesIO(bam_bytes), 0, len(bam_bytes)))
    bam, layout, _ = assemble_archive(bam_bytes, report, raws, bytes(world.sdb_bytes), jobs=6, reuse_original_blocks=True)
    res = gdir.rename_world_members(bam, stem)
    Path(a.out).write_bytes(res); tmp.unlink()
    back = W(a.out)
    ok = all(back.raw(g['index']) == raws[g['index']] for g in groups)
    rc = dict(world=a.world, world_sha256=sha(base_bytes), out=a.out, out_sha256=sha(res), out_bytes=len(res), readback=ok,
              group=G, level=a.level, floor_extent=E, instances=sum(before.values()), changed=changed,
              levels_before=dict(sorted(before.items())), levels_after=dict(sorted(after.items())))
    if a.receipt:
        Path(a.receipt).write_text(json.dumps(rc, indent=1))
    print(json.dumps(rc, indent=1))


if __name__ == '__main__':
    main()
