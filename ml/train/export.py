"""Export a trained run to ONNX (+ optional int8 QDQ) and meta.json.

    python -m ml.train.export --run ml/artifacts/<run_id>
        [--version sr-YYYY.MM.DD-<variant>]

Shippable exports land in models/skin_reader/<version>/; research runs stay
inside their ml/artifacts/<run_id>/export/ dir.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from ml.train.data import (CONCERNS, SKIN_TYPES, bag_labels, center_square_crop,
                           concern_samples, load_conflicts, load_face_info,
                           load_manifest_rows, skin_samples)
from ml.train.model import SkinReader
from ml.train.train import EVAL_TF, MANIFESTS, PROCESSED, DATASETS_JSON, REPO_ROOT, git_sha

REGIONS_PATH = REPO_ROOT / "models" / "skin_reader" / "face_regions.json"

APP_CONCERN_MAP = {
    "breakouts": "breakouts",
    "redness": "sensitivity",
    "dark_spots": "uneven_tone",
    "pores": "oil_control",
    "fine_lines": "hydration",
}


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_model(run_dir: Path, device):
    ckpt = torch.load(run_dir / "best.pt", map_location=device)
    model = SkinReader(pretrained=False)
    model.load_state_dict(ckpt["model"])
    return model.eval().to(device), ckpt


def export_fp32(model, device, out_path: Path):
    dummy = torch.randn(1, 3, 224, 224, device=device)
    torch.onnx.export(
        model, dummy, str(out_path), opset_version=18, verbose=False, external_data=False,
        input_names=["input"], output_names=["skin_type_logits", "concern_logits"],
        dynamic_axes={"input": {0: "n"}, "skin_type_logits": {0: "n"},
                      "concern_logits": {0: "n"}},
    )


def onnx_predict(path: Path, xs: np.ndarray):
    import onnxruntime as ort
    sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    s, c = sess.run(None, {"input": xs})
    return s, c


def eval_onnx(path: Path, skin_samples, bags):
    """Skin accuracy + per-concern F1 at calibrated thresholds."""
    import onnxruntime as ort
    sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    probs, true = [], []
    for s in skin_samples:
        x = EVAL_TF(Image.open(s["path"]).convert("RGB")).unsqueeze(0).numpy()
        sk, _ = sess.run(None, {"input": x})
        probs.append(sk[0])
        true.append(s["label"])
    probs = np.array(probs) if probs else np.zeros((0, 3))
    true = np.array(true)
    acc = float((probs.argmax(1) == true).mean()) if len(true) else 0.0
    bag_y, bag_m, bag_p = [], [], []
    for b in bags:
        if b["patches"]:
            imgs = [Image.open(p).convert("RGB") for p in b["patches"]]
        else:
            imgs = [center_square_crop(Image.open(REPO_ROOT / b["raw_path"]).convert("RGB"))]
        xs = np.stack([EVAL_TF(im).numpy() for im in imgs])
        _, cl = sess.run(None, {"input": xs})
        bag_p.append(1 / (1 + np.exp(-cl.max(0))))
        y, m = bag_labels(b)
        bag_y.append(y)
        bag_m.append(m)
    return acc, np.array(bag_y), np.array(bag_m), np.array(bag_p)


def f1_at(y, m, p, ths):
    from sklearn.metrics import f1_score
    vals = []
    for c, name in enumerate(CONCERNS):
        keep = m[:, c] == 1
        if keep.sum():
            vals.append(f1_score(y[keep, c], (p[keep, c] >= ths[name]).astype(int),
                                 zero_division=0))
    return float(np.mean(vals)) if vals else 0.0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--version", default=None)
    args = ap.parse_args(argv)
    run_dir = Path(args.run)
    cfg = json.loads((run_dir / "config.json").read_text("utf-8"))
    cal = json.loads((run_dir / "calibration.json").read_text("utf-8"))
    variant = cfg["variant"]
    version = args.version or f"sr-{datetime.now(timezone.utc):%Y.%m.%d}-{variant}"

    out_dir = (run_dir / "export" / version) if variant == "research" else (
        REPO_ROOT / "models" / "skin_reader" / version)
    out_dir.mkdir(parents=True, exist_ok=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model, ckpt = load_model(run_dir, device)

    fp32_path = out_dir / "model_fp32.onnx"
    export_fp32(model, device, fp32_path)

    # torch vs onnxruntime parity
    import onnxruntime as ort  # noqa
    x = np.random.RandomState(0).randn(2, 3, 224, 224).astype(np.float32)
    with torch.no_grad():
        ts, tc = model(torch.tensor(x, device=device))
    os_, oc = onnx_predict(fp32_path, x)
    diff = max(float(np.abs(ts.cpu().numpy() - os_).max()),
               float(np.abs(tc.cpu().numpy() - oc).max()))
    print(f"torch vs ort max abs diff: {diff:.2e}")
    assert diff < 1e-3, "ONNX parity check failed"

    # int8 static quantization with 300 train calibration crops
    rows = load_manifest_rows(MANIFESTS, variant, json.loads(DATASETS_JSON.read_text("utf-8")))
    face_info = load_face_info(MANIFESTS, PROCESSED)
    skin, _ = skin_samples(rows, face_info, PROCESSED, load_conflicts(MANIFESTS))
    bags = concern_samples(rows, face_info, PROCESSED)
    cal_paths = [s["path"] for s in skin if s["split"] == "train"][:300]
    if len(cal_paths) < 300:
        cal_paths += [p for b in bags if b["patches"] for p in b["patches"]]
        cal_paths = cal_paths[:300]

    from onnxruntime.quantization import quantize_static, QuantType, QuantFormat

    class Calib:
        def __init__(self, paths):
            self.it = iter(paths)

        def get_next(self):
            p = next(self.it, None)
            if p is None:
                return None
            return {"input": EVAL_TF(Image.open(p).convert("RGB")).unsqueeze(0).numpy()}

    int8_path = out_dir / "model_int8.onnx"
    quantize_static(
        str(fp32_path), str(int8_path), Calib(cal_paths),
        quant_format=QuantFormat.QDQ, per_channel=True,
        weight_type=QuantType.QInt8,
    )

    val_faces = [s for s in skin if s["split"] == "val"]
    val_bags = [b for b in bags if b["split"] == "val"]
    ths = cal["thresholds"]
    acc_f, y, m, p = eval_onnx(fp32_path, val_faces, val_bags)
    f1_f = f1_at(y, m, p, ths)
    acc_q, y2, m2, p2 = eval_onnx(int8_path, val_faces, val_bags)
    f1_q = f1_at(y2, m2, p2, ths)
    print(f"fp32 acc={acc_f:.3f} f1={f1_f:.3f} | int8 acc={acc_q:.3f} f1={f1_q:.3f}")

    use_int8 = (acc_f - acc_q) <= 0.01 and (f1_f - f1_q) <= 0.02
    chosen = int8_path if use_int8 else fp32_path
    final_path = out_dir / "model.onnx"
    shutil.copyfile(chosen, final_path)

    raw = final_path.read_bytes()
    ds_meta = {k: {"licence": v.get("licence"), "shippable": v.get("shippable")}
               for k, v in json.loads(DATASETS_JSON.read_text("utf-8")).items()}
    meta = {
        "version": version,
        "created": datetime.now(timezone.utc).isoformat(),
        "git": git_sha(),
        "variant": variant,
        "datasets": ds_meta,
        "input_size": 224,
        "mean": [0.485, 0.456, 0.406],
        "std": [0.229, 0.224, 0.225],
        "mask_fill_rgb": json.loads(REGIONS_PATH.read_text("utf-8"))["crop"]["mask_fill_rgb"],
        "face_regions_sha256": sha256_bytes(REGIONS_PATH.read_bytes()),
        "skin_labels": SKIN_TYPES,
        "concern_labels": CONCERNS,
        "thresholds": cal["thresholds"],
        "tau": cal["tau"],
        "precision": "int8" if use_int8 else "fp32",
        "onnx_sha256": sha256_bytes(raw),
        "onnx_bytes": len(raw),
        "onnx_gzip_bytes": len(gzip.compress(raw)),
        "val_metrics": {"skin_acc_fp32": acc_f, "concern_f1_fp32": f1_f,
                        "skin_acc_int8": acc_q, "concern_f1_int8": f1_q},
        "app_concern_map": APP_CONCERN_MAP,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"exported {chosen.name} -> {final_path}\nmeta -> {out_dir / 'meta.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
