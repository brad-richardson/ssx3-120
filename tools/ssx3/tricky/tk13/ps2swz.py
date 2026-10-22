# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK13: PS2 PSMT8 <-> PSMCT32 (un)swizzle of 8-bit texel data (the widespread 'swizzle8' mapping), and CLUT CSM1 order."""


def swz_index(x, y, w):
    block = (y & ~0xf) * w + (x & ~0xf) * 2
    swap = (((y + 2) >> 2) & 1) * 4
    py = (((y & ~3) >> 1) + (y & 1)) & 7
    column = py * w * 2 + ((x + swap) & 7) * 4
    byte = ((y >> 1) & 1) + ((x >> 2) & 2)
    return block + column + byte


def unswizzle8(buf, w, h):
    out = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            out[y * w + x] = buf[swz_index(x, y, w)]
    return bytes(out)


def swizzle8(buf, w, h):
    out = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            out[swz_index(x, y, w)] = buf[y * w + x]
    return bytes(out)


def csm1(i):
    return (i & 0xE7) | ((i & 0x10) >> 1) | ((i & 0x08) << 1)
