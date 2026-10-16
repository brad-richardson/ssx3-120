#!/usr/bin/env python3
"""Inventory what an SSX 3 location holds besides terrain (read-only).

Parses the SDB index (locations, 96-byte spatial records, 68-byte group records),
decodes the SSB stream groups (cached as plain files under --cache so a second run
does not re-read the archive), and the PHM/PSM name tables. Prints per-location,
per-group and per-kind tables and dumps spatial records both as floats and ints.

Kind names follow SSX-Library's SSBHandler comment (docs/investigation.md pins the
commit); they are labels, not verified semantics.

    python3 tools/location_inventory.py ARCHIVE --cache DIR inventory ERA5 A A_ARA1
    python3 tools/location_inventory.py ARCHIVE --cache DIR spatial A
    python3 tools/location_inventory.py ARCHIVE --cache DIR kinds [--location ERA5]
    python3 tools/location_inventory.py ARCHIVE --cache DIR names [--location ERA5]
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_disc import Region, big_members, file_region  # noqa: E402
from probe_worlds import refpack, resource_records  # noqa: E402

KIND_NAMES = {0: 'material', 1: 'patch', 2: 'model(MDR)', 3: 'instance', 4: 'particle model',
              5: 'particle instance', 6: 'light', 7: 'halo', 8: 'spline', 9: 'texture(SSH)',
              10: 'lightmap(SSH)', 11: 'vis curtain', 12: 'collision', 13: 'sound trigger?',
              14: 'AI paths(AIP)', 15: 'world painter?', 16: 'scripts?', 17: 'camera trigger?',
              18: 'NIS table', 19: 'missions?', 20: 'audio bank', 21: 'radar?', 22: 'avalanche anim',
              23: 'kind 23'}
# PHM/PSM array index -> resource kind (SSX-Library GetName call sites)
NAME_ARRAY_KIND = {0: 1, 1: 3, 2: 2, 3: 8, 4: 12}
LOCATION_RECORD, SPATIAL_RECORD, GROUP_RECORD = 88, 96, 68


def parse_sdb(sdb):
    n_loc, n_spatial, n_group = struct.unpack_from('<III', sdb, 8)
    locations = []
    for i in range(n_loc):
        o = 80 + i * LOCATION_RECORD
        name = sdb[o:o + 16].split(b'\0')[0].decode('ascii')
        spatial_count, group_count, last_group, spatial_start = struct.unpack_from('<IIII', sdb, o + 16)
        kinds = struct.unpack_from('<24H', sdb, o + 32)
        tail = struct.unpack_from('<4H', sdb, o + 80)
        locations.append(dict(index=i, name=name, spatial_start=spatial_start, spatial_count=spatial_count,
                              group_start=last_group - group_count + 1, group_count=group_count,
                              last_group=last_group, kind_counts={k: c for k, c in enumerate(kinds) if c},
                              tail_words=list(tail)))
    spatial_base = (80 + n_loc * LOCATION_RECORD + 15) // 16 * 16
    spatial = []
    for i in range(n_spatial):
        o = spatial_base + i * SPATIAL_RECORD
        raw = sdb[o:o + SPATIAL_RECORD]
        spatial.append(dict(index=i, floats=list(struct.unpack('<24f', raw)), ints=list(struct.unpack('<24i', raw)),
                            u16=list(struct.unpack('<48H', raw))))
    group_base = spatial_base + n_spatial * SPATIAL_RECORD
    if group_base + n_group * GROUP_RECORD != len(sdb):
        raise ValueError('Unexpected SDB layout')
    groups = []
    for i in range(n_group):
        o = group_base + i * GROUP_RECORD
        count, index, offset, memsize = struct.unpack_from('<HHII', sdb, o)
        kinds = struct.unpack_from('<14H', sdb, o + 12)
        if index != i:
            raise ValueError('SDB group index mismatch')
        groups.append(dict(index=i, count=count, ssb_offset=offset, memsize=memsize,
                           kind_counts={k: c for k, c in enumerate(kinds) if c},
                           zero_tail=sdb[o + 40:o + 68] == bytes(28)))
    for loc in locations:
        for g in range(loc['group_start'], loc['last_group'] + 1):
            groups[g]['location'] = loc['name']
    return dict(header_words=list(struct.unpack_from('<2I', sdb, 0)), unknown_tail=sdb[20:80].hex(),
                locations=locations, spatial=spatial, groups=groups)


def parse_phm(phm):
    f0, f1, n = struct.unpack_from('<ffI', phm, 0)
    pos, arrays = 12, []
    for _ in range(n):
        u0, count = struct.unpack_from('<II', phm, pos)
        pos += 8
        entries = []
        for _ in range(count):
            a, b, track = struct.unpack_from('<IIB', phm, pos)
            rid = int.from_bytes(phm[pos + 9:pos + 12], 'little')
            c = struct.unpack_from('<I', phm, pos + 12)[0]
            entries.append(dict(u0=a, u1=b, track=track, rid=rid, u3=c))
            pos += 16
        arrays.append(dict(u0=u0, entries=entries))
    return dict(f0=f0, f1=f1, arrays=arrays, trailing=len(phm) - pos)


def parse_psm(psm):
    f0, f1, n = struct.unpack_from('<ffI', psm, 0)
    pos, arrays = 12, []
    for _ in range(n):
        u0, count = struct.unpack_from('<II', psm, pos)
        pos += 8
        names = []
        for _ in range(count):
            end = psm.index(b'\0', pos)
            names.append(psm[pos:end].decode('latin-1'))
            pos = end + 1
        pos = (pos + 3) // 4 * 4
        arrays.append(dict(u0=u0, names=names))
    return dict(f0=f0, f1=f1, arrays=arrays, trailing=len(psm) - pos)


class World:
    def __init__(self, archive, cache):
        self.archive, self.cache = Path(archive), Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        with self.archive.open('rb') as f:
            region = Region(f, 0, self.archive.stat().st_size)
            _, members = big_members(region)
            self.sdb_raw = self._member(region, members, 'data/worlds/bam.sdb')
            self.phm_raw = self._member(region, members, 'data/worlds/bam.phm')
            self.psm_raw = self._member(region, members, 'data/worlds/bam.psm')
            self.sdb = parse_sdb(self.sdb_raw)
            self.phm, self.psm = parse_phm(self.phm_raw), parse_psm(self.psm_raw)
            self._decode_groups(region, members)

    def _member(self, region, members, name):
        r = file_region(region, members, name)
        return r.read(0, r.size)

    def _decode_groups(self, region, members):
        """Decode every group once; cache/group_NNN.bin holds the concatenated decoded blocks."""
        self.group_raw = {}
        missing = [g for g in self.sdb['groups'] if not (self.cache / f'group_{g["index"]:03d}.bin').exists()]
        if missing:
            ssb = file_region(region, members, 'data/worlds/bam.ssb')
            data = ssb.read(0, ssb.size)
            pos, decoded, index = 0, bytearray(), 0
            while pos < len(data):
                tag, size = struct.unpack_from('<4sI', data, pos)
                if tag not in (b'CBXS', b'CEND'):
                    raise ValueError('Unsupported SSB block')
                decoded.extend(refpack(data[pos + 8:pos + size])[0])
                pos += size
                if tag == b'CEND':
                    (self.cache / f'group_{index:03d}.bin').write_bytes(decoded)
                    decoded, index = bytearray(), index + 1
            if index != len(self.sdb['groups']):
                raise ValueError('Group count mismatch')
        for g in self.sdb['groups']:
            raw = (self.cache / f'group_{g["index"]:03d}.bin').read_bytes()
            g['records'] = list(resource_records(raw))
            if len(g['records']) != g['count']:
                raise ValueError(f'Group {g["index"]}: SDB count {g["count"]} vs stream {len(g["records"])}')

    def location(self, name):
        return next(l for l in self.sdb['locations'] if l['name'] == name)

    def groups_of(self, name):
        loc = self.location(name)
        return [self.sdb['groups'][g] for g in range(loc['group_start'], loc['last_group'] + 1)]

    def records_of(self, name):
        for g in self.groups_of(name):
            for entry, payload in g['records']:
                yield g['index'], entry, payload

    def name_lookup(self):
        """(kind, track, rid) -> name via PHM entry order == PSM string order per array."""
        table = {}
        for ai, arr in enumerate(self.phm['arrays']):
            names = self.psm['arrays'][ai]['names'] if ai < len(self.psm['arrays']) else []
            kind = NAME_ARRAY_KIND.get(ai, -1 - ai)
            for i, e in enumerate(arr['entries']):
                table[(kind, e['track'], e['rid'])] = names[i] if i < len(names) else None
        return table


# ----------------------------------------------------------------- reporting

def fmt_table(rows, headers):
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
    line = lambda r: '| ' + ' | '.join(str(v).ljust(w) for v, w in zip(r, widths)) + ' |'
    return '\n'.join([line(headers), '|' + '|'.join('-' * (w + 2) for w in widths) + '|'] + [line(r) for r in rows])


def cmd_inventory(world, names):
    for name in names:
        loc = world.location(name)
        print(f'\n## {name}: groups {loc["group_start"]}..{loc["last_group"]}, spatial {loc["spatial_start"]}..'
              f'{loc["spatial_start"] + loc["spatial_count"] - 1}, tail words {loc["tail_words"]}')
        per_kind = defaultdict(lambda: [0, 0])
        rows = []
        for g in world.groups_of(name):
            kinds = Counter(); size = Counter()
            for e, p in g['records']:
                kinds[e['kind']] += 1; size[e['kind']] += len(p) + 8
                per_kind[e['kind']][0] += 1; per_kind[e['kind']][1] += len(p) + 8
            total = sum(size.values())
            kind_str = ' '.join(f'{k}:{kinds[k]}' for k in sorted(kinds))
            tracks = sorted({e['track'] for e, _ in g['records']})
            rows.append([g['index'], g['count'], total, g['memsize'], total - g['memsize'], tracks, kind_str])
        print(fmt_table(rows, ['group', 'n', 'bytes', 'memsize', 'bytes-memsize', 'tracks', 'kinds']))
        rows = [[k, KIND_NAMES.get(k, '?'), n, b, loc['kind_counts'].get(k, 0)] for k, (n, b) in sorted(per_kind.items())]
        print(fmt_table(rows, ['kind', 'name', 'count', 'bytes(+hdr)', 'sdb loc count']))


def cmd_spatial(world, names, limit):
    for name in names:
        loc = world.location(name)
        print(f'\n## spatial records of {name} ({loc["spatial_count"]} from {loc["spatial_start"]})')
        recs = world.sdb['spatial'][loc['spatial_start']:loc['spatial_start'] + loc['spatial_count']]
        for r in recs[:limit]:
            f = r['floats']; i = r['ints']
            print(f'[{r["index"]}] f0-3 {[round(x, 1) for x in f[0:4]]} f4-7 {[round(x, 1) for x in f[4:8]]}')
            print(f'      f8-19 {[round(x, 3) if abs(x) < 1e6 else x for x in f[8:20]]}')
            print(f'      i0-7 {i[0:8]} i8-19 {i[8:20]} i20-23 {i[20:24]} hex20-23 {[hex(x & 0xffffffff) for x in i[20:24]]}')


def word_stats(payload):
    n = len(payload) // 4
    words = struct.unpack_from(f'<{n}I', payload, 0)
    floats = struct.unpack_from(f'<{n}f', payload, 0)
    plausible = sum(1 for x in floats if math.isfinite(x) and 1e-3 <= abs(x) <= 1e6)
    zeros = sum(1 for w in words if w == 0)
    printable = sum(1 for b in payload if 32 <= b < 127)
    return n, plausible, zeros, printable


def cmd_kinds(world, location):
    by_kind = defaultdict(list)
    src = world.records_of(location) if location else ((g['index'], e, p) for g in world.sdb['groups'] for e, p in g['records'])
    for gi, e, p in src:
        by_kind[e['kind']].append((gi, e, p))
    rows = []
    for k in sorted(by_kind):
        items = by_kind[k]
        sizes = [len(p) for _, _, p in items]
        c = Counter(sizes)
        top = ', '.join(f'{s}x{n}' for s, n in c.most_common(3))
        tracks = Counter(e['track'] for _, e, _ in items)
        rids = [e['rid'] for _, e, _ in items]
        stats = [word_stats(p) for _, _, p in items[:200]]
        n = sum(s[0] for s in stats) or 1
        rows.append([k, KIND_NAMES.get(k, '?'), len(items), min(sizes), int(statistics.median(sizes)), max(sizes),
                     len(c), top, len(tracks), f'{min(rids)}..{max(rids)}',
                     f'{sum(s[1] for s in stats) / n:.2f}', f'{sum(s[2] for s in stats) / n:.2f}',
                     f'{sum(s[3] for s in stats) / (4 * n):.2f}'])
    print(fmt_table(rows, ['kind', 'name', 'count', 'min', 'med', 'max', 'distinct', 'top sizes', 'tracks', 'rid range',
                           'float-ish', 'zero', 'ascii']))


def cmd_names(world, location):
    table = world.name_lookup()
    print(f'PHM header {world.phm["f0"]}, {world.phm["f1"]}, arrays {len(world.phm["arrays"])}, trailing {world.phm["trailing"]}')
    print(f'PSM header {world.psm["f0"]}, {world.psm["f1"]}, arrays {len(world.psm["arrays"])}, trailing {world.psm["trailing"]}')
    for ai, arr in enumerate(world.phm['arrays']):
        names = world.psm['arrays'][ai]['names'] if ai < len(world.psm['arrays']) else []
        u0s = Counter(e['u0'] for e in arr['entries']); u1s = Counter(e['u1'] for e in arr['entries']); u3s = Counter(e['u3'] for e in arr['entries'])
        print(f'\narray {ai} (kind {NAME_ARRAY_KIND.get(ai)}): phm u0={arr["u0"]} entries={len(arr["entries"])} psm u0={world.psm["arrays"][ai]["u0"] if ai < len(world.psm["arrays"]) else None} names={len(names)}')
        print(f'  entry u0 distinct {len(u0s)} e.g. {u0s.most_common(3)}; u1 distinct {len(u1s)} e.g. {u1s.most_common(3)}; u3 distinct {len(u3s)} e.g. {u3s.most_common(3)}')
        print(f'  sample names: {names[:6]}')
    if location:
        loc = world.location(location)
        print(f'\n## names for {location}')
        found = Counter(); missing = Counter(); samples = defaultdict(list)
        for gi, e, p in world.records_of(location):
            nm = table.get((e['kind'], e['track'], e['rid']))
            if nm is None:
                missing[e['kind']] += 1
            else:
                found[e['kind']] += 1
                if len(samples[e['kind']]) < 8:
                    samples[e['kind']].append(nm)
        for k in sorted(set(found) | set(missing)):
            print(f'kind {k:2d} {KIND_NAMES.get(k, "?"):18s} named {found[k]:5d} unnamed {missing[k]:5d}  {samples[k]}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('archive', type=Path)
    ap.add_argument('--cache', type=Path, required=True, help='directory for decoded group files')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('inventory'); p.add_argument('locations', nargs='+')
    p = sub.add_parser('spatial'); p.add_argument('locations', nargs='+'); p.add_argument('--limit', type=int, default=12)
    p = sub.add_parser('kinds'); p.add_argument('--location')
    p = sub.add_parser('names'); p.add_argument('--location')
    p = sub.add_parser('locations')
    args = ap.parse_args()
    world = World(args.archive, args.cache)
    if args.cmd == 'inventory':
        cmd_inventory(world, args.locations)
    elif args.cmd == 'spatial':
        cmd_spatial(world, args.locations, args.limit)
    elif args.cmd == 'kinds':
        cmd_kinds(world, args.location)
    elif args.cmd == 'names':
        cmd_names(world, args.location)
    elif args.cmd == 'locations':
        rows = [[l['index'], l['name'], f'{l["group_start"]}-{l["last_group"]}', l['spatial_start'], l['spatial_count'],
                 ' '.join(f'{k}:{c}' for k, c in sorted(l['kind_counts'].items())), l['tail_words']] for l in world.sdb['locations']]
        print(fmt_table(rows, ['#', 'name', 'groups', 'sp.start', 'sp.n', 'kind counts', 'tail']))


if __name__ == '__main__':
    main()
