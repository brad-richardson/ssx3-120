#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""Shared Tricky converter inputs (converter-scope-1).

Single home for every repo value the Tricky driver reads from the
builder wrapper: the SSX3 disc SHA, the ISO9660 helpers, the pinned
Tricky disc table and the converter-version hash. Both
tools/ssx3/build.py and tools/ssx3/tricky/build_tricky.py import this
module by path, so the file lives under tools/ssx3/tricky/** and is
covered by the existing tricky/** converter hashing. Editing
tools/ssx3/build.py (UX text, disk preflight, link caps) no longer
invalidates a user's cached Tricky layer.

Behaviour is byte-for-byte the moved code from tools/ssx3/build.py;
this module adds no new logic.
"""
import hashlib
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

SECTOR = 2048

ISO_SHA = '3c2f8eb182c9c6208a6e8172a41e61c98f420abe3f42c845f6829aeb9761ebf5'


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def iso_records(iso):
    """Yield (path, first LBA, byte size) from the pinned ISO9660 tree."""
    with iso.open('rb') as f:
        f.seek(16 * SECTOR)
        pvd = f.read(SECTOR)
        if pvd[:7] != b'\x01CD001\x01':
            raise ValueError('expected ISO9660 primary volume descriptor')

        def walk(path, extent, length):
            f.seek(extent * SECTOR)
            directory = f.read(length)
            pos = 0
            while pos < length:
                size = directory[pos]
                if not size:
                    pos = (pos // SECTOR + 1) * SECTOR
                    continue
                entry = directory[pos:pos + size]
                pos += size
                if len(entry) < 34 or entry[32] == 1 and entry[33] in (0, 1):
                    continue
                name = entry[33:33 + entry[32]].decode('ascii').split(';')[0]
                lba = struct.unpack_from('<I', entry, 2)[0]
                bytes_ = struct.unpack_from('<I', entry, 10)[0]
                child = path + '/' + name if path else name
                if entry[25] & 2:
                    yield from walk(child, lba, bytes_)
                else:
                    yield child, lba, bytes_

        root = pvd[156:190]
        yield from walk('', struct.unpack_from('<I', root, 2)[0],
                        struct.unpack_from('<I', root, 10)[0])


TRICKY_ELF_NAME = 'SLUS_203.26'
# Pinned Tricky disc identities: full-ISO SHA-256 -> serial, region, exact
# byte size and MD5. Only locally measured dumps are keys (see
# docs/tricky-inputs.md); any other pressing, including PAL/JP, is refused
# until its dump is measured and keyed here.
TRICKY_DISCS = {
    'deae53c10447e59c2c4cd1d3a904a1ed04cbc818444f4883962a0060da837970': {
        'serial': 'SLUS-20326', 'region': 'NTSC-U', 'bytes': 2902425600,
        'md5': '7d8ee567145bdf0c236ce5f5c679108a'},
}
# Internal markers after the ELF name: the per-course model archive and the
# music archive whose v3 .mpf members the music converter checks itself.
TRICKY_MARKERS = ('DATA/MODELS/GARI.BIG', 'DATA/AUDIO/MUSIC.BIG')


def check_tricky_iso_file(path):
    """Cheap --tricky-iso checks (exists + ISO9660); no driver gate."""
    iso = Path(path)
    if not iso.is_file():
        raise ValueError('tricky ISO not found: ' + str(iso))
    with iso.open('rb') as f:
        f.seek(16 * SECTOR)
        if f.read(7) != b'\x01CD001\x01':
            raise ValueError('not an ISO9660 disc image: ' + str(iso))
    return iso


def tricky_disc_identity(iso):
    """Full disc identity for a validated Tricky ISO (slow: hashes 2.9 GB).

    Checks the pinned key table, the exact byte size, the ELF name in the
    disc root and the internal markers, in that order. Refuses with the
    docs/tricky-inputs.md pointer on any mismatch.
    """
    iso = Path(iso)
    digest = sha(iso)
    try:
        disc = TRICKY_DISCS[digest]
    except KeyError:
        raise ValueError('unsupported Tricky disc SHA256; see docs/tricky-inputs.md')
    if iso.stat().st_size != disc['bytes']:
        raise ValueError('Tricky ISO byte size mismatch for ' + disc['serial'])
    names = {name.upper() for name, _, _ in iso_records(iso)}
    if TRICKY_ELF_NAME not in names:
        raise ValueError(TRICKY_ELF_NAME + ' missing from the Tricky disc root')
    for marker in TRICKY_MARKERS:
        if marker not in names:
            raise ValueError('Tricky disc marker missing: ' + marker)
    return {'sha256': digest, 'serial': disc['serial'], 'region': disc['region'],
            'bytes': disc['bytes'], 'md5': disc['md5']}


# No repo paths outside tools/ssx3/tricky/** affect converter output:
# the driver and the wrapper both import the shared inputs above from
# this module, so the tricky/** hash covers them.
TRICKY_CONVERTER_EXTRA = ()


def tricky_converter_table(root=ROOT):
    """Sorted (relpath, content-SHA) converter inputs (tricky-manifest-1).

    Every file the conversion reads from the repo: tools/ssx3/tricky/**
    (code + data, excluding tests/ and __pycache__) plus
    TRICKY_CONVERTER_EXTRA (repo paths the driver imports).
    """
    root = Path(root)
    tricky = root / 'tools' / 'ssx3' / 'tricky'
    candidates = []
    for candidate in sorted(tricky.rglob('*')):
        if not candidate.is_file():
            continue
        relparts = candidate.relative_to(tricky).parts
        if 'tests' in relparts or '__pycache__' in relparts:
            continue
        if candidate.suffix in ('.pyc', '.md'):
            # Docs never change converter output; a README edit must not
            # invalidate a user's cached Tricky layer.
            continue
        candidates.append(candidate.relative_to(root).as_posix())
    candidates.extend(TRICKY_CONVERTER_EXTRA)
    table = []
    for rel in sorted(candidates):
        table.append((rel, hashlib.sha256((root / rel).read_bytes()).hexdigest()))
    return table


def tricky_converter_version(root=ROOT):
    """SHA-256 over the sorted converter-input table (tricky-manifest-1)."""
    h = hashlib.sha256()
    for rel, digest in tricky_converter_table(root):
        h.update((rel + '\0' + digest + '\n').encode())
    return h.hexdigest()
