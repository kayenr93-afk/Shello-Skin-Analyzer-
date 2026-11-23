"""On-device ONNX skin-reader evaluator.

Runs the same MediaPipe preprocessing as ml.face.preprocess live per image:
skin type from the masked face crop (abstain unless exactly one non-partial
face and max softmax >= calibrated tau); concerns = max over valid patches
(centre-crop fallback when no usable face) with per-concern thresholds.

Usage: --reader onnx --model models/skin_reader/<version>
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from ml.eval.readers.base import Reader
from ml.face import preprocess as pp
from ml.train.data import SKIN_TYPES, CONCERNS, PATCH_NAMES, center_square_crop

MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _prep(img: Image.Image) -> np.ndarray:
    arr = np.asarray(img.resize((224, 224), Image.BILINEAR).convert("RGB"),
                     dtype=np.float32) / 255.0
    arr = (arr - MEAN) / STD
    return arr.transpose(2, 0, 1)[None].astype(np.float32)


class OnnxReader(Reader):
    name = "onnx"
    supports_concurrency = False

    def __init__(self, model_dir: str):
        import onnxruntime as ort
        self.dir = Path(model_dir)
        meta = json.loads((self.dir / "meta.json").read_text("utf-8"))
        self.meta = meta
        self.sess = ort.InferenceSession(
            str(self.dir / "model.onnx"), providers=["CPUExecutionProvider"])
        self.tau = meta.get("tau")
        self.thresholds = meta.get("thresholds") or {c: 0.5 for c in CONCERNS}
        self.skin_labels = meta.get("skin_labels", SKIN_TYPES)
        self.concern_labels = meta.get("concern_labels", CONCERNS)

    def version(self) -> str:
        return self.meta.get("version", "0")

    def _forward(self, xs: np.ndarray):
        s, c = self.sess.run(None, {"input": xs})
        return s, c

    def predict(self, record: dict, image_path: str) -> dict:
        t0 = time.time()
        skin_type, probs = None, None
        concerns, covered = {}, []
        faces, marks, img = pp.detect_faces(Path(image_path))
        partial = False
        face_x = None
        patch_imgs = []
        if faces == 1 and marks:
            mask = pp.skin_mask_array(img, marks)
            box = pp.crop_box(img, marks)
            w, h = img.size
            side = max(1, box[2] - box[0])
            vis = (min(box[2], w) - max(box[0], 0)) * (
                min(box[3], h) - max(box[1], 0))
            clip = 1.0 - min(1.0, vis / float(side * side))
            my0, mx0 = max(0, box[1]), max(0, box[0])
            my1, mx1 = min(h, box[3]), min(w, box[2])
            skin_frac = mask[my0:my1, mx0:mx1].mean()
            pv = pp.REGIONS.get("partial_face", {})
            partial = (clip > pv.get("crop_clip_frac", 0.05)
                       or skin_frac < pv.get("min_skin_fraction_of_crop", 0.35))
            if not partial:
                face_x = _prep(pp.face_crop(img, marks))
                valid, _missing = pp.patches(img, marks, mask=mask)
                patch_imgs = list(valid.values())

        if face_x is not None:
            s_log, _ = self._forward(face_x)
            p = np.exp(s_log[0] - s_log[0].max())
            p = p / p.sum()
            probs = {self.skin_labels[i]: float(p[i]) for i in range(len(p))}
            if self.tau is None or p.max() >= self.tau:
                skin_type = self.skin_labels[int(p.argmax())]

        if patch_imgs:
            xs = np.concatenate([_prep(im) for im in patch_imgs], 0)
        else:
            xs = _prep(center_square_crop(img))
        _, c_log = self._forward(xs)
        cp = 1 / (1 + np.exp(-c_log.max(0)))
        for i, name in enumerate(self.concern_labels):
            if name in self.thresholds:
                concerns[name] = float(cp[i])
                covered.append(name)

        return {
            "skin_type": skin_type,
            "skin_type_probs": probs,
            "concerns": concerns,
            "covered_concerns": covered,
            "confidence": float(p.max()) if probs else None,
            "meta": {
                "provider": "onnx",
                "model": self.version(),
                "prompt_version": None,
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "server_latency_ms": None,
            },
        }
