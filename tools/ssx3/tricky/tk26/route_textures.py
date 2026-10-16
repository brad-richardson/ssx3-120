#!/usr/bin/env python3
"""TK19 Part 2: rank a donor course's terrain textures by how much of the race line runs over them.

route_textures.py DONOR.pbd EXPERIMENT.json ROUTE.txt N [OUT.txt]
The route (x y [z] per line, world units) is densified to ~100-unit steps; each sample takes the texture of the
nearest donor patch (5 surface points per patch, placed with the experiment's matrix/translation, 2D distance).
Prints texture, samples, cumulative %; writes the top N texture indices (one per line) to OUT.txt.
"""
import json, math, struct, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] /'shared'))
from import_terrain import transform_coefficients
from probe_worlds import patch_point

pbd = Path(sys.argv[1]).read_bytes(); exp = json.loads(Path(sys.argv[2]).read_text())
route = [tuple(map(float, l.split()[:2])) for l in open(sys.argv[3]) if l.strip()]
top = int(sys.argv[4])
n, off = struct.unpack_from('<I', pbd, 8)[0], struct.unpack_from('<I', pbd, 68)[0]
pts = []
for (a, b), (c, d) in zip(route, route[1:]):
    k = max(1, int(math.hypot(c - a, d - b) / 100))
    pts += [(a + (c - a) * i / k, b + (d - b) * i / k) for i in range(k)]
P = []
for i in range(n):
    o = off + 448 * i
    w = transform_coefficients([list(struct.unpack_from('<4f', pbd, o + 80 + 16 * j)[:3]) for j in range(16)], exp['matrix'], exp['translation'])
    P.append(([patch_point(w, s, t) for s, t in ((.25, .25), (.75, .25), (.25, .75), (.75, .75), (.5, .5))],
              struct.unpack_from('<I', pbd, o + 432)[0] & 0xffff))
under = Counter()
for x, y in pts:
    under[min(P, key=lambda q: min((c[0] - x) ** 2 + (c[1] - y) ** 2 for c in q[0]))[1]] += 1
tot, acc = sum(under.values()), 0
for k, (t, c) in enumerate(under.most_common()):
    acc += c
    print(k + 1, t, c, round(100 * acc / tot, 1))
if len(sys.argv) > 5:
    Path(sys.argv[5]).write_text(''.join(f'{t}\n' for t, _ in under.most_common(top)))
