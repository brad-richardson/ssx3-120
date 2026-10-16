#!/usr/bin/env python3
"""tk41_composite.py <src.iso> <disc-path> <out-composite> <member=blob>... [--materialized <patched>]

TK41: rebuild a stock BIGF (same total size, rewritten directory) with replaced
members, then emit a TK10 PS2XCMP1 composite (iso segments for unchanged
sectors, self for changed). Members that fit keep their offsets (zero-padded);
grown members allocate from slack freed by shrunk members (16-aligned).
Fails loudly if the replacements don't fit the stock total.

Verification (fatal on failure): resolving the descriptor against the ISO
reproduces the patched BIG byte-for-byte (incl. the disc tail past EOF).
"""
import hashlib
import json
import struct
import sys
from pathlib import Path

SEC = 2048
sha = lambda b: hashlib.sha256(b).hexdigest()


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


def big_members(buf):
    assert buf[:4] in (b"BIGF", b"BIG4"), buf[:4]
    n = struct.unpack(">I", buf[8:12])[0]
    hl = struct.unpack(">I", buf[12:16])[0]
    d = buf[16:16 + hl]
    i = 0
    out = []
    for _ in range(n):
        off, sz = struct.unpack_from(">II", d, i)
        i += 8
        e = d.index(b"\0", i)
        out.append([d[i:e].decode("latin1"), off, sz])
        i = e + 1
    return out, 16 + hl


def main():
    a = sys.argv[1:]
    try:
        mi = a.index("--materialized")
        materialized = a[mi + 1]
        del a[mi:mi + 2]
    except ValueError:
        materialized = None
    iso, disc, out = a[0], a[1], a[2]
    repl = {}
    for spec in a[3:]:
        name, blob = spec.split("=", 1)
        repl[name] = Path(blob).read_bytes()
    image_sectors = Path(iso).stat().st_size // SEC
    with open(iso, "rb") as f:
        lbn, size = iso_find(f, disc)
        f.seek(lbn * SEC)
        stock = f.read(size)
    print(f"{disc} LBN={lbn} size={size} sectors={(size + SEC - 1) // SEC}")
    members, data_start = big_members(stock)
    byname = {m[0]: m for m in members}
    for name in repl:
        assert name in byname, f"not a member: {name}"
    # Pool all replaced members' old slots + gaps; place largest-first.
    used = bytearray(size)
    for name, off, sz in members:
        if name not in repl:
            used[off:off + sz] = b"\x01" * sz
    free = []
    # TKP4b: MUSIC.BIG's dir overruns 15 bytes into bep.mpf's home (data_start
    # 2191 > home 2176; the tail bytes serve double duty as bep's head). Start
    # the free scan at the lowest replaced home so home slots inside the dir
    # tail stay placeable; real dir bytes (< that) are never allocated.
    scan_from = min(data_start, min(byname[name][1] for name in repl))
    i = scan_from
    while i < size:
        if used[i]:
            i += 1
            continue
        j = i
        while j < size and not used[j]:
            j += 1
        free.append([i, j - i])
        i = j
    print(f"free runs: {len(free)}, bytes={sum(r[1] for r in free)}")
    alloc = {}  # name -> (off, bytes)
    for name in sorted(repl, key=lambda n: -len(repl[n])):
        nb = repl[name]
        need = (len(nb) + 15) & ~15
        pick = None
        # Prefer the member's own slot when the replacement fits: same
        # offsets keep the dir (and any consumer caching it) valid.
        home, homesz = byname[name][1], byname[name][2]
        if (home + 15) & ~15 == home and len(nb) <= homesz:
            for r in free:
                if r[0] <= home and home + len(nb) <= r[0] + r[1]:
                    pick = (r, home)
                    need = len(nb)  # exact fit at home; tail stays free
                    break
        if pick is None:
            for r in free:
                start = (r[0] + 15) & ~15
                if r[0] + r[1] - start >= need:
                    pick = (r, start)
                    break
        if pick is None:
            raise SystemExit(
                f"no room for {name} ({len(nb)} bytes, "
                f"free={sum(r[1] for r in free)})")
        r, start = pick
        alloc[name] = (start, nb)
        r0, rl = r
        rend = r0 + rl
        r[0], r[1] = r0, start - r0
        if start + need < rend:
            free.append([start + need, rend - (start + need)])
        used[start:start + need] = b"\x01" * need
    patched = bytearray(stock)
    # Slack keeps stock bytes (game reads only dir sizes; minimal diff).
    newdir = {}
    for name, off, sz in members:
        if name in repl:
            noff, nbytes = alloc[name]
            newdir[name] = (noff, len(repl[name]))
        else:
            newdir[name] = (off, sz)
    # rewrite directory (same order, same names/sizes field widths)
    n = struct.unpack(">I", stock[8:12])[0]
    hl = struct.unpack(">I", stock[12:16])[0]
    d = bytearray(stock[16:16 + hl])
    i = 0
    for _ in range(n):
        e = d.index(b"\0", i + 8)
        name = d[i + 8:e].decode("latin1")
        struct.pack_into(">II", d, i, *newdir[name])
        i = e + 1
    patched[16:16 + hl] = d
    # TKP4b: member bytes AFTER the dir rewrite, so members overlapping the
    # dir tail (bep.mpf @2176 < data_start 2191) keep their head bytes.
    for name in repl:
        noff, nbytes = alloc[name]
        patched[noff:noff + len(nbytes)] = nbytes
    patched = bytes(patched)
    assert len(patched) == len(stock)
    print("replaced:")
    for name in repl:
        o, s = byname[name][1], byname[name][2]
        no, ns = newdir[name]
        print(f"  {name}: ({o},{s}) -> ({no},{ns})")
    # sector-diff + PS2XCMP1 (same as TK25c)
    nsec = (len(stock) + SEC - 1) // SEC
    pad = nsec * SEC - len(stock)
    with open(iso, "rb") as f:
        f.seek(lbn * SEC + len(stock))
        tail = f.read(pad)
        assert len(tail) == pad
    stock_p = stock + tail
    patched_p = patched + tail
    kinds = [stock_p[s * SEC:(s + 1) * SEC] != patched_p[s * SEC:(s + 1) * SEC]
             for s in range(nsec)]
    segs = []
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
    Path(out).write_bytes(blob)
    if materialized:
        Path(materialized).write_bytes(patched)
    rebuilt = bytearray()
    with open(iso, "rb") as f:
        for k, x, n in desc_segs:
            if k == "self":
                rebuilt += blob[x:x + n * SEC]
            else:
                f.seek(x * SEC)
                rebuilt += f.read(n * SEC)
    assert bytes(rebuilt[:len(patched)]) == patched, "resolve mismatch"
    assert bytes(rebuilt[len(patched):]) == tail, "tail mismatch"
    print(json.dumps(dict(
        disc=disc, lbn=lbn, bytes=len(stock),
        stock_sha256=sha(stock), patched_sha256=sha(patched),
        changed_sectors=sum(1 for k in kinds if k), total_sectors=nsec,
        out=out, out_bytes=len(blob), out_sha256=sha(blob),
        descriptor_reproduces_big=True), indent=1))


if __name__ == "__main__":
    main()
