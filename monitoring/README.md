# Trading Bot Monitoring Setup

This directory contains the complete monitoring stack for the multi-exchange trading bot using Prometheus and Grafana.

## Architecture

- **Prometheus**: Metrics collection and alerting
- **Grafana**: Visualization and dashboards
- **Alert Rules**: Automated monitoring alerts for critical conditions

## Services and Ports

- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (admin/admin)

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
docker-compose up -d prometheus grafana
```

### 2. Access Grafana
1. Navigate to http://localhost:3000
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
- `orchestrator_active_pairs`: Number of active trading pairs
- `orchestrator_active_exchanges`: Number of active exchanges

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
- `strategy_active_strategies`: Number of active strategies

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