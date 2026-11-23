"""Face detection + region extraction for the skin reader.

Uses the same vendored face_landmarker.task model as the browser (IMAGE mode,
numFaces 2). For every image listed in a manifest it writes, only when a single
face is found:

    data/processed/v1/<image_id>/{face,forehead,left_cheek,right_cheek,
                                  nose,chin}.png

`face` is the landmark-bbox crop (expanded 10%, squared, 224px) with
non-skin-mask pixels filled with the shared fill colour; each patch is a 224px
square crop around the region centres in models/skin_reader/face_regions.json.

Also writes ml/data/manifests/<key>.face.jsonl with:
    image_id, faces_found (0|1|2), face_box_frac, ita_lm, ita_lm_group6,
    tone_group2_lm
(faces_found 2 means 2 or more faces -> image is skipped for training crops;
the ITA fields are computed only when exactly one face is found, else null.)

Usage:
    python -m ml.face.preprocess [--only KEY] [--workers N]
        [--manifests DIR] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ml.data import tone

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = REPO_ROOT / "models" / "face_landmarker" / "1" / "face_landmarker.task"
REGIONS_PATH = REPO_ROOT / "models" / "skin_reader" / "face_regions.json"
DEFAULT_MANIFESTS = REPO_ROOT / "ml" / "data" / "manifests"
DEFAULT_OUT = REPO_ROOT / "data" / "processed" / "v1"

PATCH_NAMES = ["forehead", "left_cheek", "right_cheek", "nose", "chin"]

REGIONS = json.loads(REGIONS_PATH.read_text(encoding="utf-8"))


def _load_landmarker():
    import mediapipe as mp_lib
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    base = mp_python.BaseOptions(model_asset_path=str(MODEL_PATH))
    options = vision.FaceLandmarkerOptions(
        base_options=base,
        running_mode=vision.RunningMode.IMAGE,
        num_faces=2,
    )
    return mp_lib, vision.FaceLandmarker.create_from_options(options)


def detect_faces(image_path: Path):
    """Return (faces_found, landmarks) — landmarks is a list of (x, y) in
    normalized 0..1 image coordinates for the first face, or None."""
    if _Worker.landmarker is None:
        _Worker.landmarker = _load_landmarker()
    mp_lib, landmarker = _Worker.landmarker
    img = Image.open(image_path).convert("RGB")
    mp_img = mp_lib.Image(
        image_format=mp_lib.ImageFormat.SRGB, data=np.asarray(img)
    )
    res = landmarker.detect(mp_img)
    faces = len(res.face_landmarks) if res.face_landmarks else 0
    if not faces:
        return 0, None, img
    marks = [(p.x, p.y) for p in res.face_landmarks[0]]
    return faces, marks, img


def landmark_bbox(marks, w, h):
    xs = [p[0] for p in marks]
    ys = [p[1] for p in marks]
    return min(xs) * w, min(ys) * h, max(xs) * w, max(ys) * h


def _convex_hull(points):
    """Monotonic-chain convex hull of (x, y) points."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _feature_mask(img_size, marks, name, dilate_px=0):
    """Filled feature polygon; brow chains are closed via convex hull so the
    whole brow region is masked, not just the landmark chain."""
    w, h = img_size
    m = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(m)
    pts = [(marks[i][0] * w, marks[i][1] * h) for i in REGIONS["polygons"][name]]
    if name in REGIONS.get("convex_hull_features", []):
        pts = _convex_hull(pts)
    draw.polygon(pts, fill=1)
    if dilate_px > 0:
        # Disc dilation of a filled polygon == polygon + edges stroked at
        # width 2r with round joins/caps (same as canvas lineJoin="round").
        r = dilate_px
        closed = list(pts) + [pts[0]]
        draw.line(closed, fill=1, width=2 * r + 1, joint="curve")
        for x, y in pts:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=1)
    return m


def skin_mask_array(img: Image.Image, marks) -> np.ndarray:
    """face_oval minus dilated eyes/brows/lips as a bool array (H, W)."""
    w, h = img.size
    x0, y0, x1, y1 = landmark_bbox(marks, w, h)
    dilate_px = int(round(REGIONS.get("mask_dilate_frac", 0.0) * (x1 - x0)))
    mask = np.zeros((h, w), dtype=bool)
    poly = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(poly)
    draw.polygon([(marks[i][0] * w, marks[i][1] * h) for i in REGIONS.get("face_oval", [])], fill=1)
    mask = np.asarray(poly, dtype=bool)
    for name in REGIONS["skin_mask"]["exclude"]:
        fm = np.asarray(_feature_mask((w, h), marks, name, dilate_px), dtype=bool)
        mask &= ~fm
    return mask


def crop_box(img: Image.Image, marks):
    """Expanded square crop box (ints) used for the face crop."""
    w, h = img.size
    x0, y0, x1, y1 = landmark_bbox(marks, w, h)
    expand = REGIONS["crop"]["expand_frac"]
    side = max(x1 - x0, y1 - y0) * (1 + expand)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return (
        int(round(cx - side / 2)),
        int(round(cy - side / 2)),
        int(round(cx + side / 2)),
        int(round(cy + side / 2)),
    )


def face_crop(img: Image.Image, marks, mask=None) -> Image.Image:
    box = crop_box(img, marks)
    crop = img.crop(box)
    if mask is None:
        mask = skin_mask_array(img, marks)
    mask_img = Image.fromarray(mask.astype(np.uint8) * 255)
    mask_crop = mask_img.crop(box)  # out-of-bounds pads with 0 -> filled
    out = Image.new("RGB", crop.size, tuple(REGIONS["crop"]["mask_fill_rgb"]))
    out.paste(crop, (0, 0), mask_crop)
    size = REGIONS["crop"]["output_size"]
    return out.resize((size, size), Image.LANCZOS)


def patch_boxes(img: Image.Image, marks) -> dict:
    """Square pixel boxes for each named patch on the original image."""
    w, h = img.size
    x0, y0, x1, y1 = landmark_bbox(marks, w, h)
    fw, fh = x1 - x0, y1 - y0
    boxes = {}
    for name, spec in REGIONS["patches"].items():
        if "centroid_of" in spec:
            idx = spec["centroid_of"]
            cx = sum(marks[i][0] for i in idx) / len(idx) * w
            cy = sum(marks[i][1] for i in idx) / len(idx) * h
        else:
            cx = marks[spec["center"]][0] * w
            cy = marks[spec["center"]][1] * h
        # offsets are expressed in face-width / face-height units
        cx += spec.get("offset_x_face_w", 0.0) * fw
        cy += spec.get("offset_y_face_h", 0.0) * fh
        side = spec["side_frac"] * fw
        boxes[name] = (
            int(round(cx - side / 2)),
            int(round(cy - side / 2)),
            int(round(cx + side / 2)),
            int(round(cy + side / 2)),
        )
    return boxes


def patch_valid(img, box, mask) -> bool:
    """Patch counts only if fully inside the image and >= min_skin_fraction of
    its pixels lie inside skin_mask."""
    w, h = img.size
    v = REGIONS.get("patch_validity", {})
    if v.get("must_fit_inside_image", True):
        if box[0] < 0 or box[1] < 0 or box[2] > w or box[3] > h:
            return False
    frac = mask[box[1]:box[3], box[0]:box[2]].mean()
    return frac >= v.get("min_skin_fraction", 0.8)


def patches(img: Image.Image, marks, mask=None):
    """-> ({name: PIL patch}, [missing names])."""
    if mask is None:
        mask = skin_mask_array(img, marks)
    size = REGIONS["patch_output_size"]
    out, missing = {}, []
    for name, box in patch_boxes(img, marks).items():
        if patch_valid(img, box, mask):
            out[name] = img.crop(box).resize((size, size), Image.LANCZOS)
        else:
            missing.append(name)
    return out, missing


def landmark_ita(img: Image.Image, marks, mask=None):
    if mask is None:
        mask = skin_mask_array(img, marks)
    return tone.estimate_ita(img, skin_mask_fn=lambda _img: mask)


def process_one(args):
    image_path, image_id, out_root = args
    try:
        faces, marks, img = detect_faces(Path(image_path))
    except Exception:
        return {"image_id": image_id, "faces_found": -1}
    rec = {"image_id": image_id, "faces_found": min(faces, 2)}
    if faces == 1 and marks:
        w, h = img.size
        x0, y0, x1, y1 = landmark_bbox(marks, w, h)
        rec["face_box_frac"] = round((x1 - x0) * (y1 - y0) / (w * h), 4)
        mask = skin_mask_array(img, marks)
        # Partial-face flag: the expanded crop box is clipped by the image
        # border by > crop_clip_frac of its side, or skin covers < 35% of crop.
        pv = REGIONS.get("partial_face", {})
        box = crop_box(img, marks)
        side = max(1, box[2] - box[0])
        vis_w = min(box[2], w) - max(box[0], 0)
        vis_h = min(box[3], h) - max(box[1], 0)
        clip_frac = 1.0 - min(1.0, (vis_w * vis_h) / float(side * side))
        my0, mx0 = max(0, box[1]), max(0, box[0])
        my1, mx1 = min(h, box[3]), min(w, box[2])
        skin_frac_crop = mask[my0:my1, mx0:mx1].mean()
        partial = (
            clip_frac > pv.get("crop_clip_frac", 0.05)
            or skin_frac_crop < pv.get("min_skin_fraction_of_crop", 0.35)
        )
        rec["face_partial"] = bool(partial)
        try:
            ita = landmark_ita(img, marks, mask)
        except Exception:
            ita = None
        rec["ita_lm"] = round(ita, 3) if ita is not None else None
        rec["ita_lm_group6"] = tone.ita_group6(ita)
        rec["tone_group2_lm"] = tone.tone_group2(ita)
        out_dir = Path(out_root) / image_id
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            face_crop(img, marks, mask).save(out_dir / "face.png")
            valid, missing = patches(img, marks, mask=mask)
            for name, patch in valid.items():
                patch.save(out_dir / f"{name}.png")
            if missing:
                rec["missing_patches"] = missing
        except Exception:
            rec["write_error"] = True
    return rec


class _Worker:
    landmarker = None


def _worker_init():
    _Worker.landmarker = _load_landmarker()


def iter_manifest_images(manifests_dir: Path, keys):
    for key in keys:
        mpath = manifests_dir / f"{key}.jsonl"
        if not mpath.is_file():
            continue
        rows = []
        for line in mpath.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        yield key, rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="Face-landmark preprocessing.")
    ap.add_argument("--only", help="Process a single dataset key.")
    ap.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 1))
    ap.add_argument("--manifests", default=str(DEFAULT_MANIFESTS))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--limit", type=int, default=0, help="Debug: only N images.")
    args = ap.parse_args(argv)

    manifests_dir = Path(args.manifests)
    out_root = Path(args.out)
    keys = [args.only] if args.only else [
        p.stem for p in sorted(manifests_dir.glob("*.jsonl"))
        if not p.stem.endswith(".face")
    ]
    # Append-only per-image record log makes resume lossless (partial flag,
    # landmark ITA and no-face outcomes survive an interrupted run).
    out_root.mkdir(parents=True, exist_ok=True)
    rec_log = out_root / "_records.jsonl"
    face_rec = {}
    if rec_log.is_file():
        for line in rec_log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("faces_found", -1) >= 0:
                    face_rec[r["image_id"]] = r

    todo = []
    per_key = {}
    queued = set()
    for key, rows in iter_manifest_images(manifests_dir, keys):
        per_key[key] = rows
        for r in rows:
            if r["image_id"] in face_rec or r["image_id"] in queued:
                continue
            queued.add(r["image_id"])
            todo.append((str(REPO_ROOT / r["path"]), r["image_id"], str(out_root)))
    if args.limit:
        todo = todo[: args.limit]
    print(f"processing {len(todo)} images with {args.workers} workers "
          f"({len(face_rec)} already recorded)", flush=True)

    done = 0
    with mp.Pool(args.workers, initializer=_worker_init) as pool, \
            rec_log.open("a", encoding="utf-8") as log_fh:
        for rec in pool.imap_unordered(process_one, todo, chunksize=8):
            face_rec[rec["image_id"]] = rec
            log_fh.write(json.dumps(rec) + "\n")
            done += 1
            if done % 200 == 0:
                log_fh.flush()
                print(f"  {done}/{len(todo)}", flush=True)

    # Images skipped because outputs already existed keep their prior records
    # only if we track them; simplest correct behaviour: re-derive faces_found
    # for skipped images lazily is expensive, so face.jsonl covers processed
    # images plus already-present crops (faces_found=1 inferred).
    for key, rows in per_key.items():
        out_path = manifests_dir / f"{key}.face.jsonl"
        written = 0
        with out_path.open("w", encoding="utf-8") as fh:
            for r in rows:
                rec = face_rec.get(r["image_id"])
                if rec is None:
                    if (out_root / r["image_id"] / "face.png").exists():
                        rec = {"image_id": r["image_id"], "faces_found": 1,
                               "_resumed": True}
                    else:
                        rec = {"image_id": r["image_id"], "faces_found": -1}
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
                written += 1
        counts = {}
        for r in rows:
            rec = face_rec.get(r["image_id"])
            f = rec["faces_found"] if rec else ("cached" if (out_root / r["image_id"] / "face.png").exists() else "?")
            counts[f] = counts.get(f, 0) + 1
        print(f"[{key}] {written} rows -> {out_path} face counts: {counts}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
