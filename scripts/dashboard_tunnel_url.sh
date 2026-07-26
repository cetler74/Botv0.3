#!/usr/bin/env bash
# Print the current Cloudflare Quick Tunnel URL for the trading dashboards.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

if ! docker compose ps --status running cloudflared >/dev/null 2>&1; then
  echo "cloudflared is not running. Start with:" >&2
  echo "  docker compose up -d cloudflared" >&2
  exit 1
fi

# Quick Tunnels log a trycloudflare.com URL shortly after connect.
for _ in $(seq 1 30); do
  url="$(
    docker compose logs cloudflared 2>&1 \
      | grep -Eo 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' \
      | tail -n 1 || true
  )"
  if [[ -n "${url}" ]]; then
    echo "${url}"
    echo "${url}/portfolio-dashboard"
    echo "${url}/spot-dashboard"
    exit 0
  fi
  sleep 1
done

echo "No trycloudflare.com URL found yet. Check logs:" >&2
echo "  docker compose logs --tail 50 cloudflared" >&2
exit 1
