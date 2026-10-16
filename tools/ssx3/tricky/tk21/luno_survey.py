#!/usr/bin/env python3
"""TK21 Part B: survey SSX 3 PS2 course scripts (kind 16) across every stock location in BAM.BIG.

PS2 kind-16 layout (TK13 REPORT + replace_terrain.disable_course_scripts, little-endian): +56 program count/table/end;
+64 definition base; +68 binding count, binding table, definition count, definition offsets, spline count, spline table.
Programs are LUN chunks ('LUN\\0', code_end, data_end, length); code words are BYTES op, A, B, C (as gamecube_lun.py's BE
words); the nine immediate opcodes (20-23, 29, 36-39) carry a LITTLE-endian immediate word. Function records follow the code
(back offset, entry pc, params, stack; LE). Definition +8 = class handle = program index << 8 | track (TK21 reading: class
rids stay below the program count on every location; 0x108 -> program 1 = the empty return).
    luno_survey.py BAM.BIG OUT.json
"""
import json, struct, sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tk12')); sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'shared'))
from wlib import W

IMM = {20, 21, 22, 23, 29, 36, 37, 38, 39}
NAMES = {18: 'SETINDEX', 20: 'LOADNAME', 29: 'CLOSURE', 33: 'CALLBUILTIN', 42: 'RETNIL', 38: 'PUSHF', 39: 'PUSHI', 40: 'PUSHB'}


def disasm(blob):
    magic, code_end, data_end, length = struct.unpack_from('<4I', blob)
    assert magic == 0x4e554c, hex(magic)
    out, pc = [], 16
    while pc < code_end:
        op, a, b, c = blob[pc:pc + 4]; pc += 4; imm = None
        if op in IMM:
            imm = struct.unpack_from('<I', blob, pc)[0]; pc += 4
        out.append((op, a, b, c, imm))
    nf = (data_end - code_end) // 16
    funcs = [struct.unpack_from('<iIII', blob, code_end + 16 * i) for i in range(nf)]
    return out, funcs


def main():
    w = W(sys.argv[1]); res = {}
    allb = Counter()
    for loc in w.locations():
        for g in range(loc['group_start'], loc['last_group'] + 1):
            for e, p in w.records(g):
                if e['kind'] != 16 or len(p) < 92: continue
                count, table, end = struct.unpack_from('<3I', p, 56)
                if count == 0: continue
                starts = struct.unpack_from(f'<{count}I', p, table)
                spans = list(zip(starts, (*starts[1:], end)))
                base = struct.unpack_from('<I', p, 64)[0]
                bc, bt, dc, do, sc, st = struct.unpack_from('<6I', p, 68)
                doffs = struct.unpack_from(f'<{dc}I', p, do) if dc else ()
                binds = struct.unpack_from(f'<{bc}H', p, bt) if bc else ()
                defs = [struct.unpack_from('<4I', p, base + o) for o in doffs]
                cls_inst = Counter()
                for b_ in binds:
                    if b_ < len(defs): cls_inst[defs[b_][2] >> 8] += 1
                progs = {}
                for k, (s, t) in enumerate(spans):
                    try:
                        code, funcs = disasm(p[s:t])
                    except AssertionError:
                        continue
                    if len(code) <= 1: continue
                    bi = Counter(c for op, a, b, c_, imm in code if op == 33 for c in [b])
                    allb.update(bi)
                    progs[k] = dict(ops=len(code), funcs=len(funcs),
                                    builtins=dict(sorted(bi.items())),
                                    names=sorted({hex(imm) for op, a, b, c, imm in code if op == 20}),
                                    floats=sorted({round(struct.unpack('<f', struct.pack('<I', imm))[0], 3) for op, a, b, c, imm in code if op == 38})[:12],
                                    instances_bound=cls_inst.get(k, 0))
                res[loc['name']] = dict(group=g, programs=count, definitions=dc, bindings=bc,
                                        classes=dict(sorted(Counter(d[2] >> 8 for d in defs).items())),
                                        class_instances=dict(sorted(cls_inst.items())), nonempty=progs)
    res['_builtin_totals'] = dict(sorted(allb.items()))
    json.dump(res, open(sys.argv[2], 'w'), indent=1)
    print('locations with scripts:', len(res) - 1, 'builtin index use:', dict(sorted(allb.items())))


if __name__ == '__main__':
    main()
