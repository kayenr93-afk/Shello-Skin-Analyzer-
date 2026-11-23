#!/usr/bin/env python3
"""Build web brand assets from the official Shello logo JPG.

Samples and constants:
  green  #01652F  (1, 101, 47)   modal fill of the wordmark
  red    #EE1C25  (238, 28, 37)  modal fill of the figure accent
  ink    #231F20  (35, 31, 32)   modal fill of the hair / figure
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent
SRC = HERE / "shello-logo-original.jpg"

GREEN = (1, 101, 47)
RED = (238, 28, 37)
INK = (35, 31, 32)
BG = (245, 249, 247)
WHITE = (255, 255, 255)

# Inclusive content bounds measured from non-white pixels of the original JPG
FULL_BOX = (80, 513, 1687, 1199)  # wordmark + figure + tagline
MARK_BOX = (80, 513, 1687, 1011)  # wordmark + figure (tagline is wider; trimmed later)
FIGURE_BOX = (600, 818, 1168, 1014)

BRAND_INK = np.array([GREEN, RED, INK], dtype=np.float32)


def crop_with_pad(im: Image.Image, box, pad: int) -> Image.Image:
    x0, y0, x1, y1 = box
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(im.width - 1, x1 + pad)
    y1 = min(im.height - 1, y1 + pad)
    return im.crop((x0, y0, x1 + 1, y1 + 1))


def white_to_alpha(im_rgb: Image.Image, white_cut: int = 250, ink_dist: float = 22.0) -> Image.Image:
    """Remove a white JPEG background, keeping solid ink and anti-aliased edges.

    Solid pixels close to a brand colour (or simply very dark) stay opaque
    with their original RGB, so hair (#231F20) is not un-blended into black.

    Remaining edge pixels are un-blended from white:
        P = a * Fg + (1-a) * White
    """
    arr = np.asarray(im_rgb.convert("RGB"), dtype=np.float32)
    mins = arr.min(axis=2)
    maxs = arr.max(axis=2)

    # Distance to the nearest solid brand colour
    # arr: H,W,3  ink: K,3  -> H,W,K
    dist = np.sqrt(((arr[:, :, None, :] - BRAND_INK[None, None, :, :]) ** 2).sum(axis=3))
    nearest = dist.min(axis=2)

    solid = (nearest <= ink_dist) | (maxs < 60)
    background = mins >= white_cut

    alpha = np.zeros(mins.shape, dtype=np.float32)
    fg = arr.copy()

    # Solid ink: fully opaque, keep original colour
    alpha[solid & ~background] = 255.0

    # Edge pixels: unblend from white
    edge = ~solid & ~background
    if np.any(edge):
        a = (255.0 - mins[edge]) / 255.0
        a_safe = np.maximum(a, 1e-6)
        recovered = 255.0 + (arr[edge] - 255.0) / a_safe[:, None]
        fg[edge] = recovered
        alpha[edge] = a * 255.0

    fg = np.clip(np.rint(fg), 0, 255).astype(np.uint8)
    alpha_u8 = np.clip(np.rint(alpha), 0, 255).astype(np.uint8)
    alpha_u8[alpha_u8 < 3] = 0
    return Image.fromarray(np.dstack([fg, alpha_u8]), "RGBA")


def trim_alpha(im: Image.Image, pad: int = 8, threshold: int = 8) -> Image.Image:
    a = np.array(im.split()[-1])
    ys, xs = np.where(a > threshold)
    if len(xs) == 0:
        return im
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(im.width - 1, x1 + pad)
    y1 = min(im.height - 1, y1 + pad)
    return im.crop((x0, y0, x1 + 1, y1 + 1))


def mask_out_green(im: Image.Image) -> Image.Image:
    """Drop leftover green letter fragments from the figure crop."""
    arr = np.array(im)
    r, g, b, a = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2], arr[:, :, 3]
    green = (g > r + 20) & (g > b + 10) & (g > 50) & (a > 0)
    arr[green, 3] = 0
    return Image.fromarray(arr, "RGBA")


def save_png(im: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, format="PNG", optimize=True, compress_level=9)
    print(f"  wrote {path}  {im.size[0]}x{im.size[1]}  {path.stat().st_size/1024:.1f} KB  mode={im.mode}")


def fit_on_canvas(im: Image.Image, size: tuple[int, int], bg, margin_ratio: float = 0.12) -> Image.Image:
    w, h = size
    src = im.convert("RGBA")
    max_w = int(w * (1 - 2 * margin_ratio))
    max_h = int(h * (1 - 2 * margin_ratio))
    scale = min(max_w / src.width, max_h / src.height)
    nw, nh = max(1, int(round(src.width * scale))), max(1, int(round(src.height * scale)))
    resized = src.resize((nw, nh), Image.Resampling.LANCZOS)
    base = Image.new("RGBA", size, (bg[0], bg[1], bg[2], 255))
    base.alpha_composite(resized, ((w - nw) // 2, (h - nh) // 2))
    return base.convert("RGB")


def main() -> None:
    src = Image.open(SRC).convert("RGB")
    print("source", src.size)

    full = trim_alpha(white_to_alpha(crop_with_pad(src, FULL_BOX, pad=4)), pad=12)
    save_png(full, HERE / "shello-logo.png")

    mark = trim_alpha(white_to_alpha(crop_with_pad(src, MARK_BOX, pad=4)), pad=8)
    save_png(mark, HERE / "shello-logo-mark.png")

    # Web header at ~3x of the 52px CSS height
    header_h = 156
    header_w = max(1, int(round(header_h * mark.width / mark.height)))
    header = mark.resize((header_w, header_h), Image.Resampling.LANCZOS)
    save_png(header, ASSETS / "logo.png")
    save_png(header, HERE / "shello-logo-header.png")

    fig = mask_out_green(trim_alpha(white_to_alpha(crop_with_pad(src, FIGURE_BOX, pad=2)), pad=2))
    fig = trim_alpha(fig, pad=0)
    save_png(fig, HERE / "shello-figure.png")

    # Favicon: wordmark + figure on white (readable at 64px; tagline is mud at this size)
    fav = fit_on_canvas(mark, (64, 64), WHITE, margin_ratio=0.10)
    save_png(fav, ASSETS / "favicon.png")
    save_png(fit_on_canvas(mark, (32, 32), WHITE, margin_ratio=0.10), HERE / "favicon-32.png")

    apple = fit_on_canvas(full, (180, 180), WHITE, margin_ratio=0.12)
    save_png(apple, ASSETS / "apple-touch-icon.png")

    # OG 1200x630: full lockup on light brand background, thin green hairlines
    og = Image.new("RGB", (1200, 630), BG)
    draw = ImageDraw.Draw(og)
    draw.rectangle([0, 0, 1199, 6], fill=GREEN)
    draw.rectangle([0, 623, 1199, 629], fill=GREEN)
    max_w, max_h = 1000, 520
    scale = min(max_w / full.width, max_h / full.height)
    nw, nh = int(round(full.width * scale)), int(round(full.height * scale))
    logo_og = full.resize((nw, nh), Image.Resampling.LANCZOS)
    x, y = (1200 - nw) // 2, (630 - nh) // 2
    layer = og.convert("RGBA")
    layer.alpha_composite(logo_og, (x, y))
    save_png(layer.convert("RGB"), ASSETS / "og-image.png")

    # Colour check on the full lockup
    arr = np.array(full)
    a = arr[:, :, 3]
    rgb = arr[:, :, :3]
    opaque = a > 250
    def modal(mask):
        if not np.any(mask):
            return None
        pixels = rgb[mask]
        # pack to unique
        from collections import Counter
        counts = Counter(map(tuple, pixels.tolist()))
        (c, n) = counts.most_common(1)[0]
        return c, n, mask.sum()
    greens = opaque & (rgb[:, :, 1] > rgb[:, :, 0] + 20) & (rgb[:, :, 1] > 60)
    reds = opaque & (rgb[:, :, 0] > rgb[:, :, 1] + 40)
    darks = opaque & (rgb.max(axis=2) < 80)
    for name, mask in (("green", greens), ("red", reds), ("ink", darks)):
        result = modal(mask)
        print(f"  modal {name}: {result}")


if __name__ == "__main__":
    main()
