#!/usr/bin/env python3
"""tk25b_check_slots.py <orig.dat> <built.dat> <built.dat.decisions.tsv> <receipts-prefix>
Acceptance: output size == input size; SCHl offset list identical; unreplaced
slots byte-identical (cmp per range); every replaced slot decodes with ffmpeg.
Appends rows to <receipts-prefix>-slots-built.tsv (created with header if missing):
target, slot, source, seconds, bytes_used, slot_bytes.
"""
import os, re, struct, subprocess, sys

def schl_offsets(dat):
    return [m.start() for m in re.finditer(b"SCHl", dat)]

def slot_ranges(dat):
    pos = schl_offsets(dat)
    return [(p, (pos[i + 1] if i + 1 < len(pos) else len(dat)) - p) for i, p in enumerate(pos)]

def main():
    orig_p, built_p, dec_p, prefix = sys.argv[1:5]
    orig = open(orig_p, "rb").read()
    built = open(built_p, "rb").read()
    assert len(built) == len(orig), (len(built), len(orig))
    print(f"size OK: {len(built)}")
    assert schl_offsets(built) == schl_offsets(orig), "SCHl offsets differ"
    print(f"SCHl offsets OK: {len(schl_offsets(built))} streams")
    ranges = slot_ranges(orig)
    decisions = {}
    for line in open(dec_p):
        if line.startswith("slot"):
            continue
        idx, src, sec, used, total = line.rstrip("\n").split("\t")
        decisions[int(idx)] = (src, float(sec), int(used), int(total))
    target = os.path.basename(orig_p)
    out_tsv = prefix + "-slots-built.tsv"
    new = not os.path.exists(out_tsv)
    n_rep = n_keep = 0
    with open(out_tsv, "a") as f:
        if new:
            f.write("target\tslot\tsource\tseconds\tbytes_used\tslot_bytes\n")
        for i, (start, size) in enumerate(ranges):
            src, sec, used, total = decisions[i]
            assert total == size, (i, total, size)
            ob, nb = orig[start:start + size], built[start:start + size]
            if src == "KEEP-ORIGINAL":
                assert ob == nb, f"slot {i} marked keep but differs"
                n_keep += 1
            else:
                assert ob != nb, f"slot {i} marked replaced but identical"
                assert used <= size
                # trimmed stream must decode with ffmpeg
                e = nb.index(b"SCEl")
                esz = struct.unpack("<I", nb[e + 4:e + 8])[0]
                tmp = f"/tmp/tk25b_check_{i}.ea"
                open(tmp, "wb").write(nb[:e + esz])
                r = subprocess.run(["ffprobe", "-hide_banner", "-v", "error",
                                    "-show_entries", "stream=codec_name",
                                    "-of", "default=noprint_wrappers=1", tmp],
                                   capture_output=True, text=True)
                assert "adpcm_ea_r1" in r.stdout, (i, r.stdout, r.stderr[-300:])
                os.remove(tmp)
                n_rep += 1
            f.write(f"{target}\t{i}\t{src}\t{sec:.3f}\t{used}\t{total}\n")
    print(f"{target}: {n_rep} replaced (all decode as adpcm_ea_r1), {n_keep} kept, "
          f"unreplaced byte-identical")


if __name__ == "__main__":
    main()
