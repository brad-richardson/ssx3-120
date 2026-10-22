#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK33: u0zero.py -- per course: families whose Tricky property is mode 1 + player collision (flag 0x20) with u0 = 0
(not the 1e30 'immovable' sentinel), and whether our TK16-lineage world made them solid (receipt how).
Usage: u0zero.py --root <lane work root>"""
import argparse, json, struct, sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'shared'), str(Path(__file__).resolve().parents[1] / 'tk13')]
from gamecube_scenery import instance_gameplay
from mapnames import map_names
ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--root', required=True, help='lane work root holding the TK16/TK18/TK20/TK23 src/ and w/ trees')
D = ap.parse_args().root.rstrip('/') + '/'
C = {'mesa': ('TK16/src/mesa', 'TK16/w/mesa'), 'gari': ('TK16/src/gari', 'TK16/w/gari'), 'snow': ('TK16/src/snow', 'TK16/w/snow'),
     'merq': ('TK16/src/merquer', 'TK16/w/merq-d'),
     'aloha': ('TK20/src/aloha/aloha', 'TK20/w/aloha-b'), 'elys': ('TK20/src/elysium/elysium', 'TK20/w/elys-b'),
     'alaska': ('TK18/src/alaska/alaska', 'TK18/w/alaska-b'), 'mega': ('TK18/src/megaple/megaple', 'TK23/w/mega-h')}
for c, (s, w) in C.items():
    for p in (D + s + '-ps2.gsf', D + s + '.map', D + w + '/scenery.json'):
        if not Path(p).is_file():
            raise SystemExit(f'u0zero: missing input {p} (pass --root <lane work root>)')
    g = open(D + s + '-ps2.gsf', 'rb').read(); names = map_names(open(D + s + '.map', encoding='latin1').read())['INTERNAL INSTANCES']
    rows = instance_gameplay(g, struct.unpack_from('>6I', g, 52)[2])
    how = {e['source']: e['how'] for e in json.load(open(D + w + '/scenery.json'))['collision_enabled']}
    fam = defaultdict(Counter)
    for i, r in enumerate(rows):
        if r['collision_mode'] == 1 and r['player_collision'] and not r['immovable']:
            fam[names.get(i, '?').rsplit('_', 1)[0]][how.get(i, 'not solid')] += 1
    n = sum(sum(v.values()) for v in fam.values()); ns = sum(v[k] for v in fam.values() for k in v if k != 'not solid')
    print(f'== {c}: {n} instances mode1+u0=0 ({ns} solid in our world)')
    for f, v in sorted(fam.items(), key=lambda kv: -sum(kv[1].values())): print(f'   {f:40s} {dict(v)}')
