#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TKP5: cross-step check of a chained world against every step's receipt.

    verify_chain.py ISO COURSE FINAL.BIG REFERENCE.BIG --group G --out OUT.json
- TK53 (receipt step1-tk53-<c>.json): each trigger's class row slot 2 names its program; program bytes = receipt.
- TK57 (step2-tk57-<c>.json): each glass/block item's class row slot 2 names its program; bytes = receipt.
- TK51 (s3/<C>.patch.json): its class row slot 1 names the relocated Object program (step5 receipt); the number of
  definitions naming that row = proxies in the receipt.
- TKP5 relocate (step5-reloc-<c>.json): each relocated original slot is the stub again; the new index holds the program.
- Event tables: every program index below the reference's program count whose bytes differ from the reference and are
  not the stub is named by no event table (TK57 §5 rule: any aligned word between the class and program tables).
"""
import argparse, json, struct, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import relocate_step as RS  # noqa: E402
RC = HERE.parent / 'receipts'


def load(path, group, work, tag):
    RS.ISO = ISO
    _, _, _, recs, se, p, tmp = RS.kind16(path, group, work, tag)
    tmp.unlink()
    T = se['track']
    i = RS.script_info(p)
    base = struct.unpack_from('<I', p, 64)[0]
    nb, bat, nd, dat, _, _ = struct.unpack_from('<6I', p, 68)
    cls = [struct.unpack_from('<I', p, base + o + 8)[0] for o in struct.unpack_from(f'<{nd}I', p, dat)]
    rows = [struct.unpack_from('<6I', p, i['ca'] + 24 * r) for r in range(i['cc'])]
    prog = lambda k: bytes(p[i['st'][k]:i['st'][k + 1]])
    ev = {x >> 8 for x in struct.unpack_from(f"<{(i['ptab'] - i['ca'] - 24 * i['cc']) // 4}I", p, i['ca'] + 24 * i['cc'])
          if x & 0xff == T and x != RS.NONE}
    return dict(T=T, pc=i['pc'], rows=rows, prog=prog, cls=cls, ev=ev, recs=recs)


def main():
    global ISO
    ap = argparse.ArgumentParser()
    ap.add_argument('iso'); ap.add_argument('course'); ap.add_argument('final'); ap.add_argument('reference')
    ap.add_argument('--group', type=int, required=True); ap.add_argument('--out', required=True)
    a = ap.parse_args()
    ISO = a.iso
    work = Path.home() / 'dev/ssx3-work/TKP5/scan/vc'; work.mkdir(parents=True, exist_ok=True)
    f = load(a.final, a.group, work, 'final'); r = load(a.reference, a.group, work, 'ref')
    c, C = a.course, a.course.upper()
    checks, ok = {}, True

    def slot_names(row, s, k, hexprog):
        h = f['rows'][row][s]
        return h != RS.NONE and h >> 8 == k and f['prog'](k).hex().startswith(hexprog)

    t53 = RC / f'step1-tk53-{c}.json'
    if t53.exists():
        res = [slot_names(t['class_row'], 2, t['program_slot'], t['program']) for t in json.loads(t53.read_text())['triggers']]
        checks['tk53_triggers'] = [sum(res), len(res)]; ok &= all(res)
    t57 = RC / f'step2-tk57-{c}.json'
    if t57.exists():
        items = json.loads(t57.read_text())['items']
        res = [slot_names(it['class_row'], 2, it['program_slot'], it['program']) for it in items]
        checks['tk57_items'] = [sum(res), len(res)]
        checks['tk57_glass'] = sum(1 for it in items if it['feature'] == 'glass')
        checks['tk57_blocks'] = sum(len(it['rids']) for it in items if it['feature'] == 'blocks')
        ok &= all(res)
    reloc = RC / f'step5-reloc-{c}.json'
    moved = {}
    if reloc.exists():
        for it in json.loads(reloc.read_text())['items']:
            moved[it['program']] = it['new_index']
            good = (f['prog'](it['program'])[:36] == RS.STUB and f['rows'][it['class_row']][it['slot']] >> 8 == it['new_index']
                    and RS.hashlib.sha256(f['prog'](it['new_index'])[:it['length']]).hexdigest() == it['program_sha256'])
            checks.setdefault('relocated', []).append(dict(program=it['program'], new=it['new_index'], row=it['class_row'], ok=good))
            ok &= good
    t51 = Path.home() / f'dev/ssx3-work/TKP5/w/s3/{C}.patch.json'
    if t51.exists():
        d = json.loads(t51.read_text())
        cb = d.get('class_binding')
        if cb:
            row, k = cb['row'], moved.get(cb['create_program'], cb['create_program'])
            n_defs = sum(1 for x in f['cls'] if x != RS.NONE and x >> 8 == row)
            good = f['rows'][row][1] >> 8 == k and n_defs == len(d['hidden_instances'])
            checks['tk51'] = dict(row=row, create_program=k, proxies=len(d['hidden_instances']), definitions_on_row=n_defs, ok=good)
            ok &= good
    changed = [k for k in range(r['pc']) if f['prog'](k) != r['prog'](k)]
    written = [k for k in changed if f['prog'](k)[:36] != RS.STUB]
    hits = [k for k in written if k in f['ev']]
    checks['programs'] = [r['pc'], f['pc']]
    checks['changed_below_reference_pc'] = changed
    checks['written_nonstub_in_event_tables'] = hits
    ok &= not hits and f['ev'] == r['ev']
    checks['event_tables_unchanged'] = f['ev'] == r['ev']
    out = dict(course=c, final=a.final, reference=a.reference, ok=ok, checks=checks)
    Path(a.out).write_text(json.dumps(out, indent=1) + '\n')
    print(c, 'ok' if ok else 'FAIL', json.dumps(checks))


if __name__ == '__main__':
    main()
