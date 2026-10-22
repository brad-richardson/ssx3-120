#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""mpf3to4.py: Tricky (.mpf v3) -> SSX 3 (.mpf v4) Pathfinder converter (TK41).

TK41b lane copy of MUS1's fixed converter, plus check_graph_v4(): a
standalone graph self-check that fails a v4 .mpf if the game's unbounded
node walker (0x3D3900) can hang on it. Rules: (a) from every event's
BRANCHTO target, for every intensity 0..127, the zero-progress walk (sample
<= 0 nodes) must reach a sample > 0 node without revisiting a sample <= 0
node; (b) no subentry targets a node >= the node count; (c) no sample index
is out of range. Pick semantics are the game's own (0x3D37A0: first
lo <= v <= hi wins, else the nearest range; link loop counters are NOT
modeled, so the check over-approximates — the stock 25/25 baseline decides
whether any real song needs a counter to terminate its walk).

MUS1 lane copy: nodes are emitted with the game's own node model
(v4_nodes_from_v3, read from SSX 3's walker 0x3D3900/0x3D37A0 and scheduler
0x3D41A8): the header s16 is the 1-based sample to play (0 = head, -1 = link,
both pass-through) and every subentry is [lo:s8, hi:s8, next:s16], a range
branch on the track's intensity byte. v3 uses the same model, so nodes map
1:1 with the sample and subentries verbatim. TK41's emitter read subentries
as [trigger, cmd, sample] and wrote `00 64 <sample> | 64 7f <next>`, which the
game reads as "intensity 0..100 -> node <sample id>"; node 0 then pointed at
itself and the walk spun (MUS1 REPORT).

Subcommands:
  walk <file.mpf> [mus bytes ...]  print the header/node/track/sample walk
  convert <v3.mpf> <v4.mpf>          (TK41 step 2; needs v3 samples to verify)

Layout reference: vgmstream src/meta/ea_schl_map_mpf_mus.c (function
init_vgmstream_ea_mpf_mus_schl_main). PS2 files are little-endian ("xDFP").
Game-reader evidence: SSX 3 open 0x3D2350 requires magic 'xDFP' + byte[4]==4
(TK25 REPORT section 1).
"""
import struct
import sys


def u8(b, o):
    return b[o]


def u16le(b, o):
    return struct.unpack_from("<H", b, o)[0]


def u32le(b, o):
    return struct.unpack_from("<I", b, o)[0]


def u32be(b, o):
    return struct.unpack_from(">I", b, o)[0]


def align4(n):
    return (n + 3) & ~3


def walk_v4(b):
    """Follow the vgmstream v4 walk; return a dict of section offsets."""
    assert b[0:4] == b"xDFP", b[0:4]
    ver, sub = b[4], b[5]
    assert ver == 4, (ver, sub)
    n_tracks = u8(b, 0x0D)
    n_sections = u8(b, 0x0E)
    n_events = u8(b, 0x0F)
    n_routers = u8(b, 0x10)
    n_vars = u8(b, 0x11)
    n_nodes = u16le(b, 0x12)
    out = dict(version=ver, subversion=sub, tracks=n_tracks,
               sections=n_sections, events=n_events, routers=n_routers,
               vars=n_vars, nodes=n_nodes)
    sec = 0x20
    nodes = [u16le(b, sec + i * 2) for i in range(n_nodes)]
    out["node_table"] = sec
    out["nodes_last"] = nodes[-1] if nodes else None
    entry = nodes[-1] * 4
    subentry_num = (u32be(b, entry + 0x04) >> 20) & 0x0F
    out["node_entry"] = entry
    out["node_subentries"] = subentry_num
    sec = entry + 0x10 + subentry_num * 4
    out["event_table"] = sec
    events = [u16le(b, sec + i * 2) for i in range(n_events)]
    entry = events[-1] * 4
    subentry_num = (u32be(b, entry + 0x0C) >> 8) & 0x3F
    out["event_entry"] = entry
    out["event_subentries"] = subentry_num
    sec = entry + 0x10 + subentry_num * 0x10
    sec += n_routers * 4
    out["router_end"] = sec
    sec = u32le(b, sec) * 4
    tracks_table = sec
    samples_table = tracks_table + (n_tracks + 1) * 4
    eof = u32le(b, tracks_table + n_tracks * 4) * 4
    total = (eof - samples_table) // 8
    out["tracks_table"] = tracks_table
    out["samples_table"] = samples_table
    out["eof"] = eof
    out["total_streams"] = total
    out["off_mult"] = 0x80
    track_bounds = []
    for i in range(n_tracks):
        s = u32le(b, tracks_table + i * 4) * 4
        track_bounds.append((s - samples_table) // 8)
    track_bounds.append(total)
    out["track_bounds"] = track_bounds
    samples = []
    for i in range(total):
        off = u32le(b, samples_table + i * 8)
        dur = u32le(b, samples_table + i * 8 + 4)
        samples.append((off, dur))
    out["samples"] = samples
    return out


def walk_v3(b):
    """Follow the vgmstream v3 (sub 1/2: SSX Tricky, Sled Storm) walk.

    UNVERIFIED: no v3 sample readable in this session (TK41 share block).
    """
    assert b[0:4] == b"xDFP", b[0:4]
    ver, sub = b[4], b[5]
    assert ver == 3 and sub in (1, 2), (ver, sub)
    n_tracks = u8(b, 0x0D)
    n_sections = u8(b, 0x0E)
    n_events = u8(b, 0x0F)
    n_routers = u8(b, 0x10)
    n_vars = u8(b, 0x11)
    n_nodes = u16le(b, 0x12)
    out = dict(version=ver, subversion=sub, tracks=n_tracks,
               sections=n_sections, events=n_events, routers=n_routers,
               vars=n_vars, nodes=n_nodes)
    sec = 0x24
    nodes = [u16le(b, sec + i * 2) for i in range(n_nodes)]
    out["node_table"] = sec
    entry = nodes[-1] * 4
    subentry_num = u8(b, entry + 0x0B)
    out["node_entry"] = entry
    out["node_subentries"] = subentry_num
    sec = entry + 0x0C + subentry_num * 4
    sec += align4(n_events * n_tracks * n_sections)
    sec += n_routers * 4
    sec += n_vars * 4
    tracks_table = u32le(b, sec) * 4
    samples_table = tracks_table + n_tracks * 4
    eof = len(b)
    total = (eof - samples_table) // 8
    out["tracks_table"] = tracks_table
    out["samples_table"] = samples_table
    out["eof"] = eof
    out["total_streams"] = total
    out["off_mult"] = 0x04
    return out


def check_graph_v4(path):
    """TK41b graph self-check on a v4 .mpf. Returns a list of failure
    strings (empty = pass). Fails the song if:
      (a) from some event's BRANCHTO target, at some intensity 0..127, the
          zero-progress walk (sample <= 0 nodes) cycles without reaching a
          sample > 0 node, or leaves the node table;
      (b) any subentry targets a node >= the node count;
      (c) any sample index is out of range (1..total streams).
    Walk/pick semantics are the game's (0x3D3900/0x3D37A0): while the node
    sample s16 <= 0, pick the next node (first lo <= v <= hi wins; else the
    nearest range by min(|lo-v|,|hi-v|), ties to the first, distance init
    0x7F); n=0 nodes pick -1. Link loop counters are not modeled. Event
    targets outside [0, N) take the game's "bad node ID" path (0x3CFF60-90),
    not the walker, so they are skipped and counted, not walked: 21 of 25
    stock songs carry such sentinel targets (e.g. go e2 -> node -17)."""
    b = open(path, "rb").read()
    if b[0:4] != b"xDFP" or b[4] != 4:
        return [f"{path}: not a v4 mpf (magic={b[0:4]!r} ver={b[4]})"]
    fails = []
    N = u16le(b, 0x12)
    n_events = u8(b, 0x0F)
    total = walk_v4(b)["total_streams"]
    offs = [u16le(b, 0x20 + i * 2) * 4 for i in range(N)]
    samples, subs_of = [], []
    for i, e in enumerate(offs):
        s = struct.unpack_from("<h", b, e)[0]
        cnt = (u32le(b, e + 4) >> 12) & 0x1F
        subs = []
        for j in range(cnt):
            lo = struct.unpack_from("<b", b, e + 16 + j * 4)[0]
            hi = struct.unpack_from("<b", b, e + 16 + j * 4 + 1)[0]
            nxt = struct.unpack_from("<h", b, e + 16 + j * 4 + 2)[0]
            subs.append((lo, hi, nxt))
            if nxt >= N:                                    # rule (b)
                fails.append(f"node {i} sub {j}: next {nxt} >= node count {N}")
        if s > 0 and not (1 <= s <= total):                 # rule (c)
            fails.append(f"node {i}: sample {s} out of range 1..{total}")
        samples.append(s)
        subs_of.append(subs)

    def pick(subs, v):
        for lo, hi, nxt in subs:                            # 0x3d3840..74
            if lo <= v <= hi:
                return nxt                                  # s16
        if not subs:                                        # 0x3d387c..88
            return -1
        best, bd = subs[0][2] & 0xFFFF, 0x7F                # 0x3d388c..f0
        for lo, hi, nxt in subs:                            # nearest range
            d = min(abs(lo - v), abs(hi - v))
            if d < bd:
                bd, best = d, nxt & 0xFFFF                 # lhu at 0x3d38f0
        return best if best < 0x8000 else best - 0x10000

    # event table (same layout walk_v4 uses)
    last = offs[-1]
    cnt_last = (u32le(b, last + 4) >> 12) & 0x1F
    evt = last + 0x10 + cnt_last * 4
    n_skipped_badnode = 0
    for e in range(n_events):                               # rule (a)
        ee = u16le(b, evt + e * 2) * 4
        ns = u16le(b, ee + 0xE) & 0x3F
        for k in range(ns):
            sub = b[ee + 16 + k * 16:ee + 32 + k * 16]
            if sub[9] != 4:                                 # not BRANCHTO
                continue
            t = struct.unpack_from("<h", sub, 12)[0]
            if not (0 <= t < N):
                # game's "bad node ID" path, never walked (stock sentinel)
                n_skipped_badnode += 1
                continue
            for v in range(128):
                seen = set()
                cur = t
                while True:
                    if not (0 <= cur < N):
                        fails.append(
                            f"event {e} sub {k} v={v}: walk leaves table "
                            f"at node {cur} (target {t})")
                        break
                    if samples[cur] > 0:
                        break                               # plays: progress
                    if cur in seen:
                        fails.append(
                            f"event {e} sub {k} v={v}: zero-progress cycle "
                            f"at node {cur} (target {t})")
                        break
                    seen.add(cur)
                    cur = pick(subs_of[cur], v)
                    if len(seen) > N + 64:                  # unreachable
                        fails.append(
                            f"event {e} sub {k} v={v}: walk exceeded "
                            f"{N + 64} steps (target {t})")
                        break
    info = f"N={N} events={n_events} streams={total} " \
        f"skipped-badnode-targets={n_skipped_badnode}"
    return fails, info


def cmd_check(paths):
    rc = 0
    for p in paths:
        fails, info = check_graph_v4(p)
        if fails:
            rc = 1
            print(f"FAIL {p} ({len(fails)}; {info}):")
            for f in fails[:10]:
                print(f"  {f}")
            if len(fails) > 10:
                print(f"  ... +{len(fails) - 10} more")
        else:
            print(f"PASS {p} ({info})")
    return rc


def cmd_walk(path, mus_paths):
    b = open(path, "rb").read()
    print(f"file={path} bytes={len(b)} magic={b[0:4]} ver={b[4]} sub={b[5]}")
    print("hdr16=" + b[0:32].hex(" "))
    w = walk_v4(b) if b[4] == 4 else walk_v3(b)
    for k in ("version", "subversion", "tracks", "sections", "events",
              "routers", "vars", "nodes", "node_table", "node_entry",
              "node_subentries", "event_table", "event_entry",
              "event_subentries", "router_end", "tracks_table",
              "samples_table", "eof", "total_streams", "off_mult",
              "track_bounds"):
        if k in w:
            v = w[k]
            print(f"  {k}={v if not isinstance(v, int) else hex(v)}")
    if "samples" in w:
        muss = [open(p, "rb").read() for p in mus_paths]
        print(f"  samples={len(w['samples'])} (off x{hex(w['off_mult'])} -> mus byte, dur ms, SCHl?)")
        for i, (off, dur) in enumerate(w["samples"]):
            byte = off * w["off_mult"]
            # find which mus (track) this sample belongs to
            tag = "?"
            for m in muss:
                if byte + 4 <= len(m) and m[byte:byte + 4] == b"SCHl":
                    tag = "SCHl"
                    break
                elif byte + 4 <= len(m):
                    tag = m[byte:byte + 4].hex()
            print(f"    [{i}] off={hex(off)} byte={hex(byte)} dur={dur} {tag}")


def parse_v3(b):
    """Full v3 parse. Returns dict with counts, nodes, blob, routers, samples."""
    assert b[0:4] == b"xDFP" and b[4] == 3 and b[5] in (1, 2)
    T, S, E, R, V = b[0x0D], b[0x0E], b[0x0F], b[0x10], b[0x11]
    N = u16le(b, 0x12)
    offs = [u16le(b, 0x24 + i * 2) * 4 for i in range(N)]
    nodes = []
    for i, e in enumerate(offs):
        n = b[e + 0x0B]
        hdr = bytes(b[e:e + 12])
        ss = [bytes(b[e + 0x0C + j * 4:e + 0x10 + j * 4]) for j in range(n)]
        link = hdr[0:2] == b"\xff\xff"
        head = hdr[0:2] == b"\x00\x00"
        branchmark = hdr[8:10] == b"\x00\xff"
        nd = dict(idx=i, off=e, n=n, hdr=hdr, subs=ss, link=link,
                  head=head, branchmark=branchmark, play=None, nxt=None,
                  cond=None)
        if n == 0:
            pass
        elif n == 1 and ss[0][1] == 0x7F:
            nd["nxt"] = ss[0][2] + (ss[0][3] << 8)
            if not link:
                nd["play"] = hdr[0] + (hdr[1] << 8)
        elif (n == 2 and ss[0][0] == 0 and ss[1][1] == 0x7F
                and ss[0][1] != 0):
            # play + completion-goto; the tag need not echo the play cmd
            # (shakemomma: play 0x4b, tag 0x27).
            nd["play"] = ss[0][2] + (ss[0][3] << 8)
            nd["nxt"] = ss[1][2] + (ss[1][3] << 8)
            nd["k"] = ss[0][1]
        elif n == 2 and ss[0][1] == 0x00:
            nd["nxt"] = ss[0][2] + (ss[0][3] << 8)
            nd["cond"] = (ss[1][0], ss[1][2] + (ss[1][3] << 8))
            assert ss[1][1] == 0x7F
        elif (n >= 3 and ss[0][0] == 0 and ss[-1][1] == 0x7F
                and all(ss[j][1] == ss[j + 1][0] for j in range(n - 1))):
            # play...-play-goto chain, any length (shakemomma has n=4).
            nd["play"] = [ss[j][2] + (ss[j][3] << 8) for j in range(n - 1)]
            nd["nxt"] = ss[-1][2] + (ss[-1][3] << 8)
            nd["chain"] = True
        else:
            raise SystemExit(f"node {i}: unhandled shape n={n} "
                             + str([s.hex() for s in ss]))
        nodes.append(nd)
    last = offs[-1]
    sec = last + 0x0C + b[last + 0x0B] * 4
    blob = bytes(b[sec:sec + E * T * S])
    rsec = sec + align4(E * T * S)
    routers = [bytes(b[rsec + i * 4:rsec + i * 4 + 4]) for i in range(R)]
    vsec = rsec + R * 4
    tptr = u32le(b, vsec + V * 4) * 4
    tt = tptr
    st = tt + T * 4
    total = (len(b) - st) // 8
    samples = [(u32le(b, st + i * 8) * 4, u32le(b, st + i * 8 + 4))
               for i in range(total)]
    for nd in nodes:
        pl = nd["play"]
        if pl is None:
            pass
        elif isinstance(pl, list):
            while pl and pl[-1] >= total:
                pl.pop()
                nd["endmark"] = True
            for s in pl:
                assert 0 <= s < total, (nd["idx"], s, total)
            if not pl:
                nd["play"] = None
            elif len(pl) == 1:
                nd["play"] = pl[0]
        elif pl >= total:
            # endmark family: total (peaktime) or total+1 (ginandsin,
            # systemover) — the engine ends the path on out-of-range play.
            nd["endmark"] = True
            nd["play"] = None
        else:
            assert 0 <= pl < total, (nd["idx"], pl, total)
        if nd["nxt"] is not None:
            assert 0 <= nd["nxt"] < N, (nd["idx"], nd["nxt"])
    return dict(T=T, S=S, E=E, R=R, V=V, N=N, nodes=nodes, blob=blob,
                routers=routers, samples=samples, raw=bytes(b))


def default_path(nodes, start=0, limit=2000):
    """Follow nxt edges; return (path, loopback_or_None)."""
    seen = set()
    cur = start
    path = []
    while cur is not None and cur not in seen and len(path) < limit:
        seen.add(cur)
        path.append(cur)
        cur = nodes[cur]["nxt"]
    return path, cur


def v4_nodes_from_v3(b):
    """v3 nodes -> v4 nodes (MUS1). Returns [(hdr16, [sub4...]), ...].

    v4 header (census of 7 stock .mpf + SSX 3 code):
      +0  s16 sample (1-based, 0 head, -1 link)           = v3 +0 verbatim
      +2  u16 track bits 0-4 (0) | section bits 5-10       = v3 +3 & 0x7F
          | loop nibble bits 12-15                         = v3 +9 & 0xF
          (links take the section of the nearest preceding non-link)
      +4  u32 router byte 0 | count<<12 | mode 0 | 1<<20   (count = v3 +0xB)
          | beats<<24                                      = v3 +5 (1 or 4)
      +8  u32 0x40 on sample nodes | ref<<20: sample nodes -> index of the
          nearest preceding head; heads/links -> their head/link ordinal
      +C  u32 0
    Subentries [lo, hi, next] copied verbatim (same model in v3)."""
    T, N = b[0x0D], u16le(b, 0x12)
    offs = [u16le(b, 0x24 + i * 2) * 4 for i in range(N)]
    out, sec, head, ordinal = [], 1, 0, 0
    for i, e in enumerate(offs):
        h = b[e:e + 12]
        n = h[11]
        s = struct.unpack_from("<h", h, 0)[0]
        if h[3] != 0xFF:
            sec = h[3] & 0x7F
        assert 0 < sec < 0x40, (i, h.hex())
        if s > 0:
            ref = head
        else:
            ref = ordinal
            ordinal += 1
            if s == 0:
                head = i
        assert ref < 0x1000, (i, ref)
        w2 = (0x40 if s > 0 else 0) | (ref << 20)
        hdr = (h[0:2] + struct.pack("<H", (sec << 5) | ((h[9] & 0xF) << 12))
               + struct.pack("<III", (n << 12) | (1 << 20)
                             | ((h[5] & 0xF) << 24), w2, 0))
        subs = [bytes(b[e + 12 + 4 * j:e + 16 + 4 * j]) for j in range(n)]
        for sb in subs:
            assert 0 <= struct.unpack_from("<h", sb, 2)[0] < N, (i, sb.hex())
        out.append((hdr, subs))
    return out


def emit_v4(p, loop_to_zero=True, two_tracks=False, shotgun_events=False):
    """Translate parsed v3 song to v4 bytes. Tracks=1 (2 with two_tracks,
    duplicating samples 0..13 as the tail track), events padded to 44.
    shotgun_events: empty pad events 11..43 get e=0-style restart subs
    (goto node 0, c=1 + 5x0x0a) to test whether any race event starts
    playback (b23 probe)."""
    # MUS1: nodes 1:1 in the game's model (v4_nodes_from_v3); TK41's
    # play/chain/branch reading and the empty->node 0 retarget are gone.
    N = p["N"]
    out_nodes = v4_nodes_from_v3(p["raw"])
    assert len(out_nodes) == N
    # events: E mapped via routers + pad to 44
    E4 = 44
    assert p["T"] == 1
    blob = p["blob"]
    S = p["S"]
    events = []
    for e in range(E4):
        if e < p["E"]:
            vals = set(blob[e * S:(e + 1) * S])
            assert len(vals) == 1, (e, vals)
            r = blob[e * S]
            assert r < len(p["routers"]), (e, r)
            rb = p["routers"][r]
            assert rb[0] == 0xFF, (e, r, rb.hex())
            cond, tgt = rb[1], rb[2] + (rb[3] << 8)
            if tgt == 0xFFFF:
                events.append((e, []))
            else:
                # e=0 (song start) is c=1 in all 20 stock race songs, with
                # five byte-identical 0x0a setup subs (channels 1-5); v3
                # cond 0x81 must NOT map to c=2 here (b14/b20 silence).
                c = 0x01 if (cond == 0x01 or e == 0) else 0x02
                sub = bytes([c, 0, 0, 0xF1, 0, 0, 0, 0,
                             0, 0x04, 0, 0,
                             tgt & 0xFF, (tgt >> 8) & 0xFF, 0xFF, 0])
                subs = [sub]
                if e == 0:
                    for ch in range(1, 6):
                        subs.append(bytes([0x01, 0, 0, 0xF1, 0, 0, 0, 0,
                                           0, 0x0A, 0, 0,
                                           0xDF, 0xFF, ch, 0]))
                events.append((e, subs))
        elif shotgun_events:
            subs = [bytes([0x01, 0, 0, 0xF1, 0, 0, 0, 0,
                           0, 0x04, 0, 0, 0, 0, 0xFF, 0])]
            for ch in range(1, 6):
                subs.append(bytes([0x01, 0, 0, 0xF1, 0, 0, 0, 0,
                                   0, 0x0A, 0, 0,
                                   0xDF, 0xFF, ch, 0]))
            events.append((e, subs))
        else:
            events.append((e, []))
    # layout
    out = bytearray()
    out += b"xDFP" + bytes([4, 0, 0xB0, 0x08]) + bytes(4)
    out += bytes([0, 2 if two_tracks else 1, p["S"], E4, 0, 0]) \
        + struct.pack("<H", N) + bytes(12)
    assert len(out) == 0x20
    node_off_pos = len(out)
    out += bytes(2 * N)
    while len(out) % 4:
        out += b"\x00"
    node_offs = []
    for hdr, subs in out_nodes:
        assert len(out) % 4 == 0
        node_offs.append(len(out) // 4)
        out += hdr
        for s in subs:
            out += s
    for i, o in enumerate(node_offs):
        assert o <= 0xFFFF
        struct.pack_into("<H", out, node_off_pos + i * 2, o)
    evtab_pos = len(out)
    out += bytes(2 * E4)
    while len(out) % 4:
        out += b"\x00"
    ev_offs = []
    for e, subs in events:
        assert len(out) % 4 == 0
        ev_offs.append(len(out) // 4)
        out += bytes(12) + struct.pack("<HH", e, len(subs))
        for s in subs:
            out += s
    for i, o in enumerate(ev_offs):
        assert o <= 0xFFFF
        struct.pack_into("<H", out, evtab_pos + i * 2, o)
    # no routers; tracks-table pointer; tracks; samples
    assert len(out) % 4 == 0
    tt_ptr_pos = len(out)
    out += bytes(4)
    assert len(out) % 4 == 0
    tt_off = len(out) // 4
    struct.pack_into("<I", out, tt_ptr_pos, tt_off)
    emitted = list(p["samples"])
    if two_tracks:
        # probe (b22): stock race songs are all tracks=2 with a ~14-sample
        # tail track; duplicate samples 0..13 as track 1.
        emitted = emitted + emitted[:14]
        st_pos = len(out) + 12
        t1_pos = st_pos + len(p["samples"]) * 8
        eof_pos = t1_pos + 14 * 8
        out += struct.pack("<III", st_pos // 4, t1_pos // 4, eof_pos // 4)
    else:
        st_pos = len(out) + 8
        eof_pos = st_pos + len(p["samples"]) * 8
        out += struct.pack("<II", st_pos // 4, eof_pos // 4)
    assert len(out) == st_pos
    for off, dur in emitted:
        assert off % 128 == 0, off
        out += struct.pack("<II", off // 128, dur)
    assert len(out) == eof_pos
    return bytes(out), emitted


def parse_v4_nodes(b):
    """Classify v4 nodes with the same model (for render-compare)."""
    N = u16le(b, 0x12)
    offs = [u16le(b, 0x20 + i * 2) * 4 for i in range(N)]
    nodes = []
    for i, e in enumerate(offs):
        w = u32le(b, e + 4)
        n = (w >> 12) & 0x1F
        hdr = bytes(b[e:e + 16])
        ss = [bytes(b[e + 0x10 + j * 4:e + 0x14 + j * 4]) for j in range(n)]
        nd = dict(idx=i, n=n, play=None, nxt=None)
        if n == 0:
            pass
        elif n == 1 and ss[0][1] == 0x7F:
            nd["nxt"] = ss[0][2] + (ss[0][3] << 8)
        elif (n == 2 and ss[0][0] == 0 and ss[0][1] == ss[1][0]
                and ss[1][1] == 0x7F and ss[0][1] != 0):
            nd["play"] = ss[0][2] + (ss[0][3] << 8)
            nd["nxt"] = ss[1][2] + (ss[1][3] << 8)
        elif n == 2 and ss[0][1] == 0x00:
            nd["nxt"] = ss[0][2] + (ss[0][3] << 8)
            nd["cond"] = (ss[1][0], ss[1][2] + (ss[1][3] << 8))
        elif (n == 3 and ss[0][0] == 0 and ss[0][1] == ss[1][0]
                and ss[1][1] == ss[2][0] and ss[2][1] == 0x7F):
            nd["play"] = [ss[0][2] + (ss[0][3] << 8),
                          ss[1][2] + (ss[1][3] << 8)]
            nd["nxt"] = ss[2][2] + (ss[2][3] << 8)
        else:
            print(f"warn: v4 node {i}: unhandled n={n} "
                  + str([s.hex() for s in ss]), file=sys.stderr)
        nodes.append(nd)
    return nodes


def cmd_convert(v3_path, mus_path, out_path, offsets_tsv=None,
                two_tracks=False, shotgun_events=False):
    b3 = open(v3_path, "rb").read()
    mus = open(mus_path, "rb").read()
    p = parse_v3(b3)
    if offsets_tsv:
        rows = [l.split() for l in open(offsets_tsv).read().splitlines()[1:]
                if l.strip()]
        assert len(rows) == len(p["samples"]), (len(rows),
                                                len(p["samples"]))
        new = []
        for i, o, d in rows:
            o, d = int(o), int(d)
            assert o % 128 == 0, (i, o)
            assert mus[o:o + 4] == b"SCHl", (i, o)
            new.append((o, d))
        p["samples"] = new
        print(f"external samples: {len(new)} from {offsets_tsv}")
    print(f"v3: N={p['N']} E={p['E']} R={p['R']} V={p['V']} "
          f"samples={len(p['samples'])}")
    kinds = {}
    for nd in p["nodes"]:
        k = ("empty" if nd["n"] == 0
             else "branch" if nd["cond"] is not None
             else "chain" if isinstance(nd.get("play"), list)
             else "pair" if nd["n"] == 2
             else "link" if nd["play"] is None else "playsingle")
        kinds[k] = kinds.get(k, 0) + 1
    print("v3 node kinds:", kinds)
    for e in range(p["E"]):
        vals = set(p["blob"][e * p["S"]:(e + 1) * p["S"]])
        r = p["blob"][e * p["S"]]
        rb = p["routers"][r]
        print(f"  event {e} -> router {r} "
              f"(cond {rb[1]:#04x} node {rb[2] + (rb[3] << 8)})")
    v4, emitted = emit_v4(p, two_tracks=two_tracks,
                          shotgun_events=shotgun_events)
    open(out_path, "wb").write(v4)
    print(f"wrote {out_path} ({len(v4)} bytes)")
    # verify 1: v4 game-walk
    w = walk_v4(v4)
    assert w["total_streams"] == len(emitted)
    assert w["tracks"] == (2 if two_tracks else 1)
    # verify 2: samples resolve to the same SCHl bytes
    for i, (off, dur) in enumerate(w["samples"]):
        want = emitted[i][0]
        assert off * 0x80 == want, (i, off, want)
        assert mus[want:want + 4] == b"SCHl", i
        assert dur == emitted[i][1], i
    print(f"samples: {len(w['samples'])}/{len(emitted)} resolve "
          "to identical SCHl offsets, durations equal")
    # verify 3 (MUS1): nodes 1:1 with v3 (sample + subentries verbatim) and
    # the game's walk (mus1_walk.py, 0x3D3900/0x3D37A0 model) reaches a
    # playing sample from every event target for every intensity 0..127.
    import os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    import mus1_walk as _mw
    n4 = u16le(v4, 0x12)
    assert n4 == p["N"]
    o3 = [u16le(b3, 0x24 + i * 2) * 4 for i in range(p["N"])]
    o4 = [u16le(v4, 0x20 + i * 2) * 4 for i in range(n4)]
    for i in range(n4):
        c3 = b3[o3[i] + 11]
        assert v4[o4[i]:o4[i] + 2] == b3[o3[i]:o3[i] + 2], i
        assert (u32le(v4, o4[i] + 4) >> 12) & 0x1F == c3, i
        assert v4[o4[i] + 16:o4[i] + 16 + 4 * c3] == \
            b3[o3[i] + 12:o3[i] + 12 + 4 * c3], i
    print(f"nodes: {n4}/{p['N']} sample + subentries verbatim")
    bb, nodes4, events4 = _mw.load(out_path)
    spins = []
    for e in range(len(events4)):
        for mask, t in _mw.event_targets(bb, events4, e):
            for v in range(128):
                path_, r = _mw.walk(bb, nodes4, t, v)
                if not r.startswith("play"):
                    spins.append((e, t, v, r))
    assert not spins, spins[:5]
    print("walk: every event target plays for intensity 0..127 (no spin)")
    # verify 3c (TK41b): the standalone graph self-check is fatal on convert.
    gfails, ginfo = check_graph_v4(out_path)
    assert not gfails, gfails[:5]
    print("graph self-check: PASS (no zero-progress cycle 0..127, "
          "all next/sample indices in range)")
    # verify 3b: event targets preserved (router-flattened)
    w4ev = walk_v4(v4)
    import collections as _c
    assert w4ev["events"] == 44
    b4 = v4
    evtab = w4ev["event_table"]
    for e in range(p["E"]):
        r = p["blob"][e * p["S"]]
        rb = p["routers"][r]
        want = rb[2] + (rb[3] << 8)
        ee = u16le(b4, evtab + e * 2) * 4
        nsub = (u32be(b4, ee + 0x0C) >> 8) & 0x3F
        if want == 0xFFFF:
            assert nsub == 0, (e, nsub)
        elif e == 0:
            assert nsub == 6, (e, nsub)
            got = u16le(b4, ee + 0x10 + 12)
            assert got == want, (e, got, want)
            assert b4[ee + 0x10] == 0x01, (e, b4[ee + 0x10])
            for ch in range(1, 6):
                s = bytes(b4[ee + 0x10 + ch * 16:ee + 0x20 + ch * 16])
                assert s == bytes([0x01, 0, 0, 0xF1, 0, 0, 0, 0,
                                   0, 0x0A, 0, 0, 0xDF, 0xFF, ch, 0]), (e, ch)
        else:
            assert nsub == 1, (e, nsub)
            got = u16le(b4, ee + 0x10 + 12)
            assert got == want, (e, got, want)
    print(f"events: {p['E']}/{p['E']} targets preserved, "
          f"{44 - p['E']} padded empty")
    # verify 4: PCM smoke (segment 0 decodes from the same bytes)
    import subprocess
    import tempfile
    sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    import tool_paths as _tp
    seg0 = mus[p["samples"][0][0]:p["samples"][1][0]]
    seg0_path = _os.path.join(tempfile.mkdtemp(prefix="mus1-"), "seg0.sch")
    open(seg0_path, "wb").write(seg0)
    r = subprocess.run(
        [str(_tp.ffmpeg_path()), "-hide_banner", "-loglevel", "error", "-i",
         seg0_path, "-ac", "2", "-ar", "44100",
         "-f", "s16le", "-"], capture_output=True)
    if r.returncode == 0:
        print(f"segment 0 PCM: {len(r.stdout)} bytes "
              f"(identical file+offset both sides by construction)")
    else:
        # quad/non-PCM-decodable sources (femenuquad): arrangement + events
        # already verified; the payload needs its own decoder.
        print("segment 0 PCM: SKIP (ffmpeg cannot demux this source)")


def main():
    a = sys.argv
    if len(a) >= 3 and a[1] == "walk":
        cmd_walk(a[2], a[3:])
    elif len(a) >= 3 and a[1] == "check":
        raise SystemExit(cmd_check(a[2:]))
    elif len(a) >= 5 and a[1] == "convert":
        offsets = None
        if "--offsets" in a:
            offsets = a[a.index("--offsets") + 1]
        cmd_convert(a[2], a[3], a[4], offsets,
                    two_tracks="--two-tracks" in a,
                    shotgun_events="--shotgun-events" in a)
    else:
        raise SystemExit(
            "usage: mpf3to4.py walk <file.mpf> [mus...] | "
            "check <v4.mpf>... | "
            "convert <v3.mpf> <mus> <out-v4.mpf> [--offsets <tsv>] "
            "[--two-tracks] [--shotgun-events]")


if __name__ == "__main__":
    main()
