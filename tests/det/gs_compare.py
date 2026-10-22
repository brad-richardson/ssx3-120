#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""GR1 perceptual gate: compare completed-frame dumps, baseline vs candidate.

Per frame (matched by filename, usually replay vq-%06d.ppm):
  - luminance SSIM (single-scale; Rec.601 luma; 11x11 gaussian sigma=1.5
    valid-mode; K1=0.01 K2=0.03 L=255 -- the Wang/Bovik reference form)
  - RGB PSNR over all channels jointly (inf when MSE==0)
  - max abs channel error + coords of first occurrence
  - worst non-overlapping 16x16-block MAE (in units of 1/255) + coords
  - worst non-overlapping 16x16-tile max abs error (RV12 rule) + coords
  - changed-pixel fraction: pixels whose max-channel abs diff > 8
  - frame-to-frame error change: mean abs diff of consecutive per-pixel
    max-channel-error images (flicker signal; first frame reports NaN)

Summary gates (defaults = RV9 s6 + RV12 s8.2 proposals):
  SSIM >= 0.995, PSNR >= 40 dB, worst-block MAE <= 4/255, every frame;
  worst-tile max abs <= 16/255, every frame.
Exit 0 iff every frame passes every gate. Also writes worst-crop PNGs
(baseline | candidate | amplified-error side by side).

Inputs: .ppm (P6, internal reader) or anything PIL opens. Requires numpy;
PIL only needed for non-PPM inputs and crop PNGs.

Usage:
  gs_compare.py --base replay-A/frames --cand replay-B/frames --out cmp-AB
"""
import argparse
import json
import math
import os
import sys

try:
    import numpy as np
except ImportError:
    sys.exit("gs_compare.py needs numpy: pip install numpy pillow")

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

LUMA = (0.299, 0.587, 0.114)  # Rec.601
C1 = (0.01 * 255.0) ** 2
C2 = (0.03 * 255.0) ** 2

# Gate defaults (brief s2: RV9 s6; orchestrator add: RV12 s8.2 tile rule).
DEF_SSIM = 0.995
DEF_PSNR = 40.0
DEF_BLOCK_MAE = 4.0 / 255.0
DEF_TILE_MAX = 16.0 / 255.0
CHANGED_T = 8  # max-channel abs diff above this counts as changed


def _gauss_1d(size=11, sigma=1.5):
    x = np.arange(size) - size // 2
    w = np.exp(-(x ** 2) / (2 * sigma * sigma))
    return w / w.sum()


def _valid_sep(img, w):
    # Separable valid-mode correlation with 1-D window w (float64).
    k = w.shape[0]
    t = np.tensordot(np.lib.stride_tricks.sliding_window_view(img, k, axis=1),
                     w, axes=([2], [0]))
    return np.tensordot(np.lib.stride_tricks.sliding_window_view(t, k, axis=0),
                        w, axes=([2], [0]))


def mssim(y1, y2, w):
    mu1 = _valid_sep(y1, w)
    mu2 = _valid_sep(y2, w)
    s1sq = _valid_sep(y1 * y1, w) - mu1 * mu1
    s2sq = _valid_sep(y2 * y2, w) - mu2 * mu2
    s12 = _valid_sep(y1 * y2, w) - mu1 * mu2
    num = (2.0 * mu1 * mu2 + C1) * (2.0 * s12 + C2)
    den = (mu1 * mu1 + mu2 * mu2 + C1) * (s1sq + s2sq + C2)
    return float(np.mean(num / den))


def read_image(path):
    with open(path, "rb") as f:
        magic = f.read(2)
        if magic == b"P6":
            toks = []
            while len(toks) < 3:
                line = f.readline()
                if not line:
                    raise ValueError(f"{path}: truncated PPM header")
                line = line.split(b"#")[0]
                toks += line.split()
            w, h, mx = int(toks[0]), int(toks[1]), int(toks[2])
            if mx != 255:
                raise ValueError(f"{path}: maxval {mx} != 255")
            raw = f.read(w * h * 3)
            if len(raw) != w * h * 3:
                raise ValueError(f"{path}: truncated PPM pixels")
            return np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3)
    if not HAVE_PIL:
        raise ValueError(f"{path}: not PPM and PIL missing")
    return np.asarray(Image.open(path).convert("RGB"))


def frame_metrics(a, b, w):
    if a.shape != b.shape:
        raise ValueError(f"shape {a.shape} vs {b.shape}")
    d = a.astype(np.int16) - b.astype(np.int16)
    ad = np.abs(d)
    perr = ad.max(axis=2)  # per-pixel max-channel abs error, 0..255
    mse = float(np.mean(d.astype(np.float64) ** 2))
    psnr = float("inf") if mse == 0.0 else 10.0 * math.log10(255.0**2 / mse)
    ya = LUMA[0] * a[:, :, 0] + LUMA[1] * a[:, :, 1] + LUMA[2] * a[:, :, 2]
    yb = LUMA[0] * b[:, :, 0] + LUMA[1] * b[:, :, 1] + LUMA[2] * b[:, :, 2]
    ssim = mssim(ya.astype(np.float64), yb.astype(np.float64), w)
    maxe = int(perr.max())
    my, mx = (int(v) for v in np.unravel_index(int(perr.argmax()), perr.shape))
    h, wpx = perr.shape
    bh, bw = h // 16, wpx // 16
    grid = perr[: bh * 16, : bw * 16].reshape(bh, 16, bw, 16)
    block_mae = grid.mean(axis=(1, 3)) / 255.0
    bi = int(block_mae.argmax())
    bby, bbx = bi // bw, bi % bw
    tile_max = grid.max(axis=(1, 3)) / 255.0
    ti = int(tile_max.argmax())
    bty, btx = ti // bw, ti % bw
    changed = float(np.mean(perr > CHANGED_T))
    return {
        "ssim": ssim, "psnr": psnr, "maxe": maxe, "maxe_yx": [my, mx],
        "block_mae": float(block_mae.max()), "block_yx": [bby * 16, bbx * 16],
        "tile_max": float(tile_max.max()), "tile_yx": [bty * 16, btx * 16],
        "changed_frac": changed, "perr": perr,
    }


def save_crop(path, a, b, perr, cy, cx, size=96):
    h, w, _ = a.shape
    y0 = min(max(cy - size // 2, 0), max(h - size, 0))
    x0 = min(max(cx - size // 2, 0), max(w - size, 0))
    ca = a[y0:y0 + size, x0:x0 + size]
    cb = b[y0:y0 + size, x0:x0 + size]
    ce = perr[y0:y0 + size, x0:x0 + size]
    amp = np.clip(ce.astype(np.int32) * 8, 0, 255).astype(np.uint8)
    ce3 = np.stack([amp, np.zeros_like(amp), np.zeros_like(amp)], axis=2)
    bar = np.zeros((size, 4, 3), dtype=np.uint8)
    side = np.concatenate([ca, bar, cb, bar, ce3], axis=1)
    Image.fromarray(side).save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--cand", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--crops", type=int, default=5)
    ap.add_argument("--min-ssim", type=float, default=DEF_SSIM)
    ap.add_argument("--min-psnr", type=float, default=DEF_PSNR)
    ap.add_argument("--max-block-mae", type=float, default=DEF_BLOCK_MAE)
    ap.add_argument("--max-tile", type=float, default=DEF_TILE_MAX)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    base = sorted(f for f in os.listdir(args.base)
                  if os.path.isfile(os.path.join(args.base, f)))
    cand = sorted(f for f in os.listdir(args.cand)
                  if os.path.isfile(os.path.join(args.cand, f)))
    if base != cand:
        only_b = [f for f in base if f not in cand][:5]
        only_c = [f for f in cand if f not in base][:5]
        sys.exit(f"frame sets differ: base-only={only_b} cand-only={only_c}")
    if not base:
        sys.exit("no frames found")
    w = _gauss_1d()
    rows = []
    prev_perr = None
    nfail = 0
    for i, name in enumerate(base):
        a = read_image(os.path.join(args.base, name))
        b = read_image(os.path.join(args.cand, name))
        m = frame_metrics(a, b, w)
        if prev_perr is None or prev_perr.shape != m["perr"].shape:
            f2f = float("nan")
        else:
            f2f = float(np.mean(np.abs(m["perr"].astype(np.int16) -
                                       prev_perr.astype(np.int16))))
        prev_perr = m["perr"]
        fails = []
        if m["ssim"] < args.min_ssim:
            fails.append("ssim")
        if m["psnr"] < args.min_psnr:
            fails.append("psnr")
        if m["block_mae"] > args.max_block_mae:
            fails.append("block_mae")
        if m["tile_max"] > args.max_tile:
            fails.append("tile_max")
        nfail += bool(fails)
        rows.append({"frame": name, "ssim": m["ssim"], "psnr": m["psnr"],
                     "maxe": m["maxe"], "maxe_yx": m["maxe_yx"],
                     "block_mae": m["block_mae"], "block_yx": m["block_yx"],
                     "tile_max": m["tile_max"], "tile_yx": m["tile_yx"],
                     "changed_frac": m["changed_frac"], "f2f": f2f,
                     "fail": fails})
        if (i + 1) % 200 == 0:
            print(f"  {i + 1}/{len(base)} ...", flush=True)
    with open(os.path.join(args.out, "per-frame.json"), "w") as f:
        json.dump(rows, f)
    ssim = np.array([r["ssim"] for r in rows])
    psnr = np.array([r["psnr"] if math.isfinite(r["psnr"]) else 999.0
                     for r in rows])
    finite_psnr = [r["psnr"] for r in rows if math.isfinite(r["psnr"])]
    summary = {
        "frames": len(rows), "failed_frames": nfail,
        "thresholds": {"min_ssim": args.min_ssim, "min_psnr": args.min_psnr,
                       "max_block_mae": args.max_block_mae,
                       "max_tile": args.max_tile},
        "ssim": {"min": float(ssim.min()),
                 "worst": min(rows, key=lambda r: r["ssim"])["frame"],
                 "p1": float(np.percentile(ssim, 1)),
                 "mean": float(ssim.mean())},
        "psnr": {"min": (min(finite_psnr) if finite_psnr else float("inf")),
                 "inf_frames": len(rows) - len(finite_psnr),
                 "worst": (min(rows, key=lambda r: psnr[rows.index(r)])["frame"]
                           if finite_psnr else None),
                 "p1": float(np.percentile(psnr, 1))},
        "block_mae_max": float(max(r["block_mae"] for r in rows)),
        "tile_max_max": float(max(r["tile_max"] for r in rows)),
        "changed_frac_max": float(max(r["changed_frac"] for r in rows)),
        "f2f_max": float(np.nanmax([r["f2f"] for r in rows])),
        "luma": "Rec.601", "ssim_window": "11x11 gaussian s=1.5 valid",
        "changed_def": "pixel max-channel abs diff > 8",
        "f2f_def": "mean abs diff of consecutive per-pixel max-channel-error images",
    }
    with open(os.path.join(args.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    # Worst crops: rank by tile_max (local error), then block_mae.
    if HAVE_PIL and args.crops > 0:
        ranked = sorted(rows, key=lambda r: (r["tile_max"], r["block_mae"]),
                        reverse=True)[:args.crops]
        for rank, r in enumerate(ranked):
            a = read_image(os.path.join(args.base, r["frame"]))
            b = read_image(os.path.join(args.cand, r["frame"]))
            d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
            cy, cx = r["tile_yx"]
            save_crop(os.path.join(args.out, f"crop{rank}-{r['frame']}.png"),
                      a, b, d, cy + 8, cx + 8)
    print(json.dumps(summary, indent=2))
    print("FAIL" if nfail else "PASS", f"{nfail}/{len(rows)} frames failed")
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
