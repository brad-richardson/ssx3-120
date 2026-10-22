#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK12: autopilot route (world XY per line) = the donor race chain through the world placement.

    make_route.py DONOR.AIP EXPERIMENT.JSON OUT.txt
"""
import json, math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tk12'))
import wlib  # noqa: F401 (tools path)
from course_route import donor_paths
from race_course import donor_race_chain
from import_terrain import apply

d = donor_paths(sys.argv[1])
exp = json.load(open(sys.argv[2]))
races = d['race_paths']
chain = donor_race_chain(races)
pts = []
for i in chain:
    ps = [apply(exp['matrix'], exp['translation'], p) for p in races[i]['points']]
    pts += ps if not pts else ps[1:]
with open(sys.argv[3], 'w') as f:
    for p in pts:
        f.write(f'{p[0]:.1f} {p[1]:.1f} {p[2]:.1f}\n' if len(sys.argv) > 4 and sys.argv[4] == '--z' else f'{p[0]:.1f} {p[1]:.1f}\n')
L = sum(math.dist(a[:2], b[:2]) for a, b in zip(pts, pts[1:]))
print(json.dumps(dict(chain=chain, points=len(pts), length2d=round(L), start=[round(v) for v in pts[0]],
                      end=[round(v) for v in pts[-1]])))
