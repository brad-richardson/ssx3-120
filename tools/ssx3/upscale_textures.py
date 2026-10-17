#!/usr/bin/env python3
"""Upscale a directory of dumped game textures with a spandrel model, alpha kept separate.

Needs torch and spandrel (a venv on a CUDA host works); it runs anywhere those
import. Input names are Dolphin's texture-dump
convention (`tex1_<w>x<h>_<hash>[_<tlut hash>]_<format>.png`) and are preserved
exactly, because that name is the key the hi-res loader matches.

Alpha is not fed to the model. Super-resolution models are trained on opaque
RGB, and a game texture's alpha is usually a hard cutout, so the two are scaled
separately — RGB by the model, alpha by Lanczos — and recombined. `--mode
lanczos` skips the model entirely and is the baseline to compare against.

The model may add detail but not change the art's colour: each result's mean
over the pixels the source shows is scaled back to the source's
(`--no-match-colour` to see the drift instead).

Fully transparent pixels carry no usable colour, so their RGB is filled from
the nearest opaque pixel before scaling (otherwise the model spreads the
background colour into the visible edge).

A terrain tile repeats across a surface, so its left edge has to keep matching
its right edge. No super-resolution model knows that: run one over a tile and
it invents an edge, which shows in game as a line along every tile boundary.
`--wrap auto` (the default) detects a tile from the source - its wrap seam is
no worse than its interior detail - pads that source circularly, upscales the
padded image, and crops the padding back off, so the seam survives. Sprites,
faces and UI are not tiles and are left alone, because padding them would pull
the opposite edge of the image into view.

Game data in, game data out: bring your own dumps; nothing is downloaded or
published.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

NAME_PREFIX = 'tex1_'


def opaque_fill(image, passes=None):
    """Bleed edge colour into every transparent pixel so nothing hides black.

    Two pixels of bleed is enough for a clean edge, but not enough for safety:
    the guest draws many cutouts with an *alpha test*, and a scaled alpha
    channel is soft, so pixels the source hid can end up above the test's
    threshold. Whatever colour sits behind them is then visible - and in a
    paletted dump that is usually black, which is where the black silhouettes
    on SSX 3's distant bushes came from. Filling the whole transparent area
    means a pixel that leaks through shows the art's own colour instead.
    """
    from PIL import Image
    import numpy as np
    rgba = np.array(image)
    alpha = rgba[:, :, 3]
    if alpha.min() > 0 or alpha.max() == 0:
        return image
    rgb = rgba[:, :, :3].astype(np.float32)
    mask = (alpha > 0).astype(np.float32)[:, :, None]
    filled = rgb * mask
    weight = mask.copy()
    if passes is None:
        passes = max(image.width, image.height)
    for _ in range(passes):
        if weight.min() > 0:
            break
        shifted_sum = np.zeros_like(filled)
        shifted_weight = np.zeros_like(weight)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            shifted_sum += np.roll(filled, (dy, dx), (0, 1))
            shifted_weight += np.roll(weight, (dy, dx), (0, 1))
        grow = (weight == 0) & (shifted_weight > 0)
        filled = np.where(grow, shifted_sum / np.maximum(shifted_weight, 1e-6), filled)
        weight = np.where(grow, 1.0, weight)
    out = rgba.copy()
    out[:, :, :3] = np.clip(filled, 0, 255).astype(np.uint8)
    return Image.fromarray(out, 'RGBA')


def tileable(image, gradient_floor=2.0, seam_ratio=1.6):
    """Does this texture already wrap? Then it is a tile and must stay one.

    Compares the discontinuity across the wrap with the texture's own interior
    detail. A tile drawn to repeat has no step at the wrap, so the ratio sits
    near or below 1; a sprite or a photo has a hard step there.
    """
    import numpy as np
    array = np.asarray(image.convert('RGB'), dtype=float)
    grey = array[..., 0] * 0.2126 + array[..., 1] * 0.7152 + array[..., 2] * 0.0722
    if grey.shape[0] < 4 or grey.shape[1] < 4:
        return False
    horizontal = np.abs(np.diff(grey, axis=1)).mean()
    vertical = np.abs(np.diff(grey, axis=0)).mean()
    if min(horizontal, vertical) < gradient_floor:
        return False   # too flat to tell, and nothing to protect
    across_x = np.abs(grey[:, 0] - grey[:, -1]).mean()
    across_y = np.abs(grey[0, :] - grey[-1, :]).mean()
    return (across_x / horizontal < seam_ratio) and (across_y / vertical < seam_ratio)


def wrap_pad(image, pad):
    """Tile the image so the model sees continuous neighbours on every side.

    The padding may be wider than the texture - a 16x16 tile needs more context
    than 16 pixels, because a convolutional model's border handling reaches
    further than that and leaves a residual seam - so the source is indexed
    modulo its own size rather than pasted as a 3x3 grid.
    """
    from PIL import Image
    import numpy as np
    array = np.asarray(image)
    rows = np.arange(-pad, array.shape[0] + pad) % array.shape[0]
    columns = np.arange(-pad, array.shape[1] + pad) % array.shape[1]
    return Image.fromarray(array[rows][:, columns], image.mode)


def load_model(path, device):
    import torch
    from spandrel import ModelLoader
    model = ModelLoader().load_from_file(str(path))
    # fp32 throughout: these textures are tiny and half precision changes
    # results between the SPAN and DAT architectures.
    model.eval().to(device)
    return model


def run_model(model, image, device):
    """RGB through the model, one tile, no overlap logic: game textures are small."""
    import numpy as np
    import torch
    from PIL import Image
    rgb = np.array(image.convert('RGB'), dtype=np.float32) / 255.0
    tensor = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0).to(device)
    with torch.no_grad():
        out = model(tensor)
    out = out.squeeze(0).permute(1, 2, 0).clamp(0, 1).cpu().numpy()
    return Image.fromarray((out * 255.0 + 0.5).astype(np.uint8), 'RGB')


def match_colour(result, source, strength=1.0):
    """Put the average colour back where the source had it.

    A super-resolution model is free to invent detail; it is not free to change
    the art's colour. These models do both - Real-ESRGAN darkens SSX 3's
    paletted foliage by 10 to 13 levels, which reads in game as dark specks
    where the stock game has faint debris. The correction is a per-channel gain
    chosen so the mean over the pixels the source shows is preserved, which
    leaves the detail the model added and removes the drift.
    """
    from PIL import Image
    import numpy as np
    out = np.asarray(result.convert('RGBA') if result.mode == 'RGBA' else result.convert('RGB'),
                     dtype=np.float32).copy()
    src = np.asarray(source.convert('RGBA') if source.mode == 'RGBA' else source.convert('RGB'),
                     dtype=np.float32)
    if src.shape[-1] == 4:
        visible_src = src[..., 3] > 8
        visible_out = out[..., 3] > 8
    else:
        visible_src = np.ones(src.shape[:2], dtype=bool)
        visible_out = np.ones(out.shape[:2], dtype=bool)
    if not visible_src.any() or not visible_out.any():
        return result
    for channel in range(3):
        want = float(src[..., channel][visible_src].mean())
        have = float(out[..., channel][visible_out].mean())
        if have <= 1.0 or want <= 1.0:
            continue
        gain = 1.0 + strength * (want / have - 1.0)
        out[..., channel] = np.clip(out[..., channel] * gain, 0, 255)
    return Image.fromarray(out.astype('uint8'), 'RGBA' if out.shape[-1] == 4 else 'RGB')


def binary_alpha(image, tolerance=0.95):
    """Is this a hard cutout? Then its scaled alpha has to stay hard.

    A tree or a sign is drawn with an alpha test, so a soft edge does not fade
    - it either passes the test or it does not, and a Lanczos-scaled alpha
    grows the shape by a pixel or two of whatever was behind it.
    """
    import numpy as np
    alpha = np.asarray(image.getchannel('A'), dtype=float)
    if alpha.min() > 250:
        return False
    hard = ((alpha < 8) | (alpha > 247)).mean()
    return bool(hard >= tolerance)


def upscale(image, model, scale, device, wrap=False, pad=16):
    from PIL import Image
    if wrap:
        # At least 32 pixels of context, and never less than the texture's own
        # size: below that the seam survives the padding.
        pad = max(pad, 32, image.width, image.height)
        padded = wrap_pad(image, pad)
        scaled = upscale(padded, model, scale, device, wrap=False)
        return scaled.crop((pad * scale, pad * scale,
                            (pad + image.width) * scale, (pad + image.height) * scale))
    source = opaque_fill(image) if image.mode == 'RGBA' else image
    if model is None:
        rgb = source.convert('RGB').resize(
            (source.width * scale, source.height * scale), Image.LANCZOS)
    else:
        rgb = run_model(model, source, device)
        if rgb.size != (source.width * scale, source.height * scale):
            rgb = rgb.resize((source.width * scale, source.height * scale), Image.LANCZOS)
    if image.mode != 'RGBA':
        return rgb
    alpha = image.getchannel('A').resize(rgb.size, Image.LANCZOS)
    if binary_alpha(image):
        # Back to a hard edge at the halfway point: the cutout keeps its own
        # outline instead of growing into the background.
        alpha = alpha.point(lambda value: 255 if value >= 128 else 0)
    out = rgb.convert('RGBA')
    out.putalpha(alpha)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('source', type=Path, help='Directory of dumped tex1_*.png files')
    ap.add_argument('output', type=Path, help='Directory for the upscaled copies')
    ap.add_argument('--model', type=Path, help='spandrel model file; omit for --mode lanczos')
    ap.add_argument('--mode', default='model', choices=('model', 'lanczos'))
    ap.add_argument('--scale', type=int, default=4, help='Expected scale factor (default 4)')
    ap.add_argument('--device', default='cuda', help='cuda, mps or cpu')
    ap.add_argument('--limit', type=int, help='Stop after this many images')
    ap.add_argument('--wrap', default='auto', choices=('auto', 'always', 'never'),
                    help='Keep tiles tileable by padding circularly before the model '
                         '(default auto: only textures whose source already wraps)')
    ap.add_argument('--wrap-pad', type=int, default=16,
                    help='Source pixels of circular padding (default 16)')
    ap.add_argument('--match-colour', dest='match_colour', action='store_true', default=True,
                    help='Preserve each texture\'s average colour (default)')
    ap.add_argument('--no-match-colour', dest='match_colour', action='store_false',
                    help='Leave the model\'s colour alone, drift included')
    ap.add_argument('--receipt', type=Path, help='Write a JSON receipt here')
    args = ap.parse_args()
    if args.mode == 'model' and not args.model:
        ap.error('--mode model needs --model')
    sources = sorted(p for p in args.source.iterdir()
                     if p.suffix.lower() == '.png' and p.name.startswith(NAME_PREFIX))
    if not sources:
        raise SystemExit(f'No {NAME_PREFIX}*.png files in {args.source}')
    if args.limit:
        sources = sources[:args.limit]
    args.output.mkdir(parents=True, exist_ok=True)
    device = args.device
    model = None
    if args.mode == 'model':
        import torch
        if device == 'cuda' and not torch.cuda.is_available():
            raise SystemExit('CUDA is not available on this host')
        model = load_model(args.model, device)
    from PIL import Image
    entries, started = [], time.monotonic()
    for path in sources:
        image = Image.open(path)
        image = image.convert('RGBA') if image.mode in ('RGBA', 'LA', 'P') else image.convert('RGB')
        wrap = args.wrap == 'always' or (args.wrap == 'auto' and tileable(image))
        result = upscale(image, model, args.scale, device, wrap=wrap, pad=args.wrap_pad)
        if args.match_colour:
            result = match_colour(result, image)
        target = args.output / path.name
        result.save(target)
        entries.append({'name': path.name, 'source_size': list(image.size),
                        'output_size': list(result.size), 'wrapped': wrap,
                        'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'output_sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
        print(f'{path.name}: {image.size[0]}x{image.size[1]} -> {result.size[0]}x{result.size[1]}')
    receipt = {'mode': args.mode, 'wrap': args.wrap, 'wrap_pad': args.wrap_pad,
               'match_colour': args.match_colour,
               'wrapped': sum(1 for e in entries if e['wrapped']),
               'model': str(args.model) if args.model else None,
               'model_sha256': hashlib.sha256(args.model.read_bytes()).hexdigest() if args.model else None,
               'device': device, 'scale': args.scale, 'images': len(entries),
               'seconds': round(time.monotonic() - started, 2), 'entries': entries}
    if args.receipt:
        args.receipt.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({k: receipt[k] for k in
                      ('mode', 'model', 'device', 'images', 'wrapped', 'seconds')}, indent=2))


if __name__ == '__main__':
    main()
