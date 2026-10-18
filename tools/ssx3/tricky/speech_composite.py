#!/usr/bin/env python3
"""tk25c_composite.py <src.iso> <members-dir> <out-composite> [--materialized <patched-big>]

TK25c: splice the 7 loudness-matched .dat members into a copy of the stock
SPEECH.BIG (read from the pinned ISO), then emit a TK10 PS2XCMP1 composite:
`iso` segments for unchanged sector ranges, `self` for ranges with replaced
bytes. Same size as stock (required by the mode alias). Prints a JSON receipt.

Verification (fatal on failure):
  - each member size equals the stock member size;
  - resolving the descriptor against the ISO reproduces the patched BIG byte
    for byte (emulates ps2_cd_overlay::resolve).
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

SEC = 2048
sha = lambda b: hashlib.sha256(b).hexdigest()

TARGETS = ["Wipeout_Amateur_mac.dat", "Wipeout_Pro_mac.dat", "Wipeout_Expert_mac.dat",
           "Arcade_Uber.dat", "Big_Air_Amateur_mac.dat", "Big_Air_Pro_mac.dat",
           "Big_Air_Expert_mac.dat", "Arcade_Power_Ups.dat", "Arcade_Icons.dat"]

def iso_find(f, path):
    f.seek(16 * SEC + 156)
    rec = f.read(34)
    lba, size = struct.unpack_from("<I", rec, 2)[0], struct.unpack_from("<I", rec, 10)[0]
    for part in path.strip("/").upper().split("/"):
        f.seek(lba * SEC)
        d = f.read(size)
        i = 0
        found = None
        while i < len(d):
            l = d[i]
            if l == 0:
                i = (i // SEC + 1) * SEC
                continue
            nl = d[i + 32]
            n = d[i + 33:i + 33 + nl].decode("latin1").split(";")[0]
            if n.upper() == part:
                found = (struct.unpack_from("<I", d, i + 2)[0],
                         struct.unpack_from("<I", d, i + 10)[0])
                break
            i += l
        if not found:
            raise SystemExit("not found " + part)
        lba, size = found
    return lba, size

def big_members(f, base):
    f.seek(base)
    h = f.read(16)
    assert h[:4] in (b"BIGF", b"BIG4"), h[:4]
    n = struct.unpack(">I", h[8:12])[0]
    hl = struct.unpack(">I", h[12:16])[0]
    f.seek(base + 16)
    d = f.read(hl)
    i = 0
    out = {}
    for _ in range(n):
        off, sz = struct.unpack_from(">II", d, i)
        i += 8
        e = d.index(b"\0", i)
        out[d[i:e].decode("latin1")] = (off, sz)
        i = e + 1
    return out

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("iso")
    ap.add_argument("members")
    ap.add_argument("out")
    ap.add_argument("--materialized", default=None)
    a = ap.parse_args()
    iso_path = Path(a.iso)
    image_sectors = iso_path.stat().st_size // SEC
    with open(a.iso, "rb") as f:
        lbn, size = iso_find(f, "DATA/AUDIO/SPEECH.BIG")
        base = lbn * SEC
        members = big_members(f, base)
        f.seek(base)
        stock = f.read(size)
    print(f"SPEECH.BIG LBN={lbn} size={size} sectors={(size + SEC - 1) // SEC}")
    patched = bytearray(stock)
    replaced = []
    for t in TARGETS:
        want = "data\\speech\\char\\" + t
        off, sz = members[want]
        blob = Path(a.members, t).read_bytes()
        assert len(blob) == sz, (t, len(blob), sz)
        patched[off:off + sz] = blob
        replaced.append((t, off, sz))
    patched = bytes(patched)
    assert len(patched) == len(stock)
    # Sector-diff on padded buffers. The file need not be sector-multiple:
    # the last sector's tail past the file size belongs to whatever follows
    # on disc, so it is copied from the disc into both buffers (a replaced
    # member can never extend past the file size, so the tail is unchanged).
    nsec = (len(stock) + SEC - 1) // SEC
    pad = nsec * SEC - len(stock)
    with open(a.iso, "rb") as f:
        f.seek(base + len(stock))
        tail = f.read(pad)
        assert len(tail) == pad
    stock_p = stock + tail
    patched_p = patched + tail
    kinds = []  # per sector: True = changed (self), False = stock (iso)
    for s in range(nsec):
        kinds.append(stock_p[s * SEC:(s + 1) * SEC] != patched_p[s * SEC:(s + 1) * SEC])
    segs = []  # (kind, first_sector, count)
    for s, changed in enumerate(kinds):
        if segs and segs[-1][0] == changed:
            segs[-1] = (changed, segs[-1][1], segs[-1][2] + 1)
        else:
            segs.append((changed, s, 1))
    host = bytearray()
    desc_segs = []
    for changed, first, count in segs:
        if changed:
            off = SEC + len(host)
            assert off % SEC == 0
            host += patched_p[first * SEC:(first + count) * SEC]
            desc_segs.append(("self", off, count))
        else:
            desc_segs.append(("iso", lbn + first, count))
    text = (f"PS2XCMP1\nsize {len(patched)}\nimage {image_sectors}\n" +
            "".join(f"{k} {x} {n}\n" for k, x, n in desc_segs) + "end\n")
    assert len(text) <= SEC, len(text)
    blob = text.encode() + bytes(SEC - len(text)) + bytes(host)
    Path(a.out).write_bytes(blob)
    if a.materialized:
        Path(a.materialized).write_bytes(patched)
    # verify: resolve the descriptor against the ISO == patched bytes
    rebuilt = bytearray()
    with open(a.iso, "rb") as f:
        for k, x, n in desc_segs:
            if k == "self":
                rebuilt += blob[x:x + n * SEC]
            else:
                f.seek(x * SEC)
                rebuilt += f.read(n * SEC)
    assert bytes(rebuilt[:len(patched)]) == patched, "descriptor does not reproduce the patched BIG"
    assert bytes(rebuilt[len(patched):]) == tail, "disc tail not preserved"
    changed_sec = sum(1 for k in kinds if k)
    print(json.dumps(dict(
        iso=a.iso, image_sectors=image_sectors, speech_lbn=lbn, speech_sha256=sha(stock),
        speech_bytes=len(stock), patched_sha256=sha(patched),
        members={t: {"offset": o, "size": s} for t, o, s in replaced},
        changed_sectors=changed_sec, total_sectors=nsec,
        segments=[list(s) for s in desc_segs],
        out=a.out, out_bytes=len(blob), out_sha256=sha(blob),
        descriptor_reproduces_big=True), indent=1))

if __name__ == "__main__":
    main()
