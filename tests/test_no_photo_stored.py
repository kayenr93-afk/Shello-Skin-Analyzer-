"""Verify a submitted selfie is never written to disk, logs, or responses.

Starts a stub "Gemini" HTTP service plus the real server.py (pointed at the
stub via GEMINI_API_BASE), POSTs a JPEG carrying a unique sentinel in a COM
segment, then checks every new/modified file in the repo and temp dir, the
server's captured output, and the response body for the sentinel and the
first 40 chars of the base64 body.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".vercel", ".pytest_cache"}
MAX_SCAN_BYTES = 50 * 1024 * 1024

B64_PREFIX_LEN = 40


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_jpeg_with_sentinel(sentinel: bytes) -> bytes:
    """Random-pixel JPEG with the sentinel embedded in a COM segment."""
    try:
        import numpy as np
        from PIL import Image

        rng = np.random.default_rng(secrets.randbelow(2**31))
        arr = rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8)
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, "JPEG", quality=80)
        jpeg = buf.getvalue()
    except ImportError:
        # Pillow missing: minimal JPEG-shaped bytes (SOI + JFIF + EOI). The
        # API never parses the image; the COM sentinel is what matters.
        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + secrets.token_bytes(64) + b"\xff\xd9"
    if not jpeg.startswith(b"\xff\xd8"):
        pytest.fail("generated JPEG has no SOI marker")
    com = b"\xff\xfe" + (len(sentinel) + 2).to_bytes(2, "big") + sentinel
    return jpeg[:2] + com + jpeg[2:]


def _snapshot(root: Path) -> dict:
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            snap[str(p)] = (st.st_size, st.st_mtime_ns)
    return snap


class _StubGemini(BaseHTTPRequestHandler):
    received_b64: list[str] = []

    def log_message(self, *args):  # keep the stub silent
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {}
        parts = (((body.get("contents") or [{}])[0]).get("parts")) or []
        for part in parts:
            data = (part.get("inlineData") or {}).get("data")
            if data:
                _StubGemini.received_b64.append(data)
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "skin_type_hint": "normal",
                                        "concerns": ["breakouts"],
                                        "notes": "stub response",
                                        "refer_dermatologist": False,
                                        "confidence": "low",
                                    }
                                )
                            }
                        ]
                    }
                }
            ]
        }
        out = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def stub_and_server():
    stub_port = _free_port()
    stub = ThreadingHTTPServer(("127.0.0.1", stub_port), _StubGemini)
    threading.Thread(target=stub.serve_forever, daemon=True).start()

    server_port = _free_port()
    env = dict(os.environ)
    env["GEMINI_API_KEY"] = "dummy-key-for-tests"
    env["GEMINI_API_BASE"] = f"http://127.0.0.1:{stub_port}"
    env.pop("OPENAI_API_KEY", None)
    proc = subprocess.Popen(
        [sys.executable, str(REPO / "server.py"), "--host", "127.0.0.1", "--port", str(server_port)],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.time() + 20
        up = False
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{server_port}/api/analyze", timeout=1
                ) as resp:
                    if resp.status == 200:
                        up = True
                        break
            except Exception:
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
        if not up:
            out = proc.stdout.read() if proc.stdout else ""
            pytest.fail(f"server.py did not start:\n{out}")
        yield {
            "endpoint": f"http://127.0.0.1:{server_port}/api/analyze",
            "proc": proc,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        stub.shutdown()


def test_no_photo_stored(stub_and_server):
    endpoint = stub_and_server["endpoint"]
    proc = stub_and_server["proc"]
    sentinel = ("SENTINEL-" + secrets.token_hex(16)).encode()
    jpeg = _make_jpeg_with_sentinel(sentinel)
    b64 = base64.b64encode(jpeg).decode()
    b64_prefix = b64[:B64_PREFIX_LEN]

    before_repo = _snapshot(REPO)
    before_tmp = _snapshot(Path(tempfile.gettempdir()))

    req = urllib.request.Request(
        endpoint,
        data=json.dumps({"image_base64": b64, "mime_type": "image/jpeg"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp_body = resp.read().decode("utf-8", "replace")

    # The stub provider must have seen the image, proving the request was real.
    assert _StubGemini.received_b64, "stub provider never received an image"
    assert b64 in _StubGemini.received_b64, "stub provider got a different image body"

    # Response must not echo image bytes.
    assert sentinel.decode() not in resp_body
    assert b64_prefix not in resp_body

    time.sleep(0.5)  # let any lazy writes flush
    after_repo = _snapshot(REPO)
    after_tmp = _snapshot(Path(tempfile.gettempdir()))

    changed = []
    for snap_before, snap_after in ((before_repo, after_repo), (before_tmp, after_tmp)):
        for path, meta in snap_after.items():
            if path not in snap_before or snap_before[path] != meta:
                changed.append(Path(path))

    leaks = []
    for path in changed:
        try:
            if path.stat().st_size > MAX_SCAN_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if sentinel in data or b64_prefix.encode() in data:
            leaks.append(str(path))
    assert not leaks, f"sentinel/base64 found in file(s): {leaks}"

    # Captured server output must not contain the image either.
    proc.terminate()
    try:
        server_out, _ = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        server_out, _ = proc.communicate()
    server_out = server_out or ""
    assert sentinel.decode() not in server_out
    assert b64_prefix not in server_out


def _post_json(url, body, method="POST"):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_events_rejects_image_payloads(stub_and_server):
    """POST /api/events must 400 anything image-shaped and store nothing."""
    endpoint = stub_and_server["endpoint"].replace("/api/analyze", "/api/events")
    sid = "12345678-1234-1234-1234-1234567890ab"
    sentinel = "SENTINEL-" + secrets.token_hex(8)
    base = {"session_id": sid, "type": "session_result"}
    valid_payload = {"lang": "en", "result_source": "quiz", "dermatologist_note": False}

    for bad in (
        dict(valid_payload, kit_id="data:image/jpeg;base64,AAA"),
        dict(valid_payload, kit_id="A" * 120),
        dict(valid_payload, image=sentinel),
        {"lang": "en", "result_source": "quiz", "dermatologist_note": False,
         "payload_image": sentinel},
    ):
        status, _ = _post_json(endpoint, dict(base, payload=bad))
        assert status == 400, bad

    # unknown type
    status, _ = _post_json(endpoint, {"session_id": sid, "type": "selfie", "payload": {}})
    assert status == 400

    # valid event stores, and its stored form contains no sentinel
    status, body = _post_json(endpoint, dict(base, payload=valid_payload))
    assert status == 200 and body.get("stored") is True

    # image-bearing key inside a nested payload object is also rejected
    nested = dict(valid_payload, reader={"source": "gemini", "img": sentinel})
    status, _ = _post_json(endpoint, dict(base, payload=nested))
    assert status == 400

    # DELETE removes the session's events
    status, body = _post_json(endpoint + "?session_id=" + sid, {}, method="DELETE")
    assert status == 200 and body.get("deleted") == 1


def test_event_schema_has_no_image_keys():
    schema = json.loads((REPO / "api" / "_lib" / "event_schema.json").read_text())
    schema.pop("forbidden_string_regex", None)
    text = json.dumps(schema).lower()
    for word in ("image", "photo", "selfie", "base64", "data:"):
        assert word not in text, f"event_schema contains {word!r}"


def test_api_js_no_writes_or_body_logging():
    """Static check: api/*.js must not write files or log request bodies."""
    api_dir = REPO / "api"
    bad = []
    for js in api_dir.glob("*.js"):
        text = js.read_text(encoding="utf-8")
        if re.search(r"\bfs\.(write|append|createWrite)|require\(['\"]fs['\"]\)", text):
            bad.append(f"{js.name}: references fs write APIs")
        if re.search(r"console\.(log|error|warn|info)\s*\([^)]*\b(req|body|image_base64|b64)\b", text):
            bad.append(f"{js.name}: console.* on request body")
    assert not bad, "; ".join(bad)
