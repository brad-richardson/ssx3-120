#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""tk-routes-1 data step: one Tricky route fixture per run, on a TKP5-lineage PS2XCMP1 composite.

    route_step.py ISO IN.BIG OUT.BIG --group G --receipt R.json --work DIR cover [--rids RID ...] [--cover-rids RID ... ...]
    route_step.py ISO IN.BIG OUT.BIG --group G --receipt R.json --work DIR boost --rid RID --program P [--slot 1|2]

cover (Aloha shortcut cover; the TK53/TKP5 hidden-twin trigger):
  each listed rid is a solid generic wedge (kind 1, 0x210000, static row 1, its own collision resource, part flags 3).
  - the wedge is rebound to the course's existing non-solid drawn definition (kind 0, 0x10000, row 1, no collision,
    part flags 0: the importer's generic shape, 965 Aloha users), so it no longer blocks;
  - a twin instance is appended: a byte copy of the wedge's kind-3 record (same matrix/AABB/model) with a new rid,
    bound to an appended definition in TK53's invisible-trigger shape: kind 1, flags 0x200000 (collide, no draw),
    collision = the wedge's OWN existing collision resource (exact shape it blocked with), part (0, 0, 2, -1):
    contact event, no physical response;
  - the twin's class row (a free row: all -1, named by no definition, not in the header event tables) names, in
    slot 2 (rider contact), an APPENDED program: DeadNode(self, 3); DeadNode(wedge, 3)  (TK53 trigger_program bytes).
  No debris: the donor twins/cMeshAnim pieces are not imported (disappearance only).
  --cover-rids W... (Part 2, repeatable): the same twins for one whole cover, but ONE class row and ONE appended
  program per cover: DeadNode(h, 3) for each of its wedges and twins. Needed because the per-wedge form takes one
  free row per wedge (Aloha: 24 wedges, too few free rows); any twin contact removes the whole cover.
boost (Megaplex tube volume; a fresh valid binding):
  the program P (an existing Boost class program) is byte-copied and APPENDED; a free class row names it in slot
  --slot (default 1 = create, Part 1). Part 2: slot 2 (rider contact). Stock SSX 3 Boost triggers run from slot 1
  with MODE 2 (persistent node); Tricky's converted programs are MODE 1, whose node 0x3415D0 removes once its
  0.9 s timer has run out with no rider in contact, so a slot-1 mode-1 node created at course load is gone ~56
  ticks later (tk-routes-1 Part 2 §F2); slot 2 creates it on contact, like the working Tricky rows (row 3: 12 users);
  an appended definition (2, 0x200000, row, -1, 0, 0, 2, -1) = the volume's current shape with only the class word
  changed; the rid is rebound to it. Program bytes (mode, amount, direction) are unchanged.
Programs are appended, never written into stubs (every Tricky stub is named by a class row or an event table:
TK57 §5, TKP5). Only the kind-16 script changes among existing records; cover adds kind-3 records + SDB counts.
Game data stays outside git.
"""
import argparse, hashlib, json, struct, subprocess, sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
# Port (tk-port-1 / tk-routes-2 follow-up): imports re-rooted onto the
# ported tree (tk53/tk12/tk21/tk6/shared siblings). No logic edits.
R = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(R / 'tk53'), str(R / 'tk12'), str(R / 'tk21'),
                str(R / 'tk6'), str(R / 'shared')]
from break_step import NONE, CONTACT_SLOT, block_spans, digest, materialize  # noqa: E402
from trigger_step import trigger_program, TRIGGER_DEF_HEAD, TRIGGER_PART  # noqa: E402
from wlib import W  # noqa: E402
from luno_survey import disasm  # noqa: E402
from relayout_stream import assemble_archive  # noqa: E402
from retrack import members_of  # noqa: E402
import gamecube_game_dir as gdir  # noqa: E402
gdir.WORLD_EXTENSIONS = ("sdb", "ssb", "phm", "psm")

CREATE_SLOT = 1
NONSOLID = (0, 0x10000, None, NONE, 1.0000000150474662e+30, 0.0, 0, -1)   # class word filled from row 1 of the course
BOOST_DEF_HEAD, BOOST_PART = (2, 0x00200000), (0.0, 0.0, 2, -1)


def lun_blob(span):
    magic, code_end, data_end, length = struct.unpack_from('<4I', span)
    assert magic == 0x4e554c
    assert not any(span[length:]), 'program tail not zero'
    return bytes(span[:length])


def cover_program(oids):
    """DeadNode(oid, 3) for every listed handle (the cover's wedges, then its twins, self among them); RET.
    Same opcodes as TK53 trigger_program (key1 = 3, key0 = handle, CALLBUILTIN 2 DeadNode argc 2)."""
    code = b''
    for h in oids:
        code += bytes([40, 1, 3, 0]) + bytes([39, 0, 0, 0]) + struct.pack('<I', h) + bytes([33, 2, 2, 2])
    code += bytes([42, 0xff, 0, 0])
    code_end = 16 + len(code)
    data_end = code_end + 16
    return (struct.pack('<4I', 0x4e554c, code_end, data_end, data_end) + code
            + struct.pack('<iIII', -code_end, 0, 3, 2))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('iso'); ap.add_argument('input'); ap.add_argument('output')
    ap.add_argument('--group', type=int, required=True)
    ap.add_argument('--receipt', required=True)
    ap.add_argument('--work', required=True)
    sub = ap.add_subparsers(dest='op', required=True)
    c = sub.add_parser('cover'); c.add_argument('--rids', type=int, nargs='*', default=[])
    c.add_argument('--cover-rids', type=int, nargs='+', action='append', default=[],
                   help='one whole cover (Part 2): its wedges share ONE class row; any twin contact removes all its wedges and twins')
    b = sub.add_parser('boost'); b.add_argument('--rid', type=int, required=True); b.add_argument('--program', type=int, required=True)
    b.add_argument('--slot', type=int, default=CREATE_SLOT, choices=(CREATE_SLOT, CONTACT_SLOT))
    a = ap.parse_args()
    work = Path(a.work); work.mkdir(parents=True, exist_ok=True)
    out = Path(a.output); out.parent.mkdir(parents=True, exist_ok=True)
    assert not out.exists(), 'use a fresh output path'
    before = [digest(a.input) for _ in range(2)]
    assert before[0] == before[1]

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
    # definitions may be in any layout (TK51 appends variable-size ones after the tail); the first 28 bytes of each
    # are its head + first part. New definitions are appended (TK51 style), never inserted.
    defs = [bytes(data[base + o:base + o + 28]) for o in doffs]
    assert pend == base
    rows = [struct.unpack_from('<6I', data, ca + 24 * r) for r in range(cc)]
    rows_used = {struct.unpack_from('<I', d, 8)[0] >> 8 for d in defs if struct.unpack_from('<I', d, 8)[0] != NONE}
    ev_lo, ev_hi = ca + 24 * cc, ptab                      # header event tables between class and program tables (TK57)
    assert ev_lo <= ev_hi and ev_lo % 4 == 0
    in_tables = {x >> 8 for x in struct.unpack_from(f'<{(ev_hi - ev_lo) // 4}I', data, ev_lo) if x & 0xff == T and x != NONE}
    free_rows = sorted((r for r in range(2, cc) if rows[r] == (NONE,) * 6 and r not in rows_used and r not in in_tables),
                       key=lambda r: -r)
    written, new_progs, added, items = [], [], [], []
    next_rid = {k: max((e['rid'] for e, p in recs if e['kind'] == k), default=-1) + 1 for k in (3,)}
    inst = {e['rid']: p for e, p in recs if e['kind'] == 3}

    def put_program(blob):
        disasm(blob)
        new_progs.append(blob)
        return pc + len(new_progs) - 1

    def put_row(slot_index, prog):
        row = free_rows.pop(0)
        struct.pack_into('<I', data, ca + 24 * row + 4 * slot_index, oid(prog))
        written.append((ca + 24 * row + 4 * slot_index, ca + 24 * row + 4 * slot_index + 4))
        return row

    if a.op == 'cover':
        static_cls = struct.unpack_from('<I', defs[bindings[a.rids[0]]], 8)[0]
        nonsolid = struct.pack('<4I2f2h', NONSOLID[0], NONSOLID[1], static_cls, *NONSOLID[3:])
        ns_idx = [k for k, d in enumerate(defs) if d == nonsolid]
        assert ns_idx, 'no existing non-solid generic definition'
        ns = ns_idx[0]
        assert a.rids or a.cover_rids
        for rid in a.rids:
            old = defs[bindings[rid]]
            kind, flags, cls, coll, f1, f2, pflags, pend_ = struct.unpack('<4I2f2h', old)
            assert (kind, flags, cls, pflags) == (1, 0x210000, static_cls, 3) and coll != NONE, f'wedge {rid}: {old.hex()}'
            trid = next_rid[3]; next_rid[3] += 1
            prog = trigger_program(oid(rid))
            slot = put_program(prog); row = put_row(CONTACT_SLOT, slot)
            tdef = struct.pack('<4I2f2h', *TRIGGER_DEF_HEAD, oid(row), coll, *TRIGGER_PART)
            defs.append(tdef)
            while len(bindings) <= trid:
                bindings.append(0)
            bindings[trid] = len(defs) - 1
            old_index = bindings[rid]
            bindings[rid] = ns
            ip = bytearray(inst[rid])
            assert struct.unpack_from('<I', ip, 120)[0] == oid(rid), 'instance record handle at +120'
            struct.pack_into('<I', ip, 120, oid(trid))
            added.append((dict(kind=3, track=T, rid=trid), bytes(ip)))
            items.append(dict(wedge_rid=rid, wedge_definition_before=[old_index, old.hex()], wedge_definition_after=[ns, nonsolid.hex()],
                              twin_rid=trid, twin_definition=[len(defs) - 1, tdef.hex()], twin_collision=hex(coll),
                              class_row=row, class_handle=hex(oid(row)), contact_slot=CONTACT_SLOT, program_index=slot,
                              program=prog.hex(), position=struct.unpack_from('<3f', ip, 64), aabb=struct.unpack_from('<6f', ip, 96)))
        for group_rids in a.cover_rids:                       # Part 2: one row per cover (row budget), whole-cover break
            olds = [defs[bindings[rid]] for rid in group_rids]
            heads = {struct.unpack('<4I2f2h', o) for o in olds}
            assert len(heads) == 1, f'cover wedges differ: {heads}'
            kind, flags, cls, coll, f1, f2, pflags, pend_ = heads.pop()
            assert (kind, flags, cls, pflags) == (1, 0x210000, static_cls, 3) and coll != NONE, olds[0].hex()
            trids = list(range(next_rid[3], next_rid[3] + len(group_rids))); next_rid[3] += len(group_rids)
            prog = cover_program([oid(r) for r in group_rids] + [oid(t) for t in trids])
            slot = put_program(prog); row = put_row(CONTACT_SLOT, slot)
            tdef = struct.pack('<4I2f2h', *TRIGGER_DEF_HEAD, oid(row), coll, *TRIGGER_PART)
            defs.append(tdef)
            for rid, trid in zip(group_rids, trids):
                while len(bindings) <= trid:
                    bindings.append(0)
                bindings[trid] = len(defs) - 1
                old_index = bindings[rid]
                bindings[rid] = ns
                ip = bytearray(inst[rid])
                assert struct.unpack_from('<I', ip, 120)[0] == oid(rid), 'instance record handle at +120'
                struct.pack_into('<I', ip, 120, oid(trid))
                added.append((dict(kind=3, track=T, rid=trid), bytes(ip)))
                items.append(dict(wedge_rid=rid, cover=group_rids, wedge_definition_before=[old_index, olds[0].hex()],
                                  wedge_definition_after=[ns, nonsolid.hex()], twin_rid=trid,
                                  twin_definition=[len(defs) - 1, tdef.hex()], twin_collision=hex(coll),
                                  class_row=row, class_handle=hex(oid(row)), contact_slot=CONTACT_SLOT, program_index=slot,
                                  program=prog.hex(), position=struct.unpack_from('<3f', ip, 64), aabb=struct.unpack_from('<6f', ip, 96)))
    else:
        old = defs[bindings[a.rid]]
        kind, flags, cls, coll, f1, f2, pflags, pe2 = struct.unpack('<4I2f2h', old)
        assert (kind, flags, coll, f1, f2, pflags, pe2) == (2, 0x200000, NONE, 0.0, 0.0, 2, -1), old.hex()
        users = [r for r, d in enumerate(bindings) if d == bindings[a.rid]]
        row_before = rows[cls >> 8]
        blob = lun_blob(bytes(data[st[a.program]:st[a.program + 1]]))
        code, funcs = disasm(blob)
        assert any(op == 33 and y == 7 for op, x, y, z, imm in code), 'program is not a Boost call'
        slot = put_program(blob); row = put_row(a.slot, slot)
        ndef = struct.pack('<4I2f2h', *BOOST_DEF_HEAD, oid(row), NONE, *BOOST_PART)
        defs.append(ndef)
        old_index = bindings[a.rid]
        bindings[a.rid] = len(defs) - 1
        items.append(dict(rid=a.rid, definition_before=[old_index, old.hex()], definition_before_users=users,
                          old_class_row=cls >> 8, old_row_slots=[hex(x) for x in row_before],
                          definition_after=[len(defs) - 1, ndef.hex()], source_program=a.program,
                          program_index=slot, program=blob.hex(), class_row=row, class_handle=hex(oid(row)),
                          event_slot=a.slot, boost_args=[(op, x, y, z, imm) for op, x, y, z, imm in code]))

    def layout(head, progs, new_defs, bs):
        # Programs: TKP5 relocate_step layout (TK57 v2 append generalised): the program table grows, program data
        # shifts to the next 16-byte boundary, new programs follow 16-aligned; everything from base on (definitions in
        # any layout, bindings, offsets, tail) is kept verbatim and moved by one 16-aligned delta.
        # Definitions/bindings: TK51 patch_hidden_colliders layout: new definitions are appended at the end (4-aligned),
        # then a new bindings table and a new offsets table; the header names the new tables (old ones stay as dead
        # bytes). With no new definitions and unchanged bindings the input is reproduced exactly.
        n = len(progs)
        assert st[0] == (ptab + 4 * pc + 15) // 16 * 16 and not any(head[ptab + 4 * pc:st[0]])
        shift = (ptab + 4 * (pc + n) + 15) // 16 * 16 - st[0]
        raw = bytearray(head[:ptab])
        raw += struct.pack(f'<{pc + n}I', *(x + shift for x in st[:pc]), *[0] * n)
        raw += bytes(st[0] + shift - len(raw))
        raw += head[st[0]:pend]
        new_st = []
        for blob in progs:
            raw += bytes(-len(raw) % 16); new_st.append(len(raw)); raw += blob
        raw += bytes(-(len(raw) - base) % 16)
        if n:
            struct.pack_into(f'<{n}I', raw, ptab + 4 * pc, *new_st)
        nbase = len(raw); delta = nbase - base
        raw += head[base:]
        struct.pack_into('<3I', raw, 56, pc + n, ptab, nbase)
        struct.pack_into('<6I', raw, 68, nb, bat + delta, nd, dat + delta, nspl, spl_at + delta)
        if not new_defs and list(bs) == list(struct.unpack_from(f'<{nb}H', head, bat)):
            return bytes(raw), nbase, delta
        offs = list(doffs)
        for d in new_defs:
            raw += bytes(-len(raw) % 4); offs.append(len(raw) - nbase); raw += d
        nbat = len(raw); raw += struct.pack(f'<{len(bs)}H', *bs); raw += bytes(-len(raw) % 4)
        ndat = len(raw); raw += struct.pack(f'<{len(offs)}I', *offs)
        struct.pack_into('<6I', raw, 68, len(bs), nbat, len(offs), ndat, nspl, spl_at + delta)
        return bytes(raw), nbase, delta

    assert layout(script, [], [], list(struct.unpack_from(f'<{nb}H', script, bat)))[0] == bytes(script)
    patched, nbase, delta = layout(data, new_progs, defs[nd:], bindings)
    n_new = len(new_progs)
    assert patched[:56] == bytes(script[:56])
    assert all(any(lo <= i < hi for lo, hi in written) for i in range(92, ptab) if patched[i] != script[i])
    sh = (ptab + 4 * (pc + n_new) + 15) // 16 * 16 - st[0]
    assert patched[st[0] + sh:pend + sh] == bytes(script[st[0]:pend]), 'existing programs moved/changed'
    assert patched[nbase:nbase + len(script) - base] == bytes(script[base:]), 'region from base not verbatim'
    pst = struct.unpack_from(f'<{pc + n_new}I', patched, ptab) + (nbase,)
    for k, blob in enumerate(new_progs):
        assert patched[pst[pc + k]:pst[pc + k] + len(blob)] == blob
        disasm(patched[pst[pc + k]:pst[pc + k + 1]])
    # old definitions unchanged; only the planned bindings changed
    nb2, bat2, nd2, dat2, nspl2, spl2 = struct.unpack_from('<6I', patched, 68)
    assert (nspl2, spl2) == (nspl, spl_at + delta) and patched[spl2:spl2 + len(script) - spl_at] == bytes(script[spl_at:])
    doffs2 = struct.unpack_from(f'<{nd2}I', patched, dat2)
    assert list(doffs2[:nd]) == list(doffs)
    assert [bytes(patched[nbase + o:nbase + o + 28]) for o in doffs2] == defs
    b2 = struct.unpack_from(f'<{nb2}H', patched, bat2)
    old_b = struct.unpack_from(f'<{nb}H', script, bat)
    changed_bind = sorted(r for r in range(nb) if b2[r] != old_b[r])
    planned = sorted(a.rids + [r for g in a.cover_rids for r in g]) if a.op == 'cover' else [a.rid]
    assert changed_bind == planned, (changed_bind, planned)
    assert list(b2[nb:]) == bindings[nb:]

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
    if added:
        for k in (3,):                                         # per-location next resource id (TK51/TK53)
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
    proc = subprocess.run([sys.executable, str(R / 'tk10' / 'make_composite.py'), a.iso,
                           str(patched_full), str(out)], capture_output=True, text=True)
    Path(a.receipt).with_suffix('.builder.log').write_text(proc.stdout + proc.stderr)
    if proc.returncode:
        raise SystemExit(proc.returncode)
    after = [digest(out) for _ in range(2)]
    assert after[0] == after[1]
    for f in (tmp, work / 'input-full.BIG'):
        f.unlink()
    rc = dict(operation=f'tk-routes-1 {a.op}', input=str(Path(a.input).resolve()), input_sha256=before,
              output=str(out.resolve()), output_sha256=after, materialized_input_sha256=hashlib.sha256(full).hexdigest(),
              patched_full=str(patched_full), patched_full_sha256=digest(patched_full), group=G, track=T, items=items,
              appended_records=[[e['kind'], e['rid']] for e, p in added], changed_existing_records=[list(k) for k in changed],
              changed_bindings=changed_bind, next_id_table=next_id_table, definitions=[nd, len(defs)],
              bindings=[nb, len(bindings)], programs=[pc, pc + n_new], class_rows_used=[i.get('class_row') for i in items],
              event_table_program_refs=sorted(in_tables), groups_readback=True, composite_builder=json.loads(proc.stdout))
    Path(a.receipt).write_text(json.dumps(rc, indent=2) + '\n')
    print(json.dumps({k: v for k, v in rc.items() if k not in ('composite_builder', 'items')}, indent=1))
    for i in items:
        print({k: v for k, v in i.items() if k not in ('program', 'boost_args')})


if __name__ == '__main__':
    main()
