#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK28: breakaway_passes.py BASE-WORLD.BIG BASE-RECEIPT.json MAP BREAKAWAY.txt RIDE.jsonl T0 T1 [MAXD]  (uv run --with numpy)
Ticks where a ride comes within MAXD (default 60) of the in-game collision mesh of a break-away instance as built in the
BASE world (before the fix): the contacts the fix removes (the rider now passes through)."""
import json, struct, sys
from pathlib import Path
import numpy as np
H = Path(__file__).resolve().parent
sys.path[:0] = [str(H)]
import importlib.util
spec = importlib.util.spec_from_file_location('ph', H / 'phantom.py')
w_path, rc_path, map_path, brk_path, ride_path = sys.argv[1:6]; t0, t1 = int(sys.argv[6]), int(sys.argv[7]); MAXD = float(sys.argv[8]) if len(sys.argv) > 8 else 60
sys.path[:0] = [str(H.parent /'tk12'), str(H.parent /'tk13'), str(H.parent /'tk16')]
from wlib import W
from mapnames import map_names
from ps2col import read_collision
w = W(w_path); rc = json.load(open(rc_path)); names = map_names(open(map_path, encoding='latin1').read())['INTERNAL INSTANCES']
brk = {int(x) for x in open(brk_path).read().split()}
recs = w.records(rc['group']); k3 = {e['rid']: p for e, p in recs if e['kind'] == 3}; k12 = {e['rid']: p for e, p in recs if e['kind'] == 12}
ride = [json.loads(l) for l in open(ride_path)]; ride = [q for q in ride if t0 <= q['tick'] <= t1]
R = np.array([[q['x'], q['y'], q['z']] for q in ride]); T = [q['tick'] for q in ride]
def tri_pts(p, meshes):
    M = struct.unpack_from('<16f', p, 16); s = struct.unpack_from('<f', p, 132)[0]
    Rm = np.array([[M[0], M[1], M[2]], [M[4], M[5], M[6]], [M[8], M[9], M[10]]]); Tr = np.array(M[12:15])
    pts = []
    for m in meshes:
        V = s * (np.array([v[:3] for v in m['vertices']]) @ Rm) + Tr
        for a, b, c in m['triangles']:  # dense samples on each triangle
            for u in np.linspace(0, 1, 7):
                for v in np.linspace(0, 1 - u, max(1, int(7 * (1 - u)))):
                    pts.append(V[a] + u * (V[b] - V[a]) + v * (V[c] - V[a]))
    return np.array(pts)
hits = []
for e in rc['collision_enabled']:
    if e['source'] not in brk: continue
    p = k3[e['instance']]; lo, hi = np.array(struct.unpack_from('<3f', p, 96)), np.array(struct.unpack_from('<3f', p, 108))
    near = np.where(np.linalg.norm(np.maximum(0, np.maximum(lo - R, R - hi)), axis=1) < MAXD)[0]
    if not len(near): continue
    S = tri_pts(p, read_collision(k12[e['collision']]))
    for i in near:
        d = np.linalg.norm(S - R[i], axis=1).min()
        if d < MAXD: hits.append((T[i], e['source'], names.get(e['source']), round(float(d), 1)))
hits.sort()
print('ticks within', MAXD, 'of a break-away collider:', len(hits))
last = None
for h in hits:
    if last is None or h[0] > last + 5: print('\t'.join(map(str, h)))
    last = h[0]
