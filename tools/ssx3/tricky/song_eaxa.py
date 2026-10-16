#!/usr/bin/env python3
"""AU2: reference decoder for SSX 3 SCHl streams (codec byte 0x0A), used to
check whether bytes the EE hands to the IOP are compressed stream data or
decoded PCM.

Layout (from the disc bytes, AU2 REPORT): SCDl block = 'SCDl', u32 size,
u32 samples, u32 offset[ch] (relative to the end of the offset table); each
channel starts with 2 BE16 history samples (4 bytes), then 15-byte frames of 28 samples: byte0 = coef index (hi nibble) |
shift (lo nibble), 14 bytes of 4-bit samples (high nibble first). byte0 ==
0xEE marks a raw frame (2 BE16 history words + 28 BE16 samples). Filters =
the EE table at 0x44E890: coef1 {0, 240, 460, 392}/256, coef2 {0, 0, -208,
-220}/256.

Usage:
  au2_eaxa.py decode <BIG> <entry> <seconds> <out.wav>
  au2_eaxa.py blocks <BIG> <entry> <nblocks> <out.bin>   (raw SCDl blocks)
"""
import struct, sys, wave

C1 = (0, 240, 460, 392)
C2 = (0, 0, -208, -220)


def entry(big, name):
    f = open(big, "rb"); h = f.read(16); n, ds = struct.unpack(">II", h[8:16]); d = f.read(ds); q = 0
    for _ in range(n):
        o, s = struct.unpack(">II", d[q:q + 8]); q += 8; e = d.index(b"\0", q); nm = d[q:e].decode(); q = e + 1
        if nm.endswith(name):
            f.seek(o); return f.read(s)
    raise SystemExit(f"{name} not in {big}")


def header(b):
    # SCHl: 'PT' platform block of tagged fields; return (channels, rate)
    ch, rate = 1, 22050
    p = 8 + 4
    while p < len(b):
        t = b[p]; p += 1
        if t == 0xFF: break
        if t in (0xFC, 0xFD, 0xFE): continue
        n = b[p]; p += 1; v = int.from_bytes(b[p:p + n], "big"); p += n
        if t == 0x82: ch = v
        if t == 0x84: rate = v
    return ch, rate


def frame(buf, o, hist):
    h1, h2 = hist
    fi = buf[o]
    out = []
    if fi == 0xEE:
        h1, h2 = struct.unpack_from(">hh", buf, o + 1)
        out = list(struct.unpack_from(">28h", buf, o + 5))
        return out, (out[-1], out[-2]), 61
    c1, c2, sh = C1[fi >> 4 & 3], C2[fi >> 4 & 3], (fi & 15) + 8
    for i in range(28):
        byte = buf[o + 1 + i // 2]
        nib = (byte >> 4) if i % 2 == 0 else (byte & 15)
        s = ((nib << 28) - ((nib & 8) << 29)) >> sh
        s = (s + c1 * h1 + c2 * h2 + 128) >> 8
        s = max(-32768, min(32767, s))
        out.append(s); h2, h1 = h1, s
    return out, (h1, h2), 15


def decode(b, max_samples):
    ch, rate = header(b)
    pcm = [[] for _ in range(ch)]; hist = [(0, 0)] * ch
    p = 0
    while p + 8 <= len(b) and len(pcm[0]) < max_samples:
        tag, ln = b[p:p + 4], struct.unpack_from("<I", b, p + 4)[0]
        if not tag.startswith(b"SC") or ln == 0:  # padding between segments
            nxt = b.find(b"SCHl", p + 4)
            if nxt < 0: break
            p = nxt; continue
        if tag == b"SCHl":
            hist = [(0, 0)] * ch
        if tag == b"SCDl":
            ns = struct.unpack_from("<I", b, p + 8)[0]
            offs = struct.unpack_from(f"<{ch}I", b, p + 12)
            base = p + 12 + 4 * ch
            for c in range(ch):
                o = base + offs[c]; got = 0
                hist[c] = struct.unpack_from(">hh", b, o); o += 4  # per-channel history seed
                while got < ns:
                    s, hist[c], used = frame(b, o, hist[c]); o += used
                    pcm[c].extend(s[:ns - got]); got += len(s[:ns - got])
        p += ln
    return ch, rate, pcm


if __name__ == "__main__":
    mode, big, name = sys.argv[1:4]
    b = entry(big, name)
    if mode == "decode":
        secs, out = float(sys.argv[4]), sys.argv[5]
        ch, rate, pcm = decode(b, int(secs * 32000))
        n = min(len(x) for x in pcm)
        w = wave.open(out, "wb"); w.setnchannels(ch); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack(f"<{ch}h", *(pcm[c][i] for c in range(ch))) for i in range(n)))
        w.close()
        peak = max(max(abs(v) for v in x[:n]) for x in pcm)
        print(f"{name}: ch={ch} rate={rate} samples={n} peak={peak} -> {out}")
    else:
        nb, out = int(sys.argv[4]), sys.argv[5]
        p = 0; blocks = []
        while p + 8 <= len(b) and len(blocks) < nb:
            tag, ln = b[p:p + 4], struct.unpack_from("<I", b, p + 4)[0]
            if tag == b"SCDl": blocks.append(b[p:p + ln])
            if ln == 0: break
            p += ln
        open(out, "wb").write(b"".join(blocks)); print(f"{len(blocks)} blocks -> {out}")
