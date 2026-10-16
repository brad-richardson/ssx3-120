#!/usr/bin/env python3
"""Stage a native-runtime GameCube game directory that differs from stock in its worlds.

Every entry is a symlink into the stock tree, so a staged directory costs no
disk beyond the archives it installs. `--world NAME=PATH` installs a built
archive as `files/data/worlds/NAME.big`; when NAME is not the stock `bam` the
archive's BIGF member basenames are rewritten to match, because the game builds
`data/worlds/<archive>.gdb` / `.gsb` from the event record's archive name and a
mismatch resolves to a NULL resource (docs/course-selection.md). Member data
stays at its original offsets, so only the directory bytes differ.

No manifest is written and no runtime is launched; the course redirect that
points an event row at an installed archive is the harness's --course-manifest.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct

from inspect_disc import Region, big_members

WORLD_DIR = Path('files/data/worlds')
STOCK_NAME = 'bam'
# The four members the path builder derives from the archive name. serial.txt is
# not one of them and keeps its name.
WORLD_EXTENSIONS = ('gdb', 'gsb', 'ghm', 'gsm')


def world_members(data):
    """BIGF entries plus the single basename shared by the four world members."""
    kind, entries = big_members(Region(io.BytesIO(data), 0, len(data)))
    if kind != 'BIGF':
        raise ValueError('World archive is not a BIGF container')
    stems = {Path(e['path']).stem for e in entries
             if Path(e['path']).suffix[1:] in WORLD_EXTENSIONS
             and Path(e['path']).parent.as_posix() == 'data/worlds'}
    if len(stems) != 1:
        raise ValueError('World archive members do not share one basename')
    return entries, stems.pop()


def rename_world_members(data, name):
    """Rewrite the BIGF directory so the world members read data/worlds/<name>.*

    Each entry's offset/size words, the 16-byte header and the directory
    trailer are copied verbatim, and the rebuilt directory must still end
    before the first member, so every byte of member data keeps its original
    offset. A rename to a same-length basename is therefore a pure in-place
    substitution.
    """
    if not name or not name.isascii() or not name.replace('_', '').isalnum():
        raise ValueError('Archive basename must be ASCII alphanumeric')
    entries, stem = world_members(data)
    if name == stem:
        return data
    first = min(e['offset'] for e in entries)
    count, end = struct.unpack_from('>II', data, 8)
    if not 16 <= end <= first:
        raise ValueError('Invalid BIGF directory bounds')
    table, pos, renamed_count = bytearray(), 16, 0
    for _ in range(count):
        zero = data.index(0, pos + 8)
        stored = data[pos + 8:zero].decode('ascii')
        path = PurePosixPath(stored.replace('\\', '/'))
        if path.stem == stem and path.parent.as_posix() == 'data/worlds':
            fresh = f'data/worlds/{name}{path.suffix}'
            stored = fresh.replace('/', '\\') if '\\' in stored else fresh
            renamed_count += 1
        table += data[pos:pos + 8] + stored.encode('ascii') + b'\0'
        pos = zero + 1
    table += data[pos:end]
    if not renamed_count:
        raise ValueError('No world members to rename')
    if 16 + len(table) > first:
        room = len(name) - -(-(16 + len(table) - first) // renamed_count)
        raise ValueError('Renamed directory does not fit before the first member; '
                         f'this archive allows a basename of {room} characters')
    out = bytearray(data)
    out[16:first] = table + bytes(first - 16 - len(table))
    struct.pack_into('>I', out, 12, 16 + len(table))
    fresh_entries, fresh_stem = world_members(bytes(out))
    if fresh_stem != name or [(e['offset'], e['size']) for e in fresh_entries] != [
            (e['offset'], e['size']) for e in entries]:
        raise ValueError('Rename readback disagrees with the request')
    return bytes(out)


def parse_world(value):
    name, sep, path = value.partition('=')
    if not sep:
        name, path = STOCK_NAME, value
    path = Path(path)
    if path.is_dir():
        path = path / 'BAM.BIG'
    if not path.is_file():
        raise argparse.ArgumentTypeError(f'No world archive at {path}')
    return name.lower(), path


def stage(out, stock, worlds, dol=None):
    """Symlink the stock tree into out, installing each world archive."""
    receipt = {'stock': str(stock), 'worlds': {}, 'entries': 0}
    for root, _, files in os.walk(stock):
        rel = Path(root).relative_to(stock)
        (out / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            target = out / rel / name
            source = (Path(root) / name).resolve()
            if rel == WORLD_DIR and Path(name).stem in worlds:
                continue  # installed below, from a build rather than stock
            if dol and rel / name == Path('sys/main.dol'):
                shutil.copyfile(dol.resolve(), target)
            else:
                os.symlink(source, target)
            receipt['entries'] += 1
    for name, path in worlds.items():
        data = path.read_bytes()
        renamed = rename_world_members(data, name)
        target = out / WORLD_DIR / f'{name}.big'
        if renamed is data:
            os.symlink(path.resolve(), target)
        else:
            target.write_bytes(renamed)
        receipt['entries'] += 1
        receipt['worlds'][name] = {
            'source': str(path),
            'source_sha256': hashlib.sha256(data).hexdigest(),
            'installed_sha256': hashlib.sha256(renamed).hexdigest(),
            'members_renamed': renamed is not data,
            'linked': renamed is data,
        }
    if dol:
        receipt['dol'] = {'source': str(dol),
                          'sha256': hashlib.sha256(dol.read_bytes()).hexdigest()}
    if set(receipt['worlds']) != set(worlds):
        raise ValueError('Not every requested world archive was installed')
    return receipt


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('out', type=Path, help='Game directory to create; must not exist')
    ap.add_argument('--stock', type=Path, default=Path('local/game/gxbe69-stock'),
                    help='Extracted stock game directory to link against')
    ap.add_argument('--world', type=parse_world, action='append', default=[], metavar='NAME=PATH',
                    help='Install PATH (a build directory or a BAM.BIG) as '
                         'files/data/worlds/NAME.big, renaming its members to NAME. '
                         'Repeatable. A bare PATH installs as the stock bam.')
    ap.add_argument('--dol', type=Path,
                    help='Copy this file over sys/main.dol instead of linking the stock one')
    ap.add_argument('--receipt', type=Path, help='Write the JSON receipt here as well')
    args = ap.parse_args()
    if args.out.exists():
        raise SystemExit('Output game directory already exists; choose a fresh one')
    if not (args.stock / 'sys/main.dol').is_file():
        raise SystemExit(f'No extracted game at {args.stock}')
    if not args.world:
        raise SystemExit('Nothing to stage; pass at least one --world')
    worlds = dict(args.world)
    if len(worlds) != len(args.world):
        raise SystemExit('Duplicate --world basename')
    receipt = stage(args.out, args.stock.resolve(), worlds, args.dol)
    receipt['game'] = str(args.out)
    if args.receipt:
        args.receipt.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
