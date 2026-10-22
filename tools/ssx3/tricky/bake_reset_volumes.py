#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""Bake SSX Tricky donor ResetZone collision triangles into an RSV1 sidecar.

Triangles stay in donor coordinates. The runtime inverse-transforms the rider
path from host space before its swept triangle test. PBD low/high bounds are
only broadphase bounds: a rider can enter one far from the collision surface.

The per-course placement is the world build's own (run_chain.py COURSES
build_args: host location, source/target anchors, yaw, scale), so the zones
land where the converted world does. The old TK10 plan table placed MERQ in
DRA4 (yaw 79.0, ~713k units off the ERA5 build) and GARI at yaw 80.7 (vs 73).
"""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

# Port: the lane repo-rooted its imports; here the helpers are package
# siblings (same 5-dir prepend as the gem tools: the driver runs tools
# with cwd=<workdir> and a stripped PYTHONPATH).
_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE / d) for d in ('tk12', 'tk13', 'tk30',
                                        'shared')] + [str(_HERE)]
from tricky_pbd import PBD
from mapnames import map_names

EVENTS = {
    'GARI': (0, 'gari', 'ARA1'), 'SNOW': (1, 'snow', 'BRA2'),
    'MESA': (2, 'mesa', 'CRA3'), 'MERQ': (4, 'merquer', 'ERA5'),
    'ALASKA': (6, 'alaska', 'DSS2'), 'UNTRACK': (7, 'untrack', 'ESS3'),
    'PIPE': (11, 'pipe', 'BHP1'), 'ALOHA': (14, 'aloha', 'ABC1'),
    'MEGA': (15, 'megaple', 'DBC2'), 'ELYS': (16, 'elysium', 'EBC3'),
}


RECIPE_KEYS = {'GARI': 'gari', 'SNOW': 'snow', 'MESA': 'mesa', 'MERQ': 'merq',
               'ALASKA': 'alaska', 'UNTRACK': 'untrack', 'PIPE': 'pipe',
               'ALOHA': 'aloha', 'MEGA': 'mega', 'ELYS': 'elys'}


def placement(course, host):
    """(source anchor, target anchor, yaw, scale) from the world build recipe."""
    from run_chain import COURSES
    args = COURSES[RECIPE_KEYS[course]]['build_args']

    def arg(key):
        for i, a in enumerate(args):
            if a == key:
                return args[i + 1]
            if a.startswith(key + '='):
                return a.split('=', 1)[1]
        raise KeyError((course, key))

    def vec(key):
        return [float(x) for x in arg(key).split(',')]

    assert arg('--location') == host, (course, arg('--location'), host)
    return vec('--source-anchor'), vec('--target-anchor'), float(arg('--yaw')), float(arg('--scale'))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out", help="output sidecar path (census.json lands beside it)")
    ap.add_argument("--donor-dir", required=True,
                    help="donor extracts dir (per-course <course>/<course>.pbd + .map)")
    ap.add_argument("--untrack-dir", required=True,
                    help="untrack donor dir (<untrack>.pbd + .map)")
    ap.add_argument("--drop-ordinal", action="append", default=[],
                    help="tk-fix-1: skip a ResetZone instance (COURSE:ordinal, repeatable). "
                         "Aloha 1272/1273 sit on the early start chute and reset every run to the "
                         "start; quarantine them while the rider-path question is open.")
    a = ap.parse_args(argv)
    out_path = Path(a.out)
    DONOR = Path(a.donor_dir)
    UNTRACK = Path(a.untrack_dir)
    drop = set()
    for item in a.drop_ordinal:
        try:
            course_key, ordinal = item.split(':')
            drop.add((course_key.strip().upper(), int(ordinal)))
        except ValueError:
            raise SystemExit(f'--drop-ordinal expects COURSE:ordinal, got {item!r}')
    out = ['# RSV1 reset surfaces; source: donor PBD mesh triangles, world build placement',
           '# course EVENT KEY srcXYZ dstXYZ yawDegrees scale; tri KEY ordinal ax ay az bx by bz cx cy cz']
    census = []
    for course, (event, donor, host) in EVENTS.items():
        base = UNTRACK if course == 'UNTRACK' else DONOR / donor
        pbd_path = base / f'{donor}.pbd'
        map_path = base / f'{donor}.map'
        data = pbd_path.read_bytes()
        pbd = PBD(data)
        models = map_names(map_path.read_text())['MODELS']
        src, dst, yaw, scale = placement(course, host)
        out.append('\t'.join(map(str, ('course', course, event, *src, *dst, yaw, scale))))
        found = []
        for i, inst in enumerate(pbd.instances):
            name = str(models.get(inst['model'], ''))
            if 'resetzone' not in name.lower():
                continue
            if (course, i) in drop:
                continue
            model = pbd.models[inst['model']]
            mat = inst['matrix']
            tri_count = 0
            for obj in model['objects']:
                assert obj['matrix'] is None, (course, i, 'animated object')
                for mesh in obj['meshes']:
                    for chunk in pbd.mesh_chunks(mesh):
                        vertices = []
                        for raw in chunk['pos']:
                            local = [raw[j] / 32768 * model['scale'][j] for j in range(3)]
                            vertices.append(tuple(sum(local[j] * mat[j][k] for j in range(3)) + mat[3][k]
                                                  for k in range(3)))
                        for j in range(2, len(vertices)):
                            tri = vertices[j - 2:j + 1]
                            u = [tri[1][k] - tri[0][k] for k in range(3)]
                            v = [tri[2][k] - tri[0][k] for k in range(3)]
                            cross = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
                                     u[0] * v[1] - u[1] * v[0])
                            if sum(x * x for x in cross) < 1.0:
                                continue
                            assert all(math.isfinite(x) for point in tri for x in point)
                            out.append('tri\t{}\t{}\t{}'.format(course, i,
                                       '\t'.join(str(x) for point in tri for x in point)))
                            tri_count += 1
            assert tri_count > 0, (course, i, 'no collision triangles')
            oriented = any(abs(mat[k][j] - (1.0 if k == j else 0.0)) > 1e-4
                           for k in range(3) for j in range(3))
            found.append((i, name, oriented, tri_count))
        census.append(dict(course=course, event=event, donor=donor,
                           pbd_sha256=hashlib.sha256(data).hexdigest(),
                           count=len(found), triangles=sum(t for _, _, _, t in found),
                           dropped_ordinals=sorted(o for c, o in drop if c == course),
                           oriented_mesh=sum(o for _, _, o, _ in found),
                           axis_aligned_mesh=sum(not o for _, _, o, _ in found),
                           model_names=sorted(set(n for _, n, _, _ in found)),
                           reset_target='SSX 3 active course route via 0x11d390',
                           placement=dict(source=src, target=dst, yaw_degrees=yaw, scale=scale)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text('\n'.join(out) + '\n')
    (out_path.parent / 'census.json').write_text(json.dumps(census, indent=2) + '\n')
    print('instances', sum(c['count'] for c in census), 'triangles', sum(c['triangles'] for c in census),
          'sha256', hashlib.sha256(out_path.read_bytes()).hexdigest())
    for c in census:
        print(c['course'], c['count'], c['oriented_mesh'], c['axis_aligned_mesh'])


if __name__ == '__main__':
    main()
