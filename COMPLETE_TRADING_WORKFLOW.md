# Complete Trading Workflow - Buy & Sell Signals

## 🔄 **OVERVIEW**

The trading system follows an event-driven architecture where each service handles specific responsibilities in the trading lifecycle.

---

## 🟢 **BUY SIGNAL WORKFLOW**

### **Phase 1: Signal Generation**
```
Strategy Service → Signal Analysis → Buy Decision
```

1. **Strategy Service** (`port 8004`) continuously analyzes market data
   - **Heikin Ashi Strategy**: Analyzes candlestick patterns
   - **Technical Indicators**: RSI, MACD, Moving Averages
   - **Market Conditions**: Volume, volatility, trend strength

2. **Signal Generation**:
   - Strategy generates BUY signal with confidence score (0-1)
   - Signal includes: `pair`, `exchange`, `confidence`, `strength`, `entry_reason`
   - Posted to: `/api/v1/signals/{exchange}/{symbol}`

### **Phase 2: Signal Validation & Risk Management**
```
Orchestrator Service → Risk Checks → Entry Decision
```

3. **Orchestrator Service** (`port 8005`) receives signals during trading cycles
   - **Balance Check**: Ensures sufficient funds available
   - **Position Limits**: Checks max trades per exchange (default: 2)
   - **Risk Management**: Verifies no existing position for same pair
   - **Momentum Filter**: Analyzes recent price action for confirmation

4. **Position Sizing**:
   - **Strategy-Specific**: Each strategy has percentage allocation (e.g., 10%)
   - **Minimum Order Size**: Enforced per exchange (e.g., $100 for Crypto.com)
   - **Balance Calculation**: Uses available balance × strategy percentage

### **Phase 3: Order Creation & Execution**
```
Order Mapping → Exchange Execution → Database Recording
```

5. **Order Mapping Creation**:
   - **Database Service** creates order mapping record
   - **Local Order ID**: UUID generated for internal tracking
   - **Client Order ID**: Exchange-specific identifier
   - **Status**: Initially `PENDING`

6. **Order Execution**:
   - **Exchange Service** (`port 8003`) places order on exchange
   - **Order Type**: Market order for immediate execution
   - **Response**: Exchange returns order ID and status

7. **Order Acknowledgment**:
   - **Database Service** updates order status to `ACKNOWLEDGED`
   - **Exchange Order ID** linked to local order
   - **Timestamp** recorded for tracking

### **Phase 4: Fill Detection & Trade Creation**
```
Fill Monitoring → Trade Record → Position Tracking
```

8. **Fill Detection**:
   - **Orchestrator** waits for order fill (timeout: 30-60 seconds)
   - **Exchange Service** monitors order status via API polling
   - **WebSocket** connections provide real-time updates

9. **Trade Record Creation**:
   ```json
   {
     "trade_id": "uuid",
     "pair": "1INCH/USD",
     "exchange": "cryptocom",
     "status": "OPEN",
     "position_size": 396.0,
     "entry_price": 0.25284,
     "entry_time": "2025-08-21T12:22:15Z",
     "entry_id": "exchange_order_id",
     "strategy": "heikin_ashi",
     "entry_reason": "Strategy signal with confidence 1.0"
   }
   ```

---

## 🔴 **SELL SIGNAL WORKFLOW**

### **Phase 1: Exit Monitoring**
```
Continuous Monitoring → Exit Conditions → Sell Decision
```

10. **Exit Cycle Processing**:
    - **Orchestrator** monitors all OPEN trades every cycle
    - **Price Updates**: Fetches current market prices
    - **PnL Calculation**: Compares current price vs entry price

11. **Exit Condition Evaluation**:
    - **Stop Loss**: Default -0.8% (configurable per strategy)
    - **Take Profit**: Default +5.0% (configurable per strategy)
    - **Trailing Stop**: Activates at +0.6% PnL, trails by 0.35%
    - **Profit Protection**: Moves stop loss to breakeven at +1.0% PnL

### **Phase 2: Exit Signal Generation**
```
Exit Trigger → Order Creation → Trade Closure
```

12. **Exit Decision Types**:

    **A. Stop Loss Exit**:
    ```
    Current PnL ≤ -0.8% → IMMEDIATE SELL ORDER
    ```

    **B. Take Profit Exit**:
    ```
    Current PnL ≥ +5.0% → IMMEDIATE SELL ORDER
    ```

    **C. Trailing Stop Exit**:
    ```
    Price drops below trailing trigger → IMMEDIATE SELL ORDER
    ```

### **Phase 3: Sell Order Execution**
```
Sell Order → Exchange Execution → Trade Completion
```

13. **Sell Order Creation**:
    - **Market Order**: For immediate execution
    - **Full Position Size**: Sells entire position
    - **Order Mapping**: Links sell order to original trade

14. **Exit Order Execution**:
    - **Exchange Service** places sell order
    - **Fill Detection**: Monitors sell order completion
    - **Exit Price Recording**: Captures actual fill price

### **Phase 4: Trade Completion**
```
PnL Calculation → Database Update → Position Closure
```

15. **Trade Finalization**:
    ```json
    {
      "status": "CLOSED",
      "exit_price": 0.25500,
      "exit_time": "2025-08-21T12:25:30Z",
      "exit_id": "sell_order_id",
      "realized_pnl": 8.54,
      "exit_reason": "Take profit at +5.2% PnL"
    }
    ```

16. **Position Cleanup**:
    - **Balance Update**: Available balance increases by sale proceeds
    - **Trade History**: Moved to historical records
    - **Performance Metrics**: Updated with trade results

---

## 🛠️ **SERVICE INTERACTION MAP**

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Strategy Service│───▶│Orchestrator Svc │───▶│ Exchange Service│
│   (Signals)     │    │ (Entry/Exit)    │    │   (Orders)      │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                              │                         │
                              ▼                         ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Database Service│◀───│  Order Sync Svc │◀───│  Web Dashboard  │
│  (Persistence)  │    │ (Reconciliation)│    │   (Frontend)    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

---

## 🔧 **CURRENT ISSUES IDENTIFIED**

### **Critical Gaps in Workflow**:

1. **❌ Fill Recording Failure**: Orders execute on exchange but fail to record in database
2. **❌ Events API Broken**: UUID validation preventing order sync corrections
3. **❌ 45 Stuck Orders**: Orders in PENDING state not progressing
4. **❌ 1,300 Sync Alerts**: Massive backlog of reconciliation issues

### **Impact on Trading**:
- ✅ **Buy Signals**: Working correctly
- ✅ **Order Execution**: Working on exchanges  
- ❌ **Fill Recording**: Broken - causing missing trade records
- ❌ **Sell Signals**: May miss positions due to missing trade records

---

## 🚨 **IMMEDIATE FIXES REQUIRED**

1. **Fix Database Events API UUID validation**
2. **Clear 45 stuck PENDING orders**
3. **Resolve 1,300 sync alerts backlog**
4. **Implement real-time fill detection**

This workflow analysis confirms your observation - **the system is fundamentally working but has critical gaps in the order-to-trade recording pipeline**.