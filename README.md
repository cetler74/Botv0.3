# Botv0.3 - Multi-Exchange Perpetual Futures Trading Bot

A sophisticated multi-exchange perpetual futures trading bot that operates on Binance, Crypto.com, and Bybit with a beautiful web dashboard for monitoring and control.

## Features

### 🚀 Core Features
- **Multi-Exchange Support**: Trade on Binance, Crypto.com, and Bybit simultaneously
- **Perpetual Futures Trading**: Leverage futures markets for enhanced opportunities
- **Dynamic Pair Selection**: Automatically selects the most liquid and volatile pairs
- **Multiple Trading Strategies**: Integrates existing strategy files for diverse approaches
- **Real-time Portfolio Management**: Track balances, PnL, and performance across all exchanges
- **Beautiful Web Dashboard**: Modern UI with real-time updates and comprehensive analytics

### 🎯 Trading Capabilities
- **Simulation Mode**: Test strategies without real money
- **Live Mode**: Execute real trades with proper risk management
- **Profit Protection**: Automatic profit-taking mechanisms
- **Trailing Stops**: Dynamic stop-loss management
- **Risk Management**: Position sizing and balance monitoring
- **Emergency Stop**: Instant halt of all trading activities

### 📊 Analytics & Monitoring
- **Real-time PnL Tracking**: Monitor profits/losses across all exchanges
- **Strategy Performance**: Individual strategy analytics and optimization
- **Exchange Health Monitoring**: Real-time exchange status and connectivity
- **Alert System**: Comprehensive alerting for critical events
- **Historical Data**: Complete trade history and performance metrics

## Architecture

### Microservices Design
The bot is built with a modern microservices architecture for scalability, maintainability, and fault tolerance:

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

### Service Architecture

#### 1. **Configuration Service** (Port 8001)
- **Purpose**: Centralized configuration management and validation
- **Responsibilities**:
  - Load and validate configuration from YAML files
  - Provide configuration endpoints for all services
  - Handle configuration updates and hot-reloading
  - Manage configuration versioning and rollbacks
- **API**: REST endpoints for config retrieval and updates
- **Data**: Exchange credentials, trading parameters, strategy configs

#### 2. **Database Service** (Port 8002)
- **Purpose**: Centralized database operations and data persistence
- **Responsibilities**:
  - Manage all database connections and pooling
  - Handle trade data persistence and retrieval
  - Manage balance tracking and updates
  - Store market data cache
  - Handle alerts and notifications
- **API**: REST endpoints for trades, balances, alerts, portfolio
- **Data**: PostgreSQL with connection pooling

#### 3. **Exchange Service** (Port 8003)
- **Purpose**: Multi-exchange operations and market data management
- **Responsibilities**:
  - Manage connections to multiple exchanges (Binance, Crypto.com, Bybit)
  - Handle rate limiting and API quotas
  - Fetch market data (tickers, OHLCV, order books)
  - Execute trades and manage orders
  - Handle account operations (balance, positions)
- **API**: REST endpoints for market data, trading operations, account info
- **Data**: Real-time exchange data with caching

#### 4. **Strategy Service** (Port 8004)
- **Purpose**: Strategy analysis and signal generation
- **Responsibilities**:
  - Load and manage multiple trading strategies
  - Analyze market data and generate signals
  - Calculate strategy performance metrics
  - Handle strategy consensus and voting
  - Manage strategy configuration and parameters
- **API**: REST endpoints for strategy management, signal generation, performance
- **Data**: Strategy analysis results and performance metrics

#### 5. **Orchestrator Service** (Port 8005)
- **Purpose**: Main trading coordination and decision making
- **Responsibilities**:
  - Coordinate all trading activities
  - Manage trading cycles and timing
  - Handle trade entry and exit decisions
  - Manage position sizing and risk
  - Coordinate with all other services
- **API**: REST endpoints for trading control, risk management, pair selection
- **Data**: Trading state and coordination logic

#### 6. **Web Dashboard Service** (Port 8006)
- **Purpose**: User interface and real-time monitoring
- **Responsibilities**:
  - Provide web-based dashboard interface
  - Handle real-time data updates via WebSockets
  - Display portfolio, trades, and performance metrics
  - Provide trading controls and configuration interface
- **API**: WebSocket endpoints for real-time updates, REST proxies to other services
- **Data**: UI state and real-time data streams

### Communication Patterns

#### Synchronous Communication (REST APIs)
- Configuration service provides config to all services
- Database service handles all data persistence
- Exchange service provides market data to strategy service
- Strategy service provides signals to orchestrator
- Web dashboard proxies requests to other services

#### Asynchronous Communication (WebSockets)
- Real-time updates from all services to web dashboard
- Market data streaming from exchange service
- Trade updates from orchestrator service
- Alert notifications from all services

#### Event-Driven Communication
- Trade events trigger balance updates
- Strategy signals trigger trade decisions
- Configuration changes trigger service reloads
- Error events trigger alert creation

### Infrastructure Services

#### PostgreSQL Database
- Centralized data storage for all services
- Connection pooling for optimal performance
- Automated backup and recovery

#### Redis Cache
- High-speed caching for market data
- Session management and rate limiting
- Real-time data distribution

#### Monitoring Stack
- **Prometheus**: Metrics collection and storage
- **Grafana**: Visualization and dashboards
- **Health Checks**: Service availability monitoring

## Installation

### Prerequisites

- Python 3.8+
- PostgreSQL 12+
- Redis 6+
- Node.js 14+ (for development)

### 1. Clone the Repository

```bash
git clone <repository-url>
cd Botv0.3
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Database Setup

Create the PostgreSQL database and run the setup script:

```bash
# Create database
createdb trading_bot

# Run table creation script
psql "postgresql://carloslarramba@localhost:5432/trading_bot" -f scripts/create_all_trading_bot_tables.sql
```

### 4. Configuration

1. Copy the configuration template:
```bash
cp config/config.yaml config/config.yaml.local
```

2. Edit `config/config.yaml.local` with your settings:
   - Database credentials
   - Exchange API keys
   - Trading parameters
   - Strategy configurations

### 5. Redis Setup

Start Redis server:
```bash
redis-server
```

## Usage

### Running the Bot

#### Option 1: Monolithic Mode (Legacy)
```bash
# Start the Trading Bot
python3 -m orchestrator.trading_orchestrator

# Start the Web Dashboard
cd web
python app.py
```

The dashboard will be available at `http://localhost:8000`

#### Option 2: Microservices Mode (Recommended)

The bot now supports a modern microservices architecture for better scalability and maintainability.

**Prerequisites:**
- Docker and Docker Compose installed
- At least 4GB RAM available

**Quick Start:**
```bash
# Build and start all microservices
./deploy-microservices.sh build
./deploy-microservices.sh start

# Check service status
./deploy-microservices.sh status

# View logs
./deploy-microservices.sh logs

# Stop all services
./deploy-microservices.sh stop
```

**Service Endpoints:**
- **Configuration Service**: http://localhost:8001
- **Database Service**: http://localhost:8002
- **Exchange Service**: http://localhost:8003
- **Strategy Service**: http://localhost:8004
- **Orchestrator Service**: http://localhost:8005
- **Web Dashboard**: http://localhost:8006

**Health Checks:**
```bash
# Check all service health
curl http://localhost:8001/health  # Config Service
curl http://localhost:8002/health  # Database Service
curl http://localhost:8003/health  # Exchange Service
curl http://localhost:8004/health  # Strategy Service
curl http://localhost:8005/health  # Orchestrator Service
curl http://localhost:8006/health  # Web Dashboard
```

**Deployment Script Commands:**
```bash
./deploy-microservices.sh build      # Build all service containers
./deploy-microservices.sh start      # Start all services
./deploy-microservices.sh stop       # Stop all services
./deploy-microservices.sh restart    # Restart all services
./deploy-microservices.sh status     # Show service status and health
./deploy-microservices.sh logs       # Show all service logs
./deploy-microservices.sh logs [service]  # Show specific service logs
./deploy-microservices.sh cleanup    # Clean up containers and volumes
```

**Individual Service Management:**
```bash
# Start specific services
docker-compose up -d config-service database-service

# View specific service logs
docker-compose logs -f exchange-service

# Restart specific service
docker-compose restart strategy-service
```

### Configuration

The bot uses a centralized configuration system. All settings are in `config/config.yaml`:

```yaml
# Database Configuration
database:
  host: localhost
  port: 5432
  name: trading_bot
  user: carloslarramba
  password: ""

# Exchange Configuration
exchanges:
  binance:
    api_key: "your_api_key"
    api_secret: "your_api_secret"
    sandbox: false
    base_currency: USDC
    max_pairs: 10

# Trading Configuration
trading:
  mode: simulation  # or live
  cycle_interval_seconds: 60
  max_trades_per_exchange: 3
  position_size_percentage: 0.1
  min_confidence: 0.6
```

### Trading Modes

#### Simulation Mode
- Uses live market data
- Simulates trades without real execution
- Perfect for strategy testing and validation
- No real money at risk

#### Live Mode
- Executes real trades on exchanges
- Requires proper API keys and permissions
- Implements full risk management
- Monitor carefully during initial deployment

## Strategies

The bot integrates with existing strategy files:

- `vwma_hull_strategy.py` - VWMA Hull Moving Average Strategy
- `heikin_ashi_strategy.py` - Heikin Ashi Candlestick Strategy
- `engulfing_multi_tf.py` - Multi-Timeframe Engulfing Pattern Strategy
- `multi_timeframe_confluence_strategy.py` - Multi-Timeframe Confluence Strategy
- `strategy_pnl.py` - PnL-Based Strategy
- `strategy_pnl_enhanced.py` - Enhanced PnL Strategy

### Adding New Strategies

1. Create your strategy file following the existing pattern
2. Implement the required interface methods
3. Add the strategy to the configuration
4. The bot will automatically integrate and use the new strategy

## Web Dashboard

The web dashboard provides:

### 📈 Portfolio Overview
- Total balance across all exchanges
- Real-time PnL tracking
- Daily performance metrics
- Open trades count

### 📊 Analytics
- PnL charts over time
- Performance by exchange
- Strategy performance metrics
- Historical trade analysis

### 🎛️ Controls
- Start/stop trading
- Emergency stop functionality
- Strategy enable/disable
- Configuration management

### 🔔 Alerts & Monitoring
- Real-time alerts
- Exchange health status
- Error notifications
- Performance warnings

## API Endpoints

The web dashboard exposes REST API endpoints:

- `GET /api/portfolio` - Portfolio summary
- `GET /api/trades` - Recent trades
- `GET /api/exchanges` - Exchange information
- `GET /api/strategies` - Strategy status
- `GET /api/alerts` - System alerts
- `POST /api/control/start` - Start trading
- `POST /api/control/stop` - Stop trading
- `POST /api/control/emergency-stop` - Emergency stop

## Monitoring & Alerts

### Alert Categories
- **Configuration Errors**: Missing or invalid configurations
- **Exchange Issues**: API failures or connectivity problems
- **Trading Alerts**: Unusual trading activity or errors
- **Performance Warnings**: Strategy underperformance
- **System Errors**: Database or application errors

### Logging
- Comprehensive logging to `logs/trading_bot.log`
- Different log levels for different components
- Structured logging for easy analysis

## Risk Management

### Built-in Protections
- **Position Sizing**: Configurable position size limits
- **Balance Monitoring**: Automatic balance checks
- **Profit Protection**: Automatic profit-taking
- **Trailing Stops**: Dynamic stop-loss management
- **Emergency Stop**: Instant halt capability
- **Rate Limiting**: Exchange API rate limit compliance

### Best Practices
1. Always start in simulation mode
2. Monitor the dashboard regularly
3. Set appropriate position sizes
4. Use proper API permissions
5. Keep API keys secure
6. Regular backup of configurations

## Development

### Project Structure
```
Botv0.3/
├── core/                    # Core components
│   ├── config_manager.py   # Configuration management
│   ├── database_manager.py # Database operations
│   ├── exchange_manager.py # Exchange integration
│   └── strategy_manager.py # Strategy coordination
├── orchestrator/           # Main trading logic
│   └── trading_orchestrator.py
├── web/                   # Web dashboard
│   ├── app.py            # FastAPI application
│   ├── templates/        # HTML templates
│   └── static/          # Static assets
├── config/               # Configuration files
├── scripts/             # Database scripts
├── logs/               # Log files
├── requirements.txt    # Python dependencies
└── README.md          # This file
```

### Adding Features
1. Follow the existing code patterns
2. Add proper error handling
3. Include comprehensive logging
4. Update configuration schema
5. Add tests for new functionality
6. Update documentation

## Troubleshooting

### Common Issues

#### Database Connection Errors
- Verify PostgreSQL is running
- Check database credentials in config
- Ensure database exists and tables are created

#### Exchange API Errors
- Verify API keys are correct
- Check API permissions (trading enabled)
- Ensure IP whitelist is configured
- Check rate limits

#### Strategy Errors
- Verify strategy files are properly formatted
- Check strategy dependencies
- Review strategy configuration

#### Web Dashboard Issues
- Check if FastAPI server is running
- Verify port 8000 is available
- Check browser console for JavaScript errors

### Getting Help

1. Check the logs in `logs/trading_bot.log`
2. Review the dashboard alerts
3. Verify configuration settings
4. Test in simulation mode first

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Disclaimer

This trading bot is for educational and research purposes. Trading cryptocurrencies involves substantial risk of loss. Always:

- Test thoroughly in simulation mode
- Start with small amounts
- Monitor performance closely
- Never invest more than you can afford to lose
- Consider consulting with a financial advisor

The authors are not responsible for any financial losses incurred through the use of this software. 