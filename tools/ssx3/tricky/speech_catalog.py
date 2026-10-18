#!/usr/bin/env python3
"""speech_catalog.py: split .dat members into SCHl streams, decode each to WAV.

Port of TK25b tk25b_catalog.py (port adaptations only: WORK/VGM/EXT/STR/WAV
hardcodes become CLI args; main() under a __main__ guard so the module is
importable; conversion logic unchanged).

Writes <lines-tsv>: file, index, start, slot_bytes, samples, rate, channels,
seconds. Tricky (MicroTalk) via vgmstream-cli; SSX3 (EA-XA R1) via ffmpeg.

Usage:
  speech_catalog.py --vgm <vgmstream-cli> --extract-dir <dir with tricky/ + ssx3/>
      --streams-dir <dir> --wav-dir <dir> --lines-tsv <out>
The extract dir must already hold the member .dat files (see README).
"""
import argparse
import os
import re
import struct
import subprocess
import sys

TRICKY = ["Wipeout.dat", "MS_Wipeout.dat", "Uber.dat", "Big_Air.dat", "MS_Big_Air.dat",
          "Tricky.dat", "Turbo.dat", "Boost_Icon.dat", "Bonus.dat"]
SSX3 = ["Wipeout_Amateur_mac.dat", "Wipeout_Pro_mac.dat", "Wipeout_Expert_mac.dat",
        "Arcade_Uber.dat", "Big_Air_Amateur_mac.dat", "Big_Air_Pro_mac.dat",
        "Big_Air_Expert_mac.dat", "Arcade_Power_Ups.dat", "Arcade_Icons.dat"]


def streams_of(path):
    d = open(path, "rb").read()
    pos = [m.start() for m in re.finditer(b"SCHl", d)]
    out = []
    for i, p in enumerate(pos):
        end = pos[i + 1] if i + 1 < len(pos) else len(d)
        e = d.find(b"SCEl", p)
        assert e != -1 and e < end, (path, i)
        esz = struct.unpack("<I", d[e + 4:e + 8])[0]
        out.append((p, end - p, d[p:e + esz]))
    return out


def wav_info(path):
    r = subprocess.run(["ffprobe", "-hide_banner", "-v", "error",
                        "-select_streams", "a:0",
                        "-show_entries", "stream=sample_rate,channels,bits_per_sample",
                        "-show_entries", "format=size", "-of", "default=noprint_wrappers=1", path],
                       capture_output=True, text=True)
    info = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, v = line.strip().split("=", 1)
            info[k] = v
    import wave
    with wave.open(path, "rb") as w:
        n = w.getnframes()
        info["samples"] = n
        info["sample_rate"] = w.getframerate()
        info["channels"] = w.getnchannels()
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vgm", required=True, help="vgmstream-cli binary")
    ap.add_argument("--extract-dir", required=True,
                    help="dir with tricky/ and ssx3/ member .dat files")
    ap.add_argument("--streams-dir", required=True)
    ap.add_argument("--wav-dir", required=True)
    ap.add_argument("--lines-tsv", required=True)
    a = ap.parse_args()
    os.makedirs(a.streams_dir, exist_ok=True)
    rows = []
    # Tricky
    for fn in TRICKY:
        src = os.path.join(a.extract_dir, "tricky", fn)
        for i, (start, slot_bytes, blob) in enumerate(streams_of(src)):
            stem = f"tricky-{fn[:-4]}-{i}"
            sp = os.path.join(a.streams_dir, stem + ".str")
            wp = os.path.join(a.wav_dir, "tricky", stem + ".wav")
            open(sp, "wb").write(blob)
            os.makedirs(os.path.dirname(wp), exist_ok=True)
            r = subprocess.run([a.vgm, "-o", wp, sp], capture_output=True, text=True)
            if not os.path.exists(wp):
                print(f"FAIL decode {fn} stream {i}: {r.stdout} {r.stderr}")
                sys.exit(1)
            # parse vgmstream output for rate/ch
            rate = ch = None
            for line in (r.stdout + r.stderr).splitlines():
                if "sample rate:" in line:
                    rate = int(line.split(":")[1].split()[0])
                if line.startswith("channels:"):
                    ch = int(line.split(":")[1].strip())
            info = wav_info(wp)
            samples = info["samples"]
            rows.append((fn, i, start, slot_bytes, samples, info["sample_rate"],
                         info["channels"], round(samples / info["sample_rate"], 3)))
            print(f"{fn} [{i}] start={start} slot={slot_bytes} samples={samples} "
                  f"{info['sample_rate']}Hz {info['channels']}ch {samples/info['sample_rate']:.3f}s")
    # SSX3
    for fn in SSX3:
        src = os.path.join(a.extract_dir, "ssx3", fn)
        for i, (start, slot_bytes, blob) in enumerate(streams_of(src)):
            stem = f"ssx3-{fn[:-4]}-{i}"
            sp = os.path.join(a.streams_dir, stem + ".ea")
            wp = os.path.join(a.wav_dir, "ssx3", stem + ".wav")
            open(sp, "wb").write(blob)
            os.makedirs(os.path.dirname(wp), exist_ok=True)
            r = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                                "-i", sp, wp], capture_output=True, text=True)
            if r.returncode != 0 or not os.path.exists(wp):
                print(f"FAIL decode {fn} stream {i}: {r.stderr[-2000:]}")
                sys.exit(1)
            info = wav_info(wp)
            samples = info["samples"]
            rows.append((fn, i, start, slot_bytes, samples, info["sample_rate"],
                         info["channels"], round(samples / info["sample_rate"], 3)))
            print(f"{fn} [{i}] start={start} slot={slot_bytes} samples={samples} "
                  f"{info['sample_rate']}Hz {info['channels']}ch {samples/info['sample_rate']:.3f}s")
    with open(a.lines_tsv, "w") as f:
        f.write("file\tindex\tstart\tslot_bytes\tsamples\trate\tchannels\tseconds\n")
        for r in rows:
            f.write("\t".join(map(str, r)) + "\n")
    print(f"wrote {a.lines_tsv} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
