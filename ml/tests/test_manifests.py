"""Manifest builder tests over synthetic fixtures generated in tmp_path."""

import json
import shutil
from pathlib import Path

import pytest
from openpyxl import Workbook

from ml.data import build_manifests as bm
from ml.tests.conftest import make_image


def _cfg(folder_rules, task="skin_type", **extra):
    cfg = {"_key": "toy", "task": task, "folder_labels": folder_rules}
    cfg.update(extra)
    return cfg


SKIN_RULES = [
    {"regex": "oily", "skin_type": "oily"},
    {"regex": "dry", "skin_type": "dry"},
    {"regex": "normal", "skin_type": "normal"},
]


def test_skin_type_folder_labels(tmp_path, img_factory):
    root = tmp_path / "ds"
    img_factory(root / "Oily" / "a.jpg", noise_seed=1)
    img_factory(root / "dry" / "b.jpg", noise_seed=2)
    img_factory(root / "train" / "Normal" / "c.jpg", noise_seed=3)
    cfg = _cfg(SKIN_RULES)
    records = bm.build_dataset_records(cfg, root)
    by_name = {r["_image"].name: r for r in records}
    assert by_name["a.jpg"]["skin_type"] == "oily"
    assert by_name["b.jpg"]["skin_type"] == "dry"
    # split-folder component is ignored when matching labels
    assert by_name["c.jpg"]["skin_type"] == "normal"
    assert all(r["concerns_known"] == [] for r in records)


def test_unmapped_folder_errors_loudly(tmp_path, img_factory):
    root = tmp_path / "ds"
    img_factory(root / "oily" / "a.jpg")
    img_factory(root / "mystery_folder" / "b.jpg")
    with pytest.raises(bm.ManifestError) as exc:
        bm.build_dataset_records(_cfg(SKIN_RULES), root)
    assert "mystery_folder" in str(exc.value)


def _manifest(tmp_path, img_factory):
    root = tmp_path / "ds"
    # Same bytes under two folders -> same dup_group and same split.
    img_factory(root / "oily" / "a.jpg", color=(10, 20, 30), noise_seed=10)
    dup_src = root / "oily" / "a.jpg"
    dup_dst = root / "dry" / "a_copy.jpg"
    dup_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(dup_src, dup_dst)
    img_factory(root / "dry" / "b.jpg", noise_seed=2)
    img_factory(root / "normal" / "c.jpg", noise_seed=3)
    cfg = _cfg(SKIN_RULES)
    records, summary = bm.build_dataset_manifest(cfg, root, tmp_path)
    return records, summary


def test_dedupe_and_deterministic_split(tmp_path, img_factory):
    records, summary = _manifest(tmp_path, img_factory)
    by_name = {Path(r["path"]).name: r for r in records}
    assert by_name["a.jpg"]["dup_group"] == by_name["a_copy.jpg"]["dup_group"]
    assert by_name["a.jpg"]["split"] == by_name["a_copy.jpg"]["split"]
    assert by_name["a.jpg"]["image_id"] == by_name["a_copy.jpg"]["image_id"]
    assert all(r["split"] in ("train", "val", "test") for r in records)
    assert summary["n_images"] == 4
    assert summary["n_dup_groups"] == 3
    # same inputs -> same splits (deterministic)
    records2, _ = bm.build_dataset_manifest(_cfg(SKIN_RULES), tmp_path / "ds", tmp_path)
    assert [r["split"] for r in records] == [r["split"] for r in records2]
    # all manifest fields present
    for r in records:
        for field in (
            "image_id", "path", "dataset", "task", "skin_type", "concerns_pos",
            "concerns_known", "tone_label", "dhash", "dup_group", "split",
            "ita", "ita_group6", "tone_group2",
        ):
            assert field in r


def test_xlsx_severity_labels(tmp_path, img_factory):
    root = tmp_path / "ds"
    img_factory(root / "imgs" / "face1.jpg", noise_seed=5)
    img_factory(root / "imgs" / "face2.jpg", noise_seed=6)
    wb = Workbook()
    ws = wb.active
    ws.append(["Image_ID", "Acne_Severity", "Redness Severity", "Open pores",
               "fine lines", "forehead wrinkles", "dark spots"])
    ws.append(["face1", 3, 0, 4, 1, 2, 0])
    ws.append(["face2", 0, 5, 1, 0, 0, 3])
    wb.save(root / "labels.xlsx")

    cfg = {
        "_key": "toy_xlsx",
        "task": "concerns_severity",
        "severity_threshold": 2,
        "image_id_column_regex": "^image_?id$",
        "xlsx_label_files": ["labels.xlsx"],
        "column_map": [
            {"regex": "acne.*severity", "concern": "breakouts"},
            {"regex": "redness", "concern": "redness"},
            {"regex": "pores", "concern": "pores"},
            {"regex": "fine.?lines|wrinkles", "concern": "fine_lines"},
            {"regex": "dark.?spots", "concern": "dark_spots"},
        ],
    }
    records = bm.build_dataset_records(cfg, root)
    by_name = {r["_image"].name: r for r in records}
    r1 = by_name["face1.jpg"]
    assert r1["concerns_pos"] == ["breakouts", "fine_lines", "pores"]
    assert r1["concerns_known"] == ["breakouts", "dark_spots", "fine_lines", "pores", "redness"]
    r2 = by_name["face2.jpg"]
    assert r2["concerns_pos"] == ["dark_spots", "redness"]


def test_xlsx_unmatched_column_fails_with_actual_columns(tmp_path, img_factory):
    root = tmp_path / "ds"
    img_factory(root / "imgs" / "face1.jpg")
    wb = Workbook()
    ws = wb.active
    ws.append(["Image_ID", "some_unexpected_col"])
    ws.append(["face1", 1])
    wb.save(root / "labels.xlsx")
    cfg = {
        "_key": "toy_xlsx",
        "task": "concerns_severity",
        "image_id_column_regex": "^image_?id$",
        "column_map": [{"regex": "breakouts?_sev", "concern": "breakouts"}],
    }
    with pytest.raises(bm.ManifestError) as exc:
        bm.build_dataset_records(cfg, root)
    assert "some_unexpected_col" in str(exc.value)


def test_skintone_csv_labels(tmp_path, img_factory):
    root = tmp_path / "ds"
    img_factory(root / "train" / "x1.jpg", noise_seed=7)
    img_factory(root / "valid" / "x2.jpg", noise_seed=8)
    (root / "train" / "_classes.csv").write_text("filename,class\nx1.jpg,light\n", encoding="utf-8")
    (root / "valid" / "_classes.csv").write_text("filename,class\nx2.jpg,dark\n", encoding="utf-8")
    cfg = {
        "_key": "skintone",
        "task": "tone",
        "csv_label_file_regex": "\\.csv$",
        "csv_filename_column_regex": "file|name",
        "csv_class_column_regex": "class|label",
        "folder_labels": [{"regex": "willnevermatchzz", "tone_label": "x"}],
    }
    records = bm.build_dataset_records(cfg, root)
    by_name = {r["_image"].name: r for r in records}
    assert by_name["x1.jpg"]["tone_label"] == "light"
    assert by_name["x2.jpg"]["tone_label"] == "dark"


def test_skintone_no_labels_fails(tmp_path, img_factory):
    root = tmp_path / "ds"
    img_factory(root / "train" / "x1.jpg")
    cfg = {
        "_key": "skintone",
        "task": "tone",
        "csv_label_file_regex": "\\.csv$",
        "folder_labels": [{"regex": "light", "tone_label": "light"}],
    }
    with pytest.raises(bm.ManifestError) as exc:
        bm.build_dataset_records(cfg, root)
    assert "tone" in str(exc.value).lower()


def test_write_manifest_and_summary(tmp_path, img_factory):
    records, summary = _manifest(tmp_path, img_factory)
    out_dir = tmp_path / "manifests"
    path = bm.write_manifest(records, summary, out_dir, "toy")
    lines = path.read_text().splitlines()
    assert len(lines) == len(records)
    summary_path = out_dir / "toy.summary.json"
    data = json.loads(summary_path.read_text())
    assert data["manifest_sha256"] == summary["manifest_sha256"]
    assert len(data["manifest_sha256"]) == 64
