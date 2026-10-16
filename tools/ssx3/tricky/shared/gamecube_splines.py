"""Strict Tricky GC rail reader and SSX 3 GC curve encoder.

The GC target has a 12-byte segment prefix, not the PS2 reader's 16 bytes.
GXBE69 8024807C fixes links at +92/+96 with a 144-byte stride; 80247970
registers the bounds at +104/+116. Keep gameplay/style binding separate.
"""
import math
import struct

from gamecube_surfaces import NBD_MAGICS
from import_terrain import apply
from patch_geometry import outward_float32

NULL = 0xffffffff
PROFILE = 'tricky-gc-to-ssx3-gc-rails-v1'


def polynomial(coefficients, t):
    a, b, c, d = coefficients
    return ((a*t + b)*t + c)*t + d


def curve_point(coefficients, t):
    return [polynomial([v[k] for v in coefficients], t) for k in range(3)]


def curve_bounds(coefficients):
    """Conservative Bezier hull of the actual serialized power coefficients."""
    a, b, c, d = coefficients
    controls = [d, [d[k] + c[k]/3 for k in range(3)],
                [d[k] + 2*c[k]/3 + b[k]/3 for k in range(3)],
                [math.fsum((d[k], c[k], b[k], a[k])) for k in range(3)]]
    return ([outward_float32(min(v[k] for v in controls), False) for k in range(3)],
            [outward_float32(max(v[k] for v in controls), True) for k in range(3)])


def similarity_scale(matrix, translation):
    """Distance polynomials can only be reused under a uniform scale."""
    if (len(matrix) != 3 or any(len(row) != 3 for row in matrix) or
            len(translation) != 3 or
            not all(math.isfinite(x) for row in [*matrix, translation] for x in row)):
        raise ValueError('Expected finite 3x3 transform and translation')
    columns = list(zip(*matrix))
    scale = math.sqrt(sum(x*x for x in columns[0]))
    if scale <= 0 or not math.isfinite(scale):
        raise ValueError('Invalid spline scale')
    for i in range(3):
        for j in range(3):
            dot = sum(a*b for a, b in zip(columns[i], columns[j])) / scale**2
            if not math.isclose(dot, 1 if i == j else 0, abs_tol=1e-7):
                raise ValueError('Spline transform must have uniform scale without shear')
    return scale


def read_tricky_splines(nbd, gsf):
    """Follow links, checking ownership, reciprocal edges and full coverage.

    The segment array need not be in traversal order. Unknown styles remain
    available to callers for reporting; encoders must explicitly support them.
    """
    if len(nbd) < 160 or struct.unpack_from('>I', nbd)[0] not in NBD_MAGICS:
        raise ValueError('Unsupported Tricky GC NBD header')
    h = struct.unpack_from('>40I', nbd)
    count, segment_count = h[8:10]
    start, segments, end = h[23:26]
    if not (160 <= start <= segments <= end <= len(nbd)) or (
            not 0 <= segments - (start + count*40) < 16 or
            segments + segment_count*128 != end):
        raise ValueError('Invalid NBD spline table extents')
    if len(gsf) < 76 or struct.unpack_from('>I', gsf)[0] != 0x00021e00:
        raise ValueError('Unsupported Tricky GC GSF header')
    style_count, style_start = struct.unpack_from('>II', gsf, 68)
    if style_count != count or style_start < 76 or style_start + count*8 != len(gsf):
        raise ValueError('GSF spline style table does not match NBD')
    raw = []
    for i in range(segment_count):
        o = segments + i*128
        floats = struct.unpack_from('>20f', nbd, o)
        bounds = struct.unpack_from('>6f', nbd, o+92)
        length, previous_distance = struct.unpack_from('>2f', nbd, o+116)
        if not all(math.isfinite(x) for x in (*floats, *bounds, length, previous_distance)):
            raise ValueError(f'Nonfinite spline segment {i}')
        if length <= 0 or previous_distance < 0 or any(bounds[k] > bounds[k+3] for k in range(3)):
            raise ValueError(f'Invalid spline segment bounds/distance {i}')
        if floats[3:16:4] != (0, 0, 0, 1):
            raise ValueError(f'Unexpected homogeneous spline coefficients {i}')
        previous, following, parent = struct.unpack_from('>3I', nbd, o+80)
        raw.append(dict(index=i, coefficients=[floats[k:k+4] for k in range(0, 16, 4)],
                        inverse_distance=floats[16:20], length=length,
                        previous_distance=previous_distance, previous=previous,
                        next=following, parent=parent))
    result, seen = [], set()
    for rid in range(count):
        bounds = struct.unpack_from('>6f', nbd, start+rid*40)
        flags, n, first, unknown = struct.unpack_from('>4I', nbd, start+rid*40+24)
        if (not all(math.isfinite(x) for x in bounds) or
                any(bounds[k] > bounds[k+3] for k in range(3)) or not 0 < n <= segment_count):
            raise ValueError(f'Invalid spline header {rid}')
        chain, index = [], first
        for _ in range(n):
            if not 0 <= index < segment_count or index in seen or raw[index]['parent'] != rid:
                raise ValueError(f'Invalid or shared spline segment in spline {rid}')
            seen.add(index)
            chain.append(raw[index])
            index = raw[index]['next']
        closed = index == first
        if index != NULL and not closed:
            raise ValueError(f'Spline {rid} extends beyond declared segment count')
        distance = 0
        for j, segment in enumerate(chain):
            previous = chain[j-1]['index'] if j or closed else NULL
            if segment['previous'] != previous:
                raise ValueError(f'Nonreciprocal spline link in spline {rid}')
            if not math.isclose(segment['previous_distance'], distance, rel_tol=2e-5, abs_tol=.1):
                raise ValueError(f'Inconsistent cumulative spline distance in spline {rid}')
            if j or closed:
                gap = math.dist(curve_point(chain[j-1]['coefficients'], 1),
                                curve_point(segment['coefficients'], 0))
                if gap > .1:
                    raise ValueError(f'Discontinuous spline {rid}: {gap}')
            distance += segment['length']
        result.append(dict(rid=rid, flags=flags, unknown=unknown, closed=closed,
                           style=struct.unpack_from('>HHI', gsf, style_start+8*rid),
                           segments=chain))
    if len(seen) != segment_count:
        raise ValueError('Unowned NBD spline segments')
    return result


def encode_spline(spline, matrix, translation, oid, segment_template):
    """Encode geometry; retain only the target's opaque 8-byte segment trailer.

    Tricky's inverse-distance cubic takes metres (source units / 100).
    SSX 3's takes world units. Divide its coefficients by (100*scale)^power;
    translating a direction coefficient or merely scaling E2 is incorrect.
    This preserves the donor fit, including its approximation error.
    """
    scale = similarity_scale(matrix, translation)
    if spline['flags'] != 0 or spline['unknown'] != NULL or spline['style'] != (1, 1, 13):
        raise ValueError('Unsupported donor rail flags/style for this profile')
    if len(segment_template) != 144 or segment_template[136:] != bytes.fromhex('157259000f000000'):
        raise ValueError('Unsupported GXBE69 spline segment trailer')
    if not 0 <= oid <= 0xffffffff or oid & 0xffffff >= 0x7fff:
        raise ValueError('Spline ID exceeds resource capacity')
    chain = spline['segments']
    if not chain:
        raise ValueError('Empty spline')
    data = bytearray(48 + 144*len(chain))
    lows, highs = [], []
    for j, segment in enumerate(chain):
        o = 48 + 144*j
        coefficients = [apply(matrix, translation if k == 3 else [0, 0, 0], c[:3]) + [c[3]]
                        for k, c in enumerate(segment['coefficients'])]
        struct.pack_into('>16f', data, o+12, *(x for c in coefficients for x in c))
        actual = struct.unpack_from('>16f', data, o+12)
        low, high = curve_bounds([actual[k:k+4] for k in range(0, 16, 4)])
        lows.append(low)
        highs.append(high)
        inverse = [v/(100*scale)**(3-k) for k, v in enumerate(segment['inverse_distance'])]
        previous = j-1 if j else len(chain)-1 if spline['closed'] else NULL
        following = j+1 if j+1 < len(chain) else 0 if spline['closed'] else NULL
        struct.pack_into('>4f3I8f', data, o+76, *inverse, previous, following, oid,
                         *low, *high, segment['length']*scale, segment['previous_distance']*scale)
        data[o+136:o+144] = segment_template[136:]
    low = [min(v[k] for v in lows) for k in range(3)]
    high = [max(v[k] for v in highs) for k in range(3)]
    struct.pack_into('>I6f5I', data, 0, oid, *low, *high, 0, len(chain), 0, NULL, 0)
    return bytes(data)


def audit_conversion(source, payloads, matrix, translation):
    """Check every emitted curve against source-space geometry and distance.

    Sampling is a numerical cross-check, not the proof of conservative bounds;
    the encoder's control hull supplies that. Record donor fit error separately
    from conversion error so an existing approximation is not misdiagnosed.
    """
    scale = similarity_scale(matrix, translation)
    if len(source) != len(payloads):
        raise ValueError('Spline audit resource count mismatch')
    position_error = inverse_error = fit_error = join_gap = 0
    samples = violations = segments = 0
    for spline, data in zip(source, payloads):
        if len(data) != 48+144*len(spline['segments']):
            raise ValueError('Spline audit segment count mismatch')
        previous = None
        for j, segment in enumerate(spline['segments']):
            offset = 48+144*j
            values = struct.unpack_from('>16f', data, offset+12)
            coefficients = [values[k:k+4] for k in range(0, 16, 4)]
            bounds = struct.unpack_from('>6f', data, offset+104)
            inverse = struct.unpack_from('>4f', data, offset+76)
            if not all(math.isfinite(x) for x in (*values, *bounds, *inverse)):
                raise ValueError('Nonfinite emitted spline data')
            if previous is not None:
                join_gap = max(join_gap, math.dist(previous, curve_point(coefficients, 0)))
            previous = curve_point(coefficients, 1)
            fit_error = max(fit_error, abs(polynomial(segment['inverse_distance'], segment['length']/100)-1))
            segments += 1
            for k in range(257):
                t = k/256
                actual = curve_point(coefficients, t)
                expected = apply(matrix, translation, curve_point(segment['coefficients'], t))
                position_error = max(position_error, math.dist(actual, expected))
                violations += any(not bounds[a] <= actual[a] <= bounds[a+3] for a in range(3))
                d = segment['length']*t
                inverse_error = max(inverse_error, abs(polynomial(inverse, d*scale) -
                                                       polynomial(segment['inverse_distance'], d/100)))
                samples += 1
    if position_error > .1 or inverse_error > 1e-5 or violations:
        raise ValueError(f'Spline numerical audit failed: position={position_error}, '
                         f'inverse={inverse_error}, bound violations={violations}')
    return dict(splines=len(source), segments=segments, samples=samples,
                max_position_error_world_units=position_error, max_join_gap_world_units=join_gap,
                max_inverse_parameter_error=inverse_error, bound_violations=violations,
                donor_inverse_fit_endpoint_error=fit_error,
                scope='Numerical conversion audit; does not verify live grinding or effects')
