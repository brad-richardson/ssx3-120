#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK42c: patch f7 on N kind-0 materials in a world (TK42b patch_material, generalized).

Usage: patch_blend.py WORLD.BIG OUT.BIG --spec SPEC.json [--receipt R.json]
SPEC.json: {"group": G, "mats": [{"rid": R, "f7": 3}, ...]}

Same-size records through assemble_archive (reuse_original_blocks: every
unchanged group keeps its compressed blocks verbatim). Works on materialized
composites via a synthesized probe report (SDB offsets + block walk), since
probe_ssx3 only handles linear layouts. Verifies decoded-group identity except
the patched records + lists affected world kind-3 instances.
"""
import argparse, hashlib, io, json, struct, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R = HERE.parent
sys.path.insert(0, str(R /'tk12')); sys.path.insert(0, str(R / 'shared'))
sys.path.insert(0, str(R /'tk13'))
from wlib import W, sha
from probe_worlds import resource_records
from relayout_stream import assemble_archive
from inspect_disc import Region
from retrack import members_of
from ps2mdr import parse_model
import gamecube_game_dir as gdir
gdir.WORLD_EXTENSIONS = ("sdb", "ssb", "phm", "psm")


def block_spans(ssb, off):
    """Walk CBXS/CEND blocks from a group's ssb_offset; return [(offset, size)]."""
    spans, pos = [], off
    while True:
        tag, size = struct.unpack_from('<4sI', ssb, pos)
        if tag not in (b'CBXS', b'CEND'):
            raise ValueError(f'bad block at {pos}')
        spans.append((pos, size))
        pos += size
        if tag == b'CEND':
            return spans


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('world'); ap.add_argument('out')
    ap.add_argument('--spec', required=True); ap.add_argument('--receipt')
    a = ap.parse_args()
    spec = json.loads(Path(a.spec).read_text())
    G = spec['group']

    base_bytes = Path(a.world).read_bytes()
    stem = W(a.world).stem
    bam_bytes = gdir.rename_world_members(base_bytes, 'bam')
    tmp = Path(a.out).with_suffix('.bam.tmp'); tmp.parent.mkdir(parents=True, exist_ok=True); tmp.write_bytes(bam_bytes)
    world = W(tmp)
    groups = world.sdb['groups']
    ssb = dict(members_of(bam_bytes))[f'data/worlds/bam.ssb']

    # patch group G decoded raw
    recs = world.records(G)
    for m in spec['mats']:
        hits = [(n, r) for n, r in enumerate(recs) if r[0]['kind'] == 0 and r[0]['rid'] == m['rid']]
        assert len(hits) == 1, f"want 1 kind-0 rid {m['rid']}, got {len(hits)}"
        n, (se, p) = hits[0]
        old = struct.unpack('<10h', p[:20])
        new = list(old); new[7] = m.get('f7', old[7])
        recs[n] = (dict(se, size=len(p)), struct.pack('<10h', *new) + p[20:])
    patched_raw = b''.join(bytes([e['kind']]) + len(p).to_bytes(3, 'little') + bytes([e['track']]) + e['rid'].to_bytes(3, 'little') + p
                          for e, p in recs)

    # synthesized probe report (composites aren't linear; walk blocks per group)
    raws, report_groups = {}, []
    for g in groups:
        gi = g['index']
        raw = world.raw(gi) if gi != G else patched_raw
        raws[gi] = raw
        spans = block_spans(ssb, g['ssb_offset'])
        report_groups.append(dict(index=gi, offset=g['ssb_offset'],
                                  sha256=hashlib.sha256(world.raw(gi)).hexdigest(),
                                  blocks=[dict(offset=o, size=s) for o, s in spans]))
    report = dict(groups=report_groups)
    bam, layout, _ = assemble_archive(bam_bytes, report, raws, bytes(world.sdb_bytes), jobs=6, reuse_original_blocks=True)
    res = gdir.rename_world_members(bam, stem)
    Path(a.out).write_bytes(res); tmp.unlink()

    # verify: decoded groups identical except patched records
    back = W(a.out)
    ok_groups, changed = [], []
    for g in groups:
        gi = g['index']
        same = back.raw(gi) == raws[gi]
        ok_groups.append(same)
        if gi == G:
            for m in spec['mats']:
                vals = [struct.unpack('<10h', p[:20]) for e, p in back.records(G)
                        if e['kind'] == 0 and e['rid'] == m['rid']]
                assert len(vals) == 1 and vals[0][7] == m.get('f7', vals[0][7]), (m['rid'], vals)
                old = struct.unpack('<10h', [p for e, p in world.records(G) if e['kind'] == 0 and e['rid'] == m['rid']][0][:20])
                changed.append(dict(rid=m['rid'], old=list(old), new=list(vals[0])))
    # decoded-records diff inside group G (must be exactly the patched kind-0s)
    n0 = [(e, p) for e, p in world.records(G) if e['kind'] == 0]
    n1 = [(e, p) for e, p in back.records(G) if e['kind'] == 0]
    recdiff = sum(1 for a0, a1 in zip(n0, n1) if bytes(a0[1]) != bytes(a1[1]))
    # affected world instances
    k2 = {e['rid']: p for e, p in recs if e['kind'] == 2}
    flagged = {m['rid'] for m in spec['mats']}
    aff_models = set()
    for rid, p in k2.items():
        try:
            mats = {mo >> 8 for mo in parse_model(p)['materials']}
        except Exception:
            continue
        if mats & flagged:
            aff_models.add(rid)
    aff = []
    for e, p in recs:
        if e['kind'] != 3 or len(p) < 132:
            continue
        if (struct.unpack_from('<I', p, 128)[0] >> 8) in aff_models:
            x, y, z = struct.unpack_from('<3f', p, 64)
            aff.append(dict(inst=e['rid'], model=struct.unpack_from('<I', p, 128)[0] >> 8,
                            pos=[round(x), round(y), round(z)]))
    rc = dict(world=a.world, world_sha256=sha(base_bytes), out=a.out, out_sha256=sha(res),
              out_bytes=len(res), readback=all(ok_groups), group=G, changed=changed,
              groups_identical=sum(ok_groups), groups_total=len(ok_groups),
              group_kind0_recdiff=recdiff, expect_recdiff=len(changed),
              patched_blocks=[l for l in layout if l['index'] == G],
              affected_models=sorted(aff_models), affected_instances=aff)
    if a.receipt:
        Path(a.receipt).write_text(json.dumps(rc, indent=1))
    print(json.dumps(rc, indent=1))


if __name__ == '__main__':
    main()
