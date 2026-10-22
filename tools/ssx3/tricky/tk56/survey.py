#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK56 step 1: per-course table of Tricky class binds -> class row -> program the row actually runs -> intended program.

    survey.py ISO COMPOSITE.BIG [COMPOSITE.BIG ...] --work DIR --out OUT.json

TK53 MEMO §1/§5: definition word +8 is a CLASS ROW handle (row << 8 | track); the row (24 B, 6 slots) names the program
per event slot. Every Tricky bind wrote its program into program slot S and set the handle to S << 8 | track, i.e. it
meant "program S" but binds "class row S". Here the intended program of a definition bound to row S is pS when pS is
non-stub (our bind wrote it); the actual create program is row S slot 1. Read-only; game data stays outside git.
"""
import argparse, hashlib, json, struct, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / 'tk12'), str(REPO /'shared'), str(REPO / 'tk21')]
from wlib import W  # noqa: E402
from luno_survey import disasm  # noqa: E402
import gamecube_game_dir as gdir  # noqa: E402
gdir.WORLD_EXTENSIONS = ("sdb", "ssb", "phm", "psm")

SEC = 2048
STUB = bytes.fromhex('4c554e001400000024000000240000002aff0000ecffffff000000000200000000000000')
NONE = 0xffffffff
BN = {0: 'Object', 1: 'Debounce', 2: 'DeadNode', 3: 'AnimObject', 6: 'AnimTeeter', 7: 'Boost', 12: 'Flag',
      13: 'MeshAnim', 15: 'Roller', 16: 'Particle', 21: 'UVScroll', 25: 'AddPart', 26: 'AddDynPart', 29: 'Hide',
      97: 'Halo'}


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
                iso.seek(x * SEC)
                block = iso.read(n * SEC)
            assert len(block) == n * SEC
            out += block
    out = bytes(out[:size])
    assert out[:4] == b'BIGF'
    return out


def summarize(blob):
    if blob[:36] == STUB:
        return 'STUB'
    code, funcs = disasm(blob)
    out = []
    for op, a, b, c, imm in code:
        if op == 33:
            out.append(f'{BN.get(b, b)}/{c}')
        elif op == 38:
            out.append(f'k{a}={struct.unpack("<f", struct.pack("<I", imm))[0]:.6g}')
        elif op == 40:
            out.append(f'k{a}=b{b}')
        elif op == 39:
            out.append(f'k{a}=i{imm:#x}')
        elif op == 42:
            out.append('RET')
        else:
            out.append(f'op{op}')
    return ' '.join(out)


def kind16(path_bam):
    w = W(path_bam)
    found = []
    for loc in w.locations():
        for g in range(loc['group_start'], loc['last_group'] + 1):
            for idx, (e, p) in enumerate(w.records(g)):
                if e['kind'] != 16 or len(p) < 92:
                    continue
                found.append((loc['name'], g, idx, e, p))
    return w, found


def survey(name, w, loc, g, e, p):
    lc, la, cc, ca = struct.unpack_from('<4I', p, 16)
    pc, pt, pe = struct.unpack_from('<3I', p, 56)
    st = struct.unpack_from(f'<{pc}I', p, pt) + (pe,)
    base = struct.unpack_from('<I', p, 64)[0]
    nb, bat, nd, dat, nspl, splat = struct.unpack_from('<6I', p, 68)
    loads = [h >> 8 for h in struct.unpack_from(f'<{lc}I', p, la)]
    rows = [struct.unpack_from('<6I', p, ca + 24 * r) for r in range(cc)]
    binds = struct.unpack_from(f'<{nb}H', p, bat)
    doffs = struct.unpack_from(f'<{nd}I', p, dat)
    defs = [struct.unpack_from('<4I', p, base + o) for o in doffs]
    prog = lambda k: bytes(p[st[k]:st[k + 1]])
    # instance rids of this group (kind 3), in record order == binding order
    inst_rids = [ee['rid'] for ee, _ in w.records(g) if ee['kind'] == 3]
    by_row = defaultdict(list)
    for i, d in enumerate(binds):
        cls = defs[d][2]
        if cls == NONE:
            continue
        by_row[cls >> 8].append((i, d, cls & 0xff))
    refd = defaultdict(list)
    for r, row in enumerate(rows):
        for s, h in enumerate(row):
            if h != NONE:
                refd[h >> 8].append((r, s))
    nonstub = [k for k in range(pc) if prog(k)[:36] != STUB]
    entries = []
    for r in sorted(by_row):
        inst = by_row[r]
        row = rows[r] if r < cc else None
        intended = r if r < pc else None
        intended_sum = summarize(prog(intended)) if intended is not None else None
        actual = {s: h >> 8 for s, h in enumerate(row) if h != NONE} if row else {}
        entries.append(dict(
            row=r, track_in_handle=sorted({t for _, _, t in inst}), instances=len(inst),
            rids=[inst_rids[i] if i < len(inst_rids) else f'idx{i}' for i, _, _ in inst],
            definitions=sorted({d for _, d, _ in inst}),
            def_kinds=sorted({(defs[d][0], hex(defs[d][1])) for _, d, _ in inst}),
            row_slots={f's{s}': f'p{k} {summarize(prog(k))}' for s, k in actual.items()},
            intended_program=intended, intended_summary=intended_sum,
            intended_in_load_table=intended in loads,
            intended_rows_referencing=refd.get(intended, []),
            create_program_runs=actual.get(1), create_matches_intended=actual.get(1) == intended,
        ))
    return dict(world=name, location=loc, group=g, track=e['track'], loads=loads, class_count=cc, program_count=pc,
                definitions=nd, bindings=nb, instance_records=len(inst_rids),
                nonstub_programs={f'p{k}': summarize(prog(k)) for k in nonstub},
                nonstub_program_rows={f'p{k}': refd.get(k, []) for k in nonstub},
                free_rows=sum(1 for r in range(cc) if rows[r] == (NONE,) * 6 and r not in by_row),
                bound_rows=entries)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('iso')
    ap.add_argument('composites', nargs='+')
    ap.add_argument('--work', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    res = []
    for c in a.composites:
        name = Path(c).stem
        shas = [hashlib.sha256(Path(c).read_bytes()).hexdigest() for _ in range(2)]
        assert shas[0] == shas[1]
        bam = work / f'{name}.bam.BIG'
        full = materialize(a.iso, c)
        bam.write_bytes(gdir.rename_world_members(full, 'bam'))
        w, found = kind16(bam)
        res.append(dict(composite=str(c), composite_sha256=shas, materialized_sha256=hashlib.sha256(full).hexdigest(),
                        scripts=[survey(name, w, loc, g, e, p) for loc, g, idx, e, p in found]))
        print(name, len(found), 'kind-16 records', file=sys.stderr)
    Path(a.out).write_text(json.dumps(res, indent=1) + '\n')


if __name__ == '__main__':
    main()
