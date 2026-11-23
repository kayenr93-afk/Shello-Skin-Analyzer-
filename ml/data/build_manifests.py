"""Build photo-free JSONL manifests from a local dataset directory.

    python -m ml.data.build_manifests [--only skin_type_odn]

Images stay on disk. A manifest row stores a relative path, labels, a content
id, a difference hash, and a deterministic train/val/test split. Exact byte
duplicates share image_id, dup_group, and split.

Cosmetic appearance labels only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

from PIL import Image

from ml.data import tone

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "ml" / "datasets.json"
DEFAULT_OUT = REPO_ROOT / "ml" / "data" / "manifests"

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
SPLIT_DIRS = {"train", "valid", "val", "test", "validation"}
PUBLIC_FIELDS = (
    "image_id",
    "path",
    "dataset",
    "task",
    "skin_type",
    "concerns_pos",
    "concerns_known",
    "tone_label",
    "dhash",
    "dup_group",
    "split",
    "ita",
    "ita_group6",
    "tone_group2",
)


class ManifestError(Exception):
    """A dataset folder or label file does not match the config."""


def load_config(path: Path | str) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for key, cfg in raw.items():
        item = dict(cfg)
        item["_key"] = key
        out[key] = item
    return out


def _search(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def _images(root: Path) -> list[Path]:
    found = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            found.append(path)
    return sorted(found)


def _rel(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _label_from_folders(path: Path, root: Path, rules: list[dict]) -> tuple[dict, list[str]]:
    """Match non-split path components against folder rules.

    Returns (labels, unmatched component names). Split directory names
    (train/valid/val/test) are ignored so ``train/Normal/c.jpg`` is "normal".
    """
    labels: dict = {"skin_type": None, "tone_label": None, "concerns": set()}
    unmatched = []
    rel_parts = path.relative_to(root).parts[:-1]
    for part in rel_parts:
        if part.lower() in SPLIT_DIRS:
            continue
        hits = [rule for rule in rules if _search(rule["regex"], part)]
        if not hits:
            unmatched.append(part)
            continue
        for rule in hits:
            if "skin_type" in rule:
                labels["skin_type"] = rule["skin_type"]
            if "tone_label" in rule:
                labels["tone_label"] = rule["tone_label"]
            if "concern" in rule:
                labels["concerns"].add(rule["concern"])
    return labels, unmatched


def _xlsx_paths(cfg: dict, root: Path) -> list[Path]:
    named = cfg.get("xlsx_label_files") or []
    if named:
        return [root / name for name in named]
    pattern = cfg.get("xlsx_label_file_regex")
    if pattern or cfg.get("column_map"):
        rx = re.compile(pattern or r"\.xlsx$", re.IGNORECASE)
        return sorted(p for p in root.rglob("*.xlsx") if rx.search(p.name))
    return []


def _read_xlsx(cfg: dict, root: Path) -> dict[str, dict]:
    """Map a normalised image id to concern sets. Raises on an unknown column."""
    paths = _xlsx_paths(cfg, root)
    if not paths:
        return {}
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise ManifestError("openpyxl is required to read xlsx label files") from exc

    id_rx = re.compile(cfg.get("image_id_column_regex") or r"^image_?id$", re.IGNORECASE)
    column_map = cfg.get("column_map") or []
    threshold = float(cfg.get("severity_threshold", 2))
    by_id: dict[str, dict] = {}
    for path in paths:
        if not path.is_file():
            raise ManifestError(f"xlsx label file not found: {path}")
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.active
            rows = ws.iter_rows(values_only=True)
            header = ["" if cell is None else str(cell).strip() for cell in next(rows)]
            id_idx = next((i for i, name in enumerate(header) if name and id_rx.search(name)), None)
            if id_idx is None:
                raise ManifestError(f"no image id column in {path.name}: {header}")
            assignments = []
            for i, name in enumerate(header):
                if i == id_idx or not name:
                    continue
                hit = next((rule for rule in column_map if _search(rule["regex"], name)), None)
                if hit is None:
                    raise ManifestError(f"unmatched label column: {name}")
                assignments.append((i, hit["concern"]))
            known = sorted({concern for _, concern in assignments})
            for row in rows:
                if row is None or id_idx >= len(row) or row[id_idx] in (None, ""):
                    continue
                image_id = str(row[id_idx]).strip()
                scores: dict[str, float] = {}
                for idx, concern in assignments:
                    if idx >= len(row) or row[idx] in (None, ""):
                        continue
                    try:
                        value = float(row[idx])
                    except (TypeError, ValueError):
                        continue
                    scores[concern] = max(scores.get(concern, float("-inf")), value)
                pos = sorted(name for name, value in scores.items() if value >= threshold)
                by_id[_norm_id(image_id)] = {"concerns_pos": pos, "concerns_known": known}
        finally:
            wb.close()
    return by_id


def _norm_id(value: str) -> str:
    text = str(value).strip().replace("\\", "/")
    name = text.split("/")[-1]
    stem = Path(name).stem if Path(name).suffix else name
    return stem.lower()


def _csv_labels(cfg: dict, root: Path) -> dict[str, str]:
    pattern = cfg.get("csv_label_file_regex")
    if not pattern:
        return {}
    rx = re.compile(pattern, re.IGNORECASE)
    file_rx = re.compile(cfg.get("csv_filename_column_regex") or r"file|image|name|path", re.IGNORECASE)
    class_rx = re.compile(cfg.get("csv_class_column_regex") or r"class|label|tone", re.IGNORECASE)
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not rx.search(path.name):
            continue
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            fields = reader.fieldnames or []
            file_col = next((name for name in fields if file_rx.search(name)), None)
            class_col = next((name for name in fields if class_rx.search(name) and name != file_col), None)
            if not file_col or not class_col:
                raise ManifestError(f"csv {path.name} is missing filename/class columns: {fields}")
            for row in reader:
                filename = (row.get(file_col) or "").strip()
                label = (row.get(class_col) or "").strip()
                if filename and label:
                    out[_norm_id(filename)] = label
                    out[Path(filename).name.lower()] = label
    return out


def build_dataset_records(cfg: dict, root: Path) -> list[dict]:
    """Label every image under root. Raises ManifestError on an unknown folder or column."""
    root = Path(root)
    rules = cfg.get("folder_labels") or []
    xlsx = _read_xlsx(cfg, root)
    csv_labels = _csv_labels(cfg, root)
    task = cfg.get("task") or ""
    records = []
    for image in _images(root):
        labels, unmatched = _label_from_folders(image, root, rules)
        if rules and unmatched and labels["skin_type"] is None and not labels["concerns"] and labels["tone_label"] is None:
            raise ManifestError(f"unmapped folder {unmatched[0]!r} has no label rule")
        concerns = set(labels["concerns"])
        known = set(concerns)
        sheet = xlsx.get(_norm_id(image.name)) or xlsx.get(_norm_id(image.stem))
        if sheet:
            concerns.update(sheet["concerns_pos"])
            known.update(sheet["concerns_known"])
        tone_label = csv_labels.get(_norm_id(image.name)) or csv_labels.get(image.name.lower()) or labels["tone_label"]
        if task == "tone" and not tone_label:
            raise ManifestError(f"no tone label for {image.name}")
        records.append(
            {
                "_image": image,
                "skin_type": labels["skin_type"],
                "concerns_pos": sorted(concerns),
                "concerns_known": sorted(known) if known else [],
                "tone_label": tone_label,
            }
        )
    return records


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dhash(path: Path) -> str:
    image = Image.open(path).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(image.getdata())
    bits = 0
    for y in range(8):
        for x in range(8):
            left = pixels[y * 9 + x]
            right = pixels[y * 9 + x + 1]
            bits = (bits << 1) | int(left > right)
    return f"{bits:016x}"


def _split_for(dup_group: str) -> str:
    bucket = int(hashlib.sha256(dup_group.encode("utf-8")).hexdigest()[:8], 16) % 100
    if bucket < 70:
        return "train"
    if bucket < 85:
        return "val"
    return "test"


def manifest_jsonl(records: list[dict]) -> str:
    lines = [json.dumps({key: rec.get(key) for key in PUBLIC_FIELDS}, sort_keys=True) for rec in records]
    return ("\n".join(lines) + "\n") if lines else ""


def build_dataset_manifest(cfg: dict, root: Path, base: Path) -> tuple[list[dict], dict]:
    """Return public manifest rows plus a summary that includes manifest_sha256."""
    root = Path(root)
    base = Path(base)
    raw = build_dataset_records(cfg, root)
    grouped: dict[str, str] = {}
    records = []
    for rec in raw:
        image = rec["_image"]
        content = _sha256_file(image)
        grouped.setdefault(content, content)
        ita = tone.estimate_ita(Image.open(image))
        records.append(
            {
                "image_id": content,
                "path": _rel(image, base),
                "dataset": cfg.get("_key") or cfg.get("task") or "dataset",
                "task": cfg.get("task"),
                "skin_type": rec.get("skin_type"),
                "concerns_pos": rec.get("concerns_pos") or [],
                "concerns_known": rec.get("concerns_known") or [],
                "tone_label": rec.get("tone_label"),
                "dhash": _dhash(image),
                "dup_group": content,
                "split": _split_for(content),
                "ita": None if ita is None else round(float(ita), 6),
                "ita_group6": tone.ita_group6(ita),
                "tone_group2": tone.tone_group2(ita),
            }
        )
    records.sort(key=lambda row: row["path"])
    text = manifest_jsonl(records)
    summary = {
        "dataset": records[0]["dataset"] if records else cfg.get("_key"),
        "n_images": len(records),
        "n_dup_groups": len({row["dup_group"] for row in records}),
        "manifest_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    return records, summary


def write_manifest(records: list[dict], summary: dict, out_dir: Path, key: str) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{key}.jsonl"
    path.write_text(manifest_jsonl(records), encoding="utf-8")
    (out_dir / f"{key}.summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build JSONL manifests from data/raw datasets.")
    ap.add_argument("--only", action="append", default=None, help="Dataset key from ml/datasets.json. Repeatable.")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    config = load_config(args.config)
    keys = args.only or list(config)
    out_dir = Path(args.out)
    for key in keys:
        if key not in config:
            raise SystemExit(f"unknown dataset {key!r}")
        cfg = config[key]
        root = REPO_ROOT / cfg["path"]
        if not root.is_dir():
            raise SystemExit(f"{key}: dataset directory not found: {root}")
        records, summary = build_dataset_manifest(cfg, root, REPO_ROOT)
        path = write_manifest(records, summary, out_dir, key)
        print(f"{key}: {summary['n_images']} images, {summary['n_dup_groups']} dup groups -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
