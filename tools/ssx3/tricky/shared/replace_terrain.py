#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""Replace a location's terrain with a complete Tricky PBD terrain set.

Experimental geometry build: keeps SSX 3 materials and, by default, every
non-terrain resource. --drop-kind can remove old prop instances or collision.
The explicit transform is recorded, not guessed from course bounds. This does
not produce a finished race. Input hashes, resource counts, untouched groups,
and archive readback are verified. Use relocate_archive.py to build an ISO.
"""
import argparse
from collections import Counter
import hashlib
import io
import json
import math
from pathlib import Path
import struct

from inspect_disc import Region, big_members, file_region
from probe_worlds import refpack, resource_records, probe_ssx3, patch_point
from build_world_experiment import serialize_resources
from import_terrain import load_tricky, make_record, transform_coefficients
from relayout_stream import assemble_archive
from location_inventory import parse_sdb
from course_route import make_reset_aip


def placement(source, target, yaw, scale):
    if not all(math.isfinite(x) for x in [*source, *target, yaw, scale]) or scale <= 0:
        raise ValueError('Transform must be finite with positive scale')
    a = math.radians(yaw)
    c, s = scale * math.cos(a), scale * math.sin(a)
    matrix = [[c, -s, 0], [s, c, 0], [0, 0, scale]]
    translation = [target[k] - sum(matrix[k][j] * source[j] for j in range(3)) for k in range(3)]
    return matrix, translation


def replace_patches(records, template_rid, patches, matrix, translation):
    old = [(e, p) for e, p in records if e['kind'] == 1]
    if not old or len({e['track'] for e, _ in old}) != 1:
        raise ValueError('Expected terrain on exactly one track in the target group')
    templates = [(e, p) for e, p in old if e['rid'] == template_rid]
    if len(templates) != 1 or len(templates[0][1]) != 432:
        raise ValueError('Expected one 432-byte template patch')
    if not patches:
        raise ValueError('Replacement terrain is empty')
    entry, template = templates[0]
    rids = [e['rid'] for e, _ in old]
    if len(set(rids)) != len(rids):
        raise ValueError('Duplicate source patch IDs')
    rids += list(range(max(rids) + 1, max(rids) + 1 + max(0, len(patches) - len(rids))))
    added = []
    for rid, coeffs in zip(rids, patches):
        if rid >= 1 << 24:
            raise ValueError('Patch ID exceeds 24 bits')
        transformed = transform_coefficients(coeffs, matrix, translation)
        # Bounds and corners must describe the actual float32 coefficients on disc.
        transformed = [struct.unpack('<3f', struct.pack('<3f', *c)) for c in transformed]
        if not all(math.isfinite(x) for c in transformed for x in c):
            raise ValueError('Nonfinite terrain coefficients')
        payload = bytearray(make_record(template, transformed))
        struct.pack_into('<I', payload, 336, (rid << 8) | entry['track'])
        added.append((dict(kind=1, size=432, track=entry['track'], rid=rid), bytes(payload)))
    out, inserted = [], False
    for e, p in records:
        if e['kind'] == 1:
            if not inserted:
                out.extend(added)
                inserted = True
        else:
            out.append((e, p))
    return out, added


def update_sdb(sdb, group, before, after):
    parsed = parse_sdb(sdb)
    g = parsed['groups'][group]
    old_counts = Counter(e['kind'] for e, _ in before)
    new_counts = Counter(e['kind'] for e, _ in after)
    if g['count'] != len(before) or {k: v for k, v in g['kind_counts'].items() if k <= 12} != {k: v for k, v in old_counts.items() if k <= 12}:
        raise ValueError('SDB group counts do not match the input resources')
    mem = lambda records: sum(len(p) + 8 for e, p in records if e['kind'] <= 12)
    if mem(before) != g['memsize']:
        raise ValueError('SDB memory size does not match the input resources')
    owner = next(l for l in parsed['locations'] if l['name'] == g['location'])
    loc_count, nodes, _ = struct.unpack_from('<III', sdb, 8)
    base = (80 + 88 * loc_count + 15) // 16 * 16 + nodes * 96 + group * 68
    out = bytearray(sdb)
    if len(after) > 65535:
        raise ValueError('Group resource count exceeds u16')
    struct.pack_into('<H', out, base, len(after))
    struct.pack_into('<I', out, base + 8, mem(after))
    for kind in old_counts.keys() | new_counts.keys():
        delta = new_counts[kind] - old_counts[kind]
        if not delta:
            continue
        if kind <= 12:
            struct.pack_into('<H', out, base + 12 + 2 * kind, new_counts[kind])
        count = owner['kind_counts'].get(kind, 0) + delta
        if not 0 <= count <= 65535:
            raise ValueError('Location resource count exceeds u16')
        struct.pack_into('<H', out, 80 + owner['index'] * 88 + 32 + 2 * kind, count)
    return bytes(out), owner


def clear_removed_instance_references(records, removed):
    """Clear known instance-ID fields, without searching arbitrary binary words.

    Kind 13 has a 16-byte header and 24-byte object rows. The instance ID
    is row +12; SLUS-20772's loader at 0x2b6b50 explicitly accepts -1 here.
    Kind 18 is the NIS object-ID table, also using -1 for absent objects.
    Unknown script formats are left alone and must still be runtime-tested.
    """
    ids = {(e['rid'] << 8) | e['track'] for e, _ in removed if e['kind'] == 3}
    out, changes = [], []
    for e, payload in records:
        if not ids or e['kind'] not in (13, 18):
            out.append((e, payload))
            continue
        if e['kind'] == 13:
            if len(payload) < 16 or payload[0] != 0:
                raise ValueError('Unsupported kind-13 object table')
            count = struct.unpack_from('<I', payload, 12)[0]
            table_end = 16 + count * 24
            if table_end > len(payload):
                raise ValueError('Truncated kind-13 object table')
            for i in range(count):
                offset = struct.unpack_from('<I', payload, 16 + i * 24 + 16)[0]
                if offset < table_end or offset + 8 > len(payload):
                    raise ValueError('Invalid kind-13 object data offset')
            offsets = range(28, table_end, 24)
        else:
            if len(payload) != 72:
                raise ValueError('Unsupported kind-18 object table size')
            offsets = range(0, len(payload), 4)
        data = bytearray(payload)
        for offset in offsets:
            oid = struct.unpack_from('<I', data, offset)[0]
            if oid in ids:
                struct.pack_into('<I', data, offset, 0xffffffff)
                changes.append(dict(kind=e['kind'], track=e['track'], rid=e['rid'],
                                    offset=offset, instance_id=oid))
        out.append((e, bytes(data)))
    return out, changes


def clear_script_bindings(records, removed):
    """Disable kind-16 bindings for whole removed instance/spline tables.

    SLUS-20772's script loader indexes instances by ordinal using the count
    at +68 and splines using +84. Definition types 1 and 3 resolve collision
    IDs at definition +12; type 0 has no collision lookup. Offsets and all
    unrelated script bytes are retained. Partial ordinal-table removal is
    rejected because it would require rebuilding the tables.
    """
    out, edits = [], []
    for e, payload in records:
        if e['kind'] != 16:
            out.append((e, payload))
            continue
        if len(payload) < 92 or struct.unpack_from('<I', payload)[0] != 0x1000:
            raise ValueError('Unsupported kind-16 script header')
        data = bytearray(payload)
        for kind, count_offset, index_size in ((3, 68, 2), (8, 84, 4)):
            removed_ids = {r['rid'] for r, _ in removed if r['kind'] == kind and r['track'] == e['track']}
            if not removed_ids:
                continue
            count, offset = struct.unpack_from('<II', data, count_offset)
            if removed_ids != set(range(count)) or offset + count * index_size > len(data):
                raise ValueError('Script binding count does not match complete removed resource table')
            struct.pack_into('<I', data, count_offset, 0)
            edits.append(dict(kind=16, track=e['track'], rid=e['rid'], offset=count_offset,
                              action='disable_binding_table', resource_kind=kind, count=count))
        collision_ids = {(r['rid'] << 8) | r['track'] for r, _ in removed if r['kind'] == 12}
        base = struct.unpack_from('<I', data, 64)[0]
        count, offset = struct.unpack_from('<II', data, 76)
        if offset + count * 4 > len(data):
            raise ValueError('Truncated script definition table')
        seen = set()
        for i in range(count):
            start = base + struct.unpack_from('<I', data, offset + 4 * i)[0]
            if start + 16 > len(data):
                raise ValueError('Script definition is outside its resource')
            if start in seen:
                continue
            seen.add(start)
            kind, _, _, oid = struct.unpack_from('<4I', data, start)
            if kind in (1, 3) and oid in collision_ids:
                struct.pack_into('<I', data, start, 0)
                struct.pack_into('<I', data, start + 12, 0xffffffff)
                edits.append(dict(kind=16, track=e['track'], rid=e['rid'], offset=start,
                                  action='disable_collision_definition', definition_type=kind,
                                  collision_id=oid))
        out.append((e, bytes(data)))
    return out, edits


def pin_texture_group(sdb, location, texture_group, bounds):
    """Replace one location's texture-streaming tree with a single bounded leaf.

    The fallback texture/lightmap group stays resident across the imported
    course. Other locations retain their trees, with absolute child indices
    adjusted after the removed nodes. Compressed resource groups do not move
    until the normal archive assembly step.
    """
    parsed = parse_sdb(sdb)
    owner = next(l for l in parsed['locations'] if l['name'] == location)
    if not owner['group_start'] <= texture_group < owner['last_group']:
        raise ValueError('Texture group must belong to the target location')
    kinds = parsed['groups'][texture_group]['kind_counts']
    if not kinds or set(kinds) - {9, 10}:
        raise ValueError('Pinned group must contain only textures and lightmaps')
    start, count = owner['spatial_start'], owner['spatial_count']
    if count < 1:
        raise ValueError('Location has no spatial tree')
    end, delta = start + count, count - 1
    nloc, nodes, _ = struct.unpack_from('<III', sdb, 8)
    base = (80 + 88 * nloc + 15) // 16 * 16
    prefix = bytearray(sdb[:base])
    struct.pack_into('<I', prefix, 12, nodes - delta)
    for loc in parsed['locations']:
        offset = 80 + loc['index'] * 88
        if loc['name'] == location:
            struct.pack_into('<I', prefix, offset + 16, 1)
        elif loc['spatial_start'] >= end:
            struct.pack_into('<I', prefix, offset + 28, loc['spatial_start'] - delta)
        elif start <= loc['spatial_start'] < end:
            raise ValueError('Overlapping location spatial trees')
    old = parsed['spatial'][start]['floats']
    low = [min(old[k], bounds[0][k] - 1000) for k in range(3)]
    high = [max(old[k + 4], bounds[1][k] + 1000) for k in range(3)]
    leaf = struct.pack('<20f4i', *low, 1, *high, 1, *([0] * 12), -1, -1, texture_group, 0)
    chunks = []
    for i in range(nodes):
        if i == start:
            chunks.append(leaf)
        elif start < i < end:
            continue
        else:
            raw = bytearray(sdb[base + i * 96:base + (i + 1) * 96])
            for offset in (80, 84):
                child = struct.unpack_from('<i', raw, offset)[0]
                if start <= child < end:
                    raise ValueError('Another tree references the replaced location')
                if child >= end:
                    struct.pack_into('<i', raw, offset, child - delta)
            chunks.append(bytes(raw))
    result = bytes(prefix) + b''.join(chunks) + sdb[base + nodes * 96:]
    parse_sdb(result)
    return result, dict(group=texture_group, old_nodes=count, new_nodes=1, bounds=[low, high])


def disable_course_scripts(records):
    """Use the original empty LUN program in every course-script slot.

    This explicitly disables the destination's gameplay scripts for a freeride
    prototype. Index tables, collision definitions and bindings keep their
    positions. The baseline's first two programs must be identical empty
    returns; arbitrary bytecode or literal words are never searched/replaced.
    """
    out, changes = [], []
    for e, payload in records:
        if e['kind'] != 16:
            out.append((e, payload))
            continue
        if len(payload) < 92 or struct.unpack_from('<I', payload)[0] != 0x1000:
            raise ValueError('Unsupported kind-16 script header')
        count, table, end = struct.unpack_from('<3I', payload, 56)
        if count < 2 or table + 4 * count > len(payload) or end > len(payload):
            raise ValueError('Invalid course-script index table')
        starts = struct.unpack_from(f'<{count}I', payload, table)
        if list(starts) != sorted(set(starts)) or starts[0] < table + 4 * count:
            raise ValueError('Invalid course-script program offsets')
        spans = list(zip(starts, (*starts[1:], end)))
        for index, (start, stop) in enumerate(spans):
            if stop - start < 36:
                raise ValueError('Truncated LUN program')
            magic, code_end, data_end, length = struct.unpack_from('<4I', payload, start)
            # The last program may be followed by zero alignment padding up to
            # the table end (BRA2: 12 B, ABC1: 8 B; ARA1 has none).
            padded = (index == len(spans) - 1 and length < stop - start < length + 16 and
                      not any(payload[start + length:stop]))
            if magic != 0x4e554c or not 20 <= code_end <= data_end <= length or not (length == stop - start or padded):
                raise ValueError('Unsupported LUN program bounds')
        template = payload[spans[0][0]:spans[0][1]]
        if (len(template) != 36 or template != payload[spans[1][0]:spans[1][1]] or
                struct.unpack_from('<4I', template, 4) != (20, 36, 36, 0xff2a)):
            raise ValueError('Expected matching original empty-return LUN programs')
        data = bytearray(payload)
        for index, (start, stop) in enumerate(spans):
            replacement = template + bytes(stop - start - len(template))
            if data[start:stop] != replacement:
                data[start:stop] = replacement
                changes.append(dict(kind=16, track=e['track'], rid=e['rid'], program=index,
                                    offset=start, bytes=stop - start))
        out.append((e, bytes(data)))
    return out, changes


def vector(value):
    try:
        parts = [float(x) for x in value.split(',')]
        if len(parts) != 3 or not all(math.isfinite(x) for x in parts):
            raise ValueError()
        return parts
    except ValueError:
        raise argparse.ArgumentTypeError('Expected three finite comma-separated coordinates')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('archive', type=Path)
    ap.add_argument('--pbd', type=Path, required=True)
    ap.add_argument('--world-report', type=Path, default=Path('local/reports/ssx3-world.json'))
    ap.add_argument('--cache', type=Path, help='Optional decoded group cache; each file is hash checked')
    ap.add_argument('--location', default='ARA1')
    ap.add_argument('--template-rid', type=int, required=True)
    ap.add_argument('--source-anchor', type=vector, required=True)
    ap.add_argument('--target-anchor', type=vector, required=True)
    ap.add_argument('--yaw', type=float, required=True, help='Rotation about +Z in degrees')
    ap.add_argument('--scale', type=float, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--drop-kind', type=int, choices=(3, 8, 12), action='append', default=[],
                    help='Remove old instances (3), splines (8), or object collision (12)')
    ap.add_argument('--clear-instance-references', action='store_true',
                    help='Clear removed instance IDs from kind-13 and kind-18 tables')
    ap.add_argument('--clear-script-bindings', action='store_true',
                    help='Disable script bindings for removed instances, splines and collision')
    ap.add_argument('--disable-course-scripts', action='store_true',
                    help='Replace destination gameplay programs with its original empty return (freeride only)')
    ap.add_argument('--pin-texture-group', type=int,
                    help='Use one existing fallback texture/lightmap group across the whole location')
    ap.add_argument('--reset-aip', type=Path,
                    help='Replace old AI/reset paths with transformed donor AIP paths (freeride only)')
    ap.add_argument('--relocate-freeride-start', action='store_true',
                    help='Start on the donor opening, replacing the old approach paths in their indexed slots')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.output.exists():
        ap.error('Output directory already exists')
    if args.jobs < 1:
        ap.error('--jobs must be positive')
    if args.relocate_freeride_start and not args.reset_aip:
        ap.error('--relocate-freeride-start requires --reset-aip')
    report = json.loads(args.world_report.read_text())
    locations = [l for l in report['locations'] if l['name'] == args.location]
    if len(locations) != 1:
        ap.error('Unknown destination location')
    location = locations[0]
    groups = [g for g in report['groups'] if g['index'] in location['observed_groups'] and g['kinds'].get('1', 0)]
    if len(groups) != 1:
        ap.error('Expected one terrain-containing group in destination')
    group = groups[0]['index']
    matrix, translation = placement(args.source_anchor, args.target_anchor, args.yaw, args.scale)
    donor = load_tricky(args.pbd)
    original = args.archive.read_bytes()
    sha = lambda data: hashlib.sha256(data).hexdigest()
    if sha(original) != report['archive_sha256']:
        raise ValueError('Archive hash differs from baseline report')
    region = Region(io.BytesIO(original), 0, len(original))
    _, members = big_members(region)
    ssb = file_region(region, members, 'data/worlds/bam.ssb')
    sdb = file_region(region, members, 'data/worlds/bam.sdb')
    def decoded(g):
        cached = args.cache / f'group_{g["index"]:03d}.bin' if args.cache else None
        raw = cached.read_bytes() if cached and cached.exists() else b''.join(
            refpack(ssb.read(b['offset'], b['size'])[8:])[0] for b in g['blocks'])
        if sha(raw) != g['sha256']:
            raise ValueError(f'Group {g["index"]} hash differs from baseline')
        return raw
    before = list(resource_records(decoded(groups[0])))
    after, added = replace_patches(before, args.template_rid, donor, matrix, translation)
    dropped = [(e, p) for e, p in after if e['kind'] in args.drop_kind]
    after = [(e, p) for e, p in after if e['kind'] not in args.drop_kind]
    reference_changes = []
    if args.clear_instance_references:
        if 3 not in args.drop_kind:
            ap.error('--clear-instance-references requires --drop-kind 3')
        after, reference_changes = clear_removed_instance_references(after, dropped)
    script_changes = []
    if args.clear_script_bindings:
        after, script_changes = clear_script_bindings(after, dropped)
    program_changes = []
    if args.disable_course_scripts:
        after, program_changes = disable_course_scripts(after)
    reset_paths = None
    if args.reset_aip:
        candidates = [(e, p) for e, p in after if e['kind'] == 14 and p]
        if len(candidates) != 1 or candidates[0][0]['rid'] != 0:
            raise ValueError('Expected one populated kind-14 path resource with ID zero')
        reset_data, reset_paths = make_reset_aip(args.reset_aip.read_bytes(), matrix, translation, args.scale,
                                               candidates[0][1], relocate_start=args.relocate_freeride_start)
        after = [(dict(e, size=len(reset_data)), reset_data) if e['kind'] == 14 and e['rid'] == 0
                 else (e, p) for e, p in after]
    sdb_bytes, owner = update_sdb(sdb.read(0, sdb.size), group, before, after)
    bounds = ([min(struct.unpack_from('<3f', p, 344)[k] for _, p in added) for k in range(3)],
              [max(struct.unpack_from('<3f', p, 356)[k] for _, p in added) for k in range(3)])
    spatial = parse_sdb(sdb_bytes)['spatial'][owner['spatial_start']]['floats']
    outside = sum(any(struct.unpack_from('<3f', p, 344)[k] < spatial[k] or
                      struct.unpack_from('<3f', p, 356)[k] > spatial[4 + k] for k in range(3)) for _, p in added)
    texture_streaming = None
    if args.pin_texture_group is not None:
        required = {(9, struct.unpack_from('<H', p, 416)[0]) for _, p in added}
        required |= {(10, struct.unpack_from('<H', p, 418)[0]) for _, p in added}
        available = {(e['kind'], e['rid']) for e, _ in resource_records(decoded(report['groups'][args.pin_texture_group]))}
        if not required <= available:
            raise ValueError('Pinned group does not contain every imported texture/lightmap ID')
        sdb_bytes, texture_streaming = pin_texture_group(sdb_bytes, args.location, args.pin_texture_group, bounds)
    details = dict(mode='replace-terrain', location=args.location, group=group, track=added[0][0]['track'],
                   old_patch_count=sum(e['kind'] == 1 for e, _ in before), patch_count=len(added),
                   template_rid=args.template_rid, source_anchor=args.source_anchor, target_anchor=args.target_anchor,
                   yaw_degrees=args.yaw, scale=args.scale, matrix=matrix, translation=translation,
                   bounds=bounds, patches_outside_original_location_bounds=outside,
                   source_archive_sha256=sha(original), donor_pbd_sha256=sha(args.pbd.read_bytes()),
                   nonterrain_resources_unchanged=not (dropped or reference_changes or script_changes or program_changes or reset_paths),
                   removed_resource_counts=dict(Counter(e['kind'] for e, _ in dropped)),
                   cleared_instance_references=dict(Counter(c['kind'] for c in reference_changes)),
                   script_cleanup=script_changes,
                   disabled_course_programs=len(program_changes),
                   spatial_records_unchanged=texture_streaming is None,
                   texture_streaming=texture_streaming, emulator_tested=False,
                   reset_paths=reset_paths,
                   limitations=['Destination gameplay scripts disabled for freeride' if args.disable_course_scripts else 'Original event rules retained; object dependency cleanup is experimental',
                                'Transformed donor reset paths; race setup is incomplete' if reset_paths else 'Original AI/reset paths retained',
                                'One fallback texture group pinned' if texture_streaming else 'Original spatial texture-streaming tree retained',
                                'SSX 3 template textures and lightmap; no Tricky art import',
                                'Uniform scale is an experimental placement choice'])
    print(json.dumps(details, indent=2), flush=True)
    if args.dry_run:
        return
    raws = {g['index']: decoded(g) for g in report['groups']}
    raws[group] = serialize_resources(after)
    archive, layout, _ = assemble_archive(original, report, raws, sdb_bytes, jobs=args.jobs, reuse_original_blocks=True)
    check = probe_ssx3(Region(io.BytesIO(archive), 0, len(archive)))
    check['archive_sha256'] = sha(archive)
    if any(g['sha256'] != sha(raws[g['index']]) for g in check['groups']):
        raise ValueError('Decoded output group differs from planned bytes')
    expected = {int(k): v for k, v in report['resource_counts'].items()}
    expected[1] += len(added) - details['old_patch_count']
    for kind, count in Counter(e['kind'] for e, _ in dropped).items():
        expected[kind] -= count
        if not expected[kind]:
            del expected[kind]
    if check['resource_counts'] != expected:
        raise ValueError('Output resource inventory differs from plan')
    details.update(rebuilt_archive_sha256=sha(archive), rebuilt_archive_bytes=len(archive),
                   group_layout=layout[group], max_patch_corner_error=check['max_patch_corner_error_game_units'])
    args.output.mkdir(parents=True, exist_ok=False)
    path = args.output / 'BAM.BIG'
    path.write_bytes(archive)
    if sha(path.read_bytes()) != sha(archive):
        raise ValueError('Archive readback hash failed')
    details['full_readback_verified'] = True
    (args.output / 'experiment.json').write_text(json.dumps(details, indent=2) + '\n')
    (args.output / 'world-report.json').write_text(json.dumps(check, indent=2) + '\n')
    (args.output / 'terrain.bin').write_bytes(serialize_resources(added))
    if reference_changes:
        (args.output / 'reference-edits.json').write_text(json.dumps(reference_changes, indent=2) + '\n')
    if program_changes:
        (args.output / 'disabled-programs.json').write_text(json.dumps(program_changes, indent=2) + '\n')
    print(json.dumps({k: details[k] for k in ('rebuilt_archive_sha256', 'rebuilt_archive_bytes', 'group_layout')}, indent=2))


if __name__ == '__main__':
    main()
