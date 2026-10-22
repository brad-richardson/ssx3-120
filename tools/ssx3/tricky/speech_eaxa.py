#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""tk25b_eaxa.py: EA-XA R1 (ADPCM_EA_R1) encoder matching ffmpeg's decoder bit for bit.

References:
  - ffmpeg libavcodec/adpcm.c, AV_CODEC_ID_ADPCM_EA_R1 decode case: per 28-sample
    frame, control byte hi nibble = filter index, lo nibble selects shift = 20-lo;
    delta = sign_extend(nibble,4) << shift; out = clip16((delta + cur*c1 + prev*c2) >> 8).
  - vgmstream src/coding/ea_xa_decoder.c (same filter table / shift relation).
Filter pairs (c1,c2)/256: (0,0) (240,0) (460,-208) (392,-220).

SCDl block layout (measured from SSX 3 Arcade_Uber.dat / Wipeout_Expert_mac.dat):
  payload = LE32 samples_per_block, LE32 off per channel (relative; abs = +4+4*ch),
  then per channel: LE16 current_hist, LE16 prev_hist, frames, zero pad to even len.
  frame = 1 control byte + 14 data bytes (28 nibbles, high nibble first).

Stream layout: SCHl (copied from target slot, 0x85 sample count patched) +
  SCCl (12 B, LE32 block count) + SCDl* + SCEl (copied from target slot).
"""
import struct

C1 = (0, 240, 460, 392)
C2 = (0, 0, -208, -220)

class TooLong(Exception):
    """Encoded stream does not fit the slot (sample count exceeds SCHl width)."""

def clip16(v):
    return -32768 if v < -32768 else (32767 if v > 32767 else v)

def sext4(n):
    return n - 16 if n & 8 else n

def encode_frame(src28, cur, prev):
    """Brute-force filter (0..3) x shift-lo (0..15); carry decoder history.
    Returns (control, data14, new_cur, new_prev, sse)."""
    best_key = None
    best_val = None
    for f in range(4):
        c1, c2 = C1[f], C2[f]
        for lo in range(16):
            shift = 20 - lo
            h_cur, h_prev = cur, prev
            nib = [0] * 28
            sse = 0
            for j in range(28):
                s = src28[j]
                contrib = h_cur * c1 + h_prev * c2
                # decoder: rec = clip16(((nib<<shift) + contrib) >> 8)
                t = (s << 8) - contrib
                n = (t + (1 << (shift - 1))) >> shift if shift > 0 else t
                if n < -8:
                    n = -8
                elif n > 7:
                    n = 7
                rec = ((n << shift) + contrib) >> 8
                if rec < -32768:
                    rec = -32768
                elif rec > 32767:
                    rec = 32767
                d = s - rec
                sse += d * d
                nib[j] = n
                h_prev, h_cur = h_cur, rec
            if best_key is None or sse < best_key:
                best_key = sse
                best_val = (f, lo, tuple(nib), h_cur, h_prev)
    assert best_val is not None
    f, lo, nib, ncur, nprev = best_val
    data = bytearray(14)
    for i in range(0, 28, 2):
        data[i // 2] = ((nib[i] & 15) << 4) | (nib[i + 1] & 15)
    ctl_byte = (f << 4) | lo
    assert best_key is not None
    return ctl_byte, bytes(data), ncur, nprev, best_key

def _decode_frame(nib, f, lo, cur, prev):
    c1, c2 = C1[f], C2[f]
    shift = 20 - lo
    out = []
    h_cur, h_prev = cur, prev
    for n in nib:
        rec = clip16(((n << shift) + h_cur * c1 + h_prev * c2) >> 8)
        out.append(rec)
        h_prev, h_cur = h_cur, rec
    return out

def encode_channel(pcm, s_block):
    """pcm: list of int16, len multiple of 28. Returns list of (S, hist, frames_blob)."""
    assert len(pcm) % 28 == 0
    blocks = []
    pos = 0
    total = len(pcm)
    cur, prev = 0, 0
    while pos < total:
        remain = total - pos
        s = s_block if remain > s_block else remain
        assert s % 28 == 0
        hist = (cur, prev)
        blob = bytearray()
        for _ in range(s // 28):
            ctl, data, cur, prev = encode_frame(pcm[pos:pos + 28], cur, prev)[:4]
            blob.append(ctl)
            blob += data
            pos += 28
        blocks.append((s, hist, bytes(blob)))
    return blocks

def build_scdl(s, ch_blobs, channels):
    """ch_blobs: list per channel of (hist, frames_blob). Returns full SCDl bytes."""
    ch_data = []
    for hist, fb in ch_blobs:
        d = struct.pack("<hh", hist[0], hist[1]) + fb
        if len(d) & 1:
            d += b"\x00"
        ch_data.append(d)
    hdr_len = 4 + 4 * channels
    offs = []
    acc = 0
    for d in ch_data:
        offs.append(acc)
        acc += len(d)
    payload = struct.pack("<I", s) + b"".join(struct.pack("<I", o) for o in offs)
    for d in ch_data:
        payload += d
    return b"SCDl" + struct.pack("<I", 8 + len(payload)) + payload

def encode_blocks(pcm_ch, s_block):
    """Encode PCM channels with shared framing. Returns (splits, ch_blocks).
    ch_blocks[c] = list per block of (hist, frames_blob). Template-independent."""
    channels = len(pcm_ch)
    n = len(pcm_ch[0])
    assert all(len(c) == n for c in pcm_ch)
    assert n % 28 == 0
    splits = []
    pos = 0
    while pos < n:
        s = s_block if n - pos > s_block else n - pos
        assert s % 28 == 0
        splits.append(s)
        pos += s
    ch_blocks = []
    for c in range(channels):
        cur, prev = 0, 0
        bl = []
        pos = 0
        for s in splits:
            hist = (cur, prev)
            blob = bytearray()
            for _ in range(s // 28):
                ctl, data, cur, prev = encode_frame(pcm_ch[c][pos:pos + 28], cur, prev)[:4]
                blob.append(ctl)
                blob += data
                pos += 28
            bl.append((hist, bytes(blob)))
        ch_blocks.append(bl)
    return splits, ch_blocks

def packetize(splits, ch_blocks):
    """Build SCDl packets from encoded blocks. Returns list of SCDl bytes."""
    channels = len(ch_blocks)
    out = []
    for bi, s in enumerate(splits):
        out.append(build_scdl(s, [(ch_blocks[c][bi][0], ch_blocks[c][bi][1])
                                  for c in range(channels)], channels))
    return out

def assemble(template, scdl_list, total_samples):
    """Assemble full stream: template SCHl (0x85 patched) + SCCl + SCDls + SCEl."""
    assert template[:4] == b"SCHl"
    sch_len = struct.unpack("<I", template[0 + 4:0 + 8])[0]
    sch = bytearray(template[:sch_len])
    fd = sch.index(bytes([0xFD]))
    i = sch.index(bytes([0x85]), fd)
    w = sch[i + 1]
    assert 1 <= w <= 4
    if total_samples >= (1 << (8 * w)):
        raise TooLong(f"samples {total_samples} need more than {w} bytes")
    sch[i + 2:i + 2 + w] = total_samples.to_bytes(w, "big")
    ei = template.index(b"SCEl")
    esz = struct.unpack("<I", template[ei + 4:ei + 8])[0]
    scel = template[ei:ei + esz]
    out = bytes(sch)
    out += b"SCCl" + struct.pack("<I", 12) + struct.pack("<I", len(scdl_list))
    for p in scdl_list:
        out += p
    out += scel
    return out

def build_stream(template, pcm_ch, s_block):
    """template: original slot bytes (SCHl..SCEl region of slot, trimmed at SCEl end).
    pcm_ch: list of C int16 lists (each len multiple of 28, equal len).
    Returns full stream bytes (SCHl..SCEl)."""
    splits, ch_blocks = encode_blocks(pcm_ch, s_block)
    return assemble(template, packetize(splits, ch_blocks), len(pcm_ch[0]))
