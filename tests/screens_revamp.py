"""Phone-width screenshots of every quiz screen (screenshots/revamp-*.png).

    ml\\.venv\\Scripts\\python tests/screens_revamp.py
"""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "screenshots"
OUT.mkdir(exist_ok=True)


def main():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
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
    errors = []
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
            page.on("console", lambda m: m.type == "error" and "insights" not in m.text and errors.append(m.text))
            page.on("pageerror", lambda e: errors.append(str(e)))
            shot = lambda name: page.screenshot(path=str(OUT / f"revamp-{name}.png"), full_page=True)
            page.goto(base + "/", wait_until="load"); page.wait_for_selector("#btn-start-quiz")
            shot("1-landing")
            page.click("#btn-start-quiz")
            page.click('.choice[data-id="oily"]')
            shot("2-skin")
            page.click("#btn-next")
            page.click('.choice[data-id="breakouts"]')
            page.click("#btn-next")
            shot("3-safety-empty")
            page.click('#flags .choice[data-id="none"]')
            page.click("#btn-next")
            page.click('.choice[data-id="face"]')
            page.click("#btn-next")
            page.click('#opts-climate .choice[data-id="humid"]')
            shot("5-climate-hint")
            page.click('#opts-outdoors .choice[data-id="not_sure"]')
            page.click("#btn-next")
            page.click("details.more summary")
            page.click('.choice[data-id="everyday"]')
            shot("6-budget")
            page.click("#btn-next")
            shot("7-selfie")
            page.click("#btn-next")
            page.wait_for_selector(".routine-list")
            page.wait_for_timeout(1500)
            shot("8-result")
            page.click(".routine-list .product:first-child .btn-add-item")
            page.wait_for_selector(".btn-added")
            for lang in ("si", "ta"):
                page.goto(base + "/", wait_until="load"); page.wait_for_selector("#btn-start-quiz")
                page.click(f'button[data-lang="{lang}"]')
                page.wait_for_timeout(300)
                shot(f"1-landing-{lang}")
            page.click('button[data-lang="en"]')
    finally:
        srv.terminate()
    print("errors:", errors or "none")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
