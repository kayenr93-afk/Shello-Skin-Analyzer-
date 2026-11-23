"""Reader that calls the Shello /api/analyze HTTP endpoint.

Mirrors the browser path: the image is downscaled to max side 1024 and encoded
as JPEG quality 0.85 (same as downscaleToJpeg in js/app.js), then POSTed as
{image_base64, mime_type: "image/jpeg"}. Client-observed latency plus the
provider / model / prompt_version / latency_ms the API reports are recorded.

Mapping from the current API vocabulary to the canonical concern vocabulary:
breakouts -> breakouts, uneven_tone -> dark_spots, sensitivity -> redness
(score 1.0 when present, 0.0 otherwise). pores and fine_lines are NOT covered
by the current prompt and are reported as uncovered — never faked as zero.
A null skin_type_hint counts as an abstention.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import time
from pathlib import Path

import requests
from PIL import Image

from ml.eval.readers.base import Reader

READER_VERSION = "1"
DEFAULT_ENDPOINT = "http://127.0.0.1:8765/api/analyze"
CACHE_DIR = Path(__file__).resolve().parents[1] / "cache"

OBSERVATION_TO_CANON = {
    "breakouts": "breakouts",
    "uneven_tone": "dark_spots",
    "sensitivity": "redness",
}
COVERED_CONCERNS = sorted(set(OBSERVATION_TO_CANON.values()))

MAX_SIDE = 1024
JPEG_QUALITY = 85
MAX_RETRIES = 4
TIMEOUT_S = 60


def downscale_to_jpeg_b64(image_path: str, max_side: int = MAX_SIDE) -> str:
    img = Image.open(image_path).convert("RGB")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY)
    return base64.b64encode(buf.getvalue()).decode("ascii")


class GeminiHttpReader(Reader):
    name = "gemini_http"
    supports_concurrency = True

    def __init__(self, endpoint: str = DEFAULT_ENDPOINT, cache_dir: Path | None = None):
        self.endpoint = endpoint
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        # The endpoint namespaces the cache so local stubs and production do
        # not share predictions.
        ep_tag = hashlib.sha1(endpoint.encode("utf-8")).hexdigest()[:8]
        self.cache_path = self.cache_dir / f"{self.name}-{READER_VERSION}-{ep_tag}.jsonl"
        self._cache = self._load_cache()
        self._session = requests.Session()

    def version(self) -> str:
        return READER_VERSION

    def _load_cache(self) -> dict:
        cache = {}
        if self.cache_path.is_file():
            for line in self.cache_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "image_id" in row and "prediction" in row:
                    cache[row["image_id"]] = row["prediction"]
        return cache

    def _append_cache(self, image_id: str, prediction: dict) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"image_id": image_id, "prediction": prediction}) + "\n")

    def _call_with_retry(self, b64: str) -> tuple[dict, float, int]:
        attempt = 0
        while True:
            start = time.perf_counter()
            try:
                resp = self._session.post(
                    self.endpoint,
                    json={"image_base64": b64, "mime_type": "image/jpeg"},
                    timeout=TIMEOUT_S,
                )
                status = resp.status_code
                body = resp.json() if resp.content else {}
            except requests.RequestException:
                status, body = -1, {}
            latency_ms = (time.perf_counter() - start) * 1000.0
            if status == 429 or status >= 500 or status == -1:
                attempt += 1
                if attempt > MAX_RETRIES:
                    return body, latency_ms, status
                time.sleep(min(2.0**attempt, 15.0))
                continue
            return body, latency_ms, status

    @staticmethod
    def _call_failed(body: dict, status: int) -> bool:
        if status < 200 or status >= 300:
            return True
        if body.get("error_kind"):
            return True
        return not body.get("analysis_available") or body.get("observations") is None

    def _predict_uncached(self, record: dict, image_path: str) -> tuple[dict, bool]:
        b64 = downscale_to_jpeg_b64(image_path)
        body, latency_ms, status = self._call_with_retry(b64)
        failed = self._call_failed(body, status)
        error_kind = body.get("error_kind")
        if failed and not error_kind:
            error_kind = "network" if status == -1 else f"http_{status}"
        obs = body.get("observations") or {}
        hint = obs.get("skin_type_hint")
        observed = set(obs.get("concerns") or [])
        concerns = {}
        for canon in COVERED_CONCERNS:
            score = 0.0
            for obs_name, canon_name in OBSERVATION_TO_CANON.items():
                if canon_name == canon and obs_name in observed:
                    score = 1.0
            concerns[canon] = score
        pred = {
            "skin_type": hint,  # null hint = abstain
            "skin_type_probs": None,
            "concerns": concerns,
            "covered_concerns": list(COVERED_CONCERNS),
            "confidence": obs.get("confidence"),
            "meta": {
                "provider": body.get("provider"),
                "model": body.get("model"),
                "prompt_version": body.get("prompt_version"),
                "latency_ms": round(latency_ms, 2),
                "server_latency_ms": body.get("latency_ms"),
                "error_kind": error_kind,
                "http_status": status,
                "observations_null": obs == {} or body.get("observations") is None,
            },
        }
        return pred, failed

    def predict(self, record: dict, image_path: str) -> dict:
        key = record.get("image_id") or image_path
        if key in self._cache:
            cached = dict(self._cache[key])
            meta = dict(cached.get("meta") or {})
            meta["cache_hit"] = True
            cached["meta"] = meta
            return cached
        pred, failed = self._predict_uncached(record, image_path)
        if not failed:
            # Failed calls are never cached so reruns retry them.
            self._cache[key] = pred
            self._append_cache(key, pred)
        return pred
