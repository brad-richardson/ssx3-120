#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""tkp4_speech.py: TKP4 merged speech composite (TK25c rider members + TK43b meter members).

Subcommands (read-only on inputs; writes only the named outputs):
  materialize <iso> <composite> <out-big>
      Resolve a PS2XCMP1 speech descriptor against the ISO -> full patched BIG.
  extract <big> <member-path> <out>
      Extract one BIGF member (e.g. 'data\\speech\\char\\Arcade_Uber.dat').
  merge <iso> <members-dir> <out-composite> <t1.dat> [<t2.dat> ...]
      Splice the named members into stock SPEECH.BIG, emit a PS2XCMP1 composite.
      Same splice/diff/verify semantics as TK25c's tk25c_composite.py (fatal unless
      every member is size-equal to stock and the descriptor reproduces the BIG).

TKP4 merge set (9): the 6 TK25c rider members (extracted from the shipped
speech-tricky-big, levels untouched) + TK43b's 3 meter members. The displaced
TK25c Arcade_Uber chatter is replaced by TK43b's Arcade_Uber (Brad: "take them
back"); the chatter line itself survives as TK43b Icons slot 3.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

SEC = 2048
sha = lambda b: hashlib.sha256(b).hexdigest()
CHAR = "data\\speech\\char\\"


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


def parse_desc(blob):
    text = blob[:SEC].split(b"\0")[0].decode()
    lines = text.splitlines()
    assert lines[0] == "PS2XCMP1", lines[0]
    size = int(lines[1].split()[1])
    segs = []
    for ln in lines[3:]:
        if ln == "end":
            break
        k, x, n = ln.split()
        segs.append((k, int(x), int(n)))
    return size, segs


def cmd_materialize(a):
    blob = Path(a.composite).read_bytes()
    size, segs = parse_desc(blob)
    rebuilt = bytearray()
    with open(a.iso, "rb") as f:
        for k, x, n in segs:
            if k == "self":
                rebuilt += blob[x:x + n * SEC]
            else:
                f.seek(x * SEC)
                rebuilt += f.read(n * SEC)
    out = bytes(rebuilt[:size])
    Path(a.out).write_bytes(out)
    print(json.dumps(dict(composite=a.composite, composite_sha256=sha(blob),
                           out=a.out, out_bytes=len(out), out_sha256=sha(out),
                           segments=len(segs)), indent=1))


def cmd_extract(a):
    with open(a.big, "rb") as f:
        f.seek(0)
        members = big_members(f, 0)
        if a.member not in members:
            cands = [m for m in members if m.endswith(a.member.split("\\")[-1])]
            raise SystemExit("no member %r (same-name: %r)" % (a.member, cands))
        off, sz = members[a.member]
        f.seek(off)
        data = f.read(sz)
    Path(a.out).write_bytes(data)
    print(json.dumps(dict(member=a.member, offset=off, size=sz,
                           out=a.out, out_sha256=sha(data)), indent=1))


def cmd_merge(a):
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
    for t in a.targets:
        want = CHAR + t
        if want not in members:
            raise SystemExit("no BIG member %r" % want)
        off, sz = members[want]
        blob = Path(a.members, t).read_bytes()
        assert len(blob) == sz, (t, len(blob), sz)
        patched[off:off + sz] = blob
        replaced.append((t, off, sz))
    patched = bytes(patched)
    assert len(patched) == len(stock)
    nsec = (len(stock) + SEC - 1) // SEC
    pad = nsec * SEC - len(stock)
    with open(a.iso, "rb") as f:
        f.seek(base + len(stock))
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
    Path(a.out).write_bytes(blob)
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
    print(json.dumps(dict(
        iso=a.iso, image_sectors=image_sectors, speech_lbn=lbn, speech_sha256=sha(stock),
        speech_bytes=len(stock), patched_sha256=sha(patched),
        members={t: {"offset": o, "size": s} for t, o, s in replaced},
        changed_sectors=sum(1 for k in kinds if k), total_sectors=nsec,
        segments=[list(s) for s in desc_segs],
        out=a.out, out_bytes=len(blob), out_sha256=sha(blob),
        descriptor_reproduces_big=True), indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("materialize")
    m.add_argument("iso"); m.add_argument("composite"); m.add_argument("out")
    e = sub.add_parser("extract")
    e.add_argument("big"); e.add_argument("member"); e.add_argument("out")
    g = sub.add_parser("merge")
    g.add_argument("iso"); g.add_argument("members"); g.add_argument("out")
    g.add_argument("targets", nargs="+")
    a = ap.parse_args()
    {"materialize": cmd_materialize, "extract": cmd_extract, "merge": cmd_merge}[a.cmd](a)


if __name__ == "__main__":
    main()
