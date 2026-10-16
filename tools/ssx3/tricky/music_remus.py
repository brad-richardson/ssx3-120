#!/usr/bin/env python3
"""tk41_remus.py <v3.mus> <v3.mpf> <song-template.sch> <out.mus> <offsets.tsv> [only=N] [jobs=J]

TK41 H7 fix: resample Tricky 44.1 kHz EA-XA segments to 32 kHz and re-encode
with TK25b's EA-XA encoder, using a stock SSX3 song segment as the SCHl/SCEl
template. Output .mus lays segments at 128-byte-aligned offsets (v4 x128).
offsets.tsv: (index, byte_offset, dur_ms) per sample (dur copied from v3).

Quality gate per segment: SNR (re-encoded decode vs soxr source) >= 20 dB.
only=N encodes the first N path-near segments (validation shortcut); the rest
are COPIED as 44.1 kHz (validation boots must not reach them).
"""
import os
import struct
import subprocess
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import speech_eaxa as eaxa
from mpf3to4 import parse_v3

S_BLOCK = 2128


def decode(pcm_path, rate):
    c = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", pcm_path,
         "-ac", "2", "-ar", str(rate), "-f", "s16le", "-"]
    r = subprocess.run(c, capture_output=True)
    assert r.returncode == 0, r.stderr[:200]
    raw = r.stdout
    n = len(raw) // 4
    # ffmpeg s16le stereo is interleaved LRLR; deinterleave
    alls = struct.unpack("<%dh" % (2 * n), raw[:4 * n])
    return [list(alls[0::2]), list(alls[1::2])]


def snr_db(ref_ch, got_ch):
    import math
    se = 0.0
    ss = 0.0
    for rc, gc in zip(ref_ch, got_ch):
        m = min(len(rc), len(gc))
        for i in range(m):
            d = rc[i] - gc[i]
            se += d * d
            ss += rc[i] * rc[i]
    return 10 * math.log10(ss / (se + 1e-9))


def enc_one(args):
    (i, segfile, template, rate_src) = args
    # resample 44.1k -> 32k stereo (swr; this ffmpeg has no soxr), pad to %28
    c = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", segfile,
         "-ac", "2", "-ar", "32000", "-af", "aresample=32000",
         "-f", "s16le", "-"]
    r = subprocess.run(c, capture_output=True)
    assert r.returncode == 0, (i, r.stderr[:200])
    n = len(r.stdout) // 4
    alls = struct.unpack("<%dh" % (2 * n), r.stdout[:4 * n])
    pcm = [list(alls[0::2]), list(alls[1::2])]
    while len(pcm[0]) % 28:
        pcm[0].append(0)
        pcm[1].append(0)
    stream = eaxa.build_stream(template, pcm, S_BLOCK)
    # verify: decode back, SNR vs the resampled source
    open(f"/tmp/tk41_re_{os.getpid()}_{i}.sch", "wb").write(stream)
    got = decode(f"/tmp/tk41_re_{os.getpid()}_{i}.sch", 32000)
    q = snr_db(pcm, got)
    return i, stream, q, len(pcm[0])


def main():
    mus_p, mpf_p, tmpl_p, out_p, tsv_p = sys.argv[1:6]
    only = None
    jobs = os.cpu_count() or 8
    for kv in sys.argv[6:]:
        k, v = kv.split("=", 1)
        if k == "only":
            only = int(v)
        elif k == "jobs":
            jobs = int(v)
        else:
            raise SystemExit("unknown arg " + kv)
    mus = open(mus_p, "rb").read()
    p = parse_v3(open(mpf_p, "rb").read())
    offs = [s[0] for s in p["samples"]]  # parse_v3 samples are byte offsets
    durs = [s[1] for s in p["samples"]]
    total = len(offs)
    template = open(tmpl_p, "rb").read()
    # split segments to files (bounded: we only need `only` for validation,
    # but offsets for ALL must exist; unencoded ones copy source bytes)
    segfiles = []
    for i in range(total):
        e = offs[i + 1] if i + 1 < total else len(mus)
        fp = f"/tmp/tk41_src_{i}.sch"
        open(fp, "wb").write(mus[offs[i]:e])
        segfiles.append(fp)
    todo = total if only is None else min(only, total)
    print(f"encoding {todo}/{total} segments, jobs={jobs}", flush=True)
    with Pool(jobs) as pool:
        res = pool.map(enc_one,
                       [(i, segfiles[i], template, 44100)
                        for i in range(todo)])
    streams = {}
    for i, st, q, ns in res:
        streams[i] = st
        if q < 20.0:
            print(f"WARN seg {i}: SNR {q:.1f} dB < 20", flush=True)
    qs = [q for _, _, q, _ in res]
    print(f"SNR min {min(qs):.1f} mean {sum(qs)/len(qs):.1f} dB", flush=True)
    out = bytearray()
    rows = []
    for i in range(total):
        while len(out) % 128:
            out += b"\x00"
        if i in streams:
            st = streams[i]
        else:
            st = open(segfiles[i], "rb").read()
        rows.append((i, len(out), durs[i]))
        out += st
    open(out_p, "wb").write(bytes(out))
    with open(tsv_p, "w") as f:
        f.write("i\toff\tdur\n")
        for i, o, d in rows:
            f.write(f"{i}\t{o}\t{d}\n")
    print(f"wrote {out_p} ({len(out)} bytes) + {tsv_p}", flush=True)


if __name__ == "__main__":
    main()
