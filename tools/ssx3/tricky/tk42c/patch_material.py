#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK42b: patch kind-0 material tail fields (f8,f9) in a world, same-size records.

    patch_material.py WORLD.BIG GROUP MAT_RID F8 F9 OUT.BIG [--receipt R.json] [--f7 N]

Rewrites the group's raw through assemble_archive (SDB counts/sizes unchanged:
same record count, same sizes). Prints SHA + receipt.
"""
import argparse, io, json, struct, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R = HERE.parent
sys.path.insert(0, str(R /'tk12')); sys.path.insert(0, str(R / 'shared'))
from wlib import W, sha
from probe_worlds import probe_ssx3
from relayout_stream import assemble_archive
from inspect_disc import Region
import gamecube_game_dir as gdir
gdir.WORLD_EXTENSIONS = ("sdb", "ssb", "phm", "psm")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('world'); ap.add_argument('group', type=int); ap.add_argument('mat_rid', type=int)
    ap.add_argument('f8', type=int); ap.add_argument('f9', type=int); ap.add_argument('out')
    ap.add_argument('--receipt')
    ap.add_argument('--f7', type=int, default=None)
    a = ap.parse_args()

    base_bytes = Path(a.world).read_bytes()
    stem = W(a.world).stem
    bam_bytes = gdir.rename_world_members(base_bytes, 'bam')
    tmp = Path(a.out).with_suffix('.bam.tmp'); tmp.parent.mkdir(parents=True, exist_ok=True); tmp.write_bytes(bam_bytes)
    world = W(tmp)
    recs = world.records(a.group)
    hits = [(n, r) for n, r in enumerate(recs) if r[0]['kind'] == 0 and r[0]['rid'] == a.mat_rid]
    assert len(hits) == 1, f'want 1 kind-0 rid {a.mat_rid}, got {len(hits)}'
    n, (se, p) = hits[0]
    old = struct.unpack('<10h', p[:20])
    new = list(old[:8]) + [a.f8, a.f9]
    if a.f7 is not None:
        new[7] = a.f7
    new = tuple(new)
    recs[n] = (dict(se, size=len(p)), struct.pack('<10h', *new) + p[20:])
    raw = b''.join(bytes([e['kind']]) + len(p).to_bytes(3, 'little') + bytes([e['track']]) + e['rid'].to_bytes(3, 'little') + p
                   for e, p in recs)
    groups = world.sdb['groups']
    raws = {g['index']: world.raw(g['index']) for g in groups}; raws[a.group] = raw
    report = probe_ssx3(Region(io.BytesIO(bam_bytes), 0, len(bam_bytes)))
    bam, layout, _ = assemble_archive(bam_bytes, report, raws, bytes(world.sdb_bytes), jobs=6, reuse_original_blocks=True)
    res = gdir.rename_world_members(bam, stem)
    Path(a.out).write_bytes(res); tmp.unlink()
    back = W(a.out)
    ok = all(back.raw(g['index']) == raws[g['index']] for g in groups)
    rc = dict(world=a.world, world_sha256=sha(base_bytes), out=a.out, out_sha256=sha(res),
              out_bytes=len(res), readback=ok, group=a.group, mat_rid=a.mat_rid,
              old_material=list(old), new_material=list(new))
    if a.receipt: Path(a.receipt).write_text(json.dumps(rc, indent=1))
    print(json.dumps(rc, indent=1))


if __name__ == '__main__':
    main()
