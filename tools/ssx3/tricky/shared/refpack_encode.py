"""Small deterministic 10FB encoder for bounded SSX 3 block experiments.

LZ matching with one-byte lookahead; does not reproduce EA's compressor byte for byte.
The caller must verify decoded equality and enforce the original block capacity.
"""
from collections import defaultdict, deque


def encode(data, candidates=64):
    if len(data) > 0xffffff:
        raise ValueError('10FB supports at most 24-bit output lengths')
    if candidates < 1:
        raise ValueError('At least one match candidate is required')
    data = bytes(data)
    out = bytearray(b'\x10\xfb' + len(data).to_bytes(3, 'big'))
    positions = defaultdict(lambda: deque(maxlen=candidates))
    pos = pending = 0

    def remember(start, end):
        for i in range(start, min(end, len(data)-2)):
            positions[data[i:i+3]].append(i)

    def literals(start, end):
        # Literal-only commands encode multiples of four, up to 112 bytes.
        # Leave up to three bytes for the following match/stop command.
        while end - start >= 4:
            n = min(112, (end-start) & ~3)
            out.append(0xe0 + n//4 - 1)
            out.extend(data[start:start+n])
            start += n
        return start

    def match(at):
        length = distance = 0
        if at + 3 <= len(data):
            limit = min(1028, len(data)-at)
            for previous in reversed(positions.get(data[at:at+3], ())):
                dist = at - previous
                if dist > 131072:
                    break
                n = 3
                # Skip a candidate that cannot improve the current longest match.
                if length and data[previous:previous+length] != data[at:at+length]:
                    continue
                while n < limit and data[previous+n] == data[at+n]:
                    n += 1
                minimum = 3 if dist <= 1024 else 4 if dist <= 16384 else 5
                if n >= minimum and n > length:
                    length, distance = n, dist
                    if n == limit:
                        break
        return length, distance

    while pos < len(data):
        length, distance = match(pos)
        # A slightly later, longer match often avoids an extra command. SSX's
        # original blocks are nearly full, so pure greedy parsing is insufficient.
        if length and length < 1028:
            next_length, _ = match(pos+1)
            if next_length > length+1:
                length = 0
        if not length:
            remember(pos, pos+1)
            pos += 1
            continue
        pending = literals(pending, pos)
        prefix = pos - pending
        offset = distance - 1
        if length <= 10 and distance <= 1024:
            out.extend((prefix | ((length-3) << 2) | ((offset >> 8) << 5), offset & 255))
        elif length <= 67 and distance <= 16384:
            out.extend((0x80 | (length-4), (prefix << 6) | (offset >> 8), offset & 255))
        else:
            out.extend((0xc0 | prefix | (((length-5) >> 8) << 2) | ((offset >> 16) << 4),
                        (offset >> 8) & 255, offset & 255, (length-5) & 255))
        out.extend(data[pending:pos])
        remember(pos, pos+length)
        pos += length
        pending = pos
    pending = literals(pending, len(data))
    out.append(0xfc | (len(data)-pending))
    out.extend(data[pending:])
    return bytes(out)
