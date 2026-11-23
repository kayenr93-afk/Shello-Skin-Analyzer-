"""Measure POST /api/analyze latency only.

    python -m ml.eval.latency --endpoint URL --manifest PATH [--n 30] [--data-root DIR]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import requests

from ml.eval.readers.gemini_http import downscale_to_jpeg_b64
from ml.eval.run import REPO_ROOT, load_manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description="Measure /api/analyze latency.")
    ap.add_argument("--endpoint", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args(argv)

    records = load_manifest(Path(args.manifest))[: args.n]
    root = Path(args.data_root).resolve() if args.data_root else REPO_ROOT
    client_ms, server_ms = [], []
    session = requests.Session()
    for rec in records:
        b64 = downscale_to_jpeg_b64(str(root / rec["path"]))
        start = time.perf_counter()
        try:
            resp = session.post(
                args.endpoint,
                json={"image_base64": b64, "mime_type": "image/jpeg"},
                timeout=60,
            )
            body = resp.json() if resp.content else {}
        except requests.RequestException:
            body = {}
        client_ms.append((time.perf_counter() - start) * 1000.0)
        if body.get("latency_ms") is not None:
            server_ms.append(body["latency_ms"])

    def pct(v):
        arr = np.asarray(v, dtype=float)
        return {p: float(np.percentile(arr, p)) for p in (50, 95, 99)}

    out = {
        "n": len(records),
        "client_ms": pct(client_ms) if client_ms else None,
        "server_ms": pct(server_ms) if server_ms else None,
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
