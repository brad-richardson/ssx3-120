#!/usr/bin/env python3
"""tk25c_slots.py <ssx3.dat> <tk25b-decisions.tsv> <mapping.tsv> <out.dat> --target <name> --loudrows <tsv>

TK25c: same mapping as TK25b (same Tricky source per slot) but with loudness
matching: per replaced slot, the Tricky PCM is gain-scaled so its integrated
loudness (ffmpeg ebur128) matches the SSX 3 line it replaces (+-1 LU after the
EA-XA encode), before encoding with tk25b_eaxa. Gain is capped so peaks stay
at or below -1 dBFS (clipping would survive the encode); capped slots are
flagged in the loudness table.

Writes out.dat (same size as input) + out.dat.decisions.tsv (TK25b format) and
appends loudness rows: target, slot, source, ssx3_lufs, tricky_before_lufs,
gain_db, applied_db, after_lufs, delta_lu, capped.
"""
import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import tool_paths  # noqa: E402
from speech_eaxa import encode_blocks, packetize, assemble, TooLong  # noqa: E402
from speech_loud import (measure_lufs, measure_peak_db, apply_gain_db,  # noqa: E402
                         init_cache)

WORK25B = ""  # set by main() from --tk25b-work (Tricky WAVs + resample cache)
WORK = ""  # set by main() from --work-dir (gain/probe outputs)
TARGET = ""  # set by main(); used for probe filenames
# No peak cap: stock slots peak at 0 dBFS (measured), so a pure gain match is
# faithful to the stock mastering; only transient overs clip in the s16
# conversion, as in any hot master.

# Inlined from TK25b tk25b_slots.py (that module runs main() on import, so it
# cannot be imported; logic unchanged).
def parse_slots(dat):
    pos = [m.start() for m in re.finditer(b"SCHl", dat)]
    slots = []
    for i, p in enumerate(pos):
        end = pos[i + 1] if i + 1 < len(pos) else len(dat)
        sch_len = struct.unpack("<I", dat[p + 4:p + 8])[0]
        sch = dat[p:p + sch_len]
        fd = sch.index(bytes([0xFD]))
        ch = 1
        j = sch.find(bytes([0x82]), fd)
        if j != -1:
            w = sch[j + 1]
            ch = int.from_bytes(sch[j + 2:j + 2 + w], "big")
        off = p + sch_len + 12
        assert dat[off:off + 4] == b"SCDl", (i, off)
        s_block = struct.unpack("<I", dat[off + 8:off + 12])[0]
        e = dat.index(b"SCEl", p, end)
        esz = struct.unpack("<I", dat[e + 4:e + 8])[0]
        template = dat[p:e + esz]
        slots.append(dict(index=i, start=p, bytes=end - p, channels=ch,
                          s_block=s_block, template=template))
    return slots

def load_resampled(stem, channels):
    src = os.path.join(WORK25B, "wav", "tricky", stem + ".wav")
    tmp = os.path.join(WORK25B, "built", f"rs-{stem}-{channels}ch.wav")
    if not os.path.exists(tmp):
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        r = subprocess.run([str(tool_paths.ffmpeg_path()), "-hide_banner",
                            "-v", "error", "-y",
                            "-i", src, "-ar", "22050", "-ac", str(channels), tmp],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-500:]
    w = wave.open(tmp, "rb")
    n = w.getnframes()
    assert w.getframerate() == 22050 and w.getnchannels() == channels, (stem, channels)
    raw = w.readframes(n)
    px = struct.unpack("<" + "h" * (n * channels), raw)
    pcm = [list(px[i::channels]) for i in range(channels)]
    m = ((n + 27) // 28) * 28
    for c in range(channels):
        pcm[c] += [0] * (m - n)
    return pcm, m

def decode_slot_to_wav(slot_bytes, channels, dst):
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(slot_bytes)
        tmp = f.name
    r = subprocess.run([str(tool_paths.ffmpeg_path()), "-hide_banner",
                        "-v", "error", "-y",
                        "-i", tmp, "-ac", str(channels), "-ar", "22050", dst],
                       capture_output=True, text=True)
    os.unlink(tmp)
    assert r.returncode == 0, r.stderr[-500:]

def gained_pcm(stem, channels, gain_db):
    """Resampled candidate (TK25b cache) with gain applied; returns (pcm, m, path)."""
    base = os.path.join(WORK25B, "built", f"rs-{stem}-{channels}ch.wav")
    assert os.path.exists(base), f"missing TK25b resample cache {base}"
    if abs(gain_db) < 0.005:
        path = base
    else:
        path = os.path.join(WORK, "built", f"g-{stem}-{channels}ch-{gain_db:+.2f}dB.wav")
        if not os.path.exists(path):
            apply_gain_db(base, gain_db, path)
    w = wave.open(path, "rb")
    n = w.getnframes()
    assert w.getframerate() == 22050 and w.getnchannels() == channels
    raw = w.readframes(n)
    px = struct.unpack("<" + "h" * (n * channels), raw)
    pcm = [list(px[i::channels]) for i in range(channels)]
    m = ((n + 27) // 28) * 28
    for c in range(channels):
        pcm[c] += [0] * (m - n)
    return pcm, m, path

def match_candidate(s, ssx3_lu, src):
    """Gain-match + encode one candidate. Returns
    (enc, m, before_lu, peak, applied, after_lu, delta) or None (TooLong/no fit)."""
    rs_path = os.path.join(WORK25B, "built", f"rs-{src}-{s['channels']}ch.wav")
    if not os.path.exists(rs_path):
        load_resampled(src, s["channels"])
    before_lu = measure_lufs(rs_path)
    peak = measure_peak_db(rs_path)
    applied = ssx3_lu - before_lu
    try:
        pcm, m, _ = gained_pcm(src, s["channels"], applied)
        splits, chb = encode_blocks(pcm, s["s_block"])
        scdls = packetize(splits, chb)
        enc = assemble(s["template"], scdls, m)
    except TooLong:
        return None
    if len(enc) > s["bytes"]:
        return None
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(enc)
        tmp = f.name
    vwav = os.path.join(WORK, "probe", f"v-{TARGET}-{s['index']}-{src}.wav")
    if os.path.exists(vwav):
        os.unlink(vwav)
    decode_slot_to_wav(enc, s["channels"], vwav)
    os.unlink(tmp)
    after_lu = measure_lufs(vwav)
    return enc, m, before_lu, peak, applied, after_lu, after_lu - ssx3_lu

def main(argv=None):
    global TARGET, WORK25B, WORK
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ssx3_dat")
    ap.add_argument("decisions")
    ap.add_argument("mapping")
    ap.add_argument("out")
    ap.add_argument("--target", required=True)
    ap.add_argument("--loudrows", required=True)
    ap.add_argument("--tk25b-work", required=True,
                    help="dir with wav/tricky/<stem>.wav sources (resample cache in built/)")
    ap.add_argument("--work-dir", required=True,
                    help="work dir for gained/probe WAVs (built/, probe/) + loud cache")
    a = ap.parse_args(argv)
    ssx3_path, dec_path, map_path, out_path = a.ssx3_dat, a.decisions, a.mapping, a.out
    TARGET = a.target
    target = TARGET
    loud_path = a.loudrows
    WORK25B = a.tk25b_work
    WORK = a.work_dir
    init_cache(os.path.join(WORK, "loud-cache.json"))
    os.makedirs(os.path.join(WORK, "built"), exist_ok=True)
    os.makedirs(os.path.join(WORK, "probe"), exist_ok=True)
    dat = open(ssx3_path, "rb").read()
    slots = parse_slots(dat)
    want = {}
    cands = {}
    for line in open(dec_path):
        line = line.rstrip("\n")
        if not line or line.startswith("slot\t"):
            continue
        idx, src = line.split("\t")[:2]
        want[int(idx)] = src
    for line in open(map_path):
        line = line.rstrip("\n")
        if not line or line.startswith("slot\t"):
            continue
        idx, _, rest = line.partition("\t")
        cands[int(idx)] = [c for c in rest.split(",") if c]
    out = bytearray(dat)
    decisions = []
    loud_rows = []
    for s in slots:
        src = want.get(s["index"], "KEEP-ORIGINAL")
        if src == "KEEP-ORIGINAL":
            decisions.append((s["index"], "KEEP-ORIGINAL", 0, s["bytes"], s["bytes"]))
            continue
        seg = dat[s["start"]:s["start"] + s["bytes"]]
        s3wav = os.path.join(WORK, "probe", f"s3-{target}-slot{s['index']}.wav")
        if not os.path.exists(s3wav):
            decode_slot_to_wav(seg, s["channels"], s3wav)
        ssx3_lu = measure_lufs(s3wav)
        # TK25b's source first (mapping priority); if it misses +-1 LU, walk
        # the rest of the mapping's candidates for one that fits and matches.
        ordered = [src] + [c for c in cands.get(s["index"], []) if c != src]
        first = None
        chosen = None
        for cand in ordered:
            r = match_candidate(s, ssx3_lu, cand)
            if r is None:
                continue
            if first is None:
                first = (cand,) + r
            if abs(r[5]) <= 1.0:
                chosen = (cand,) + r
                break
        if chosen is None:
            assert first is not None, (target, s["index"], "no candidate fits")
            chosen = first
        src, enc, m, before_lu, peak, applied, after_lu, delta = chosen
        out[s["start"]:s["start"] + len(enc)] = enc
        tail = s["start"] + len(enc)
        end = s["start"] + s["bytes"]
        out[tail:end] = b"\x00" * (end - tail)
        decisions.append((s["index"], src, m / 22050, len(enc), s["bytes"]))
        loud_rows.append((target, s["index"], src, ssx3_lu, before_lu,
                          applied, applied, after_lu, delta, False, peak))
        print(f"slot {s['index']}: {src} ssx3={ssx3_lu:.1f} before={before_lu:.1f} "
              f"gain={applied:+.2f} after={after_lu:.1f} "
              f"delta={delta:+.2f} {len(enc)}/{s['bytes']}B", flush=True)
    assert len(out) == len(dat)
    open(out_path, "wb").write(bytes(out))
    with open(out_path + ".decisions.tsv", "w") as f:
        f.write("slot\tsource\tseconds\tbytes_used\tslot_bytes\n")
        for d in decisions:
            f.write(f"{d[0]}\t{d[1]}\t{d[2]:.3f}\t{d[3]}\t{d[4]}\n")
    new_file = not os.path.exists(loud_path)
    with open(loud_path, "a") as f:
        if new_file:
            f.write("target\tslot\tsource\tssx3_lufs\ttricky_before_lufs\tgain_db\t"
                    "applied_db\tafter_lufs\tdelta_lu\tcapped\ttricky_peak_db\n")
        for r in loud_rows:
            f.write(f"{r[0]}\t{r[1]}\t{r[2]}\t{r[3]:.2f}\t{r[4]:.2f}\t{r[5]:+.2f}\t"
                    f"{r[6]:+.2f}\t{r[7]:.2f}\t{r[8]:+.2f}\t{int(r[9])}\t{r[10]:+.1f}\n")


if __name__ == "__main__":
    main()
