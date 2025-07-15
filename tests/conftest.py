"""
Pytest configuration and shared fixtures for trading bot microservices tests
"""

import pytest
import asyncio
import httpx
import os
import tempfile
import json
from typing import Dict, Any, Generator
from unittest.mock import Mock, patch
import psycopg2
from psycopg2.extras import RealDictCursor

# Test configuration
TEST_CONFIG = {
    "database": {
        "host": "localhost",
        "port": 5432,
        "database": "trading_bot_test",
        "user": "carloslarramba",
        "password": "password"
    },
    "services": {
        "config": "http://localhost:8001",
        "database": "http://localhost:8002",
        "exchange": "http://localhost:8003",
        "strategy": "http://localhost:8004",
        "orchestrator": "http://localhost:8005",
        "web_dashboard": "http://localhost:8006"
    }
}

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
def test_config() -> Dict[str, Any]:
    """Provide test configuration."""
    return TEST_CONFIG

@pytest.fixture
def mock_exchange_data():
    """Mock exchange data for testing."""
    return {
        "binance": {
            "ticker": {
                "symbol": "BTC/USDC",
                "last": 45000.0,
                "bid": 44950.0,
                "ask": 45050.0,
                "volume": 1000.0,
                "timestamp": "2024-01-01T00:00:00Z"
            },
            "ohlcv": [
                [1640995200000, 45000.0, 45100.0, 44900.0, 45050.0, 100.0],
                [1640995260000, 45050.0, 45200.0, 45000.0, 45150.0, 150.0]
            ],
            "orderbook": {
                "bids": [[44950.0, 1.0], [44900.0, 2.0]],
                "asks": [[45050.0, 1.5], [45100.0, 2.5]]
            }
        }
    }

@pytest.fixture
def mock_strategy_signals():
    """Mock strategy signals for testing."""
    return {
        "vwma_hull": {
            "signal": "buy",
            "confidence": 0.8,
            "strength": 0.7,
            "market_regime": "trending"
        },
        "heikin_ashi": {
            "signal": "hold",
            "confidence": 0.6,
            "strength": 0.5,
            "market_regime": "sideways"
        }
    }

@pytest.fixture
def mock_trade_data():
    """Mock trade data for testing."""
    return {
        "trade_id": "test_trade_001",
        "pair": "BTC/USDC",
        "exchange": "binance",
        "entry_price": 45000.0,
        "position_size": 0.1,
        "status": "OPEN",
        "strategy": "consensus",
        "entry_time": "2024-01-01T00:00:00Z"
    }

@pytest.fixture
def mock_portfolio_data():
    """Mock portfolio data for testing."""
    return {
        "total_balance": 10000.0,
        "available_balance": 9500.0,
        "total_pnl": 500.0,
        "daily_pnl": 50.0,
        "win_rate": 0.65,
        "total_trades": 100,
        "winning_trades": 65
    }

@pytest.fixture
def mock_alert_data():
    """Mock alert data for testing."""
    return {
        "alert_id": "test_alert_001",
        "level": "WARNING",
        "category": "TRADING",
        "message": "Test alert message",
        "exchange": "binance",
        "timestamp": "2024-01-01T00:00:00Z",
        "resolved": False
    }

@pytest.fixture
async def http_client():
    """Async HTTP client for testing service endpoints."""
    async with httpx.AsyncClient() as client:
        yield client

@pytest.fixture
def temp_config_file():
    """Create a temporary configuration file for testing."""
    config_data = {
        "exchanges": {
            "binance": {
                "api_key": "test_key",
                "api_secret": "test_secret",
                "sandbox": True,
                "base_currency": "USDC",
                "max_pairs": 5,
                "min_volume_24h": 1000000,
                "min_volatility": 0.02
            }
        },
        "strategies": {
            "vwma_hull": {
                "enabled": True,
                "parameters": {}
            },
            "heikin_ashi": {
                "enabled": True,
                "parameters": {}
            }
        },
        "trading": {
            "mode": "simulation",
            "cycle_interval_seconds": 60,
            "exit_cycle_first": True,
            "max_concurrent_trades": 5,
            "max_daily_trades": 20,
            "max_daily_loss": 100.0,
            "max_total_loss": 500.0,
            "position_size_percentage": 0.1
        },
        "database": {
            "host": "localhost",
            "port": 5432,
            "database": "trading_bot_test",
            "user": "carloslarramba",
            "password": "password",
            "pool_size": 5
        }
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        import yaml
        yaml.dump(config_data, f)
        temp_file = f.name
    
    yield temp_file
    
    # Cleanup
    os.unlink(temp_file)

@pytest.fixture
def mock_database_connection():
    """Mock database connection for testing."""
    with patch('psycopg2.connect') as mock_connect:
        mock_conn = Mock()
        mock_cursor = Mock()
        
        # Mock cursor methods
        mock_cursor.fetchone.return_value = {"count": 1}
        mock_cursor.fetchall.return_value = []
        mock_cursor.execute.return_value = None
        
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connect.return_value = mock_conn
        
        yield mock_connect

@pytest.fixture
def mock_ccxt_exchange():
    """Mock CCXT exchange for testing."""
    with patch('ccxt.async_support.binance') as mock_exchange_class:
        mock_exchange = Mock()
        
        # Mock exchange methods
        mock_exchange.fetch_ticker.return_value = {
            "symbol": "BTC/USDC",
            "last": 45000.0,
            "bid": 44950.0,
            "ask": 45050.0,
            "volume": 1000.0
        }
        
        mock_exchange.fetch_ohlcv.return_value = [
            [1640995200000, 45000.0, 45100.0, 44900.0, 45050.0, 100.0],
            [1640995260000, 45050.0, 45200.0, 45000.0, 45150.0, 150.0]
        ]
        
        mock_exchange.fetch_balance.return_value = {
            "total": {"USDC": 10000.0},
            "free": {"USDC": 9500.0},
            "used": {"USDC": 500.0}
        }
        
        mock_exchange.create_order.return_value = {
            "id": "test_order_001",
            "symbol": "BTC/USDC",
            "type": "market",
            "side": "buy",
            "amount": 0.1,
            "status": "closed"
        }
        
        mock_exchange_class.return_value = mock_exchange
        yield mock_exchange

@pytest.fixture
def mock_strategy_instance():
    """Mock strategy instance for testing."""
    strategy = Mock()
    
    # Mock strategy methods
    strategy.initialize.return_value = None
    strategy.update.return_value = None
    strategy.generate_signal.return_value = ("buy", 0.8, 0.7)
    
    # Mock strategy state
    strategy.state = Mock()
    strategy.state.market_regime = "trending"
    
    yield strategy

@pytest.fixture
def sample_market_data():
    """Sample market data for testing."""
    import pandas as pd
    from datetime import datetime, timedelta
    
    dates = pd.date_range(start='2024-01-01', periods=100, freq='1H')
    data = {
        'open': [45000 + i * 10 for i in range(100)],
        'high': [45100 + i * 10 for i in range(100)],
        'low': [44900 + i * 10 for i in range(100)],
        'close': [45050 + i * 10 for i in range(100)],
        'volume': [100 + i for i in range(100)]
    }
    
    df = pd.DataFrame(data, index=dates)
    return df

@pytest.fixture
def mock_websocket_connection():
    """Mock WebSocket connection for testing."""
    with patch('websockets.connect') as mock_ws_connect:
        mock_ws = Mock()
        
        # Mock WebSocket methods
        mock_ws.send = Mock()
        mock_ws.recv = Mock()
        mock_ws.close = Mock()
        
        mock_ws_connect.return_value.__aenter__.return_value = mock_ws
        yield mock_ws

@pytest.fixture
def test_database():
    """Test database setup and teardown."""
    # Setup test database
    conn = psycopg2.connect(
        host=TEST_CONFIG["database"]["host"],
        port=TEST_CONFIG["database"]["port"],
        database=TEST_CONFIG["database"]["database"],
        user=TEST_CONFIG["database"]["user"],
        password=TEST_CONFIG["database"]["password"]
    )
    
    # Create test tables
    with conn.cursor() as cursor:
        # Create test trades table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_trades (
                trade_id VARCHAR(50) PRIMARY KEY,
                pair VARCHAR(20) NOT NULL,
                exchange VARCHAR(20) NOT NULL,
                entry_price DECIMAL(20,8),
                exit_price DECIMAL(20,8),
                position_size DECIMAL(20,8),
                status VARCHAR(20),
                strategy VARCHAR(50),
                entry_time TIMESTAMP,
                exit_time TIMESTAMP,
                pnl DECIMAL(20,8)
            )
        """)
        
        # Create test balances table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_balances (
                exchange VARCHAR(20) PRIMARY KEY,
                balance DECIMAL(20,8),
                available_balance DECIMAL(20,8),
                total_pnl DECIMAL(20,8),
                daily_pnl DECIMAL(20,8),
                timestamp TIMESTAMP
            )
        """)
        
        # Create test alerts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_alerts (
                alert_id VARCHAR(50) PRIMARY KEY,
                level VARCHAR(20),
                category VARCHAR(50),
                message TEXT,
                exchange VARCHAR(20),
                timestamp TIMESTAMP,
                resolved BOOLEAN
            )
        """)
    
    conn.commit()
    conn.close()
    
    yield
    
    # Cleanup test database
    conn = psycopg2.connect(
        host=TEST_CONFIG["database"]["host"],
        port=TEST_CONFIG["database"]["port"],
        database=TEST_CONFIG["database"]["database"],
        user=TEST_CONFIG["database"]["user"],
        password=TEST_CONFIG["database"]["password"]
    )
    
    with conn.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS test_trades")
        cursor.execute("DROP TABLE IF EXISTS test_balances")
        cursor.execute("DROP TABLE IF EXISTS test_alerts")
    
    conn.commit()
    conn.close()

@pytest.fixture
def performance_test_data():
    """Generate performance test data."""
    import random
    from datetime import datetime, timedelta
    
    trades = []
    for i in range(1000):
        trade = {
            "trade_id": f"perf_trade_{i:04d}",
            "pair": random.choice(["BTC/USDC", "ETH/USDC", "ADA/USDC"]),
            "exchange": random.choice(["binance", "cryptocom", "bybit"]),
            "entry_price": random.uniform(100, 50000),
            "exit_price": random.uniform(100, 50000),
            "position_size": random.uniform(0.01, 1.0),
            "status": random.choice(["OPEN", "CLOSED", "CANCELLED"]),
            "strategy": random.choice(["vwma_hull", "heikin_ashi", "consensus"]),
            "entry_time": datetime.now() - timedelta(hours=random.randint(1, 168)),
            "exit_time": datetime.now() - timedelta(hours=random.randint(0, 167)) if random.random() > 0.3 else None,
            "pnl": random.uniform(-1000, 1000) if random.random() > 0.3 else None
        }
        trades.append(trade)
    
    return trades 