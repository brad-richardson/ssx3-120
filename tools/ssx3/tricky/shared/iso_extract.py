#!/usr/bin/env python3
"""Single-member ISO/BIG extraction through the in-repo ISO9660 reader.

Shared by the Tricky driver (build_tricky.py) and the HUD atlas tool
(make_hud_atlas.py); both used to shell out to bsdtar for the one file
they need. Standard library only.
"""
import hashlib
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from inspect_disc import Region, iso_files, big_members  # noqa: E402
from inventory_tricky import decode_member  # noqa: E402


def sha(path):
    h1 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    h2 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    assert h1 == h2, f'unstable read: {path}'
    return h1


def extract_member(iso, member_path, out):
    """Copy one ISO member (case-insensitive full path) to out."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(iso, 'rb') as f:
        reg = Region(f, 0, Path(iso).stat().st_size)
        _, files = iso_files(reg)
        e = next(e for e in files if e['path'].upper() == member_path.upper())
        f.seek(e['offset'])
        out.write_bytes(f.read(e['size']))
    back = out.read_bytes()
    assert sha(out) == hashlib.sha256(back).hexdigest()
    return out


def big_member(iso, big_iso_path, want, out):
    """Extract one member from a BIG container on the disc (no full decode)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(iso, 'rb') as f:
        reg = Region(f, 0, Path(iso).stat().st_size)
        _, files = iso_files(reg)
        big = next(e for e in files if e['path'].upper() == big_iso_path.upper())
        sub = Region(f, big['offset'], big['size'])
        _, members = big_members(sub)
        norm = want.upper().replace('/', '\\')
        if '\\' in norm:
            # big_members stores forward slashes; normalize both sides.
            hits = [m for m in members
                    if m['path'].upper().replace('/', '\\') == norm]
        else:
            hits = [m for m in members
                    if m['path'].upper().replace('\\', '/').split('/')[-1] == norm]
        if len(hits) != 1:
            raise SystemExit(f'{big_iso_path}: member {want}: {len(hits)} hits')
        raw, _ = decode_member(sub, hits[0])
        out.write_bytes(raw)
    back = out.read_bytes()
    assert sha(out) == hashlib.sha256(back).hexdigest()
    return out
