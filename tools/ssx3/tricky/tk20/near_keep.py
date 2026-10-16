#!/usr/bin/env python3
"""TK20: near_keep.py WORLD.BIG RECEIPT.json ROUTE KEEP_D OUT.txt [EXTRA_DROP.txt] -- donor instance indices whose world AABB is farther
than KEEP_D from the race line (route densified every 300), plus EXTRA_DROP's, one per line (make_scenery16 --drop-instances).
Distances as budget_drop.py (TK16)."""
import json, math, struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tk12'))
from wlib import W
w = W(sys.argv[1]); rc = json.load(open(sys.argv[2])); KD = float(sys.argv[4])
rt = [tuple(map(float, l.split())) for l in open(sys.argv[3]) if l.strip()]
pts = []
for a, b in zip(rt, rt[1:]):
    n = max(1, int(math.dist(a[:3], b[:3]) / 300)); pts += [tuple(a[k] + (b[k] - a[k]) * j / n for k in range(3)) for j in range(n)]
src = rc['instance_source']  # rid -> donor index
far = []
for e, p in w.records(rc['group']):
    if e['kind'] != 3: continue
    lo, hi = struct.unpack_from('<3f', p, 96), struct.unpack_from('<3f', p, 108)
    d = min(math.sqrt(sum(max(lo[k] - q[k], 0, q[k] - hi[k]) ** 2 for k in range(3))) for q in pts)
    if d > KD: far.append(src[e['rid']])
extra = [int(l) for l in open(sys.argv[6])] if len(sys.argv) > 6 else []
out = sorted(set(far) | set(extra))
open(sys.argv[5], 'w').write(''.join(f'{i}\n' for i in out))
print(json.dumps(dict(instances=sum(1 for e, _ in w.records(rc['group']) if e['kind'] == 3), far=len(far), extra=len(extra), dropped=len(out))))
