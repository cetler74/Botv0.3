#!/usr/bin/env bash
# Verify local dashboard is up before exposing via Cloudflare Tunnel.
set -euo pipefail

ORIGIN="${TUNNEL_ORIGIN:-http://127.0.0.1:8006}"
PUBLIC_HOST="${PUBLIC_HOST:-bot.portugalexpatdirectory.com}"

echo "Checking dashboard at ${ORIGIN} ..."
if ! curl -fsS --max-time 5 "${ORIGIN}/health" >/dev/null; then
  echo "FAIL: nothing healthy on ${ORIGIN}/health"
  echo "Start the stack: docker compose up -d web-dashboard-service"
  exit 1
fi

title=$(curl -fsS --max-time 5 "${ORIGIN}/" | grep -o '<title>[^<]*</title>' | head -1 || true)
echo "OK: ${ORIGIN}/health"
echo "Page: ${title:-(no <title> found)}"
echo ""
echo "Cloudflare setup (domain: portugalexpatdirectory.com):"
echo "  1. Cloudflare Dashboard → Websites → add portugalexpatdirectory.com (Active nameservers)."
echo "  2. Zero Trust → Networks → Tunnels → your tunnel → Public hostname:"
echo "       Subdomain: bot   Domain: portugalexpatdirectory.com   URL: http://localhost:8006"
echo "     Remove any hostname on *.workers.dev (that shows Hello World from Workers)."
echo "  3. DNS: confirm CNAME bot → <tunnel-id>.cfargotunnel.com (proxied, orange cloud)."
echo "  4. On this Mac: cloudflared tunnel run --token <from Zero Trust>"
echo "  5. Browser: https://${PUBLIC_HOST}/"
echo ""
echo "Security: enable Cloudflare Access on ${PUBLIC_HOST} — the dashboard has no login by default."
