#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK56: check a fixed composite against the pre-fix survey.

    verify.py ISO SURVEY_BEFORE.json WORLD COMPOSITE.BIG --work DIR --out OUT.json
For every instance that the pre-fix survey lists on a bind row (row S != 1, intended program pS): its definition's row
now has exactly one slot, slot 1, and that program's bytes summarize identically to pS before. Every load-time program
is the stub. Every instance rid keeps its binding index (instance count unchanged).
"""
import argparse, json, struct, sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from survey import materialize, summarize, kind16, STUB, NONE  # noqa: E402
import gamecube_game_dir as gdir  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('iso'); ap.add_argument('before'); ap.add_argument('world'); ap.add_argument('composite')
ap.add_argument('--work', required=True); ap.add_argument('--out', required=True)
a = ap.parse_args()
before = next(w for w in json.load(open(a.before)) if Path(w['composite']).stem == a.world)
sb = next(s for s in before['scripts'] if len(s['nonstub_programs']) < 0.3 * s['program_count'])
bam = Path(a.work) / f'{a.world}-after.bam.BIG'
bam.parent.mkdir(parents=True, exist_ok=True)
bam.write_bytes(gdir.rename_world_members(materialize(a.iso, a.composite), 'bam'))
w, found = kind16(bam)
(loc, g, idx, e, p), = [f for f in found if f[1] == sb['group']]
lc, la, cc, ca = struct.unpack_from('<4I', p, 16)
pc, pt, pe = struct.unpack_from('<3I', p, 56)
st = struct.unpack_from(f'<{pc}I', p, pt) + (pe,)
base = struct.unpack_from('<I', p, 64)[0]
nb, bat, nd, dat, _, _ = struct.unpack_from('<6I', p, 68)
binds = struct.unpack_from(f'<{nb}H', p, bat)
doffs = struct.unpack_from(f'<{nd}I', p, dat)
cls = [struct.unpack_from('<I', p, base + o + 8)[0] for o in doffs]
prog = lambda k: bytes(p[st[k]:st[k + 1]])
loads = [h >> 8 for h in struct.unpack_from(f'<{lc}I', p, la)]
rows_out, ok = [], True
for r in sb['bound_rows']:
    if r['row'] == 1:
        continue
    for rid in r['rids']:
        c = cls[binds[rid]]
        R = c >> 8
        row = struct.unpack_from('<6I', p, ca + 24 * R)
        slots = {s: h >> 8 for s, h in enumerate(row) if h != NONE}
        runs = summarize(prog(slots[1])) if list(slots) == [1] else None
        good = runs == r['intended_summary'] and (c & 0xff) == e['track']
        ok &= good
        rows_out.append(dict(rid=rid, row_before=r['row'], row_after=R, slots_after={f's{s}': f'p{k}' for s, k in slots.items()},
                             runs=runs, intended=r['intended_summary'], ok=good))
load_stub = {f'p{k}': prog(k)[:36] == STUB for k in loads if k >= 2}
ok &= all(load_stub.values()) and nb == sb['bindings'] and nd == sb['definitions']
res = dict(world=a.world, composite=a.composite, group=g, ok=ok, load_programs_stub=load_stub,
           bindings=[sb['bindings'], nb], definitions=[sb['definitions'], nd], instances=rows_out)
Path(a.out).write_text(json.dumps(res, indent=1) + '\n')
print(a.world, 'ok' if ok else 'FAIL', 'instances', len(rows_out), 'bad', [x['rid'] for x in rows_out if not x['ok']], 'loads', load_stub)
