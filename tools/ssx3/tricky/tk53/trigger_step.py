#!/usr/bin/env python3
"""TK53 candidate 3: stock-style invisible break trigger for Tricky break-away covers.

    trigger_step.py ISO IN_COMPOSITE.BIG OUT_COMPOSITE.BIG --group G --rids R [R ...] --donor-pbd mesa.pbd
                    --scenery mesa-final-scenery.json --receipt R.json --work DIR

Per target cover (left exactly as imported: kind 0, drawn, non-colliding) the step appends:
  - a kind-12 collision resource = the importer's render hull of the cover's donor model (make_scenery33
    hull_or_box(model_points(convert_model)), the same shape TK33b gives mode-3 solids);
  - a kind-3 instance record = a byte copy of the cover's record with a new rid (same matrix, scale, AABB, model);
  - a definition in the stock invisible-trigger shape: kind 1, flags 0x00200000 (collide, no draw), the trigger's own
    class row, collision = the new resource, one part (0.0, 0.0, 2, -1): part flag 0x2 = contact event only, no 0x1
    physical response (MEMO §7), so it never blocks the rider.
The trigger's class row (a free row; the definition names it and its slot 2 names our program directly) runs
    DeadNode(self, 3); DeadNode(cover, 3)          (stock CRA3 row 120 order: trigger first, then the drawn object)
No existing record changes except the kind-16 course script (one stub program per cover, one class-row word per cover,
appended definitions/bindings) and the SDB counts. Game data stays outside git.
"""
import argparse, hashlib, json, struct, subprocess, sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path[:0] = [str(HERE), str(REPO / 'tk33b'), str(REPO / 'tk16'),
                str(REPO / 'tk13'), str(REPO / 'tk12'), str(REPO /'shared')]
from break_step import STUB, NONE, CONTACT_SLOT, block_spans, digest, materialize  # noqa: E402
from make_scenery33 import convert_model, model_points, hull_or_box  # noqa: E402
from ps2col import encode_collision, read_collision  # noqa: E402
from tricky_pbd import PBD  # noqa: E402
from wlib import W  # noqa: E402
from relayout_stream import assemble_archive  # noqa: E402
from retrack import members_of  # noqa: E402
import gamecube_game_dir as gdir  # noqa: E402
gdir.WORLD_EXTENSIONS = ("sdb", "ssb", "phm", "psm")

TRIGGER_DEF_HEAD = (1, 0x00200000)       # kind 1 (collision resource), collide flag only: invisible helper
TRIGGER_PART = (0.0, 0.0, 2, -1)         # stock invisible-trigger part: event bit 0x2, no physical bit 0x1


def trigger_program(cover_oid):
    code = bytes([40, 1, 3, 0]) + bytes([33, 2, 2, 1])                                   # DeadNode(self, 3)
    code += bytes([40, 1, 3, 0]) + bytes([39, 0, 0, 0]) + struct.pack('<I', cover_oid)  # key1=3, key0=cover
    code += bytes([33, 2, 2, 2]) + bytes([42, 0xff, 0, 0])                               # DeadNode(cover, 3); RET
    code_end = 16 + len(code)
    data_end = code_end + 16
    return (struct.pack('<4I', 0x4e554c, code_end, data_end, data_end) + code
            + struct.pack('<iIII', -code_end, 0, 3, 2))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('iso'); ap.add_argument('input'); ap.add_argument('output')
    ap.add_argument('--group', type=int, required=True)
    ap.add_argument('--rids', type=int, nargs='+', required=True)
    ap.add_argument('--donor-pbd', required=True)
    ap.add_argument('--scenery', required=True)
    ap.add_argument('--receipt', required=True)
    ap.add_argument('--work', required=True)
    a = ap.parse_args()
    work = Path(a.work); work.mkdir(parents=True, exist_ok=True)
    out = Path(a.output); out.parent.mkdir(parents=True, exist_ok=True)
    assert not out.exists(), 'use a fresh output path'
    before = [digest(a.input) for _ in range(2)]
    assert before[0] == before[1]
    pbd_bytes = Path(a.donor_pbd).read_bytes()
    pbd = PBD(pbd_bytes)
    source = json.loads(Path(a.scenery).read_text())['instance_source']

    full = materialize(a.iso, a.input)
    (work / 'input-full.BIG').write_bytes(full)
    stem = W(work / 'input-full.BIG').stem
    bam_bytes = gdir.rename_world_members(full, 'bam')
    tmp = work / 'input-full.bam.BIG'
    tmp.write_bytes(bam_bytes)
    world = W(tmp)
    G = a.group
    recs = world.records(G)
    ki = next(n for n, (e, p) in enumerate(recs) if e['kind'] == 16)
    se, script = recs[ki]
    T = se['track']
    oid = lambda r: (r << 8) | T
    data = bytearray(script)
    lc, la, cc, ca = struct.unpack_from('<4I', data, 16)
    pc, ptab, pend = struct.unpack_from('<3I', data, 56)
    st = struct.unpack_from(f'<{pc}I', data, ptab) + (pend,)
    base = struct.unpack_from('<I', data, 64)[0]
    nb, bat, nd, dat, nspl, spl_at = struct.unpack_from('<6I', data, 68)
    bindings = list(struct.unpack_from(f'<{nb}H', data, bat))
    doffs = struct.unpack_from(f'<{nd}I', data, dat)
    assert all(y - x == 28 for x, y in zip(doffs, doffs[1:])) and bat - base - doffs[-1] == 28
    defs = [bytes(data[base + o:base + o + 28]) for o in doffs]
    tail = bytes(data[spl_at:])
    loads = {h >> 8 for h in struct.unpack_from(f'<{lc}I', data, la)}
    rows = [struct.unpack_from('<6I', data, ca + 24 * r) for r in range(cc)]
    referenced = {h >> 8 for row in rows for h in row if h != NONE}
    # Programs are also named by two hashed event tables in the header (read on the Mesa host CRA3, receipts
    # header-event-tables.txt): +0x20/+0x24 = count/offset of 0x54-byte entries (hash, count, sub-offset, handles at
    # +0x0C..+0x4C; 0x308DB8 runs the +0x4C handle at race start) and +0x30/+0x34 = count/offset of 56-byte entries
    # (hash, 13 handles). Candidates 1/2 overwrote p168, which the first table names. Every handle there is excluded.
    n20, o20, _, _, n30, o30 = struct.unpack_from('<6I', data, 32)
    s20 = (ptab - o20) // n20 if n20 else 0
    s30 = (o20 - o30) // n30 if n30 else 0
    event_handles = [struct.unpack_from('<I', data, o20 + s20 * k + f)[0] for k in range(n20) for f in range(12, 80, 4)]
    event_handles += [struct.unpack_from('<I', data, o30 + s30 * k + f)[0] for k in range(n30) for f in range(4, s30, 4)]
    referenced |= {h >> 8 for h in event_handles if h != NONE and h & 0xff == T}
    rows_used = {struct.unpack_from('<I', d, 8)[0] >> 8 for d in defs if struct.unpack_from('<I', d, 8)[0] != NONE}
    # Every stub on the Mesa host is named by a class row or an event table. A stub named only by class rows that
    # no definition uses never runs, so it is safe to reuse; event-table and load-time programs are never touched.
    live_rows = {r for r in rows_used if r < cc}
    live_programs = {h >> 8 for r in live_rows for h in rows[r] if h != NONE}
    event_programs = referenced - {h >> 8 for row in rows for h in row if h != NONE}
    event_programs |= {h >> 8 for h in event_handles if h != NONE and h & 0xff == T}
    free_progs = [k for k in range(2, pc) if bytes(data[st[k]:st[k] + 36]) == STUB and k not in loads
                  and k not in event_programs and k not in live_programs]
    free_rows = [r for r in range(2, cc) if rows[r] == (NONE,) * 6 and r not in rows_used]

    inst = {e['rid']: p for e, p in recs if e['kind'] == 3}
    next_rid = {k: max((e['rid'] for e, p in recs if e['kind'] == k), default=-1) + 1 for k in (3, 12)}
    added, triggers = [], []
    hulls = {}
    for rid in sorted(a.rids):
        cover_def = defs[bindings[rid]]
        kind, flags, cls, coll = struct.unpack_from('<4I', cover_def, 0)
        assert kind == 0 and coll == NONE, f'cover {rid} is not a non-colliding import: {cover_def.hex()}'
        donor = source[rid]
        model = pbd.instances[donor]['model']
        if model not in hulls:
            cm = convert_model(pbd, pbd.models[model], None)
            mesh, how = hull_or_box(model_points(cm))
            coll_bytes = encode_collision(mesh)
            read_collision(coll_bytes)
            assert struct.unpack_from('<H', coll_bytes, 2)[0] == 1, 'one collision part'
            hulls[model] = (coll_bytes, how, len(mesh[0]['vertices']), len(mesh[0]['triangles']))
        coll_bytes, how, nv, nt = hulls[model]
        crid = next_rid[12]; next_rid[12] += 1
        trid = next_rid[3]; next_rid[3] += 1
        prog = trigger_program(oid(rid))
        slot = next(k for k in free_progs if st[k + 1] - st[k] >= len(prog))
        free_progs.remove(slot)
        dead_rows_naming = [r for r in range(cc) if any(h != NONE and h >> 8 == slot for h in rows[r])]
        data[st[slot]:st[slot + 1]] = prog + bytes(st[slot + 1] - st[slot] - len(prog))
        row = free_rows.pop(0)
        struct.pack_into('<I', data, ca + 24 * row + 4 * CONTACT_SLOT, oid(slot))
        tdef = struct.pack('<4I2f2h', *TRIGGER_DEF_HEAD, oid(row), oid(crid), *TRIGGER_PART)
        defs.append(tdef)
        while len(bindings) <= trid:
            bindings.append(0)
        bindings[trid] = len(defs) - 1
        ip = bytearray(inst[rid])
        assert struct.unpack_from('<I', ip, 120)[0] == oid(rid), 'instance record handle at +120'
        struct.pack_into('<I', ip, 120, oid(trid))
        added += [(dict(kind=12, track=T, rid=crid), coll_bytes), (dict(kind=3, track=T, rid=trid), bytes(ip))]
        triggers.append(dict(cover_rid=rid, donor=donor, donor_model=model, trigger_rid=trid, collision_rid=crid,
                             collision=how, hull_vertices=nv, hull_triangles=nt,
                             collision_sha256=hashlib.sha256(coll_bytes).hexdigest(),
                             class_row=row, class_handle=hex(oid(row)), program_slot=slot,
                             program_also_named_by_unused_rows=dead_rows_naming, program=prog.hex(),
                             trigger_definition=tdef.hex(), cover_definition_unchanged=cover_def.hex(),
                             position=struct.unpack_from('<3f', ip, 64), aabb=struct.unpack_from('<6f', ip, 96)))

    def layout(head, ds, bs):
        raw = bytearray(head[:base]); offs = []
        for d in ds:
            offs.append(len(raw) - base); raw += d
        b_at = len(raw); raw += struct.pack(f'<{len(bs)}H', *bs); raw += bytes(-len(raw) % 4)
        d_at = len(raw); raw += struct.pack(f'<{len(offs)}I', *offs)
        s_at = len(raw); raw += tail
        struct.pack_into('<6I', raw, 68, len(bs), b_at, len(ds), d_at, nspl, s_at)
        return bytes(raw)

    assert layout(script, defs[:nd], list(struct.unpack_from(f'<{nb}H', script, bat))) == bytes(script)
    patched = layout(data, defs, bindings)
    assert patched[:68] == bytes(script[:68])
    allowed = [(st[t['program_slot']], st[t['program_slot'] + 1]) for t in triggers] + \
              [(ca + 24 * t['class_row'] + 8, ca + 24 * t['class_row'] + 12) for t in triggers]
    assert all(any(lo <= i < hi for lo, hi in allowed) for i in range(92, base) if patched[i] != script[i])

    new_recs = [(dict(e, size=len(patched)), patched) if e['kind'] == 16 else (e, p) for e, p in recs]
    for e, p in added:                                   # after the last existing record of the same kind; order kept
        at = max(n for n, (x, _) in enumerate(new_recs) if x['kind'] == e['kind']) + 1
        new_recs.insert(at, (e, p))
    raw = b''.join(bytes([e['kind']]) + len(p).to_bytes(3, 'little') + bytes([e['track']])
                   + e['rid'].to_bytes(3, 'little') + p for e, p in new_recs)
    groups = world.sdb['groups']
    raws = {g['index']: world.raw(g['index']) for g in groups}
    raws[G] = raw
    sdb = bytearray(world.sdb_bytes)
    lcount, nodes, _ = struct.unpack_from('<III', sdb, 8)
    go = (80 + 88 * lcount + 15) // 16 * 16 + nodes * 96 + 68 * G
    struct.pack_into('<H', sdb, go, len(new_recs))
    struct.pack_into('<I', sdb, go + 8, sum(len(p) + 8 for e, p in new_recs if e['kind'] <= 12))
    counts = Counter(e['kind'] for e, p in new_recs)
    for k in range(14):
        struct.pack_into('<H', sdb, go + 12 + 2 * k, counts.get(k, 0))
    next_id_table = {}
    for k in (3, 12):                                    # per-location next resource id (TK51)
        at = 80 + 88 * T + 32 + 2 * k
        old = struct.unpack_from('<h', sdb, at)[0]
        struct.pack_into('<h', sdb, at, max(old, next_rid[k]))
        next_id_table[k] = [old, max(old, next_rid[k])]
    ssb = dict(members_of(bam_bytes))['data/worlds/bam.ssb']
    report = dict(groups=[dict(index=g['index'], offset=g['ssb_offset'],
                               sha256=hashlib.sha256(world.raw(g['index'])).hexdigest(),
                               blocks=[dict(offset=o, size=n) for o, n in block_spans(ssb, g['ssb_offset'])])
                          for g in groups])
    bam, _, _ = assemble_archive(bam_bytes, report, raws, bytes(sdb), jobs=6, reuse_original_blocks=True)
    patched_full = work / 'patched-full.BIG'
    patched_full.write_bytes(gdir.rename_world_members(bam, stem))
    back = W(patched_full)
    assert all(back.raw(g['index']) == raws[g['index']] for g in groups), 'group readback'
    old_map = {(e['kind'], e['rid']): p for e, p in recs}
    new_map = {(e['kind'], e['rid']): p for e, p in back.records(G)}
    changed = sorted(k for k in old_map if new_map[k] != old_map[k])
    assert changed == [(16, se['rid'])], changed
    proc = subprocess.run([sys.executable, str(REPO / 'tk10/make_composite.py'), a.iso,
                           str(patched_full), str(out)], capture_output=True, text=True)
    Path(a.receipt).with_suffix('.builder.log').write_text(proc.stdout + proc.stderr)
    if proc.returncode:
        raise SystemExit(proc.returncode)
    after = [digest(out) for _ in range(2)]
    assert after[0] == after[1]
    rc = dict(operation='TK53 candidate 3: invisible stock-style break trigger per cover',
              input=str(Path(a.input).resolve()), input_sha256=before, output=str(out.resolve()),
              output_sha256=after, patched_full_sha256=digest(patched_full), donor_pbd_sha256=hashlib.sha256(pbd_bytes).hexdigest(),
              group=G, track=T, triggers=triggers, appended_records=[[e['kind'], e['rid']] for e, p in added],
              changed_existing_records=[list(k) for k in changed], next_id_table=next_id_table,
              event_table_programs=sorted(h >> 8 for h in event_handles if h != NONE and h & 0xff == T),
              definitions=[nd, len(defs)], bindings=[nb, len(bindings)], groups_readback=True,
              composite_builder=json.loads(proc.stdout))
    Path(a.receipt).write_text(json.dumps(rc, indent=2) + '\n')
    print(json.dumps({k: v for k, v in rc.items() if k not in ('composite_builder',)}, indent=1)[:4000])


if __name__ == '__main__':
    main()
