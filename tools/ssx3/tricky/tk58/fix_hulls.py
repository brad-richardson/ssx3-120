#!/usr/bin/env python3
"""TK58 post-composite step: replace TK33b scenery hulls that are not convex hulls.

Guest read (TK58 REPORT): 0x334888 collects a mode-1 instance part's triangle hits from 0x32c0f8
into a 64-record (0x80 bytes each) stack array with no capacity check. TK33b's incremental
make_scenery33.convex_hull loses convexity on near-coplanar input (inexact visibility tests), and the
damage snowballs: e.g. Mesa resource 226 has 5664 overlapping faces on 237 vertices (a convex hull has
at most 2V-4 = 470). One query there returned 99 hits, overran the array and zeroed the query's
dispatch pointer (JALR 0x32c434 -> 0).

Rule (one mechanism): in the course group, every single-part kind12 resource whose face count exceeds
the convex bound 2V-4 and that is an importer hull is rebuilt from the same input points with
hull.convex_hull_fixed (exact orientation tests). "Importer hull" = make_scenery's own hull_or_box of
one donor input (render model points or a multipart SSF mesh) re-encodes to the payload byte for byte
(searched over every donor input; the scenery receipts' collision indices are not rids on every
lineage). Over-bound resources that no donor input reproduces are authored meshes and stay unchanged.
Every other record and group stays byte-identical.
Usage: fix_hulls.py INPUT.BIG OUTPUT.BIG --course mesa|gari|snow|aloha|merq|elys [--receipt R.json]
"""
import argparse, json, struct, sys, subprocess
from pathlib import Path
from collections import Counter
sys.dont_write_bytecode = True
R = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(R / 'tk58'), str(R / 'tk51'), str(R / 'tk16')]
from patch_source_bridge import materialize, sha, rewind, W, read_collision, encode_collision, block_spans, members_of, assemble_archive, gdir, ISO, PBD, ssf_to_gsf, read_tricky_collision
import make_scenery33 as ms
from hull import convex_hull_fixed, verify

CONFIG = {  # course: (group, scenery receipt (unused since provenance search), donor stem)
    'mesa': (80, 'mesa-final', 'mesa'), 'gari': (33, 'gari-f', 'gari'), 'snow': (56, 'snow-f', 'snow'),
    'aloha': (22, 'aloha-b', 'aloha'), 'merq': (148, 'merq-d', 'merquer'), 'elys': (135, 'elys-b', 'elysium')}
SCENERY = Path('/Users/brad/dev/ssx3-work/pinned/tricky/scenery-effects/scenery')
DONOR = Path('/Users/brad/dev/ssx3-work/pinned/tricky/donor')


def main():
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument('input'); ap.add_argument('output')
    ap.add_argument('--course', required=True, choices=sorted(CONFIG)); ap.add_argument('--receipt')
    a = ap.parse_args(); src = Path(a.input); out = Path(a.output); assert not out.exists(); out.parent.mkdir(parents=True, exist_ok=True)
    inputs = [sha(src.read_bytes()) for _ in range(2)]; assert inputs[0] == inputs[1]
    G, scen, stem = CONFIG[a.course]
    D = DONOR / stem; pbd = PBD((D / (stem + '.pbd')).read_bytes()); colls = read_tricky_collision(ssf_to_gsf((D / (stem + '.ssf')).read_bytes()))
    raw = materialize(src.read_bytes()); bam = gdir.rename_world_members(raw, 'bam'); tmp = out.with_suffix('.bam.tmp'); tmp.write_bytes(bam); world = W(tmp)
    recs = world.records(G); fixed = []; skipped = []; cands = []
    for j, (e, p) in enumerate(recs):
        if e['kind'] != 12: continue
        try: parts = read_collision(p)
        except ValueError: continue
        if len(parts) != 1: continue
        nV, nF = len(parts[0]['vertices']), len(parts[0]['triangles'])
        if nV and nF > 2 * nV - 4: cands.append((j, nV, nF))
    # Provenance: the importer's own hull_or_box of one donor input (render model points or a multipart
    # SSF mesh's vertices) must re-encode to the payload byte for byte. Search every donor input.
    sources = {}
    if cands:
        want = {recs[j][1]: j for j, _, _ in cands}
        donor_inputs = []
        for mi in range(len(pbd.models)):
            try: donor_inputs.append((('model', mi), ms.model_points(ms.convert_model(pbd, pbd.models[mi], None))))
            except (ValueError, AssertionError, KeyError, IndexError): pass
        for ci in range(len(colls)):
            donor_inputs.append((('mesh', ci), [v[:3] for mm in rewind(colls[ci]) for v in mm['vertices']]))
        for key, pts in donor_inputs:
            if len(pts) < 4: continue
            h, how = ms.hull_or_box(pts)
            if how != 'hull': continue
            j = want.get(encode_collision(h))
            if j is not None: sources.setdefault(j, []).append((key, pts))
    for j, nV, nF in cands:
        e, p = recs[j]
        if j not in sources: skipped.append(dict(rid=e['rid'], faces=nF, verts=nV, reason='not an importer hull (no donor input reproduces it)')); continue
        (key, pts) = sources[j][0]
        h = convex_hull_fixed(pts)
        if h is None: skipped.append(dict(rid=e['rid'], reason='corrected hull did not verify')); continue
        v = verify(h, [q[:3] for q in pts]); assert v['ok'] and v['faces'] <= 2 * v['verts'] - 4
        data = encode_collision([h]); back, = read_collision(data); assert struct.unpack_from('<H', data, 2)[0] == 1 and len(back['triangles']) == v['faces']
        recs[j] = (dict(e, size=len(data)), data)
        fixed.append(dict(rid=e['rid'], source=list(key), reproducing_sources=[list(k) for k, _ in sources[j]], old_faces=nF, old_verts=nV, new_faces=v['faces'], new_verts=v['verts'],
                          new_worst_outside=v['worst_outside'], old_payload_sha=sha(p), new_payload_sha=sha(data), importer_hull_reproduced=True))
    if not fixed:
        out.write_bytes(src.read_bytes()); outputs = [sha(out.read_bytes()) for _ in range(2)]; assert outputs == inputs; tmp.unlink()
        receipt = dict(course=a.course, group=G, input_sha_reads=inputs, output_sha_reads=outputs, fixed=[], skipped=skipped, no_op=True)
    else:
        patched = b''.join(bytes([e['kind']]) + len(p).to_bytes(3, 'little') + bytes([e['track']]) + e['rid'].to_bytes(3, 'little') + p for e, p in recs)
        raws = {g['index']: world.raw(g['index']) for g in world.sdb['groups']}; raws[G] = patched
        ssb = dict(members_of(bam))['data/worlds/bam.ssb']
        report = dict(groups=[dict(index=g['index'], offset=g['ssb_offset'], sha256=sha(world.raw(g['index'])), blocks=[dict(offset=o, size=n) for o, n in block_spans(ssb, g['ssb_offset'])]) for g in world.sdb['groups']])
        sdb = bytearray(world.sdb_bytes); lc, nodes, _ = struct.unpack_from('<III', sdb, 8); gtab = (80 + 88 * lc + 15) // 16 * 16 + nodes * 96; go = gtab + 68 * G
        assert struct.unpack_from('<H', sdb, go)[0] == len(recs)
        struct.pack_into('<I', sdb, go + 8, sum(len(p) + 8 for e, p in recs if e['kind'] <= 12))
        counts = Counter(e['kind'] for e, p in recs); assert all(struct.unpack_from('<H', sdb, go + 12 + 2 * k)[0] == counts[k] for k in range(14))
        result, _, _ = assemble_archive(bam, report, raws, bytes(sdb), jobs=2, reuse_original_blocks=True)
        full = out.with_suffix('.materialized.BIG'); full.write_bytes(gdir.rename_world_members(result, a.course))
        back = W(full); assert all(back.raw(g['index']) == raws[g['index']] for g in world.sdb['groups'])
        old = {(e['kind'], e['rid']): p for e, p in world.records(G)}; new = {(e['kind'], e['rid']): p for e, p in back.records(G)}
        changed = sorted(k for k in old if old[k] != new[k]); assert changed == sorted((12, f['rid']) for f in fixed) and old.keys() == new.keys()
        unchanged_groups = [g['index'] for g in world.sdb['groups'] if g['index'] != G and world.raw(g['index']) == back.raw(g['index'])]
        assert len(unchanged_groups) == len(world.sdb['groups']) - 1
        with out.with_suffix('.composite.json').open('w') as f:
            subprocess.run([sys.executable, str(R / 'local/research/TK10/make_composite.py'), str(ISO), str(full), str(out)], stdout=f, check=True)
        outputs = [sha(out.read_bytes()) for _ in range(2)]; assert outputs[0] == outputs[1]; tmp.unlink(); full.unlink()
        receipt = dict(course=a.course, group=G, input=str(src), input_sha_reads=inputs, output=str(out), output_sha_reads=outputs, fixed=fixed, skipped=skipped,
                       changed_records=[list(k) for k in changed], other_groups_byte_identical=True, all_groups_readback=True)
    Path(a.receipt or out.with_suffix('.hulls.json')).write_text(json.dumps(receipt, indent=1) + '\n')
    print(json.dumps(dict(course=a.course, fixed=[(f['rid'], f['old_faces'], f['new_faces']) for f in fixed], skipped=skipped, output_sha=receipt['output_sha_reads'][0])))


if __name__ == '__main__':
    main()
