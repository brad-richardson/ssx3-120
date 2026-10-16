#!/usr/bin/env python3
"""Place a section of SSX Tricky (Garibaldi) terrain into an SSX 3 location (roadmap M4).

Selects Tricky patches near a course centreline segment, rotates the section so its
downhill direction matches a target line in the SSX 3 world, tunes pitch and height so
the surface clears the target line by a small margin, converts each patch into an
SSX 3 terrain record built on a template patch (texture-binding words copied, ids
regenerated), and appends the records to a stream group with updated SDB counts.
Other groups keep the disc's blocks. Build the image with relocate_archive.py --append.

Geometry only: no Tricky textures, lighting, props, or collision objects. The section
is an overlay on existing SSX 3 terrain; wherever it is higher, the rider rides on it.
"""
import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import struct

from inspect_disc import Region, big_members, file_region
from probe_worlds import refpack, resource_records, patch_point, probe_ssx3, export_obj
from build_world_experiment import serialize_resources
from relayout_stream import assemble_archive
from grow_group import add_resources_to_sdb, KIND_PATCH
from paths import workbench_root

TRICKY_STRIDE, TRICKY_COEFF = 448, 80
UV_CORNERS = [(0, 0), (0, 1), (1, 0), (1, 1)]  # SSX 3 stored corner order


def unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def rotation_between(a, b):
    """Rotation matrix taking unit vector a to unit vector b (Rodrigues)."""
    v = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
    c = sum(x * y for x, y in zip(a, b)); s = math.sqrt(sum(x * x for x in v))
    if s < 1e-9:
        return [[1, 0, 0], [0, 1, 0], [0, 0, 1]] if c > 0 else [[-1, 0, 0], [0, -1, 0], [0, 0, 1]]
    k = [x / s for x in v]
    K = [[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]]
    R = [[(1 if i == j else 0) + s * K[i][j] + (1 - c) * sum(K[i][m] * K[m][j] for m in range(3)) for j in range(3)] for i in range(3)]
    return R


def rot_axis(axis, angle):
    k = unit(axis); c, s = math.cos(angle), math.sin(angle)
    K = [[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]]
    return [[(1 if i == j else 0) + s * K[i][j] + (1 - c) * sum(K[i][m] * K[m][j] for m in range(3)) for j in range(3)] for i in range(3)]


def matmul(A, B):
    return [[sum(A[i][m] * B[m][j] for m in range(3)) for j in range(3)] for i in range(3)]


def apply(R, t, p):
    return [sum(R[i][j] * p[j] for j in range(3)) + t[i] for i in range(3)]


def transform_coefficients(coeffs, R, t):
    """Stored power-basis vectors: all rotate; the constant term (stored last) also translates."""
    out = [apply(R, [0, 0, 0], c) for c in coeffs]
    out[15] = [out[15][i] + t[i] for i in range(3)]
    return out


def load_tricky(pbd):
    raw = pbd.read_bytes()
    count = struct.unpack_from('<I', raw, 8)[0]
    offset = struct.unpack_from('<I', raw, 68)[0]
    patches = []
    for i in range(count):
        o = offset + TRICKY_STRIDE * i
        patches.append([list(struct.unpack_from('<4f', raw, o + TRICKY_COEFF + j * 16)[:3]) for j in range(16)])
    return patches


def surface_samples(coeffs, n=8):
    return [patch_point(coeffs, i / n, j / n) for i in range(n + 1) for j in range(n + 1)]


def make_record(template, coeffs):
    payload = bytearray(template)
    for j in range(16):
        struct.pack_into('<4f', payload, 64 + 16 * j, *coeffs[j], 1.0)
    pts = surface_samples(coeffs, 8)
    lo = [min(p[k] for p in pts) for k in range(3)]; hi = [max(p[k] for p in pts) for k in range(3)]
    centre = [(lo[k] + hi[k]) / 2 for k in range(3)]
    radius = math.dist(lo, hi) / 2
    struct.pack_into('<4f', payload, 320, *centre, radius)
    struct.pack_into('<3f', payload, 344, *lo)
    struct.pack_into('<3f', payload, 356, *hi)
    for k, (u, v) in enumerate(UV_CORNERS):
        struct.pack_into('<3f', payload, 368 + 12 * k, *patch_point(coeffs, u, v))
    return bytes(payload)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('archive', type=Path)
    ap.add_argument('--pbd', type=Path, default=None,
                      help='Donor PBD (default: $SSX3_WORKBENCH/extracted/garibaldi/gari.pbd)')
    ap.add_argument('--world-report', type=Path, default=Path('local/reports/ssx3-world.json'))
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--group', type=int, default=2)
    ap.add_argument('--track', type=int, default=1)
    ap.add_argument('--template-rid', type=int, default=213)
    ap.add_argument('--z-range', default='-26000,-18000', help='Tricky centre Z range of the section')
    ap.add_argument('--lateral', type=float, default=4000, help='max distance from the section centreline')
    ap.add_argument('--line', type=Path, required=True, help='ride log (jsonl) giving the target line in SSX 3 space')
    ap.add_argument('--line-x', default='-88500,-73000', help='x range of ride-log samples to use as the target line')
    ap.add_argument('--clearance', type=float, default=30.0, help='minimum surface height above the target line')
    ap.add_argument('--pitch-search', type=float, default=20.0, help='degrees of pitch searched either way')
    ap.add_argument('--entry-length', type=float, default=3000.0, help='length of target line used to fit the entry')
    ap.add_argument('--jobs', type=int, default=6)
    ap.add_argument('--dry-run', action='store_true', help='fit only; print the fit and exit')
    args = ap.parse_args()
    if args.pbd is None:
        args.pbd = workbench_root() / 'extracted/garibaldi/gari.pbd'
    if args.output.exists() and not args.dry_run:
        ap.error('Output directory already exists')
    # --- Tricky section
    tricky = load_tricky(args.pbd)
    centres = [patch_point(c, .5, .5) for c in tricky]
    z0, z1 = (float(x) for x in args.z_range.split(','))
    cand = [i for i, c in enumerate(centres) if z0 <= c[2] <= z1]
    hi = [i for i in cand if centres[i][2] > (z0 + z1) / 2]; lo = [i for i in cand if centres[i][2] <= (z0 + z1) / 2]
    cen = lambda ids: [sum(centres[i][k] for i in ids) / len(ids) for k in range(3)]
    top, bottom = cen(hi), cen(lo)
    d_g = unit([bottom[k] - top[k] for k in range(3)])
    def lateral(i):
        v = [centres[i][k] - top[k] for k in range(3)]; along = sum(v[k] * d_g[k] for k in range(3))
        return math.sqrt(max(0.0, sum(x * x for x in v) - along * along)), along
    section = [i for i in cand if lateral(i)[0] <= args.lateral]
    if len(section) < 10:
        raise ValueError('Section too small; widen the ranges')
    # --- target line
    rows = [json.loads(l) for l in args.line.open()]
    xa, xb = (float(x) for x in args.line_x.split(','))
    line = [[r['x'], r['y'], r['z']] for r in rows if min(xa, xb) <= r['x'] <= max(xa, xb)][::3]
    line_a, line_b = line[0], line[-1]
    d_h = unit([line_b[k] - line_a[k] for k in range(3)])
    R0 = rotation_between(d_g, d_h)
    lateral_axis = unit([d_h[1], -d_h[0], 0.0])
    # --- pitch and height search over the entry stretch only: after entering, the rider
    # follows the imported surface wherever it goes. Minimise the max clearance over the
    # first --entry-length units and shift so the first quarter of that clears by --clearance.
    entry_len = args.entry_length
    entry = [q for q in line if math.dist(q[:2], line_a[:2]) <= entry_len]
    def evaluate(pitch_deg):
        R = matmul(rot_axis(lateral_axis, math.radians(pitch_deg)), R0)
        t = [line_a[k] - sum(R[k][j] * top[j] for j in range(3)) for k in range(3)]
        pts = []
        for i in section:
            pts.extend(apply(R, t, p) for p in surface_samples(tricky[i], 6))
        clear = []
        for x, y, z in entry:
            near = [p for p in pts if abs(p[0] - x) < 900 and abs(p[1] - y) < 900]
            if not near:
                clear.append(None); continue
            p = min(near, key=lambda q: (q[0] - x) ** 2 + (q[1] - y) ** 2)
            clear.append(p[2] - z if math.hypot(p[0] - x, p[1] - y) < 500 else None)
        first = [c for c in clear[:max(2, len(clear) // 4)] if c is not None]
        rest = [c for c in clear if c is not None]
        if not first or len(rest) < len(entry) // 2:
            return None
        shift = args.clearance - min(first)
        return dict(pitch=pitch_deg, shift=shift, max_clear=max(rest) + shift, min_clear=min(rest) + shift, covered=len(rest), R=R, t=[t[0], t[1], t[2] + shift])
    best = None
    for deg10 in range(int(-args.pitch_search * 2), int(args.pitch_search * 2) + 1):
        e = evaluate(deg10 / 2)
        if e and (best is None or e['max_clear'] < best['max_clear']):
            best = e
    if best is None:
        raise ValueError('The section never lies over the entry of the target line; adjust the selection')
    R, t = best['R'], best['t']
    if args.dry_run:
        print(json.dumps(dict(patches=len(section), pitch=best['pitch'], entry_clear_min=round(best['min_clear'], 1),
                              entry_clear_max=round(best['max_clear'], 1), covered=best['covered'], of=len(entry))))
        return
    # --- SSX 3 records
    original = args.archive.read_bytes()
    report = json.loads(args.world_report.read_text())
    if hashlib.sha256(original).hexdigest() != report['archive_sha256']:
        raise ValueError('Source archive differs from inspected baseline')
    region = Region(io.BytesIO(original), 0, len(original)); _, members = big_members(region)
    ssb = file_region(region, members, 'data/worlds/bam.ssb'); sdb_region = file_region(region, members, 'data/worlds/bam.sdb')
    sdb = bytearray(sdb_region.read(0, sdb_region.size))
    group_raw = {}
    for g in report['groups']:
        raw = b''.join(refpack(ssb.read(b['offset'], b['size'])[8:])[0] for b in g['blocks'])
        if hashlib.sha256(raw).hexdigest() != g['sha256']:
            raise ValueError(f'Group {g["index"]} differs from baseline')
        group_raw[g['index']] = raw
    records = list(resource_records(group_raw[args.group]))
    template = next(p for e, p in records if e['kind'] == KIND_PATCH and e['track'] == args.track and e['rid'] == args.template_rid)
    next_rid = max(e['rid'] for e, _ in records if e['kind'] == KIND_PATCH and e['track'] == args.track) + 1
    added, transformed = [], []
    for i in section:
        coeffs = transform_coefficients(tricky[i], R, t)
        payload = bytearray(make_record(template, coeffs))
        struct.pack_into('<I', payload, 336, (next_rid << 8) | args.track)
        added.append((dict(kind=KIND_PATCH, size=len(payload), track=args.track, rid=next_rid), bytes(payload)))
        transformed.append(coeffs); next_rid += 1
    group_raw[args.group] = serialize_resources(records + added)
    count, memsize, owner = add_resources_to_sdb(sdb, report, args.group, len(records), added)
    archive, layout, stream_len = assemble_archive(original, report, group_raw, bytes(sdb), jobs=args.jobs, reuse_original_blocks=True)
    check = probe_ssx3(Region(io.BytesIO(archive), 0, len(archive)))
    expect = dict(report['resource_counts']); expect[str(KIND_PATCH)] += len(added)
    if {str(k): v for k, v in check['resource_counts'].items()} != expect:
        raise ValueError('Rebuilt inventory differs from plan')
    hub_pts = []
    for e, p in records:
        if e['kind'] == KIND_PATCH:
            c = [struct.unpack_from('<4f', p, 64 + 16 * j)[:3] for j in range(16)]
            hub_pts.extend(surface_samples(c, 4))
    above = below = 0
    for c in transformed:
        for q in surface_samples(c, 4):
            near = [h for h in hub_pts if abs(h[0] - q[0]) < 700 and abs(h[1] - q[1]) < 700]
            if near:
                h = min(near, key=lambda r: (r[0] - q[0]) ** 2 + (r[1] - q[1]) ** 2)
                if q[2] >= h[2]: above += 1
                else: below += 1
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'BAM.BIG').write_bytes(archive)
    export_obj(transformed, args.output / 'section-ssx3-space.obj', subdivisions=6)
    samples = [p for c in transformed for p in surface_samples(c, 8)]
    details = dict(mode='import', source='garibaldi', tricky_patches=section, patch_count=len(added), group=args.group, track=args.track,
                   template_rid=args.template_rid, new_rids=[e['rid'] for e, _ in added], location=report['locations'][owner]['name'],
                   section_direction=d_g, target_direction=d_h, target_line=[line_a, line_b], pitch_degrees=best['pitch'],
                   clearance_min=round(best['min_clear'], 1), clearance_max=round(best['max_clear'], 1), entry_length=entry_len, line_points_covered=best['covered'],
                   rotation=R, translation=t, rebuilt_archive_sha256=hashlib.sha256(archive).hexdigest(), rebuilt_archive_bytes=len(archive),
                   stream_bytes=stream_len, group_layout=[l for l in layout if l['index'] == args.group],
                   section_samples_above_hub=above, section_samples_below_hub=below, max_patch_corner_error=check['max_patch_corner_error_games_units'] if False else check['max_patch_corner_error_game_units'], emulator_tested=False)
    (args.output / 'experiment.json').write_text(json.dumps(details, indent=2) + '\n')
    (args.output / 'surface-samples.json').write_text(json.dumps(samples))
    print(json.dumps({k: v for k, v in details.items() if k not in ('rotation', 'new_rids', 'tricky_patches')}, indent=2))


if __name__ == '__main__':
    main()
