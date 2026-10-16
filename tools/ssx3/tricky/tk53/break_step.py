#!/usr/bin/env python3
"""TK53 data step: make Tricky break-away instances break on rider contact, the stock SSX 3 way.

    break_step.py ISO IN_COMPOSITE.BIG OUT_COMPOSITE.BIG --group G --rids R [R ...] --receipt R.json --work DIR

Input: a TKP4-lineage PS2XCMP1 composite. Output: a composite on the same base. Only the course's kind-16
course-script record changes (MEMO.md §4):
  1. a stub program that no class row references gets `PUSHB key1=3; CALLBUILTIN 2,2,1; RETNIL`
     (DeadNode(self, cmd 3) = stock RestoreNode break; first two instructions of stock CRA3 p37 byte for byte);
  2. a free class row (all -1, no definition points at it) gets slot 2 (rider contact) = that program;
  3. each target's definition is cloned as the stock self-DeadNode shape: kind 2, flags 0x210000,
     class = row, collision -1, tail (0.0, 0.0, 2, -1); only the targets are rebound.
No geometry, instance record, collision mesh, other definition, class row or program changes.
Game data stays outside git.
"""
import argparse, hashlib, io, json, struct, subprocess, sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / 'tk12'), str(REPO /'shared')]
from wlib import W  # noqa: E402
from relayout_stream import assemble_archive  # noqa: E402
from retrack import members_of  # noqa: E402
import gamecube_game_dir as gdir  # noqa: E402
gdir.WORLD_EXTENSIONS = ("sdb", "ssb", "phm", "psm")

SEC = 2048
STUB = bytes.fromhex('4c554e001400000024000000240000002aff0000ecffffff000000000200000000000000')
CREATE_SLOT, CONTACT_SLOT = 1, 2
NONE = 0xffffffff
NEW_KIND, NEW_FLAGS, NEW_TAIL = 2, 0x00210000, (0.0, 0.0, 2, -1)


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def materialize(iso_path, composite):
    blob = Path(composite).read_bytes()
    lines = blob[:SEC].split(b'\0')[0].decode().splitlines()
    assert lines[0] == 'PS2XCMP1', lines[0]
    size = int(lines[1].split()[1])
    out = bytearray()
    with open(iso_path, 'rb') as iso:
        for line in lines[3:]:
            if line == 'end':
                break
            kind, x, n = line.split()
            x, n = int(x), int(n)
            if kind == 'self':
                block = blob[x:x + n * SEC]
            else:
                assert kind == 'iso'
                iso.seek(x * SEC)
                block = iso.read(n * SEC)
            assert len(block) == n * SEC
            out += block
    out = bytes(out[:size])
    assert out[:4] == b'BIGF'
    return out


def block_spans(ssb, off):
    """TK42c patch_blend: walk CBXS/CEND blocks from a group's ssb_offset (composites are not linear)."""
    spans, pos = [], off
    while True:
        tag, size = struct.unpack_from('<4sI', ssb, pos)
        if tag not in (b'CBXS', b'CEND'):
            raise ValueError(f'bad block at {pos}')
        spans.append((pos, size))
        pos += size
        if tag == b'CEND':
            return spans


def deadnode_program():
    code = bytes([40, 1, 3, 0]) + bytes([33, 2, 2, 1]) + bytes([42, 0xff, 0, 0])
    code_end = 16 + len(code)
    data_end = code_end + 16
    return (struct.pack('<4I', 0x4e554c, code_end, data_end, data_end) + code
            + struct.pack('<iIII', -code_end, 0, 3, 1))


def object_program():
    """Create-slot program: CALLBUILTIN 2,0,0 (Object, builtin 0) -> behaviour node (vtable 0x490E80) whose
    contact method 0x355770 forwards rider contacts to class slot 2 (MEMO §6). Same bytes as TK50's Object-only."""
    code = bytes([33, 2, 0, 0]) + bytes([42, 0xff, 0, 0])
    code_end = 16 + len(code)
    data_end = code_end + 16
    return (struct.pack('<4I', 0x4e554c, code_end, data_end, data_end) + code
            + struct.pack('<iIII', -code_end, 0, 3, 0))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('iso')
    ap.add_argument('input')
    ap.add_argument('output')
    ap.add_argument('--group', type=int, required=True)
    ap.add_argument('--rids', type=int, nargs='+', required=True)
    ap.add_argument('--receipt', required=True)
    ap.add_argument('--work', required=True)
    ap.add_argument('--create-object', action='store_true',
                    help='also bind slot 1 (create) of the same row to an Object-only program (MEMO §6)')
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    assert not out.exists(), 'use a fresh output path'
    before = [digest(a.input) for _ in range(2)]
    assert before[0] == before[1]

    full = materialize(a.iso, a.input)
    stem_world = work / 'input-full.BIG'
    stem_world.write_bytes(full)
    stem = W(stem_world).stem
    bam_bytes = gdir.rename_world_members(full, 'bam')
    tmp = work / 'input-full.bam.BIG'
    tmp.write_bytes(bam_bytes)
    world = W(tmp)
    recs = world.records(a.group)
    hits = [n for n, (e, p) in enumerate(recs) if e['kind'] == 16]
    assert len(hits) == 1, hits
    ki = hits[0]
    se, script = recs[ki]
    T = se['track']
    data = bytearray(script)

    lc, la, cc, ca = struct.unpack_from('<4I', data, 16)
    pc, ptab, pend = struct.unpack_from('<3I', data, 56)
    st = struct.unpack_from(f'<{pc}I', data, ptab) + (pend,)
    base = struct.unpack_from('<I', data, 64)[0]
    nb, bat, nd, dat, nspl, spl_at = struct.unpack_from('<6I', data, 68)
    assert ca + 24 * cc <= base and la + 4 * lc <= base
    bindings = list(struct.unpack_from(f'<{nb}H', data, bat))
    doffs = struct.unpack_from(f'<{nd}I', data, dat)
    assert all(y - x == 28 for x, y in zip(doffs, doffs[1:])) and bat - base - doffs[-1] == 28
    defs = [bytes(data[base + o:base + o + 28]) for o in doffs]
    tail = bytes(data[spl_at:])
    loads = {h >> 8 for h in struct.unpack_from(f'<{lc}I', data, la)}
    rows = [struct.unpack_from('<6I', data, ca + 24 * r) for r in range(cc)]
    referenced_programs = {h >> 8 for row in rows for h in row if h != NONE}
    rows_used_by_defs = {struct.unpack_from('<I', d, 8)[0] >> 8 for d in defs
                         if struct.unpack_from('<I', d, 8)[0] != NONE}

    # 1. program slot
    blob = deadnode_program()
    free = [k for k in range(2, pc) if bytes(data[st[k]:st[k] + 36]) == STUB and st[k + 1] - st[k] >= len(blob)
            and k not in loads and k not in referenced_programs]
    assert free, 'no unreferenced stub program slot'
    slot = free[0]
    old_program = bytes(data[st[slot]:st[slot + 1]])
    data[st[slot]:st[slot + 1]] = blob + bytes(st[slot + 1] - st[slot] - len(blob))

    create_slot = None
    if a.create_object:
        oblob = object_program()
        ofree = [k for k in free if k != slot and st[k + 1] - st[k] >= len(oblob)]
        assert ofree, 'no second unreferenced stub program slot'
        create_slot = ofree[0]
        old_create = bytes(data[st[create_slot]:st[create_slot + 1]])
        data[st[create_slot]:st[create_slot + 1]] = oblob + bytes(st[create_slot + 1] - st[create_slot] - len(oblob))

    # 2. class row: the definition names row R, and row R's own slots name our programs (no row/program offset)
    free_rows = [r for r in range(2, cc) if rows[r] == (NONE,) * 6 and r not in rows_used_by_defs]
    assert free_rows, 'no free class row'
    row = free_rows[0]
    struct.pack_into('<I', data, ca + 24 * row + 4 * CONTACT_SLOT, slot << 8 | T)
    if create_slot is not None:
        struct.pack_into('<I', data, ca + 24 * row + 4 * CREATE_SLOT, create_slot << 8 | T)

    # 3. definitions
    inst = {e['rid']: p for e, p in recs if e['kind'] == 3}
    new_def = struct.pack('<4I2f2h', NEW_KIND, NEW_FLAGS, row << 8 | T, NONE, *NEW_TAIL)
    defs.append(new_def)
    new_index = len(defs) - 1
    changes = []
    for rid in a.rids:
        old = defs[bindings[rid]]
        kind, flags, cls, coll = struct.unpack_from('<4I', old, 0)
        assert coll == NONE and kind in (0, 3), f'rid {rid}: unexpected definition {old.hex()}'
        q = inst[rid]
        changes.append(dict(rid=rid, old_definition_index=bindings[rid], old_definition=old.hex(),
                            old_kind=kind, old_flags=hex(flags), old_class=hex(cls),
                            new_definition_index=new_index, new_definition=new_def.hex(),
                            position=struct.unpack_from('<3f', q, 64), aabb=struct.unpack_from('<6f', q, 96)))
        bindings[rid] = new_index

    def layout(head, ds, bs):
        raw = bytearray(head[:base])
        offs = []
        for d in ds:
            offs.append(len(raw) - base)
            raw += d
        b_at = len(raw)
        raw += struct.pack(f'<{len(bs)}H', *bs)
        raw += bytes(-len(raw) % 4)
        d_at = len(raw)
        raw += struct.pack(f'<{len(offs)}I', *offs)
        s_at = len(raw)
        raw += tail
        struct.pack_into('<6I', raw, 68, len(bs), b_at, len(ds), d_at, nspl, s_at)
        return bytes(raw)

    original_bindings = list(struct.unpack_from(f'<{nb}H', script, bat))
    assert layout(script, defs[:nd], original_bindings) == bytes(script), 'identity re-layout'
    patched = layout(data, defs, bindings)
    # Only the program slot, one class-row word and the definition/binding tables may differ.
    assert patched[:68] == bytes(script[:68]) and patched[92:ca] == bytes(script[92:ca])
    changed = [i for i in range(92, base) if patched[i] != script[i]]
    assert all(st[slot] <= i < st[slot + 1] or ca + 24 * row + 4 <= i < ca + 24 * row + 12
               or (create_slot is not None and st[create_slot] <= i < st[create_slot + 1]) for i in changed), \
        'bytes outside the program slot and the class-row word changed'
    recs[ki] = (dict(se, size=len(patched)), patched)
    raw = b''.join(bytes([e['kind']]) + len(p).to_bytes(3, 'little') + bytes([e['track']])
                   + e['rid'].to_bytes(3, 'little') + p for e, p in recs)
    groups = world.sdb['groups']
    raws = {g['index']: world.raw(g['index']) for g in groups}
    raws[a.group] = raw
    # Record count and kind <= 12 memsize are unchanged (only the kind-16 record grows), so the SDB is reused.
    ssb = dict(members_of(bam_bytes))['data/worlds/bam.ssb']
    report = dict(groups=[dict(index=g['index'], offset=g['ssb_offset'],
                               sha256=hashlib.sha256(world.raw(g['index'])).hexdigest(),
                               blocks=[dict(offset=o, size=n) for o, n in block_spans(ssb, g['ssb_offset'])])
                          for g in groups])
    bam, _, _ = assemble_archive(bam_bytes, report, raws, bytes(world.sdb_bytes), jobs=6, reuse_original_blocks=True)
    patched_full = work / 'patched-full.BIG'
    patched_full.write_bytes(gdir.rename_world_members(bam, stem))
    back = W(patched_full)
    readback = all(back.raw(g['index']) == raws[g['index']] for g in groups)
    assert readback, 'group readback'
    proc = subprocess.run([sys.executable, str(REPO / 'local/research/TK10/make_composite.py'), a.iso,
                           str(patched_full), str(out)], capture_output=True, text=True)
    Path(a.receipt).with_suffix('.builder.log').write_text(proc.stdout + proc.stderr)
    if proc.returncode:
        raise SystemExit(proc.returncode)
    after = [digest(out) for _ in range(2)]
    assert after[0] == after[1]
    rc = dict(operation='TK53 contact break (DeadNode(self, 3) in class slot 2)',
              input=str(Path(a.input).resolve()), input_sha256=before,
              output=str(out.resolve()), output_sha256=after,
              materialized_input_sha256=hashlib.sha256(full).hexdigest(),
              patched_full_sha256=digest(patched_full),
              group=a.group, track=T, class_count=cc, program_count=pc,
              program_slot=slot, program_slot_old_sha256=hashlib.sha256(old_program).hexdigest(), program_slot_bytes=len(old_program), program_slot_old_is_stub=old_program[:36] == STUB, program=blob.hex(),
              create_slot_program=create_slot,
              create_program=(object_program().hex() if create_slot is not None else None),
              create_slot_old_is_stub=(old_create[:36] == STUB if create_slot is not None else None),
              class_row=row, class_row_slots=[hex(x) for x in struct.unpack_from('<6I', patched, ca + 24 * row)], class_row_slot2=hex(slot << 8 | T), class_handle=hex(row << 8 | T),
              definitions=[nd, len(defs)], changes=changes, groups_readback=readback,
              kind16_bytes=[len(script), len(patched)],
              composite_builder=json.loads(proc.stdout))
    Path(a.receipt).write_text(json.dumps(rc, indent=2) + '\n')
    print(json.dumps({k: v for k, v in rc.items() if k != 'composite_builder'}, indent=2))


if __name__ == '__main__':
    main()
