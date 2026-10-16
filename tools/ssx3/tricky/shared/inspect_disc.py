#!/usr/bin/env python3
"""Read-only ISO9660 / EA BIG inspection. Python standard library only.

Supports the single-extent, 2048-byte-sector PS2 discs in this investigation.
EA archive layouts were checked against GlitcherOG/SSX-Library (see docs).
Extraction deliberately preserves stored bytes; it does not decompress assets.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path


class Region:
    def __init__(self, stream, base, size):
        self.stream, self.base, self.size = stream, base, size

    def read(self, offset, length):
        if offset < 0 or length < 0 or offset + length > self.size:
            raise ValueError(f"Read outside region: {offset}+{length}>{self.size}")
        self.stream.seek(self.base + offset)
        data = self.stream.read(length)
        if len(data) != length:
            raise ValueError("Truncated input")
        return data

    def child(self, offset, size):
        if offset < 0 or size < 0 or offset + size > self.size:
            raise ValueError("Child outside region")
        return Region(self.stream, self.base + offset, size)


def iso_files(region):
    pvd = region.read(16 * 2048, 2048)
    if pvd[:7] != b"\x01CD001\x01" or struct.unpack_from('<H', pvd, 128)[0] != 2048:
        raise ValueError("Expected ISO9660 primary descriptor with 2048-byte sectors")
    files, seen = [], set()

    def walk(record, prefix):
        extent = int.from_bytes(record[2:6], 'little')
        size = int.from_bytes(record[10:14], 'little')
        if extent in seen:
            raise ValueError("Repeated directory extent")
        seen.add(extent)
        directory = region.read(extent * 2048, size)
        pos = 0
        while pos < size:
            length = directory[pos]
            if not length:
                pos = (pos // 2048 + 1) * 2048
                continue
            rec = directory[pos:pos + length]
            if len(rec) != length or length < 34 or 33 + rec[32] > length:
                raise ValueError("Malformed directory record")
            pos += length
            raw_name = rec[33:33 + rec[32]]
            if raw_name in (b'\0', b'\1'):
                continue
            if rec[1] or rec[25] & 128 or rec[26] or rec[27]:
                raise ValueError("Unsupported extended/interleaved/multi-extent file")
            name = raw_name.decode('ascii').split(';')[0]
            path = prefix + name
            if rec[25] & 2:
                walk(rec, path + '/')
            else:
                offset = int.from_bytes(rec[2:6], 'little') * 2048
                length = int.from_bytes(rec[10:14], 'little')
                region.child(offset, length)  # validate extent bounds
                files.append(dict(path=path, offset=offset, size=length))

    walk(pvd[156:156 + pvd[156]], '')
    return pvd[40:72].decode('ascii').strip(), files


def big_members(region):
    header = region.read(0, min(48, region.size))
    members = []
    if header[:4] in (b'BIGF', b'BIG4'):
        count, end = struct.unpack_from('>II', header, 8)
        if end < 16 or end > region.size or count > (end - 16) // 9:
            raise ValueError("Invalid BIG directory bounds")
        table = region.read(0, end)
        pos = 16
        for _ in range(count):
            offset, size = struct.unpack_from('>II', table, pos)
            pos += 8
            zero = table.index(0, pos)
            name = table[pos:zero].decode('ascii').replace('\\', '/')
            pos = zero + 1
            members.append(dict(path=name, offset=offset, size=size, stored_size=size))
        kind = header[:4].decode('ascii')
    elif header[:2] == b'\xc0\xfb':
        end, count = struct.unpack_from('>HH', header, 2)
        # C0FB counts the directory bytes after its six-byte fixed header.
        end += 6
        if end < 6 or end > region.size or count > (end - 6) // 7:
            raise ValueError("Invalid C0FB directory bounds")
        table = region.read(0, end)
        pos = 6
        for _ in range(count):
            if pos + 6 > end:
                raise ValueError("Truncated C0FB entry")
            offset = int.from_bytes(table[pos:pos+3], 'big')
            size = int.from_bytes(table[pos+3:pos+6], 'big')
            pos += 6
            zero = table.index(0, pos)
            name = table[pos:zero].decode('ascii').replace('\\', '/')
            pos = zero + 1
            members.append(dict(path=name, offset=offset, size=size, stored_size=size))
        kind = 'C0FB'
    else:
        raise ValueError(f"Unsupported BIG signature: {header[:4].hex()}")
    for entry in members:
        child = region.child(entry['offset'], entry['stored_size'])
        entry['prefix_hex'] = child.read(0, min(child.size, 16)).hex()
    return kind, members


def file_region(region, entries, path):
    matches = [e for e in entries if e['path'].casefold() == path.casefold()]
    if len(matches) != 1:
        raise ValueError(f"Expected one match for {path}, got {len(matches)}")
    entry = matches[0]
    return region.child(entry['offset'], entry.get('stored_size', entry['size']))


def digest(region, algorithm):
    h = hashlib.new(algorithm)
    for pos in range(0, region.size, 4 * 1024 * 1024):
        h.update(region.read(pos, min(4 * 1024 * 1024, region.size - pos)))
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('iso', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--archive', help='Inventory one ISO member as a BIG archive')
    ap.add_argument('--extract', help='Extract one ISO member (or member of --archive), as stored')
    ap.add_argument('--hash-iso', action='store_true', help='Also stream entire ISO for SHA-256')
    args = ap.parse_args()
    if args.iso.resolve() == args.output.resolve():
        ap.error('Output cannot be the source ISO')
    with args.iso.open('rb') as f:
        disc = Region(f, 0, args.iso.stat().st_size)
        volume, files = iso_files(disc)
        region, entries = disc, files
        report = dict(source=str(args.iso.resolve()), size=disc.size, volume=volume, files=files)
        for entry in files:
            if entry['path'].startswith(('SLUS_', 'SLES_')):
                report['executable'] = dict(path=entry['path'], sha1=digest(file_region(disc, files, entry['path']), 'sha1'))
        report['system_cnf'] = file_region(disc, files, 'SYSTEM.CNF').read(0, next(e['size'] for e in files if e['path'] == 'SYSTEM.CNF')).decode('ascii')
        if args.hash_iso:
            report['sha256'] = digest(disc, 'sha256')
        if args.archive:
            region = file_region(disc, files, args.archive)
            kind, entries = big_members(region)
            report['archive'] = dict(path=args.archive, kind=kind, size=region.size, members=entries)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.extract:
            selected = file_region(region, entries, args.extract)
            with args.output.open('xb') as out:
                for pos in range(0, selected.size, 4 * 1024 * 1024):
                    out.write(selected.read(pos, min(4 * 1024 * 1024, selected.size-pos)))
            print(f'Extracted {selected.size} stored bytes to {args.output}')
        else:
            args.output.write_text(json.dumps(report, indent=2) + '\n')
            print(f'{volume or "(unnamed volume)"}: {len(files)} files; {report.get("executable")}')
            if args.archive:
                print(f'{kind}: {len(entries)} members; {region.size} bytes')


if __name__ == '__main__':
    main()
