#!/usr/bin/env python3
"""Inspect staged SSX world archives; export a Garibaldi terrain preview.

File-format reference: SSX-Library, pinned in docs/investigation.md.
All source files are opened read-only. This is an inspector, not a level builder.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import struct

from inspect_disc import Region, big_members, file_region


def refpack(data, max_output=64 * 1024 * 1024):
    """Decode the 10FB variant used by these assets, including overlapping matches.

Returns (decoded bytes, consumed compressed bytes). SSB block padding is excluded.
Unknown header variants are rejected instead of interpreted as this variant.
"""
    if len(data) < 5 or data[:2] != b'\x10\xfb':
        raise ValueError('Expected 10FB RefPack')
    expected = int.from_bytes(data[2:5], 'big')
    if expected > max_output:
        raise ValueError('RefPack output exceeds limit')
    pos, out = 5, bytearray()

    def take(n):
        nonlocal pos
        if pos + n > len(data):
            raise ValueError('Truncated RefPack')
        value = data[pos:pos+n]
        pos += n
        return value

    while True:
        cmd = take(1)[0]
        count = distance = 0
        if cmd < 0x80:
            b = take(1)[0]
            literal, count, distance = cmd & 3, ((cmd >> 2) & 7) + 3, ((cmd & 0x60) << 3) + b + 1
        elif cmd < 0xc0:
            b, c = take(2)
            literal, count, distance = b >> 6, (cmd & 0x3f) + 4, ((b & 0x3f) << 8) + c + 1
        elif cmd < 0xe0:
            b, c, d = take(3)
            literal, count, distance = cmd & 3, ((cmd & 12) << 6) + d + 5, ((cmd & 16) << 12) + (b << 8) + c + 1
        elif cmd < 0xfc:
            literal = ((cmd & 31) + 1) * 4
        else:
            literal = cmd & 3
        if len(out) + literal + count > expected:
            raise ValueError('RefPack output exceeds declared size')
        out.extend(take(literal))
        if count:
            if distance > len(out):
                raise ValueError('RefPack backreference precedes output')
            # Repeat the initial referenced span to handle overlapping matches.
            span = bytes(out[-distance:])[:count]
            out.extend((span * ((count + len(span) - 1) // len(span)))[:count])
        if cmd >= 0xfc:
            if len(out) != expected:
                raise ValueError('RefPack output size mismatch')
            return bytes(out), pos


def resource_records(data):
    pos = 0
    while pos < len(data):
        if len(data) - pos < 8:
            raise ValueError('Truncated SSB resource header')
        kind = data[pos]
        size = int.from_bytes(data[pos+1:pos+4], 'little')
        track = data[pos+4]
        rid = int.from_bytes(data[pos+5:pos+8], 'little')
        if pos + 8 + size > len(data):
            raise ValueError('SSB resource outside group')
        yield dict(kind=kind, size=size, track=track, rid=rid, offset=pos), data[pos+8:pos+8+size]
        pos += size + 8


def probe_ssx3(region):
    _, members = big_members(region)
    sdb = file_region(region, members, 'data/worlds/bam.sdb')
    sdb_data = sdb.read(0, sdb.size)
    location_count, tree_node_count, group_count = struct.unpack_from('<III', sdb_data, 8)
    locations = []
    for i in range(location_count):
        o = 80 + i * 88
        name = sdb_data[o:o+16].split(b'\0')[0].decode('ascii')
        # Unlike the reference parser's names: field 3 is the LAST stream group,
        # field 4 indexes the separate 96-byte spatial records. Verify below.
        nodes, ngroups, last_group, node_start = struct.unpack_from('<IIII', sdb_data, o+16)
        locations.append(dict(name=name, group_start=last_group-ngroups+1, groups=ngroups,
                              last_group=last_group, tree_node_start=node_start, tree_nodes=nodes))
    if sum(loc['groups'] for loc in locations) != group_count or sum(loc['tree_nodes'] for loc in locations) != tree_node_count:
        raise ValueError('SDB location totals do not match tables')
    table_start = (80 + location_count*88 + 15) // 16 * 16 + tree_node_count*96
    if table_start + group_count*68 != len(sdb_data):
        raise ValueError('Unexpected SDB table layout')
    ssb = file_region(region, members, 'data/worlds/bam.ssb')
    # Read sequentially in batches: thousands of small SMB reads are expensive.
    data = ssb.read(0, ssb.size)
    groups, blocks, decoded, group_start = [], [], bytearray(), 0
    pos = 0
    patch_sizes, kinds, padding_values = Counter(), Counter(), Counter()
    corner_error = 0.0
    corner_errors, worst_patch = Counter(), None
    for location in locations:
        location['observed_groups'] = []
    while pos < len(data):
        tag, size = struct.unpack_from('<4sI', data, pos)
        if tag not in (b'CBXS', b'CEND') or size < 13 or pos + size > len(data):
            raise ValueError(f'Unsupported SSB block at {pos}: {tag!r}, {size}')
        raw, consumed = refpack(data[pos+8:pos+size])
        decoded.extend(raw)
        padding = data[pos+8+consumed:pos+size]
        padding_values.update(padding)
        blocks.append(dict(offset=pos, size=size, decoded_size=len(raw), compressed_size=consumed, padding_size=len(padding)))
        pos += size
        if tag == b'CEND':
            counts, samples, local_sizes = Counter(), [], Counter()
            for entry, payload in resource_records(decoded):
                counts[entry['kind']] += 1
                if entry['kind'] == 1:
                    patch_sizes[entry['size']] += 1
                    local_sizes[entry['size']] += 1
                    if len(payload) != 432:
                        raise ValueError('Unexpected SSX 3 terrain patch size')
                    coefficients = [struct.unpack_from('<4f', payload, 64+j*16)[:3] for j in range(16)]
                    # Disc evidence: bounds at 344/356, FOUR corners at 368.
                    # WorldPatch.cs labels these six vectors in a different order.
                    stored_corners = [struct.unpack_from('<3f', payload, 368+j*12) for j in range(4)]
                    error = patch_corner_error(coefficients, stored_corners)
                    corner_errors['at_most_0.1' if error <= .1 else 'at_most_1' if error <= 1 else 'over_1'] += 1
                    if error > corner_error:
                        corner_error = error
                        worst_patch = dict(group=len(groups), **entry,
                                           stored_corners=stored_corners,
                                           computed_corners=[patch_point(coefficients, u, v) for u, v in [(0,0),(0,1),(1,0),(1,1)]],
                                           header_hex=payload[:16].hex())
                    if len(samples) < 2:
                        samples.append(entry)
            index = len(groups)
            owners = [loc for loc in locations if loc['group_start'] <= index <= loc['last_group']]
            if len(owners) != 1:
                raise ValueError(f'Expected one location for stream group {index}')
            sdb_resources = struct.unpack_from('<H', sdb_data, table_start + index*68)[0]
            if sdb_resources != sum(counts.values()):
                raise ValueError(f'SDB group {index}: expects {sdb_resources} resources, got {sum(counts.values())}')
            for owner in owners:
                owner['observed_groups'].append(index)
            groups.append(dict(index=index, locations=[loc['name'] for loc in owners],
                               offset=group_start, stored_size=pos-group_start,
                               decoded_size=len(decoded), sha256=hashlib.sha256(decoded).hexdigest(),
                               kinds=dict(counts), patch_sizes=dict(local_sizes), patch_samples=samples, blocks=blocks))
            kinds.update(counts)
            blocks, decoded, group_start = [], bytearray(), pos
    if decoded or blocks:
        raise ValueError('SSB ended without CEND')
    if len(groups) != group_count:
        raise ValueError(f'SDB expects {group_count} groups; SSB has {len(groups)}')
    return dict(locations=locations, groups=groups, resource_counts=dict(kinds),
                patch_sizes=dict(patch_sizes), padding_byte_counts=dict(padding_values),
                max_patch_corner_error_game_units=corner_error,
                patch_corner_error_counts=dict(corner_errors), worst_patch=worst_patch,
                sdb_counts=dict(locations=location_count, spatial_records=tree_node_count, stream_groups=group_count))


def patch_point(coefficients, u, v):
    """Evaluate the stored bicubic power coefficients (reversed on disk)."""
    c = list(reversed(coefficients))
    return tuple(sum(c[row*4+col][axis] * v**row * u**col for row in range(4) for col in range(4)) for axis in range(3))


def patch_corner_error(coefficients, stored):
    computed = [patch_point(coefficients, u, v) for u, v in [(0, 0), (0, 1), (1, 0), (1, 1)]]
    if not all(math.isfinite(x) for p in computed + stored for x in p):
        raise ValueError('Nonfinite patch corner')
    return max(max(min(math.dist(p, q) for q in stored) for p in computed),
               max(min(math.dist(p, q) for q in computed) for p in stored))


def export_obj(patches, destination, subdivisions=4):
    # Preview only: no textures, props, normals, welds, or validated game collision.
    with destination.open('x') as out:
        out.write('# Terrain preview in original game coordinates; not a playable level\n')
        base = 1
        for i, coefficients in enumerate(patches):
            out.write(f'o patch_{i}\n')
            for row in range(subdivisions+1):
                for col in range(subdivisions+1):
                    point = patch_point(coefficients, col/subdivisions, row/subdivisions)
                    if not all(math.isfinite(x) for x in point):
                        raise ValueError('Nonfinite terrain coordinates')
                    out.write('v ' + ' '.join(f'{x:.7g}' for x in point) + '\n')
            for row in range(subdivisions):
                for col in range(subdivisions):
                    a = base + row*(subdivisions+1) + col
                    b, c, d = a+1, a+subdivisions+2, a+subdivisions+1
                    out.write(f'f {a} {b} {c}\nf {a} {c} {d}\n')
            base += (subdivisions+1)**2


def probe_tricky(region, output=None):
    _, members = big_members(region)
    stored = file_region(region, members, 'data/models/gari.pbd')
    raw, consumed = refpack(stored.read(0, stored.size))
    if consumed != stored.size:
        raise ValueError('Unexpected trailing data in Garibaldi PBD')
    starts, count, instances = struct.unpack_from('<III', raw, 4)
    patch_offset, instance_offset = struct.unpack_from('<II', raw, 68)
    stride = 448
    if patch_offset + count * stride != instance_offset:
        raise ValueError('Unexpected Tricky patch layout')
    patches, surfaces, corner_error = [], Counter(), 0.0
    for i in range(count):
        offset = patch_offset + stride*i
        coefficients = [struct.unpack_from('<4f', raw, offset+80+j*16)[:3] for j in range(16)]
        patches.append(coefficients)
        corners = [struct.unpack_from('<4f', raw, offset+360+j*16)[:3] for j in range(4)]
        corner_error = max(corner_error, patch_corner_error(coefficients, corners))
        surfaces[struct.unpack_from('<I', raw, offset+424)[0]] += 1
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
        with (output / 'gari.pbd').open('xb') as f:
            f.write(raw)
        export_obj(patches, output / 'garibaldi-terrain.obj')
    return dict(pbd_bytes=len(raw), pbd_sha256=hashlib.sha256(raw).hexdigest(),
                player_starts=starts, patches=count, instances=instances,
                patch_offset=patch_offset, patch_stride=stride, surface_counts=dict(surfaces),
                max_patch_corner_error_game_units=corner_error,
                preview=str(output / 'garibaldi-terrain.obj') if output else None)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('game', choices=['ssx3', 'tricky'])
    ap.add_argument('archive', type=Path)
    ap.add_argument('--report', type=Path, required=True)
    ap.add_argument('--assets', type=Path, help='Optional Tricky preview output; files must not already exist')
    args = ap.parse_args()
    if args.archive.resolve() == args.report.resolve():
        ap.error('Report cannot replace archive')
    with args.archive.open('rb') as f:
        region = Region(f, 0, args.archive.stat().st_size)
        result = probe_ssx3(region) if args.game == 'ssx3' else probe_tricky(region, args.assets)
        result['archive_sha256'] = hashlib.sha256(region.read(0, region.size)).hexdigest()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(f'Wrote {args.report}', flush=True)
    print(json.dumps({k:v for k,v in result.items() if k not in ('groups', 'locations')}, indent=2))


if __name__ == '__main__':
    main()
