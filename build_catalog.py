#!/usr/bin/env python3
"""Rebuild the Shello skin-analyzer catalog from the public WooCommerce APIs.

Primary source: WooCommerce Store API
  https://shellocosmetics.com/wp-json/wc/store/v1/products?per_page=100&page=N
  Variation detail: https://shellocosmetics.com/wp-json/wc/store/v1/products/{id}

Fallback (read-only) if the Store API fails:
  WC_CONSUMER_KEY / WC_CONSUMER_SECRET against /wp-json/wc/v3/products

Size is taken only from the product name, attributes, or (as a last resort,
clearly labelled) the shipping weight field — never invented.

Usage:
    python3 build_catalog.py
    python3 build_catalog.py --upgrade-images
    python3 build_catalog.py --no-write

Exit codes:
    0  catalog written (or already current) and unchanged vs the previous file
    1  error, or the new catalog failed a sanity check and was not written
    2  catalog written and changed (price, stock, products, or images)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import ssl
import html
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

STORE_BASE = "https://shellocosmetics.com/wp-json/wc/store/v1/products"
V3_BASE = "https://shellocosmetics.com/wp-json/wc/v3/products"
USER_AGENT = "Mozilla/5.0 (compatible; ShelloSkinAnalyzer/1.0; +https://skin.shellocosmetics.com)"
COLOMBO = ZoneInfo("Asia/Colombo")
HERE = Path(__file__).resolve().parent

SIZE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(ml|mL|ML|g|kg|l|L|ltr|litre|liter)\b",
    re.I,
)
HTML_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")

# Categories / name tokens that are out of scope for a face/body skin routine.
EXCLUDE_CATEGORY_REASONS = {
    "bundle offer": "bundle/combo",
    "deodorant": "deodorant",
    "shampoo": "hair",
    "conditioner": "hair",
    "hair gel": "hair",
    "hair oil": "hair",
    "hand wash": "hand wash",
    "foot cream": "foot cream",
}

HAIR_NAME_TOKENS = (
    "shampoo",
    "conditioner",
    "hair gel",
    "hair oil",
    "hair treatment",
)

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_SSL = ssl.create_default_context()


def _headers(accept: str = "application/json") -> dict:
    return {"User-Agent": USER_AGENT, "Accept": accept}


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 408, 429, 500, 502, 503, 504):
        return True
    if isinstance(exc, urllib.error.URLError):
        return True
    text = str(exc).lower()
    if "unexpected eof" in text or "ssl" in text or "timed out" in text:
        return True
    return False


def http_get_json(url: str, timeout: int = 45, retries: int = 5):
    """GET JSON, returning (data, headers_dict). Retries transient failures, including 403/TLS."""
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=_headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as resp:
                raw = resp.read()
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if "json" not in ctype:
                    raise RuntimeError(
                        f"Non-JSON response from {url} ({ctype}, {len(raw)} bytes)"
                    )
                return json.loads(raw.decode("utf-8")), dict(resp.headers)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, RuntimeError) as exc:
            last_err = exc
            if not _is_retryable(exc):
                raise
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} attempts: {url} ({last_err})")


def v3_auth_qs() -> str:
    key = os.environ.get("WC_CONSUMER_KEY", "").strip()
    secret = os.environ.get("WC_CONSUMER_SECRET", "").strip()
    if not key or not secret:
        return ""
    return urllib.parse.urlencode({"consumer_key": key, "consumer_secret": secret})


# ---------------------------------------------------------------------------
# Product images (prefer a small WooCommerce size, verify, fallback)
# ---------------------------------------------------------------------------

_SIZE_IN_NAME = re.compile(r"-(\d+)x(\d+)(\.[a-zA-Z]+)$")


def sized_image_candidates(url: str) -> list[str]:
    """Build likely WooCommerce thumbnail URLs from a full-size upload URL."""
    if not url:
        return []
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path
    if not path:
        return [url]
    # Already a 300x300 derivative.
    if path.endswith(("-300x300.jpg", "-300x300.jpeg", "-300x300.png", "-300x300.webp")):
        return [url]
    stem, ext = path.rsplit(".", 1) if "." in path.rsplit("/", 1)[-1] else (path, "")
    if not ext:
        return [url]
    ext = "." + ext
    # Drop an existing -WxH suffix so we can request 300x300.
    stem_plain = re.sub(r"-\d+x\d+$", "", stem)
    candidates = []
    def add(pathname: str):
        built = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, pathname, "", ""))
        if built not in candidates:
            candidates.append(built)
    if stem.endswith("-scaled"):
        add(stem[: -len("-scaled")] + "-300x300" + ext)
    add(stem_plain + "-300x300" + ext)
    if url not in candidates:
        candidates.append(url)
    return candidates


def parse_srcset_300(srcset: str | None) -> str | None:
    if not srcset:
        return None
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        if bits[0].endswith("-300x300.jpg") or bits[0].endswith("-300x300.jpeg") or bits[0].endswith("-300x300.png") or bits[0].endswith("-300x300.webp") or (len(bits) > 1 and bits[1] == "300w"):
            if "-300x300." in bits[0]:
                return bits[0]
    return None


def http_image_ok(url: str, timeout: int = 20, retries: int = 3) -> bool:
    """Return True if url responds 200 with an image content type. HEAD, then GET."""
    last_err = None
    for attempt in range(retries):
        for method in ("HEAD", "GET"):
            req = urllib.request.Request(url, headers=_headers("image/*,*/*"), method=method)
            try:
                with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as resp:
                    if resp.status != 200:
                        last_err = f"HTTP {resp.status}"
                        continue
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    if ctype.startswith("image/") or not ctype:
                        if method == "GET":
                            # Don't download the whole original; close after headers+start.
                            resp.read(64)
                        return True
                    last_err = ctype
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                last_err = exc
                if isinstance(exc, urllib.error.HTTPError) and exc.code in (404, 400, 410):
                    return False
                if method == "HEAD" and isinstance(exc, urllib.error.HTTPError) and exc.code in (405, 501):
                    continue
                if not _is_retryable(exc) and not (isinstance(exc, urllib.error.HTTPError) and exc.code == 403):
                    return False
        time.sleep(0.8 * (attempt + 1))
    return False


def dimensions_from_url(url: str) -> tuple[int | None, int | None]:
    match = _SIZE_IN_NAME.search(urllib.parse.urlsplit(url).path)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


def resolve_product_image(url: str, srcset: str | None = None) -> tuple[str, int | None, int | None]:
    """Pick a verified 300x300 (or similar) derivative, else the original URL."""
    if not url:
        return "", None, None
    ordered: list[str] = []
    from_srcset = parse_srcset_300(srcset)
    if from_srcset:
        ordered.append(from_srcset)
    for cand in sized_image_candidates(url):
        if cand not in ordered:
            ordered.append(cand)
    for cand in ordered:
        if http_image_ok(cand):
            w, h = dimensions_from_url(cand)
            if w is None:
                w, h = (300, 300) if cand != url else (None, None)
            return cand, w, h
        time.sleep(0.15)
    w, h = dimensions_from_url(url)
    return url, w, h


def apply_sized_images(items: list[dict]) -> None:
    """Resolve image URLs in place, serially, with a short pause between checks."""
    cache: dict[str, tuple[str, int | None, int | None]] = {}
    unique_src = []
    for item in items:
        src = item.get("image") or ""
        if src and src not in cache and src not in unique_src:
            unique_src.append(src)
    print(f"Resolving {len(unique_src)} product image URL(s) to a small WooCommerce size…", flush=True)
    ok_small = 0
    for i, src in enumerate(unique_src, 1):
        resolved = resolve_product_image(src)
        cache[src] = resolved
        if resolved[0] and resolved[0] != src:
            ok_small += 1
        if i % 10 == 0 or i == len(unique_src):
            print(f"  images {i}/{len(unique_src)} ({ok_small} using a smaller size)", flush=True)
        time.sleep(0.2)
    for item in items:
        src = item.get("image") or ""
        if not src:
            item["image_width"] = None
            item["image_height"] = None
            continue
        item["image_original"] = src
        url, w, h = cache.get(src, (src, None, None))
        item["image"] = url
        item["image_width"] = w
        item["image_height"] = h
    print(f"Image resolve done: {ok_small}/{len(unique_src)} switched to a smaller size.", flush=True)


def sku_fingerprint(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "parent_id": item.get("parent_id"),
        "name": item.get("name"),
        "size": item.get("size"),
        "price_lkr": item.get("price_lkr"),
        "in_stock": bool(item.get("in_stock")),
        "permalink": item.get("permalink"),
        "product_type": item.get("product_type"),
        "image": item.get("image"),
    }


def diff_catalogs(old: dict | None, new: dict) -> dict:
    old_inc = {i["id"]: i for i in (old or {}).get("included") or []}
    new_inc = {i["id"]: i for i in (new or {}).get("included") or []}
    old_ids, new_ids = set(old_inc), set(new_inc)
    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)
    price = []
    stock = []
    other = []
    for iid in sorted(old_ids & new_ids):
        a, b = old_inc[iid], new_inc[iid]
        if a.get("price_lkr") != b.get("price_lkr"):
            price.append({"id": iid, "name": b.get("name"), "from": a.get("price_lkr"), "to": b.get("price_lkr")})
        if bool(a.get("in_stock")) != bool(b.get("in_stock")):
            stock.append({"id": iid, "name": b.get("name"), "from": bool(a.get("in_stock")), "to": bool(b.get("in_stock"))})
        fa, fb = sku_fingerprint(a), sku_fingerprint(b)
        skip = {"price_lkr", "in_stock"}
        changed = {k for k in fa if k not in skip and fa[k] != fb[k]}
        if changed:
            other.append({"id": iid, "name": b.get("name"), "fields": sorted(changed)})
    changed = bool(added or removed or price or stock or other) or (
        (old or {}).get("counts") != new.get("counts")
    )
    return {
        "changed": changed,
        "added": [{"id": i, "name": new_inc[i].get("name"), "price_lkr": new_inc[i].get("price_lkr")} for i in added],
        "removed": [{"id": i, "name": old_inc[i].get("name")} for i in removed],
        "price": price,
        "stock": stock,
        "other": other,
    }


def print_diff(diff: dict) -> None:
    print("Catalog diff:", flush=True)
    if not diff["changed"]:
        print("  no product/price/stock/image changes", flush=True)
        return
    print(f"  added: {len(diff['added'])}, removed: {len(diff['removed'])}, price: {len(diff['price'])}, stock: {len(diff['stock'])}, other: {len(diff['other'])}", flush=True)
    for row in diff["added"][:20]:
        print(f"    + {row['id']} {row['name']} Rs {row.get('price_lkr')}", flush=True)
    for row in diff["removed"][:20]:
        print(f"    - {row['id']} {row['name']}", flush=True)
    for row in diff["price"][:20]:
        print(f"    price {row['id']} {row['name']}: {row['from']} -> {row['to']}", flush=True)
    for row in diff["stock"][:20]:
        print(f"    stock {row['id']} {row['name']}: {row['from']} -> {row['to']}", flush=True)
    for row in diff["other"][:20]:
        print(f"    other {row['id']} {row['name']}: {', '.join(row['fields'])}", flush=True)


LEAK_TOKENS = (
    "hair",
    "shampoo",
    "conditioner",
    "deodorant",
    "anti-perspirant",
    "hand wash",
    "handwash",
    "foot cream",
    "bundle",
    "combo",
    "bundle offer",
)


def catalog_is_sane(catalog: dict) -> tuple[bool, str]:
    included = catalog.get("included") or []
    if len(included) < 40:
        return False, f"only {len(included)} included SKUs (need at least 40)"
    leaks = []
    for item in included:
        blob = " ".join(
            [item.get("name") or "", " ".join(item.get("categories") or []), item.get("permalink") or ""]
        ).lower()
        hits = [tok for tok in LEAK_TOKENS if tok in blob]
        # "hand cream" is allowed and should not match "hand wash"
        hits = [h for h in hits if not (h in ("hand wash", "handwash") and "hand cream" in (item.get("name") or "").lower())]
        if hits:
            leaks.append(f"{item.get('id')} {item.get('name')} ({', '.join(hits)})")
    if leaks:
        return False, "excluded category leaked into included: " + "; ".join(leaks[:8])
    return True, "ok"


def load_existing_catalog(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Text / price / size
# ---------------------------------------------------------------------------

def strip_html(text: str | None) -> str:
    if not text:
        return ""
    text = HTML_RE.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    return SPACE_RE.sub(" ", text).strip()


def store_price_lkr(prices: dict | None) -> float | None:
    if not prices:
        return None
    raw = prices.get("price")
    if raw in (None, ""):
        return None
    minor = int(prices.get("currency_minor_unit") or 2)
    return int(raw) / (10 ** minor)


def v3_price_lkr(value) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def parse_size_token(text: str | None) -> tuple[str | None, float | None, str | None]:
    """Return (label, amount, unit) from a name or attribute string."""
    if not text:
        return None, None, None
    cleaned = html.unescape(text)
    match = SIZE_RE.search(cleaned)
    if not match:
        return None, None, None
    amount = float(match.group(1))
    unit = match.group(2).lower()
    if unit in ("ltr", "litre", "liter"):
        unit = "l"
    if unit == "l":
        # Prefer ml when it's a whole litre like 1L
        if amount >= 1 and abs(amount * 1000 - round(amount * 1000)) < 1e-6:
            return f"{int(amount)}L" if amount == int(amount) else f"{amount}L", amount * 1000, "ml"
        return f"{amount}L", amount * 1000, "ml"
    if unit == "kg":
        label = f"{amount:g} kg"
        return label, amount * 1000, "g"
    pretty_unit = "ml" if unit == "ml" else "g"
    if amount == int(amount):
        label = f"{int(amount)}{pretty_unit}"
    else:
        label = f"{amount:g}{pretty_unit}"
    return label, amount, pretty_unit


def unique_size_from_attributes(attributes: list) -> tuple[str | None, float | None, str | None, list[str]]:
    """If attributes clearly name one size, return it. Always return option labels."""
    options = []
    chosen = None
    for attr in attributes or []:
        name = (attr.get("name") or attr.get("slug") or "").lower()
        # Store parent: terms[].name; store variation: attributes often empty
        # v3 variation: attributes[].option
        # v3 parent: attributes[].options
        values = []
        if attr.get("option"):
            values.append(str(attr["option"]))
        if attr.get("value") and attr["value"] not in values:
            values.append(str(attr["value"]))
        for term in attr.get("terms") or []:
            label = term.get("name") if isinstance(term, dict) else term
            if label and str(label) not in values:
                values.append(str(label))
        for opt in attr.get("options") or []:
            if opt and str(opt) not in values:
                values.append(str(opt))
        if "size" in name or name in {"sizes", "volume", "weight"}:
            options.extend(values)
            if len(values) == 1 and chosen is None:
                chosen = values[0]
        elif not options:
            # scent/variant attributes are not sizes; ignore
            pass
    if chosen:
        label, amount, unit = parse_size_token(chosen)
        return (label or chosen.strip(), amount, unit, options)
    # Several size options on a simple / parent product — not unique
    return None, None, None, options


def size_from_name(name: str) -> tuple[str | None, float | None, str | None]:
    return parse_size_token(name)


def shipping_weight_label(weight) -> tuple[str | None, float | None]:
    """Return a labelled shipping-weight string; never treat it as pack volume."""
    if weight in (None, "", "N/A"):
        return None, None
    try:
        kg = float(weight)
    except (TypeError, ValueError):
        text = str(weight).strip()
        return (f"{text} (shipping weight)" if text else None), None
    if kg <= 0:
        return None, None
    return f"{kg:g} kg (shipping weight)", kg


def extract_copy_sizes(text: str) -> list[str]:
    found = []
    seen = set()
    for match in SIZE_RE.finditer(text or ""):
        label, _, _ = parse_size_token(match.group(0))
        key = (label or match.group(0)).lower().replace(" ", "")
        if key not in seen:
            seen.add(key)
            found.append(label or match.group(0))
    return found


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def category_names(product: dict) -> list[str]:
    names = []
    for cat in product.get("categories") or []:
        if isinstance(cat, dict):
            name = cat.get("name") or cat.get("slug") or ""
        else:
            name = str(cat)
        name = html.unescape(name).strip()
        if name:
            names.append(name)
    return names


def exclusion_reason(name: str, categories: list[str]) -> str | None:
    lowered = name.lower()
    cats = [c.lower() for c in categories]
    for cat in cats:
        if cat in EXCLUDE_CATEGORY_REASONS:
            return EXCLUDE_CATEGORY_REASONS[cat]
    if "bundle" in lowered or "combo" in lowered:
        return "bundle/combo"
    if "deodorant" in lowered or "anti-perspirant" in lowered:
        return "deodorant"
    if "hand wash" in lowered or "handwash" in lowered:
        return "hand wash"
    if "foot cream" in lowered or "foot cream" in lowered:
        return "foot cream"
    if any(token in lowered for token in HAIR_NAME_TOKENS):
        return "hair"
    return None


def derive_product_type(name: str, categories: list[str], copy: str) -> str:
    n = name.lower()
    cats = [c.lower() for c in categories]
    blob = f"{n} {' '.join(cats)} {copy.lower()}"

    if "sun care" in n or "sun protection" in n or re.search(r"\bspf\b", n):
        return "sunscreen"
    if "glow gel" in cats or "glow gel" in n:
        return "serum/treat"
    if "face scrub" in cats or "face scrub" in n:
        return "scrub"
    if "cleansing milk" in cats or "cleansing milk" in n or "face wash" in cats or "face wash" in n:
        return "cleanser"
    if "shower gel" in cats or "shower gel" in n:
        return "shower gel"
    if "body wash" in cats or "body wash" in n:
        return "body wash"
    if "face cream" in cats or "face cream" in n:
        return "moisturiser"
    if "aloe vera gel" in cats or "moisturing gel" in cats or "moisturising gel" in n or "moisturizing gel" in n:
        return "moisturiser"
    if "hand cream" in cats or "hand cream" in n:
        return "hand cream"
    if "lotion" in cats or "lotion" in n:
        return "body lotion"
    if "mask" in n or "mask" in cats:
        return "mask"
    if "serum" in n:
        return "serum/treat"
    if "sunscreen" in n or "spf" in blob:
        return "sunscreen"
    return "other"


def derive_area(name: str, categories: list[str], product_type: str, copy: str) -> str:
    n = name.lower()
    cats = [c.lower() for c in categories]
    c = copy.lower()

    face_cat = any(
        cat in cats or cat in n
        for cat in ("face wash", "face cream", "face scrub", "cleansing milk")
    )
    body_cat = any(
        cat in cats or token in n
        for cat in ("body wash", "shower gel", "lotion", "hand cream", "foot cream")
        for token in (cat,)
    ) or product_type in {"body wash", "shower gel", "body lotion", "hand cream", "sunscreen"}

    both_phrase = bool(
        re.search(r"\bboth face and body\b", c)
        or re.search(r"\bface and body\b", c)
        or re.search(r"\bon face and body\b", c)
        or re.search(r"\bapply face and body\b", c)
    )
    face_neck = bool(re.search(r"\bface and neck\b", c))

    if both_phrase:
        return "both"
    if face_cat or product_type == "cleanser" or (product_type == "scrub" and "face" in n):
        return "face"
    if face_neck and not body_cat:
        return "face"
    if body_cat:
        return "body"
    if "face" in n and "body" not in n:
        return "face"
    if "body" in n and "face" not in n:
        return "body"
    return "both" if both_phrase else ("body" if body_cat else "face")


def copy_signals(copy: str) -> list[str]:
    """Index phrases that actually appear in the product page copy."""
    c = (copy or "").lower()
    rules = [
        ("oily", r"\boily\b|\bexcess oil\b|\bexcess sebum\b|\bunwanted shine\b|\bshine-free\b"),
        ("pimples", r"\bpimple|\bblemish|\bbreakout"),
        ("dry", r"\bdry skin\b|\bdryness\b|\bdry areas\b|\bextra-dry\b|\brepair dryness\b"),
        ("sensitive", r"\bsensitive skin\b|\bsensitive and\b|\bfragrance-free\b|\bcalms?\b|\bsoothe|\birritat"),
        ("dull", r"\bdullness\b|\bdull skin\b|\btired, dull\b|\brevive dull\b"),
        ("glow", r"\bglow\b|\bradiant\b|\bbrighten\b|\bluminous\b|\billuminat"),
        ("tan", r"\btan\b|\bde-tanning\b|\bdetanning\b|\bsun tan\b"),
        ("uneven_tone", r"\beven out (your )?skin tone\b|\bdark (spots|marks)\b|\buneven\b"),
        ("lightweight", r"\blightweight\b|\bnon-greasy\b|\bnon-sticky\b|\bnon-oily\b"),
        ("rich", r"\brich\b|\bdeep(ly)? hydrat|\bintense moisture\b"),
        ("soap_free", r"\bsoap[ -]?free\b"),
        ("sun", r"\bspf\b|\buv-?a\b|\buv-?b\b|\bsun protection\b|\bsunburn\b|\buv rays\b"),
        ("exfoliate", r"\bexfoliat|\bscrub particles\b|\bdead skin cells\b|\bunclog"),
        ("makeup", r"\bmakeup\b|\bmake-up\b"),
        ("normal_skin", r"\bnormal skin\b|\bnormal to dry\b"),
        ("all_skin_types", r"\ball skin types\b"),
        ("hands_and_body", r"\bhands and body\b"),
        ("face_and_body", r"\bface and body\b"),
        ("ayurvedic", r"\bayurvedic\b"),
        ("fragrance_free", r"\bfragrance-free\b"),
    ]
    found = []
    for label, pattern in rules:
        if re.search(pattern, c):
            found.append(label)
    return found


def first_sentences(text: str, n: int = 2) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(parts[:n]).strip()


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_store_products() -> tuple[list[dict], str]:
    products = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        url = f"{STORE_BASE}?per_page=100&page={page}"
        data, headers = http_get_json(url)
        if not isinstance(data, list):
            raise RuntimeError(f"Store API returned non-list payload on page {page}")
        products.extend(data)
        total_header = headers.get("X-WP-TotalPages") or headers.get("x-wp-totalpages")
        if total_header:
            total_pages = max(1, int(total_header))
        elif len(data) < 100:
            break
        page += 1
    return products, "store_api"


def fetch_v3_products() -> tuple[list[dict], str]:
    qs = v3_auth_qs()
    if not qs:
        raise RuntimeError(
            "Store API failed and WC_CONSUMER_KEY / WC_CONSUMER_SECRET are not set"
        )
    products = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        url = f"{V3_BASE}?per_page=100&page={page}&{qs}"
        data, headers = http_get_json(url)
        if not isinstance(data, list):
            raise RuntimeError(f"WC v3 returned non-list payload on page {page}")
        products.extend(data)
        total_header = headers.get("X-WP-TotalPages") or headers.get("x-wp-totalpages")
        if total_header:
            total_pages = max(1, int(total_header))
        elif len(data) < 100:
            break
        page += 1
    return products, "wc_v3_rest"


def fetch_store_variation(var_id: int) -> dict:
    data, _ = http_get_json(f"{STORE_BASE}/{int(var_id)}")
    return data


def fetch_v3_variations(parent_id: int) -> list[dict]:
    qs = v3_auth_qs()
    if not qs:
        raise RuntimeError("WC v3 credentials missing while fetching variations")
    url = f"{V3_BASE}/{int(parent_id)}/variations?per_page=100&{qs}"
    data, _ = http_get_json(url)
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected variations payload for {parent_id}")
    return data


def normalize_parent(product: dict, source: str) -> dict:
    name = html.unescape(product.get("name") or "").replace("&#038;", "&")
    short = strip_html(product.get("short_description"))
    long = strip_html(product.get("description"))
    cats = category_names(product)
    if source == "store_api":
        price = store_price_lkr(product.get("prices"))
        in_stock = bool(product.get("is_in_stock"))
        images = product.get("images") or []
        image = images[0].get("src") if images else ""
        variations = product.get("variations") or []
        weight = product.get("weight")
        sku = product.get("sku") or ""
        ptype = product.get("type") or "simple"
        permalink = product.get("permalink") or ""
        attrs = product.get("attributes") or []
    else:
        price = v3_price_lkr(product.get("price"))
        in_stock = (product.get("stock_status") == "instock")
        images = product.get("images") or []
        image = images[0].get("src") if images else ""
        variations = [{"id": vid} if not isinstance(vid, dict) else vid for vid in (product.get("variations") or [])]
        weight = product.get("weight")
        sku = product.get("sku") or ""
        ptype = product.get("type") or "simple"
        permalink = product.get("permalink") or ""
        attrs = product.get("attributes") or []

    return {
        "id": int(product["id"]),
        "name": name,
        "type": ptype,
        "sku": sku,
        "permalink": permalink,
        "short_description": short,
        "description": long,
        "page_copy": SPACE_RE.sub(" ", f"{short} {long}").strip(),
        "categories": cats,
        "price_lkr": price,
        "in_stock": in_stock,
        "image": image,
        "weight": weight,
        "attributes": attrs,
        "variations": variations,
        "raw_source": source,
    }


def normalize_variation(var: dict, parent: dict, source: str) -> dict:
    if source == "store_api":
        name = html.unescape(var.get("name") or parent["name"])
        variation_label = var.get("variation") or ""
        price = store_price_lkr(var.get("prices"))
        in_stock = bool(var.get("is_in_stock"))
        sku = var.get("sku") or ""
        permalink = var.get("permalink") or parent["permalink"]
        weight = var.get("weight")
        short = strip_html(var.get("short_description")) or parent["short_description"]
        long = strip_html(var.get("description")) or parent["description"]
        images = var.get("images") or []
        image = images[0].get("src") if images else parent["image"]
        attrs = var.get("attributes") or []
        # Store variation objects often put the size in `variation` ("Size: 35ml")
        attr_values = []
        if variation_label:
            attr_values.append(variation_label)
    else:
        name = html.unescape(var.get("name") or parent["name"])
        variation_label = ""
        price = v3_price_lkr(var.get("price"))
        in_stock = var.get("stock_status") == "instock"
        sku = var.get("sku") or ""
        permalink = var.get("permalink") or parent["permalink"]
        weight = var.get("weight")
        short = strip_html(var.get("description")) or parent["short_description"]
        long = parent["description"]
        image = ((var.get("image") or {}) or {}).get("src") or parent["image"]
        attrs = var.get("attributes") or []
        attr_values = []

    size_label, amount, unit, size_options = unique_size_from_attributes(attrs)
    if not size_label and variation_label:
        size_label, amount, unit = parse_size_token(variation_label)
        if not size_label and ":" in variation_label:
            size_label, amount, unit = parse_size_token(variation_label.split(":", 1)[1])
        # If the variation label is a scent / colour (e.g. "Sunset"), do not treat it as a size.
    if not size_label:
        size_label, amount, unit = size_from_name(name)
        if not size_label:
            size_label, amount, unit = size_from_name(parent["name"])
    if size_label and (unique_size_from_attributes(attrs)[0] or (variation_label and parse_size_token(variation_label)[0])):
        size_source = "attribute"
    elif size_label:
        size_source = "name"
    else:
        size_source = ""

    shipping_label, shipping_kg = shipping_weight_label(weight)
    # Never substitute shipping weight when a pack size is already ambiguous.
    if not size_label and shipping_label and len(size_options) <= 1:
        size_label = shipping_label
        size_source = "weight_field"
        amount, unit = None, None

    display_name = parent["name"]
    # Avoid "Saffron Glow Gel – Pump" style; keep parent name + size
    return {
        "id": int(var["id"]),
        "parent_id": parent["id"],
        "sku": sku,
        "name": display_name,
        "variation_label": variation_label or (size_label or ""),
        "size": size_label or "",
        "size_amount": amount,
        "size_unit": unit or "",
        "size_source": size_source or "",
        "size_options_published": size_options,
        "shipping_weight": shipping_label or "",
        "price_lkr": price,
        "in_stock": in_stock,
        "categories": parent["categories"],
        "permalink": permalink,
        "short_description": short,
        "description": long,
        "page_copy": SPACE_RE.sub(" ", f"{short} {long}").strip() or parent["page_copy"],
        "image": image,
        "raw_source": source,
    }


def normalize_simple_sku(parent: dict) -> dict:
    size_label, amount, unit, size_options = unique_size_from_attributes(parent["attributes"])
    size_source = "attribute" if size_label else ""
    if not size_label:
        size_label, amount, unit = size_from_name(parent["name"])
        if size_label:
            size_source = "name"
    shipping_label, _ = shipping_weight_label(parent["weight"])
    notes = ""
    if not size_label and len(size_options) > 1:
        notes = (
            "Attribute lists more than one size but this is a simple product "
            f"with a single price; pack size is not uniquely published "
            f"({', '.join(size_options)})."
        )
        size_source = ""
        shipping_label, _ = shipping_weight_label(parent["weight"])  # keep as shipping only
    elif not size_label and shipping_label:
        size_label = shipping_label
        size_source = "weight_field"

    return {
        "id": parent["id"],
        "parent_id": parent["id"],
        "sku": parent["sku"],
        "name": parent["name"],
        "variation_label": "",
        "size": size_label or "",
        "size_amount": amount,
        "size_unit": unit or "",
        "size_source": size_source,
        "size_options_published": size_options,
        "shipping_weight": shipping_label or "",
        "price_lkr": parent["price_lkr"],
        "in_stock": parent["in_stock"],
        "categories": parent["categories"],
        "permalink": parent["permalink"],
        "short_description": parent["short_description"],
        "description": parent["description"],
        "page_copy": parent["page_copy"],
        "image": parent["image"],
        "size_notes": notes,
        "raw_source": parent["raw_source"],
    }


# ---------------------------------------------------------------------------
# Budget bands + gaps
# ---------------------------------------------------------------------------

def cheapest_step(items: list[dict], types: set[str], area: str) -> dict | None:
    fitted = []
    for item in items:
        if item["product_type"] not in types:
            continue
        if item["area"] == "both" or item["area"] == area or area == "both":
            fitted.append(item)
    if not fitted:
        return None
    return min(fitted, key=lambda x: (x["price_lkr"] is None, x["price_lkr"] or 0))


def mid_step(items: list[dict], types: set[str], area: str) -> dict | None:
    fitted = [
        item
        for item in items
        if item["product_type"] in types
        and (item["area"] in {area, "both"} or area == "both")
    ]
    if not fitted:
        return None
    fitted = sorted(fitted, key=lambda x: x["price_lkr"] or 0)
    return fitted[len(fitted) // 2]


def derive_budget_bands(items: list[dict]) -> list[dict]:
    prices = sorted(i["price_lkr"] for i in items if i["price_lkr"] is not None)
    if not prices:
        return []

    face_cleanse = cheapest_step(items, {"cleanser"}, "face")
    face_treat = cheapest_step(items, {"serum/treat", "scrub", "sunscreen"}, "face")
    face_moist = cheapest_step(items, {"moisturiser"}, "face")
    body_cleanse = cheapest_step(items, {"body wash", "shower gel"}, "body")
    body_treat = cheapest_step(items, {"serum/treat", "sunscreen"}, "body")
    body_moist = cheapest_step(items, {"body lotion", "moisturiser", "hand cream"}, "body")

    def total(*parts):
        vals = [p["price_lkr"] for p in parts if p and p["price_lkr"] is not None]
        return sum(vals) if vals else None

    cheapest_face_3 = total(face_cleanse, face_treat, face_moist)
    cheapest_face_2 = total(face_cleanse, face_moist)
    cheapest_body_3 = total(body_cleanse, body_treat, body_moist)

    # Everyday: prefer mid-size / mid-price SKUs in each step
    everyday_face = total(
        mid_step(items, {"cleanser"}, "face"),
        mid_step(items, {"serum/treat", "scrub"}, "face"),
        mid_step(items, {"moisturiser"}, "face"),
    )
    everyday_body = total(
        mid_step(items, {"body wash", "shower gel"}, "body"),
        mid_step(items, {"serum/treat", "sunscreen"}, "body"),
        mid_step(items, {"body lotion", "moisturiser"}, "body"),
    )

    def round_up_50(value: float) -> int:
        return int(((value + 49) // 50) * 50)

    value_cap = None
    if cheapest_face_3:
        # Give a little headroom above the cheapest complete face routine
        value_cap = round_up_50(max(cheapest_face_3, cheapest_body_3 or 0) * 1.15)
    elif cheapest_face_2:
        value_cap = round_up_50(cheapest_face_2 * 1.2)

    everyday_cap = None
    candidates = [c for c in (everyday_face, everyday_body) if c]
    if candidates:
        everyday_cap = round_up_50(max(candidates) * 1.1)
    if value_cap and everyday_cap and everyday_cap <= value_cap:
        everyday_cap = round_up_50(value_cap + 500)

    max_price = max(prices)
    generous_note_cap = round_up_50(max_price * 3)

    def fmt(part):
        if not part:
            return None
        size = f" {part['size']}" if part.get("size") else ""
        return f"{part['name']}{size} (Rs {part['price_lkr']:.0f})"

    value_examples = [fmt(face_cleanse), fmt(face_treat), fmt(face_moist)]
    bands = [
        {
            "id": "value",
            "label": "Value",
            "max_lkr": value_cap,
            "min_lkr": 0,
            "note": (
                f"Smallest in-stock sizes that still complete a 2–3 step routine. "
                f"The lowest complete face routine (cleanse + treat/glow + moisturise) "
                f"is Rs {cheapest_face_3:.0f}" if cheapest_face_3 else
                "Smallest in-stock sizes for a 2-step cleanse + moisturise routine"
            )
            + (
                f": {', '.join(x for x in value_examples if x)}. "
                f"A two-step cleanse + moisturise starts at Rs {cheapest_face_2:.0f}."
                if cheapest_face_2 and cheapest_face_3
                else "."
            ),
        },
        {
            "id": "everyday",
            "label": "Everyday",
            "min_lkr": (value_cap or 0) + 1 if value_cap else 0,
            "max_lkr": everyday_cap,
            "note": (
                "Daily/personal sizes around the mid-price of each routine step. "
                + (
                    f"A typical mid-catalog face routine totals about Rs {everyday_face:.0f}."
                    if everyday_face
                    else ""
                )
                + (
                    f" A typical mid-catalog body routine totals about Rs {everyday_body:.0f}."
                    if everyday_body
                    else ""
                )
            ).strip(),
        },
        {
            "id": "generous",
            "label": "Generous",
            "min_lkr": (everyday_cap or 0) + 1 if everyday_cap else 0,
            "max_lkr": None,
            "note": (
                "Larger home or family sizes (for example 180ml treat gels, 200ml face washes, "
                f"400–500ml lotions and washes). Individual SKUs in this catalog go up to "
                f"Rs {max_price:.0f}; a three-step routine of larger sizes can approach "
                f"Rs {generous_note_cap}."
            ),
        },
    ]
    # Attach distribution context
    def percentile(ps, q):
        if not ps:
            return None
        idx = min(len(ps) - 1, max(0, int(round((q / 100) * (len(ps) - 1)))))
        return ps[idx]

    meta = {
        "sku_count": len(prices),
        "min_sku_lkr": prices[0],
        "median_sku_lkr": percentile(prices, 50),
        "p75_sku_lkr": percentile(prices, 75),
        "max_sku_lkr": prices[-1],
        "cheapest_face_3step_lkr": cheapest_face_3,
        "cheapest_face_2step_lkr": cheapest_face_2,
        "cheapest_body_3step_lkr": cheapest_body_3,
        "everyday_face_3step_lkr": everyday_face,
        "everyday_body_3step_lkr": everyday_body,
    }
    for band in bands:
        band["price_distribution"] = meta
    return bands


def detect_gaps(parents: list[dict], included: list[dict], excluded: list[dict]) -> list[dict]:
    gaps = []
    types_present = {i["product_type"] for i in included}
    areas_present = {i["area"] for i in included}

    needed_face = {
        "cleanser": "A face cleanser (wash or cleansing milk)",
        "serum/treat": "A treat/glow step (gel, serum, or similar)",
        "moisturiser": "A facial moisturiser",
        "sunscreen": "A dedicated sunscreen",
        "mask": "A face mask",
    }
    # sunscreen exists as body lotion; check face-specific
    has_face_sunscreen = any(
        i["product_type"] == "sunscreen" and i["area"] in {"face", "both"} for i in included
    )
    has_body_sunscreen = any(
        i["product_type"] == "sunscreen" and i["area"] in {"body", "both"} for i in included
    )
    if "cleanser" not in types_present:
        gaps.append({"kind": "missing_type", "detail": "No in-stock facial cleanser."})
    if "serum/treat" not in types_present:
        gaps.append({"kind": "missing_type", "detail": "No in-stock treat/glow product (serum, glow gel, or similar)."})
    if "moisturiser" not in types_present:
        gaps.append({"kind": "missing_type", "detail": "No in-stock facial moisturiser."})
    if "mask" not in types_present:
        gaps.append({"kind": "missing_type", "detail": "No face mask is listed in the in-stock skin catalog."})
    if not has_face_sunscreen:
        gaps.append({
            "kind": "missing_type",
            "detail": (
                "No face-specific sunscreen SKU. "
                + (
                    "Advance Sun Care Protection Lotion (SPF 20) is listed as a body lotion / sunscreen."
                    if has_body_sunscreen
                    else "No SPF product is in the included catalog."
                )
            ),
        })
    if "toner" not in types_present:
        gaps.append({"kind": "missing_type", "detail": "No toner is listed in the in-stock skin catalog."})

    # Size mismatches: copy vs published SKU sizes, grouped by parent
    by_parent: dict[int, list[dict]] = {}
    for item in included:
        by_parent.setdefault(item["parent_id"], []).append(item)

    parent_map = {p["id"]: p for p in parents}
    for pid, skus in by_parent.items():
        parent = parent_map.get(pid) or {}
        copy = parent.get("page_copy") or skus[0].get("page_copy") or ""
        copy_sizes = extract_copy_sizes(copy)
        sku_sizes = []
        for sku in skus:
            if sku.get("size") and "shipping weight" not in sku["size"]:
                sku_sizes.append(sku["size"])
            for opt in sku.get("size_options_published") or []:
                label, _, _ = parse_size_token(opt)
                if label:
                    sku_sizes.append(label)
        # Attribute options advertised on the parent but missing as sellable SKUs
        parent_opts = []
        for attr in parent.get("attributes") or []:
            name = (attr.get("name") or "").lower()
            if "size" not in name:
                continue
            for term in attr.get("terms") or []:
                label = term.get("name") if isinstance(term, dict) else term
                if label:
                    parent_opts.append(str(label))
            for opt in attr.get("options") or []:
                if opt:
                    parent_opts.append(str(opt))
        missing_opts = []
        sku_amt = set()
        for s in sku_sizes:
            m = SIZE_RE.search(s or "")
            if m:
                sku_amt.add(round(float(m.group(1)), 3))
        for opt in parent_opts:
            parsed = parse_size_token(opt)
            if parsed[1] is not None and round(parsed[1], 3) not in sku_amt and opt not in missing_opts:
                missing_opts.append(parsed[0] or opt)
        if missing_opts:
            gaps.append({
                "kind": "size_mismatch",
                "product": parent.get("name") or skus[0]["name"],
                "parent_id": pid,
                "detail": (
                    f"The product attributes list {', '.join(missing_opts)} "
                    f"but no matching in-stock SKU was published "
                    f"(sellable sizes: {', '.join(dict.fromkeys(sku_sizes)) or 'none'})."
                ),
            })

        def norm(s):
            return re.sub(r"\s+", "", (s or "").lower())
        sku_norm = {norm(s) for s in sku_sizes}
        copy_norm = {norm(s) for s in copy_sizes}
        # Compare ml/g loosely (50ml vs 50g is a unit mismatch)
        def amount_unit(label):
            m = SIZE_RE.search(label or "")
            if not m:
                return None
            amt = float(m.group(1))
            unit = m.group(2).lower()
            if unit in {"l", "ltr", "litre", "liter"}:
                return amt * 1000, "ml"
            if unit == "kg":
                return amt * 1000, "g"
            if unit == "ml":
                return amt, "ml"
            return amt, "g"

        copy_parsed = [amount_unit(s) for s in copy_sizes if amount_unit(s)]
        sku_parsed = [amount_unit(s) for s in sku_sizes if amount_unit(s)]
        copy_amounts = {(round(a, 3), u) for a, u in copy_parsed}
        sku_amounts = {(round(a, 3), u) for a, u in sku_parsed}
        extra_in_copy = [s for s in copy_sizes if amount_unit(s) and (round(amount_unit(s)[0], 3), amount_unit(s)[1]) not in sku_amounts]
        missing_in_copy = []
        # Only flag copy sizes that don't match any sku amount (regardless of unit first)
        sku_amt_only = {round(a, 3) for a, _ in sku_parsed}
        copy_amt_only = {round(a, 3) for a, _ in copy_parsed}
        extra_in_copy = []
        for s in copy_sizes:
            parsed = amount_unit(s)
            if parsed and round(parsed[0], 3) not in sku_amt_only:
                extra_in_copy.append(s)
        # Unit mismatches: same amount, different unit
        unit_mismatches = []
        for amt, unit in copy_amounts:
            for samt, sunit in sku_amounts:
                if amt == samt and unit != sunit:
                    unit_mismatches.append(f"{amt:g}{unit} in copy vs {samt:g}{sunit} on the SKU")

        if extra_in_copy:
            gaps.append({
                "kind": "size_mismatch",
                "product": parent.get("name") or skus[0]["name"],
                "parent_id": pid,
                "detail": (
                    f"Product copy mentions {', '.join(extra_in_copy)} "
                    f"but published in-stock SKUs are {', '.join(dict.fromkeys(sku_sizes)) or 'unspecified'}."
                ),
            })
        if unit_mismatches:
            gaps.append({
                "kind": "size_mismatch",
                "product": parent.get("name") or skus[0]["name"],
                "parent_id": pid,
                "detail": "Unit mismatch between copy and SKUs: " + "; ".join(unit_mismatches) + ".",
            })
        if any(s.get("size_notes") for s in skus):
            gaps.append({
                "kind": "size_mismatch",
                "product": parent.get("name") or skus[0]["name"],
                "parent_id": pid,
                "detail": next(s["size_notes"] for s in skus if s.get("size_notes")),
            })

        if not (parent.get("short_description") or skus[0].get("short_description")):
            gaps.append({
                "kind": "missing_description",
                "product": parent.get("name") or skus[0]["name"],
                "parent_id": pid,
                "detail": "Short description is empty.",
            })
        if not (parent.get("description") or "").strip():
            # only if short also thin?
            if len((parent.get("short_description") or "")) < 40:
                gaps.append({
                    "kind": "missing_description",
                    "product": parent.get("name") or skus[0]["name"],
                    "parent_id": pid,
                    "detail": "Long description is empty and the short description is very short.",
                })

        # Fairness face wash 500ml typo style: already covered by extra_in_copy

    # Hand cream copy only mentions one scent
    hand = parent_map.get(4088)
    if hand:
        copy = (hand.get("page_copy") or "").lower()
        for scent in ("sunset", "forest", "ocean"):
            if scent == "sunset":
                continue
            if scent not in copy:
                gaps.append({
                    "kind": "missing_description",
                    "product": hand["name"],
                    "parent_id": 4088,
                    "detail": (
                        f"The parent short/long description refers to Sunset Hand Cream & Body Cream "
                        f"and does not describe the {scent.title()} variant."
                    ),
                })
                break  # one gap covering missing variant copy is enough if we mention both
        missing_scents = [s for s in ("Forest", "Ocean") if s.lower() not in copy]
        if missing_scents and any(g.get("parent_id") == 4088 and g["kind"] == "missing_description" for g in gaps):
            # refine last one
            for g in gaps:
                if g.get("parent_id") == 4088 and g["kind"] == "missing_description" and "Sunset" in g["detail"]:
                    g["detail"] = (
                        "The parent product copy describes Sunset Hand Cream & Body Cream only; "
                        f"it does not describe the {', '.join(missing_scents)} scent variant(s)."
                    )

    return gaps


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def load_variation_details(parents: list[dict], store_ok: bool) -> dict[int, tuple[dict, str]]:
    """Map variation_id -> (payload, source)."""
    needed = []
    for parent in parents:
        if parent["type"] == "variable" or parent["variations"]:
            for var in parent["variations"]:
                vid = var["id"] if isinstance(var, dict) else var
                needed.append((parent, int(vid)))

    results: dict[int, tuple[dict, str]] = {}
    if not needed:
        return results

    def load_one(vid: int):
        if store_ok:
            try:
                return vid, fetch_store_variation(vid), "store_api"
            except Exception:
                pass
        # fallback v3 single? handled at parent level later
        raise RuntimeError(f"store variation {vid} failed")

    # Store API, one request at a time, with a short pause (avoids Imunify360 403s).
    pending = [vid for _, vid in needed]
    print(f"Fetching {len(pending)} variation records from the Store API…", flush=True)
    errors = []
    if store_ok:
        for i, vid in enumerate(pending, 1):
            try:
                results[vid] = (fetch_store_variation(vid), "store_api")
            except Exception as exc:
                errors.append((vid, exc))
            if i % 20 == 0:
                print(f"  store variations {i}/{len(pending)} ok={len(results)}", flush=True)
            time.sleep(0.25)

    missing_ids = [vid for vid in pending if vid not in results]
    if missing_ids:
        print(
            f"Store API missed {len(missing_ids)} variation(s); retrying those IDs…",
            flush=True,
        )
        time.sleep(1.5)
        for vid in list(missing_ids):
            try:
                results[vid] = (fetch_store_variation(vid), "store_api")
            except Exception as exc:
                print(f"  retry store variation {vid} failed: {type(exc).__name__}", flush=True)
            time.sleep(0.25)

    missing_ids = [vid for vid in pending if vid not in results]
    if missing_ids:
        print(
            f"Store API still missing {len(missing_ids)} variation(s); "
            f"trying WC v3 (read-only) by parent…",
            flush=True,
        )
        parents_needed = {}
        for parent, vid in needed:
            if vid in missing_ids:
                parents_needed.setdefault(parent["id"], parent)
        for parent in parents_needed.values():
            try:
                time.sleep(0.4)
                variations = fetch_v3_variations(parent["id"])
            except Exception as exc:
                print(f"  v3 variations failed for parent {parent['id']}: {exc}", flush=True)
                continue
            for var in variations:
                results[int(var["id"])] = (var, "wc_v3_rest")

    still_missing = [vid for vid in pending if vid not in results]
    if still_missing:
        print(f"Final Store API attempt for {len(still_missing)} variation(s)…", flush=True)
        time.sleep(2)
        for vid in list(still_missing):
            try:
                results[vid] = (fetch_store_variation(vid), "store_api")
                print(f"  recovered {vid}", flush=True)
            except Exception as exc:
                print(f"  still missing {vid}: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(0.4)

    still_missing = [vid for vid in pending if vid not in results]
    if still_missing:
        print(f"WARNING: could not load variations: {still_missing}", flush=True)
    return results


def build():
    source_notes = []
    store_ok = False
    try:
        raw_products, source = fetch_store_products()
        store_ok = True
        source_notes.append(f"Product list: Store API ({len(raw_products)} products).")
        print(f"Store API returned {len(raw_products)} products.", flush=True)
    except Exception as exc:
        print(f"Store API failed ({exc}); falling back to WC v3 REST…", flush=True)
        raw_products, source = fetch_v3_products()
        source_notes.append(f"Product list: WC v3 REST fallback ({len(raw_products)} products). Store API error: {exc}.")
        print(f"WC v3 returned {len(raw_products)} products.", flush=True)

    parents = [normalize_parent(p, source) for p in raw_products]

    excluded = []
    included_parents = []
    for parent in parents:
        reason = exclusion_reason(parent["name"], parent["categories"])
        if reason:
            excluded.append({
                "id": parent["id"],
                "name": parent["name"],
                "categories": parent["categories"],
                "permalink": parent["permalink"],
                "in_stock": parent["in_stock"],
                "reason": reason,
            })
            continue
        if not parent["in_stock"] and parent["type"] != "variable":
            excluded.append({
                "id": parent["id"],
                "name": parent["name"],
                "categories": parent["categories"],
                "permalink": parent["permalink"],
                "in_stock": False,
                "reason": "out of stock",
            })
            continue
        included_parents.append(parent)

    var_payloads = load_variation_details(included_parents, store_ok=store_ok)
    var_sources = {src for _, src in var_payloads.values()}
    if var_payloads:
        source_notes.append(
            "Variations: "
            + ", ".join(sorted(var_sources))
            + f" ({len(var_payloads)} records)."
        )

    included = []
    for parent in included_parents:
        var_ids = [
            (v["id"] if isinstance(v, dict) else v)
            for v in (parent["variations"] or [])
        ]
        if parent["type"] == "variable" or var_ids:
            any_in_stock = False
            for vid in var_ids:
                payload = var_payloads.get(int(vid))
                if not payload:
                    continue
                raw, src = payload
                sku = normalize_variation(raw, parent, src)
                if not sku["in_stock"]:
                    excluded.append({
                        "id": sku["id"],
                        "name": f"{sku['name']} {sku.get('size') or ''}".strip(),
                        "categories": sku["categories"],
                        "permalink": sku["permalink"],
                        "in_stock": False,
                        "reason": "out of stock",
                    })
                    continue
                any_in_stock = True
                included.append(sku)
            if not any_in_stock and var_ids:
                excluded.append({
                    "id": parent["id"],
                    "name": parent["name"],
                    "categories": parent["categories"],
                    "permalink": parent["permalink"],
                    "in_stock": False,
                    "reason": "out of stock",
                })
        else:
            included.append(normalize_simple_sku(parent))

    # Classify
    for item in included:
        copy = item["page_copy"]
        item["product_type"] = derive_product_type(item["name"], item["categories"], copy)
        item["area"] = derive_area(item["name"], item["categories"], item["product_type"], copy)
        item["copy_signals"] = copy_signals(copy)
        item["currency"] = "LKR"
        # reason helper: first two sentences of short description
        item["reason_source"] = first_sentences(item["short_description"] or item["description"], 2)

    apply_sized_images(included)

    # Sort stably
    included.sort(key=lambda x: (x["product_type"], x["area"], x["name"].lower(), x["price_lkr"] or 0, x["id"]))
    excluded.sort(key=lambda x: (x["reason"], x["name"].lower(), x["id"]))

    bands = derive_budget_bands(included)
    gaps = detect_gaps(parents, included, excluded)

    fetched_at = datetime.now(COLOMBO).isoformat(timespec="seconds")
    catalog = {
        "brand": "Shello Cosmetics",
        "store_url": "https://shellocosmetics.com",
        "currency": "LKR",
        "currency_prefix": "Rs",
        "fetched_at": fetched_at,
        "timezone": "Asia/Colombo",
        "source_notes": source_notes,
        "counts": {
            "store_products": len(parents),
            "included_skus": len(included),
            "excluded_products": len(excluded),
            "excluded_by_reason": {},
        },
        "budget_bands": bands,
        "included": included,
        "excluded": excluded,
        "gaps": gaps,
        "disclaimer": (
            "Recommendations built from this catalog are cosmetic suggestions only "
            "and are not a medical diagnosis or treatment."
        ),
    }
    reasons: dict[str, int] = {}
    for row in excluded:
        reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
    catalog["counts"]["excluded_by_reason"] = reasons
    type_counts: dict[str, int] = {}
    for row in included:
        type_counts[row["product_type"]] = type_counts.get(row["product_type"], 0) + 1
    catalog["counts"]["included_by_type"] = type_counts

    return catalog


def write_catalog(catalog: dict) -> None:
    json_path = HERE / "catalog.json"
    csv_path = HERE / "catalog.csv"
    json_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    fieldnames = [
        "id",
        "parent_id",
        "sku",
        "name",
        "size",
        "size_source",
        "price_lkr",
        "in_stock",
        "categories",
        "permalink",
        "short_description",
        "product_type",
        "area",
        "copy_signals",
        "image",
        "image_width",
        "image_height",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for item in catalog["included"]:
            writer.writerow({
                "id": item["id"],
                "parent_id": item["parent_id"],
                "sku": item.get("sku") or "",
                "name": item["name"],
                "size": item.get("size") or "",
                "size_source": item.get("size_source") or "",
                "price_lkr": f"{item['price_lkr']:.2f}" if item.get("price_lkr") is not None else "",
                "in_stock": "true" if item.get("in_stock") else "false",
                "categories": " | ".join(item.get("categories") or []),
                "permalink": item.get("permalink") or "",
                "short_description": item.get("short_description") or "",
                "product_type": item.get("product_type") or "",
                "area": item.get("area") or "",
                "copy_signals": " | ".join(item.get("copy_signals") or []),
                "image": item.get("image") or "",
                "image_width": item.get("image_width") or "",
                "image_height": item.get("image_height") or "",
            })

    type_counts = catalog["counts"].get("included_by_type") or {}
    reasons = catalog["counts"].get("excluded_by_reason") or {}
    print(f"Wrote {json_path} ({len(catalog['included'])} included SKUs, {len(catalog['excluded'])} excluded).")
    print(f"Wrote {csv_path}")
    print("Included by type:", type_counts)
    print("Excluded by reason:", reasons)
    print("Budget bands:")
    for band in catalog.get("budget_bands") or []:
        cap = f"Rs {band['max_lkr']}" if band.get("max_lkr") else "no upper cap"
        print(f"  - {band['label']}: {cap}")
        print(f"    {band['note']}")
    gaps = catalog.get("gaps") or []
    if gaps:
        print(f"Catalog gaps ({len(gaps)}):")
        for gap in gaps:
            who = f" [{gap['product']}]" if gap.get("product") else ""
            print(f"  - {gap['kind']}{who}: {gap['detail']}")


def upgrade_images_only() -> dict:
    existing = load_existing_catalog(HERE / "catalog.json")
    if not existing:
        raise RuntimeError("catalog.json is missing; run a full rebuild first.")
    apply_sized_images(existing.get("included") or [])
    return existing


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Rebuild the Shello skin-analyzer catalog.")
    parser.add_argument(
        "--upgrade-images",
        action="store_true",
        help="Only resolve smaller image URLs on the existing catalog.json (no store crawl).",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Report the diff but do not write catalog.json / catalog.csv.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    json_path = HERE / "catalog.json"
    previous = load_existing_catalog(json_path)
    try:
        catalog = upgrade_images_only() if args.upgrade_images else build()
    except Exception as exc:
        print(f"ERROR: catalog rebuild failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    sane, reason = catalog_is_sane(catalog)
    if not sane:
        print(f"ERROR: new catalog failed sanity check: {reason}", file=sys.stderr)
        raise SystemExit(1)

    diff = diff_catalogs(previous, catalog)
    print_diff(diff)

    if args.no_write:
        print("No files written (--no-write).")
        raise SystemExit(2 if diff["changed"] else 0)

    if not diff["changed"] and previous is not None and not args.upgrade_images:
        # Still refresh fetched_at? Keep previous file so daily deploys don't fire on timestamp-only rebuilds.
        print("Catalog is unchanged; leaving the existing catalog.json in place.")
        raise SystemExit(0)

    write_catalog(catalog)
    raise SystemExit(2 if diff["changed"] else 0)
