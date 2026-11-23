#!/usr/bin/env python3
"""Local static server plus /api/analyze.

Loads GEMINI_API_KEY from the environment or from .env.gemini / .env
in this directory (values are never printed). Prefers Gemini, same
contract as api/analyze.js. Selfie bytes stay in the request and are
not written to disk.

Usage:
    python3 server.py            # http://127.0.0.1:8765
    python3 server.py --port 8765
"""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
GEMINI_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-flash-latest",
]
OPENAI_MODEL = "gpt-4o-mini"
PROMPT_VERSION = "2026-11-21"
# API base overrides exist only so tests can point at a local stub.
GEMINI_API_BASE = os.environ.get(
    "GEMINI_API_BASE", "https://generativelanguage.googleapis.com"
).rstrip("/")
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com").rstrip("/")
SYSTEM_PROMPT = """You are a cosmetic skin-care assistant for Shello Cosmetics.
You look at an optional selfie and return JSON only.

Rules:
- Cosmetic appearance notes only. Never diagnose, never name a medical condition, never suggest prescription treatment.
- If the photo appears to show painful-looking cysts, a widespread rash, bleeding, or a sudden / severe change, set refer_dermatologist to true and keep observations conservative.
- Do not invent product names or ingredients. Do not recommend any product.
- If the image is not a face or body skin photo, say so in notes and leave hints empty.
- Concerns must be chosen only from this list: breakouts, dullness, dryness, uneven_tone, tan, sensitivity, oil_control, sun_protection, hydration.
- skin_type_hint must be one of: oily, dry, combination, normal, sensitive, or null if unsure.

Return a JSON object with this exact shape:
{
  "skin_type_hint": "oily" | "dry" | "combination" | "normal" | "sensitive" | null,
  "concerns": string[],
  "notes": string,
  "refer_dermatologist": boolean,
  "confidence": "low" | "medium" | "high"
}"""

# Same CSP as vercel.json so local testing matches production headers.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval' https://va.vercel-scripts.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: blob: https://shellocosmetics.com; media-src 'self' blob:; "
    "connect-src 'self' https://vitals.vercel-insights.com https://va.vercel-scripts.com; "
    "worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)

_EVENT_SCHEMA = json.loads(
    (HERE / "api" / "_lib" / "event_schema.json").read_text(encoding="utf-8")
)
_FORBIDDEN_STR = re.compile(_EVENT_SCHEMA["forbidden_string_regex"], re.I)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# In-memory event store for local runs (Neon is used on Vercel).
EVENTS: list[dict] = []
EVENTS_LOCK = threading.Lock()


def _check_event_string(s: str) -> bool:
    return len(s) <= _EVENT_SCHEMA["max_string_chars"] and not _FORBIDDEN_STR.search(s)


def _check_event_field(value, spec) -> bool:
    if value is None:
        return True  # optional
    t = spec.get("t")
    if t == "str":
        return isinstance(value, str) and _check_event_string(value)
    if t == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "num":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "bool":
        return isinstance(value, bool)
    if t in ("str[]", "int[]"):
        if not isinstance(value, list) or len(value) > _EVENT_SCHEMA["max_array_items"]:
            return False
        if t == "str[]":
            return all(isinstance(v, str) and _check_event_string(v) for v in value)
        return all(isinstance(v, int) and not isinstance(v, bool) for v in value)
    if t == "obj":
        if not isinstance(value, dict):
            return False
        sub_fields = spec.get("fields") or {}
        if any(k not in sub_fields for k in value):
            return False
        return all(
            _check_event_field(value.get(k), sub) and _check_enum(value.get(k), sub)
            for k, sub in sub_fields.items()
        )
    return False


def _check_enum(value, spec) -> bool:
    if spec.get("enum") and value is not None:
        return value in spec["enum"]
    return True


def validate_event(event_type, payload) -> bool:
    """Strict allowlist validation shared with api/_lib/validate.js."""
    tspec = _EVENT_SCHEMA["types"].get(event_type)
    if not tspec or not isinstance(payload, dict):
        return False
    fields = tspec.get("fields") or {}
    if any(k not in fields for k in payload):
        return False
    if any(payload.get(k) is None for k in tspec.get("required") or []):
        return False
    return all(
        _check_event_field(payload.get(k), spec) and _check_enum(payload.get(k), spec)
        for k, spec in fields.items()
    )


ALLOWED_TYPES = {"oily", "dry", "combination", "normal", "sensitive"}
ALLOWED_CONCERNS = {
    "breakouts",
    "dullness",
    "dryness",
    "uneven_tone",
    "tan",
    "sensitivity",
    "oil_control",
    "sun_protection",
    "hydration",
}


def load_env_files() -> None:
    for name in (".env.gemini", ".env", ".env.local"):
        path = HERE / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _device_reader_pct() -> int:
    try:
        return max(0, min(100, int(os.environ.get("DEVICE_READER_PCT", "0"))))
    except ValueError:
        return 0


def availability() -> dict:
    gemini = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    openai = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    return {
        "ok": True,
        "analysis_available": gemini or openai,
        "provider": "gemini" if gemini else ("openai" if openai else None),
        "device_reader_pct": _device_reader_pct(),
        "message": (
            "Optional selfie analysis is available. Photos are not stored."
            if gemini or openai
            else "Selfie analysis is not configured. Your quiz answers will still be used to build a routine."
        ),
    }


def _strip_data_url(image_b64: str, mime: str) -> tuple[str, str]:
    image_b64 = (image_b64 or "").strip()
    mime = (mime or "image/jpeg").strip()
    if image_b64.startswith("data:"):
        match = re.match(r"^data:([^;]+);base64,(.+)$", image_b64, re.S)
        if match:
            mime = match.group(1)
            image_b64 = match.group(2)
    image_b64 = re.sub(r"\s+", "", image_b64)
    return image_b64, mime


def safe_parse_json(text: str) -> dict | None:
    if not text:
        return None
    trimmed = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", trimmed)
    raw = fence.group(1) if fence else trimmed
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None


def sanitize(parsed: dict | None) -> dict:
    parsed = parsed or {}
    hint = parsed.get("skin_type_hint") if parsed.get("skin_type_hint") in ALLOWED_TYPES else None
    concerns = [c for c in (parsed.get("concerns") or []) if c in ALLOWED_CONCERNS]
    confidence = parsed.get("confidence") if parsed.get("confidence") in {"low", "medium", "high"} else "low"
    notes = parsed.get("notes") if isinstance(parsed.get("notes"), str) else ""
    return {
        "skin_type_hint": hint,
        "concerns": concerns,
        "notes": notes[:400],
        "refer_dermatologist": bool(parsed.get("refer_dermatologist")),
        "confidence": confidence,
    }


def http_json(url: str, payload: dict, headers: dict, timeout: int = 20) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw.decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            data = {"error": {"message": f"HTTP {exc.code}"}}
        return exc.code, data


def analyze_gemini(image_b64: str, mime: str) -> tuple[str, dict]:
    key = os.environ["GEMINI_API_KEY"].strip()
    last_error = "No Gemini model accepted the request"
    payload_base = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": "Review this selfie for cosmetic skin-appearance notes only. Return JSON."},
                    {"inlineData": {"mimeType": mime, "data": image_b64}},
                ],
            }
        ],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    for model in GEMINI_MODELS:
        url = f"{GEMINI_API_BASE}/v1beta/models/{model}:generateContent?key={key}"
        status, data = http_json(
            url,
            payload_base,
            {"Content-Type": "application/json", "User-Agent": "ShelloSkinAnalyzer/1.0"},
        )
        if status in (404, 503) or (
            status >= 400
            and re.search(
                r"not found|not supported|unknown model|high demand|unavailable|no longer available",
                str((data.get("error") or {}).get("message") or ""),
                re.I,
            )
        ):
            last_error = (data.get("error") or {}).get("message") or f"Gemini HTTP {status}"
            continue
        if status >= 400:
            raise RuntimeError((data.get("error") or {}).get("message") or f"Gemini HTTP {status}")
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        text = "\n".join(p.get("text") or "" for p in parts)
        parsed = safe_parse_json(text)
        return model, sanitize(parsed)
    raise RuntimeError(last_error)


def analyze_openai(image_b64: str, mime: str) -> tuple[str, dict]:
    key = os.environ["OPENAI_API_KEY"].strip()
    payload = {
        "model": OPENAI_MODEL,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Review this selfie for cosmetic skin-appearance notes only. Return JSON.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                    },
                ],
            },
        ],
    }
    status, data = http_json(
        f"{OPENAI_API_BASE}/v1/chat/completions",
        payload,
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": "ShelloSkinAnalyzer/1.0",
        },
    )
    if status >= 400:
        raise RuntimeError((data.get("error") or {}).get("message") or f"OpenAI HTTP {status}")
    text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    return OPENAI_MODEL, sanitize(safe_parse_json(text))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HERE), **kwargs)

    def end_headers(self):
        # Same CSP as vercel.json so local testing matches production.
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, format, *args):
        # Do not log request bodies (they may contain images). Path + status only.
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if self.path.split("?", 1)[0] == "/api/analyze":
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_error(404)

    def _read_json_body(self, max_bytes):
        length = int(self.headers.get("Content-Length") or 0)
        if length > max_bytes:
            self.rfile.read(length)
            return None, "too_big"
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw.decode("utf-8") or "{}"), None
        except json.JSONDecodeError:
            return None, "bad_json"

    def _dashboard_auth(self):
        token = os.environ.get("DASHBOARD_TOKEN", "")
        if not token:
            return "unset"
        header = self.headers.get("Authorization", "")
        got = re.sub(r"^Bearer\s+", "", header, flags=re.I)
        return "ok" if hmac.compare_digest(got, token) else "bad"

    def _handle_events_post(self):
        body, err = self._read_json_body(_EVENT_SCHEMA["max_body_bytes"])
        if err or not isinstance(body, dict):
            self._json(400, {"ok": False})
            return
        if any(k not in ("type", "payload", "session_id") for k in body) or len(body) > 3:
            self._json(400, {"ok": False})
            return
        session_id = body.get("session_id")
        event_type = body.get("type")
        if not isinstance(event_type, str) or not _UUID_RE.match(str(session_id or "")):
            self._json(400, {"ok": False})
            return
        if not validate_event(event_type, body.get("payload")):
            self._json(400, {"ok": False})
            return
        with EVENTS_LOCK:
            EVENTS.append(
                {
                    "id": len(EVENTS) + 1,
                    "session_id": session_id,
                    "type": event_type,
                    "payload": body["payload"],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        self._json(200, {"ok": True, "stored": True})

    def _handle_events_delete(self):
        from urllib.parse import urlparse, parse_qs

        sid = (parse_qs(urlparse(self.path).query).get("session_id") or [""])[0]
        if not _UUID_RE.match(sid):
            self._json(400, {"ok": False, "error": "invalid session_id"})
            return
        with EVENTS_LOCK:
            before = len(EVENTS)
            EVENTS[:] = [e for e in EVENTS if e["session_id"] != sid]
            deleted = before - len(EVENTS)
        self._json(200, {"ok": True, "deleted": deleted})

    def _handle_dashboard_get(self):
        from urllib.parse import urlparse, parse_qs

        auth = self._dashboard_auth()
        if auth == "unset":
            self._json(503, {"ok": False, "error": "dashboard not configured"})
            return
        if auth != "ok":
            self._json(401, {"ok": False})
            return
        qs = parse_qs(urlparse(self.path).query)
        try:
            days = min(max(int((qs.get("days") or ["30"])[0]), 1), 180)
        except ValueError:
            days = 30
        since = datetime.now(timezone.utc) - timedelta(days=days)
        with EVENTS_LOCK:
            rows = [e for e in EVENTS if e["created_at"] >= since.isoformat()][-50000:]
        self._json(200, {"ok": True, "events": rows, "days": days})

    def _handle_dashboard_delete(self):
        from urllib.parse import urlparse, parse_qs

        auth = self._dashboard_auth()
        if auth == "unset":
            self._json(503, {"ok": False, "error": "dashboard not configured"})
            return
        if auth != "ok":
            self._json(401, {"ok": False})
            return
        code = (parse_qs(urlparse(self.path).query).get("code") or [""])[0]
        if not re.match(r"^[0-9a-fA-F]{8}$", code):
            self._json(400, {"ok": False, "error": "code must be 8 hex chars"})
            return
        with EVENTS_LOCK:
            before = len(EVENTS)
            EVENTS[:] = [e for e in EVENTS if not e["session_id"].startswith(code)]
            deleted = before - len(EVENTS)
        self._json(200, {"ok": True, "deleted": deleted})

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/analyze":
            self._json(200, availability())
            return
        if path == "/api/dashboard":
            self._handle_dashboard_get()
            return
        super().do_GET()

    def do_DELETE(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/events":
            self._handle_events_delete()
            return
        if path == "/api/dashboard":
            self._handle_dashboard_delete()
            return
        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/events":
            self._handle_events_post()
            return
        if path != "/api/analyze":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 12_000_000:
            self._json(413, {
                "ok": False,
                "analysis_available": availability()["analysis_available"],
                "provider": availability()["provider"],
                "error": "That photo is too large. Please try a smaller snapshot.",
            })
            return
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            body = {}
        avail = availability()
        image_b64, mime = _strip_data_url(
            (body.get("image_base64") or body.get("image") or ""),
            body.get("mime_type") or body.get("mimeType") or "image/jpeg",
        )
        if not avail["analysis_available"]:
            self._json(200, {**avail, "observations": None})
            return
        if not image_b64:
            self._json(200, {
                **avail,
                "observations": None,
                "message": "No selfie was sent. Your quiz answers will still be used to build a routine.",
            })
            return
        if not re.match(r"^image/(jpeg|jpg|png|webp|gif|heic|heif)$", mime, re.I):
            self._json(400, {
                "ok": False,
                "analysis_available": True,
                "provider": avail["provider"],
                "error": "Please upload a JPEG, PNG, or WebP photo.",
            })
            return
        try:
            started = time.monotonic()
            if os.environ.get("GEMINI_API_KEY", "").strip():
                model, observations = analyze_gemini(image_b64, mime)
                provider = "gemini"
            else:
                model, observations = analyze_openai(image_b64, mime)
                provider = "openai"
            self._json(200, {
                "ok": True,
                "analysis_available": True,
                "provider": provider,
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "latency_ms": round((time.monotonic() - started) * 1000.0, 2),
                "observations": observations,
                "message": "Cosmetic appearance notes only — not a diagnosis. The photo was not stored.",
            })
        except Exception as exc:
            name = type(exc).__name__.lower()
            if "timeout" in name or "timedout" in name or isinstance(exc, TimeoutError):
                error_kind = "timeout"
            elif "json" in name or "decode" in name:
                error_kind = "parse"
            else:
                error_kind = "provider_error"
            self._json(200, {
                "ok": True,
                "analysis_available": True,
                "provider": avail["provider"],
                "model": None,
                "prompt_version": PROMPT_VERSION,
                "error_kind": error_kind,
                "observations": None,
                "message": "Selfie analysis could not be completed just now. Your quiz answers will still be used to build a routine.",
            })


def main():
    load_env_files()
    parser = argparse.ArgumentParser(description="Serve the Shello skin analyzer locally.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    avail = availability()
    print(f"Shello skin analyzer at http://{args.host}:{args.port}", flush=True)
    print(
        "Selfie analysis: "
        + (
            f"available via {avail['provider']}"
            if avail["analysis_available"]
            else "not configured (quiz matching still works)"
        )
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        httpd.server_close()


if __name__ == "__main__":
    main()
