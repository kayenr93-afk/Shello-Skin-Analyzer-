#!/usr/bin/env python3
"""Update skin-analyzer prices and stock from the shared Shello price list.

Reads /workspace/shello/price-list.json (written by another bot). Does not
modify that file, and does not add new products (no images or categories in
the shared list). Out-of-stock included SKUs stay in the catalog but are
marked in_stock=false so the matcher will not recommend them.

Usage:
    python3 apply_shared_prices.py
    python3 apply_shared_prices.py --dry-run

Exit codes (same as build_catalog.py):
    0  unchanged; catalog files left in place
    1  error or failed sanity check; catalog files not written
    2  catalog written (or would be written, in --dry-run) and changed
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from build_catalog import COLOMBO, HERE, catalog_is_sane

DEFAULT_SHARED = Path("/workspace/shello/price-list.json")
STATUS_NAME = "last-shared-apply.json"
UPDATED_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
MIN_ITEMS = 40
MIN_INCLUDED_IN_STOCK = 40
LARGE_PRICE_RATIO = 0.5


def log(message: str) -> None:
    print(message, flush=True)


def err(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr, flush=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Apply the shared Shello price list to catalog.json / catalog.csv."
    )
    parser.add_argument(
        "--shared",
        type=Path,
        default=DEFAULT_SHARED,
        help="Path to price-list.json (default: /workspace/shello/price-list.json).",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=HERE / "catalog.json",
        help="Path to catalog.json.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=HERE / "catalog.csv",
        dest="csv_path",
        help="Path to catalog.csv.",
    )
    parser.add_argument(
        "--status",
        type=Path,
        default=HERE / "logs" / STATUS_NAME,
        help="Where to write the machine-readable run summary.",
    )
    parser.add_argument(
        "--dry-run",
        "--no-write",
        action="store_true",
        dest="dry_run",
        help="Report the diff but do not write catalog.json / catalog.csv.",
    )
    return parser.parse_args(argv)


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"{path} is missing") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} must be a JSON object")
    return data


def updated_is_today(updated: str, today=None) -> bool:
    if not updated or not str(updated).strip():
        return False
    match = UPDATED_DATE_RE.search(str(updated))
    if not match:
        return False
    day = datetime.strptime(match.group(1), "%Y-%m-%d").date()
    today = today or datetime.now(COLOMBO).date()
    return day == today


def variation_key(product_id, variation_id) -> tuple[int, int | None]:
    vid = variation_id
    if vid is None or vid == "":
        vid = None
    else:
        vid = int(vid)
    return (int(product_id), vid)


def included_key(item: dict) -> tuple[int, int | None]:
    parent_id = int(item["parent_id"] if item.get("parent_id") is not None else item["id"])
    sku_id = int(item["id"])
    if sku_id == parent_id:
        return (parent_id, None)
    return (parent_id, sku_id)


def index_price_list(items: list[dict]) -> dict[tuple[int, int | None], dict]:
    index: dict[tuple[int, int | None], dict] = {}
    for item in items:
        if item.get("product_id") is None:
            continue
        key = variation_key(item["product_id"], item.get("variation_id"))
        index[key] = item
    return index


def known_product_ids(catalog: dict) -> set[int]:
    ids: set[int] = set()
    for item in (catalog.get("included") or []) + (catalog.get("excluded") or []):
        for field in ("id", "parent_id"):
            value = item.get(field)
            if value is not None and value != "":
                ids.add(int(value))
    return ids


def as_price(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def large_price_change(old: float | None, new: float | None) -> bool:
    if old is None or new is None:
        return False
    if old == 0:
        return new != 0
    return abs(new - old) / abs(old) > LARGE_PRICE_RATIO


def write_catalog_files(catalog: dict, json_path: Path, csv_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
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
    csv_path.parent.mkdir(parents=True, exist_ok=True)
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


def stamp_sync_metadata(catalog: dict, shared_updated: str) -> None:
    now = datetime.now(COLOMBO)
    catalog["fetched_at"] = now.isoformat(timespec="seconds")
    notes = list(catalog.get("source_notes") or [])
    sync_line = (
        "Prices and stock synced from shared price-list.json "
        f"(updated: {shared_updated})."
    )
    notes = [n for n in notes if not str(n).startswith("Prices and stock synced from shared price-list.json")]
    notes.append(sync_line)
    catalog["source_notes"] = notes


def write_status(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def apply(catalog: dict, shared: dict) -> dict:
    items = shared.get("items") or []
    index = index_price_list(items)
    known = known_product_ids(catalog)

    new_product_ids = sorted({
        int(item["product_id"])
        for item in items
        if item.get("product_id") is not None and int(item["product_id"]) not in known
    })

    price_changes = []
    stock_changes = []
    large_price_flags = []
    unmatched_included = []

    for item in catalog.get("included") or []:
        try:
            key = included_key(item)
        except (TypeError, ValueError, KeyError):
            unmatched_included.append({
                "id": item.get("id"),
                "parent_id": item.get("parent_id"),
                "name": item.get("name"),
                "size": item.get("size") or "",
            })
            continue
        row = index.get(key)
        if not row:
            unmatched_included.append({
                "id": item.get("id"),
                "parent_id": item.get("parent_id"),
                "name": item.get("name"),
                "size": item.get("size") or "",
                "key": [key[0], key[1]],
            })
            continue

        old_price = as_price(item.get("price_lkr"))
        new_price = as_price(row.get("price_lkr"))
        old_stock = bool(item.get("in_stock"))
        new_stock = bool(row.get("in_stock"))

        if new_price is not None and old_price != new_price:
            change = {
                "id": item.get("id"),
                "parent_id": item.get("parent_id"),
                "name": item.get("name"),
                "size": item.get("size") or "",
                "from": old_price,
                "to": new_price,
            }
            price_changes.append(change)
            if large_price_change(old_price, new_price):
                large_price_flags.append(change)
            item["price_lkr"] = new_price

        if old_stock != new_stock:
            stock_changes.append({
                "id": item.get("id"),
                "parent_id": item.get("parent_id"),
                "name": item.get("name"),
                "size": item.get("size") or "",
                "from": old_stock,
                "to": new_stock,
            })
            item["in_stock"] = new_stock

    included_in_stock = sum(1 for item in (catalog.get("included") or []) if item.get("in_stock"))
    return {
        "new_product_ids": new_product_ids,
        "price_changes": price_changes,
        "stock_changes": stock_changes,
        "large_price_flags": large_price_flags,
        "unmatched_included": unmatched_included,
        "included_count": len(catalog.get("included") or []),
        "included_in_stock": included_in_stock,
        "changed": bool(price_changes or stock_changes),
    }


def print_report(result: dict, shared_updated: str, item_count: int) -> None:
    log(f"Shared price list updated: {shared_updated}")
    log(f"Shared items: {item_count}")
    log(f"Included SKUs: {result['included_count']} ({result['included_in_stock']} in stock)")

    if result["unmatched_included"]:
        log(f"WARNING: {len(result['unmatched_included'])} included SKU(s) not in the shared price list:")
        for row in result["unmatched_included"]:
            log(f"  - id={row.get('id')} parent_id={row.get('parent_id')} {row.get('name')} {row.get('size')}".rstrip())

    if result["price_changes"]:
        log(f"Price changes: {len(result['price_changes'])}")
        for row in result["price_changes"]:
            log(
                f"  - id={row['id']} {row['name']} {row['size']}: "
                f"Rs {row['from']} -> Rs {row['to']}".rstrip()
            )
    else:
        log("Price changes: 0")

    if result["large_price_flags"]:
        log(f"FLAG: {len(result['large_price_flags'])} price change(s) over 50%:")
        for row in result["large_price_flags"]:
            log(
                f"  - id={row['id']} {row['name']} {row['size']}: "
                f"Rs {row['from']} -> Rs {row['to']}".rstrip()
            )

    if result["stock_changes"]:
        log(f"Stock changes: {len(result['stock_changes'])}")
        for row in result["stock_changes"]:
            state = "in stock" if row["to"] else "out of stock (will not be recommended)"
            log(f"  - id={row['id']} {row['name']} {row['size']}: {row['from']} -> {row['to']} ({state})".rstrip())
    else:
        log("Stock changes: 0")

    if result["new_product_ids"]:
        ids = ",".join(str(pid) for pid in result["new_product_ids"])
        log(f"new products, need full rebuild: {ids}")
    else:
        log("new products, need full rebuild: none")

    # Machine-readable lines for the wrapper.
    log(f"NEW_PRODUCT_IDS={','.join(str(pid) for pid in result['new_product_ids'])}")
    log(f"RESULT={'changed' if result['changed'] else 'unchanged'}")


def main(argv=None) -> int:
    args = parse_args(argv)
    status = {
        "result": "error",
        "written": False,
        "dry_run": bool(args.dry_run),
        "new_product_ids": [],
        "price_changes": [],
        "stock_changes": [],
        "large_price_flags": [],
        "unmatched_included": [],
        "included_in_stock": 0,
        "error": None,
    }

    def finish(code: int) -> int:
        try:
            write_status(args.status, status)
        except Exception as exc:
            err(f"could not write status file {args.status}: {exc}")
            return 1
        return code

    try:
        shared = load_json(args.shared)
    except Exception as exc:
        err(str(exc))
        status["error"] = str(exc)
        return finish(1)

    updated = str(shared.get("updated") or "").strip()
    items = shared.get("items") or []
    if not updated_is_today(updated):
        msg = (
            f"shared price list is not from today Asia/Colombo "
            f"(updated={updated or 'missing'})"
        )
        err(msg)
        status["error"] = msg
        return finish(1)
    if len(items) < MIN_ITEMS:
        msg = f"shared price list has only {len(items)} items (need at least {MIN_ITEMS})"
        err(msg)
        status["error"] = msg
        return finish(1)

    try:
        catalog = load_json(args.catalog)
    except Exception as exc:
        err(str(exc))
        status["error"] = str(exc)
        return finish(1)

    result = apply(catalog, shared)
    status.update({
        "new_product_ids": result["new_product_ids"],
        "price_changes": result["price_changes"],
        "stock_changes": result["stock_changes"],
        "large_price_flags": result["large_price_flags"],
        "unmatched_included": result["unmatched_included"],
        "included_in_stock": result["included_in_stock"],
        "included_count": result["included_count"],
        "shared_updated": updated,
        "shared_items": len(items),
    })
    print_report(result, updated, len(items))

    if result["included_in_stock"] < MIN_INCLUDED_IN_STOCK:
        msg = (
            f"only {result['included_in_stock']} included in-stock SKUs "
            f"(need at least {MIN_INCLUDED_IN_STOCK})"
        )
        err(msg)
        status["error"] = msg
        return finish(1)

    sane, reason = catalog_is_sane(catalog)
    if not sane:
        msg = f"catalog failed sanity check: {reason}"
        err(msg)
        status["error"] = msg
        return finish(1)

    if not result["changed"]:
        log("Catalog prices and stock are unchanged; leaving the existing files in place.")
        status["result"] = "unchanged"
        return finish(0)

    stamp_sync_metadata(catalog, updated)
    if args.dry_run:
        log("No files written (--dry-run).")
        status["result"] = "changed"
        status["written"] = False
        return finish(2)

    try:
        write_catalog_files(catalog, args.catalog, args.csv_path)
    except Exception as exc:
        msg = f"failed to write catalog files: {exc}"
        err(msg)
        status["error"] = msg
        return finish(1)

    log(f"Wrote {args.catalog} and {args.csv_path}.")
    status["result"] = "changed"
    status["written"] = True
    return finish(2)


if __name__ == "__main__":
    raise SystemExit(main())
