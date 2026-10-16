#!/usr/bin/env python3
"""Optimal-parse 10FB RefPack encoder for fixed-capacity SSB blocks.

The greedy encoder in refpack_encode.py is about 0.2% larger than EA's output, which
matters because EA's packer fills most 32 KiB blocks to within a few bytes of capacity.
This encoder chooses the command sequence with minimum total size by dynamic
programming over the 10FB command forms:

  2 bytes: length 3..10,   distance <= 1024,   0..3 prefix literals
  3 bytes: length 4..67,   distance <= 16384,  0..3 prefix literals
  4 bytes: length 5..1028, distance <= 131072, 0..3 prefix literals
  1 byte:  literal-only run of 4..112 bytes (multiple of four)
  1 byte:  stop with 0..3 trailing literals

Match candidates come from hash chains on three-byte keys; for each position the
longest match within each distance class is kept. Output is verified by the caller
with the independent decoder. Deterministic; no randomness.
"""
from collections import defaultdict, deque

MAX_LEN = 1028
MAX_DIST = 131072


def _extend(data, a, b, limit):
    """Length of the common prefix of data[a:] and data[b:], at most limit (a < b)."""
    n = 0
    while n < limit:
        k = min(64, limit - n)
        if data[a + n:a + n + k] == data[b + n:b + n + k]:
            n += k
            continue
        for t in range(k):
            if data[a + n + t] != data[b + n + t]:
                return n + t
        return n + k
    return n


def find_matches(data, candidates=128, skip_threshold=64):
    """For each position, a list of (length, distance) candidates: the longest match in each
    distance class (<=1024, <=16384, <=131072). Positions inside a long match reuse the
    match found at its start instead of searching, which keeps the cost near linear."""
    n = len(data)
    chains = defaultdict(lambda: deque(maxlen=candidates))
    result = [()] * n
    pos = 0
    carry = None  # (length, distance) continuing from a long match
    while pos < n - 2:
        key = data[pos:pos + 3]
        if carry and carry[0] >= 3:
            best = {}
            L, D = carry
            cls = 0 if D <= 1024 else 1 if D <= 16384 else 2
            best[cls] = (min(L, MAX_LEN), D)
            result[pos] = tuple(best.values())
            carry = (L - 1, D)
            chains[key].append(pos)
            pos += 1
            continue
        best = {}
        limit = min(MAX_LEN, n - pos)
        for prev in reversed(chains.get(key, ())):
            dist = pos - prev
            if dist > MAX_DIST:
                break
            cls = 0 if dist <= 1024 else 1 if dist <= 16384 else 2
            if cls in best and best[cls][0] >= limit:
                continue
            length = _extend(data, prev, pos, limit)
            minimum = (3, 4, 5)[cls]
            if length >= minimum and (cls not in best or length > best[cls][0]):
                best[cls] = (length, dist)
        # a long near match also serves the farther classes' minimums; keep as found
        result[pos] = tuple(best.values())
        chains[key].append(pos)
        longest = max((v for v in best.values()), default=None)
        if longest and longest[0] >= skip_threshold:
            carry = (longest[0] - 1, longest[1])
        pos += 1
    return result


def _cost(length, distance):
    if length <= 10 and distance <= 1024:
        return 2
    if length <= 67 and distance <= 16384:
        return 3
    return 4


def encode(data, candidates=128, skip_threshold=64):
    data = bytes(data)
    n = len(data)
    if n > 0xffffff:
        raise ValueError('10FB supports at most 24-bit output lengths')
    matches = find_matches(data, candidates, skip_threshold)
    INF = float('inf')
    cost = [INF] * (n + 1)
    choice = [None] * (n + 1)
    cost[n] = 1  # stop command with no literals
    for i in range(n - 1, -1, -1):
        best, pick = INF, None
        rem = n - i
        if rem <= 3:
            best, pick = 1 + rem, ('stop', rem)
        # literal-only runs
        k = 4
        while k <= 112 and k <= rem:
            c = 1 + k + cost[i + k]
            if c < best:
                best, pick = c, ('lit', k)
            k += 4
        # prefix literals then a match
        for p in range(0, 4):
            j = i + p
            if j >= n:
                break
            for length, dist in matches[j]:
                if j + length > n:
                    length = n - j
                minimum = 3 if dist <= 1024 else 4 if dist <= 16384 else 5
                if length < minimum:
                    continue
                # Every legal length for short matches; for long ones the full length,
                # the cheaper-form boundaries, and a few shorter lengths.
                if length <= 16:
                    lengths = range(minimum, length + 1)
                else:
                    lengths = {length, length - 1, length - 2, length - 3, 10, 67, 16} - set(range(0, minimum))
                for L in lengths:
                    if L > length:
                        continue
                    c = p + _cost(L, dist) + cost[j + L]
                    if c < best:
                        best, pick = c, ('match', p, L, dist)
        cost[i], choice[i] = best, pick
    # emit
    out = bytearray(b'\x10\xfb' + n.to_bytes(3, 'big'))
    i = 0
    while True:
        pick = choice[i] if i < n else ('stop', 0)
        if pick[0] == 'stop':
            rem = pick[1]
            out.append(0xfc | rem)
            out.extend(data[i:i + rem])
            break
        if pick[0] == 'lit':
            k = pick[1]
            out.append(0xe0 + k // 4 - 1)
            out.extend(data[i:i + k])
            i += k
            continue
        _, p, L, dist = pick
        offset = dist - 1
        if L <= 10 and dist <= 1024:
            out.extend((p | ((L - 3) << 2) | ((offset >> 8) << 5), offset & 255))
        elif L <= 67 and dist <= 16384:
            out.extend((0x80 | (L - 4), (p << 6) | (offset >> 8), offset & 255))
        else:
            out.extend((0xc0 | p | (((L - 5) >> 8) << 2) | ((offset >> 16) << 4),
                        (offset >> 8) & 255, offset & 255, (L - 5) & 255))
        out.extend(data[i:i + p])
        i += p + L
    return bytes(out)


if __name__ == '__main__':
    import sys, time
    from probe_worlds import refpack
    from refpack_encode import encode as greedy
    raw = open(sys.argv[1], 'rb').read()
    t = time.time(); a = encode(raw); ta = time.time() - t
    t = time.time(); b = greedy(raw); tb = time.time() - t
    assert refpack(a)[0] == raw
    print(f'input {len(raw)} optimal {len(a)} ({ta:.1f}s) greedy {len(b)} ({tb:.1f}s)')
