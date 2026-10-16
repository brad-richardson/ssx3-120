"""Conservative bounds for SSX bicubic power-basis terrain patches."""
import math
import struct


def outward_float32(value, upper):
    """Round a bound outward to the actual float32 value written to the world."""
    rounded = struct.unpack('>f', struct.pack('>f', value))[0]
    if not math.isfinite(rounded):
        raise ValueError('Nonfinite terrain bound')
    if (upper and rounded >= value) or (not upper and rounded <= value):
        return rounded
    if rounded == 0:
        bits = 1 if upper else 0x80000001
    else:
        bits = struct.unpack('>I', struct.pack('>f', rounded))[0]
        bits += 1 if (rounded > 0) == upper else -1
    return struct.unpack('>f', struct.pack('>I', bits))[0]


def surface_bounds(coefficients):
    """Enclose the entire surface using its Bezier control-point convex hull.

    A finite grid misses extrema between samples. Convert the reversed power
    coefficients to Bernstein form; its convex hull contains every point on
    u,v in [0,1], including crests between the old 1/8-grid samples.
    """
    if len(coefficients) != 16 or not all(math.isfinite(x) for p in coefficients for x in p):
        raise ValueError('Expected 16 finite terrain coefficient vectors')
    c = list(reversed(coefficients))
    weights = [[math.comb(i, k) / math.comb(3, k) for k in range(i + 1)] for i in range(4)]
    controls = [tuple(math.fsum(c[r * 4 + s][axis] * weights[i][r] * weights[j][s]
                               for r in range(i + 1) for s in range(j + 1))
                      for axis in range(3)) for i in range(4) for j in range(4)]
    low = [outward_float32(min(p[k] for p in controls), False) for k in range(3)]
    high = [outward_float32(max(p[k] for p in controls), True) for k in range(3)]
    return low, high
