"""Debug montage: 12 face images across skin_type_odn + skintone.

For each picked image: left cell = original with the 5 patch boxes drawn,
right cell = the masked 224px face crop. Rows are labelled with the dataset
and tone/type label. Output: ml/debug/montage_regions.png
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw

from ml.face import preprocess as pp

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "ml" / "debug" / "montage_regions_v2.png"

CELL = 224


def pick_images():
    rng = random.Random(7)
    picks = []
    for key, pattern, take in (
        ("odn/oily", "data/raw/skin_type_odn/**/oily/*.jp*g", 2),
        ("odn/dry", "data/raw/skin_type_odn/**/dry/*.jp*g", 2),
        ("odn/normal", "data/raw/skin_type_odn/**/normal/*.jp*g", 2),
        ("tone/fair", "data/raw/skintone/**/fair/*.jp*g", 2),
        ("tone/light", "data/raw/skintone/**/light/*.jp*g", 2),
        ("tone/dark", "data/raw/skintone/**/dark/*.jp*g", 2),
        ("fsa", "data/raw/facial_skin_analysis/**/*.jp*g", 6),
    ):
        files = sorted((REPO).glob(pattern))
        rng.shuffle(files)
        found = 0
        for f in files:
            if found >= take:
                break
            try:
                faces, marks, _img = pp.detect_faces(Path(f))
            except Exception:
                continue
            if faces == 1:
                picks.append((key, f))
                found += 1
    return picks


def dashed_box(draw, box, color, width=2, dash=6):
    x0, y0, x1, y1 = box
    for edge in ((x0, y0, x1, y0), (x0, y1, x1, y1), (x0, y0, x0, y1), (x1, y0, x1, y1)):
        xa, ya, xb, yb = edge
        length = max(abs(xb - xa), abs(yb - ya))
        steps = max(1, length // (dash * 2))
        for i in range(steps + 1):
            t0 = i * 2 * dash / length
            t1 = min(1, t0 + dash / length)
            if t0 >= 1:
                break
            draw.line(
                [(xa + (xb - xa) * t0, ya + (yb - ya) * t0),
                 (xa + (xb - xa) * t1, ya + (yb - ya) * t1)],
                fill=color, width=width,
            )


def main():
    picks = pick_images()
    if not picks:
        print("no faces found for montage")
        return 1
    rows = []
    for label, path in picks:
        faces, marks, img = pp.detect_faces(Path(path))
        img = img.convert("RGB")
        w, h = img.size
        scale = CELL / max(w, h)
        disp = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        draw = ImageDraw.Draw(disp)
        colors = {
            "forehead": "#ff5555", "left_cheek": "#55ff55",
            "right_cheek": "#55aaff", "nose": "#ffff55", "chin": "#ff55ff",
        }
        mask = pp.skin_mask_array(img, marks)
        for name, box in pp.patch_boxes(img, marks).items():
            sb = tuple(int(round(c * scale)) for c in box)
            if pp.patch_valid(img, box, mask):
                draw.rectangle(sb, outline=colors[name], width=2)
            else:
                dashed_box(draw, sb, "#ff2222")
            draw.text((sb[0] + 2, sb[1] + 2), name[:3], fill=colors[name])
        crop = pp.face_crop(img, marks)
        row = Image.new("RGB", (CELL * 2 + 16, CELL), (30, 30, 30))
        row.paste(disp, (0, (CELL - disp.size[1]) // 2))
        row.paste(crop, (CELL + 16, 0))
        rows.append((label, row))
    label_h = 18
    sheet = Image.new("RGB", (CELL * 2 + 16, (CELL + label_h) * len(rows)), (18, 18, 18))
    d = ImageDraw.Draw(sheet)
    for i, (label, row) in enumerate(rows):
        d.text((4, i * (CELL + label_h) + 2), label, fill="#ffffff")
        sheet.paste(row, (0, i * (CELL + label_h) + label_h))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(OUT)
    print(f"montage -> {OUT} ({len(rows)} rows)")


if __name__ == "__main__":
    raise SystemExit(main())
