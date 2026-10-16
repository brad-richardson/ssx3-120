#!/usr/bin/env python3
"""TK45c: bake the Tricky gem sidecar (one TSV row per gem).

Inputs per course: the shipped world BIG (worlds-final; gem kind-3 records
are byte-identical to the TKP4b preset composites), the scenery.json
(world rid -> donor index) and the donor effects.json (donor -> name).
Position = kind-3 R-translation (lab: instance +40 == R-trans bitwise);
radius = the record's own half-diagonal; value from the donor colour
(Yellow -> x2, Orange -> x3, Red -> x5).

The sidecar is game data: staged, never committed. This tool is the
committed artifact. Verifies f32 round-trip and inter-course disjointness
(the runtime tests every course's gems; spheres must not overlap).
"""
import argparse
import hashlib
import json
import math
import struct
import sys
from pathlib import Path

# Port: the lane imported TK12's world reader; here it is a package sibling
# (provided by the world-pipeline port).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from wlib import W

sha = lambda b: hashlib.sha256(b).hexdigest()


def f32bits(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--course', action='append', required=True,
                    help='ARCH:world:scenery:effects:group (repeat per course)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--receipt', required=True)
    a = ap.parse_args()
    rows = []
    courses = []
    for spec in a.course:
        arch, world, scenery, effects, group = spec.split(':')
        group = int(group)
        wb = Path(world).read_bytes()
        w = W(world)
        k3 = {e['rid']: p for e, p in w.records(group) if e['kind'] == 3}
        sc = json.loads(Path(scenery).read_bytes())
        src = sc['instance_source']
        fx = json.loads(Path(effects).read_bytes())
        names = {i['instance']: i['name'] for i in fx['instances']}
        if len(k3) != len(src):
            raise SystemExit(f'{arch}: kind-3 {len(k3)} != scenery {len(src)} (wrong pairing?)')
        gems = []
        for i, d in enumerate(src):
            n = names.get(d, '')
            if not n.startswith('Gem_TrickMultiplier'):
                continue
            p = k3.get(i)
            if p is None:
                raise SystemExit(f'{arch}: gem rid {i} has no kind-3 record')
            R = struct.unpack_from('<16f', p, 16)
            cx, cy, cz, rad = R[12], R[13], R[14], struct.unpack_from('<f', p, 92)[0]
            if 'Yellow' in n:
                val = 2
            elif 'Orange' in n:
                val = 3
            elif 'Red' in n:
                val = 5
            else:
                raise SystemExit(f'{arch}: rid {i} unknown gem colour {n}')
            if not all(math.isfinite(v) for v in (cx, cy, cz, rad)) or rad <= 0:
                raise SystemExit(f'{arch}: rid {i} bad sphere {(cx, cy, cz, rad)}')
            # f32 round-trip through the TSV text form.
            txt = [repr(float(v)) for v in (cx, cy, cz, rad)]
            rt = [struct.unpack('<f', struct.pack('<f', float(t)))[0] for t in txt]
            if [f32bits(v) for v in rt] != [f32bits(v) for v in (cx, cy, cz, rad)]:
                raise SystemExit(f'{arch}: rid {i} f32 round-trip failed')
            gems.append((i, txt[0], txt[1], txt[2], txt[3], val, n,
                         (f32bits(cx), f32bits(cy), f32bits(cz)), (cx, cy, cz, rad)))
        if not gems:
            raise SystemExit(f'{arch}: no gems baked')
        donor_gems = [i for i in fx['instances'] if i['name'].startswith('Gem_TrickMultiplier')]
        imported = set(src)
        dropped = [(i['instance'], i['name'], i['visible']) for i in donor_gems
                 if i['instance'] not in imported]
        courses.append({'arch': arch, 'world_sha256': sha(wb), 'group': group,
                        'n_gems': len(gems),
                        'values': {str(v): sum(1 for g in gems if g[5] == v) for v in (2, 3, 5)},
                        'donor_gems': len(donor_gems), 'dropped': dropped})
        rows.extend([(arch,) + g[:7] for g in gems])
    # Stacked pairs (same center, intra-course): warn, don't fail (TK45 pair motif).
    seen = {}
    stacks = []
    for r in rows:
        key = (r[0], r[2], r[3], r[4])
        if key in seen:
            stacks.append((r[0], seen[key], r[1]))
        else:
            seen[key] = r[1]
    # Inter-course disjointness (surface gap; the runtime tests all courses).
    sph = {}
    for r in rows:
        sph.setdefault(r[0], []).append((float(r[2]), float(r[3]), float(r[4]), float(r[5])))
    arches = sorted(sph)
    gaps = {}
    for i in range(len(arches)):
        for j in range(i + 1, len(arches)):
            m = min(math.dist(a[:3], b[:3]) - (a[3] + b[3])
                    for a in sph[arches[i]] for b in sph[arches[j]])
            gaps[f'{arches[i]}-{arches[j]}'] = round(m)
            if m < 10000:
                raise SystemExit(f'INTER-COURSE OVERLAP RISK {arches[i]}-{arches[j]} gap {m:.0f}')
    with open(a.out, 'w') as f:
        f.write('# course rid x y z rad value donor (x/y/z = kind-3 R-translation, f32 round-trip)\n')
        for r in rows:
            f.write(' '.join([r[0], str(r[1]), r[2], r[3], r[4], r[5], str(r[6]), r[7]]) + '\n')
    sidecar = Path(a.out).read_bytes()
    receipt = {'courses': courses, 'n_rows': len(rows), 'stacked_pairs': stacks,
               'inter_course_min_gap': gaps, 'sidecar_sha256': sha(sidecar),
               'sidecar_bytes': len(sidecar)}
    Path(a.receipt).write_text(json.dumps(receipt, indent=1) + '\n')
    print(f'baked {len(rows)} gems, {len(courses)} courses, stacked={len(stacks)} '
          f'min_gap={min(gaps.values()):.0f} sha={sha(sidecar)[:12]}')


if __name__ == '__main__':
    main()
