"""Sample assembly for Phase 2B training.

Skin-type samples: 224px masked face crops (data/processed/v1/<id>/face.png)
for records labelled oily/dry/normal from skin_type_odn + facial_skin_analysis,
excluding conflict drops, non-single faces and partial faces.

Concern samples are bags: the valid patch PNGs when a good face exists
(faces_found==1, not partial), otherwise a single close-up bag = centre-square
crop of the raw image. Bag logit = max over patches at train and inference.

facial_skin_analysis severity-labelled rows are TEST-ONLY for concerns.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

SKIN_TYPES = ["oily", "dry", "normal"]
CONCERNS = ["breakouts", "redness", "dark_spots", "pores", "fine_lines"]
PATCH_NAMES = ["forehead", "left_cheek", "right_cheek", "nose", "chin"]
SKIN_DATASETS = ("skin_type_odn", "facial_skin_analysis")
# facial_skin_analysis rows with severity labels are never trained on.
TEST_ONLY_DATASET = "facial_skin_analysis"


def load_manifest_rows(manifests_dir: Path, variant: str, datasets_cfg: dict):
    """-> {dataset_key: [manifest rows]} filtered by the variant's shippable set."""
    out = {}
    for key, cfg in datasets_cfg.items():
        if variant == "shippable" and not cfg.get("shippable", False):
            continue
        mpath = manifests_dir / f"{key}.jsonl"
        if not mpath.is_file():
            continue
        rows = [json.loads(l) for l in mpath.read_text("utf-8").splitlines() if l.strip()]
        out[key] = rows
    return out


def load_face_info(manifests_dir: Path, processed_dir: Path):
    """-> {image_id: face record}. Falls back to scanning processed dirs when a
    <key>.face.jsonl hasn't been written yet (partial preprocess runs)."""
    info = {}
    for fpath in manifests_dir.glob("*.face.jsonl"):
        for line in fpath.read_text("utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                info[r["image_id"]] = r
    return info


def _face_fallback(processed_dir: Path, image_id: str, info: dict):
    if image_id in info:
        return info[image_id]
    d = processed_dir / image_id
    if (d / "face.png").exists():
        patches = [n for n in PATCH_NAMES if (d / f"{n}.png").exists()]
        return {"image_id": image_id, "faces_found": 1, "face_partial": False,
                "missing_patches": [n for n in PATCH_NAMES if n not in patches]}
    return {"image_id": image_id, "faces_found": 0}


def load_conflicts(manifests_dir: Path):
    p = manifests_dir / "conflicts.json"
    if not p.is_file():
        return set(), set()
    data = json.loads(p.read_text("utf-8"))
    return set(data.get("conflict_image_ids", [])), set(
        data.get("dropped_duplicate_image_ids", [])
    )


def skin_samples(rows_by_ds, face_info, processed_dir, conflicts):
    """-> (samples, exclusion_counts)."""
    conflict_ids, dup_drops = conflicts
    drops = conflict_ids | dup_drops
    samples, excl = [], {
        "conflict_or_dup": 0, "not_included_ds": 0, "no_skin_label": 0,
        "faces_not_1": 0, "face_partial": 0, "missing_crop": 0,
    }
    for ds, rows in rows_by_ds.items():
        if ds not in SKIN_DATASETS:
            excl["not_included_ds"] += len(rows)
            continue
        for r in rows:
            st = r.get("skin_type")
            if st not in SKIN_TYPES:
                if r["image_id"] in drops:
                    excl["conflict_or_dup"] += 1
                else:
                    excl["no_skin_label"] += 1
                continue
            if r["image_id"] in drops:
                excl["conflict_or_dup"] += 1
                continue
            fi = _face_fallback(processed_dir, r["image_id"], face_info)
            if fi.get("faces_found") != 1:
                excl["faces_not_1"] += 1
                continue
            if fi.get("face_partial"):
                excl["face_partial"] += 1
                continue
            crop = processed_dir / r["image_id"] / "face.png"
            if not crop.exists():
                excl["missing_crop"] += 1
                continue
            samples.append({
                "image_id": r["image_id"], "path": str(crop),
                "label": SKIN_TYPES.index(st), "split": r["split"],
                "dataset": ds, "skin_type": st,
            })
    return samples, excl


def center_square_box(w, h):
    side = min(w, h)
    return ((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2)


def center_square_crop(img: Image.Image) -> Image.Image:
    return img.crop(center_square_box(*img.size))


def concern_samples(rows_by_ds, face_info, processed_dir):
    """-> bags: {image_id, patches:[paths]|None, raw_path, pos:set, known:set, split, dataset}."""
    bags = []
    for ds, rows in rows_by_ds.items():
        for r in rows:
            known = set(r.get("concerns_known") or [])
            pos = set(r.get("concerns_pos") or [])
            if not known and not pos:
                continue
            split = "test" if ds == TEST_ONLY_DATASET and known else r["split"]
            fi = _face_fallback(processed_dir, r["image_id"], face_info)
            patches = None
            if fi.get("faces_found") == 1 and not fi.get("face_partial"):
                d = processed_dir / r["image_id"]
                patch_paths = [str(d / f"{n}.png") for n in PATCH_NAMES
                               if (d / f"{n}.png").exists()]
                if patch_paths:
                    patches = patch_paths
            bags.append({
                "image_id": r["image_id"], "patches": patches,
                "raw_path": r["path"], "pos": pos, "known": known,
                "split": split, "dataset": ds,
            })
    return bags


def bag_labels(bag):
    """-> (y float[5], known_mask float[5])."""
    y = [1.0 if c in bag["pos"] else 0.0 for c in CONCERNS]
    m = [1.0 if c in bag["known"] else 0.0 for c in CONCERNS]
    return y, m
