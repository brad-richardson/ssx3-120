"""Transfer verified terrain behavior from Tricky GC to SSX 3 GC.

Tricky surface zero means reset; SSX 3 zero means normal snow. SSX 3 terrain
flag 0x0002 requests a course-path reset. This deliberately partial profile keeps
other template surfaces unchanged and reports their source types for follow-up.
See docs/gamecube-collision.md for runtime evidence and limitations.
"""
from collections import Counter
import hashlib
import math
import struct

from import_terrain import transform_coefficients

# Two NBD header words appear across the ten GameCube Tricky courses:
# 0x00161d03 (gari, merquer, snow) and 0x00161b03 (the other seven). Every
# course's terrain section passes the same 448-byte stride and extent checks
# under both, so the third byte is treated as a version/flag, not a layout
# change. Surveyed 2026-09-14 over all ten `*.nbd` members.
NBD_MAGICS = (0x00161d03, 0x00161b03)

RESET_PROFILE = 'tricky-gc-to-ssx3-gc-reset-v2'
PROFILES = (RESET_PROFILE, 'template')


def source_patches(raw):
    if len(raw) < 160 or struct.unpack_from('>I', raw)[0] not in NBD_MAGICS:
        raise ValueError('Expected a GameCube Tricky NBD header')
    count = struct.unpack_from('>I', raw, 8)[0]
    start, end = struct.unpack_from('>2I', raw, 68)
    if not count or not 160 <= start <= end <= len(raw) or start + 448 * count != end:
        raise ValueError('Invalid Tricky terrain section extent')
    result = []
    for i in range(count):
        at = start + 448 * i
        coeffs = [struct.unpack_from('>4f', raw, at + 80 + 16 * j)[:3] for j in range(16)]
        if not all(math.isfinite(v) for c in coeffs for v in c):
            raise ValueError(f'Nonfinite source terrain at patch {i}')
        result.append((coeffs, struct.unpack_from('>I', raw, at + 360)[0]))
    return result


def transfer_surfaces(records, raw, matrix, translation, *, profile=RESET_PROFILE, limit=None):
    """Match every patch's transformed geometry before assigning its behavior.

    Exact float32 coefficient matching prevents applying ordinal metadata to a
    reordered, differently placed, or unrelated course. Bytes 8..11 carry the
    surface/reset flag; other collision flags, rendering and geometry remain.
    """
    if profile not in PROFILES:
        raise ValueError(f'Unknown terrain surface profile: {profile}')
    if (len(matrix) != 3 or any(len(row) != 3 for row in matrix) or len(translation) != 3 or
            not all(math.isfinite(v) for row in matrix for v in row) or
            not all(math.isfinite(v) for v in translation)):
        raise ValueError('Expected a finite 3x3 placement and translation')
    source = source_patches(raw)
    if limit is not None:
        if not isinstance(limit, int) or not 0 < limit <= len(source):
            raise ValueError('Terrain limit outside source count')
        source = source[:limit]
    terrain = [(e, p) for e, p in records if e['kind'] == 1]
    if len(terrain) != len(source):
        raise ValueError('Source and target terrain counts differ')
    ids = [(e['track'], e['rid']) for e, _ in terrain]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate terrain resource identity')
    mapped, changed, out, index = [], [], [], 0
    for e, p in records:
        if e['kind'] == 1:
            coeffs, surface = source[index]
            if len(p) != 430 or e['size'] != 430:
                raise ValueError('Expected a 430-byte GC terrain resource')
            expected = transform_coefficients(coeffs, matrix, translation)
            expected = b''.join(struct.pack('>3f', *c) for c in expected)
            actual = b''.join(p[64+16*j:76+16*j] for j in range(16))
            if expected != actual:
                raise ValueError(f'Terrain geometry differs at source patch {index}; refusing ordinal surface assignment')
            if profile == RESET_PROFILE and surface == 0:
                mapped.append(dict(source_patch=index, track=e['track'], rid=e['rid']))
                # GXBE69 80008650/54 copies patch +10 to rider +684. At
                # 8000606C..84, bit 1 calls reset(rider, 0, 1): a course-path
                # reset. Surface 18 takes a different wipeout branch first and
                # can recover repeatedly onto the same hazard (v1 regression).
                flags = struct.unpack_from('>H', p, 10)[0]
                header = struct.pack('>hH', 0, flags | 2)
                if p[8:12] != header:
                    changed.append(index)
                    p = p[:8] + header + p[12:]
            index += 1
        out.append((e, p))
    counts = Counter(surface for _, surface in source)
    return out, dict(profile=profile, donor_nbd_sha256=hashlib.sha256(raw).hexdigest(),
                     geometry_matches=len(source), target_reset_flag=2, target_surface=0,
                     source_surface_counts=dict(sorted(counts.items())),
                     mapped_reset_patches=mapped, changed_source_patches=changed,
                     retained_template_surface_counts={s: n for s, n in sorted(counts.items())
                                                       if profile == 'template' or s != 0})
