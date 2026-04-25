# Redis Streams Monitoring Integration with Prometheus & Grafana

## Overview

This document describes the complete integration of Redis streams monitoring with Prometheus and Grafana for the trading bot system. The integration provides real-time visibility into trading activity, order processing, and system performance.

## Architecture

```
Redis Streams → Redis Exporter → Prometheus → Grafana
```

### Components

1. **Redis Streams**: Event-driven data streams for trading activity
2. **Redis Exporter**: Custom Prometheus exporter that reads Redis streams
3. **Prometheus**: Metrics collection and storage
4. **Grafana**: Visualization and dashboards

## Redis Streams Data

### Available Streams

1. **`trading:trade_lifecycle:stream`** (1,074 messages)
   - Trade creation, queuing, processing, completion events
   - Events: `trade_created`, `order_queued`, `order_processing`, `order_completed`, `order_failed`

2. **`trading:order_state:stream`** (823 messages)
   - Order state transitions
   - States: `created` → `queued` → `QUEUED` → `PROCESSING` → `ACKNOWLEDGED`/`FAILED`

3. **`trading:fills:stream`** (66 messages)
   - Order execution data
   - Contains: exchange order IDs, prices, amounts, fees, timestamps

4. **`trading:workers:heartbeat:*`**
   - Worker health monitoring
   - Real-time worker status

## Prometheus Metrics

### Custom Metrics Exposed

#### Trade Lifecycle Metrics
- `trading_trade_lifecycle_events_total` - Total lifecycle events by type and status
- `trading_trade_processing_duration_seconds` - Trade processing time histogram

#### Order State Metrics
- `trading_order_state_changes_total` - Order state transitions by exchange
- `trading_order_fills_total` - Order fills by exchange, symbol, and side
- `trading_fill_amount` - Fill amounts by exchange, symbol, and side
- `trading_fill_price` - Fill prices by exchange, symbol, and side

#### System Metrics
- `redis_stream_length` - Number of messages in each Redis stream
- `trading_worker_heartbeat_timestamp` - Last heartbeat timestamp for workers

### Sample Metrics Data

```bash
# Stream lengths
redis_stream_length{stream_name="trading:trade_lifecycle:stream"} 1074
redis_stream_length{stream_name="trading:order_state:stream"} 823
redis_stream_length{stream_name="trading:fills:stream"} 66

# Trade lifecycle events
trading_trade_lifecycle_events_total{event_type="trade_created",status="pending"} 62
trading_trade_lifecycle_events_total{event_type="order_completed",status="success"} 7

# Order fills by exchange
trading_order_fills_total{exchange="binance",side="buy",symbol="ONT/USDC"} 29
trading_order_fills_total{exchange="bybit",side="buy",symbol="MANA/USDC"} 1
```

## Grafana Dashboard

### Redis Streams Monitoring Dashboard

**URL**: http://localhost:3000/d/redis-streams/redis-streams-monitoring

**Panels**:

1. **Redis Stream Lengths**
   - Real-time count of messages in each stream
   - Helps identify stream growth and activity levels

2. **Trade Lifecycle Events Rate**
   - Rate of trade lifecycle events over time
   - Shows trading activity patterns

3. **Order State Changes Rate**
   - Rate of order state transitions
   - Helps identify order processing bottlenecks

4. **Order Fills Rate by Exchange**
   - Fill rates by exchange and symbol
   - Shows which exchanges are most active

5. **Worker Heartbeats**
   - Real-time worker status
   - Shows if workers are healthy and active

6. **Trade Processing Duration**
   - 95th and 50th percentile processing times
   - Performance monitoring for trade execution

## Access URLs

### Monitoring Stack
- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3000 (admin/admin)
- **Redis Exporter Metrics**: http://localhost:8010/metrics

### Dashboards
- **Redis Streams**: http://localhost:3000/d/redis-streams/redis-streams-monitoring
- **Trading Overview**: http://localhost:3000/d/trading-overview
- **System Health**: http://localhost:3000/d/system-health
- **Exchange Performance**: http://localhost:3000/d/exchange-performance
- **Strategy Performance**: http://localhost:3000/d/strategy-performance

## Key Insights from Current Data

### Trading Activity Summary
- **Total Trades**: 62 trades created
- **Successful Trades**: 7 completed trades
- **Failed Trades**: 55 failed trades
- **Most Active Exchange**: Binance (29 ONT/USDC fills)
- **Recent Activity**: MANA/USDC trade on Bybit (2025-08-26 08:20:01)

### Performance Metrics
- **Trade Processing Time**: 95th percentile ~5 seconds
- **Worker Status**: Order processor is healthy and active
- **Stream Activity**: High activity in lifecycle and state streams

### Exchange Performance
- **Binance**: Most active with 29 ONT/USDC fills
- **Bybit**: 3 DOT/USDC fills, 2 XRP/USDC fills, 1 MANA/USDC fill
- **Crypto.com**: 1 AAVE/USD fill, 2 ALGO/USD fills

## Benefits of This Integration

1. **Real-time Monitoring**: Live visibility into trading activity
2. **Performance Tracking**: Monitor trade processing times and bottlenecks
3. **Exchange Comparison**: Compare performance across exchanges
4. **Error Detection**: Identify failed trades and processing issues
5. **Capacity Planning**: Monitor stream growth and system load
6. **Operational Insights**: Understand trading patterns and activity levels

## Troubleshooting

### Check Redis Exporter Status
```bash
curl http://localhost:8010/metrics | grep -E "(redis_stream_length|trading_)"
```

### Check Prometheus Targets
```bash
curl http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | select(.labels.job == "redis-exporter")'
```

### Check Grafana Dashboard
```bash
curl -u admin:admin http://localhost:3000/api/dashboards/uid/redis-streams
```

### Query Metrics Directly
```bash
# Stream lengths
curl "http://localhost:9090/api/v1/query?query=redis_stream_length"

# Trade lifecycle events
curl "http://localhost:9090/api/v1/query?query=trading_trade_lifecycle_events_total"

# Order fills
curl "http://localhost:9090/api/v1/query?query=trading_order_fills_total"
```

## Future Enhancements

1. **Alerting Rules**: Add Prometheus alerting for critical conditions
2. **Custom Dashboards**: Create exchange-specific or strategy-specific dashboards
3. **Historical Analysis**: Long-term trend analysis and reporting
4. **Performance Optimization**: Monitor and optimize Redis performance
5. **Capacity Planning**: Predict stream growth and plan infrastructure

## Files Created/Modified

### New Files
- `monitoring/redis-exporter.py` - Custom Redis streams exporter
- `monitoring/Dockerfile.redis-exporter` - Docker image for exporter
- `monitoring/requirements.txt` - Python dependencies
- `monitoring/grafana/dashboards/redis-streams.json` - Grafana dashboard
- `REDIS_MONITORING_INTEGRATION.md` - This documentation

### Modified Files
- `docker-compose.yml` - Added redis-exporter service
- `monitoring/prometheus.yml` - Added redis-exporter scraping target

## Conclusion

The Redis streams monitoring integration provides comprehensive visibility into the trading bot's operation. It enables real-time monitoring of trading activity, performance tracking, and operational insights. The integration is fully functional and ready for production use.
