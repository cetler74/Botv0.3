# Strategy Monitoring Guide

This guide shows you how to check if strategies are running for all exchanges in your trading bot.

## 📊 Current Status Summary

Based on the latest tests, here's the current status:

### ✅ Working Components
- **All 3 Exchanges**: Binance, Bybit, and Crypto.com are healthy and providing data
- **Strategy Service**: Running and processing signals for all exchanges
- **Exchange Service**: Successfully retrieving OHLCV data from all exchanges
- **Orchestrator Service**: Running and ready to execute trading cycles

### 🔍 Strategy Execution Status
- **Binance**: Strategies analyzing BTCUSDC (0% agreement, 0 participating strategies)
- **Bybit**: Strategies analyzing BTCUSDC (0% agreement, 0 participating strategies)  
- **Crypto.com**: Strategies analyzing BTCUSD (0% agreement, 0 participating strategies)

### 📈 Trading Status
- **Status**: Currently stopped (ready to start)
- **Cycles Completed**: 0
- **Last Cycle**: 2025-07-19T07:23:01.240833

---

## 🛠️ Monitoring Tools

### 1. Comprehensive Strategy Check
```bash
python3 check_strategies.py
```
**What it shows:**
- Exchange health status
- Strategy service status
- Strategy signals for all exchanges
- Trading activity status
- Active trades count
- Summary statistics

### 2. Real-Time Monitoring
```bash
# Monitor with 30-second intervals (default)
python3 monitor_strategies_realtime.py

# Monitor with custom intervals
python3 monitor_strategies_realtime.py --interval 10
```
**What it shows:**
- Live trading status updates
- Real-time exchange health
- Current strategy signals
- New trading cycle detection
- Continuous monitoring with timestamps

### 3. Trading Control Tool
```bash
# Check trading status
python3 trading_control.py status

# Check strategy execution
python3 trading_control.py strategies

# Check exchange health
python3 trading_control.py exchanges

# Start trading
python3 trading_control.py start

# Stop trading
python3 trading_control.py stop

# View service logs
python3 trading_control.py logs --service orchestrator
python3 trading_control.py logs --service strategy
python3 trading_control.py logs --service exchange
```

---

## 🔍 Manual Verification Methods

### 1. Check Exchange Data Retrieval
```bash
# Test OHLCV data for each exchange
curl "http://localhost:8003/api/v1/market/ohlcv/binance/BTCUSDC?timeframe=1h&limit=5"
curl "http://localhost:8003/api/v1/market/ohlcv/bybit/BTCUSDC?timeframe=1h&limit=5"
curl "http://localhost:8003/api/v1/market/ohlcv/cryptocom/BTCUSD?timeframe=1h&limit=5"
```

### 2. Check Strategy Signals
```bash
# Test strategy consensus for each exchange
curl "http://localhost:8004/api/v1/signals/consensus/binance/BTCUSDC"
curl "http://localhost:8004/api/v1/signals/consensus/bybit/BTCUSDC"
curl "http://localhost:8004/api/v1/signals/consensus/cryptocom/BTCUSD"
```

### 3. Check Trading Status
```bash
# Check if trading is running
curl "http://localhost:8005/api/v1/trading/status"
```

### 4. View Service Logs
```bash
# Orchestrator logs (trading cycles)
docker logs trading-bot-orchestrator --tail 20

# Strategy service logs (signal generation)
docker logs trading-bot-strategy --tail 20

# Exchange service logs (data retrieval)
docker logs trading-bot-exchange --tail 20
```

---

## 📋 What to Look For

### ✅ Signs That Strategies Are Working

1. **Exchange Health**: All exchanges should show "healthy" status
2. **Strategy Signals**: Should return consensus signals (buy/sell/hold)
3. **Data Flow**: Logs should show OHLCV data being retrieved
4. **Trading Cycles**: Orchestrator should complete trading cycles
5. **Signal Generation**: Strategy service should process market data

### ❌ Signs of Issues

1. **Exchange Errors**: High error counts or degraded status
2. **No Strategy Signals**: 404 errors or empty responses
3. **Data Retrieval Failures**: OHLCV endpoints returning errors
4. **Service Crashes**: Containers not running or unhealthy
5. **No Trading Activity**: Zero cycles completed

---

## 🚀 Starting Strategy Execution

To start the strategies and begin trading:

```bash
# Start trading (this will begin strategy execution cycles)
python3 trading_control.py start

# Monitor in real-time
python3 monitor_strategies_realtime.py --interval 10

# Check status
python3 trading_control.py status
```

---

## 📊 Expected Output Examples

### Healthy Strategy Execution
```
🧠 Strategy Signals:
   🟢 BINANCE: BUY (75.0% agreement, 3 strategies)
   🟡 BYBIT: HOLD (45.0% agreement, 2 strategies)
   🔴 CRYPTOCOM: SELL (80.0% agreement, 4 strategies)
```

### Active Trading
```
📊 Trading Status:
   Status: 🟢 RUNNING
   Cycles Completed: 15
   Last Cycle: 2025-07-19T07:25:30.123456
```

### Exchange Health
```
📈 Exchange Health:
   🟢 BINANCE: healthy (0.291s, 0 errors)
   🟢 BYBIT: healthy (0.951s, 0 errors)
   🟢 CRYPTOCOM: healthy (0.454s, 0 errors)
```

---

## 🔧 Troubleshooting

### If Strategies Show 0% Agreement
- Check if market data is being retrieved correctly
- Verify strategy service is processing data
- Check for errors in strategy service logs

### If Trading Cycles Don't Complete
- Ensure orchestrator service is healthy
- Check for errors in orchestrator logs
- Verify all required services are running

### If Exchange Data Fails
- Check exchange API connectivity
- Verify API keys are configured correctly
- Check exchange service logs for errors

---

## 📞 Quick Commands Reference

```bash
# Quick status check
python3 trading_control.py status && python3 trading_control.py strategies

# Start monitoring
python3 monitor_strategies_realtime.py

# Comprehensive check
python3 check_strategies.py

# View recent activity
python3 trading_control.py logs --service strategy --lines 30
```

This monitoring setup gives you complete visibility into strategy execution across all exchanges! 