#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK6: re-track an added location's groups (add_location.py output): OLD -> NEW track.

    retrack.py IN.BIG STEM G0 G1 OLD NEW OUT.BIG [KEEP_KINDS]

KEEP_KINDS (comma list): kinds whose record header keeps OLD. Kind 14 (AIP) must: the
resource dispatcher (0x3AB434) passes the header track to sub_0012A340, which maps it to the
ELF location (func_22E0E0, slot table 0x442168) and loads the start table (0x4D33A0) only
for ELF locations < 22 (runs + hubs). Track 49 -> ELF 49 is skipped; track 8 -> ELF 0 loads.

Resources are keyed by `rid<<8 | track` (header bytes 4-7, parsed at 0x3AEBB8) and the
same handle is embedded in payloads. In groups G0..G1 only this rewrites:
- the header track byte of every record whose track is OLD (track 255 = global, kept);
- embedded handles whose low byte is OLD and whose rid is one of the location's rids, at
  the offsets mapped per kind (scan_handles.py on stock ARA1/ERA5 + the donor, REPORT §1):
    kind 1 +336; kind 2 +0 and the handle list from +44 up to 0xFFFFFFFF; kind 3 +120,
    +128; kind 4 +0; kind 5 +96, +100; kind 18 +0, +4, +32, +40;
    kind 8 and kind 16: every scan hit (kind 8's extra hits sit in the same slot as ERA5's
    own-track values; kind 16's header tables hold ~1.3k handles, control hits ~2).
- Not rewritten (listed in the receipt): kind 9/10 (track 255), kind 13 (the donor nulled its
  handles; hits are offset fields), kinds 14/15/17/20 (hits are small ints/offsets).
Changed groups are re-packed (relayout_stream.pack_group); stock groups keep their SSB
bytes and SDB records; only the added groups' SDB offsets change. Prints a JSON receipt.
"""
import hashlib, io, json, struct, sys
from collections import Counter
from multiprocessing import Pool
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] /'shared'))
from inspect_disc import Region, big_members, file_region
from location_inventory import parse_sdb
from probe_worlds import refpack, resource_records
from relayout_stream import pack_group, write_bigf

FIXED = {1: [336], 2: [0], 3: [120, 128], 4: [0], 5: [96, 100], 18: [0, 4, 32, 40]}
LIST_FROM = {2: 44}
SCAN_ALL = {8, 16}
sha = lambda b: hashlib.sha256(b).hexdigest()


def members_of(data):
    r = Region(io.BytesIO(data), 0, len(data)); _, ms = big_members(r)
    return [(m['path'], file_region(r, ms, m['path']).read(0, m['size'])) for m in ms]


def decode_group(ssb, off):
    out, pos = bytearray(), off
    while True:
        tag, size = struct.unpack_from('<4sI', ssb, pos)
        if tag not in (b'CBXS', b'CEND'): raise ValueError(f'bad block at {pos}')
        out += refpack(ssb[pos + 8:pos + size])[0]; pos += size
        if tag == b'CEND': return bytes(out), pos


def handle_hits(pl, old, rids):
    return [o for o in range(0, len(pl) - 3, 4)
            if (lambda v: v & 0xFF == old and (v >> 8) in rids)(struct.unpack_from('<I', pl, o)[0])]


def retrack_group(raw, old, new, rids, stats, keep=()):
    raw = bytearray(raw)
    for r, pl in resource_records(bytes(raw)):
        k, base = r['kind'], r['offset'] + 8
        if r['track'] == old and k not in keep:
            raw[r['offset'] + 4] = new; stats['headers'][k] += 1
        offs = set(FIXED.get(k, []))
        if k in LIST_FROM:
            o = LIST_FROM[k]
            while o + 4 <= len(pl) and struct.unpack_from('<I', pl, o)[0] != 0xFFFFFFFF and o < LIST_FROM[k] + 128:
                offs.add(o); o += 4
        if k in SCAN_ALL:
            offs |= set(handle_hits(pl, old, rids))
        for o in sorted(offs):
            if o + 4 > len(pl): continue
            v = struct.unpack_from('<I', pl, o)[0]
            if v != 0xFFFFFFFF and v & 0xFF == old and (v >> 8) in rids:
                raw[base + o] = new; stats['handles'][k] += 1
    return bytes(raw)


def main():
    in_p, stem, g0, g1, old, new, out_p = sys.argv[1], sys.argv[2], *map(int, sys.argv[3:7]), sys.argv[7]
    keep = {int(k) for k in sys.argv[8].split(',')} if len(sys.argv) > 8 else set()
    src = Path(in_p).read_bytes(); ms = members_of(src); md = dict(ms)
    sdb_p, ssb_p = f'data/worlds/{stem}.sdb', f'data/worlds/{stem}.ssb'
    sdb, ssb = md[sdb_p], md[ssb_p]; s = parse_sdb(sdb)
    n_loc, n_sp, n_gr = struct.unpack_from('<III', sdb, 8)
    if g1 != n_gr - 1: raise SystemExit('added groups must be the last groups')
    gtab = (80 + n_loc * 88 + 15) // 16 * 16 + n_sp * 96
    first = s['groups'][g0]['ssb_offset']
    raws, ends = {}, []
    for g in range(g0, g1 + 1):
        raws[g], end = decode_group(ssb, s['groups'][g]['ssb_offset']); ends.append(end)
    if ends[-1] != len(ssb): raise SystemExit('added groups do not end the SSB')
    rids = {r['rid'] for g in raws for r, _ in resource_records(raws[g]) if r['track'] == old}
    stats = dict(headers=Counter(), handles=Counter())
    new_raw = {g: retrack_group(raws[g], old, new, rids, stats, keep) for g in raws}
    with Pool(6) as pool:
        packed = {i: (d, n) for i, d, n in pool.imap_unordered(pack_group, [(g, new_raw[g], 81920, 96) for g in raws])}
    new_ssb, new_sdb = bytearray(ssb[:first]), bytearray(sdb)
    for g in range(g0, g1 + 1):
        struct.pack_into('<I', new_sdb, gtab + g * 68 + 4, len(new_ssb)); new_ssb += packed[g][0]
    arch, _ = write_bigf([(p, bytes(new_ssb) if p == ssb_p else bytes(new_sdb) if p == sdb_p else b) for p, b in ms], src)
    Path(out_p).write_bytes(arch)
    # verify from the written file
    vd = dict(members_of(Path(out_p).read_bytes())); vs = parse_sdb(vd[sdb_p]); vssb = vd[ssb_p]
    decoded_ok = all(decode_group(vssb, vs['groups'][g]['ssb_offset'])[0] == new_raw[g] for g in raws)
    left_hdr, left_scan, t_new = Counter(), Counter(), Counter()
    for g in raws:
        for r, pl in resource_records(new_raw[g]):
            if r['track'] == old and r['kind'] not in keep: left_hdr[r['kind']] += 1
            t_new[r['track']] += 1
            left_scan[r['kind']] += len(handle_hits(pl, old, rids))
    stock_rec = gtab + g0 * 68
    print(json.dumps(dict(
        input=in_p, input_sha256=sha(src), groups=[g0, g1], old_track=old, new_track=new, keep_header_kinds=sorted(keep), rids=len(rids),
        headers_rewritten=dict(stats['headers']), handles_rewritten=dict(stats['handles']),
        after=dict(record_tracks=dict(t_new), headers_still_old=dict(left_hdr),
                   scan_hits_still_old={k: v for k, v in left_scan.items() if v}),
        stock_ssb_prefix_identical=vssb[:first] == ssb[:first],
        stock_sdb_prefix_identical=vd[sdb_p][:stock_rec] == sdb[:stock_rec],
        added_group_records_identical_but_offset=all(vd[sdb_p][gtab + g * 68:gtab + g * 68 + 4] == sdb[gtab + g * 68:gtab + g * 68 + 4]
                                                     and vd[sdb_p][gtab + g * 68 + 8:gtab + g * 68 + 68] == sdb[gtab + g * 68 + 8:gtab + g * 68 + 68] for g in raws),
        other_members_identical=all(vd[p] == md[p] for p in md if p not in (sdb_p, ssb_p)),
        added_groups_decode_to_retracked=decoded_ok,
        blocks={g: [len(raws[g]), packed[g][1]] for g in raws},
        ssb_bytes=[len(ssb), len(new_ssb)], out=out_p, out_sha256=sha(arch), out_bytes=len(arch)), indent=1))


if __name__ == '__main__':
    main()
