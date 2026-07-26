# Cloudflare Quick Tunnel for Trading Dashboards

Date: 2026-07-18  
Status: approved for implementation

## Goal

Expose the Botv0.3 web dashboards (portfolio + spot) on the public internet via a temporary Cloudflare Quick Tunnel (`*.trycloudflare.com`), without a custom domain and without access control.

## Non-goals

- Named Cloudflare tunnels / custom hostnames
- Cloudflare Access or HTTP Basic Auth
- Exposing Grafana, Prometheus, orchestrator, config, database, or exchange APIs
- Permanent stable public URL

## Design

1. Add a `cloudflared` Compose service that runs:
   `tunnel --no-autoupdate --url http://web-dashboard-service:8006`
2. Keep only `web-dashboard-service` behind the tunnel (covers `/`, `/portfolio-dashboard`, `/spot-dashboard` and their APIs).
3. Bind host publish of the web dashboard to `127.0.0.1:8006` so it is not reachable on LAN/WAN except via the tunnel (and local loopback).
4. Document/script reading the ephemeral `https://*.trycloudflare.com` URL from `cloudflared` logs after start.
5. URL may change when the `cloudflared` container is recreated.

## Security notes

- View-oriented deployment with no auth, as requested. Anyone with the URL can load PnL/dashboard data.
- Backend service ports remain localhost-bound.
- Prefer rotating the tunnel (recreate container) if the URL leaks.

## Verification

- `docker compose up -d cloudflared`
- Helper prints a `trycloudflare.com` HTTPS URL
- Opening that URL loads the portfolio/spot dashboards
