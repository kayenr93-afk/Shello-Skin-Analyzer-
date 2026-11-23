#!/usr/bin/env bash
# Apply the shared daily Shello price list to the skin-analyzer catalog.
# Deploys production only when prices or stock changed and the catalog is sane.
# On Sunday, or when the price list has product_ids not yet in the catalog,
# runs a full shop rebuild via refresh_and_deploy.sh (new products need images).
#
# Usage (from any cwd):
#   /workspace/skin-analyzer/refresh_from_shared.sh
#   /workspace/skin-analyzer/refresh_from_shared.sh --dry-run
#
# Schedule after the 8:00 AM Asia/Colombo shared price-list refresh, e.g. 8:15.
# Exit codes: 0 unchanged, 2 changed (and deployed unless --dry-run), 1 error.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/logs"

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" || "${1:-}" == "--no-write" || "${DRY_RUN:-}" == "1" ]]; then
  DRY_RUN=1
fi

ts="$(TZ=Asia/Colombo date +%Y%m%d-%H%M%S)"
# Sunday is %u=7 in Asia/Colombo.
dow="$(TZ=Asia/Colombo date +%u)"
log="$ROOT/logs/shared-refresh-${ts}.log"
status="$ROOT/logs/last-shared-apply.json"
live_catalog="https://skin.shellocosmetics.com/catalog.json"

# Do not print environment values; deploy may have secrets in the env.
{
  echo "=== Shello shared catalog refresh ${ts} Asia/Colombo ==="
  echo "start $(TZ=Asia/Colombo date -R)"
  echo "dry_run=${DRY_RUN} weekday=${dow} (7=Sunday)"

  run_full_rebuild() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
      echo "Dry run: would run full refresh_and_deploy.sh; running build_catalog.py --no-write instead."
      python3 "$ROOT/build_catalog.py" --no-write
      local code=$?
      echo "build_catalog.py --no-write exit ${code}"
      if [[ "$code" -eq 1 ]]; then
        return 1
      fi
      # 0 unchanged, 2 would write a change. Keep those codes.
      return "$code"
    fi
    echo "Running full refresh_and_deploy.sh"
    bash "$ROOT/refresh_and_deploy.sh"
    local code=$?
    echo "refresh_and_deploy.sh exit ${code}"
    if [[ "$code" -ne 0 ]]; then
      return 1
    fi
    if grep -q "Catalog unchanged; skipping deploy." "$log" 2>/dev/null; then
      # The tee'd log includes this script's output; also check the latest rebuild log.
      return 0
    fi
    # refresh_and_deploy.sh exits 0 on a successful deploy too. Treat a deploy as changed (2)
    # when the rebuild said the catalog changed.
    if ls -1t "$ROOT/logs"/refresh-*.log >/dev/null 2>&1; then
      local latest
      latest="$(ls -1t "$ROOT/logs"/refresh-*.log | head -n 1)"
      if grep -q "Catalog unchanged; skipping deploy." "$latest"; then
        return 0
      fi
      if grep -q "Catalog changed and passed sanity; deploying production." "$latest"; then
        return 2
      fi
    fi
    return 0
  }

  verify_live() {
    python3 - "$status" "$live_catalog" <<'PY'
import json, sys, time, urllib.request

status_path, url = sys.argv[1], sys.argv[2]
status = json.loads(open(status_path, encoding="utf-8").read())
expected = {}
for row in status.get("price_changes") or []:
    expected.setdefault(int(row["id"]), {})["price_lkr"] = row["to"]
for row in status.get("stock_changes") or []:
    expected.setdefault(int(row["id"]), {})["in_stock"] = bool(row["to"])
if not expected:
    print("Nothing to verify on the live catalog.")
    raise SystemExit(0)

def load(live_url):
    req = urllib.request.Request(
        live_url,
        headers={"User-Agent": "ShelloSkinAnalyzer/1.0 catalog-verify", "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

last_error = "live catalog did not match"
for attempt in range(1, 7):
    bust = f"{url}?v={int(time.time())}-{attempt}"
    try:
        catalog = load(bust)
    except Exception as exc:
        last_error = f"could not fetch {url}: {exc}"
        print(f"verify attempt {attempt}/6 failed: {last_error}", flush=True)
        time.sleep(5)
        continue
    by_id = {int(item["id"]): item for item in (catalog.get("included") or [])}
    missing = []
    for sku_id, fields in expected.items():
        item = by_id.get(sku_id)
        if not item:
            # Out-of-stock SKUs remain in included with in_stock=false, so a
            # missing id means the live file has not picked up this deploy.
            missing.append(f"id {sku_id} not in live included list")
            continue
        if "price_lkr" in fields and float(item.get("price_lkr")) != float(fields["price_lkr"]):
            missing.append(
                f"id {sku_id} price live={item.get('price_lkr')} expected={fields['price_lkr']}"
            )
        if "in_stock" in fields and bool(item.get("in_stock")) != bool(fields["in_stock"]):
            missing.append(
                f"id {sku_id} in_stock live={item.get('in_stock')} expected={fields['in_stock']}"
            )
    if not missing:
        print(f"Live catalog verified ({len(expected)} changed SKU(s) match) at {bust}")
        raise SystemExit(0)
    last_error = "; ".join(missing)
    print(f"verify attempt {attempt}/6: {last_error}", flush=True)
    time.sleep(5)
print(f"ERROR: live catalog at {url} did not reflect the new prices/stock: {last_error}", file=sys.stderr)
raise SystemExit(1)
PY
  }

  if [[ "$dow" == "7" ]]; then
    echo "Sunday: running a full catalog rebuild instead of the shared price apply."
    run_full_rebuild
    code=$?
    echo "end $(TZ=Asia/Colombo date -R)"
    exit "$code"
  fi

  dry_flag=()
  if [[ "$DRY_RUN" -eq 1 ]]; then
    dry_flag=(--dry-run)
  fi

  python3 "$ROOT/apply_shared_prices.py" "${dry_flag[@]}" --status "$status"
  code=$?
  echo "apply_shared_prices.py exit ${code}"

  new_ids=""
  if [[ -f "$status" ]]; then
    new_ids="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1],encoding="utf-8")); print(",".join(str(i) for i in (d.get("new_product_ids") or [])))' "$status" 2>/dev/null || true)"
  fi

  if [[ "$code" -eq 1 ]]; then
    echo "Shared price apply failed. Not deploying."
    echo "end $(TZ=Asia/Colombo date -R)"
    exit 1
  fi

  if [[ -n "$new_ids" ]]; then
    echo "New product_ids in the shared list, need full rebuild: ${new_ids}"
    run_full_rebuild
    code=$?
    echo "end $(TZ=Asia/Colombo date -R)"
    exit "$code"
  fi

  if [[ "$code" -eq 0 ]]; then
    echo "Catalog unchanged; skipping deploy."
    echo "end $(TZ=Asia/Colombo date -R)"
    exit 0
  fi

  if [[ "$code" -ne 2 ]]; then
    echo "Unexpected apply exit ${code}. Not deploying."
    echo "end $(TZ=Asia/Colombo date -R)"
    exit 1
  fi

  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Dry run: catalog would change; skipping deploy."
    echo "end $(TZ=Asia/Colombo date -R)"
    exit 2
  fi

  echo "Catalog changed and passed sanity; deploying production."
  npx -y vercel@latest --prod --yes
  deploy_code=$?
  echo "vercel --prod exit ${deploy_code}"
  if [[ "$deploy_code" -ne 0 ]]; then
    echo "Deploy failed."
    echo "end $(TZ=Asia/Colombo date -R)"
    exit 1
  fi

  echo "Verifying ${live_catalog} reflects the catalog change."
  if ! verify_live; then
    echo "end $(TZ=Asia/Colombo date -R)"
    exit 1
  fi

  echo "end $(TZ=Asia/Colombo date -R)"
  exit 2
} 2>&1 | tee -a "$log"

exit "${PIPESTATUS[0]}"
