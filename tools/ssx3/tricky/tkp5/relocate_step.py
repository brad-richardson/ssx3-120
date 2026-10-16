#!/usr/bin/env python3
"""TKP5 chain step: move programs that TK56/TK51 wrote into event-table stubs to appended program slots.

    relocate_step.py ISO IN.BIG OUT.BIG --group G --programs K [K ...] --reference REF.BIG --receipt R.json --work DIR

Why: TK56 `fix_binds.py` (moved load-time bind programs) and TK51 `patch_hidden_colliders.py` (its stock Object create
program) each wrote one program into a stub that no class row or load table named. TK57 §5 found that every such stub
on the Tricky courses is still named by the kind-16 header event tables (+32/+36, +48/+52), which the engine broadcasts
(race start), so an overwritten stub runs with a foreign "self" (TK57 d1/d2, TK53 3a). TK57's hand-off: append instead.

Per program K (each asserted: not a stub, not a load-time program, named by exactly one class row, named by an event
table, and the same index is a stub in REF = the TK49 preset copy, i.e. the slot was a stub before the chain):
  - append K's bytes (the LUN length from its header, verbatim) as a new program at pc, pc+1, ... (TK57 v2 layout:
    the program table grows, program data shifts to the next 16-byte boundary, definitions follow);
  - repoint the one class-row slot that named K to the new index (same track byte);
  - restore K's span to the stock empty-return stub + zero fill, asserted byte-equal to REF's span at K.
Nothing else changes: event tables, load table and other rows are asserted byte-identical, and everything from the
definitions base on (definitions in any layout, e.g. TK51's appended ones, bindings, offsets, tail) is asserted verbatim,
moved by one 16-aligned delta with the header offsets. All other records are byte-identical. Game data stays outside git.
"""
import argparse, hashlib, json, struct, subprocess, sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / 'tk53'), str(REPO / 'tk33b'),
                str(REPO / 'tk16'), str(REPO / 'tk13'), str(REPO / 'tk12'),
                str(REPO / 'tk21'), str(REPO /'shared')]
from break_step import STUB, NONE, block_spans, digest, materialize  # noqa: E402
from wlib import W  # noqa: E402
from luno_survey import disasm  # noqa: E402
from relayout_stream import assemble_archive  # noqa: E402
from retrack import members_of  # noqa: E402
import gamecube_game_dir as gdir  # noqa: E402
gdir.WORLD_EXTENSIONS = ("sdb", "ssb", "phm", "psm")


def script_info(data):
    lc, la, cc, ca = struct.unpack_from('<4I', data, 16)
    pc, ptab, pend = struct.unpack_from('<3I', data, 56)
    st = struct.unpack_from(f'<{pc}I', data, ptab) + (pend,)
    return dict(lc=lc, la=la, cc=cc, ca=ca, pc=pc, ptab=ptab, pend=pend, st=st)


def kind16(path, group, work, tag):
    full = materialize(ISO, path)
    tmp = work / f'{tag}.bam.BIG'
    bam = gdir.rename_world_members(full, 'bam')
    tmp.write_bytes(bam)
    w = W(tmp)
    recs = w.records(group)
    se, p = next((e, p) for e, p in recs if e['kind'] == 16)
    return full, bam, w, recs, se, bytes(p), tmp


def main():
    global ISO
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('iso'); ap.add_argument('input'); ap.add_argument('output')
    ap.add_argument('--group', type=int, required=True)
    ap.add_argument('--programs', type=int, nargs='+', required=True)
    ap.add_argument('--reference', required=True)
    ap.add_argument('--receipt', required=True)
    ap.add_argument('--work', required=True)
    a = ap.parse_args()
    ISO = a.iso
    work = Path(a.work); work.mkdir(parents=True, exist_ok=True)
    out = Path(a.output); out.parent.mkdir(parents=True, exist_ok=True)
    assert not out.exists(), 'use a fresh output path'
    before = [digest(a.input) for _ in range(2)]
    assert before[0] == before[1]
    ref_sha = [digest(a.reference) for _ in range(2)]
    assert ref_sha[0] == ref_sha[1]

    _, _, _, _, _, ref_script, ref_tmp = kind16(a.reference, a.group, work, 'reference')
    ref_tmp.unlink()
    ri = script_info(ref_script)
    full, bam_bytes, world, recs, se, script, tmp = kind16(a.input, a.group, work, 'input')
    stem = W(_w(work / 'input-full.BIG', full)).stem
    G, T = a.group, se['track']
    i = script_info(script)
    lc, la, cc, ca, pc, ptab, pend, st = (i[k] for k in ('lc', 'la', 'cc', 'ca', 'pc', 'ptab', 'pend', 'st'))
    base = struct.unpack_from('<I', script, 64)[0]
    assert pend == base
    nb, bat, nd, dat, nspl, spl_at = struct.unpack_from('<6I', script, 68)
    loads = {h >> 8 for h in struct.unpack_from(f'<{lc}I', script, la)}
    rows = [list(struct.unpack_from('<6I', script, ca + 24 * r)) for r in range(cc)]
    ev_lo, ev_hi = ca + 24 * cc, ptab
    ev_words = struct.unpack_from(f'<{(ev_hi - ev_lo) // 4}I', script, ev_lo)
    in_tables = {x >> 8 for x in ev_words if x & 0xff == T and x != NONE}
    assert ri['pc'] <= pc

    data = bytearray(script)
    new_progs, items = [], []
    for k in a.programs:
        span = bytes(script[st[k]:st[k + 1]])
        assert span[:36] != STUB, f'p{k} is a stub'
        assert k not in loads, f'p{k} is a load-time program'
        assert k in in_tables, f'p{k} is not named by an event table (nothing to relocate)'
        magic, code_end, data_end, length = struct.unpack_from('<4I', span)
        assert magic == 0x4e554c and length <= len(span) and not any(span[length:]), f'p{k} LUN layout'
        blob = span[:length]
        ref_span = bytes(ref_script[ri['st'][k]:ri['st'][k + 1]])
        assert ref_span[:36] == STUB and len(ref_span) == len(span), f'p{k} was not a same-size stub in the reference'
        restored = STUB + bytes(len(span) - 36)
        assert restored == ref_span, f'p{k} reference stub bytes differ from the stock stub + zero fill'
        users = [(r, s) for r, row in enumerate(rows) for s, h in enumerate(row) if h != NONE and h >> 8 == k]
        assert len(users) == 1, f'p{k} row users {users}'
        (r, s), = users
        assert rows[r][s] & 0xff == T
        new_k = pc + len(new_progs)
        new_progs.append(blob)
        rows[r][s] = new_k << 8 | T
        struct.pack_into('<I', data, ca + 24 * r + 4 * s, rows[r][s])
        data[st[k]:st[k + 1]] = restored
        items.append(dict(program=k, new_index=new_k, class_row=r, slot=s, handle_before=hex(k << 8 | T),
                          handle_after=hex(new_k << 8 | T), length=length, program_sha256=hashlib.sha256(blob).hexdigest(),
                          ops=disasm(span), restored_stub_equals_reference=True, event_table=True))

    def layout(head, progs):
        # TK57 v2 append (program table grows, program data shifts to the next 16-byte boundary, new programs follow,
        # 16-aligned), generalised: everything from base on (definitions in any layout, bindings, offsets, tail) is
        # kept verbatim and moved by a 16-aligned delta; the header offsets that point there follow it.
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
        return bytes(raw), delta

    assert layout(script, [])[0] == script
    patched, delta = layout(data, new_progs)
    n_new = len(new_progs)
    # header up to the program table: only the repointed row words differ
    row_words = {ca + 24 * it['class_row'] + 4 * it['slot'] + b for it in items for b in range(4)}
    assert patched[:56] == script[:56]
    assert all(patched[x] == script[x] or x in row_words for x in range(92, ptab))
    assert patched[nbase_new(patched):] == script[base:], 'region from base not verbatim'
    assert patched[ev_lo:ev_hi] == script[ev_lo:ev_hi] and patched[la:la + 4 * lc] == script[la:la + 4 * lc]
    pi = script_info(patched)
    assert pi['pc'] == pc + n_new
    for k in range(pc):
        want = bytes(data[st[k]:st[k + 1]])
        assert patched[pi['st'][k]:pi['st'][k + 1]][:len(want)] == want, f'program {k} moved bytes'
    for it, blob in zip(items, new_progs):
        nk = it['new_index']
        assert patched[pi['st'][nk]:pi['st'][nk] + len(blob)] == blob
        assert disasm(patched[pi['st'][nk]:pi['st'][nk + 1]]) == it['ops']
        assert patched[pi['st'][it['program']]:pi['st'][it['program']] + 36] == STUB

    new_recs = [(dict(e, size=len(patched)), patched) if e['kind'] == 16 else (e, p) for e, p in recs]
    raw = b''.join(bytes([e['kind']]) + len(p).to_bytes(3, 'little') + bytes([e['track']])
                   + e['rid'].to_bytes(3, 'little') + p for e, p in new_recs)
    groups = world.sdb['groups']
    raws = {g['index']: world.raw(g['index']) for g in groups}
    raws[G] = raw
    sdb = bytearray(world.sdb_bytes)
    lcount, nodes, _ = struct.unpack_from('<III', sdb, 8)
    go = (80 + 88 * lcount + 15) // 16 * 16 + nodes * 96 + 68 * G
    assert struct.unpack_from('<H', sdb, go)[0] == len(new_recs)
    struct.pack_into('<I', sdb, go + 8, sum(len(p) + 8 for e, p in new_recs if e['kind'] <= 12))
    counts = Counter(e['kind'] for e, p in new_recs)
    for k in range(14):
        struct.pack_into('<H', sdb, go + 12 + 2 * k, counts.get(k, 0))
    assert bytes(sdb) == world.sdb_bytes, 'SDB group entry changed (only kind 16 grows; it is not counted)'
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
    rc = dict(operation='TKP5: relocate TK56/TK51 programs out of event-table stubs (appended, TK57 v2 layout)',
              input=str(Path(a.input).resolve()), input_sha256=before, reference=str(Path(a.reference).resolve()),
              reference_sha256=ref_sha, output=str(out.resolve()), output_sha256=after,
              patched_full=str(patched_full), patched_full_sha256=digest(patched_full), group=G, track=T,
              programs=[pc, pc + n_new], items=items, event_table_programs=len(in_tables),
              changed_existing_records=[list(k) for k in changed], groups_readback=True,
              composite_builder=json.loads(proc.stdout))
    Path(a.receipt).write_text(json.dumps(rc, indent=2) + '\n')
    print(json.dumps({k: v for k, v in rc.items() if k not in ('composite_builder',)}, indent=1)[:4000])


def nbase_new(p):
    return struct.unpack_from('<I', p, 64)[0]


def _w(p, b):
    p.write_bytes(b)
    return p


if __name__ == '__main__':
    main()
