#!/usr/bin/env python3
"""TK56 data step: rebind every Tricky class bind to a class row whose create slot runs its intended program.

    fix_binds.py ISO IN_COMPOSITE.BIG OUT_COMPOSITE.BIG --group G --receipt R.json --work DIR

Input: a TKP4-lineage PS2XCMP1 composite. Output: a composite on the same base.

Why (TK53 MEMO §1/§5): definition word +8 is a CLASS ROW handle (row << 8 | track) and each 24-byte row names one
program per event slot. Every PS2 Tricky bind (TK21 Boost, TK24/24c/42/TKP2/TKP3 UVScroll via bind_effects.py) wrote
its program into program slot S and set the handle to S << 8 | track, so the instances bound "class row S" of the
host's stubbed class table, which runs a neighbouring program (or a stub, or nothing). bind_effects.py also picked the
first stub slot from 3 up without skipping the load-time programs (kind-16 +16/+20), so several bind programs also run
at location load. The GameCube binder (`tools/gamecube_interactions.py` free_class_row / free_program_slot) already
does it the right way: a free class row whose slot 1 names the program, in a stub slot that is not a load-time program.

For every class row S bound by a definition (other than the static row 1) whose own-index program pS is non-stub
(the bind's program):
  - already right (row S == only slot 1 = pS, pS not a load program): unchanged;
  - else: if pS is a load-time program, its bytes move to the first unreferenced, non-load stub slot that fits and pS
    returns to the stock empty-return stub + zero fill (exactly what disable_course_scripts wrote);
    a free class row R (all -1, no definition points at it) gets slot 1 = the program; every definition whose class
    handle is S << 8 | track gets R << 8 | track.
Only those program spans, the new rows' slot-1 words and the definitions' class words change; the kind-16 record keeps
its size and layout, every instance record, rid and binding index is unchanged (TK45c gem sidecar keys on course + rid).
Game data stays outside git.
"""
import argparse, hashlib, json, struct, subprocess, sys
from pathlib import Path

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / 'tk12'), str(REPO /'shared'), str(REPO / 'tk56')]
from wlib import W  # noqa: E402
from relayout_stream import assemble_archive  # noqa: E402
from retrack import members_of  # noqa: E402
import gamecube_game_dir as gdir  # noqa: E402
from survey import materialize, summarize  # noqa: E402
gdir.WORLD_EXTENSIONS = ("sdb", "ssb", "phm", "psm")

STUB = bytes.fromhex('4c554e001400000024000000240000002aff0000ecffffff000000000200000000000000')
NONE = 0xffffffff
CREATE_SLOT = 1


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def block_spans(ssb, off):
    spans, pos = [], off
    while True:
        tag, size = struct.unpack_from('<4sI', ssb, pos)
        if tag not in (b'CBXS', b'CEND'):
            raise ValueError(f'bad block at {pos}')
        spans.append((pos, size))
        pos += size
        if tag == b'CEND':
            return spans


def fix_script(script, T):
    data = bytearray(script)
    lc, la, cc, ca = struct.unpack_from('<4I', data, 16)
    pc, ptab, pend = struct.unpack_from('<3I', data, 56)
    st = struct.unpack_from(f'<{pc}I', data, ptab) + (pend,)
    base = struct.unpack_from('<I', data, 64)[0]
    nb, bat, nd, dat, _, _ = struct.unpack_from('<6I', data, 68)
    loads = {h >> 8 for h in struct.unpack_from(f'<{lc}I', data, la)}
    rows = [list(struct.unpack_from('<6I', data, ca + 24 * r)) for r in range(cc)]
    doffs = struct.unpack_from(f'<{nd}I', data, dat)
    binds = struct.unpack_from(f'<{nb}H', data, bat)
    cls_of = [struct.unpack_from('<I', data, base + o + 8)[0] for o in doffs]
    prog = lambda k: bytes(data[st[k]:st[k + 1]])
    is_stub = lambda k: prog(k)[:36] == STUB
    # every stub slot is the stock empty return + zero fill (disable_course_scripts); restoring one is exact
    assert all(prog(k)[36:] == bytes(st[k + 1] - st[k] - 36) for k in range(pc) if is_stub(k))
    referenced = {h >> 8 for row in rows for h in row if h != NONE}
    rows_by_defs = {c >> 8 for c in cls_of if c != NONE}
    bound_rows = sorted({cls_of[d] >> 8 for d in binds if cls_of[d] != NONE} - {1})
    taken_rows, taken_slots, plan = set(), set(), []
    for S in bound_rows:
        assert S < pc and not is_stub(S), f'row {S}: own-index program is a stub (not a bind)'
        handles = {cls_of[d] & 0xff for d in binds if cls_of[d] >> 8 == S}
        assert handles == {T}, (S, handles)
        right = rows[S] == [NONE, S << 8 | T, NONE, NONE, NONE, NONE] and S not in loads
        entry = dict(row=S, program=S, summary=summarize(prog(S)), row_before=[hex(x) for x in rows[S]],
                     load_time=S in loads, definitions=[d for d, c in enumerate(cls_of) if c >> 8 == S],
                     instances=sum(1 for d in binds if cls_of[d] >> 8 == S))
        if right:
            plan.append(dict(entry, action='unchanged (row already runs its program)'))
            continue
        target = S
        if S in loads:
            # the program must not be referenced by any host row either (checked: it would run there too)
            assert not any(h >> 8 == S for row in rows for h in row if h != NONE), f'p{S} referenced by a row'
            length = struct.unpack_from('<I', data, st[S] + 12)[0]
            blob = prog(S)[:length]
            assert prog(S)[length:] == bytes(st[S + 1] - st[S] - length)
            free = [k for k in range(2, pc) if is_stub(k) and k not in loads and k not in referenced
                    and k not in taken_slots and st[k + 1] - st[k] >= len(blob)]
            assert free, 'no free program slot'
            target = free[0]
            taken_slots.add(target)
            data[st[target]:st[target + 1]] = blob + bytes(st[target + 1] - st[target] - len(blob))
            data[st[S]:st[S + 1]] = STUB + bytes(st[S + 1] - st[S] - 36)
        free_rows = [r for r in range(2, cc) if rows[r] == [NONE] * 6 and r not in rows_by_defs and r not in taken_rows]
        assert free_rows, 'no free class row'
        R = free_rows[0]
        taken_rows.add(R)
        rows[R][CREATE_SLOT] = target << 8 | T
        struct.pack_into('<I', data, ca + 24 * R + 4 * CREATE_SLOT, target << 8 | T)
        for d, o in enumerate(doffs):
            if cls_of[d] == S << 8 | T:
                struct.pack_into('<I', data, base + o + 8, R << 8 | T)
        plan.append(dict(entry, action='rebound', new_row=R, new_row_slots=[hex(x) for x in rows[R]],
                         program_moved_to=(target if target != S else None),
                         load_slot_restored_to_stub=S in loads, runs=f'p{target} {summarize(prog(target))}'))
    return bytes(data), plan, dict(loads=sorted(loads), class_count=cc, program_count=pc, definitions=nd, bindings=nb,
                                   st=st, ca=ca, base=base, doffs=doffs)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('iso')
    ap.add_argument('input')
    ap.add_argument('output')
    ap.add_argument('--group', type=int, required=True)
    ap.add_argument('--receipt', required=True)
    ap.add_argument('--work', required=True)
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    assert not out.exists(), 'use a fresh output path'
    before = [digest(a.input) for _ in range(2)]
    assert before[0] == before[1]

    full = materialize(a.iso, a.input)
    stem = W(_write(work / 'input-full.BIG', full)).stem
    bam_bytes = gdir.rename_world_members(full, 'bam')
    world = W(_write(work / 'input-full.bam.BIG', bam_bytes))
    recs = world.records(a.group)
    hits = [n for n, (e, p) in enumerate(recs) if e['kind'] == 16]
    assert len(hits) == 1, hits
    ki = hits[0]
    se, script = recs[ki]
    T = se['track']
    patched, plan, info = fix_script(script, T)
    assert len(patched) == len(script)
    # allowed bytes: moved/restored program spans, new rows' slot-1 words, definition class words
    st, ca, base = info['st'], info['ca'], info['base']
    allowed = set()
    for p in plan:
        if p['action'] != 'rebound':
            continue
        for k in [p['row']] + ([p['program_moved_to']] if p['program_moved_to'] is not None else []):
            if p['load_slot_restored_to_stub'] or k != p['row']:
                allowed |= set(range(st[k], st[k + 1]))
        allowed |= set(range(ca + 24 * p['new_row'] + 4, ca + 24 * p['new_row'] + 8))
        for d in p['definitions']:
            allowed |= set(range(base + info['doffs'][d] + 8, base + info['doffs'][d] + 12))
    changed = [i for i in range(len(script)) if patched[i] != script[i]]
    assert all(i in allowed for i in changed), 'bytes outside the planned spans changed'
    recs[ki] = (se, patched)
    raw = b''.join(bytes([e['kind']]) + len(p).to_bytes(3, 'little') + bytes([e['track']])
                   + e['rid'].to_bytes(3, 'little') + p for e, p in recs)
    assert len(raw) == len(world.raw(a.group))
    groups = world.sdb['groups']
    raws = {g['index']: world.raw(g['index']) for g in groups}
    raws[a.group] = raw
    ssb = dict(members_of(bam_bytes))['data/worlds/bam.ssb']
    report = dict(groups=[dict(index=g['index'], offset=g['ssb_offset'],
                               sha256=hashlib.sha256(world.raw(g['index'])).hexdigest(),
                               blocks=[dict(offset=o, size=n) for o, n in block_spans(ssb, g['ssb_offset'])])
                          for g in groups])
    bam, _, _ = assemble_archive(bam_bytes, report, raws, bytes(world.sdb_bytes), jobs=6, reuse_original_blocks=True)
    patched_full = _write(work / 'patched-full.BIG', gdir.rename_world_members(bam, stem))
    back = W(patched_full)
    readback = all(back.raw(g['index']) == raws[g['index']] for g in groups)
    assert readback, 'group readback'
    # instance records (kind 3) and every other record of the group are byte-identical
    brecs = back.records(a.group)
    assert [(e, p) for n, (e, p) in enumerate(brecs) if n != ki] == [(e, p) for n, (e, p) in enumerate(recs) if n != ki]
    proc = subprocess.run([sys.executable, str(REPO / 'tk10/make_composite.py'), a.iso,
                           str(patched_full), str(out)], capture_output=True, text=True)
    Path(a.receipt).with_suffix('.builder.log').write_text(proc.stdout + proc.stderr)
    if proc.returncode:
        raise SystemExit(proc.returncode)
    after = [digest(out) for _ in range(2)]
    assert after[0] == after[1]
    rc = dict(operation='TK56 class-row rebind (row whose create slot runs the intended program; load slots restored)',
              input=str(Path(a.input).resolve()), input_sha256=before, output=str(out.resolve()), output_sha256=after,
              materialized_input_sha256=hashlib.sha256(full).hexdigest(), patched_full_sha256=digest(patched_full),
              group=a.group, track=T, loads=info['loads'], class_count=info['class_count'],
              program_count=info['program_count'], kind16_bytes=len(script), changed_bytes=len(changed),
              groups_readback=readback, plan=plan, composite_builder=json.loads(proc.stdout))
    Path(a.receipt).write_text(json.dumps(rc, indent=2) + '\n')
    print(json.dumps({k: v for k, v in rc.items() if k != 'composite_builder'}, indent=1))


def _write(path, b):
    Path(path).write_bytes(b)
    return path


if __name__ == '__main__':
    main()
