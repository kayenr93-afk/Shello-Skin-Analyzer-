#!/usr/bin/env python3
"""Serve-locally checks, permalink audit, and three persona screenshot runs."""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SCREENSHOTS = HERE / "screenshots"
BASE = os.environ.get("SKIN_ANALYZER_BASE", "http://127.0.0.1:8765")

PERSONAS = [
    {
        "id": "oily-breakouts",
        "label": "Oily with breakouts",
        "filename": "persona-oily-breakouts.png",
        "quiz": {
            "skin_type": "oily",
            "concerns": ["breakouts", "oil_control"],
            "area": "face",
            "budget": "everyday",
            "red_flags": [],
        },
    },
    {
        "id": "dry-dull-value",
        "label": "Dry and dull on a low budget",
        "filename": "persona-dry-dull-value.png",
        "quiz": {
            "skin_type": "dry",
            "concerns": ["dullness", "dryness"],
            "area": "face",
            "budget": "value",
            "red_flags": [],
        },
    },
    {
        "id": "sensitive",
        "label": "Sensitive",
        "filename": "persona-sensitive.png",
        "quiz": {
            "skin_type": "sensitive",
            "concerns": ["sensitivity"],
            "area": "face",
            "budget": "everyday",
            "red_flags": [],
        },
    },
]


def http_status(url: str, timeout: int = 30, attempts: int = 4) -> int:
    ctx = ssl.create_default_context()
    last = 0
    for attempt in range(attempts):
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"User-Agent": "ShelloSkinAnalyzerLinkCheck/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except Exception:
            last = 0
            time.sleep(0.6 * (attempt + 1))
    # curl often succeeds when Python hits a transient TLS EOF
    try:
        import subprocess
        proc = subprocess.run(
            ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "-L",
             "--retry", "2", "--retry-delay", "1", "--max-time", str(timeout),
             "-A", "ShelloSkinAnalyzerLinkCheck/1.0", url],
            capture_output=True, text=True, timeout=timeout + 10,
        )
        code = int(proc.stdout.strip() or 0)
        return code
    except Exception:
        return last


def wait_for_server(url: str, tries: int = 30) -> bool:
    for _ in range(tries):
        try:
            if http_status(url) == 200:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def start_server_if_needed() -> subprocess.Popen | None:
    if wait_for_server(BASE + "/", tries=2):
        return None
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "server.py"), "--host", "127.0.0.1", "--port", "8765"],
        cwd=str(HERE),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if not wait_for_server(BASE + "/", tries=40):
        out = proc.stdout.read() if proc.stdout else b""
        proc.terminate()
        raise SystemExit("Local server failed to start.\n" + out.decode("utf-8", "replace")[:1000])
    return proc


def check_permalinks(catalog: dict) -> dict:
    urls = []
    for item in catalog.get("included") or []:
        url = item.get("permalink")
        if url and url not in urls:
            urls.append(url)
    results = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(http_status, url): url for url in urls}
        for fut in as_completed(futs):
            url = futs[fut]
            results[url] = fut.result()
    ok = sum(1 for s in results.values() if s == 200)
    failed = {u: s for u, s in results.items() if s != 200}
    return {"total": len(urls), "ok": ok, "failed": failed, "results": results}


def choose(page, selector, data_id: str):
    page.locator(f'{selector} [data-id="{data_id}"]').click()


def run_persona(page, persona, width=430, height=900):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(BASE + "/", wait_until="networkidle")
    page.wait_for_selector("#btn-start-quiz")
    page.locator("#btn-start-quiz").click()
    page.wait_for_selector("#opts")
    quiz = persona["quiz"]
    # Step 1 skin type
    choose(page, "#opts", quiz["skin_type"])
    page.locator("#btn-next").click()
    # Step 2 concerns + flags (max two concerns)
    page.wait_for_selector("#opts")
    for cid in (quiz["concerns"] or [])[:2]:
        choose(page, "#opts", cid)
    for fid in quiz.get("red_flags") or []:
        choose(page, "#flags", fid)
    page.locator("#btn-next").click()
    # Step 3 area
    page.wait_for_selector("#opts")
    choose(page, "#opts", quiz["area"])
    page.locator("#btn-next").click()
    # Step 4 climate (light context; default mixed + sometimes so skin type still leads)
    page.wait_for_selector("#opts-climate")
    choose(page, "#opts-climate", quiz.get("climate") or "mixed")
    choose(page, "#opts-outdoors", quiz.get("outdoors") or "sometimes")
    page.locator("#btn-next").click()
    # Step 5 budget
    page.wait_for_selector("#opts")
    choose(page, "#opts", quiz["budget"])
    page.locator("#btn-next").click()
    # Step 6 selfie skip
    page.wait_for_selector("#btn-next")
    page.locator("#btn-next").click()
    page.wait_for_selector(".routine-list, .alert-danger")
    try:
        page.wait_for_function(
            """() => {
              const imgs = [...document.querySelectorAll('.product img')];
              return imgs.every(img => img.complete);
            }""",
            timeout=8000,
        )
    except Exception:
        pass
    time.sleep(0.3)
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOTS / persona["filename"]
    page.screenshot(path=str(path), full_page=True)
    # Collect routine from the DOM
    steps = []
    for card in page.locator(".product").all():
        steps.append({
            "label": card.locator(".step-name").inner_text(),
            "name": card.locator("h3").inner_text(),
            "meta": card.locator(".meta").inner_text(),
            "reason": card.locator(".reason").inner_text(),
            "link": card.locator("a.buy").get_attribute("href"),
        })
    heading = page.locator("h2").inner_text()
    total = ""
    if page.locator(".total").count():
        total = page.locator(".total").inner_text()
    derm = page.locator(".alert-danger").count() > 0
    return {"heading": heading, "steps": steps, "total": total, "dermatologist": derm, "screenshot": str(path)}


def test_analyze_endpoint() -> dict:
    """POST a tiny generated PNG. Never print secrets. Do not save the image."""
    # 1x1 PNG
    png = base64_tiny_png()
    req = urllib.request.Request(
        BASE + "/api/analyze",
        data=json.dumps({"image_base64": png, "mime_type": "image/png"}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "ShelloSkinAnalyzerTest/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {
                "http": resp.status,
                "analysis_available": data.get("analysis_available"),
                "provider": data.get("provider"),
                "has_observations": bool(data.get("observations")),
                "message": data.get("message"),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        return {"http": exc.code, "error": body}
    except Exception as exc:
        return {"http": 0, "error": type(exc).__name__}


def test_analyze_status() -> dict:
    req = urllib.request.Request(BASE + "/api/analyze", method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def base64_tiny_png() -> str:
    # Minimal valid 1x1 PNG (transparent)
    return (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )


def take_v2_screenshots(page, width=430, height=900):
    """First screen (kits+privacy+language), Sinhala results, WhatsApp/add-all area."""
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    out = {}
    page.set_viewport_size({"width": width, "height": height})
    page.goto(BASE + "/", wait_until="networkidle")
    page.wait_for_selector("#btn-start-quiz")
    page.wait_for_selector("#ready-kits")
    page.wait_for_selector("#lang-toggle")
    path = SCREENSHOTS / "v2-first-screen.png"
    page.screenshot(path=str(path), full_page=True)
    out["first_screen"] = str(path)

    # Sinhala results: walk oily/breakouts quiz, then switch language on results
    page.locator("#btn-start-quiz").click()
    page.wait_for_selector("#opts")
    choose(page, "#opts", "oily")
    page.locator("#btn-next").click()
    page.wait_for_selector("#opts")
    choose(page, "#opts", "breakouts")
    page.locator("#btn-next").click()
    page.wait_for_selector("#opts")
    choose(page, "#opts", "face")
    page.locator("#btn-next").click()
    page.wait_for_selector("#opts-climate")
    choose(page, "#opts-climate", "mixed")
    choose(page, "#opts-outdoors", "sometimes")
    page.locator("#btn-next").click()
    page.wait_for_selector("#opts")
    choose(page, "#opts", "everyday")
    page.locator("#btn-next").click()
    page.wait_for_selector("#btn-next")
    page.locator("#btn-next").click()
    page.wait_for_selector(".routine-list, .alert-danger")
    page.locator('#lang-toggle button[data-lang="si"]').click()
    page.wait_for_selector("#bundle-actions")
    try:
        page.wait_for_function(
            """() => {
              const imgs = [...document.querySelectorAll('.product img')];
              return imgs.every(img => img.complete);
            }""",
            timeout=8000,
        )
    except Exception:
        pass
    time.sleep(0.3)
    path = SCREENSHOTS / "v2-sinhala-results.png"
    page.screenshot(path=str(path), full_page=True)
    out["sinhala_results"] = str(path)

    # Zoom on WhatsApp / add-all area (same results page)
    bundle = page.locator("#bundle-actions")
    path = SCREENSHOTS / "v2-whatsapp-add-all.png"
    if bundle.count():
        bundle.scroll_into_view_if_needed()
        time.sleep(0.2)
        bundle.screenshot(path=str(path))
    else:
        page.screenshot(path=str(path), full_page=True)
    out["whatsapp_add_all"] = str(path)
    return out


def main():
    catalog = json.loads((HERE / "catalog.json").read_text(encoding="utf-8"))
    proc = start_server_if_needed()
    started = proc is not None
    report = {
        "base": BASE,
        "catalog_counts": catalog.get("counts"),
        "budget_bands": [
            {"id": b["id"], "label": b["label"], "max_lkr": b.get("max_lkr"), "note": b.get("note")}
            for b in catalog.get("budget_bands") or []
        ],
        "gaps": catalog.get("gaps") or [],
        "personas": [],
        "links": None,
        "analyze": None,
    }
    try:
        status = test_analyze_status()
        analyze_post = test_analyze_endpoint()
        report["analyze"] = {"get": {
            "analysis_available": status.get("analysis_available"),
            "provider": status.get("provider"),
        }, "post": analyze_post}
        print("Analyze GET: available=", status.get("analysis_available"), "provider=", status.get("provider"))
        print("Analyze POST: http=", analyze_post.get("http"),
              "available=", analyze_post.get("analysis_available"),
              "provider=", analyze_post.get("provider"),
              "has_observations=", analyze_post.get("has_observations"),
              "err=", analyze_post.get("error"))

        print("Checking product permalinks…")
        links = check_permalinks(catalog)
        report["links"] = {"total": links["total"], "ok": links["ok"], "failed": links["failed"]}
        print(f"Permalinks: {links['ok']}/{links['total']} returned 200")
        if links["failed"]:
            for url, code in links["failed"].items():
                print(f"  FAIL {code} {url}")

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                channel="chrome",
                args=["--disable-dev-shm-usage"],
            )
            page = browser.new_page()
            for persona in PERSONAS:
                print(f"Running persona: {persona['label']}")
                result = run_persona(page, persona)
                report["personas"].append({"id": persona["id"], "label": persona["label"], **result})
                print(f"  {result['heading']}")
                for step in result["steps"]:
                    print(f"    - {step['label']}: {step['name']} ({step['meta']})")
                    print(f"      {step['reason']}")
                if result["total"]:
                    print(f"  {result['total']}")
                print(f"  screenshot: {result['screenshot']}")

            # v2 marketing / UX screenshots
            v2 = take_v2_screenshots(page)
            report["v2_screenshots"] = v2
            for name, path in v2.items():
                print(f"  v2 {name}: {path}")
            browser.close()
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    out = HERE / "tests" / "persona-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    if report["links"]["ok"] != report["links"]["total"]:
        sys.exit(2)
    if any(not p["steps"] and not p["dermatologist"] for p in report["personas"]):
        sys.exit(3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
