#!/usr/bin/env python3
"""TK13 survey: parse every kind-2 model of a world group with ps2mdr; count shapes and invariants.
    survey.py WORLD.BIG GROUP"""
import json, struct, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tk12')); sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tk13'))
from wlib import W
from ps2mdr import parse_model, model_chunks
w = W(sys.argv[1]); g = int(sys.argv[2])
st = Counter(); fails = Counter(); maxv = 0
for e, p in w.records(g):
    if e['kind'] != 2: continue
    try:
        m = parse_model(p)
        for oi, mi, c in model_chunks(p, m):
            st['chunks'] += 1; maxv = max(maxv, c['verts'])
            st[('base', c['first'], c['base'])] += 1
            st[('nstrips==len', c['nstrips'] == len(c['strips']))] += 1
            st[('striplen-sum==verts', sum(a for a, b in c['strips']) == c['verts'])] += 1
            st[('strip2nd', tuple(sorted(set(b for a, b in c['strips']))))] += 1
            B = c['base']; n = c['verts']; A = c['addr']
            st[('addr-rule', A == dict(strips=B + 2, pos=B + 3, uv=B + 3 + n, nrm=B + 3 + 2 * n))] += 1
            st[('gif', c['gif'][4:].hex())] += 1
        st['ok'] += 1
    except Exception as ex:
        fails[str(ex)[:60]] += 1
print(json.dumps(dict(stats={str(k): v for k, v in st.most_common()}, fails=dict(fails), max_verts=maxv), indent=1))
