#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""english_base.py: build the 8-member Tricky ENGLISH base composite.

Reconstructed tk-audio-2 merge recipe (english-recipe-1): the phase_english
base that carries Tricky speech takes inside stock SSX 3 ENGLISH.BIG,
from the user's own two discs only. Nothing game-derived ships: the only
repo data is this recipe (member list, Tricky donor bank/stream indices,
per-slot gains); every audio byte is derived at build time from the discs
plus local ffmpeg/vgmstream (resolved by the driver, same rules as the
other audio phases).

The 8 members (stock SSX 3 ENGLISH.BIG, 30 members) and their takes:

| member | take source (Tricky SPEECH.BIG) | transform |
| --- | --- | --- |
| PA_Venue_Intro_eng.dat | mc\\VenueIntro.dat stream 20 | donor + zero pad to the 101,632 B stream-0 region |
| PA_Finish_Line_eng.dat | mc\\Go.dat stream 0 | fill all 60 slots + zero pad |
| PA_Sponsor_Intro_eng.dat | narr\\Pre_Race_Overlay_01.dat stream 0 | fill all 68 slots + zero pad |
| DJ_Artist_Intro_2_eng.dat | mc\\VenueIntro.dat stream 20 | vgmstream decode -> ffmpeg 48 kHz mono -> 22050 stereo -> EA-XA re-encode (+1.30 dB peak-capped take), fill all 9 slots |
| PA_Medal_Run_Intro_eng.dat | mc banks Big_Air + Takeoff (14 slots) | per-slot EA-XA re-encode, embedded gains |
| PA_Rider_Intro_eng.dat | mc bank Wipeout (38 slots) | per-slot EA-XA re-encode, embedded gains |
| PA_Rider_Postition_eng.dat | mc banks Position + Pass + Positioning (80 slots) | per-slot EA-XA re-encode, embedded gains |
| PA_Rider_Race_Intro_eng.dat | mc bank Knockdown (30 slots) | per-slot EA-XA re-encode, embedded gains |

Slot takes for the 5 encoded members come from data/english-decisions/:
one TSV per member, `slot<TAB>source<TAB>gain_db`, source
`tricky-<Bank>-<Idx>` (Tricky SPEECH.BIG `data\\speech\\mc\\<Bank>.dat`
stream, or `data\\speech\\narr\\...` where noted) or KEEP-ORIGINAL.
Gains are the validated lane takes (peak-capped +1.30 dB for the artist
take; per-slot LUFS-match takes for the vessels) applied with
`ffmpeg volume=<gain>dB` — never re-measured at runtime, so local
ebur128 float drift cannot change the bytes.

Validated reproduction (Mac: vgmstream-cli r2117, ffmpeg 9.0.2):
materialized output is byte-identical to the lab oracle
english-tk-audio-2f-big (31,180,800-byte PS2XCMP1, descriptor reproduces
the patched BIG). Toolchain note: vgmstream decode + ffmpeg
resample/volume bytes are the identity-sensitive steps; other ffmpeg /
vgmstream builds reproduce the same takes in practice but byte-identity
is verified only for the pair above.

Usage:
  english_base.py --tricky-iso <Tricky.iso> --ssx3-iso <SSX3.iso>
      --ffmpeg <ffmpeg> --vgmstream <vgmstream-cli>
      --out-composite <out> [--materialized <big>] [--work-dir DIR]
Only the named outputs are written; inputs are read-only.
"""
import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from speech_eaxa import build_stream, encode_blocks, packetize, assemble  # noqa: E402
from speech_merge import iso_find, big_members  # noqa: E402
from speech_slots import parse_slots  # noqa: E402

SEC = 2048
CHAR = "data\\speech\\char\\"
MC = "data\\speech\\mc\\"
NARR = "data\\speech\\narr\\"
sha = lambda b: hashlib.sha256(b).hexdigest()

# Tricky SPEECH.BIG donor (member, stream) for the three verbatim takes.
# Stream bounds are SCHl-to-next-SCHl regions; the take is the whole region
# (single-variant fill, zero-padded per slot).
VERBATIM = {
    "PA_Finish_Line_eng.dat": ("data\\speech\\mc\\Go.dat", 0),
    "PA_Sponsor_Intro_eng.dat": ("data\\speech\\narr\\Pre_Race_Overlay_01.dat", 0),
}
VENUE_MEMBER = "PA_Venue_Intro_eng.dat"
VENUE_DONOR = ("data\\speech\\mc\\VenueIntro.dat", 20)

# Stock member sizes of the pinned SSX 3 USA disc (structural gate: a
# different ENGLISH.BIG shape fails here with the member name, not
# mid-encode). The SSX 3 ISO itself is hash-gated by the driver preflight.
MEMBER_SIZES = {
    "PA_Venue_Intro_eng.dat": 5067264,
    "PA_Medal_Run_Intro_eng.dat": 1764096,
    "PA_Rider_Intro_eng.dat": 4634880,
    "PA_Rider_Postition_eng.dat": 7662336,
    "PA_Rider_Race_Intro_eng.dat": 3144704,
    "PA_Finish_Line_eng.dat": 5573376,
    "PA_Sponsor_Intro_eng.dat": 6563328,
    "DJ_Artist_Intro_2_eng.dat": 1924608,
}
ENCODED_MEMBERS = ["DJ_Artist_Intro_2_eng.dat", "PA_Medal_Run_Intro_eng.dat",
                   "PA_Rider_Intro_eng.dat", "PA_Rider_Postition_eng.dat",
                   "PA_Rider_Race_Intro_eng.dat"]


def run(*args):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(args)}: {(r.stderr or '')[-500:]}")


def slot_bounds(dat):
    o = [m.start() for m in re.finditer(b"SCHl", dat)]
    if not o:
        raise SystemExit("member has no SCHl takes")
    return [(s, (o[i + 1] if i + 1 < len(o) else len(dat)) - s) for i, s in enumerate(o)]


def donor_stream(tricky_big, members, member, idx):
    """Raw (SCHl..SCEl) donor stream bytes from the Tricky SPEECH.BIG image."""
    if member not in members:
        raise SystemExit(f"Tricky SPEECH.BIG has no member {member!r}")
    off, sz = members[member]
    blob = tricky_big[off:off + sz]
    o = [m.start() for m in re.finditer(b"SCHl", blob)]
    if idx >= len(o):
        raise SystemExit(f"{member}: stream {idx} out of range ({len(o)} streams)")
    e = blob.find(b"SCEl", o[idx])
    if e < 0:
        raise SystemExit(f"{member} stream {idx}: no SCEl")
    elen = struct.unpack("<I", blob[e + 4:e + 8])[0]
    return blob[o[idx]:e + 4 + elen]


def decode_donor(vgmstream, stream_bytes, wav_path):
    sp = wav_path.with_suffix(".str")
    sp.write_bytes(stream_bytes)
    run(str(vgmstream), "-o", str(wav_path), str(sp))


def resample_22050(ffmpeg, src_wav, channels, dst_wav):
    run(str(ffmpeg), "-hide_banner", "-v", "error", "-y",
        "-i", str(src_wav), "-ar", "22050", "-ac", str(channels), str(dst_wav))


def read_pcm(path, channels):
    with wave.open(str(path), "rb") as w:
        n = w.getnframes()
        if w.getframerate() != 22050 or w.getnchannels() != channels:
            raise SystemExit(f"bad resample shape: {path}")
        px = struct.unpack("<" + "h" * (n * channels), w.readframes(n))
    pcm = [list(px[i::channels]) for i in range(channels)]
    m = ((n + 27) // 28) * 28
    for c in range(channels):
        pcm[c] += [0] * (m - n)
    return pcm, m


def build_take(ffmpeg, rs_wav, gain_db, template, s_block, slot_bytes, what):
    """Encode one take: fixed gain -> EA-XA stream. Fatal unless it fits."""
    if abs(gain_db) < 0.005:
        gwav = rs_wav
    else:
        gwav = rs_wav.with_name(f"{rs_wav.stem}-g{gain_db:+.2f}dB.wav")
        if not gwav.is_file():
            run(str(ffmpeg), "-hide_banner", "-v", "error", "-y",
                "-i", str(rs_wav), "-af", f"volume={gain_db:+.2f}dB", str(gwav))
    pcm, m = read_pcm(gwav, read_channels(template))
    enc = assemble(template, packetize(*encode_blocks(pcm, s_block)), m)
    if len(enc) > slot_bytes:
        raise SystemExit(f"{what}: take {len(enc)}B exceeds min slot {slot_bytes}B")
    return enc


def read_channels(template):
    sch_len = struct.unpack("<I", template[4:8])[0]
    sch = template[:sch_len]
    try:
        fd = sch.index(bytes([0xFD]))
    except ValueError:
        return 1
    j = sch.find(bytes([0x82]), fd)
    if j == -1:
        return 1
    w = sch[j + 1]
    return int.from_bytes(sch[j + 2:j + 2 + w], "big")


def load_decisions(path):
    """[(slot, bank, idx, gain)] ; KEEP-ORIGINAL rows carry gain None."""
    rows = []
    for ln in Path(path).read_text().splitlines():
        if not ln or ln.startswith("slot\t"):
            continue
        slot, source, gain = (ln.split("\t") + ["", ""])[:3]
        slot = int(slot)
        if source == "KEEP-ORIGINAL":
            rows.append((slot, None, None, None))
            continue
        if not source.startswith("tricky-") or gain == "":
            raise SystemExit(f"bad decisions row: {ln!r}")
        bank, idx = source[len("tricky-"):].rsplit("-", 1)
        rows.append((slot, bank, int(idx), float(gain)))
    return rows


def merge_container(ssx3_iso, members, out_comp):
    """members: list of (shortname, bytes). Splice into stock ENGLISH.BIG.

    Same sector-diff PS2XCMP1 emit + reproduce check as speech_merge and
    silence_intro_banks (fatal on any size mismatch)."""
    with open(ssx3_iso, "rb") as f:
        lbn, size = iso_find(f, "DATA/AUDIO/ENGLISH.BIG")
        base = lbn * SEC
        table = big_members(f, base)
        f.seek(base)
        stock = f.read(size)
    for short, blob in members:
        want = CHAR + short
        if want not in table:
            raise SystemExit(f"stock ENGLISH.BIG has no member {want!r}")
        off, sz = table[want]
        if len(blob) != sz:
            raise SystemExit(f"{short}: built {len(blob)}B != stock {sz}B")
    for short, expect in MEMBER_SIZES.items():
        off, sz = table[CHAR + short]
        if sz != expect:
            raise SystemExit(f"{short}: stock size {sz} != recipe {expect} (wrong disc?)")
    patched = bytearray(stock)
    replaced = []
    for short, blob in members:
        off, sz = table[CHAR + short]
        patched[off:off + sz] = blob
        replaced.append((short, off, sz))
    patched = bytes(patched)
    nsec = (len(stock) + SEC - 1) // SEC
    with open(ssx3_iso, "rb") as f:
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
    image_sectors = Path(ssx3_iso).stat().st_size // SEC
    text = (f"PS2XCMP1\nsize {len(patched)}\nimage {image_sectors}\n" +
            "".join(f"{k} {x} {n}\n" for k, x, n in desc) + "end\n")
    if len(text) > SEC:
        raise SystemExit(f"composite descriptor too long ({len(text)}B)")
    blob = text.encode() + bytes(SEC - len(text)) + bytes(host)
    Path(out_comp).write_bytes(blob)
    rebuilt = bytearray()
    with open(ssx3_iso, "rb") as f:
        for k, x, n in desc:
            if k == "self":
                rebuilt += blob[x:x + n * SEC]
            else:
                f.seek(x * SEC)
                rebuilt += f.read(n * SEC)
    if bytes(rebuilt[:len(patched)]) != patched:
        raise SystemExit("descriptor does not reproduce the patched BIG")
    if bytes(rebuilt[len(patched):]) != tail:
        raise SystemExit("disc tail not preserved")
    return dict(members={t: {"offset": o, "size": s} for t, o, s in replaced},
                changed_sectors=sum(1 for c in kinds if c),
                out_sha256=sha(blob), out_bytes=len(blob),
                descriptor_reproduces_big=True)


def fill_all_slots(member_bytes, take, short):
    """Splice one take into every SCHl slot + zero pad (part3_data rule)."""
    bounds = slot_bounds(member_bytes)
    for _, n in bounds:
        if len(take) > n:
            raise SystemExit(f"{short}: take {len(take)}B exceeds a {n}B slot")
    dat = bytearray(member_bytes)
    for s, n in bounds:
        dat[s:s + len(take)] = take
        dat[s + len(take):s + n] = b"\0" * (n - len(take))
    return bytes(dat), len(bounds)


def build_encoded_member(ffmpeg, vgmstream, work, stock_member_bytes,
                         decisions_path, tricky_big, tmembers, short,
                         via_mono48=False):
    """One EA-XA re-encoded member from its decisions TSV.

    via_mono48 reproduces the part3c take chain (artist gate-call take):
    donor -> 48 kHz mono -> 22050 stereo. The intermediate is not a
    no-op: mixdown+resample sample values differ from a direct stereo
    resample, and the validated take bytes require it.
    """
    slots = parse_slots(stock_member_bytes)
    by_index = {s["index"]: s for s in slots}
    rows = load_decisions(decisions_path)
    wav_cache = {}
    out = bytearray(stock_member_bytes)
    took = []
    for slot_idx, bank, idx, gain in rows:
        if slot_idx not in by_index:
            raise SystemExit(f"{short}: slot {slot_idx} out of range")
        s = by_index[slot_idx]
        if bank is None:
            continue  # KEEP-ORIGINAL: stock bytes stay
        key = (bank, idx)
        if key not in wav_cache:
            member = bank_member_name(bank)
            stream = donor_stream(tricky_big, tmembers, member, idx)
            wav = work / f"{bank}-{idx:03}.wav"
            if not wav.is_file():
                decode_donor(vgmstream, stream, wav)
            wav_cache[key] = wav
        if via_mono48:
            mono = work / f"{bank}-{idx:03}-mono48.wav"
            if not mono.is_file():
                run(str(ffmpeg), "-hide_banner", "-v", "error", "-y",
                    "-i", str(wav_cache[key]), "-ar", "48000", "-ac", "1", str(mono))
            rs = work / f"{bank}-{idx:03}-mono48-{s['channels']}ch.wav"
            if not rs.is_file():
                resample_22050(ffmpeg, mono, s["channels"], rs)
        else:
            rs = work / f"{bank}-{idx:03}-{s['channels']}ch.wav"
            if not rs.is_file():
                resample_22050(ffmpeg, wav_cache[key], s["channels"], rs)
        take = build_take(ffmpeg, rs, gain, s["template"], s["s_block"],
                          s["bytes"], f"{short} slot {slot_idx}")
        out[s["start"]:s["start"] + len(take)] = take
        out[s["start"] + len(take):s["start"] + s["bytes"]] = \
            b"\0" * (s["bytes"] - len(take))
        took.append(slot_idx)
    return bytes(out), took


def bank_member_name(bank):
    """Tricky SPEECH.BIG member for a decisions bank stem."""
    if bank == "Pre_Race_Overlay_01":
        return NARR + "Pre_Race_Overlay_01.dat"
    return MC + bank + ".dat"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tricky-iso", required=True)
    ap.add_argument("--ssx3-iso", required=True)
    ap.add_argument("--ffmpeg", required=True)
    ap.add_argument("--vgmstream", required=True)
    ap.add_argument("--out-composite", required=True)
    ap.add_argument("--materialized", default=None)
    ap.add_argument("--work-dir", default=None)
    a = ap.parse_args(argv)
    ffmpeg, vgmstream = Path(a.ffmpeg), Path(a.vgmstream)
    if not ffmpeg.is_file() or not os.access(ffmpeg, os.X_OK):
        raise SystemExit(f"ffmpeg is not executable: {ffmpeg}")
    if not vgmstream.is_file() or not os.access(vgmstream, os.X_OK):
        raise SystemExit(f"vgmstream-cli is not executable: {vgmstream}")
    work = Path(a.work_dir) if a.work_dir else Path(a.out_composite).parent / "english-base-work"
    work.mkdir(parents=True, exist_ok=True)

    with open(a.ssx3_iso, "rb") as f:
        lbn, size = iso_find(f, "DATA/AUDIO/ENGLISH.BIG")
        stable = big_members(f, lbn * SEC)
        f.seek(lbn * SEC)
        stock = f.read(size)
    members = []
    # 1. venue: donor stream into the stream-0 region, rest stock.
    vshort = VENUE_MEMBER
    voff, vsz = stable[CHAR + vshort]
    if vsz != MEMBER_SIZES[vshort]:
        raise SystemExit(f"{vshort}: stock size {vsz} != recipe (wrong disc?)")
    with open(a.tricky_iso, "rb") as f:
        tlbn, tsize = iso_find(f, "DATA/AUDIO/SPEECH.BIG")
        tmembers = big_members(f, tlbn * SEC)
        f.seek(tlbn * SEC)
        tricky_big = f.read(tsize)
    vdonor_member, vdonor_idx = VENUE_DONOR
    vdonor = donor_stream(tricky_big, tmembers, vdonor_member, vdonor_idx)
    vdat = stock[voff:voff + vsz]
    bounds = slot_bounds(vdat)
    region = bounds[1][0]
    if len(vdonor) > region:
        raise SystemExit(f"{vshort}: donor {len(vdonor)}B exceeds stream-0 region {region}B")
    vpatched = vdonor + bytes(region - len(vdonor)) + vdat[region:]
    assert len(vpatched) == vsz
    members.append((vshort, vpatched))
    # 2-3. verbatim takes fill every slot.
    for short, (member, idx) in VERBATIM.items():
        off, sz = stable[CHAR + short]
        if sz != MEMBER_SIZES[short]:
            raise SystemExit(f"{short}: stock size {sz} != recipe (wrong disc?)")
        take = donor_stream(tricky_big, tmembers, member, idx)
        filled, nslots = fill_all_slots(stock[off:off + sz], take, short)
        members.append((short, filled))
    # 4. encoded members from the decisions TSVs. The artist gate-call
    # take alone goes through the part3c mono48 intermediate (see
    # build_encoded_member): the mc1 vessel takes resample stereo direct.
    decdir = HERE / "data" / "english-decisions"
    for short in ENCODED_MEMBERS:
        off, sz = stable[CHAR + short]
        if sz != MEMBER_SIZES[short]:
            raise SystemExit(f"{short}: stock size {sz} != recipe (wrong disc?)")
        built, took = build_encoded_member(
            ffmpeg, vgmstream, work, stock[off:off + sz],
            decdir / (short + ".decisions.tsv"), tricky_big, tmembers, short,
            via_mono48=(short == "DJ_Artist_Intro_2_eng.dat"))
        members.append((short, built))
        print(f"{short}: {len(took)} takes placed", flush=True)
    rec = merge_container(a.ssx3_iso, members, a.out_composite)
    if a.materialized:
        rec2 = dict(rec)
        size2, segs = parse_desc(Path(a.out_composite).read_bytes())
        rebuilt = bytearray()
        blob = Path(a.out_composite).read_bytes()
        with open(a.ssx3_iso, "rb") as f:
            for k, x, n in segs:
                if k == "self":
                    rebuilt += blob[x:x + n * SEC]
                else:
                    f.seek(x * SEC)
                    rebuilt += f.read(n * SEC)
        Path(a.materialized).write_bytes(bytes(rebuilt[:size2]))
        rec2["materialized"] = str(a.materialized)
        rec = rec2
    receipt = dict(container="ENGLISH", recipe="english-recipe-1/2f",
                   tricky_iso=str(a.tricky_iso), ssx3_iso=str(a.ssx3_iso),
                   **rec)
    print(json.dumps(receipt, indent=1))
    return receipt


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


if __name__ == "__main__":
    main()
