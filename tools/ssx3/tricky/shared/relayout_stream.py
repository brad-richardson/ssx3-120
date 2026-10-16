#!/usr/bin/env python3
"""Rebuild an SSX 3 world archive with our own SSB block boundaries.

Every stream group is decoded, re-chunked into 32 KiB blocks at boundaries chosen by
this tool (not EA's), encoded with the optimal RefPack parser, and written back with
regenerated SDB group offsets and a rebuilt BIGF directory. Decoded content is
identical to the source; this is the control for a growable rebuild. The archive
size changes, so images must be built with relocate_archive.py.

Block policy: a block holds up to --max-decoded bytes (default 81,920, the largest
decoded block on the disc) and its compressed payload must fit 32,760 bytes; the
largest fitting prefix is found by probing with the fast greedy encoder minus a
margin, then encoded optimally and shrunk if it still overflows.
"""
import argparse
import hashlib
import io
import json
from multiprocessing import Pool
from pathlib import Path
import struct

from inspect_disc import Region, big_members, file_region
from probe_worlds import refpack, probe_ssx3
from refpack_encode import encode as greedy
from refpack_optimal import encode as optimal

BLOCK = 32768
CAPACITY = BLOCK - 8
ALIGN = 2048


def sha(data):
    return hashlib.sha256(data).hexdigest()


def largest_fitting(raw, start, max_decoded, margin):
    """Largest length whose greedy encoding fits CAPACITY - margin (binary search)."""
    lo, hi = 512, min(max_decoded, len(raw) - start)
    if len(greedy(raw[start:start + hi])) <= CAPACITY - margin:
        return hi
    while hi - lo > 256:
        mid = (lo + hi) // 2
        if len(greedy(raw[start:start + mid])) <= CAPACITY - margin:
            lo = mid
        else:
            hi = mid
    return lo


def pack_group(job):
    index, raw, max_decoded, margin = job
    blocks, start = [], 0
    while start < len(raw):
        length = largest_fitting(raw, start, max_decoded, margin)
        while True:
            chunk = raw[start:start + length]
            payload = optimal(chunk)
            if len(payload) <= CAPACITY:
                break
            length -= 512
            if length < 256:
                raise ValueError(f'Group {index}: cannot fit a block at {start}')
        if refpack(payload)[0] != chunk:
            raise ValueError(f'Group {index}: encoder round trip failed at {start}')
        start += length
        tag = b'CEND' if start >= len(raw) else b'CBXS'
        blocks.append(tag + struct.pack('<I', BLOCK) + payload + bytes(CAPACITY - len(payload)))
    return index, b''.join(blocks), len(blocks)


def bigf_entries(header):
    """Yield (position of the offset field, name) for each entry of a BIGF header."""
    count, end = struct.unpack_from('>II', header, 8)
    pos = 16
    for _ in range(count):
        zero = header.index(0, pos + 8)
        yield pos, header[pos + 8:zero].decode('ascii')
        pos = zero + 1


def write_bigf(members, template):
    """Rebuild a BIGF archive from (path, bytes) members, patching the template archive's
    header/table verbatim (names, trailer, and directory padding are copied) so that an
    unchanged member set reproduces the original byte for byte. Members are 2048-aligned;
    the last member is not padded, matching the original."""
    kind, entries = big_members(Region(io.BytesIO(template), 0, len(template)))
    if kind != 'BIGF' or [e['path'] for e in entries] != [p for p, _ in members]:
        raise ValueError('Template archive members do not match')
    first = min(e['offset'] for e in entries)
    header = bytearray(template[:first])
    offsets, cursor = [], first
    for path, data in members:
        offsets.append(cursor)
        cursor += (len(data) + ALIGN - 1) // ALIGN * ALIGN
    total = offsets[-1] + len(members[-1][1])
    struct.pack_into('<I', header, 4, total)
    for (pos, name), (path, data), offset in zip(bigf_entries(header), members, offsets):
        if name.replace('\\', '/') != path:
            raise ValueError('Template entry order differs')
        struct.pack_into('>II', header, pos, offset, len(data))
    out = bytearray(header)
    for (path, data), offset in zip(members, offsets):
        out += bytes(offset - len(out)) + data
    return bytes(out), offsets


def assemble_archive(original, report, group_raw, sdb_bytes, jobs=6, max_decoded=81920, margin=96, reuse_original_blocks=False):
    """Assemble a new archive from decoded group bytes.

    group_raw: {group index: decoded bytes}. With reuse_original_blocks, groups whose bytes
    equal the baseline keep EA's original blocks verbatim; only changed groups are re-chunked.
    sdb_bytes: SDB member bytes (already edited for any count changes); group offsets are
    regenerated here. Returns (archive bytes, per-group layout list, stream length).
    """
    region = Region(io.BytesIO(original), 0, len(original))
    _, members = big_members(region)
    ssb = file_region(region, members, 'data/worlds/bam.ssb')
    todo, packed = [], {}
    for g in report['groups']:
        raw = group_raw[g['index']]
        if reuse_original_blocks and hashlib.sha256(raw).hexdigest() == g['sha256']:
            data = b''.join(ssb.read(b['offset'], b['size']) for b in g['blocks'])
            packed[g['index']] = (data, len(g['blocks']))
        else:
            todo.append((g['index'], raw, max_decoded, margin))
    if todo:
        with Pool(min(jobs, len(todo))) as pool:
            for i, data, n in pool.imap_unordered(pack_group, todo, chunksize=1):
                packed[i] = (data, n)
    sdb_bytes = bytearray(sdb_bytes)
    location_count, node_count, group_count = struct.unpack_from('<III', sdb_bytes, 8)
    table = (80 + location_count * 88 + 15) // 16 * 16 + node_count * 96
    stream, layout = bytearray(), []
    for g in report['groups']:
        data, n = packed[g['index']]
        struct.pack_into('<I', sdb_bytes, table + g['index'] * 68 + 4, len(stream))
        layout.append(dict(index=g['index'], old_offset=g['offset'], new_offset=len(stream), old_blocks=len(g['blocks']), new_blocks=n,
                           decoded_size=len(group_raw[g['index']]), sha256=hashlib.sha256(group_raw[g['index']]).hexdigest()))
        stream += data
    new_members = []
    for m in members:
        if m['path'] == 'data/worlds/bam.ssb':
            new_members.append((m['path'], bytes(stream)))
        elif m['path'] == 'data/worlds/bam.sdb':
            new_members.append((m['path'], bytes(sdb_bytes)))
        else:
            new_members.append((m['path'], region.read(m['offset'], m['size'])))
    archive, _ = write_bigf(new_members, original)
    return archive, layout, len(stream)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('archive', type=Path)
    ap.add_argument('--world-report', type=Path, default=Path('local/reports/ssx3-world.json'))
    ap.add_argument('--output', type=Path, required=True, help='New directory; never overwrites')
    ap.add_argument('--max-decoded', type=int, default=81920)
    ap.add_argument('--margin', type=int, default=96, help='greedy-probe slack below capacity')
    ap.add_argument('--jobs', type=int, default=8)
    args = ap.parse_args()
    if args.output.exists():
        ap.error('Output directory already exists')
    original = args.archive.read_bytes()
    report = json.loads(args.world_report.read_text())
    if sha(original) != report['archive_sha256']:
        raise ValueError('Source archive differs from inspected baseline')
    region = Region(io.BytesIO(original), 0, len(original))
    kind, members = big_members(region)
    if kind != 'BIGF':
        raise ValueError('Expected BIGF')
    ssb = file_region(region, members, 'data/worlds/bam.ssb')
    sdb = file_region(region, members, 'data/worlds/bam.sdb')
    jobs = []
    for g in report['groups']:
        raw = b''.join(refpack(ssb.read(b['offset'], b['size'])[8:])[0] for b in g['blocks'])
        if sha(raw) != g['sha256']:
            raise ValueError(f'Group {g["index"]} differs from baseline')
        jobs.append((g['index'], raw, args.max_decoded, args.margin))
    with Pool(args.jobs) as pool:
        packed = dict((i, (data, n)) for i, data, n in pool.imap_unordered(pack_group, jobs, chunksize=1))
    # Assemble the stream in group order and regenerate SDB offsets.
    sdb_bytes = bytearray(sdb.read(0, sdb.size))
    location_count, node_count, group_count = struct.unpack_from('<III', sdb_bytes, 8)
    table = (80 + location_count * 88 + 15) // 16 * 16 + node_count * 96
    stream, groups_out = bytearray(), []
    for g in report['groups']:
        data, n = packed[g['index']]
        rec = table + g['index'] * 68
        old_offset = struct.unpack_from('<I', sdb_bytes, rec + 4)[0]
        if old_offset != g['offset']:
            raise ValueError(f'SDB offset for group {g["index"]} does not match the stream')
        struct.pack_into('<I', sdb_bytes, rec + 4, len(stream))
        groups_out.append(dict(index=g['index'], old_offset=g['offset'], new_offset=len(stream),
                               old_blocks=len(g['blocks']), new_blocks=n, decoded_size=g['decoded_size'], sha256=g['sha256']))
        stream += data
    new_members = []
    for m in members:
        if m['path'] == 'data/worlds/bam.ssb':
            new_members.append((m['path'], bytes(stream)))
        elif m['path'] == 'data/worlds/bam.sdb':
            new_members.append((m['path'], bytes(sdb_bytes)))
        else:
            new_members.append((m['path'], region.read(m['offset'], m['size'])))
    archive, offsets = write_bigf(new_members, original)
    # Independent verification with the world inspector: same groups, same SDB agreement.
    check = probe_ssx3(Region(io.BytesIO(archive), 0, len(archive)))
    if [g['sha256'] for g in check['groups']] != [g['sha256'] for g in report['groups']]:
        raise ValueError('Rebuilt archive decodes to different group content')
    def keyed(d):  # JSON reports carry string keys; the inspector returns ints
        return {str(k): v for k, v in d.items()}
    if keyed(check['resource_counts']) != keyed(report['resource_counts']) or keyed(check['patch_sizes']) != keyed(report['patch_sizes']):
        raise ValueError('Rebuilt archive resource inventory differs')
    details = dict(mode='relayout', source_archive_sha256=sha(original), rebuilt_archive_sha256=sha(archive),
                   source_archive_bytes=len(original), rebuilt_archive_bytes=len(archive),
                   source_stream_bytes=ssb.size, rebuilt_stream_bytes=len(stream),
                   source_blocks=sum(len(g['blocks']) for g in report['groups']), rebuilt_blocks=sum(v[1] for v in packed.values()),
                   max_decoded=args.max_decoded, margin=args.margin, member_offsets=dict(zip([m[0] for m in new_members], offsets)),
                   groups=groups_out, decoded_groups_unchanged=True, sdb_group_offsets_regenerated=True, emulator_tested=False)
    args.output.mkdir(parents=True, exist_ok=False)
    path = args.output / 'BAM.BIG'
    with path.open('xb') as f:
        f.write(archive)
    if sha(path.read_bytes()) != details['rebuilt_archive_sha256']:
        raise ValueError('Saved archive failed readback hash verification')
    (args.output / 'experiment.json').write_text(json.dumps(details, indent=2) + '\n')
    print(json.dumps({k: v for k, v in details.items() if k != 'groups'}, indent=2))


if __name__ == '__main__':
    main()
