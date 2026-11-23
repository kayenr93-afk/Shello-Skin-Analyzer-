#!/usr/bin/env bash
# Rebuild the public catalog and deploy production only when it changed and is sane.
# Usage (from any cwd):
#   /workspace/skin-analyzer/refresh_and_deploy.sh
# Exit codes: 0 unchanged or deploy ok; 1 rebuild/sanity/deploy failure.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/logs"
ts="$(TZ=Asia/Colombo date +%Y%m%d-%H%M%S)"
log="$ROOT/logs/refresh-${ts}.log"

# Do not print environment values; this script may run with WC keys in the env.
{
  echo "=== Shello catalog refresh ${ts} Asia/Colombo ==="
  echo "start $(TZ=Asia/Colombo date -R)"
  python3 "$ROOT/build_catalog.py"
  code=$?
  echo "build_catalog.py exit ${code}"
  if [[ "$code" -eq 0 ]]; then
    echo "Catalog unchanged; skipping deploy."
    echo "end $(TZ=Asia/Colombo date -R)"
    exit 0
  fi
  if [[ "$code" -ne 2 ]]; then
    echo "Catalog rebuild failed or failed a sanity check (exit ${code}). Not deploying."
    echo "end $(TZ=Asia/Colombo date -R)"
    exit 1
  fi
  echo "Catalog changed and passed sanity; deploying production."
  npx -y vercel@latest --prod --yes
  deploy_code=$?
  echo "vercel --prod exit ${deploy_code}"
  echo "end $(TZ=Asia/Colombo date -R)"
  exit "$deploy_code"
} 2>&1 | tee -a "$log"

exit "${PIPESTATUS[0]}"
