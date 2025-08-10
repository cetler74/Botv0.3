"""
Exchange Service for the Multi-Exchange Trading Bot
Multi-exchange operations and market data management
"""

import asyncio
import json
import logging
import os
import time
import websockets
from datetime import datetime
from typing import Any, Dict, List, Optional

import ccxt
import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Response
from pydantic import BaseModel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Exchange Service", version="1.0.0")

class WebSocketExchangeHandler:
    """Exchange-specific WebSocket handler"""
    
    def __init__(self, exchange_name: str, exchange_config: dict):
        self.exchange_name = exchange_name
        self.exchange_config = exchange_config
        self.websocket_url = exchange_config.get('websocket_url')
        self.api_key = exchange_config.get('api_key')
        self.secret = exchange_config.get('secret')
        self.connection = None
        self.is_connected = False
        self.last_update = None
        self.order_callbacks = {}  # order_id -> callback function
        
    async def connect(self) -> bool:
        """Establish WebSocket connection to exchange"""
        try:
            if not self.websocket_url:
                logger.warning(f"No WebSocket URL configured for {self.exchange_name}")
                return False
                
            logger.info(f"Connecting to {self.exchange_name} WebSocket: {self.websocket_url}")
            self.connection = await websockets.connect(self.websocket_url)
            self.is_connected = True
            self.last_update = datetime.utcnow().isoformat()
            
            # Start listening for messages
            asyncio.create_task(self._listen_for_messages())
            
            logger.info(f"Successfully connected to {self.exchange_name} WebSocket")
            return True
            
        except Exception as e:
            logger.error(f"Error connecting to {self.exchange_name} WebSocket: {e}")
            self.is_connected = False
            return False
    
    async def disconnect(self):
        """Disconnect from WebSocket"""
        try:
            if self.connection:
                await self.connection.close()
                self.connection = None
            self.is_connected = False
            logger.info(f"Disconnected from {self.exchange_name} WebSocket")
        except Exception as e:
            logger.error(f"Error disconnecting from {self.exchange_name} WebSocket: {e}")
    
    async def _listen_for_messages(self):
        """Listen for incoming WebSocket messages"""
        try:
            async for message in self.connection:
                await self._process_message(message)
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"WebSocket connection closed for {self.exchange_name}")
            self.is_connected = False
        except Exception as e:
            logger.error(f"Error in WebSocket message listener for {self.exchange_name}: {e}")
            self.is_connected = False
    
    async def _process_message(self, message: str):
        """Process incoming WebSocket message"""
        try:
            data = json.loads(message)
            await self._handle_order_update(data)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON message from {self.exchange_name}: {message}")
        except Exception as e:
            logger.error(f"Error processing message from {self.exchange_name}: {e}")
    
    async def _handle_order_update(self, data: dict):
        """Handle order update from WebSocket"""
        try:
            # Extract order information based on exchange format
            order_id = self._extract_order_id(data)
            status = self._extract_order_status(data)
            
            if order_id and status:
                # Update last update time
                self.last_update = datetime.utcnow().isoformat()
                
                # Notify registered callbacks
                if order_id in self.order_callbacks:
                    await self.order_callbacks[order_id](status, data)
                
                logger.info(f"Order update from {self.exchange_name}: {order_id} -> {status}")
                
        except Exception as e:
            logger.error(f"Error handling order update from {self.exchange_name}: {e}")
    
    def _extract_order_id(self, data: dict) -> Optional[str]:
        """Extract order ID from exchange-specific message format"""
        # Exchange-specific order ID extraction
        if self.exchange_name == 'binance':
            return data.get('s') or data.get('symbol')  # Binance format
        elif self.exchange_name == 'cryptocom':
            return data.get('order_id') or data.get('id')  # Crypto.com format
        elif self.exchange_name == 'bybit':
            return data.get('orderId') or data.get('order_id')  # Bybit format
        else:
            return data.get('order_id') or data.get('id') or data.get('orderId')
    
    def _extract_order_status(self, data: dict) -> Optional[str]:
        """Extract order status from exchange-specific message format"""
        # Exchange-specific status extraction
        if self.exchange_name == 'binance':
            status = data.get('X') or data.get('status')
        elif self.exchange_name == 'cryptocom':
            status = data.get('status')
        elif self.exchange_name == 'bybit':
            status = data.get('orderStatus') or data.get('status')
        else:
            status = data.get('status')
        
        # Normalize status
        if status:
            status = status.lower()
            if status in ['filled', 'complete', 'closed']:
                return 'filled'
            elif status in ['canceled', 'cancelled']:
                return 'cancelled'
            elif status in ['pending', 'new', 'open']:
                return 'pending'
        
        return status
    
    def register_order_callback(self, order_id: str, callback):
        """Register a callback for order status updates"""
        self.order_callbacks[order_id] = callback
    
    def unregister_order_callback(self, order_id: str):
        """Unregister a callback for order status updates"""
        if order_id in self.order_callbacks:
            del self.order_callbacks[order_id]
    
    def get_status(self) -> dict:
        """Get current connection status"""
        return {
            'exchange': self.exchange_name,
            'connected': self.is_connected,
            'websocket_url': self.websocket_url,
            'last_update': self.last_update,
            'registered_callbacks': len(self.order_callbacks)
        }

class WebSocketManager:
    """Manages WebSocket connections for all exchanges"""
    
    def __init__(self):
        self.handlers = {}
        self.exchange_configs = {
            'binance': {
                'websocket_url': 'wss://stream.binance.com:9443/ws/!miniTicker@arr',
                'api_key': os.getenv('BINANCE_API_KEY'),
                'secret': os.getenv('BINANCE_SECRET')
            },
            'cryptocom': {
                'websocket_url': 'wss://stream.crypto.com/v2/market',
                'api_key': os.getenv('CRYPTOCOM_API_KEY'),
                'secret': os.getenv('CRYPTOCOM_SECRET')
            },
            'bybit': {
                'websocket_url': 'wss://stream.bybit.com/v5/public/spot',
                'api_key': os.getenv('BYBIT_API_KEY'),
                'secret': os.getenv('BYBIT_SECRET')
            }
        }
    
    async def initialize_connections(self):
        """Initialize WebSocket connections for all exchanges"""
        for exchange_name, config in self.exchange_configs.items():
            handler = WebSocketExchangeHandler(exchange_name, config)
            self.handlers[exchange_name] = handler
            
            # Attempt to connect
            success = await handler.connect()
            if success:
                logger.info(f"WebSocket connection established for {exchange_name}")
            else:
                logger.warning(f"Failed to establish WebSocket connection for {exchange_name}")
    
    async def disconnect_all(self):
        """Disconnect all WebSocket connections"""
        for handler in self.handlers.values():
            await handler.disconnect()
    
    def get_handler(self, exchange_name: str) -> Optional[WebSocketExchangeHandler]:
        """Get WebSocket handler for specific exchange"""
        return self.handlers.get(exchange_name)
    
    def get_all_status(self) -> dict:
        """Get status of all WebSocket connections"""
        return {
            exchange_name: handler.get_status()
            for exchange_name, handler in self.handlers.items()
        }
    
    def register_order_callback(self, exchange_name: str, order_id: str, callback):
        """Register callback for specific exchange and order"""
        handler = self.get_handler(exchange_name)
        if handler:
            handler.register_order_callback(order_id, callback)
    
    def unregister_order_callback(self, exchange_name: str, order_id: str):
        """Unregister callback for specific exchange and order"""
        handler = self.get_handler(exchange_name)
        if handler:
            handler.unregister_order_callback(order_id)

# Global WebSocket manager
websocket_manager = WebSocketManager()

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
exchange_manager = None
config_service_url = os.getenv("CONFIG_SERVICE_URL", "http://config-service:8001")
database_service_url = os.getenv("DATABASE_SERVICE_URL", "http://database_service:8002")

async def _perform_comprehensive_sync(exchange_name: str):
    """Comprehensive sync with exchange to update database with exchange reality"""
    if not exchange_manager or exchange_name not in exchanges:
        logger.error(f"[SYNC ERROR] Exchange {exchange_name} not available for sync")
        return
    
    try:
        await exchange_manager._rate_limit(exchange_name)
        exchange_instance = exchanges[exchange_name]['instance']
        
        # 1. Sync recent orders/trades
        logger.info(f"[SYNC] Step 1: Syncing recent orders for {exchange_name}")
        
        # Get recent trades (exchange is source of truth)
        recent_trades = []
        try:
            if exchange_name == 'bybit':
                # Bybit requires symbol for fetch_my_trades, get all recent orders instead
                orders = await exchange_instance.fetch_orders(limit=50)
                for order in orders:
                    if order.get('status') == 'closed' and order.get('filled', 0) > 0:
                        # Convert order to trade format
                        recent_trades.append({
                            'id': order.get('id'),
                            'order': order.get('id'),
                            'symbol': order.get('symbol'),
                            'side': order.get('side'),
                            'amount': order.get('filled'),
                            'price': order.get('price'),
                            'cost': order.get('cost'),
                            'timestamp': order.get('timestamp'),
                            'datetime': order.get('datetime')
                        })
            elif exchange_name == 'binance':
                # Binance supports fetch_my_trades without symbol
                trades = await exchange_instance.fetch_my_trades(limit=50)
                recent_trades = trades
            else:
                # For cryptocom, try to get recent order history
                orders = await exchange_instance.fetch_orders(limit=50)
                for order in orders:
                    if order.get('status') == 'closed' and order.get('filled', 0) > 0:
                        # Convert order to trade format
                        recent_trades.append({
                            'id': order.get('id'),
                            'order': order.get('id'),
                            'symbol': order.get('symbol'),
                            'side': order.get('side'),
                            'amount': order.get('filled'),
                            'price': order.get('price'),
                            'cost': order.get('cost'),
                            'timestamp': order.get('timestamp'),
                            'datetime': order.get('datetime')
                        })
        except Exception as trades_error:
            logger.warning(f"[SYNC] Could not fetch recent trades for {exchange_name}: {trades_error}")
        
        # 2. Sync current balance (exchange is source of truth)
        logger.info(f"[SYNC] Step 2: Syncing balance for {exchange_name}")
        try:
            current_balance = await exchange_instance.fetch_balance()
            
            # Update database with actual balance
            async with httpx.AsyncClient() as client:
                # Extract base currency balance as scalar values
                base_currency = 'USD' if exchange_name == 'cryptocom' else 'USDC'
                
                if exchange_name == 'bybit':
                    # For Bybit Unified Trading Account, use the unified account totals
                    info = current_balance.get('info', {})
                    result = info.get('result', {})
                    if 'list' in result and len(result['list']) > 0:
                        account_info = result['list'][0]
                        if account_info.get('accountType') == 'UNIFIED':
                            total_balance = float(account_info.get('totalEquity', 0))
                            available_balance = float(account_info.get('totalAvailableBalance', 0))
                        else:
                            # Fallback to regular processing
                            total_balance = float(current_balance.get('total', {}).get(base_currency, 0) or 0)
                            available_balance = float(current_balance.get('free', {}).get(base_currency, 0) or total_balance)
                    else:
                        # No unified account data, use fallback
                        total_balance = float(current_balance.get('total', {}).get(base_currency, 0) or 0)
                        available_balance = float(current_balance.get('free', {}).get(base_currency, 0) or total_balance)
                else:
                    # Standard processing for other exchanges
                    total_balance = float(current_balance.get('total', {}).get(base_currency, 0) or 0)
                    available_balance = float(current_balance.get('free', {}).get(base_currency, 0) or 0)
                
                balance_data = {
                    'exchange': exchange_name,
                    'balance': total_balance,  # Fixed: use scalar float, not dict
                    'available_balance': available_balance,  # Fixed: use scalar float, not dict
                    'total_pnl': 0.0,  # Will be calculated from trades
                    'daily_pnl': 0.0,  # Will be calculated from trades
                    'timestamp': datetime.utcnow().isoformat()
                }
                response = await client.put(f"{database_service_url}/api/v1/balances/{exchange_name}", json=balance_data)
                if response.status_code == 200:
                    logger.info(f"[SYNC] Balance updated for {exchange_name}: total={total_balance}, available={available_balance}")
                else:
                    logger.error(f"[SYNC] Failed to update balance for {exchange_name}: HTTP {response.status_code} - {response.text}")
                
        except Exception as balance_error:
            logger.warning(f"[SYNC] Could not sync balance for {exchange_name}: {balance_error}")
        
        # 3. Sync positions/open orders (exchange is source of truth)
        logger.info(f"[SYNC] Step 3: Syncing open positions for {exchange_name}")
        try:
            open_orders = await exchange_instance.fetch_open_orders()
            
            # Send comprehensive sync data to database service
            async with httpx.AsyncClient() as client:
                comprehensive_sync_data = {
                    'exchange': exchange_name,
                    'recent_trades': recent_trades[-20:],  # Last 20 trades
                    'current_balance': current_balance,
                    'open_orders': open_orders,
                    'sync_timestamp': datetime.utcnow().isoformat(),
                    'sync_type': 'comprehensive_post_order'
                }
                response = await client.post(f"{database_service_url}/api/v1/trades/comprehensive-sync", json=comprehensive_sync_data)
                if response.status_code == 200:
                    sync_result = response.json()
                    logger.info(f"[SYNC SUCCESS] {exchange_name}: {sync_result}")
                else:
                    logger.warning(f"[SYNC] Database sync returned {response.status_code} for {exchange_name}")
                    
        except Exception as orders_error:
            logger.warning(f"[SYNC] Could not sync open orders for {exchange_name}: {orders_error}")
            
    except Exception as e:
        logger.error(f"[SYNC ERROR] Comprehensive sync failed for {exchange_name}: {e}")
        raise

class ExchangeManager:
    """Manages multiple exchange connections and operations"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._initialize_exchanges()
        
    def _initialize_exchanges(self) -> None:
        """Initialize exchange connections"""
        global exchanges
        exchange_configs = self.config.get('exchanges', {})
        
        for exchange_name, config in exchange_configs.items():
            try:
                # Map exchange names to CCXT exchange classes
                exchange_class_map = {
                    'binance': ccxt.binance,
                    'cryptocom': ccxt.cryptocom,
                    'bybit': ccxt.bybit
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
                    'rateLimit': 500,  # 500ms between requests (more conservative)
                    'timeout': 60000,  # 60 seconds (increased timeout)
                }
                
                # Add Bybit-specific configuration for market buy orders
                if exchange_name == 'bybit':
                    exchange_config['options'] = {
                        'createMarketBuyOrderRequiresPrice': False  # Allow market buy orders without price
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
                    'timestamp': datetime.utcnow().isoformat(),
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
            response = await client.get(f"{config_service_url}/api/v1/config/exchanges/internal")
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
        print("[DEBUG] BINANCE KEY (FULL):", repr(config.get('binance', {}).get('api_key')))
        print("[DEBUG] BYBIT KEY (FULL):", repr(config.get('bybit', {}).get('api_key')))
        exchange_manager = ExchangeManager({'exchanges': config})
        logger.info("Exchange service initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize exchange service: {e}")
        raise

def get_binance_balance_direct():
    api_key = os.environ.get("EXCHANGE_BINANCE_API_KEY", "").strip()
    api_secret = os.environ.get("EXCHANGE_BINANCE_API_SECRET", "").strip()
    logger.info(f"[DEBUG] BINANCE KEY (from env): {repr(api_key)}")
    logger.info(f"[DEBUG] BINANCE SECRET (from env): {repr(api_secret)}")
    base_url = "https://api.binance.com"
    endpoint = "/api/v3/account"
    timestamp = int(time.time() * 1000)
    query_string = f"timestamp={timestamp}"
    signature = hmac.new(
        api_secret.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    url = f"{base_url}{endpoint}?{query_string}&signature={signature}"
    headers = {"X-MBX-APIKEY": api_key}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()

# API Endpoints
@app.get("/metrics")
async def get_metrics():
    """Prometheus metrics endpoint"""
    return Response(generate_latest(exchange_registry), media_type=CONTENT_TYPE_LATEST)

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
        # Convert symbol format for different exchanges
        exchange_symbol = symbol
        if exchange == 'cryptocom':
            # Crypto.com needs symbols with slashes, convert from BTCUSD to BTC/USD
            if len(symbol) >= 6 and symbol.endswith('USD'):
                base = symbol[:-3]
                exchange_symbol = f"{base}/USD"
            elif len(symbol) >= 6 and symbol.endswith('USDC'):
                base = symbol[:-4]
                exchange_symbol = f"{base}/USDC"
            
            # Handle complex symbols with colons (like 1INCHUSD -> 1INCH/USD:USD)
            if ':' not in exchange_symbol and len(exchange_symbol) >= 8:
                # Try to find the base currency part
                if exchange_symbol.endswith('/USD'):
                    base_part = exchange_symbol[:-4]
                    # For complex symbols, try the format with :USD suffix
                    exchange_symbol = f"{base_part}/USD:USD"
        elif exchange in ['binance', 'bybit']:
            # Binance and Bybit need symbols with slashes, convert from BTCUSD to BTC/USD
            if len(symbol) >= 6 and symbol.endswith('USD'):
                base = symbol[:-3]
                exchange_symbol = f"{base}/USD"
            elif len(symbol) >= 6 and symbol.endswith('USDC'):
                base = symbol[:-4]
                exchange_symbol = f"{base}/USDC"
        
        await exchange_manager._rate_limit(exchange)
        exchange_instance = exchanges[exchange]['instance']
        
        # Retry logic with exponential backoff
        max_retries = 3
        retry_delay = 1  # Start with 1 second
        ticker = None
        
        for attempt in range(max_retries):
            try:
                start_time = time.time()
                ticker = await exchange_instance.fetch_ticker(exchange_symbol)
                response_time = time.time() - start_time
                break  # Success, exit retry loop
            except Exception as e:
                if attempt == max_retries - 1:  # Last attempt
                    raise e  # Re-raise the exception
                    
                logger.warning(f"Ticker fetch attempt {attempt + 1} failed for {exchange_symbol} on {exchange}: {e}. Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
                await exchange_manager._rate_limit(exchange)  # Apply rate limit between retries
        
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
                    'timestamp': datetime.utcnow().isoformat(),
                    'expires_at': (datetime.utcnow() + timedelta(minutes=1)).isoformat()
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
        # Convert symbol format for different exchanges
        exchange_symbol = symbol
        if exchange == 'cryptocom':
            # Crypto.com needs symbols with slashes, convert from BTCUSD to BTC/USD
            if len(symbol) >= 6 and symbol.endswith('USD'):
                base = symbol[:-3]
                exchange_symbol = f"{base}/USD"
            elif len(symbol) >= 6 and symbol.endswith('USDC'):
                base = symbol[:-4]
                exchange_symbol = f"{base}/USDC"
            
            # Handle complex symbols with colons (like 1INCHUSD -> 1INCH/USD:USD)
            if ':' not in exchange_symbol and len(exchange_symbol) >= 8:
                # Try to find the base currency part
                if exchange_symbol.endswith('/USD'):
                    base_part = exchange_symbol[:-4]
                    # For complex symbols, try the format with :USD suffix
                    exchange_symbol = f"{base_part}/USD:USD"
        

        
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
        
        # Retry logic with exponential backoff
        max_retries = 3
        retry_delay = 1  # Start with 1 second
        ohlcv_data = None
        
        for attempt in range(max_retries):
            try:
                start_time = time.time()
                ohlcv_data = await exchange_instance.fetch_ohlcv(exchange_symbol, timeframe, limit=limit)
                response_time = time.time() - start_time
                break  # Success, exit retry loop
            except Exception as e:
                if attempt == max_retries - 1:  # Last attempt
                    raise e  # Re-raise the exception
                    
                logger.warning(f"OHLCV fetch attempt {attempt + 1} failed for {exchange_symbol} on {exchange}: {e}. Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
                await exchange_manager._rate_limit(exchange)  # Apply rate limit between retries
        
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
                # Convert timestamps to ISO format strings for JSON serialization
                df_cache = df.copy()
                df_cache['timestamp'] = df_cache['timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                
                cache_data = {
                    'exchange': exchange,
                    'pair': symbol,
                    'data_type': f'ohlcv_{timeframe}',
                    'data': df_cache.to_dict('records'),
                    'timestamp': datetime.utcnow().isoformat(),
                    'expires_at': (datetime.utcnow() + timedelta(minutes=5)).isoformat()
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
                    'timestamp': datetime.utcnow().isoformat(),
                    'expires_at': (datetime.utcnow() + timedelta(minutes=1)).isoformat()
                }
                await client.post(f"{database_service_url}/api/v1/cache/market-data", json=cache_data)
        except Exception as e:
            logger.warning(f"Failed to cache orderbook data: {e}")
        
        return order_book
        
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, f"get_order_book({symbol})")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/market/pairs/{exchange}")
async def get_trading_pairs(exchange: str, base_currency: Optional[str] = None):
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    try:
        await exchange_manager._rate_limit(exchange)
        exchange_instance = exchanges[exchange]['instance']
        
        # Get base currency from config if not provided
        if base_currency is None:
            base_currency = exchanges[exchange]['config'].get('base_currency', 'USDC')
        
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
            # For Bybit market buy orders, set the parameter to not require price
            if order.exchange.lower() == 'bybit' and order.side == 'buy':
                params['createMarketBuyOrderRequiresPrice'] = False
        elif order.order_type == 'limit':
            params['type'] = 'limit'
            if not order.price:
                raise HTTPException(status_code=400, detail="Price required for limit orders")
        
        # Get current balance for debugging
        try:
            current_balance = await exchange_instance.fetch_balance()
            base_currency = order.symbol.split('/')[1] if '/' in order.symbol else 'USD'
            quote_currency = order.symbol.split('/')[0] if '/' in order.symbol else order.symbol[:3]
            
            # Handle Bybit unified account structure where 'free' can be None
            free_balances = current_balance.get('free', {})
            total_balances = current_balance.get('total', {})
            
            # For Bybit unified accounts, use total balance when free is None
            base_free = free_balances.get(base_currency)
            quote_free = free_balances.get(quote_currency)
            
            if base_free is None and order.exchange.lower() == 'bybit':
                available_base = float(total_balances.get(base_currency, 0) or 0)
            else:
                available_base = float(base_free or 0)
                
            if quote_free is None and order.exchange.lower() == 'bybit':
                available_quote = float(total_balances.get(quote_currency, 0) or 0)
            else:
                available_quote = float(quote_free or 0)
            
            logger.info(f"[BALANCE DEBUG] {order.exchange} - Available balances:")
            logger.info(f"  Base currency ({base_currency}): {available_base}")
            logger.info(f"  Quote currency ({quote_currency}): {available_quote}")
        except Exception as balance_error:
            logger.warning(f"Could not fetch balance for debugging: {balance_error}")
        
        # Log order details for debugging
        logger.info(f"[ORDER DEBUG] {order.exchange} - Creating order:")
        logger.info(f"  Symbol: {order.symbol}")
        logger.info(f"  Type: {params.get('type', 'market')}")
        logger.info(f"  Side: {order.side}")
        logger.info(f"  Amount: {order.amount}")
        logger.info(f"  Price: {order.price}")
        logger.info(f"  Params: {params}")
        
        # Get current price for notional validation
        current_price = None
        try:
            ticker = await exchange_instance.fetch_ticker(order.symbol)
            current_price = ticker.get('last') or ticker.get('close')
        except Exception as price_error:
            logger.warning(f"Could not fetch current price for {order.symbol}: {price_error}")
        
        # Calculate required balance and validate
        if order.side == 'buy':
            # For market buy orders, need to determine what the amount represents
            if order.order_type == 'market':
                # For market buy orders, amount is typically the quantity to buy (not cost to spend)
                # Calculate required balance based on current price
                if current_price and current_price > 0:
                    required_amount = order.amount * current_price  # quantity * price = cost
                    logger.info(f"  Market BUY: {order.amount} {quote_currency} at ~${current_price:.6f} = ${required_amount:.2f} {base_currency}")
                else:
                    # Fallback: assume amount is cost to spend (old behavior)
                    required_amount = order.amount
                    logger.warning(f"  Market BUY: No current price available, assuming amount is cost to spend: ${required_amount:.2f} {base_currency}")
            else:
                required_amount = order.amount * order.price  # Calculate cost for limit orders
                
            logger.info(f"  Required balance for {order.order_type.upper()} BUY: {required_amount} {base_currency}")
            
            if available_base < required_amount:
                error_msg = f"Insufficient balance for BUY order: need {required_amount} {base_currency}, have {available_base}"
                logger.error(f"[BALANCE ERROR] {error_msg}")
                raise HTTPException(status_code=400, detail=error_msg)
        else:
            logger.info(f"  Required balance for SELL: {order.amount} {quote_currency}")
            
            # Validate minimum amount precision for sell orders
            min_amounts = {
                'cryptocom': {
                    'AAVE/USD': 0.001,
                    'default': 0.000001
                },
                'binance': {
                    'default': 0.000001  
                },
                'bybit': {
                    'default': 0.000001
                }
            }
            
            exchange_min_amounts = min_amounts.get(order.exchange.lower(), {})
            min_amount = exchange_min_amounts.get(order.symbol, exchange_min_amounts.get('default', 0.000001))
            
            if order.amount < min_amount:
                error_msg = f"{order.exchange} amount of {order.symbol} must be greater than minimum amount precision of {min_amount}"
                logger.error(f"[AMOUNT PRECISION ERROR] {error_msg}")
                raise HTTPException(status_code=400, detail=error_msg)
            
            # Validate notional value for sell orders
            if current_price and order.exchange == 'binance':
                notional_value = order.amount * current_price
                min_notional = 5.0  # Binance minimum notional is $5 USD
                if notional_value < min_notional:
                    error_msg = f"Order notional too small: ${notional_value:.2f} < ${min_notional} minimum for {order.exchange}"
                    logger.error(f"[NOTIONAL ERROR] {error_msg}")
                    raise HTTPException(status_code=400, detail=error_msg)
            
            if available_quote < order.amount:
                error_msg = f"Insufficient balance for SELL order: need {order.amount} {quote_currency}, have {available_quote}"
                logger.error(f"[BALANCE ERROR] {error_msg}")
                
                # Try to sync order status before failing
                logger.info(f"[SYNC] Attempting to sync order status for {order.exchange} before failing...")
                try:
                    # Get open orders to check if there are pending orders we don't know about
                    open_orders = await exchange_instance.fetch_open_orders()
                    logger.info(f"[SYNC] Found {len(open_orders)} open orders on {order.exchange}")
                    
                    # Get recent order history to see if orders were filled
                    try:
                        order_history = await exchange_instance.fetch_orders(limit=20)
                        filled_orders = [o for o in order_history if o.get('status') == 'closed' and o.get('filled', 0) > 0]
                        logger.info(f"[SYNC] Found {len(filled_orders)} recently filled orders")
                        
                        # Log the filled orders for database sync
                        for filled_order in filled_orders[-5:]:  # Last 5 filled orders
                            logger.info(f"[SYNC] Filled order: {filled_order.get('id')} - {filled_order.get('symbol')} {filled_order.get('side')} {filled_order.get('filled')}")
                    except Exception as history_error:
                        logger.warning(f"Could not fetch order history: {history_error}")
                        
                except Exception as sync_error:
                    logger.warning(f"Could not sync order status: {sync_error}")
                
                raise HTTPException(status_code=400, detail=error_msg)
        
        start_time = time.time()
        try:
            # Sanitize all numeric inputs to prevent decimal conversion errors
            sanitized_amount = None
            sanitized_price = None
            
            if order.amount is not None:
                # Clean the amount to prevent decimal conversion issues
                try:
                    sanitized_amount = float(f"{float(order.amount):.10g}")  # Use g format to remove trailing zeros
                    logger.info(f"[NUMBER SANITIZE] Amount: {order.amount} -> {sanitized_amount}")
                except (ValueError, TypeError) as e:
                    logger.error(f"[NUMBER SANITIZE ERROR] Could not sanitize amount {order.amount}: {e}")
                    sanitized_amount = float(order.amount)
            
            if order.price is not None:
                try:
                    sanitized_price = float(f"{float(order.price):.10g}")
                    logger.info(f"[NUMBER SANITIZE] Price: {order.price} -> {sanitized_price}")
                except (ValueError, TypeError) as e:
                    logger.error(f"[NUMBER SANITIZE ERROR] Could not sanitize price {order.price}: {e}")
                    sanitized_price = float(order.price)
            
            # Special handling for Bybit market buy orders
            if order.exchange.lower() == 'bybit' and order.side == 'buy' and order.order_type == 'market':
                # For Bybit unified accounts, market buy orders should use quoteOrderQty
                # Convert amount (quantity) to cost (USDC to spend)
                if current_price and current_price > 0 and sanitized_amount and sanitized_amount > 0:
                    try:
                        # Defensive calculation with validation
                        cost_to_spend = float(sanitized_amount) * float(current_price)
                        # Ensure proper decimal formatting for Bybit API (round to reasonable precision)
                        cost_to_spend = round(cost_to_spend, 8)  # Round to 8 decimal places
                        
                        # Validate the result before using it
                        if cost_to_spend <= 0 or not isinstance(cost_to_spend, (int, float)):
                            raise ValueError(f"Invalid cost calculation: {cost_to_spend}")
                            
                        logger.info(f"[BYBIT MARKET BUY] Converting {sanitized_amount} {quote_currency} to cost: ${cost_to_spend:.2f} {base_currency}")
                        
                        # Use quoteOrderQty parameter for Bybit - ensure it's a clean number
                        params['quoteOrderQty'] = float(cost_to_spend)
                    except (ValueError, TypeError, ArithmeticError) as calc_error:
                        logger.error(f"[BYBIT COST CALCULATION ERROR] Failed to calculate cost: {calc_error}")
                        # Use fixed amount to avoid calculation issues
                        params['quoteOrderQty'] = 10.0  # Fixed $10 order
                        logger.info(f"[BYBIT FALLBACK] Using fixed $10 order due to calculation error")
                    
                    result = await exchange_instance.create_order(
                        symbol=order.symbol,
                        type=params.get('type', 'market'),
                        side=order.side,
                        amount=None,  # Don't specify amount when using quoteOrderQty
                        price=sanitized_price,
                        params=params
                    )
                else:
                    # Fallback to original method if no current price
                    logger.warning(f"[BYBIT MARKET BUY] No current price, using original method")
                    result = await exchange_instance.create_order(
                        symbol=order.symbol,
                        type=params.get('type', 'market'),
                        side=order.side,
                        amount=sanitized_amount,
                        price=sanitized_price,
                        params=params
                    )
            else:
                # Standard order creation for other exchanges or non-market orders
                result = await exchange_instance.create_order(
                    symbol=order.symbol,
                    type=params.get('type', 'market'),
                    side=order.side,
                    amount=sanitized_amount,
                    price=sanitized_price,
                    params=params
                )
        except Exception as order_error:
            error_str = str(order_error)
            error_type = type(order_error).__name__
            logger.error(f"[ORDER CREATION ERROR] Detailed error for {order.exchange}: {error_type}: {error_str}")
            
            # Debug logging for decimal error detection
            logger.info(f"[DEBUG] Checking for decimal errors in: '{error_str}'")
            logger.info(f"[DEBUG] ConversionSyntax in string: {'ConversionSyntax' in error_str}")
            logger.info(f"[DEBUG] InvalidOperation in string: {'InvalidOperation' in error_str}")
            
            # Special handling for decimal conversion errors
            if "ConversionSyntax" in error_str or "InvalidOperation" in error_str:
                logger.warning(f"[DECIMAL ERROR RECOVERY] Attempting to fix decimal conversion error for {order.symbol}")
                
                # Try with more aggressive number sanitization
                try:
                    # Ultra-sanitize the amount and price
                    ultra_sanitized_amount = None
                    ultra_sanitized_price = None
                    
                    if order.amount is not None:
                        # Convert to string, then back to float to eliminate precision issues
                        amount_str = f"{float(order.amount):.8f}".rstrip('0').rstrip('.')
                        ultra_sanitized_amount = float(amount_str)
                        logger.info(f"[ULTRA SANITIZE] Amount: {order.amount} -> {ultra_sanitized_amount}")
                    
                    if order.price is not None:
                        price_str = f"{float(order.price):.8f}".rstrip('0').rstrip('.')
                        ultra_sanitized_price = float(price_str)
                        logger.info(f"[ULTRA SANITIZE] Price: {order.price} -> {ultra_sanitized_price}")
                    
                    # Retry the order with ultra-sanitized values
                    logger.info(f"[DECIMAL RECOVERY] Retrying order with ultra-sanitized values")
                    
                    if order.exchange.lower() == 'bybit' and order.side == 'buy' and order.order_type == 'market':
                        # For Bybit decimal errors, use quoteOrderQty approach to avoid amount precision issues
                        # Use fixed cost to completely avoid any decimal calculations
                        target_cost = 10.0  # Fixed $10 order (minimum for Bybit)
                        
                        logger.info(f"[BYBIT DECIMAL FIX] Using quoteOrderQty: ${target_cost} instead of amount calculation")
                        logger.info(f"[BYBIT DECIMAL FIX] This avoids decimal precision issues entirely")
                        
                        # Create completely clean params with only essential values
                        clean_params = {
                            'type': 'market',
                            'quoteOrderQty': target_cost  # Use fixed clean value
                        }
                        
                        # Use None for amount when using quoteOrderQty
                        result = await exchange_instance.create_order(
                            symbol=order.symbol,
                            type='market',
                            side=order.side,
                            amount=None,  # Don't specify amount when using quoteOrderQty
                            price=None,   # Don't specify price for market orders
                            params=clean_params
                        )
                    else:
                        # Standard retry with ultra-sanitized values for non-Bybit exchanges
                        result = await exchange_instance.create_order(
                            symbol=order.symbol,
                            type=params.get('type', 'market'),
                            side=order.side,
                            amount=ultra_sanitized_amount,
                            price=ultra_sanitized_price,
                            params=params
                        )
                    
                    logger.info(f"[DECIMAL RECOVERY] Successfully recovered from decimal error: {result.get('id', 'N/A')}")
                    
                except Exception as recovery_error:
                    logger.error(f"[DECIMAL RECOVERY FAILED] Could not recover from decimal error: {recovery_error}")
                    # Import traceback for detailed error logging
                    import traceback
                    logger.error(f"[ORDER CREATION TRACEBACK] {traceback.format_exc()}")
                    raise order_error
            else:
                # Import traceback for detailed error logging
                import traceback
                logger.error(f"[ORDER CREATION TRACEBACK] {traceback.format_exc()}")
                raise order_error
        
        response_time = time.time() - start_time
        
        logger.info(f"[ORDER SUCCESS] {order.exchange} - Order created successfully: {result.get('id', 'N/A')}")
        
        # Update health
        exchanges[order.exchange]['health']['status'] = 'healthy'
        exchanges[order.exchange]['health']['response_time'] = response_time
        exchanges[order.exchange]['health']['last_check'] = datetime.utcnow()
        
        # Automatic post-order sync - ensure database reflects exchange reality
        # CRITICAL FIX: Post-order sync failures should NOT cause successful orders to fail
        sync_success = False
        max_sync_retries = 3
        
        try:
            for retry_attempt in range(max_sync_retries):
                try:
                    logger.info(f"[POST-ORDER SYNC] Starting automatic sync for {order.exchange} after order {result.get('id', 'N/A')} (attempt {retry_attempt + 1}/{max_sync_retries})")
                    await _perform_comprehensive_sync(order.exchange)
                    sync_success = True
                    logger.info(f"[POST-ORDER SYNC SUCCESS] Successfully synced {order.exchange} after order {result.get('id', 'N/A')}")
                    break
                except Exception as sync_error:
                    if retry_attempt == max_sync_retries - 1:
                        # Last attempt failed - log critical error but don't fail the order
                        logger.error(f"[POST-ORDER SYNC CRITICAL] FAILED ALL {max_sync_retries} sync attempts for {order.exchange} after order {result.get('id', 'N/A')}: {sync_error}")
                        
                        # Create critical alert in database (non-blocking)
                        try:
                            async with httpx.AsyncClient() as alert_client:
                                alert_data = {
                                    'alert_id': f"sync_failure_{order.exchange}_{result.get('id', 'unknown')}_{int(time.time())}",
                                    'level': 'CRITICAL',
                                    'category': 'ORDER_SYNC',
                                    'message': f"Order {result.get('id')} placed on {order.exchange} but failed to sync with database after {max_sync_retries} attempts",
                                    'exchange': order.exchange,
                                    'details': {
                                        'order_id': result.get('id'),
                                        'symbol': order.symbol,
                                        'side': order.side,
                                        'amount': order.amount,
                                        'sync_error': str(sync_error),
                                        'timestamp': datetime.utcnow().isoformat()
                                    },
                                    'timestamp': datetime.utcnow().isoformat(),
                                    'resolved': False
                                }
                                await alert_client.post(f"{database_service_url}/api/v1/alerts", json=alert_data)
                        except Exception as alert_error:
                            logger.error(f"Failed to create sync failure alert: {alert_error}")
                    else:
                        logger.warning(f"[POST-ORDER SYNC RETRY] Sync attempt {retry_attempt + 1} failed for {order.exchange}: {sync_error}. Retrying...")
                        await asyncio.sleep(1)  # Wait 1 second before retry
        except Exception as sync_critical_error:
            # Even if sync completely fails, the order was successful - just log and continue
            logger.error(f"[POST-ORDER SYNC FATAL] Complete sync failure for {order.exchange} after successful order {result.get('id', 'N/A')}: {sync_critical_error}")
            sync_success = False
        
        # Add sync status to order result (for debugging)
        result['sync_status'] = 'success' if sync_success else 'failed'
        result['sync_attempts'] = max_sync_retries if not sync_success else retry_attempt + 1
        
        # CRITICAL: Always return successful order result, regardless of sync status
        logger.info(f"[ORDER FINAL SUCCESS] {order.exchange} - Order {result.get('id', 'N/A')} completed successfully (sync_status: {result['sync_status']})")
        return result
        
    except HTTPException:
        # Re-raise HTTP exceptions (like 400 for insufficient balance) as-is
        raise
    except Exception as e:
        error_message = str(e).lower()
        
        # Check if this is a balance-related error from the exchange
        if any(keyword in error_message for keyword in ['insufficient', 'balance', 'funds', 'account has insufficient']):
            logger.error(f"[BALANCE ERROR] {order.exchange} - Insufficient balance on exchange:")
            logger.error(f"  Symbol: {order.symbol}")
            logger.error(f"  Side: {order.side}")
            logger.error(f"  Amount: {order.amount}")
            logger.error(f"  Error: {str(e)}")
            await exchange_manager._handle_exchange_error(order.exchange, e, f"create_order({order.symbol})")
            raise HTTPException(status_code=400, detail=f"Insufficient balance: {str(e)}")
        
        # For all other errors, log and return 500
        logger.error(f"[ORDER FAILED] {order.exchange} - Order failed:")
        logger.error(f"  Symbol: {order.symbol}")
        logger.error(f"  Type: {order.order_type}")
        logger.error(f"  Side: {order.side}")
        logger.error(f"  Amount: {order.amount}")
        logger.error(f"  Price: {order.price}")
        logger.error(f"  Error: {str(e)}")
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

@app.get("/api/v1/trading/filled-orders/{exchange}")
async def get_filled_orders(exchange: str, symbol: Optional[str] = None):
    """Get recently filled (closed) orders for an exchange"""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    try:
        await exchange_manager._rate_limit(exchange)
        exchange_instance = exchanges[exchange]['instance']
        filled_orders = []
        if symbol:
            order_history = await exchange_instance.fetch_orders(symbol, limit=50)
        else:
            order_history = await exchange_instance.fetch_orders(limit=50)
        for order in order_history:
            if order.get('status') == 'closed' and order.get('filled', 0) > 0:
                filled_orders.append(order)
        return {"orders": filled_orders}
    except Exception as e:
        logger.error(f"Error fetching filled orders for {exchange}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Account Operations Endpoints
@app.get("/api/v1/account/balance/{exchange}")
async def get_balance(exchange: str):
    logger.info(f"[DEBUG] Received balance request for {exchange}")
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    try:
        await exchange_manager._rate_limit(exchange)
        exchange_instance = exchanges[exchange]['instance']
        
        # Retry logic with exponential backoff
        max_retries = 3
        retry_delay = 1  # Start with 1 second
        balance = None
        
        for attempt in range(max_retries):
            try:
                start_time = time.time()
                balance = await exchange_instance.fetch_balance()
                logger.info(f"[BALANCE RAW] {exchange} - Full balance response: {balance}")
                response_time = time.time() - start_time
                break  # Success, exit retry loop
            except Exception as e:
                if attempt == max_retries - 1:  # Last attempt
                    raise e  # Re-raise the exception
                    
                logger.warning(f"Balance fetch attempt {attempt + 1} failed for {exchange}: {e}. Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
                await exchange_manager._rate_limit(exchange)  # Apply rate limit between retries
        
        # Update health
        exchanges[exchange]['health']['status'] = 'healthy'
        exchanges[exchange]['health']['response_time'] = response_time
        exchanges[exchange]['health']['last_check'] = datetime.utcnow()
        
        # Update balance in database
        try:
            async with httpx.AsyncClient() as client:
                # Extract base currency balance as scalar values
                base_currency = 'USD' if exchange == 'cryptocom' else 'USDC'
                
                # Special handling for Bybit Unified Trading Account
                if exchange == 'bybit':
                    # For Bybit Unified Trading Account, use the unified account totals in USD
                    info = balance.get('info', {})
                    result = info.get('result', {})
                    if 'list' in result and len(result['list']) > 0:
                        account_info = result['list'][0]
                        if account_info.get('accountType') == 'UNIFIED':
                            total_balance = float(account_info.get('totalEquity', 0))
                            available_balance = float(account_info.get('totalAvailableBalance', 0))
                            logger.debug(f"[{exchange}] Using Unified Trading Account: total=${total_balance:.2f}, available=${available_balance:.2f}")
                        else:
                            # Fallback to regular processing
                            total_balance = float(balance.get('total', {}).get(base_currency, 0) or 0)
                            available_balance = float(balance.get('free', {}).get(base_currency, 0) or total_balance)
                    else:
                        # No unified account data, use fallback
                        total_balance = float(balance.get('total', {}).get(base_currency, 0) or 0)
                        available_balance = float(balance.get('free', {}).get(base_currency, 0) or total_balance)
                else:
                    # Standard processing for other exchanges
                    total_balance = float(balance.get('total', {}).get(base_currency, 0) or 0)
                    
                    # Handle cases where free balance is None/null
                    free_balance = balance.get('free', {}).get(base_currency, 0)
                    if free_balance is None:
                        # Fallback: use total balance as available if free is not provided
                        available_balance = total_balance
                        logger.debug(f"[{exchange}] Free balance is None, using total balance as available: {available_balance}")
                    else:
                        available_balance = float(free_balance or 0)
                
                balance_data = {
                    'exchange': exchange,
                    'balance': total_balance,  # Fixed: use scalar float, not dict
                    'available_balance': available_balance,  # Fixed: use scalar float, not dict
                    'total_pnl': 0.0,  # Will be calculated from trades
                    'daily_pnl': 0.0,  # Will be calculated from trades
                    'timestamp': datetime.utcnow().isoformat()
                }
                response = await client.put(f"{database_service_url}/api/v1/balances/{exchange}", json=balance_data)
                if response.status_code == 200:
                    logger.info(f"Successfully updated balance for {exchange} in database: total={total_balance}, available={available_balance}")
                else:
                    logger.error(f"Failed to update balance for {exchange}: HTTP {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"Failed to update balance for {exchange} in database: {e}")
        
        # Return processed balance structure instead of raw balance
        processed_balance = dict(balance)  # Copy original structure
        
        # Update the processed balance with correct available values
        if exchange == 'bybit':
            # For Bybit, ensure the free balance reflects the processed available balance
            info = balance.get('info', {})
            result = info.get('result', {})
            if 'list' in result and len(result['list']) > 0:
                account_info = result['list'][0]
                if account_info.get('accountType') == 'UNIFIED':
                    available_balance = float(account_info.get('totalAvailableBalance', 0))
                    total_balance = float(account_info.get('totalEquity', 0))
                    
                    # Update the free balance structure to reflect actual available balance
                    if 'free' not in processed_balance:
                        processed_balance['free'] = {}
                    processed_balance['free']['USDC'] = available_balance
                    
                    # Also update the top-level available field if it exists
                    processed_balance['available'] = available_balance
                    
                    logger.info(f"[{exchange}] Processed Bybit balance: total={total_balance:.2f}, available={available_balance:.2f}")
        
        return processed_balance
        
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, "get_balance")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/account/balance/binance")
async def get_balance_binance():
    logger.info("[DEBUG] Using direct Binance balance handler")
    try:
        balance = get_binance_balance_direct()
        return balance
    except Exception as e:
        logger.error(f"Direct Binance balance error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Order Synchronization Endpoints
@app.post("/api/v1/trading/sync-orders/{exchange}")
async def sync_orders(exchange: str, symbol: str = None):
    """Sync order status with exchange to update database"""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    try:
        await exchange_manager._rate_limit(exchange)
        exchange_instance = exchanges[exchange]['instance']
        
        # Get recent order history - some exchanges require symbol
        filled_orders = []
        
        if exchange in ['binance', 'bybit']:
            # These exchanges require symbol for fetchOrders, so get all recent orders via different method
            try:
                # Try to get all orders using fetchMyTrades instead
                trades = await exchange_instance.fetch_my_trades(limit=100)
                
                # Group trades by order ID to reconstruct orders
                order_groups = {}
                for trade in trades:
                    order_id = trade.get('order')
                    if order_id:
                        if order_id not in order_groups:
                            order_groups[order_id] = {
                                'id': order_id,
                                'symbol': trade.get('symbol'),
                                'side': trade.get('side'),
                                'amount': 0,
                                'filled': 0,
                                'cost': 0,
                                'timestamp': trade.get('timestamp'),
                                'status': 'closed'
                            }
                        order_groups[order_id]['filled'] += trade.get('amount', 0)
                        order_groups[order_id]['amount'] += trade.get('amount', 0)
                        order_groups[order_id]['cost'] += trade.get('cost', 0)
                        if trade.get('price'):
                            order_groups[order_id]['price'] = trade.get('price')
                
                filled_orders = list(order_groups.values())
                
            except Exception as trades_error:
                logger.warning(f"Could not fetch trades for {exchange}: {trades_error}")
                # Fallback: try to get orders for common symbols
                common_symbols = ['BTC/USDT', 'ETH/USDT', 'BTC/USD', 'ETH/USD', 'ADA/USD', 'SOL/USD']
                
                for sym in common_symbols:
                    try:
                        orders = await exchange_instance.fetch_orders(sym, limit=10)
                        for order in orders:
                            if order.get('status') == 'closed' and order.get('filled', 0) > 0:
                                filled_orders.append({
                                    'id': order.get('id'),
                                    'symbol': order.get('symbol'),
                                    'side': order.get('side'),
                                    'amount': order.get('amount'),
                                    'filled': order.get('filled'),
                                    'price': order.get('price'),
                                    'cost': order.get('cost'),
                                    'timestamp': order.get('timestamp'),
                                    'status': order.get('status')
                                })
                    except Exception as sym_error:
                        logger.debug(f"Could not fetch orders for {sym} on {exchange}: {sym_error}")
                        continue
                        
        else:
            # For exchanges like cryptocom that support fetching all orders
            order_history = await exchange_instance.fetch_orders(limit=50)
            
            for order in order_history:
                if order.get('status') == 'closed' and order.get('filled', 0) > 0:
                    filled_orders.append({
                        'id': order.get('id'),
                        'symbol': order.get('symbol'),
                        'side': order.get('side'),
                        'amount': order.get('amount'),
                        'filled': order.get('filled'),
                        'price': order.get('price'),
                        'cost': order.get('cost'),
                        'timestamp': order.get('timestamp'),
                        'status': order.get('status')
                    })
        
        # Send to database service for updating
        try:
            async with httpx.AsyncClient() as client:
                sync_data = {
                    'exchange': exchange,
                    'filled_orders': filled_orders,
                    'timestamp': datetime.utcnow().isoformat()
                }
                response = await client.post(f"{database_service_url}/api/v1/trades/sync-orders", json=sync_data)
                response.raise_for_status()
                
                logger.info(f"[SYNC SUCCESS] Synced {len(filled_orders)} filled orders for {exchange}")
                return {
                    "exchange": exchange,
                    "synced_orders": len(filled_orders),
                    "filled_orders": filled_orders
                }
                
        except Exception as db_error:
            logger.error(f"Failed to sync orders with database: {db_error}")
            # Still return the orders for manual processing
            return {
                "exchange": exchange,
                "synced_orders": len(filled_orders),
                "filled_orders": filled_orders,
                "database_sync_error": str(db_error)
            }
        
    except Exception as e:
        logger.error(f"Error syncing orders for {exchange}: {e}")
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

# WebSocket Endpoints
@app.get("/api/v1/websocket/status")
async def get_websocket_status():
    """Get WebSocket connection status for all exchanges"""
    try:
        return websocket_manager.get_all_status()
    except Exception as e:
        logger.error(f"Error getting WebSocket status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/websocket/{exchange_name}/status")
async def get_exchange_websocket_status(exchange_name: str):
    """Get WebSocket connection status for specific exchange"""
    try:
        handler = websocket_manager.get_handler(exchange_name)
        if handler:
            return handler.get_status()
        else:
            raise HTTPException(status_code=404, detail=f"WebSocket handler for {exchange_name} not found")
    except Exception as e:
        logger.error(f"Error getting WebSocket status for {exchange_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws/orders/{exchange_name}")
async def websocket_orders(websocket: WebSocket, exchange_name: str):
    """WebSocket endpoint for real-time order updates from specific exchange"""
    await websocket.accept()
    try:
        handler = websocket_manager.get_handler(exchange_name)
        if not handler:
            await websocket.send_text(json.dumps({
                "error": f"WebSocket handler for {exchange_name} not found"
            }))
            return
        
        # Send initial status
        await websocket.send_text(json.dumps({
            "type": "status",
            "exchange": exchange_name,
            "connected": handler.is_connected,
            "timestamp": datetime.utcnow().isoformat()
        }))
        
        # Keep connection alive and send periodic updates
        while True:
            try:
                # Send periodic status updates
                status_data = {
                    "type": "status_update",
                    "exchange": exchange_name,
                    "connected": handler.is_connected,
                    "last_update": handler.last_update,
                    "registered_callbacks": len(handler.order_callbacks),
                    "timestamp": datetime.utcnow().isoformat()
                }
                await websocket.send_text(json.dumps(status_data))
                await asyncio.sleep(10)  # Update every 10 seconds
                
            except WebSocketDisconnect:
                logger.info(f"WebSocket client disconnected for {exchange_name}")
                break
            except Exception as e:
                logger.error(f"WebSocket error for {exchange_name}: {e}")
                break
                
    except Exception as e:
        logger.error(f"Error in WebSocket connection for {exchange_name}: {e}")
        try:
            await websocket.close()
        except:
            pass

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
    await websocket_manager.initialize_connections()

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
    await websocket_manager.disconnect_all()

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8003,
        reload=True,
        log_level="info"
    ) 