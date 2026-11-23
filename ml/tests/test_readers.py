"""Reader tests: prior, and gemini_http against a local stub /api/analyze."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ml.eval.readers.gemini_http import GeminiHttpReader
from ml.eval.readers.prior import PriorReader


def test_prior_majority_and_quiz_fields():
    reader = PriorReader(majority_skin_type="normal")
    out = reader.predict({"image_id": "1"}, "unused.jpg")
    assert out["skin_type"] == "normal"
    assert out["concerns"] == {}
    out2 = reader.predict(
        {"image_id": "2", "quiz_skin_type": "oily", "quiz_concerns": ["pores"]},
        "unused.jpg",
    )
    assert out2["skin_type"] == "oily"
    assert out2["concerns"] == {"pores": 1.0}
    assert out2["covered_concerns"] == ["pores"]


class _AnalyzeStub(BaseHTTPRequestHandler):
    hits = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        _AnalyzeStub.hits += 1
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = json.dumps(
            {
                "ok": True,
                "analysis_available": True,
                "provider": "gemini",
                "model": "stub-model-1",
                "prompt_version": "2026-11-21",
                "latency_ms": 12.5,
                "observations": {
                    "skin_type_hint": "oily",
                    "concerns": ["breakouts", "uneven_tone"],
                    "notes": "stub",
                    "refer_dermatologist": False,
                    "confidence": "medium",
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def analyze_stub(tmp_path):
    _AnalyzeStub.hits = 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _AnalyzeStub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/api/analyze"
    srv.shutdown()


def test_gemini_http_mapping_and_cache(analyze_stub, tmp_path, img_factory):
    img = img_factory(tmp_path / "face.jpg", noise_seed=1)
    cache_dir = tmp_path / "cache"
    reader = GeminiHttpReader(endpoint=analyze_stub, cache_dir=cache_dir)
    rec = {"image_id": "abc123"}
    out = reader.predict(rec, str(img))
    assert out["skin_type"] == "oily"
    # uneven_tone maps to dark_spots; breakouts covered; redness covered at 0.
    assert out["concerns"] == {"breakouts": 1.0, "dark_spots": 1.0, "redness": 0.0}
    assert out["covered_concerns"] == ["breakouts", "dark_spots", "redness"]
    assert "pores" not in out["concerns"] and "fine_lines" not in out["concerns"]
    assert out["meta"]["model"] == "stub-model-1"
    assert out["meta"]["prompt_version"] == "2026-11-21"
    assert out["meta"]["server_latency_ms"] == 12.5
    assert out["meta"]["latency_ms"] is not None
    assert _AnalyzeStub.hits == 1
    cached_files = list(cache_dir.glob(f"gemini_http-{reader.version()}-*.jsonl"))
    assert len(cached_files) == 1  # filename is namespaced by endpoint sha1

    # New reader instance, warm cache: no extra HTTP call.
    reader2 = GeminiHttpReader(endpoint=analyze_stub, cache_dir=cache_dir)
    out2 = reader2.predict(rec, str(img))
    assert out2["skin_type"] == "oily"
    assert out2["meta"]["cache_hit"] is True
    assert _AnalyzeStub.hits == 1


def test_gemini_http_failures_not_cached(tmp_path, img_factory):
    """A 500-then-200 stub: the failed call must not be cached; the next run
    retries, succeeds, and caches."""
    calls = {"n": 0}

    class _Flaky(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls["n"] += 1
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            if calls["n"] <= 5:
                self.send_response(500)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
                return
            body = json.dumps(
                {
                    "ok": True,
                    "analysis_available": True,
                    "provider": "gemini",
                    "model": "stub-model-1",
                    "observations": {"skin_type_hint": "dry", "concerns": []},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Flaky)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        endpoint = f"http://127.0.0.1:{srv.server_address[1]}/api/analyze"
        img = img_factory(tmp_path / "face.jpg", noise_seed=3)
        rec = {"image_id": "flaky1"}
        cache_dir = tmp_path / "cache"

        reader1 = GeminiHttpReader(endpoint=endpoint, cache_dir=cache_dir)
        out1 = reader1.predict(rec, str(img))
        assert out1["skin_type"] is None  # abstain on failure
        assert out1["meta"]["error_kind"] == "http_500"
        assert calls["n"] == 5  # 1 initial + MAX_RETRIES retries
        assert not list(cache_dir.glob("*.jsonl"))  # nothing cached

        # New reader instance: retries the endpoint (not served from cache).
        reader2 = GeminiHttpReader(endpoint=endpoint, cache_dir=cache_dir)
        out2 = reader2.predict(rec, str(img))
        assert out2["skin_type"] == "dry"
        assert calls["n"] == 6
        assert list(cache_dir.glob("gemini_http-*.jsonl"))

        # Third reader: warm cache, no more calls.
        reader3 = GeminiHttpReader(endpoint=endpoint, cache_dir=cache_dir)
        out3 = reader3.predict(rec, str(img))
        assert out3["meta"]["cache_hit"] is True
        assert calls["n"] == 6
    finally:
        srv.shutdown()


def test_gemini_http_abstain(analyze_stub, tmp_path, img_factory):
    img = img_factory(tmp_path / "face2.jpg", noise_seed=2)

    class _Abstain(_AnalyzeStub):
        def do_POST(self):
            _AnalyzeStub.hits += 1
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            body = json.dumps(
                {
                    "ok": True,
                    "provider": "gemini",
                    "model": "m",
                    "observations": {
                        "skin_type_hint": None,
                        "concerns": [],
                        "confidence": "low",
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Abstain)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        reader = GeminiHttpReader(
            endpoint=f"http://127.0.0.1:{srv.server_address[1]}/api/analyze",
            cache_dir=tmp_path / "cache2",
        )
        out = reader.predict({"image_id": "z"}, str(img))
        assert out["skin_type"] is None  # abstain
    finally:
        srv.shutdown()
