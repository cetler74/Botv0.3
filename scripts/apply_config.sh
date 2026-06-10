#!/usr/bin/env bash
# Apply config/config.yaml changes to the running stack.
#
# Why: config.yaml is COPYed into 4 service images at build time (config-,
# exchange-, mcp-, web-dashboard-service Dockerfiles). Colima on this host
# cannot bind-mount the host path because /Volumes/OWC\ Volume/... is not
# exposed to the VM. So a plain `docker compose restart` is NOT enough --
# we must rebuild those four images, recreate their containers, and restart
# strategy-service + orchestrator-service so they re-fetch config over HTTP.
#
# Usage:   ./scripts/apply_config.sh
# Run from anywhere; auto-cds to repo root.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

BAKED_SERVICES=(config-service exchange-service mcp-service web-dashboard-service)
CACHED_CONSUMERS=(strategy-service orchestrator-service)

echo "[apply_config] rebuilding images that bake config.yaml..."
docker compose build "${BAKED_SERVICES[@]}"

echo "[apply_config] recreating those containers..."
docker compose up -d --no-deps "${BAKED_SERVICES[@]}"

echo "[apply_config] syncing config.yaml into shared config_data volume..."
docker compose cp config/config.yaml config-service:/app/config/config.yaml

echo "[apply_config] waiting for config-service to become healthy..."
for _ in $(seq 1 30); do
  if docker compose exec -T config-service curl -sf http://localhost:8001/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "[apply_config] reloading config-service in-memory cache..."
docker compose exec -T config-service curl -sf -X POST http://localhost:8001/api/v1/config/reload \
  || echo "[apply_config] warn: /reload returned non-zero (ok if endpoint not yet up)."

echo "[apply_config] restarting cached config consumers..."
docker compose restart "${CACHED_CONSUMERS[@]}"

echo "[apply_config] verifying macd_momentum params from live config-service..."
docker compose exec -T config-service sh -lc \
  'curl -sf http://localhost:8001/api/v1/config/all' \
  | python3 -c '
import json, sys
d = json.load(sys.stdin)
m = d.get("strategies", {}).get("macd_momentum", {}).get("parameters", {})
keys = [
    "ema_filter_period",
    "adx_threshold",
    "volume_multiplier",
    "min_confidence_score",
    "skip_sideways_regime",
    "macd_green_rsi_force_buy_override",
    "macd_green_rsi_buy_threshold",
    "macd_green_rsi_lookback",
    "rsi_buy_max",
]
for k in keys:
    print(f"  {k}: {m.get(k)}")
'

echo "[apply_config] verifying adaptive_pnl_control from live config-service..."
docker compose exec -T config-service sh -lc \
  'curl -sf http://localhost:8001/api/v1/config/trading' \
  | python3 -c '
import json, sys
d = json.load(sys.stdin)
a = d.get("hyperliquid_perps", {}).get("adaptive_pnl_control", {})
keys = [
    "enabled",
    "lookback_hours",
    "fetch_limit",
    "min_reduce_trades",
    "min_block_trades",
    "min_scale_trades",
    "probation_size_multiplier",
    "scale_up_multiplier",
    "min_profit_factor_for_scale",
    "min_net_edge_for_scale",
    "min_gross_edge_for_scale",
    "max_fee_drag_for_scale",
]
for k in keys:
    print(f"  {k}: {a.get(k)}")
if a.get("enabled") is not True:
    raise SystemExit("adaptive_pnl_control.enabled is not true in live config-service")
'

echo "[apply_config] done. Tail the strategy log to confirm new values are applied:"
echo "  docker compose logs --since 30s strategy-service | grep -E 'lookback=|threshold=|applied overrides'"
