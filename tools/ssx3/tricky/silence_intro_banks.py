#!/usr/bin/env python3
"""silence_intro_banks.py: drop the stock SSX 3 radio/artist DJ intros.

The Tricky race sequencer streams song intros from two ENGLISH.BIG bank
members before the pool song starts:

  data\\speech\\char\\DJ_Radio_Big_Intro_eng.dat   ("You're listening to EA Radio Big...")
  data\\speech\\char\\DJ_Artist_Intro_eng.dat      (per-song artist line, e.g. N.E.R.D.)

The music-pool builder (build_music_pool.py) only covers MUSIC.BIG/MUSIC2.BIG,
so these slots stayed stock, and the DJ speech gate cannot catch them: the
sequencer fetches the banks directly with no speech post through the gated
site (tk-pool-fix-1: zero gated posts in-race, intro still audible in tag-1).

This tool rebuilds an ENGLISH composite whose two intro members are
bit-identical in structure (same takes, offsets, sizes, durations) but
decode to digital silence. Take audio (SCDl payloads: history seeds + ADPCM
frames) is zeroed; SCHl/PT headers, sample counts, channel offsets, SCEl
markers, padding and the pre-first-SCHl region are preserved byte-for-byte,
so take indexing and intro timing are unchanged and the pool song starts on
the same tick as before.

Usage:
  silence_intro_banks.py --iso <SSX3.iso> --base-composite <english-big>
      --out-composite <out> [--out-members DIR] [--materialized BIG]
Only the named outputs are written; inputs are read-only. Game bytes never
enter git: this script is deterministic from the pinned ISO + base composite.

Merge semantics (sector diff, PS2XCMP1 emit, reproduce check) mirror
tk-audio-2 merge_unified.py merge_container; the EA-XA segment walk mirrors
song_eaxa.decode. Fatal on any mismatch.
"""
import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

SEC = 2048
CHAR = "data\\speech\\char\\"
INTRO = ["DJ_Radio_Big_Intro_eng.dat", "DJ_Artist_Intro_eng.dat"]
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
        assert found, part
        lba, size = found
    return lba, size


def big_members(buf):
    assert buf[:4] in (b"BIGF", b"BIG4"), buf[:4]
    n = struct.unpack(">I", buf[8:12])[0]
    hl = struct.unpack(">I", buf[12:16])[0]
    d = buf[16:16 + hl]
    i, out = 0, {}
    for _ in range(n):
        off, sz = struct.unpack_from(">II", d, i)
        i += 8
        e = d.index(b"\0", i)
        out[d[i:e].decode("latin1")] = (off, sz)
        i = e + 1
    return out


def materialize(composite, iso):
    blob = Path(composite).read_bytes()
    lines = blob[:SEC].split(b"\0")[0].decode().splitlines()
    assert lines[0] == "PS2XCMP1", lines[0]
    size = int(lines[1].split()[1])
    segs = []
    for ln in lines[3:]:
        if ln == "end":
            break
        k, x, n = ln.split()
        segs.append((k, int(x), int(n)))
    out = bytearray()
    with open(iso, "rb") as f:
        for k, x, n in segs:
            if k == "self":
                out += blob[x:x + n * SEC]
            else:
                f.seek(x * SEC)
                out += f.read(n * SEC)
    return bytes(out[:size])


def take_channels(sch):
    """Channel count from the SCHl PT tags (0x82 after 0xFD); default 1."""
    try:
        fd = sch.index(bytes([0xFD]))
    except ValueError:
        return 1
    j = sch.find(bytes([0x82]), fd)
    if j == -1:
        return 1
    w = sch[j + 1]
    return int.from_bytes(sch[j + 2:j + 2 + w], "big")


def silence_member(blob):
    """Zero take audio in place; return (silenced, takes, skipped)."""
    takes = [m.start() for m in re.finditer(b"SCHl", blob)]
    assert takes, "no takes"
    out = bytearray(blob)
    skipped = []
    for i, p in enumerate(takes):
        end = takes[i + 1] if i + 1 < len(takes) else len(blob)
        sch_len = struct.unpack_from("<I", blob, p + 4)[0]
        assert 12 <= sch_len <= end - p, (i, sch_len)
        ch = take_channels(blob[p:p + sch_len])
        assert 1 <= ch <= 2, (i, ch)
        q = p + sch_len
        while q + 8 <= end:
            tag = bytes(out[q:q + 4])
            ln = struct.unpack_from("<I", bytes(out[q + 4:q + 8]))[0]
            if tag == b"SCDl" and 12 + 4 * ch <= ln and q + ln <= len(blob):
                ns = struct.unpack_from("<I", bytes(out[q + 8:q + 12]))[0]
                offs = struct.unpack_from("<%dI" % ch, bytes(out[q + 12:q + 12 + 4 * ch]))
                if ns <= 8 * ln and all(o < ln for o in offs):
                    for b in range(q + 12 + 4 * ch, q + ln):
                        out[b] = 0
                    q += ln
                    continue
                skipped.append((i, q))
                q += ln
                continue
            if 8 <= ln <= len(blob) - q:
                q += ln
            else:
                q += 1
    return bytes(out), len(takes), skipped


def decode_peak(blob):
    """Peak of the reference EA-XA decode (song_eaxa.decode semantics)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import song_eaxa
    _, _, pcm = song_eaxa.decode(blob + b"\0" * 65536, 3600 * 32000)
    n = min((len(x) for x in pcm), default=0)
    if not n:
        return 0
    return max(max(abs(v) for v in x[:n]) for x in pcm)


def merge_container(iso, members, out_comp):
    """members: list of (shortname, bytes). Splice into stock ENGLISH.BIG."""
    with open(iso, "rb") as f:
        lbn, size = iso_find(f, "DATA/AUDIO/ENGLISH.BIG")
        base = lbn * SEC
        table = big_members(_hdr(f, base))
        f.seek(base)
        stock = f.read(size)
    patched = bytearray(stock)
    replaced = []
    for short, blob in members:
        want = CHAR + short
        assert want in table, want
        off, sz = table[want]
        assert len(blob) == sz, (short, len(blob), sz)
        patched[off:off + sz] = blob
        replaced.append((short, off, sz))
    patched = bytes(patched)
    nsec = (len(stock) + SEC - 1) // SEC
    with open(iso, "rb") as f:
        f.seek(base + len(stock))
        tail = f.read(nsec * SEC - len(stock))
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
    image_sectors = Path(iso).stat().st_size // SEC
    text = (f"PS2XCMP1\nsize {len(patched)}\nimage {image_sectors}\n" +
            "".join(f"{k} {x} {n}\n" for k, x, n in desc) + "end\n")
    assert len(text) <= SEC, len(text)
    blob = text.encode() + bytes(SEC - len(text)) + bytes(host)
    Path(out_comp).write_bytes(blob)
    rebuilt = bytearray()
    with open(iso, "rb") as f:
        for k, x, n in desc:
            if k == "self":
                rebuilt += blob[x:x + n * SEC]
            else:
                f.seek(x * SEC)
                rebuilt += f.read(n * SEC)
    assert bytes(rebuilt[:len(patched)]) == patched, "descriptor mismatch"
    assert bytes(rebuilt[len(patched):]) == tail, "disc tail not preserved"
    return dict(members={t: {"offset": o, "size": s} for t, o, s in replaced},
                changed_sectors=sum(1 for c in kinds if c),
                out_sha256=sha(blob), out_bytes=len(blob),
                descriptor_reproduces_big=True)


def _hdr(f, base):
    f.seek(base)
    h = f.read(16)
    assert h[:4] in (b"BIGF", b"BIG4"), h[:4]
    hl = struct.unpack(">I", h[12:16])[0]
    f.seek(base)
    return f.read(16 + hl)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--base-composite", required=True)
    ap.add_argument("--out-composite", required=True)
    ap.add_argument("--out-members", default=None)
    ap.add_argument("--materialized", default=None)
    a = ap.parse_args(argv)
    with open(a.iso, "rb") as f:
        lbn, size = iso_find(f, "DATA/AUDIO/ENGLISH.BIG")
        base = lbn * SEC
        table = big_members(_hdr(f, base))
        f.seek(base)
        stock = f.read(size)
    patched = materialize(a.base_composite, a.iso)
    assert len(patched) == len(stock), (len(patched), len(stock))
    # Precondition: the base composite left both intro members stock.
    for short in INTRO:
        off, sz = table[CHAR + short]
        assert patched[off:off + sz] == stock[off:off + sz], \
            f"{short} already replaced in base; refusing to double-patch"
    # Changed (Tricky) members of the base composite carry over unchanged.
    carry = []
    for name, (off, sz) in table.items():
        if stock[off:off + sz] != patched[off:off + sz]:
            carry.append((name.split("\\")[-1], patched[off:off + sz]))
    silent, info = [], {}
    for short in INTRO:
        off, sz = table[CHAR + short]
        blob, takes, skipped = silence_member(stock[off:off + sz])
        assert not skipped, (short, skipped)
        peak = decode_peak(blob)
        assert peak == 0, (short, peak)
        assert len(blob) == sz
        if a.out_members:
            Path(a.out_members).mkdir(parents=True, exist_ok=True)
            Path(a.out_members, short).write_bytes(blob)
        silent.append((short, blob))
        info[short] = {"offset": off, "size": sz, "takes": takes,
                       "sha256": sha(blob), "decode_peak": peak}
    rec = merge_container(a.iso, carry + silent, a.out_composite)
    if a.materialized:
        Path(a.materialized).write_bytes(patched[:0] + materialize(a.out_composite, a.iso))
    receipt = dict(container="ENGLISH", base_composite=str(a.base_composite),
                   base_sha256=sha(Path(a.base_composite).read_bytes()),
                   carried=[s for s, _ in carry], silenced=info,
                   orphan_note="pre-first-SCHl bytes preserved (unplayed; "
                               "no race fetch touches them in the Gari/Ely traces)",
                   **rec)
    print(json.dumps(receipt, indent=1))
    return receipt


if __name__ == "__main__":
    main()
