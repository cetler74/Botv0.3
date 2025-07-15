# Microservice Architecture Design
## Multi-Exchange Trading Bot

### Overview
This document outlines the microservice architecture for the multi-exchange perpetual futures trading bot, designed to support Crypto.com, Binance, and Bybit with centralized configuration, real-time monitoring, and simulation/live trading modes.

### Architecture Principles
- **Service Independence**: Each service can be developed, deployed, and scaled independently
- **API-First Design**: All inter-service communication via REST APIs and WebSockets
- **Event-Driven**: Real-time updates via WebSocket connections
- **Container-Ready**: Each service is containerized with Docker
- **Fault Tolerance**: Services handle failures gracefully with retry mechanisms
- **Observability**: Comprehensive logging, monitoring, and health checks

---

## Service Boundaries

### 1. Configuration Service (`config-service`)
**Port**: 8001  
**Purpose**: Centralized configuration management and validation

#### Responsibilities:
- Load and validate configuration from YAML files
- Provide configuration endpoints for all services
- Handle configuration updates and hot-reloading
- Manage configuration versioning and rollbacks
- Validate exchange credentials and settings

#### API Endpoints:
```
GET    /api/v1/config/database
GET    /api/v1/config/exchanges
GET    /api/v1/config/trading
GET    /api/v1/config/strategies
GET    /api/v1/config/web-ui
PUT    /api/v1/config/update
POST   /api/v1/config/validate
GET    /api/v1/config/health
```

#### Data Models:
```python
class DatabaseConfig(BaseModel):
    host: str
    port: int
    name: str
    user: str
    password: str
    pool_size: int
    max_overflow: int

class ExchangeConfig(BaseModel):
    api_key: str
    api_secret: str
    sandbox: bool
    base_currency: str
    max_pairs: int
    min_volume_24h: float
    min_volatility: float

class TradingConfig(BaseModel):
    mode: str  # 'simulation' or 'live'
    max_concurrent_trades: int
    position_size_percentage: float
    stop_loss_percentage: float
    take_profit_percentage: float
```

---

### 2. Database Service (`database-service`)
**Port**: 8002  
**Purpose**: Centralized database operations and data persistence

#### Responsibilities:
- Manage all database connections and pooling
- Handle trade data persistence and retrieval
- Manage balance tracking and updates
- Store market data cache
- Handle alerts and notifications
- Provide portfolio analytics

#### API Endpoints:
```
# Trade Management
POST   /api/v1/trades
GET    /api/v1/trades
GET    /api/v1/trades/{trade_id}
PUT    /api/v1/trades/{trade_id}
GET    /api/v1/trades/open
GET    /api/v1/trades/exchange/{exchange_name}

# Balance Management
GET    /api/v1/balances
GET    /api/v1/balances/{exchange_name}
PUT    /api/v1/balances/{exchange_name}
GET    /api/v1/portfolio/summary

# Market Data Cache
POST   /api/v1/cache/market-data
GET    /api/v1/cache/market-data/{exchange}/{pair}/{data_type}
DELETE /api/v1/cache/expired

# Alerts
POST   /api/v1/alerts
GET    /api/v1/alerts
PUT    /api/v1/alerts/{alert_id}/resolve
GET    /api/v1/alerts/unresolved

# Performance Analytics
GET    /api/v1/performance/exchange/{exchange_name}
GET    /api/v1/performance/strategy/{strategy_name}
GET    /api/v1/performance/portfolio
```

#### Data Models:
```python
class Trade(BaseModel):
    trade_id: str
    pair: str
    exchange: str
    entry_price: float
    exit_price: Optional[float]
    status: str  # 'OPEN', 'CLOSED', 'CANCELLED'
    position_size: float
    strategy: str
    entry_time: datetime
    exit_time: Optional[datetime]
    pnl: Optional[float]

class Balance(BaseModel):
    exchange: str
    total_balance: float
    available_balance: float
    total_pnl: float
    daily_pnl: float
    timestamp: datetime

class Alert(BaseModel):
    alert_id: str
    level: str  # 'INFO', 'WARNING', 'ERROR'
    category: str
    message: str
    exchange: Optional[str]
    details: Dict[str, Any]
    timestamp: datetime
    resolved: bool
```

---

### 3. Exchange Service (`exchange-service`)
**Port**: 8003  
**Purpose**: Multi-exchange operations and market data management

#### Responsibilities:
- Manage connections to multiple exchanges (Binance, Crypto.com, Bybit)
- Handle rate limiting and API quotas
- Fetch market data (tickers, OHLCV, order books)
- Execute trades and manage orders
- Handle account operations (balance, positions)
- Provide exchange health monitoring

#### API Endpoints:
```
# Market Data
GET    /api/v1/market/ticker/{exchange}/{symbol}
GET    /api/v1/market/ohlcv/{exchange}/{symbol}
GET    /api/v1/market/orderbook/{exchange}/{symbol}
GET    /api/v1/market/pairs/{exchange}

# Trading Operations
POST   /api/v1/trading/order
DELETE /api/v1/trading/order/{exchange}/{order_id}
GET    /api/v1/trading/orders/{exchange}
GET    /api/v1/trading/positions/{exchange}

# Account Operations
GET    /api/v1/account/balance/{exchange}
GET    /api/v1/account/positions/{exchange}

# Exchange Management
GET    /api/v1/exchanges
GET    /api/v1/exchanges/{exchange_name}/health
GET    /api/v1/exchanges/{exchange_name}/info

# Simulation Mode
POST   /api/v1/simulation/order
GET    /api/v1/simulation/balance/{exchange}
```

#### Data Models:
```python
class MarketData(BaseModel):
    exchange: str
    symbol: str
    data_type: str  # 'ticker', 'ohlcv', 'orderbook'
    data: Dict[str, Any]
    timestamp: datetime

class Order(BaseModel):
    exchange: str
    symbol: str
    order_type: str  # 'market', 'limit', 'stop'
    side: str  # 'buy', 'sell'
    amount: float
    price: Optional[float]
    order_id: Optional[str]
    status: str

class ExchangeHealth(BaseModel):
    exchange: str
    status: str  # 'healthy', 'degraded', 'down'
    response_time: float
    last_check: datetime
    error_count: int
```

---

### 4. Strategy Service (`strategy-service`)
**Port**: 8004  
**Purpose**: Strategy analysis and signal generation

#### Responsibilities:
- Load and manage multiple trading strategies
- Analyze market data and generate signals
- Calculate strategy performance metrics
- Handle strategy consensus and voting
- Manage strategy configuration and parameters
- Provide strategy backtesting capabilities

#### API Endpoints:
```
# Strategy Management
GET    /api/v1/strategies
GET    /api/v1/strategies/{strategy_name}
POST   /api/v1/strategies/{strategy_name}/enable
POST   /api/v1/strategies/{strategy_name}/disable

# Signal Generation
POST   /api/v1/analysis/{exchange}/{pair}
GET    /api/v1/signals/{exchange}/{pair}
GET    /api/v1/signals/consensus/{exchange}/{pair}

# Strategy Performance
GET    /api/v1/performance/{strategy_name}
POST   /api/v1/performance/{strategy_name}/update
GET    /api/v1/performance/compare

# Strategy Configuration
GET    /api/v1/strategies/{strategy_name}/config
PUT    /api/v1/strategies/{strategy_name}/config
POST   /api/v1/strategies/{strategy_name}/backtest
```

#### Data Models:
```python
class StrategySignal(BaseModel):
    strategy_name: str
    pair: str
    exchange: str
    signal: str  # 'buy', 'sell', 'hold'
    confidence: float
    strength: float
    timestamp: datetime
    market_regime: str

class StrategyConsensus(BaseModel):
    pair: str
    exchange: str
    consensus_signal: str
    agreement_percentage: float
    participating_strategies: int
    signals: List[StrategySignal]
    timestamp: datetime

class StrategyPerformance(BaseModel):
    strategy_name: str
    total_trades: int
    winning_trades: int
    win_rate: float
    total_pnl: float
    sharpe_ratio: float
    max_drawdown: float
    period: str
```

---

### 5. Orchestrator Service (`orchestrator-service`)
**Port**: 8005  
**Purpose**: Main trading coordination and decision making

#### Responsibilities:
- Coordinate all trading activities
- Manage trading cycles and timing
- Handle trade entry and exit decisions
- Manage position sizing and risk
- Coordinate with all other services
- Handle emergency stops and safety measures

#### API Endpoints:
```
# Trading Control
POST   /api/v1/trading/start
POST   /api/v1/trading/stop
POST   /api/v1/trading/emergency-stop
GET    /api/v1/trading/status

# Trading Operations
POST   /api/v1/trading/cycle/entry
POST   /api/v1/trading/cycle/exit
GET    /api/v1/trading/active-trades
GET    /api/v1/trading/cycle-stats

# Risk Management
GET    /api/v1/risk/limits
PUT    /api/v1/risk/limits
GET    /api/v1/risk/exposure
POST   /api/v1/risk/check

# Pair Selection
GET    /api/v1/pairs/selected
POST   /api/v1/pairs/select
GET    /api/v1/pairs/candidates
```

#### Data Models:
```python
class TradingStatus(BaseModel):
    status: str  # 'running', 'stopped', 'emergency_stop'
    cycle_count: int
    active_trades: int
    total_pnl: float
    last_cycle: datetime
    uptime: timedelta

class TradingCycle(BaseModel):
    cycle_id: str
    cycle_type: str  # 'entry', 'exit', 'maintenance'
    start_time: datetime
    end_time: Optional[datetime]
    trades_processed: int
    signals_generated: int
    errors: List[str]

class RiskLimits(BaseModel):
    max_concurrent_trades: int
    max_daily_trades: int
    max_daily_loss: float
    max_total_loss: float
    position_size_percentage: float
```

---

### 6. Web Dashboard Service (`web-dashboard-service`)
**Port**: 8006  
**Purpose**: User interface and real-time monitoring

#### Responsibilities:
- Provide web-based dashboard interface
- Handle real-time data updates via WebSockets
- Display portfolio, trades, and performance metrics
- Provide trading controls and configuration interface
- Handle user authentication and authorization
- Serve static assets and templates

#### API Endpoints:
```
# Dashboard Pages
GET    /
GET    /dashboard
GET    /trades
GET    /portfolio
GET    /strategies
GET    /alerts
GET    /settings

# Real-time Data
WS     /ws/dashboard
WS     /ws/trades
WS     /ws/portfolio
WS     /ws/alerts

# API Proxies (forward to other services)
GET    /api/portfolio
GET    /api/trades
GET    /api/strategies
GET    /api/alerts
POST   /api/control/start
POST   /api/control/stop
```

#### WebSocket Events:
```javascript
// Portfolio Updates
{
  "type": "portfolio_update",
  "data": {
    "total_balance": 50000.0,
    "total_pnl": 2500.0,
    "daily_pnl": 150.0,
    "active_trades": 3
  }
}

// Trade Updates
{
  "type": "trade_update",
  "data": {
    "trade_id": "uuid",
    "pair": "BTC/USDC",
    "exchange": "binance",
    "status": "OPEN",
    "entry_price": 45000.0,
    "current_pnl": 500.0
  }
}

// Alert Updates
{
  "type": "alert",
  "data": {
    "level": "WARNING",
    "message": "High volatility detected",
    "exchange": "binance",
    "timestamp": "2024-01-01T12:00:00Z"
  }
}
```

---

## Service Communication Patterns

### 1. Synchronous Communication (REST APIs)
- Configuration service provides config to all services
- Database service handles all data persistence
- Exchange service provides market data to strategy service
- Strategy service provides signals to orchestrator
- Web dashboard proxies requests to other services

### 2. Asynchronous Communication (WebSockets)
- Real-time updates from all services to web dashboard
- Market data streaming from exchange service
- Trade updates from orchestrator service
- Alert notifications from all services

### 3. Event-Driven Communication
- Trade events trigger balance updates
- Strategy signals trigger trade decisions
- Configuration changes trigger service reloads
- Error events trigger alert creation

---

## Data Flow Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Web Dashboard │    │   Orchestrator  │    │   Strategy      │
│   Service       │    │   Service       │    │   Service       │
│   (8006)        │    │   (8005)        │    │   (8004)        │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Configuration │    │   Database      │    │   Exchange      │
│   Service       │    │   Service       │    │   Service       │
│   (8001)        │    │   (8002)        │    │   (8003)        │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

### Data Flow:
1. **Configuration Service** provides config to all services
2. **Exchange Service** fetches market data from exchanges
3. **Strategy Service** analyzes market data and generates signals
4. **Orchestrator Service** makes trading decisions based on signals
5. **Database Service** persists all trade and market data
6. **Web Dashboard Service** displays real-time information

---

## Deployment Architecture

### Docker Compose Setup
```yaml
version: '3.8'

services:
  config-service:
    build: ./services/config-service
    ports:
      - "8001:8001"
    environment:
      - CONFIG_PATH=/app/config/config.yaml
    volumes:
      - ./config:/app/config
    networks:
      - trading-bot-network

  database-service:
    build: ./services/database-service
    ports:
      - "8002:8002"
    environment:
      - CONFIG_SERVICE_URL=http://config-service:8001
    depends_on:
      - postgres
      - redis
    networks:
      - trading-bot-network

  exchange-service:
    build: ./services/exchange-service
    ports:
      - "8003:8003"
    environment:
      - CONFIG_SERVICE_URL=http://config-service:8001
      - DATABASE_SERVICE_URL=http://database-service:8002
    networks:
      - trading-bot-network

  strategy-service:
    build: ./services/strategy-service
    ports:
      - "8004:8004"
    environment:
      - CONFIG_SERVICE_URL=http://config-service:8001
      - EXCHANGE_SERVICE_URL=http://exchange-service:8003
      - DATABASE_SERVICE_URL=http://database-service:8002
    networks:
      - trading-bot-network

  orchestrator-service:
    build: ./services/orchestrator-service
    ports:
      - "8005:8005"
    environment:
      - CONFIG_SERVICE_URL=http://config-service:8001
      - DATABASE_SERVICE_URL=http://database-service:8002
      - EXCHANGE_SERVICE_URL=http://exchange-service:8003
      - STRATEGY_SERVICE_URL=http://strategy-service:8004
    networks:
      - trading-bot-network

  web-dashboard-service:
    build: ./services/web-dashboard-service
    ports:
      - "8006:8006"
    environment:
      - CONFIG_SERVICE_URL=http://config-service:8001
      - DATABASE_SERVICE_URL=http://database-service:8002
      - EXCHANGE_SERVICE_URL=http://exchange-service:8003
      - STRATEGY_SERVICE_URL=http://strategy-service:8004
      - ORCHESTRATOR_SERVICE_URL=http://orchestrator-service:8005
    networks:
      - trading-bot-network

  postgres:
    image: postgres:15
    environment:
      - POSTGRES_DB=trading_bot
      - POSTGRES_USER=carloslarramba
      - POSTGRES_PASSWORD=password
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - trading-bot-network

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    networks:
      - trading-bot-network

networks:
  trading-bot-network:
    driver: bridge

volumes:
  postgres_data:
```

---

## Service Dependencies

### Startup Order:
1. **PostgreSQL & Redis** (Infrastructure)
2. **Configuration Service** (Provides config to all services)
3. **Database Service** (Data persistence)
4. **Exchange Service** (Market data)
5. **Strategy Service** (Analysis)
6. **Orchestrator Service** (Trading logic)
7. **Web Dashboard Service** (User interface)

### Health Checks:
Each service implements health check endpoints:
```
GET /health
GET /ready
GET /live
```

### Circuit Breakers:
- Service-to-service communication uses circuit breakers
- Fallback mechanisms for critical operations
- Graceful degradation when services are unavailable

---

## Security Considerations

### API Security:
- JWT-based authentication for service-to-service communication
- API rate limiting on all endpoints
- Input validation and sanitization
- HTTPS/TLS encryption for all communications

### Data Security:
- Encrypted storage of exchange API keys
- Database connection encryption
- Audit logging for all operations
- Regular security updates and patches

---

## Monitoring and Observability

### Logging:
- Structured JSON logging across all services
- Centralized log aggregation (ELK stack)
- Log correlation IDs for request tracing

### Metrics:
- Prometheus metrics for all services
- Grafana dashboards for monitoring
- Custom trading metrics and alerts

### Tracing:
- Distributed tracing with Jaeger
- Request flow visualization
- Performance bottleneck identification

---

## Scaling Strategy

### Horizontal Scaling:
- Stateless services can be scaled horizontally
- Database service with read replicas
- Exchange service with multiple instances
- Strategy service with load balancing

### Vertical Scaling:
- Resource allocation based on service requirements
- Memory and CPU optimization
- Database connection pooling

---

## Migration Strategy

### Phase 1: Service Extraction
1. Extract Configuration Service
2. Extract Database Service
3. Update existing components to use service APIs

### Phase 2: Core Services
1. Extract Exchange Service
2. Extract Strategy Service
3. Implement service communication

### Phase 3: Orchestration
1. Extract Orchestrator Service
2. Implement trading coordination
3. Add monitoring and health checks

### Phase 4: Web Interface
1. Extract Web Dashboard Service
2. Implement real-time updates
3. Add user authentication

### Phase 5: Production Deployment
1. Containerize all services
2. Implement CI/CD pipelines
3. Add monitoring and alerting
4. Performance testing and optimization

---

## Next Steps

1. **Create service directories** with FastAPI applications
2. **Implement service APIs** with proper error handling
3. **Add service discovery** and health checks
4. **Create Docker configurations** for each service
5. **Implement inter-service communication**
6. **Add monitoring and logging**
7. **Create deployment scripts**
8. **Test service integration**
9. **Performance optimization**
10. **Production deployment**

This microservice architecture provides a scalable, maintainable, and robust foundation for your trading bot while preserving all existing functionality and adding new capabilities for future growth. 