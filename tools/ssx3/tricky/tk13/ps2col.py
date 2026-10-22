# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK13: SSX 3 PS2 kind-12 triangle collision (type 1), little-endian port of tools/gamecube_collision.py.

The GameCube and PS2 SSX 3 records share one layout with byte-swapped words (checked here by reading
every stock ARA1 type-1 resource with this reader). Encoder rules carried over from the GC route
(docs/reserve/gamecube-collision.md at archive/2026-09-27-pre-cleanup): uint8 indices (partition at 256
vertices), one bbox per ten triangles, reversed winding (Tricky normals follow (b-a)x(c-a), SSX 3's the
opposite), authored normals kept, 0xDEADBEEF tail words.
"""
import math
import struct
from gamecube_collision import partition


def _vectors(data, at, count, w):
    out = [struct.unpack_from('<4f', data, at + 16 * i) for i in range(count)]
    if any(not all(math.isfinite(v) for v in p) or p[3] != w for p in out):
        raise ValueError('Unsupported/nonfinite collision vector')
    return out


def encode_collision(source):
    parts = [part for mesh in source for part in partition(mesh)]
    if not 0 < len(parts) <= 65535:
        raise ValueError('Expected a nonempty collision mesh')
    data = bytearray(16)
    for mesh in parts:
        at = len(data)
        vertices = [tuple(p[:3]) + (1.,) for p in mesh['vertices']]
        vertices = [struct.unpack('<4f', struct.pack('<4f', *p)) for p in vertices]
        triangles = [(a, c, b) for a, b, c in mesh['triangles']]
        normals = mesh['normals']
        data += bytes(20)
        data += bytes(i for tri in triangles for i in tri)
        data += bytes(-len(data) % 4)
        bbox = len(data) - at
        for start in range(0, len(triangles), 10):
            pts = [vertices[i] for tri in triangles[start:start + 10] for i in tri]
            data += struct.pack('<6f', *[min(p[k] for p in pts) for k in range(3)],
                                *[max(p[k] for p in pts) for k in range(3)])
        data += bytes(-len(data) % 16)
        verts = len(data) - at
        for p in vertices: data += struct.pack('<4f', *p)
        norms = len(data) - at
        for p in normals: data += struct.pack('<4f', *p)
        struct.pack_into('<2H4I', data, at, len(triangles), len(vertices), 20, bbox, verts, norms)
    tail = len(data)
    data += bytes.fromhex('efbeaddeefbeadde0000000000000000')
    data += bytes.fromhex('efbeadde') * len(parts)
    struct.pack_into('<2H3I', data, 0, 1, len(parts), 16, tail, tail + 16)
    return bytes(data)


def read_collision(data, bounds_tolerance=0.):
    kind, count, at, tail, refs = struct.unpack_from('<2H3I', data)
    if kind != 1 or not count or at != 16 or refs != tail + 16 or refs + 4 * count != len(data):
        raise ValueError('Unsupported target triangle collision header')
    result = []
    for _ in range(count):
        faces, verts, indices, bbox, pos, norm = struct.unpack_from('<2H4I', data, at)
        boxes = (faces + 9) // 10
        if (verts > 256 or indices != 20 or bbox < indices + 3 * faces or bbox % 4 or
                pos < bbox + 24 * boxes or (at + pos) % 16 or norm != pos + 16 * verts):
            raise ValueError('Invalid target collision offsets')
        triangles = [tuple(data[at + indices + 3 * i:at + indices + 3 * i + 3]) for i in range(faces)]
        vertices = _vectors(data, at + pos, verts, 1.)
        normals = _vectors(data, at + norm, faces, 0.)
        for tri, normal in zip(triangles, normals):
            a, b, c = [vertices[i] for i in tri]
            u, v = [b[k] - a[k] for k in range(3)], [c[k] - a[k] for k in range(3)]
            cross = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            if sum(x * y for x, y in zip(cross, normal)) > 1e-6:
                raise ValueError('winding')
        for j in range(boxes):
            b6 = struct.unpack_from('<6f', data, at + bbox + 24 * j)
            if any(not b6[k] - bounds_tolerance <= vertices[i][k] <= b6[k + 3] + bounds_tolerance
                   for tri in triangles[10 * j:10 * j + 10] for i in tri for k in range(3)):
                raise ValueError('bounds')
        result.append(dict(vertices=vertices, triangles=triangles, normals=normals))
        at += norm + 16 * faces
    if at != tail:
        raise ValueError('Unexpected target mesh trailing bytes')
    return result
