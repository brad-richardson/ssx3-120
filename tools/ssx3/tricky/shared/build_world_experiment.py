#!/usr/bin/env python3
"""Build a bounded SSX 3 control or terrain-bump archive for emulator testing.

Only recompresses blocks containing the selected patch. It preserves every archive
offset, block size, resource ID, unknown field, and unrelated byte. Source is read-only.
"""
import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import struct

from inspect_disc import Region, big_members, file_region
from probe_worlds import refpack, resource_records, patch_point, export_obj
from refpack_encode import encode


def sha(data):
    return hashlib.sha256(data).hexdigest()


def serialize_resources(records):
    out = bytearray()
    for entry, payload in records:
        if len(payload) != entry['size']:
            raise ValueError('Resource payload changed size')
        out.extend(bytes([entry['kind']]) + len(payload).to_bytes(3, 'little')
                   + bytes([entry['track']]) + entry['rid'].to_bytes(3, 'little'))
        out.extend(payload)
    return bytes(out)


def coefficients(payload):
    if len(payload) != 432:
        raise ValueError('Expected a 432-byte SSX 3 terrain patch')
    return [struct.unpack_from('<4f', payload, 64+i*16)[:3] for i in range(16)]


def control_points(coeffs):
    """Power -> Bernstein basis. Its control hull bounds the entire surface."""
    c = list(reversed(coeffs))
    return [tuple(sum(c[r*4+k][axis] * math.comb(i,r)/math.comb(3,r)
                      * math.comb(j,k)/math.comb(3,k)
                      for r in range(i+1) for k in range(j+1))
                  for axis in range(3)) for i in range(4) for j in range(4)]


def bump_patch(payload, height, axis=2):
    if not math.isfinite(height) or height == 0 or axis not in (0, 1, 2):
        raise ValueError('Expected a finite nonzero height and a valid axis')
    original = coefficients(payload)
    edited = bytearray(payload)
    changes = []
    # h * 16u(1-u)v(1-v): zero on every edge, maximum h at the centre.
    for row, col, sign in [(1,1,1), (1,2,-1), (2,1,-1), (2,2,1)]:
        offset = 64 + (15-(row*4+col))*16 + axis*4
        old = struct.unpack_from('<f', payload, offset)[0]
        struct.pack_into('<f', edited, offset, old + sign*16*height)
        new = struct.unpack_from('<f', edited, offset)[0]
        changes.append(dict(offset=offset, before=old, after=new))
    new_coeffs = coefficients(edited)
    old_controls, new_controls = control_points(original), control_points(new_coeffs)
    bounds = struct.unpack_from('<6f', payload, 344)
    sphere = struct.unpack_from('<4f', payload, 320)
    if not all(math.isfinite(x) for p in new_controls for x in p) or sphere[3] <= 0:
        raise ValueError('Invalid geometry or bounding sphere')
    # Do not enlarge any existing spatial bounds. Allow only the original
    # float32 rounding discrepancy (plus 0.02 game units) for unchanged controls.
    tolerance = .02
    for old, new in zip(old_controls, new_controls):
        for a in range(3):
            if new[a] < min(bounds[a], old[a])-tolerance or new[a] > max(bounds[a+3], old[a])+tolerance:
                raise ValueError('Bump escapes existing bounding box; choose a smaller height or another patch')
        if math.dist(new, sphere[:3]) > max(sphere[3], math.dist(old, sphere[:3])) + tolerance:
            raise ValueError('Bump escapes existing bounding sphere')
    edge_error = 0.0
    for i in range(33):
        t = i/32
        for u, v in [(0,t), (1,t), (t,0), (t,1)]:
            edge_error = max(edge_error, math.dist(patch_point(original,u,v), patch_point(new_coeffs,u,v)))
    if edge_error > tolerance:
        raise ValueError('Float32 encoding moved a patch edge')
    centre_before, centre_after = patch_point(original,.5,.5), patch_point(new_coeffs,.5,.5)
    if abs(centre_after[axis]-centre_before[axis]-height) > tolerance:
        raise ValueError('Float32 encoding failed to preserve requested displacement')
    return bytes(edited), dict(axis='xyz'[axis], height_game_units=height, float_changes=changes,
                              centre_before=centre_before, centre_after=centre_after,
                              max_edge_displacement=edge_error, existing_bounds_retained=True,
                              bounds_validation_tolerance_game_units=tolerance)


def uv_tile_patch(payload, factor):
    """Scale the four corner texture coordinates (payload 32..63) about their minimum."""
    if not math.isfinite(factor) or factor <= 0 or factor == 1:
        raise ValueError('Expected a positive tiling factor other than 1')
    if len(payload) != 432:
        raise ValueError('Expected a 432-byte SSX 3 terrain patch')
    uvs = [struct.unpack_from('<2f', payload, 32 + 8 * i) for i in range(4)]
    u0, v0 = min(u for u, _ in uvs), min(v for _, v in uvs)
    edited = bytearray(payload)
    changes = []
    for i, (u, v) in enumerate(uvs):
        nu, nv = u0 + (u - u0) * factor, v0 + (v - v0) * factor
        struct.pack_into('<2f', edited, 32 + 8 * i, nu, nv)
        changes.append(dict(offset=32 + 8 * i, before=[u, v], after=[nu, nv]))
    if edited[:32] != payload[:32] or edited[64:] != payload[64:]:
        raise ValueError('UV edit touched bytes outside 32..63')
    return bytes(edited), dict(kind='uv_tile', factor=factor, float_changes=changes)


def set_words_patch(payload, assignments):
    """Replace u32 words at given payload offsets (outside the 64..320 coefficient array)."""
    if len(payload) != 432:
        raise ValueError('Expected a 432-byte SSX 3 terrain patch')
    edited = bytearray(payload)
    changes = []
    for offset, value in assignments:
        if offset % 4 or offset < 0 or offset + 4 > 432 or 64 <= offset < 320:
            raise ValueError('Word edits must be 4-byte aligned and outside the coefficient array')
        old = struct.unpack_from('<I', payload, offset)[0]
        struct.pack_into('<I', edited, offset, value)
        changes.append(dict(offset=offset, before=old, after=value, before_hex=hex(old), after_hex=hex(value)))
    return bytes(edited), dict(kind='set_words', changes=changes)


HEADER_RANGES = ((0, 64), (336, 344), (416, 432))


def transplant_header_patch(payload, donor):
    """Copy the non-geometry header, id words, and tail from a donor patch payload."""
    if len(payload) != 432 or len(donor) != 432:
        raise ValueError('Expected 432-byte SSX 3 terrain patches')
    edited = bytearray(payload)
    for start, end in HEADER_RANGES:
        edited[start:end] = donor[start:end]
    if edited[64:336] != payload[64:336] or edited[344:416] != payload[344:416]:
        raise ValueError('Transplant touched geometry bytes')
    changes = [dict(offset=o, before=struct.unpack_from('<I', payload, o)[0], after=struct.unpack_from('<I', donor, o)[0])
               for start, end in HEADER_RANGES for o in range(start, end, 4)
               if payload[o:o + 4] != donor[o:o + 4]]
    return bytes(edited), dict(kind='transplant_header', ranges=HEADER_RANGES, changes=changes)


def load_patch(archive_bytes, world_report, group_index, track, rid):
    archive = Region(io.BytesIO(archive_bytes), 0, len(archive_bytes))
    _, members = big_members(archive)
    ssb = file_region(archive, members, 'data/worlds/bam.ssb')
    group = world_report['groups'][group_index]
    raw = b''.join(refpack(ssb.read(b['offset'], b['size'])[8:])[0] for b in group['blocks'])
    found = [p for e, p in resource_records(raw) if e['kind'] == 1 and e['track'] == track and e['rid'] == rid]
    if len(found) != 1:
        raise ValueError('Expected exactly one donor patch')
    return found[0]


def patch_name(archive, members, track, rid):
    phm = file_region(archive, members, 'data/worlds/bam.phm')
    psm = file_region(archive, members, 'data/worlds/bam.psm')
    ph, ps = phm.read(0, phm.size), psm.read(0, psm.size)
    count = struct.unpack_from('<I', ph, 16)[0]
    if count != struct.unpack_from('<I', ps, 16)[0]:
        raise ValueError('PHM/PSM terrain name counts differ')
    names = ps[20:].split(b'\0', count)[:count]
    for i in range(count):
        pos = 20+16*i
        if ph[pos+8] == track and int.from_bytes(ph[pos+9:pos+12], 'little') == rid:
            return names[i].decode('ascii')
    raise ValueError('Patch name not found')


def rebuild(original, world_report, group_index, track, rid, height=None, uv_tile=None, set_words=None, donor=None):
    if sha(original) != world_report['archive_sha256']:
        raise ValueError('Source archive differs from inspected baseline')
    archive = Region(io.BytesIO(original), 0, len(original))
    kind, members = big_members(archive)
    if kind != 'BIGF':
        raise ValueError('Expected SSX 3 BIGF archive')
    ssb = file_region(archive, members, 'data/worlds/bam.ssb')
    group = world_report['groups'][group_index]
    raw_blocks, raw = [], bytearray()
    for info in group['blocks']:
        packed = ssb.read(info['offset'], info['size'])
        data, consumed = refpack(packed[8:])
        if len(data) != info['decoded_size'] or consumed != info['compressed_size']:
            raise ValueError('Block does not match inventory')
        raw_blocks.append((len(raw), packed, data, info))
        raw.extend(data)
    if sha(raw) != group['sha256']:
        raise ValueError('Group does not match inspected baseline')
    records = list(resource_records(raw))
    if serialize_resources(records) != raw:
        raise ValueError('Unchanged resource serialization did not round-trip')
    selected = [(i,e,p) for i,(e,p) in enumerate(records) if e['kind']==1 and e['track']==track and e['rid']==rid]
    if len(selected) != 1:
        raise ValueError('Expected exactly one selected terrain resource')
    index, entry, old_payload = selected[0]
    if sum(x is not None for x in (height, uv_tile, set_words, donor)) > 1:
        raise ValueError('Choose one edit per build')
    if donor is not None:
        new_payload, geometry = transplant_header_patch(old_payload, donor)
    elif height is not None:
        new_payload, geometry = bump_patch(old_payload, height)
    elif uv_tile is not None:
        new_payload, geometry = uv_tile_patch(old_payload, uv_tile)
    elif set_words is not None:
        new_payload, geometry = set_words_patch(old_payload, set_words)
    else:
        new_payload, geometry = old_payload, None
    records[index] = (entry, new_payload)
    changed_raw = serialize_resources(records)
    start, end = entry['offset']+8, entry['offset']+8+entry['size']
    if changed_raw[:start] != raw[:start] or changed_raw[end:] != raw[end:]:
        raise ValueError('Unexpected changes outside selected patch')
    rebuilt = bytearray(original)
    block_changes, rebuilt_group = [], bytearray()
    for raw_start, packed, block_raw, info in raw_blocks:
        raw_end = raw_start + len(block_raw)
        new_block_raw = changed_raw[raw_start:raw_end]
        if start < raw_end and end > raw_start:
            compressed = encode(new_block_raw)
            if len(compressed) > len(packed)-8:
                raise ValueError(f'Recompressed block at {info["offset"]} exceeds capacity by {len(compressed)-(len(packed)-8)} bytes')
            new_block = packed[:8] + compressed + bytes(len(packed)-8-len(compressed))
            decoded, consumed = refpack(new_block[8:])
            if decoded != new_block_raw or consumed != len(compressed):
                raise ValueError('Recompressed block failed independent decoder check')
            absolute = ssb.base + info['offset']
            rebuilt[absolute:absolute+len(packed)] = new_block
            block_changes.append(dict(archive_offset=absolute, ssb_offset=info['offset'],
                                      size=len(packed), raw_offset=raw_start, decoded_size=len(decoded),
                                      original_compressed_size=info['compressed_size'],
                                      rebuilt_compressed_size=len(compressed),
                                      original_sha256=sha(packed), rebuilt_sha256=sha(new_block)))
        else:
            new_block = packed
        rebuilt_group.extend(refpack(new_block[8:])[0])
    if rebuilt_group != changed_raw:
        raise ValueError('Rebuilt group differs from planned data')
    cursor = 0
    for block in block_changes:
        offset = block['archive_offset']
        if rebuilt[cursor:offset] != original[cursor:offset]:
            raise ValueError('Unrelated archive bytes changed')
        cursor = offset+block['size']
    if rebuilt[cursor:] != original[cursor:]:
        raise ValueError('Archive suffix changed')
    details = dict(mode='bump' if height is not None else 'uv' if uv_tile is not None else 'words' if set_words is not None else 'header' if donor is not None else 'control', group=group_index,
                   locations=group['locations'], track=track, rid=rid,
                   patch_name=patch_name(archive,members,track,rid), geometry=geometry,
                   source_archive_sha256=sha(original), rebuilt_archive_sha256=sha(rebuilt),
                   source_group_sha256=sha(raw), rebuilt_group_sha256=sha(rebuilt_group),
                   archive_bytes=len(rebuilt), group_decoded_bytes=len(raw),
                   changed_blocks=block_changes, unrelated_archive_bytes_unchanged=True,
                   resource_headers_unchanged=True, emulator_tested=False)
    return bytes(rebuilt), details, old_payload, new_payload


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('archive', type=Path)
    ap.add_argument('--world-report', type=Path, default=Path('local/reports/ssx3-world.json'))
    ap.add_argument('--output', type=Path, required=True, help='New output directory, never overwrites a build')
    ap.add_argument('--group', type=int, default=2)
    ap.add_argument('--track', type=int, default=1)
    ap.add_argument('--rid', type=int, default=76)
    ap.add_argument('--height', type=float, help='Omit for control; otherwise centre displacement in +Z game units')
    ap.add_argument('--uv-tile', type=float, help='Scale the corner texture coordinates by this factor instead of bumping')
    ap.add_argument('--set', action='append', metavar='OFFSET=HEX', help='Set a u32 payload word (repeatable), e.g. --set 8=0x9000a')
    ap.add_argument('--copy-header-from', metavar='GROUP:TRACK:RID', help='Transplant header/id/tail words from this donor patch')
    args = ap.parse_args()
    if args.output.exists():
        ap.error('Output directory already exists; choose a new build directory')
    set_words = [(int(o, 0), int(v, 0)) for o, v in (item.split('=') for item in args.set)] if args.set else None
    archive_bytes, report = args.archive.read_bytes(), json.loads(args.world_report.read_text())
    donor = None
    if args.copy_header_from:
        g, t, rid = (int(x) for x in args.copy_header_from.split(':'))
        donor = load_patch(archive_bytes, report, g, t, rid)
    data, details, before, after = rebuild(archive_bytes, report, args.group,args.track,args.rid,args.height,args.uv_tile,set_words,donor)
    if donor is not None:
        details['donor'] = args.copy_header_from
    # Only create outputs after in-memory checks have passed.
    args.output.mkdir(parents=True, exist_ok=False)
    archive_path = args.output / 'BAM.BIG'
    with archive_path.open('xb') as f:
        f.write(data)
    if sha(archive_path.read_bytes()) != details['rebuilt_archive_sha256']:
        raise ValueError('Saved archive failed readback hash verification')
    export_obj([coefficients(before)], args.output/'patch-before.obj', subdivisions=16)
    export_obj([coefficients(after)], args.output/'patch-after.obj', subdivisions=16)
    (args.output/'experiment.json').write_text(json.dumps(details,indent=2)+'\n')
    print(json.dumps(details,indent=2))


if __name__ == '__main__':
    main()
