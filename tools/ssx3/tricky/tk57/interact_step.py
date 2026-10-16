#!/usr/bin/env python3
"""TK57 data step: Tricky bouncing blocks and breakable glass on SSX 3's own behaviour templates.

    interact_step.py ISO IN_COMPOSITE.BIG OUT_COMPOSITE.BIG --group G --scenery S.json --donor-pbd D.pbd
                     --stock-bam BAM.BIG [--blocks RID ...] [--glass PANE:SCAN[,SCAN] ...] --receipt R.json --work DIR

Input: a TKP4-lineage PS2XCMP1 composite. Output: a composite on the same base. Templates (template_survey.py):

Blocks (Tricky mode-3 CrashBag, on-hit `Roller?`) = stock ARA1 row 113, the host's own knockable block:
  - definition (3, 0x40210000, row, col, 1e30, 0.0, 3, -1): kind 3 (movable body), drawn + collide, part flags 3
    (physical response + contact event);
  - col = a byte copy of the stock ARA1 kind-12 rid 241 rigid-body shape (type 3) that the stock blocks use;
  - a free class row whose slot 2 (rider contact) names a byte copy of stock ARA1 p49 `CALL Object/0; CALL 15/0; RET`
    (builtin 15 at 0x2FD250, the Roller node). One row, one program, one definition shared by every block (stock shares
    them the same way).
Glass (Tricky LCD pane, on-hit `BreakLogo*` = DeadNode(pane) + DeadNode(scan) + cMeshAnim(broken twin)) = stock BRA2
row 190 (p183), a framed glass pane (texture 152): DeadNode(self, 3) + MeshAnim(shard twin, 11 args):
  - the pane's definition is cloned as (1, 0x210000, row, col, 0.0, 0.0, 2, -1): kind 1 with a collision resource,
    drawn, part flag 2 only (contact event, no physical response: the rider passes through; TK53 MEMO §7);
    col = the importer's render hull of the pane's donor model (make_scenery33, as TK53's trigger step);
  - its own free class row's slot 2 names a new program: p183's opening DeadNode(self, 3) (bytes verbatim), then
    DeadNode(scan, 3) for the donor's scan overlay, RET. The broken twin is not imported (TKP4 skipped hidden
    instances), so p183's MeshAnim(twin) has no target and is left out (follow-up: import the twin).
New programs are APPENDED to the program table (index pc, pc+1, ...): every stub on the Tricky courses is named by a
class row or by the event tables between the class and program tables, which the engine broadcasts (an overwritten
stub ran at race start in TK57 d1/d2). Rows are all -1 and named by no definition. Each definition names its row directly, so there is no row/program offset (TK53 §5, TK56).
Only the kind-16 course script changes, plus appended kind-12 records and the SDB counts. Game data stays outside git.
"""
import argparse, hashlib, json, struct, subprocess, sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path[:0] = [str(REPO / 'tk53'), str(REPO / 'tk33b'),
                str(REPO / 'tk16'), str(REPO / 'tk13'), str(REPO / 'tk12'),
                str(REPO / 'tk21'), str(REPO /'shared')]
from break_step import STUB, NONE, CONTACT_SLOT, block_spans, digest, materialize  # noqa: E402
from make_scenery33 import convert_model, model_points, hull_or_box  # noqa: E402
from ps2col import encode_collision, read_collision  # noqa: E402
from tricky_pbd import PBD  # noqa: E402
from wlib import W  # noqa: E402
from luno_survey import disasm, IMM  # noqa: E402
from relayout_stream import assemble_archive  # noqa: E402
from retrack import members_of  # noqa: E402
import gamecube_game_dir as gdir  # noqa: E402
gdir.WORLD_EXTENSIONS = ("sdb", "ssb", "phm", "psm")

BLOCK_TEMPLATE = dict(location='ARA1', group=33, row=113, program=49, collision_rid=241)
GLASS_TEMPLATE = dict(location='BRA2', group=56, row=190, program=183)
BLOCK_DEF_HEAD, BLOCK_PART = (3, 0x40210000), (1.0000000150474662e+30, 0.0, 3, -1)
GLASS_DEF_HEAD, GLASS_PART = (1, 0x00210000), (0.0, 0.0, 2, -1)


def stock_script(w, group):
    recs = w.records(group)
    return next(p for e, p in recs if e['kind'] == 16), recs


def program_span(p, k):
    pc, pt, pe = struct.unpack_from('<3I', p, 56)
    st = struct.unpack_from(f'<{pc}I', p, pt) + (pe,)
    return bytes(p[st[k]:st[k + 1]])


def lun_length(blob):
    magic, code_end, data_end, length = struct.unpack_from('<4I', blob)
    assert magic == 0x4e554c
    return blob[:length]


def glass_program(opening, scan_oids):
    code = opening                                                 # stock p183: DeadNode(self, 3), bytes verbatim
    for o in scan_oids:                                            # the donor break also kills the scan overlay
        code += bytes([40, 1, 3, 0]) + bytes([39, 0, 0, 0]) + struct.pack('<I', o) + bytes([33, 2, 2, 2])
    code += bytes([42, 0xff, 0, 0])
    code_end = 16 + len(code)
    data_end = code_end + 16
    blob = (struct.pack('<4I', 0x4e554c, code_end, data_end, data_end) + code
            + struct.pack('<iIII', -code_end, 0, 3, 2))
    disasm(blob)
    return blob


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('iso'); ap.add_argument('input'); ap.add_argument('output')
    ap.add_argument('--group', type=int, required=True)
    ap.add_argument('--scenery', required=True)
    ap.add_argument('--donor-pbd', required=True)
    ap.add_argument('--stock-bam', required=True)
    ap.add_argument('--blocks', type=int, nargs='*', default=[])
    ap.add_argument('--glass', nargs='*', default=[], help='PANE:SCAN[,SCAN] world rids')
    ap.add_argument('--receipt', required=True)
    ap.add_argument('--work', required=True)
    a = ap.parse_args()
    work = Path(a.work); work.mkdir(parents=True, exist_ok=True)
    out = Path(a.output); out.parent.mkdir(parents=True, exist_ok=True)
    assert not out.exists(), 'use a fresh output path'
    before = [digest(a.input) for _ in range(2)]
    assert before[0] == before[1]
    pbd_bytes = Path(a.donor_pbd).read_bytes(); pbd = PBD(pbd_bytes)
    source = json.loads(Path(a.scenery).read_text())['instance_source']

    stock = W(a.stock_bam)
    assert stock.sdb['locations'][[l['name'] for l in stock.sdb['locations']].index('ARA1')]
    bp, brecs = stock_script(stock, BLOCK_TEMPLATE['group'])
    assert struct.unpack_from('<6I', bp, struct.unpack_from('<I', bp, 28)[0] + 24 * BLOCK_TEMPLATE['row'])[2] >> 8 == BLOCK_TEMPLATE['program']
    block_prog = lun_length(program_span(bp, BLOCK_TEMPLATE['program']))
    assert block_prog.hex() == '4c554e001c0000002c0000002c0000002102000021020f002aff0000e4ffffff000000000300000000000000'
    block_coll = next(p for e, p in brecs if e['kind'] == 12 and e['rid'] == BLOCK_TEMPLATE['collision_rid'])
    assert block_coll[:2] == b'\x03\x00'
    gp, _ = stock_script(stock, GLASS_TEMPLATE['group'])
    assert struct.unpack_from('<6I', gp, struct.unpack_from('<I', gp, 28)[0] + 24 * GLASS_TEMPLATE['row'])[2] >> 8 == GLASS_TEMPLATE['program']
    p183 = lun_length(program_span(gp, GLASS_TEMPLATE['program']))
    opening = p183[16:24]
    assert opening == bytes([40, 1, 3, 0, 33, 2, 2, 1]), opening.hex()

    full = materialize(a.iso, a.input)
    (work / 'input-full.BIG').write_bytes(full)
    stem = W(work / 'input-full.BIG').stem
    bam_bytes = gdir.rename_world_members(full, 'bam')
    tmp = work / 'input-full.bam.BIG'; tmp.write_bytes(bam_bytes)
    world = W(tmp)
    G = a.group
    recs = world.records(G)
    se, script = next((e, p) for e, p in recs if e['kind'] == 16)
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
    rows_used = {struct.unpack_from('<I', d, 8)[0] >> 8 for d in defs if struct.unpack_from('<I', d, 8)[0] != NONE}
    # Header tables besides the class table also name programs (+32/+36: 10 entries, +48/+52: 29 entries on Garibaldi;
    # the engine broadcasts them, e.g. at race start). Any aligned word in the header/table region that reads as a
    # program handle (k << 8 | track) makes program k taken. Instance oids share the encoding: over-exclusion is safe.
    ev_lo, ev_hi = ca + 24 * cc, ptab                     # the event tables sit between the class and program tables
    assert ev_lo <= ev_hi and ev_lo % 4 == 0
    table_words = {x for x in struct.unpack_from(f'<{(ev_hi - ev_lo) // 4}I', data, ev_lo) if x & 0xff == T and x != NONE}
    in_tables = {x >> 8 for x in table_words}
    free_stubs = [k for k in range(2, pc) if bytes(data[st[k]:st[k] + 36]) == STUB and k not in loads
                  and k not in referenced and k not in in_tables]          # reported only; new programs are appended
    free_rows = sorted((r for r in range(2, cc) if rows[r] == (NONE,) * 6 and r not in rows_used and r not in in_tables),
                       key=lambda r: -r)

    next_rid = {k: max((e['rid'] for e, p in recs if e['kind'] == k), default=-1) + 1 for k in (12,)}
    added, written, receipt_items = [], [], []

    new_progs = []

    def put_program(blob):
        # Append a new program (index pc + n). Every existing stub on the Tricky courses is named by a class row or by
        # the event tables, so overwriting one lets an engine broadcast run it (TK57 d1/d2: race-start slot-3 sweep).
        new_progs.append(blob)
        return pc + len(new_progs) - 1

    def put_row(slot):
        row = free_rows.pop(0)
        struct.pack_into('<I', data, ca + 24 * row + 4 * CONTACT_SLOT, oid(slot))
        written.append((ca + 24 * row + 8, ca + 24 * row + 12))
        return row

    def add_collision(blob):
        crid = next_rid[12]; next_rid[12] += 1
        added.append((dict(kind=12, track=T, rid=crid), blob))
        return crid

    if a.blocks:
        slot = put_program(block_prog); row = put_row(slot); crid = add_collision(block_coll)
        bdef = struct.pack('<4I2f2h', *BLOCK_DEF_HEAD, oid(row), oid(crid), *BLOCK_PART)
        defs.append(bdef)
        old_defs = {}
        for rid in a.blocks:
            old = defs[bindings[rid]]
            kind, flags, cls, coll = struct.unpack_from('<4I', old)
            assert kind == 0 and cls >> 8 in (1,) and coll == NONE, f'block {rid}: {old.hex()}'
            old_defs[rid] = old.hex()
            bindings[rid] = len(defs) - 1
        receipt_items.append(dict(feature='blocks', template=BLOCK_TEMPLATE, rids=a.blocks,
                                  donors=[source[r] for r in a.blocks],
                                  donor_models=sorted({pbd.instances[source[r]]['model'] for r in a.blocks}),
                                  definitions_before=old_defs,
                                  program_slot=slot, program=block_prog.hex(), class_row=row, class_handle=hex(oid(row)),
                                  collision_rid=crid, collision_sha256=hashlib.sha256(block_coll).hexdigest(),
                                  definition=bdef.hex(), definition_index=len(defs) - 1))
    hulls = {}
    for spec in a.glass:
        pane_s, scans_s = spec.split(':')
        pane = int(pane_s); scans = [int(x) for x in scans_s.split(',') if x]
        old = defs[bindings[pane]]
        kind, flags, cls, coll = struct.unpack_from('<4I', old)
        assert kind == 0 and coll == NONE, f'pane {pane}: {old.hex()}'
        model = pbd.instances[source[pane]]['model']
        if model not in hulls:
            cm = convert_model(pbd, pbd.models[model], None)
            mesh, how = hull_or_box(model_points(cm))
            cb = encode_collision(mesh); read_collision(cb)
            hulls[model] = (add_collision(cb), cb, how, len(mesh[0]['vertices']), len(mesh[0]['triangles']))
        crid, cb, how, nv, nt = hulls[model]
        prog = glass_program(opening, [oid(s) for s in scans])
        slot = put_program(prog); row = put_row(slot)
        gdef = struct.pack('<4I2f2h', *GLASS_DEF_HEAD, oid(row), oid(crid), *GLASS_PART)
        defs.append(gdef); bindings[pane] = len(defs) - 1
        receipt_items.append(dict(feature='glass', template=GLASS_TEMPLATE, pane=pane, pane_donor=source[pane],
                                  scans=scans, scan_donors=[source[s] for s in scans], donor_model=model,
                                  pane_definition_before=old.hex(), definition=gdef.hex(), definition_index=len(defs) - 1,
                                  collision_rid=crid, collision=how, hull_vertices=nv, hull_triangles=nt,
                                  collision_sha256=hashlib.sha256(cb).hexdigest(), program_slot=slot, program=prog.hex(),
                                  template_program=p183.hex(), twin='not imported (TKP4 skipped hidden instances): stock MeshAnim(twin) step omitted',
                                  class_row=row, class_handle=hex(oid(row))))

    def layout(head, progs, ds, bs):
        n = len(progs)
        # program data starts at the 16-byte boundary after the table (zero pad), as on every course
        assert st[0] == (ptab + 4 * pc + 15) // 16 * 16 and not any(head[ptab + 4 * pc:st[0]])
        shift = (ptab + 4 * (pc + n) + 15) // 16 * 16 - st[0]
        raw = bytearray(head[:ptab])
        raw += struct.pack(f'<{pc + n}I', *(x + shift for x in st[:pc]), *[0] * n)
        raw += bytes(st[0] + shift - len(raw))
        raw += head[st[0]:pend]
        new_st = []
        for blob in progs:
            raw += bytes(-len(raw) % 16); new_st.append(len(raw)); raw += blob
        raw += bytes(-len(raw) % 16)
        if n:
            struct.pack_into(f'<{n}I', raw, ptab + 4 * pc, *new_st)
        nbase = len(raw)
        struct.pack_into('<3I', raw, 56, pc + n, ptab, nbase)
        offs = []
        for d in ds:
            offs.append(len(raw) - nbase); raw += d
        b_at = len(raw); raw += struct.pack(f'<{len(bs)}H', *bs); raw += bytes(-len(raw) % 4)
        d_at = len(raw); raw += struct.pack(f'<{len(offs)}I', *offs)
        s_at = len(raw); raw += tail
        struct.pack_into('<6I', raw, 68, len(bs), b_at, len(ds), d_at, nspl, s_at)
        return bytes(raw)

    assert pend == base and layout(script, [], defs[:nd], list(struct.unpack_from(f'<{nb}H', script, bat))) == bytes(script)
    patched = layout(data, new_progs, defs, bindings)
    n_new = len(new_progs)
    assert patched[:56] == bytes(script[:56])                       # counts/tables before the program table unchanged
    assert all(any(lo <= i < hi for lo, hi in written) for i in range(92, ptab) if patched[i] != script[i])
    sh = (ptab + 4 * (pc + n_new) + 15) // 16 * 16 - st[0]
    assert patched[st[0] + sh:pend + sh] == bytes(script[st[0]:pend])
    pst = struct.unpack_from(f'<{pc + n_new}I', patched, ptab) + (struct.unpack_from('<I', patched, 64)[0],)
    for k, blob in enumerate(new_progs):
        assert patched[pst[pc + k]:pst[pc + k] + len(blob)] == blob
        disasm(patched[pst[pc + k]:pst[pc + k + 1]])

    new_recs = [(dict(e, size=len(patched)), patched) if e['kind'] == 16 else (e, p) for e, p in recs]
    for e, p in added:
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
    for k in (12,):
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
    proc = subprocess.run([sys.executable, str(REPO / 'local/research/TK10/make_composite.py'), a.iso,
                           str(patched_full), str(out)], capture_output=True, text=True)
    Path(a.receipt).with_suffix('.builder.log').write_text(proc.stdout + proc.stderr)
    if proc.returncode:
        raise SystemExit(proc.returncode)
    after = [digest(out) for _ in range(2)]
    assert after[0] == after[1]
    for f in (tmp, work / 'input-full.BIG'):
        f.unlink()
    rc = dict(operation='TK57: SSX 3 block (ARA1 Roller) and glass (BRA2 p183) templates on Tricky instances',
              input=str(Path(a.input).resolve()), input_sha256=before, output=str(out.resolve()), output_sha256=after,
              patched_full=str(patched_full), patched_full_sha256=digest(patched_full),
              stock_bam_sha256=digest(a.stock_bam), donor_pbd_sha256=hashlib.sha256(pbd_bytes).hexdigest(),
              group=G, track=T, items=receipt_items, appended_records=[[e['kind'], e['rid']] for e, p in added],
              changed_existing_records=[list(k) for k in changed], next_id_table=next_id_table,
              definitions=[nd, len(defs)], bindings=[nb, len(bindings)], programs=[pc, pc + n_new], free_stubs=free_stubs,
              event_table_program_refs=len(in_tables), groups_readback=True,
              composite_builder=json.loads(proc.stdout))
    Path(a.receipt).write_text(json.dumps(rc, indent=2) + '\n')
    print(json.dumps({k: v for k, v in rc.items() if k != 'composite_builder'}, indent=1)[:5000])


if __name__ == '__main__':
    main()
