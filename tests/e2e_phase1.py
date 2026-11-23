"""Playwright e2e for Phase 1: quality gate, confirm-or-correct, events.

Starts server.py with a stubbed Gemini provider (GEMINI_API_BASE) that
returns a canned read, and DASHBOARD_TOKEN so events can be read back via
/api/dashboard. Run:  python tests/e2e_phase1.py
Requires: pip install -r tests/requirements.txt && playwright install chromium
"""

import io
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
SHOTS = REPO / "screenshots"
SHOTS.mkdir(exist_ok=True)

STUB_BODY = {
    "analysis_available": True,
    "observations": {
        "skin_type_hint": "oily",
        "concerns": ["breakouts"],
        "confidence": "medium",
        "recommendation_focus": "oil balance",
    },
}
TOKEN = "e2e-dash-token-" + secrets.token_hex(4)
BASE = None  # filled in


class StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        # Gemini generateContent response shape; server.py parses the text part.
        payload = {"candidates": [{"content": {"parts": [
            {"text": json.dumps(STUB_BODY["observations"])}]}}]}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_up(url, tries=60):
    for _ in range(tries):
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.25)
    return False


def dash_events():
    req = urllib.request.Request(
        f"{BASE}/api/dashboard?days=1",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())["events"]


def black_jpeg(path):
    from PIL import Image
    Image.new("RGB", (640, 480), (5, 5, 5)).save(path, "JPEG")


def find_face_image():
    root = REPO / "data" / "raw" / "skintone"
    if not root.is_dir():
        return None
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            return p
    return None


results = []


def report(name, ok, note=""):
    results.append((name, ok, note))
    print(("PASS" if ok else "FAIL") + f" {name} {note}")


def goto_selfie(page, lang=None):
    """Drive the quiz UI through to the selfie step (app state is private)."""
    page.goto(BASE + "/", wait_until="domcontentloaded")
    if lang:
        page.click(f'button[data-lang="{lang}"]')
    page.click("#btn-start-quiz")
    page.click('.choice[data-id="oily"]')
    page.click("#btn-next")
    page.click('.choice[data-id="breakouts"]')
    page.click("#btn-next")
    page.click('#flags .choice[data-id="none"]')
    page.click("#btn-next")
    page.click('.choice[data-id="face"]')
    page.click("#btn-next")
    page.click('#opts-climate .choice[data-id="humid"]')
    page.click('#opts-outdoors .choice[data-id="rarely"]')
    page.click("#btn-next")
    page.click('.choice[data-id="everyday"]')
    page.click("#btn-next")
    page.wait_for_selector("#selfie-upload", state="attached")


def main():
    global BASE
    stub = ThreadingHTTPServer(("127.0.0.1", free_port()), StubHandler)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    stub_base = f"http://127.0.0.1:{stub.server_address[1]}"

    port = free_port()
    BASE = f"http://127.0.0.1:{port}"
    env = dict(
        os.environ,
        GEMINI_API_KEY="dummy",
        GEMINI_API_BASE=stub_base,
        DASHBOARD_TOKEN=TOKEN,
    )
    # Log to a file: an undrained PIPE fills up and blocks the server.
    server_log = open(REPO / "tests" / "_e2e_server.log", "w", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, str(REPO / "server.py"), "--port", str(port)],
        cwd=REPO, env=env,
        stdout=server_log, stderr=subprocess.STDOUT, text=True,
    )
    if not wait_up(BASE + "/index.html"):
        print("server.py did not start")
        print((REPO / "tests" / "_e2e_server.log").read_text(encoding="utf-8"))
        server.kill()
        sys.exit(1)

    tmp = REPO / "tests" / "_e2e_tmp"
    tmp.mkdir(exist_ok=True)
    black = tmp / "black.jpg"
    black_jpeg(black)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            analyze_calls = []
            page.on("request", lambda r: analyze_calls.append(r.url)
                    if "/api/analyze" in r.url and r.method == "POST" else None)
            csp_violations = []
            page.on("console", lambda m: csp_violations.append(m.text)
                    if "Content Security Policy" in m.text else None)

            # (a) black photo -> quality gate fails, no analyze call
            goto_selfie(page)
            page.set_input_files("#selfie-upload", str(black))
            page.wait_for_selector("#quality-status li", timeout=30000)
            reasons = page.locator("#quality-status li").all_inner_texts()
            dark = any("dark" in r.lower() for r in reasons)
            report("quality gate fails black photo", dark, "; ".join(reasons))
            page.screenshot(path=str(SHOTS / "phase1-quality-fail.png"))
            page.click("#btn-next")  # See routine
            page.wait_for_timeout(1500)
            report("no analyze call on failed gate", len(analyze_calls) == 0)

            # (b) face photo -> confirm -> correct -> result + event
            face = find_face_image()
            if face is None:
                report("face-path cases", True,
                       "skipped: no image under data/raw/skintone")
            else:
                page.click("#btn-continue-no-photo")  # clear the failed photo
                page.wait_for_selector("#selfie-upload", state="attached")
                page.set_input_files("#selfie-upload", str(face))
                page.wait_for_selector(".quality-ok", timeout=60000)
                report("quality gate passes face photo", True)
                page.click("#btn-next")  # See routine -> analyze + confirm
                page.wait_for_selector("#btn-confirm-yes", timeout=60000)
                report("confirm step shown", True)
                page.screenshot(path=str(SHOTS / "phase1-confirm-en.png"))
                page.click("#btn-confirm-change")
                page.wait_for_selector('#confirm-types [data-id="dry"]')
                page.click('#confirm-types [data-id="dry"]')
                page.click('#confirm-concerns [data-id="dullness"]')
                page.click("#btn-next")
                page.wait_for_selector(".routine-list .product", timeout=15000)
                ev = [e for e in dash_events() if e["type"] == "session_result"]
                ok = (len(ev) >= 1
                      and ev[-1]["payload"].get("confirm", {}).get("action") == "correct"
                      and ev[-1]["payload"]["confirm"].get("corrected_skin_type") == "dry"
                      and "dullness" in ev[-1]["payload"]["confirm"].get("corrected_concerns", []))
                report("session_result action=correct", ok)

                # (d) thumbs + clicks + per-product add to cart
                page.click(".routine-list .product:first-child .thumb[data-val=\"up\"]")
                page.click(".routine-list .product:first-child .buy")
                page.click(".routine-list .product:first-child .btn-add-item")
                page.wait_for_selector(".routine-list .product:first-child .btn-added")
                time.sleep(0.5)
                types = {e["type"] for e in dash_events()}
                report("product_feedback/click/add_to_cart events",
                       {"product_feedback", "click", "add_to_cart"} <= types,
                       str(types))

                # (e) delete my answers
                sid = page.evaluate("ShelloEvents.sessionId()")
                page.click("#btn-delete-answers")
                page.wait_for_timeout(1000)
                remaining = [e for e in dash_events()
                             if e["session_id"] == sid]
                report("delete my answers removes events", len(remaining) == 0)

                # (f) si/ta confirm has no English fallback
                for lang in ("si", "ta"):
                    page2 = browser.new_page()
                    goto_selfie(page2, lang=lang)
                    page2.set_input_files("#selfie-upload", str(face))
                    page2.wait_for_selector(".quality-ok", timeout=60000)
                    page2.click("#btn-next")
                    page2.wait_for_selector("#btn-confirm-yes", timeout=60000)
                    txt = page2.locator("#btn-confirm-yes").inner_text()
                    report(f"confirm localized ({lang})",
                           "Yes, that's right" not in txt, txt)
                    page2.close()

            # (c) Yes path -> action=confirm (use black->? need pass; reuse face if present else stub)
            if face is not None:
                page3 = browser.new_page()
                goto_selfie(page3)
                page3.set_input_files("#selfie-upload", str(face))
                page3.wait_for_selector(".quality-ok", timeout=60000)
                page3.click("#btn-next")
                page3.wait_for_selector("#btn-confirm-yes", timeout=60000)
                sid3 = page3.evaluate("ShelloEvents.sessionId()")
                page3.click("#btn-confirm-yes")
                page3.wait_for_selector(".routine-list .product", timeout=15000)
                ev = [e for e in dash_events()
                      if e["type"] == "session_result"
                      and e["session_id"] == sid3]
                ok = ev and ev[-1]["payload"].get("confirm", {}).get("action") == "confirm"
                report("session_result action=confirm", bool(ok))
                page3.close()

            # (g) MediaPipe actually loads under the CSP, no console violations
            goto_selfie(page)
            status = page.evaluate(
                "ShelloFace.load().then(() => ShelloFace.status())"
            )
            report("MediaPipe landmarker loads", status == "ready", status)
            page.set_input_files("#selfie-upload", str(black))
            page.wait_for_selector("#quality-status", timeout=60000)
            page.wait_for_timeout(2000)
            report("no CSP console violations", len(csp_violations) == 0,
                   str(csp_violations[:3]))

            browser.close()
    finally:
        server.kill()
        stub.shutdown()
        try:
            black.unlink()
            tmp.rmdir()
        except OSError:
            pass

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
