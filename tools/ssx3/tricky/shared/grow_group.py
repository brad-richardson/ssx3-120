#!/usr/bin/env python3
"""Grow one SSX 3 stream group by appending raised copies of some of its terrain patches.

This is the roadmap's M2 step 4: a group with more resources than the disc's, a
longer stream, regenerated SDB offsets, and updated SDB/location counts. Each copy
is the original patch shifted by --dz game units along Z (coefficients, sphere,
bounds, corners), with a fresh resource ID and the 336 handle set to rid<<8|track.
Everything else in the copy, including the texture-binding words, is left as in
its original so it renders with the same resources. Other groups keep EA's blocks.

Writes BAM.BIG and experiment.json; build the image with relocate_archive.py
(padding or --append) because the archive grows.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct

from inspect_disc import Region, big_members, file_region
from probe_worlds import refpack, resource_records, probe_ssx3
from build_world_experiment import serialize_resources
from relayout_stream import assemble_archive

KIND_PATCH = 1


def shifted_patch(payload, dz):
    out = bytearray(payload)
    # power-basis coefficients: only the constant term moves under translation
    # (reversed order on disk: the constant term is the last vector)
    x, y, z, w = struct.unpack_from('<4f', payload, 64 + 15 * 16)
    struct.pack_into('<4f', out, 64 + 15 * 16, x, y, z + dz, w)
    cx, cy, cz, r = struct.unpack_from('<4f', payload, 320)
    struct.pack_into('<4f', out, 320, cx, cy, cz + dz, r)
    for off in (344, 356):
        bx, by, bz = struct.unpack_from('<3f', payload, off)
        struct.pack_into('<3f', out, off, bx, by, bz + dz)
    for i in range(4):
        px, py, pz = struct.unpack_from('<3f', payload, 368 + 12 * i)
        struct.pack_into('<3f', out, 368 + 12 * i, px, py, pz + dz)
    return bytes(out)


def add_resources_to_sdb(sdb, report, group, existing_count, added):
    """Update the SDB group record (total count, memory size for kinds 0-12, per-kind counts)
    and the owning location record (per-kind counts) for resources appended to a group.
    added: list of (entry dict with kind/size, payload). Returns (old count, old memsize, owner)."""
    location_count, node_count, group_count = struct.unpack_from('<III', sdb, 8)
    table = (80 + location_count * 88 + 15) // 16 * 16 + node_count * 96
    rec = table + group * 68
    count, index = struct.unpack_from('<HH', sdb, rec)
    if index != group or count != existing_count:
        raise ValueError('SDB group record does not match the decoded group')
    struct.pack_into('<H', sdb, rec, count + len(added))
    memsize = struct.unpack_from('<I', sdb, rec + 8)[0]
    struct.pack_into('<I', sdb, rec + 8, memsize + sum(len(p) + 8 for e, p in added if e['kind'] <= 12))
    owners = [i for i, loc in enumerate(report['locations']) if group in loc['observed_groups']]
    if len(owners) != 1:
        raise ValueError('Expected one owning location')
    for kind in {e['kind'] for e, _ in added}:
        n = sum(1 for e, _ in added if e['kind'] == kind)
        if kind <= 13:
            slot = rec + 12 + 2 * kind
            struct.pack_into('<H', sdb, slot, struct.unpack_from('<H', sdb, slot)[0] + n)
        loc_slot = 80 + owners[0] * 88 + 32 + 2 * kind
        struct.pack_into('<H', sdb, loc_slot, struct.unpack_from('<H', sdb, loc_slot)[0] + n)
    return count, memsize, owners[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('archive', type=Path)
    ap.add_argument('--world-report', type=Path, default=Path('local/reports/ssx3-world.json'))
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--group', type=int, default=2)
    ap.add_argument('--track', type=int, default=1)
    ap.add_argument('--rids', required=True, help='comma-separated RIDs to copy, or "all"')
    ap.add_argument('--dz', type=float, default=60.0)
    ap.add_argument('--jobs', type=int, default=6)
    args = ap.parse_args()
    if args.output.exists():
        ap.error('Output directory already exists')
    original = args.archive.read_bytes()
    report = json.loads(args.world_report.read_text())
    if hashlib.sha256(original).hexdigest() != report['archive_sha256']:
        raise ValueError('Source archive differs from inspected baseline')
    region = Region(io.BytesIO(original), 0, len(original))
    _, members = big_members(region)
    ssb = file_region(region, members, 'data/worlds/bam.ssb')
    sdb_region = file_region(region, members, 'data/worlds/bam.sdb')
    sdb = bytearray(sdb_region.read(0, sdb_region.size))
    group_raw = {}
    for g in report['groups']:
        raw = b''.join(refpack(ssb.read(b['offset'], b['size'])[8:])[0] for b in g['blocks'])
        if hashlib.sha256(raw).hexdigest() != g['sha256']:
            raise ValueError(f'Group {g["index"]} differs from baseline')
        group_raw[g['index']] = raw
    records = list(resource_records(group_raw[args.group]))
    if serialize_resources(records) != group_raw[args.group]:
        raise ValueError('Group serialization does not round-trip')
    patches = [(e, p) for e, p in records if e['kind'] == KIND_PATCH and e['track'] == args.track]
    wanted = None if args.rids == 'all' else {int(x) for x in args.rids.split(',')}
    selected = [(e, p) for e, p in patches if wanted is None or e['rid'] in wanted]
    if wanted is not None and len(selected) != len(wanted):
        raise ValueError('Some requested RIDs were not found')
    next_rid = max(e['rid'] for e, _ in patches) + 1
    copies = []
    for e, p in selected:
        payload = bytearray(shifted_patch(p, args.dz))
        struct.pack_into('<I', payload, 336, (next_rid << 8) | args.track)
        copies.append((dict(kind=KIND_PATCH, size=len(payload), track=args.track, rid=next_rid), bytes(payload)))
        next_rid += 1
    new_raw = serialize_resources(records + copies)
    group_raw[args.group] = new_raw
    count, memsize, owner = add_resources_to_sdb(sdb, report, args.group, len(records), copies)
    archive, layout, stream_len = assemble_archive(original, report, group_raw, bytes(sdb), jobs=args.jobs, reuse_original_blocks=True)
    check = probe_ssx3(Region(io.BytesIO(archive), 0, len(archive)))  # verifies SDB counts against the stream
    expect = dict(report['resource_counts']); expect[str(KIND_PATCH)] = expect.get(str(KIND_PATCH), 0) + len(copies)
    if {str(k): v for k, v in check['resource_counts'].items()} != expect:
        raise ValueError('Rebuilt archive resource inventory differs from plan')
    details = dict(mode='grow', group=args.group, track=args.track, dz=args.dz, copied_rids=[e['rid'] for e, _ in selected],
                   new_rids=[e['rid'] for e, _ in copies], location=report['locations'][owner]['name'],
                   source_archive_sha256=hashlib.sha256(original).hexdigest(), rebuilt_archive_sha256=hashlib.sha256(archive).hexdigest(),
                   rebuilt_archive_bytes=len(archive), stream_bytes=stream_len, group_layout=[l for l in layout if l['index'] == args.group],
                   sdb_group_count=count + len(copies), sdb_group_memsize=memsize + sum(len(p) + 8 for _, p in copies), owner_location=report['locations'][owner]['name'],
                   max_patch_corner_error=check['max_patch_corner_error_game_units'], emulator_tested=False)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'BAM.BIG').write_bytes(archive)
    if hashlib.sha256((args.output / 'BAM.BIG').read_bytes()).hexdigest() != details['rebuilt_archive_sha256']:
        raise ValueError('Saved archive failed readback')
    (args.output / 'experiment.json').write_text(json.dumps(details, indent=2) + '\n')
    print(json.dumps(details, indent=2))


if __name__ == '__main__':
    main()
