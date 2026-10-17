#!/usr/bin/env python3
"""patch_music_inf.py: retitle the music-pool slots in DATA/CONFIG/MUSIC.INF.

The race-start song banner shows, per playing pool slot, the station brand
("EA RADIO BIG", guest rodata) plus the slot section's TITLE and ARTIST from
the stock music-metadata text DATA/CONFIG/MUSIC.INF (tk-banner-1: banner
"EA RADIO BIG / Buffet of Breaks / John Morgan" over pool B2/buffet, whose
section carries MUSDATA "buffet.mus"). The brand line is handled separately
(manifest poke); this tool fixes the song lines so Tricky races name the
Tricky song staged into each slot (music-pool-default.tsv) with the
title/artist from Tricky's own disc (music-banner-default.tsv).

Only the 35 pool-slot sections' TITLE/ARTIST/ALBUM quoted values change
(ALBUM is cleared: the banner falls back to ALBUM when ARTIST is empty, so
a stock ALBUM like "SSX 3" would leak through); every other byte (headers,
keys, order, CRLF, menu/hub sections) is preserved, and the output is
padded back to the exact stock size with a trailing "#" comment so the
PS2XCMP1 alias serves a same-size file. Fatal on any mismatch (slot without
section, section without TITLE/ARTIST/ALBUM, size drift).

Usage:
  patch_music_inf.py --ssx3-iso <SSX3.iso> --map-tsv <music-banner-default.tsv>
      --out-composite <music-inf-tricky-big> [--materialized <patched.inf>]
Only the named outputs are written; inputs are read-only. Game bytes never
enter git: this script is deterministic from the pinned ISO + map TSV.

Merge semantics (sector diff, PS2XCMP1 emit, reproduce check) mirror
music_composite.py; the composite text layout is identical (descriptor
sector NUL-padded to 2048, self offsets >= 2048).
"""
import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

SEC = 2048
DISC = "DATA/CONFIG/MUSIC.INF"
sha = lambda b: hashlib.sha256(b).hexdigest()


def iso_find(f, path):
    f.seek(16 * SEC + 156)
    rec = f.read(34)
    lba, size = struct.unpack_from("<I", rec, 2)[0], struct.unpack_from("<I", rec, 10)[0]
    for part in path.strip("/").upper().split("/"):
        f.seek(lba * SEC)
        d = f.read(size)
        i, found = 0, None
        while i < len(d):
            ln = d[i]
            if ln == 0:
                i = (i // SEC + 1) * SEC
                continue
            nl = d[i + 32]
            n = d[i + 33:i + 33 + nl].decode("latin1").split(";")[0]
            if n.upper() == part:
                found = (struct.unpack_from("<I", d, i + 2)[0],
                         struct.unpack_from("<I", d, i + 10)[0])
                break
            i += ln
        assert found, path
        lba, size = found
    return lba, size


def load_map(tsv_path):
    rows = []
    for line in Path(tsv_path).read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        assert len(parts) in (3, 4), line
        slot = parts[0]
        assert re.fullmatch(r"B[12]/[A-Za-z0-9]+", slot), line
        title = parts[2]
        artist = parts[3] if len(parts) == 4 else ""
        assert title and '"' not in title and "\r" not in title and "\n" not in title, line
        assert '"' not in artist and "\r" not in artist and "\n" not in artist, line
        assert len(title.encode()) <= 64 and len(artist.encode()) <= 64, line
        mus = "mxpxsong.mus" if slot == "B1/mxpx" else slot.split("/")[1] + ".mus"
        rows.append((mus, title, artist))
    assert len(rows) == 35, len(rows)
    assert len({m for m, _, _ in rows}) == 35, "duplicate MUSDATA"
    return rows


def patch_inf(stock, mapping):
    # Two passes: sections carry TITLE/ARTIST lines BEFORE their MUSDATA
    # line, so the owning MUSDATA is only known after the whole section.
    lines = stock.decode("latin1").split("\r\n")
    want = {mus: (title, artist) for mus, title, artist in mapping}
    title_re = re.compile(r'^([ \t]*TITLE[ \t]*=[ \t]*)"[^"\r\n]*"(.*)$')
    artist_re = re.compile(r'^([ \t]*ARTIST[ \t]*=[ \t]*)"[^"\r\n]*"(.*)$')
    album_re = re.compile(r'^([ \t]*ALBUM[ \t]*=[ \t]*)"[^"\r\n]*"(.*)$')
    mus_re = re.compile(r'^[ \t]*MUSDATA[ \t]*=[ \t]*"([^"\r\n]*)"')
    section_re = re.compile(r"^#### |^\[")
    bounds, cur = [], None
    for i, line in enumerate(lines):
        if section_re.match(line):
            if cur is not None:
                bounds.append((cur, i))
            cur = i
    if cur is not None:
        bounds.append((cur, len(lines)))
    out = list(lines)
    seen = set()
    for start, end in bounds:
        mus = None
        for i in range(start, end):
            m = mus_re.match(lines[i])
            if m:
                mus = m.group(1)
                break
        if mus not in want:
            continue
        title, artist = want[mus]
        for i in range(start, end):
            t = title_re.match(lines[i])
            a = artist_re.match(lines[i])
            b = album_re.match(lines[i])
            if t:
                out[i] = t.group(1) + '"%s"' % title + t.group(2)
                seen.add((mus, "TITLE"))
            elif a:
                out[i] = a.group(1) + '"%s"' % artist + a.group(2)
                seen.add((mus, "ARTIST"))
            elif b:
                out[i] = b.group(1) + '""' + b.group(2)
                seen.add((mus, "ALBUM"))
    missing = {(mus, k) for mus, _, _ in mapping for k in ("TITLE", "ARTIST", "ALBUM")} - seen
    assert not missing, "unpatched: %s" % sorted(missing)
    return "\r\n".join(out).encode("latin1")


def pad_exact(buf, size):
    assert len(buf) <= size, (len(buf), size)
    if len(buf) == size:
        return buf
    tail = b"\r\n# tk-banner-1 title pad (keeps the stock byte size)"
    assert len(buf) < size - len(tail) - 64, "no room to pad"
    fill = size - len(buf) - len(tail) - 2
    return buf + b"\r\n# " + b"." * (fill - 4) + tail + b"\r\n"


def build_composite(iso_path, lbn, stock, patched, out_path, image_sectors):
    assert len(patched) == len(stock)
    nsec = (len(stock) + SEC - 1) // SEC
    with open(iso_path, "rb") as f:
        f.seek(lbn * SEC + len(stock))
        tail = f.read(nsec * SEC - len(stock))
        assert len(tail) == nsec * SEC - len(stock)
    stock_p, patched_p = stock + tail, patched + tail
    kinds = [stock_p[s * SEC:(s + 1) * SEC] != patched_p[s * SEC:(s + 1) * SEC]
             for s in range(nsec)]
    segs = []
    for s, changed in enumerate(kinds):
        if segs and segs[-1][0] == changed:
            segs[-1] = (changed, segs[-1][1], segs[-1][2] + 1)
        else:
            segs.append((changed, s, 1))
    host = bytearray()
    desc = []
    for changed, first, count in segs:
        if changed:
            off = SEC + len(host)
            assert off % SEC == 0
            host += patched_p[first * SEC:(first + count) * SEC]
            desc.append(("self", off, count))
        else:
            desc.append(("iso", lbn + first, count))
    text = ("PS2XCMP1\nsize %d\nimage %d\n" % (len(patched), image_sectors) +
            "".join("%s %d %d\n" % d for d in desc) + "end\n")
    assert len(text) <= SEC, len(text)
    blob = text.encode() + bytes(SEC - len(text)) + bytes(host)
    Path(out_path).write_bytes(blob)
    rebuilt = bytearray()
    with open(iso_path, "rb") as f:
        for k, x, n in desc:
            if k == "self":
                rebuilt += blob[x:x + n * SEC]
            else:
                f.seek(x * SEC)
                rebuilt += f.read(n * SEC)
    assert bytes(rebuilt[:len(patched)]) == patched, "resolve mismatch"
    assert bytes(rebuilt[len(patched):]) == tail, "tail mismatch"
    return dict(changed_sectors=sum(1 for k in kinds if k), total_sectors=nsec,
                out_bytes=len(blob), out_sha256=sha(blob))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ssx3-iso", required=True)
    ap.add_argument("--map-tsv", required=True)
    ap.add_argument("--out-composite", required=True)
    ap.add_argument("--materialized", default=None)
    a = ap.parse_args(argv)
    mapping = load_map(a.map_tsv)
    image_sectors = Path(a.ssx3_iso).stat().st_size // SEC
    with open(a.ssx3_iso, "rb") as f:
        lbn, size = iso_find(f, DISC)
        f.seek(lbn * SEC)
        stock = f.read(size)
    assert len(stock) == size and size == 21483, (len(stock), size)
    patched = pad_exact(patch_inf(stock, mapping), len(stock))
    assert sha(patched) != sha(stock)
    if a.materialized:
        Path(a.materialized).write_bytes(patched)
    comp = build_composite(a.ssx3_iso, lbn, stock, patched, a.out_composite, image_sectors)
    print(json.dumps(dict(
        disc=DISC, lbn=lbn, bytes=len(stock),
        stock_sha256=sha(stock), patched_sha256=sha(patched),
        slots=len(mapping), descriptor_reproduces_file=True, **comp), indent=1))


if __name__ == "__main__":
    main()
