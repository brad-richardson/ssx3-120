#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TKP4b: assign Tricky songs to stock music slots (in-place fit).

Fit rule (TK41b shape): Tricky .mpf/.mus must each fit the stock song's home
slot (offsets stay home, sizes shrink, total same). One stock slot = one
(mpf, mus) pair in one BIG. Loop banks and hub/menu/stinger songs stay stock.

Usage: music_assign.py <stage-members-dir>   (prints the fit matrix + assignment TSV)

Port of TKP4b tkp4b_assign.py (port adaptations only: STAGE hardcode becomes
argv[1]; main() under a __main__ guard).
"""
import os
import sys

STAGE = None  # set by main() from argv (port: was a hardcoded lane path)

# stock slot: (big, mpf_member, mpf_size, mus_member, mus_size)
SLOTS = [
    # MUSIC.BIG pool songs (hub/menu mapsel/pktrans/sphub*/spokehub stay stock)
    (1, "bep", 12468, "bep.mus", 18401280),
    (1, "go", 20124, "go.mus", 28713216),
    (1, "hground", 13776, "hground.mus", 17134080),
    (1, "irobot", 13820, "irobot.mus", 16355840),
    (1, "janes", 13816, "janes.mus", 20107776),
    (1, "kinky", 10752, "kinky.mus", 17318400),
    (1, "kos", 12584, "kos.mus", 21511296),
    (1, "leave", 11324, "leave.mus", 17240576),
    (1, "likethis", 10304, "likethis.mus", 12666752),
    (1, "nerd", 11568, "nerd.mus", 16914560),
    (1, "poorleno", 15484, "poorleno.mus", 21720832),
    (1, "qoftsa", 14812, "qoftsa.mus", 18222592),
    (1, "ride", 19240, "ride.mus", 27789696),
    (1, "screwup", 12172, "screwup.mus", 7224064),
    (1, "snowptrl", 16516, "snowptrl.mus", 25925888),
    (1, "theman", 8304, "theman.mus", 15119104),
    (1, "thrice", 18556, "thrice.mus", 21563136),
    (1, "wobble", 23160, "wobble.mus", 23967744),
    (1, "yellow", 19136, "yellow.mus", 20025984),
    (1, "mxpx", 15456, "mxpxsong.mus", 21362816),
    # MUSIC2.BIG pool songs (charsel/chartune/Peak*Amb stay stock)
    (2, "abullys", 12128, "abullys.mus", 16293376),
    (2, "allnight", 13688, "allnight.mus", 20547072),
    (2, "apo", 19848, "apo.mus", 19655680),
    (2, "auto", 13336, "auto.mus", 23920640),
    (2, "avalnch", 17604, "avalnch.mus", 26051584),
    (2, "bjaxx", 11188, "bjaxx.mus", 13697280),
    (2, "buffet", 15440, "buffet.mus", 20757504),
    (2, "caesars", 12792, "caesars.mus", 17951744),
    (2, "deepend", 17772, "deepend.mus", 19720960),
    (2, "dilated", 10124, "dilated.mus", 18665472),
    (2, "felix", 15400, "felix.mus", 23920128),
    (2, "finger", 14920, "finger.mus", 21446656),
    (2, "fspoon", 18548, "fspoon.mus", 26027904),
    (2, "gdanse", 12108, "gdanse.mus", 18248704),
    (2, "placebo", 12720, "placebo.mus", 10774016),
]

SONGS = ["adamsrev", "bassinvader", "bburner", "downtime", "finsym", "ginandsin",
         "hiphop", "kingbeat", "moveit", "peaktime", "reality", "shakemomma",
         "slayboarder", "slaybreak", "smartbomb", "songfordot", "ssxmenu",
         "superwoman", "systemover", "topbomb"]


def sizes(song):
    mpf = os.path.getsize(f"{STAGE}/{song}.mpf")
    mus = os.path.getsize(f"{STAGE}/{song}.mus")
    return mpf, mus


def main(argv=None):
    global STAGE
    a = argv if argv is not None else sys.argv[1:]
    if not a:
        raise SystemExit("usage: music_assign.py <stage-members-dir>")
    STAGE = a[0]
    sz = {s: sizes(s) for s in SONGS}
    # fit matrix
    print("song\tmpf\tmus\tslots-that-fit")
    usable = []
    for s in SONGS:
        fit = [slot for (_, slot, mpf, _, mus) in SLOTS
               if sz[s][0] <= mpf and sz[s][1] <= mus]
        print(f"{s}\t{sz[s][0]}\t{sz[s][1]}\t{len(fit)}")
        if fit:
            usable.append(s)
        else:
            print(f"  NO FIT: {s} left out", file=sys.stderr)
    print(f"\nusable songs: {len(usable)}", file=sys.stderr)

    # slots nothing fits
    for (big, slot, mpf, _, mus) in SLOTS:
        if not any(sz[s][0] <= mpf and sz[s][1] <= mus for s in SONGS):
            print(f"  SLOT STOCK: B{big} {slot} (mpf {mpf}, mus {mus})", file=sys.stderr)

    # Constrained-first best-fit: songs with the fewest fitting slots place
    # first (2 copies each, or as many as fit); each copy takes the smallest
    # fitting free slot. A 3rd copy fills any remainder. Duplication is safe:
    # each slot's .mpf indexes only its own .mus bytes.
    fit = {}
    for s in usable:
        fit[s] = [(mus, mpf, f"B{big}/{slot}")
                  for (big, slot, mpf, _, mus) in SLOTS
                  if sz[s][0] <= mpf and sz[s][1] <= mus]
    order = sorted(usable, key=lambda s: (len(fit[s]), -sz[s][1]))
    print("place order:", [(s, len(fit[s])) for s in order], file=sys.stderr)
    use = {s: 0 for s in usable}
    assign = {}
    free = {f"B{big}/{slot}" for (big, slot, _, _, _) in SLOTS}
    for rnd in (1, 2):
        for s in order:
            cands = sorted([f for f in fit[s] if f[2] in free])
            if not cands:
                continue
            pick = cands[0][2]
            free.discard(pick)
            use[s] += 1
            assign[pick] = s
        print(f"after round {rnd}: free={len(free)}", file=sys.stderr)
    # remainder: 3rd copies, least-used fitting song per slot (ties: fullest).
    for key in sorted(free):
        (big, slot, mpf, _, mus) = next(x for x in SLOTS if f"B{x[0]}/{x[1]}" == key)
        cands = sorted([s for s in usable if sz[s][0] <= mpf and sz[s][1] <= mus],
                       key=lambda s: (use[s], -sz[s][1]))
        if cands:
            assign[key] = cands[0]
            use[cands[0]] += 1
            free.discard(key)
    for key in sorted(free):
        assign[key] = "STOCK"
    print("\nslot\ttricky\tmpf_fit\tmus_fit")
    for (big, slot, mpf, musm, mus) in SLOTS:
        key = f"B{big}/{slot}"
        t = assign[key]
        if t == "STOCK":
            print(f"{key}\tSTOCK\t-\t-")
        else:
            print(f"{key}\t{t}\t{sz[t][0]}<={mpf}\t{sz[t][1]}<={mus}")
    print("\nuses:", sorted(use.items(), key=lambda kv: kv[1]), file=sys.stderr)
    nstock = sum(1 for v in assign.values() if v == "STOCK")
    print(f"stock slots left: {nstock}", file=sys.stderr)


if __name__ == "__main__":
    main()
