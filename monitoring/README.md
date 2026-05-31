# Trading Bot Monitoring Setup

This directory contains the complete monitoring stack for the multi-exchange trading bot using Prometheus and Grafana.

## Architecture

- **Prometheus**: Metrics collection and alerting
- **Grafana**: Visualization and dashboards
- **Alert Rules**: Automated monitoring alerts for critical conditions

## Services and Ports

- Prometheus: http://localhost:9090
- Grafana: http://localhost:3030 (admin / `GRAFANA_ADMIN_PASSWORD` or default admin)
- Alertmanager: http://localhost:9093
- Node exporter: http://localhost:9100/metrics
- cAdvisor: http://localhost:8088
- Postgres exporter: http://localhost:9187/metrics
- KPI exporter: http://localhost:8015/metrics

## Dashboards

### 1. Trading Overview Dashboard
**URL**: http://localhost:3000/d/trading-overview

**Key Metrics**:
- Total trades executed (24h)
- Current P&L and balance
- Trade rate per minute by exchange
- Trade distribution by exchange and status
- Active trading pairs and exchanges

**Use Cases**:
- Monitor overall trading performance
- Track P&L trends
- Identify most active exchanges and pairs

### 2. System Health Dashboard
**URL**: http://localhost:3000/d/system-health

**Key Metrics**:
- Service status (up/down) for all microservices
- HTTP response times (95th percentile)
- Request rates and error rates
- Database query performance
- Exchange API call rates and latencies

**Use Cases**:
- Monitor system availability
- Identify performance bottlenecks
- Track service-level error rates

### 3. Exchange Performance Dashboard
**URL**: http://localhost:3000/d/exchange-performance

**Key Metrics**:
- Exchange API latency and call rates
- API calls distribution by exchange
- Exchange-specific error rates
- Balance by exchange
- Trade rates and P&L by exchange

**Use Cases**:
- Compare exchange performance
- Monitor API health per exchange
- Track exchange-specific trading results

### 4. Strategy Performance Dashboard
**URL**: http://localhost:3000/d/strategy-performance

**Key Metrics**:
- Active strategies count
- Strategy analysis rates and success rates
- Signal generation by strategy and type
- Strategy analysis duration
- Performance comparison table

**Use Cases**:
- Monitor strategy effectiveness
- Compare strategy performance
- Track signal generation patterns

## Alert Rules

The system includes comprehensive alerting for:

### Critical Alerts
- **ServiceDown**: Any service is unavailable
- **LargeLoss**: Loss exceeds $1000 in 1 hour
- **LowBalance**: Balance below $100
- **NoActiveStrategies**: No strategies running

### Warning Alerts
- **HighErrorRate**: HTTP 5xx errors > 10%
- **HighLatency**: 95th percentile > 2s
- **NoTradesExecuted**: No trades for 30 minutes
- **ExchangeAPIErrors**: Exchange errors > 0.5/sec
- **DatabaseQueryErrors**: DB errors > 0.1/sec
- **StrategyAnalysisFailures**: Strategy failures > 0.2/sec

## Setup Instructions

### 1. Start Monitoring Stack
```bash
docker compose --profile monitoring up -d prometheus alertmanager grafana redis-exporter node-exporter cadvisor postgres-exporter kpi-exporter
```

### 2. Access Grafana
1. Navigate to http://localhost:3030
2. Login with admin/admin
3. Dashboards will be automatically provisioned

### 3. Configure Alerting (Optional)
To enable alert notifications:
1. Go to Grafana > Alerting > Notification channels
2. Add your preferred notification method (Slack, email, etc.)
3. Alerts will automatically use Prometheus alert rules

## Metrics Reference

### Orchestrator Service Metrics
- `orchestrator_trades_total`: Total trades by exchange, pair, side, status
- `orchestrator_trades_pnl`: P&L distribution histogram
- `orchestrator_balance_total`: Current balance by exchange
- `orchestrator_active_pairs{market_type="spot"|"perp"}`: Pairs in orchestrator rotation
- `orchestrator_active_exchanges{market_type="spot"|"perp"}`: Exchanges with pair selections
- `trading_spot_realized_pnl_usd` / `trading_spot_realized_pnl_24h_usd`: Spot PnL from database

### Database Service Metrics
- `database_queries_total`: Total queries by operation, table, status
- `database_query_duration_seconds`: Query duration histogram
- `database_connections_active`: Active database connections

### Exchange Service Metrics
- `exchange_api_calls_total`: API calls by exchange, endpoint, status
- `exchange_api_latency`: API call latency histogram
- `exchange_api_errors_total`: API errors by exchange, error_type

### Strategy Service Metrics
- `strategy_analyses_total`: Analyses by strategy, exchange, pair, result
- `strategy_analysis_duration_seconds`: Analysis duration histogram
- `strategy_signals_total`: Signals by strategy, signal_type
- `strategy_active_strategies{market_type="spot"|"perp"}`: Enabled strategy modules per market (spot vs Hyperliquid perps)

## Hyperliquid live pilot (rsi_stoch only)

Manual go-live checklist — does **not** auto-enable live orders:

1. Deploy Phase 1 (closed-bar contract + `validate_rsi_stoch_actionable` on fast/slow paths). Confirm logs show `[FastEntry] Skip HL … validator …` when rules fail.
2. Paper soak ≥48h; run `python3 scripts/report_hl_paper_pnl.py` and `python3 scripts/check_hl_live_readiness.py` (exit 0 required).
3. Set `trading.hyperliquid_perps.mode: live`, `allow_live_orders: true`, small `live_max_margin_per_trade` / `live_max_open_positions`, `live_strategy_allowlist: [rsi_stoch_reversal_5m]`. Set `HYPERLIQUID_PRIVATE_KEY` in `.env`.
4. `./scripts/apply_config.sh` — rebuilds baked-config images and restarts strategy + orchestrator.
5. Monitor first live fills vs `bar_close_time` / `entry_path` in `trading.perp_live_trades.metadata`.
6. Emergency: `live_kill_switch: true` then `apply_config.sh`.

Prometheus: `trading_hl_rsi_stoch_live_readiness` (1 = paper metrics meet `live_promotion` thresholds). HL user WebSocket fill reconciliation is a follow-up after the pilot.

## Troubleshooting

### Common Issues

1. **No metrics appearing**
   - Check if services are exposing `/metrics` endpoints
   - Verify Prometheus targets are accessible
   - Check network connectivity between containers

2. **Dashboards not loading**
   - Ensure Grafana provisioning volumes are mounted correctly
   - Check Grafana logs for configuration errors
   - Verify JSON dashboard syntax

3. **Alerts not firing**
   - Check Prometheus rule evaluation in the web UI
   - Verify alert rule syntax
   - Ensure metric names match actual metrics

### Debug Commands

```bash
# Check service metrics endpoints
curl http://localhost:8001/metrics  # Config service
curl http://localhost:8002/metrics  # Database service
curl http://localhost:8003/metrics  # Exchange service
curl http://localhost:8004/metrics  # Strategy service
curl http://localhost:8005/metrics  # Orchestrator service

# Check Prometheus targets
curl http://localhost:9090/api/v1/targets

# Check Prometheus rules
curl http://localhost:9090/api/v1/rules

# View container logs
docker logs trading-bot-prometheus
docker logs trading-bot-grafana
```

## Troubleshooting

### Grafana ERR_CONNECTION_REFUSED (local)
Monitoring uses profile `monitoring` and port **3030** (not 3000 or 8000):

```bash
docker compose --profile monitoring up -d prometheus grafana kpi-exporter
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3030/api/health   # expect 200
```

### DNS_PROBE_FINISHED_NXDOMAIN (Cloudflare / public URL)

This is **not a Grafana or Docker bug** — the browser cannot resolve the hostname in DNS.

Verified on this host (2026-05-30):
- `portugalexpatdirectory.com` — zone exists in Cloudflare (NS ok) but **no A record** on apex
- `bot.portugalexpatdirectory.com` — **NXDOMAIN** (no DNS record)
- `grafana.portugalexpatdirectory.com` — **NXDOMAIN** (no DNS record)

**Fix in Cloudflare Dashboard:**

1. **Websites** → `portugalexpatdirectory.com` → status **Active**
2. **Zero Trust** → **Networks** → **Tunnels** → your tunnel → **Public Hostname** → Add:
   - `bot` → `http://127.0.0.1:8006`
   - `grafana` → `http://127.0.0.1:3030`
3. Confirm DNS auto-created: **DNS** → Records → CNAME `bot` / `grafana` → `*.cfargotunnel.com` (proxied)
4. Run connector: `cloudflared tunnel run --token <from Zero Trust>`
5. Use **subdomains only** — do **not** open `https://portugalexpatdirectory.com` (apex has no record)

Check script: `./scripts/verify_tunnel_dashboard.sh`

**Quick test without DNS:** `cloudflared tunnel --url http://127.0.0.1:3030` (temporary `*.trycloudflare.com` URL)

Local access always works: http://127.0.0.1:3030 (Grafana), http://127.0.0.1:8006 (dashboard)

### Grafana via Cloudflare (502 / blank / redirect loop)
1. Tunnel must point to `http://127.0.0.1:3030` — see `config/cloudflared.example.yml`.
2. Do **not** use port 3000 (container internal) or 8000 (blocked in this project).
3. Set in `.env` when using a public subdomain:
   ```
   GRAFANA_ROOT_URL=https://grafana.yourdomain.com
   GRAFANA_DOMAIN=grafana.yourdomain.com
   ```
4. Rebuild Grafana after env change:
   ```bash
   docker compose --profile monitoring build grafana
   docker compose --profile monitoring up -d grafana
   ```

### Panels show "No data" after metric changes
Rebuild and restart services that export new metrics, then wait one scrape interval (15s):

```bash
docker compose build strategy-service orchestrator-service database-service
docker compose up -d strategy-service orchestrator-service database-service
docker compose --profile monitoring build grafana kpi-exporter
docker compose --profile monitoring up -d grafana kpi-exporter
```

Verify in Prometheus:
```bash
curl -sG 'http://127.0.0.1:9090/api/v1/query' \
  --data-urlencode 'query=strategy_active_strategies{market_type="spot"}'
curl -sG 'http://127.0.0.1:9090/api/v1/query' \
  --data-urlencode 'query=trading_spot_realized_pnl_usd'
```

Expected: spot strategies = 3, perp = 12, spot realized PnL = numeric value.

### Balance / trade-rate panels empty
`orchestrator_balance_total` and `orchestrator_trades_total` only appear after orchestrator refreshes balances or closes a trade. KPI panels using `trading_spot_*` from kpi-exporter are the database source of truth.

## Customization

### Adding New Dashboards
1. Create JSON dashboard file in `monitoring/grafana/dashboards/`
2. Restart Grafana or wait for auto-reload
3. Dashboard will appear in the "Trading Bot" folder

### Adding New Metrics
1. Add metric to relevant service using prometheus_client
2. Update dashboard queries to include new metric
3. Consider adding alerts for critical thresholds

### Modifying Alert Rules
1. Edit `monitoring/alert-rules.yml`
2. Restart Prometheus: `docker-compose restart prometheus`
3. Alerts will be automatically loaded

## Performance Considerations

- **Retention**: Prometheus retains data for 200h by default
- **Scrape Interval**: 15s intervals balance freshness vs. resource usage
- **Storage**: Monitor disk usage for Prometheus data
- **Query Performance**: Use recording rules for complex queries if needed

## Security

- Change default Grafana admin password in production
- Consider authentication/authorization for metrics endpoints
- Use HTTPS in production environments
- Restrict network access to monitoring ports