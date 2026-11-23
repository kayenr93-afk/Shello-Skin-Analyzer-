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
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
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


def availability() -> dict:
    gemini = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    openai = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    return {
        "ok": True,
        "analysis_available": gemini or openai,
        "provider": "gemini" if gemini else ("openai" if openai else None),
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
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={key}"
        )
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
        "https://api.openai.com/v1/chat/completions",
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

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/analyze":
            self._json(200, availability())
            return
        super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
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
                "observations": observations,
                "message": "Cosmetic appearance notes only — not a diagnosis. The photo was not stored.",
            })
        except Exception:
            self._json(200, {
                "ok": True,
                "analysis_available": True,
                "provider": avail["provider"],
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
