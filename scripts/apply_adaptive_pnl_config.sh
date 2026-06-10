#!/usr/bin/env bash
# Apply adaptive PnL config changes and prove they are live in Docker.
#
# Status language used by the webpage/API contract:
# - pending reload: config/config.yaml has changed but containers have not proven the values live.
# - applied live: config-service, orchestrator-service, and dashboard agree on loaded adaptive values.
# - apply failed: any rebuild, reload, restart, or verification step fails.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

status="pending reload"
echo "[apply_adaptive_pnl_config] status: ${status}"

fail() {
  status="apply failed"
  echo "[apply_adaptive_pnl_config] status: ${status}" >&2
  echo "[apply_adaptive_pnl_config] error: $*" >&2
  exit 1
}

echo "[apply_adaptive_pnl_config] running base Docker config apply workflow..."
./scripts/apply_config.sh || fail "base apply_config workflow failed"

echo "[apply_adaptive_pnl_config] waiting for restarted services..."
for service_port in "orchestrator-service 8005" "web-dashboard-service 8006"; do
  service="${service_port% *}"
  port="${service_port#* }"
  ready="false"
  for _ in $(seq 1 45); do
    if docker compose exec -T "${service}" sh -lc "curl -sf http://localhost:${port}/health >/dev/null"; then
      ready="true"
      break
    fi
    sleep 1
  done
  if [[ "${ready}" != "true" ]]; then
    fail "${service} did not become healthy after reload"
  fi
done

echo "[apply_adaptive_pnl_config] forcing adaptive PnL control reevaluation..."
docker compose exec -T orchestrator-service sh -lc \
  'curl -sf -X POST http://localhost:8005/api/v1/perps/adaptive-pnl-control/reevaluate >/dev/null' \
  || fail "adaptive PnL reevaluation failed"

echo "[apply_adaptive_pnl_config] verifying adaptive_pnl_control from live config-service..."
config_json="$(
  docker compose exec -T config-service sh -lc \
    'curl -sf http://localhost:8001/api/v1/config/trading'
)" || fail "could not read live config-service trading config"

CONFIG_JSON="${config_json}" python3 - <<'PY' || fail "config-service adaptive verification failed"
import json
import os
import sys

payload = json.loads(os.environ["CONFIG_JSON"])
adaptive = (payload.get("hyperliquid_perps") or {}).get("adaptive_pnl_control") or {}
required = [
    "enabled",
    "lookback_hours",
    "fetch_limit",
    "min_reduce_trades",
    "min_block_trades",
    "min_scale_trades",
    "probation_size_multiplier",
    "scale_up_multiplier",
]
missing = [key for key in required if key not in adaptive]
if missing:
    raise SystemExit(f"missing adaptive_pnl_control keys in config-service: {missing}")
print("[apply_adaptive_pnl_config] config-service adaptive_pnl_control:")
for key in required:
    print(f"  {key}: {adaptive.get(key)}")
PY

echo "[apply_adaptive_pnl_config] verifying orchestrator adaptive-control endpoint..."
orchestrator_json="$(
  docker compose exec -T orchestrator-service sh -lc \
    'curl -sf http://localhost:8005/api/v1/perps/adaptive-pnl-control'
)" || fail "could not read orchestrator adaptive-control endpoint"

ORCHESTRATOR_JSON="${orchestrator_json}" python3 - <<'PY' || fail "orchestrator adaptive verification failed"
import json
import os
import sys

payload = json.loads(os.environ["ORCHESTRATOR_JSON"])
control = payload.get("control") or {}
if "status" not in control:
    raise SystemExit("orchestrator adaptive control is missing status")
if not payload.get("configLoadedAt"):
    raise SystemExit("orchestrator adaptive control is missing configLoadedAt live proof")
if "adaptiveConfig" not in payload:
    raise SystemExit("orchestrator adaptive control is missing adaptiveConfig live proof")
print("[apply_adaptive_pnl_config] orchestrator adaptive control:")
print(f"  status: {control.get('status')}")
print(f"  historySource: {control.get('historySource')}")
print(f"  activeDecisionKeys: {control.get('activeDecisionKeys')}")
print(f"  configLoadedAt: {payload.get('configLoadedAt')}")
PY

echo "[apply_adaptive_pnl_config] verifying dashboard adaptiveControl payload..."
docker compose exec -T web-dashboard-service python - <<'PY' || fail "dashboard adaptive verification failed"
import json
import sys
import urllib.request

with urllib.request.urlopen("http://localhost:8006/api/v1/dashboard/portfolio-intelligence", timeout=30) as response:
    payload = json.load(response)
adaptive = payload.get("adaptiveControl") or {}
if "control" not in adaptive:
    raise SystemExit("dashboard payload is missing adaptiveControl.control")
print("[apply_adaptive_pnl_config] dashboard adaptiveControl:")
print(f"  status: {(adaptive.get('control') or {}).get('status')}")
print(f"  historySource: {(adaptive.get('control') or {}).get('historySource')}")
PY

status="applied live"
echo "[apply_adaptive_pnl_config] status: ${status}"
