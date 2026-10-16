#!/usr/bin/env python3
"""TK56: render the per-course bind table (markdown) from survey.py JSON + TK49's instance inventory (names).

    table.py SURVEY.json INVENTORY.tsv [BIND_RECEIPT.json ...] > table.md
Names: TK49 inventory, plus every bind receipt's (world_instance, name) (course = receipt file-name prefix).
Course script = the location whose kind-16 programs are mostly stubs (the Tricky-replaced location).
"""
import csv, json, sys
from collections import Counter, defaultdict

COURSE = {'GARI': 'gari', 'SNOW': 'snow', 'MESA': 'mesa', 'MERQ': 'merq', 'ALOHA': 'aloha', 'ELYS': 'elys',
          'ALASKA': 'alaska', 'PIPE': 'pipe', 'UNTRACK': 'untrack', 'MEGA': 'mega'}
res = json.load(open(sys.argv[1]))
names = defaultdict(dict)
for row in csv.DictReader(open(sys.argv[2]), delimiter='\t'):
    names[row['course']][int(row['rid'])] = row['name']
for f in sys.argv[3:]:
    c = f.rsplit('/', 1)[1].replace('TKP3-receipt-', '').split('-')[0]
    for i in json.load(open(f)).get('instances', []):
        names[c].setdefault(i['world_instance'], i['name'])


def short(n):
    return n.rsplit('_', 1)[0] if n else 'not found'


print('| Course (loc, group, track) | Row bound | Instances (rids: name) | Row runs (create slot 1; other slots) | Intended program (= row index) | Intended prog in load table | Verdict |')
print('| --- | --- | --- | --- | --- | --- | --- |')
for w in res:
    for s in w['scripts']:
        if len(s['nonstub_programs']) >= 0.3 * s['program_count']:
            continue
        c = COURSE[s['world']]
        rows = [r for r in s['bound_rows'] if r['row'] != 1]
        if not rows:
            print(f"| {s['world']} ({s['location']}, g{s['group']}, T{s['track']}) | none (only row 1, {s['bound_rows'][0]['instances']} inst) | — | — | — | — | no binds |")
        for r in rows:
            nm = Counter(short(names[c].get(x)) for x in r['rids'])
            inst = f"{r['instances']}: " + ', '.join(f'{k}×{v}' for k, v in nm.items()) + ' (rids ' + ','.join(map(str, r['rids'])) + ')'
            slots = r['row_slots']
            runs = '; '.join(f'{k} {v}' for k, v in slots.items()) or '(empty row: nothing)'
            ok = r['create_matches_intended'] and len(slots) == 1
            verdict = 'correct' if ok else ('WRONG: neighbour program' if r['create_program_runs'] is not None and 'STUB' not in slots.get('s1', '') else 'WRONG: runs nothing')
            if r['intended_in_load_table']:
                verdict += ' + intended prog runs at load'
            print(f"| {s['world']} ({s['location']}, g{s['group']}, T{s['track']}) | {r['row']} | {inst} | {runs} | p{r['intended_program']} {r['intended_summary']} | {'yes' if r['intended_in_load_table'] else 'no'} | {verdict} |")
