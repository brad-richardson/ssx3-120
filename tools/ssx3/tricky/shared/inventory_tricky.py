#!/usr/bin/env python3
"""Read-only inventory of every SSX Tricky (PS2) course archive on the disc.

For each DATA/MODELS/*.BIG (C0FB archive, RefPack members) this lists the
members by extension with stored and decoded sizes, decodes the .pbd terrain
file (448-byte patch records, see docs/investigation.md) and reports patch
count, extent, drop, an elevation-slice path length, slope, surface types,
texture and lightmap references. It also summarises SSX 3 per-location stream
sizes from local/reports/ssx3-world.json so the two can be compared. Nothing is
written except the JSON report requested with --report.

Layouts follow GlitcherOG/SSX-Library PBDHandler.cs; the patch tail fields are
its labels and are unverified in play. The Tricky .aip layout differs from the
library's original-SSX AIPHandler (magic 0x0A0A0A0A) and is not decoded here.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_disc import Region, big_members, file_region, iso_files  # noqa: E402
from probe_worlds import patch_point, refpack  # noqa: E402

ARCHIVES = ['ALASKA', 'ALOHA', 'ELYSIUM', 'GARI', 'MEGAPLE', 'MERQUER',
            'MESA', 'PIPE', 'SNOW', 'UNTRACK', 'TRICK', 'SSXFE']
PATCH_STRIDE = 448
PATCH_COEFF = 80
PATCH_BOUNDS = 336
PATCH_CORNERS = 360
PATCH_TAIL = 424
MATERIAL_STRIDE = 72
HEADER_COUNTS = ['player_starts', 'patches', 'instances', 'particle_instances',
                 'materials', 'material_blocks', 'lights', 'splines',
                 'spline_segments', 'texture_flipbooks', 'models',
                 'particle_models', 'textures', 'cameras', 'lightmap_size']


def decode_member(region, entry):
    stored = region.child(entry['offset'], entry['stored_size']).read(0, entry['stored_size'])
    if stored[:2] == b'\x10\xfb':
        raw, consumed = refpack(stored)
        if consumed != len(stored):
            raise ValueError(f"Trailing data after RefPack member {entry['path']}")
        return raw, True
    return stored, False


def pbd_header(raw):
    counts = struct.unpack_from('<15I', raw, 4)
    offsets = struct.unpack_from('<18I', raw, 64)
    return dict(zip(HEADER_COUNTS, counts)), offsets


def ssh_count(raw):
    if raw[:4] != b'SHPS':
        return None
    return struct.unpack_from('<I', raw, 8)[0]


def polyline_length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def patch_slope(coefficients):
    """Slope angle at the patch centre (degrees from horizontal) and a
    parallelogram area weight from the tangent vectors."""
    h = 1e-3
    c = (patch_point(coefficients, 0.5 + h, 0.5), patch_point(coefficients, 0.5 - h, 0.5),
         patch_point(coefficients, 0.5, 0.5 + h), patch_point(coefficients, 0.5, 0.5 - h))
    du = [(c[0][i] - c[1][i]) / (2 * h) for i in range(3)]
    dv = [(c[2][i] - c[3][i]) / (2 * h) for i in range(3)]
    n = (du[1] * dv[2] - du[2] * dv[1], du[2] * dv[0] - du[0] * dv[2], du[0] * dv[1] - du[1] * dv[0])
    length = math.sqrt(sum(x * x for x in n))
    if length == 0:
        return None, 0.0
    return math.degrees(math.acos(min(1.0, abs(n[2]) / length))), length


def analyse_pbd(raw):
    counts, offsets = pbd_header(raw)
    patch_offset, instance_offset, _, material_offset = offsets[1:5]
    n = counts['patches']
    if patch_offset + n * PATCH_STRIDE != instance_offset:
        raise ValueError('Unexpected Tricky patch layout')
    materials = [struct.unpack_from('<h', raw, material_offset + MATERIAL_STRIDE * i)[0]
                 for i in range(counts['materials'])]
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    surfaces, assignments, lightmaps, textures = Counter(), Counter(), Counter(), Counter()
    tails = [Counter() for _ in range(5)]
    slope_sum = weight_sum = 0.0
    slopes = []
    centres = []
    hidden = 0
    for i in range(n):
        o = patch_offset + PATCH_STRIDE * i
        coefficients = [struct.unpack_from('<4f', raw, o + PATCH_COEFF + 16 * j)[:3] for j in range(16)]
        bmin = struct.unpack_from('<3f', raw, o + PATCH_BOUNDS)
        bmax = struct.unpack_from('<3f', raw, o + PATCH_BOUNDS + 12)
        lo = [min(a, b) for a, b in zip(lo, bmin)]
        hi = [max(a, b) for a, b in zip(hi, bmax)]
        surface, u2, vis, tex, lm = struct.unpack_from('<I4h', raw, o + PATCH_TAIL)
        rest = struct.unpack_from('<3I', raw, o + PATCH_TAIL + 12)
        surfaces[surface] += 1
        assignments[tex] += 1
        lightmaps[lm] += 1
        textures[materials[tex] if 0 <= tex < len(materials) else None] += 1
        for counter, value in zip(tails, (u2, vis) + rest):
            counter[value] += 1
        hidden += vis != 0
        angle, weight = patch_slope(coefficients)
        if angle is not None:
            slope_sum += angle * weight
            weight_sum += weight
            slopes.append(angle)
        centres.append(patch_point(coefficients, 0.5, 0.5))
    slopes.sort()
    bins = dict(under_30=sum(s < 30 for s in slopes), from_30_to_60=sum(30 <= s < 60 for s in slopes),
                over_60=sum(s >= 60 for s in slopes))
    return dict(
        header=counts,
        patches=n,
        patch_bytes=n * PATCH_STRIDE,
        extent_min=lo, extent_max=hi,
        size=[b - a for a, b in zip(lo, hi)],
        drop=hi[2] - lo[2],
        slope_area_weighted_deg=slope_sum / weight_sum if weight_sum else None,
        slope_median_deg=slopes[len(slopes) // 2] if slopes else None,
        slope_bins=bins, hidden_patches=hidden,
        surface_types=len(surfaces), surface_histogram={str(k): v for k, v in sorted(surfaces.items())},
        texture_assignments=len(assignments), texture_ids=len(textures),
        lightmap_ids=len(lightmaps), material_records=len(materials),
        distinct_material_texture_ids=len(set(materials)),
        tail_distinct=[len(c) for c in tails],
        tail_top=[c.most_common(3) for c in tails],
        centres=centres,
    )


def spine_length(centres, slices=60):
    """Path estimate for a descending course: chain the mean position of
    equal-count elevation slices (top to bottom). Also returns the straight
    top-to-bottom chord. Meaningless for non-descending scenes (front end)."""
    if len(centres) < 2:
        return 0.0, 0.0
    ordered = sorted(centres, key=lambda p: -p[2])
    step = max(1, len(ordered) // slices)
    spine = []
    for i in range(0, len(ordered), step):
        chunk = ordered[i:i + step]
        spine.append(tuple(sum(p[k] for p in chunk) / len(chunk) for k in range(3)))
    return polyline_length(spine), math.dist(spine[0], spine[-1])


def inventory_archive(disc, files, name):
    region = file_region(disc, files, f'DATA/MODELS/{name}.BIG')
    kind, members = big_members(region)
    result = dict(archive=f'{name}.BIG', kind=kind, stored_size=region.size,
                  archive_sha256=None, members=[], by_extension={}, pbd=None)
    h = hashlib.sha256()
    for pos in range(0, region.size, 1 << 22):
        h.update(region.read(pos, min(1 << 22, region.size - pos)))
    result['archive_sha256'] = h.hexdigest()
    ext_counts, ext_bytes = Counter(), Counter()
    for entry in members:
        raw, compressed = decode_member(region, entry)
        ext = Path(entry['path']).suffix.lower().lstrip('.') or '(none)'
        member = dict(path=entry['path'], ext=ext, stored=entry['stored_size'], decoded=len(raw),
                      compressed=compressed, sha256=hashlib.sha256(raw).hexdigest())
        if ext == 'ssh':
            member['ssh_entries'] = ssh_count(raw)
        if ext == 'pbd':
            info = analyse_pbd(raw)
            member['pbd'] = {k: v for k, v in info.items() if k != 'centres'}
            member['pbd']['spine_length'], member['pbd']['chord_length'] = spine_length(info['centres'])
            if result['pbd'] is None or info['patches'] > result['pbd']['patches']:
                result['pbd'] = dict(member['pbd'], member=entry['path'], decoded=len(raw))
        ext_counts[ext] += 1
        ext_bytes[ext] += len(raw)
        result['members'].append(member)
    result['by_extension'] = {e: dict(count=ext_counts[e], decoded=ext_bytes[e]) for e in sorted(ext_counts)}
    result['decoded_total'] = sum(ext_bytes.values())
    return result


def ssx3_locations(world):
    groups = {g['index']: g for g in world['groups']}
    rows = []
    for loc in world['locations']:
        members = [groups[i] for i in range(loc['group_start'], loc['last_group'] + 1)]
        rows.append(dict(name=loc['name'], groups=len(members),
                         decoded=sum(g['decoded_size'] for g in members),
                         stored=sum(g['stored_size'] for g in members),
                         patches=sum(g['kinds'].get('1', 0) for g in members)))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('iso', type=Path)
    ap.add_argument('--report', type=Path, required=True)
    ap.add_argument('--world', type=Path, help='SSX 3 world report for the location comparison')
    ap.add_argument('--only', nargs='*', help='Archive base names to inventory')
    args = ap.parse_args()
    if args.iso.resolve() == args.report.resolve():
        ap.error('Report cannot replace the ISO')
    report = dict(source=str(args.iso), archives=[], ssx3_locations=None)
    with args.iso.open('rb') as f:
        disc = Region(f, 0, args.iso.stat().st_size)
        _, files = iso_files(disc)
        for name in args.only or ARCHIVES:
            entry = inventory_archive(disc, files, name)
            report['archives'].append(entry)
            pbd = entry['pbd']
            summary = (f"{pbd['patches']} patches, drop {pbd['drop']:.0f}, slope {pbd['slope_area_weighted_deg']:.1f} deg"
                       if pbd else 'no pbd')
            print(f"{name:8} {entry['stored_size']:>9} stored {entry['decoded_total']:>9} decoded; {summary}", flush=True)
    if args.world:
        report['ssx3_locations'] = ssx3_locations(json.loads(args.world.read_text()))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(f'Wrote {args.report}')


if __name__ == '__main__':
    main()
