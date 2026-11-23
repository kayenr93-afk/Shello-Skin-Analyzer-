"""Browser vs Python parity for the on-device skin reader.

Runs js/skin_reader.js (ONNX Runtime Web + MediaPipe in Chromium) and
ml.eval.readers.onnx_reader.OnnxReader on the same held-out images and
compares skin-type probabilities and concern scores.

    ml\\.venv\\Scripts\\python tests/parity_device_reader.py [--n 20]
Uses the model named in models/skin_reader/active.json.
"""

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ml.eval.readers.onnx_reader import OnnxReader  # noqa: E402

JS = """
async ([dataUrl]) => {
  const img = new Image();
  img.src = dataUrl;
  await img.decode();
  const c = document.createElement('canvas');
  c.width = img.naturalWidth; c.height = img.naturalHeight;
  c.getContext('2d').drawImage(img, 0, 0);
  const face = await window.ShelloFace.detect(c);
  const lm = face && face.faces === 1 ? face.landmarks : null;
  const r = await window.ShelloSkinReader.read(c, lm);
  return r && { probs: r.probs, concern_scores: r.concern_scores,
                skin: r.observations.skin_type_hint, ms: r.meta.latency_ms,
                faces: face ? face.faces : -1 };
}
"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--manifest", default="ml/data/manifests/skin_type_odn.jsonl")
    args = ap.parse_args()

    version = json.loads((REPO / "models/skin_reader/active.json").read_text())["version"]
    reader = OnnxReader(str(REPO / "models/skin_reader" / version))
    rows = [json.loads(l) for l in (REPO / args.manifest).read_text("utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if r["split"] == "test"][: args.n]

    port = free_port()
    env = dict(os.environ, GEMINI_API_KEY="", OPENAI_API_KEY="")
    srv = subprocess.Popen([sys.executable, "server.py", "--port", str(port)], cwd=REPO, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            urllib.request.urlopen(base + "/", timeout=1)
            break
        except Exception:
            time.sleep(0.25)

    diffs, agree, both, lat = [], 0, 0, []
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page()
            errors = []
            page.on("console", lambda m: m.type == "error" and errors.append(m.text))
            page.goto(base + "/?reader=device", wait_until="networkidle")
            for r in rows:
                path = REPO / r["path"]
                mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
                url = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()
                js = page.evaluate(JS, [url])
                py = reader.predict(r, str(path))
                if not js:
                    print("FAIL browser returned null", r["image_id"])
                    continue
                lat.append(js["ms"])
                if js["probs"] and py["skin_type_probs"]:
                    d = max(abs(js["probs"][k] - py["skin_type_probs"][k]) for k in js["probs"])
                    diffs.append(d)
                    pp = sorted(py["skin_type_probs"].values(), reverse=True)
                    if pp[0] - pp[1] >= 0.1:  # argmax only meaningful with a margin
                        both += 1
                        jt = max(js["probs"], key=js["probs"].get)
                        pt = max(py["skin_type_probs"], key=py["skin_type_probs"].get)
                        agree += jt == pt
                elif bool(js["probs"]) != bool(py["skin_type_probs"]):
                    print("face/partial mismatch", r["image_id"], "js faces", js["faces"])
                cd = max(abs(js["concern_scores"][k] - py["concerns"][k]) for k in py["concerns"])
                diffs.append(cd)
            if errors:
                print("console errors:", errors[:5])
    finally:
        srv.terminate()

    diffs.sort()
    lat.sort()
    print(json.dumps({
        "model": version, "images": len(rows), "skin_compared": both,
        "skin_argmax_agreement": agree / both if both else None,
        "max_abs_prob_diff_median": diffs[len(diffs) // 2] if diffs else None,
        "max_abs_prob_diff_p90": diffs[int(len(diffs) * 0.9)] if diffs else None,
        "browser_read_ms_median": lat[len(lat) // 2] if lat else None,
    }, indent=2))
    ok = bool(diffs) and diffs[int(len(diffs) * 0.9)] <= 0.1 and (not both or agree / both >= 0.9)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
