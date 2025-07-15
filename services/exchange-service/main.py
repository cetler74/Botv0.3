"""
Exchange Service for the Multi-Exchange Trading Bot
Multi-exchange operations and market data management
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import ccxt
import ccxt.async_support as ccxt_async
import pandas as pd
import numpy as np
from decimal import Decimal
import time
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Exchange Service",
    description="Multi-exchange operations and market data management",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data Models
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
    price: Optional[float] = None
    order_id: Optional[str] = None
    status: str = 'pending'

class ExchangeHealth(BaseModel):
    exchange: str
    status: str  # 'healthy', 'degraded', 'down'
    response_time: float
    last_check: datetime
    error_count: int

class Balance(BaseModel):
    exchange: str
    total: float
    free: float
    used: float
    timestamp: datetime

class Position(BaseModel):
    exchange: str
    symbol: str
    side: str  # 'long', 'short'
    size: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    timestamp: datetime

class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str
    exchanges_connected: int
    total_exchanges: int

# Global variables
exchanges = {}
rate_limits = {}
config_service_url = os.getenv("CONFIG_SERVICE_URL", "http://config-service:8001")
database_service_url = os.getenv("DATABASE_SERVICE_URL", "http://database_service:8002")

class ExchangeManager:
    """Manages multiple exchange connections and operations"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._initialize_exchanges()
        
    def _initialize_exchanges(self) -> None:
        """Initialize exchange connections"""
        exchange_configs = self.config.get('exchanges', {})
        
        for exchange_name, config in exchange_configs.items():
            try:
                # Map exchange names to CCXT exchange classes
                exchange_class_map = {
                    'binance': ccxt_async.binance,
                    'cryptocom': ccxt_async.cryptocom,
                    'bybit': ccxt_async.bybit
                }
                
                if exchange_name not in exchange_class_map:
                    logger.warning(f"Unsupported exchange: {exchange_name}")
                    continue
                    
                exchange_class = exchange_class_map[exchange_name]
                
                # Initialize exchange with configuration
                exchange_config = {
                    'apiKey': config.get('api_key', ''),
                    'secret': config.get('api_secret', ''),
                    'sandbox': config.get('sandbox', False),
                    'enableRateLimit': True,
                    'rateLimit': 100,  # 100ms between requests
                    'timeout': 30000,  # 30 seconds
                }
                
                # Create exchange instance
                exchange = exchange_class(exchange_config)
                
                # Store exchange and its configuration
                exchanges[exchange_name] = {
                    'instance': exchange,
                    'config': config,
                    'last_request': 0,
                    'request_count': 0,
                    'health': {
                        'status': 'unknown',
                        'last_check': None,
                        'response_time': 0,
                        'error_count': 0
                    }
                }
                
                # Initialize rate limiting
                rate_limits[exchange_name] = {
                    'last_request': 0,
                    'request_count': 0,
                    'rate_limit_delay': config.get('rate_limit_delay', 0.1)
                }
                
                logger.info(f"Initialized {exchange_name} exchange")
                
            except Exception as e:
                logger.error(f"Failed to initialize {exchange_name} exchange: {e}")
                
    async def _rate_limit(self, exchange_name: str) -> None:
        """Apply rate limiting for exchange requests"""
        if exchange_name in rate_limits:
            rate_limit = rate_limits[exchange_name]
            current_time = time.time()
            
            # Check if we need to wait
            time_since_last = current_time - rate_limit['last_request']
            if time_since_last < rate_limit['rate_limit_delay']:
                await asyncio.sleep(rate_limit['rate_limit_delay'] - time_since_last)
                
            rate_limit['last_request'] = time.time()
            rate_limit['request_count'] += 1
            
    async def _handle_exchange_error(self, exchange_name: str, error: Exception, operation: str) -> None:
        """Handle exchange errors and create alerts"""
        error_msg = f"Exchange error on {exchange_name} during {operation}: {str(error)}"
        logger.error(error_msg)
        
        # Update exchange health
        if exchange_name in exchanges:
            exchanges[exchange_name]['health']['error_count'] += 1
            exchanges[exchange_name]['health']['status'] = 'degraded'
        
        # Create alert in database
        try:
            async with httpx.AsyncClient() as client:
                alert_data = {
                    'alert_id': f"exchange_error_{exchange_name}_{int(time.time())}",
                    'level': 'ERROR',
                    'category': 'EXCHANGE',
                    'message': error_msg,
                    'exchange': exchange_name,
                    'details': {
                        'operation': operation,
                        'error_type': type(error).__name__,
                        'timestamp': datetime.utcnow().isoformat()
                    },
                    'timestamp': datetime.utcnow(),
                    'resolved': False
                }
                await client.post(f"{database_service_url}/api/v1/alerts", json=alert_data)
        except Exception as e:
            logger.error(f"Failed to create alert: {e}")

# Global exchange manager
exchange_manager = None

async def get_config_from_service() -> Dict[str, Any]:
    """Get configuration from config service"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{config_service_url}/api/v1/config/exchanges")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Failed to get config from service: {e}")
        # Fallback to environment variables
        return {
            'binance': {
                'api_key': os.getenv('BINANCE_API_KEY', ''),
                'api_secret': os.getenv('BINANCE_API_SECRET', ''),
                'sandbox': os.getenv('BINANCE_SANDBOX', 'true').lower() == 'true',
                'base_currency': 'USDC',
                'max_pairs': 10,
                'min_volume_24h': 1000000,
                'min_volatility': 0.02
            }
        }

async def initialize_exchanges():
    """Initialize exchange connections"""
    global exchange_manager
    try:
        config = await get_config_from_service()
        exchange_manager = ExchangeManager({'exchanges': config})
        logger.info("Exchange service initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize exchange service: {e}")
        raise

# API Endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    connected_count = sum(1 for ex in exchanges.values() if ex['health']['status'] == 'healthy')
    return HealthResponse(
        status="healthy" if connected_count > 0 else "degraded",
        timestamp=datetime.utcnow(),
        version="1.0.0",
        exchanges_connected=connected_count,
        total_exchanges=len(exchanges)
    )

@app.get("/ready")
async def readiness_check():
    """Readiness check endpoint"""
    if not exchange_manager:
        raise HTTPException(status_code=503, detail="Exchange manager not initialized")
    return {"status": "ready"}

@app.get("/live")
async def liveness_check():
    """Liveness check endpoint"""
    return {"status": "alive"}

# Market Data Endpoints
@app.get("/api/v1/market/ticker/{exchange}/{symbol}")
async def get_ticker(exchange: str, symbol: str):
    """Get ticker information for a symbol"""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    try:
        await exchange_manager._rate_limit(exchange)
        exchange_instance = exchanges[exchange]['instance']
        
        start_time = time.time()
        ticker = await exchange_instance.fetch_ticker(symbol)
        response_time = time.time() - start_time
        
        # Update health
        exchanges[exchange]['health']['status'] = 'healthy'
        exchanges[exchange]['health']['response_time'] = response_time
        exchanges[exchange]['health']['last_check'] = datetime.utcnow()
        
        # Cache the data
        try:
            async with httpx.AsyncClient() as client:
                cache_data = {
                    'exchange': exchange,
                    'pair': symbol,
                    'data_type': 'ticker',
                    'data': ticker,
                    'timestamp': datetime.utcnow(),
                    'expires_at': datetime.utcnow() + timedelta(minutes=1)
                }
                await client.post(f"{database_service_url}/api/v1/cache/market-data", json=cache_data)
        except Exception as e:
            logger.warning(f"Failed to cache ticker data: {e}")
        
        return ticker
        
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, f"get_ticker({symbol})")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/market/ohlcv/{exchange}/{symbol}")
async def get_ohlcv(exchange: str, symbol: str, timeframe: str = '1h', limit: int = 100):
    """Get OHLCV data for a symbol"""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    try:
        # Check cache first
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{database_service_url}/api/v1/cache/market-data/{exchange}/{symbol}/ohlcv_{timeframe}")
                if response.status_code == 200:
                    cached_data = response.json()
                    return cached_data['data']
        except Exception:
            pass  # Continue to fetch from exchange
        
        await exchange_manager._rate_limit(exchange)
        exchange_instance = exchanges[exchange]['instance']
        
        start_time = time.time()
        ohlcv_data = await exchange_instance.fetch_ohlcv(symbol, timeframe, limit=limit)
        response_time = time.time() - start_time
        
        # Update health
        exchanges[exchange]['health']['status'] = 'healthy'
        exchanges[exchange]['health']['response_time'] = response_time
        exchanges[exchange]['health']['last_check'] = datetime.utcnow()
        
        if not ohlcv_data:
            raise HTTPException(status_code=404, detail="No OHLCV data available")
        
        # Convert to DataFrame for processing
        df = pd.DataFrame(ohlcv_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        # Cache the data
        try:
            async with httpx.AsyncClient() as client:
                cache_data = {
                    'exchange': exchange,
                    'pair': symbol,
                    'data_type': f'ohlcv_{timeframe}',
                    'data': df.to_dict('records'),
                    'timestamp': datetime.utcnow(),
                    'expires_at': datetime.utcnow() + timedelta(minutes=5)
                }
                await client.post(f"{database_service_url}/api/v1/cache/market-data", json=cache_data)
        except Exception as e:
            logger.warning(f"Failed to cache OHLCV data: {e}")
        
        return df.to_dict('records')
        
    except HTTPException:
        raise
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, f"get_ohlcv({symbol}, {timeframe})")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/market/orderbook/{exchange}/{symbol}")
async def get_order_book(exchange: str, symbol: str, limit: int = 20):
    """Get order book for a symbol"""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    try:
        await exchange_manager._rate_limit(exchange)
        exchange_instance = exchanges[exchange]['instance']
        
        start_time = time.time()
        order_book = await exchange_instance.fetch_order_book(symbol, limit)
        response_time = time.time() - start_time
        
        # Update health
        exchanges[exchange]['health']['status'] = 'healthy'
        exchanges[exchange]['health']['response_time'] = response_time
        exchanges[exchange]['health']['last_check'] = datetime.utcnow()
        
        # Cache the data
        try:
            async with httpx.AsyncClient() as client:
                cache_data = {
                    'exchange': exchange,
                    'pair': symbol,
                    'data_type': 'orderbook',
                    'data': order_book,
                    'timestamp': datetime.utcnow(),
                    'expires_at': datetime.utcnow() + timedelta(minutes=1)
                }
                await client.post(f"{database_service_url}/api/v1/cache/market-data", json=cache_data)
        except Exception as e:
            logger.warning(f"Failed to cache orderbook data: {e}")
        
        return order_book
        
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, f"get_order_book({symbol})")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/market/pairs/{exchange}")
async def get_trading_pairs(exchange: str, base_currency: str = 'USDC'):
    """Get trading pairs for an exchange"""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    try:
        await exchange_manager._rate_limit(exchange)
        exchange_instance = exchanges[exchange]['instance']
        
        start_time = time.time()
        markets = await exchange_instance.fetch_markets()
        response_time = time.time() - start_time
        
        # Update health
        exchanges[exchange]['health']['status'] = 'healthy'
        exchanges[exchange]['health']['response_time'] = response_time
        exchanges[exchange]['health']['last_check'] = datetime.utcnow()
        
        # Filter pairs by base currency
        pairs = []
        for market in markets:
            if market['quote'] == base_currency and market['active']:
                pairs.append(market['symbol'])
        
        return {"pairs": pairs, "total": len(pairs)}
        
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, "get_trading_pairs")
        raise HTTPException(status_code=500, detail=str(e))

# Trading Operations Endpoints
@app.post("/api/v1/trading/order")
async def create_order(order: Order):
    """Create a new order"""
    if not exchange_manager or order.exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {order.exchange} not found")
    
    try:
        await exchange_manager._rate_limit(order.exchange)
        exchange_instance = exchanges[order.exchange]['instance']
        
        # Prepare order parameters
        params = {}
        if order.order_type == 'market':
            params['type'] = 'market'
        elif order.order_type == 'limit':
            params['type'] = 'limit'
            if not order.price:
                raise HTTPException(status_code=400, detail="Price required for limit orders")
        
        start_time = time.time()
        result = await exchange_instance.create_order(
            symbol=order.symbol,
            type=params.get('type', 'market'),
            side=order.side,
            amount=order.amount,
            price=order.price,
            params=params
        )
        response_time = time.time() - start_time
        
        # Update health
        exchanges[order.exchange]['health']['status'] = 'healthy'
        exchanges[order.exchange]['health']['response_time'] = response_time
        exchanges[order.exchange]['health']['last_check'] = datetime.utcnow()
        
        return result
        
    except Exception as e:
        await exchange_manager._handle_exchange_error(order.exchange, e, f"create_order({order.symbol})")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/v1/trading/order/{exchange}/{order_id}")
async def cancel_order(exchange: str, order_id: str, symbol: str):
    """Cancel an order"""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    try:
        await exchange_manager._rate_limit(exchange)
        exchange_instance = exchanges[exchange]['instance']
        
        start_time = time.time()
        result = await exchange_instance.cancel_order(order_id, symbol)
        response_time = time.time() - start_time
        
        # Update health
        exchanges[exchange]['health']['status'] = 'healthy'
        exchanges[exchange]['health']['response_time'] = response_time
        exchanges[exchange]['health']['last_check'] = datetime.utcnow()
        
        return result
        
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, f"cancel_order({order_id})")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/trading/orders/{exchange}")
async def get_open_orders(exchange: str, symbol: Optional[str] = None):
    """Get open orders for an exchange"""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    try:
        await exchange_manager._rate_limit(exchange)
        exchange_instance = exchanges[exchange]['instance']
        
        start_time = time.time()
        if symbol:
            orders = await exchange_instance.fetch_open_orders(symbol)
        else:
            orders = await exchange_instance.fetch_open_orders()
        response_time = time.time() - start_time
        
        # Update health
        exchanges[exchange]['health']['status'] = 'healthy'
        exchanges[exchange]['health']['response_time'] = response_time
        exchanges[exchange]['health']['last_check'] = datetime.utcnow()
        
        return {"orders": orders}
        
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, "get_open_orders")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/trading/positions/{exchange}")
async def get_positions(exchange: str):
    """Get positions for an exchange"""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    try:
        await exchange_manager._rate_limit(exchange)
        exchange_instance = exchanges[exchange]['instance']
        
        start_time = time.time()
        positions = await exchange_instance.fetch_positions()
        response_time = time.time() - start_time
        
        # Update health
        exchanges[exchange]['health']['status'] = 'healthy'
        exchanges[exchange]['health']['response_time'] = response_time
        exchanges[exchange]['health']['last_check'] = datetime.utcnow()
        
        # Filter out zero positions
        active_positions = [pos for pos in positions if pos.get('size', 0) != 0]
        
        return {"positions": active_positions}
        
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, "get_positions")
        raise HTTPException(status_code=500, detail=str(e))

# Account Operations Endpoints
@app.get("/api/v1/account/balance/{exchange}")
async def get_balance(exchange: str):
    """Get account balance for an exchange"""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    try:
        await exchange_manager._rate_limit(exchange)
        exchange_instance = exchanges[exchange]['instance']
        
        start_time = time.time()
        balance = await exchange_instance.fetch_balance()
        response_time = time.time() - start_time
        
        # Update health
        exchanges[exchange]['health']['status'] = 'healthy'
        exchanges[exchange]['health']['response_time'] = response_time
        exchanges[exchange]['health']['last_check'] = datetime.utcnow()
        
        # Update balance in database
        try:
            async with httpx.AsyncClient() as client:
                balance_data = {
                    'exchange': exchange,
                    'total_balance': balance.get('total', {}),
                    'available_balance': balance.get('free', {}),
                    'total_pnl': 0.0,  # Will be calculated from trades
                    'daily_pnl': 0.0,  # Will be calculated from trades
                    'timestamp': datetime.utcnow()
                }
                await client.put(f"{database_service_url}/api/v1/balances/{exchange}", json=balance_data)
        except Exception as e:
            logger.warning(f"Failed to update balance in database: {e}")
        
        return balance
        
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, "get_balance")
        raise HTTPException(status_code=500, detail=str(e))

# Exchange Management Endpoints
@app.get("/api/v1/exchanges")
async def get_exchanges():
    """Get all configured exchanges"""
    return {
        "exchanges": list(exchanges.keys()),
        "total": len(exchanges)
    }

@app.get("/api/v1/exchanges/{exchange_name}/health")
async def get_exchange_health(exchange_name: str):
    """Get health status for a specific exchange"""
    if exchange_name not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange_name} not found")
    
    health = exchanges[exchange_name]['health']
    return ExchangeHealth(
        exchange=exchange_name,
        status=health['status'],
        response_time=health['response_time'],
        last_check=health['last_check'] or datetime.utcnow(),
        error_count=health['error_count']
    )

@app.get("/api/v1/exchanges/{exchange_name}/info")
async def get_exchange_info(exchange_name: str):
    """Get detailed information about an exchange"""
    if not exchange_manager or exchange_name not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange_name} not found")
    
    try:
        await exchange_manager._rate_limit(exchange_name)
        exchange_instance = exchanges[exchange_name]['instance']
        
        start_time = time.time()
        info = await exchange_instance.fetch_exchange_info()
        response_time = time.time() - start_time
        
        # Update health
        exchanges[exchange_name]['health']['status'] = 'healthy'
        exchanges[exchange_name]['health']['response_time'] = response_time
        exchanges[exchange_name]['health']['last_check'] = datetime.utcnow()
        
        return {
            "exchange": exchange_name,
            "info": info,
            "config": exchanges[exchange_name]['config'],
            "health": exchanges[exchange_name]['health']
        }
        
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange_name, e, "get_exchange_info")
        raise HTTPException(status_code=500, detail=str(e))

# Simulation Mode Endpoints
@app.post("/api/v1/simulation/order")
async def create_simulation_order(order: Order):
    """Create a simulation order (no real execution)"""
    # In simulation mode, we just return a mock order response
    mock_order = {
        "id": f"sim_{int(time.time())}",
        "symbol": order.symbol,
        "type": order.order_type,
        "side": order.side,
        "amount": order.amount,
        "price": order.price or 0,
        "status": "closed",
        "filled": order.amount,
        "remaining": 0,
        "cost": (order.price or 0) * order.amount,
        "timestamp": datetime.utcnow().isoformat(),
        "datetime": datetime.utcnow().isoformat(),
        "fee": None,
        "trades": [],
        "info": {"simulation": True}
    }
    
    return mock_order

@app.get("/api/v1/simulation/balance/{exchange}")
async def get_simulation_balance(exchange: str):
    """Get simulation balance for an exchange"""
    # Return a mock balance for simulation
    mock_balance = {
        "info": {},
        "free": {"USDC": 10000.0},
        "used": {"USDC": 0.0},
        "total": {"USDC": 10000.0},
        "timestamp": datetime.utcnow().isoformat(),
        "datetime": datetime.utcnow().isoformat(),
        "simulation": True
    }
    
    return mock_balance

# Startup and shutdown events
@app.on_event("startup")
async def startup_event():
    """Initialize exchanges on startup"""
    await initialize_exchanges()

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    global exchanges
    for exchange_name, exchange_data in exchanges.items():
        try:
            await exchange_data['instance'].close()
            logger.info(f"Closed connection to {exchange_name}")
        except Exception as e:
            logger.error(f"Error closing {exchange_name}: {e}")

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8003,
        reload=True,
        log_level="info"
    ) 