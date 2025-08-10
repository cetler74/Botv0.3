"""
Exchange Service for the Multi-Exchange Trading Bot - FIXED VERSION
Multi-exchange operations and market data management with proper WebSocket support
"""

import asyncio
import json
import logging
import os
import time
import websockets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

import ccxt.async_support as ccxt
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
        self.subscribed_symbols: Set[str] = set()
        
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
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to {self.exchange_name} WebSocket: {e}")
            self.is_connected = False
            return False
    
    async def disconnect(self):
        """Close WebSocket connection"""
        if self.connection:
            try:
                await self.connection.close()
            except:
                pass
            finally:
                self.connection = None
                self.is_connected = False
                logger.info(f"Disconnected from {self.exchange_name} WebSocket")
    
    async def _listen_for_messages(self):
        """Listen for incoming WebSocket messages"""
        try:
            async for message in self.connection:
                try:
                    data = json.loads(message)
                    # Try to parse ticker updates first; if not, handle as order update
                    handled_ticker = await self._handle_ticker_update(data)
                    if not handled_ticker:
                        await self._handle_order_update(data)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON message from {self.exchange_name}: {message}")
                except Exception as e:
                    logger.error(f"Error processing message from {self.exchange_name}: {e}")
        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"WebSocket connection closed for {self.exchange_name}")
            self.is_connected = False
        except Exception as e:
            logger.error(f"Error in WebSocket listener for {self.exchange_name}: {e}")
            self.is_connected = False
    
    async def _handle_order_update(self, data: dict):
        """Handle order update from WebSocket - FIXED VERSION"""
        try:
            # CRITICAL FIX: Ensure data is dict, not list
            if isinstance(data, list):
                # If data is a list, process each item
                for item in data:
                    if isinstance(item, dict):
                        await self._process_single_order_update(item)
            elif isinstance(data, dict):
                await self._process_single_order_update(data)
            else:
                logger.warning(f"Unexpected data type from {self.exchange_name}: {type(data)}")
                
        except Exception as e:
            logger.error(f"Error handling order update from {self.exchange_name}: {e}")

    async def _handle_ticker_update(self, data: dict) -> bool:
        """Try to handle incoming message as a ticker update. Return True if handled."""
        try:
            # Binance combined tickers: list of objects with 's','c','b','a'
            if self.exchange_name.lower() == 'binance':
                if isinstance(data, list):
                    for item in data:
                        symbol = item.get('s')
                        if not symbol:
                            continue
                        last = item.get('c')
                        bid = item.get('b')
                        ask = item.get('a')
                        _update_ticker_cache(self.exchange_name, symbol, last, bid, ask)
                    self.last_update = datetime.utcnow().isoformat()
                    return True
                elif isinstance(data, dict) and data.get('s') and data.get('c'):
                    symbol = data.get('s')
                    _update_ticker_cache(self.exchange_name, symbol, data.get('c'), data.get('b'), data.get('a'))
                    self.last_update = datetime.utcnow().isoformat()
                    return True

            # Bybit v5 public spot tickers
            if self.exchange_name.lower() == 'bybit':
                if isinstance(data, dict) and data.get('topic') and 'tickers' in str(data.get('topic')):
                    payload = data.get('data') or []
                    if isinstance(payload, dict):
                        payload = [payload]
                    for item in payload:
                        symbol = item.get('symbol')
                        last = item.get('lastPrice') or item.get('last_price')
                        bid = item.get('bid1Price') or item.get('bid_price')
                        ask = item.get('ask1Price') or item.get('ask_price')
                        if symbol:
                            _update_ticker_cache(self.exchange_name, symbol, last, bid, ask)
                    self.last_update = datetime.utcnow().isoformat()
                    return True

            # Crypto.com public ticker
            if self.exchange_name.lower() == 'cryptocom':
                if isinstance(data, dict) and data.get('result') and isinstance(data['result'], dict):
                    items = data['result'].get('data') or []
                    if isinstance(items, dict):
                        items = [items]
                    for item in items:
                        # instrument name could be like BTC_USD; normalize by removing non-alnum
                        instr = item.get('i') or item.get('instrument_name') or item.get('s')
                        if not instr:
                            continue
                        symbol = ''.join(ch for ch in str(instr) if ch.isalnum())
                        last = item.get('a') or item.get('c') or item.get('last')
                        bid = item.get('b')
                        ask = item.get('k') or item.get('a')
                        _update_ticker_cache(self.exchange_name, symbol, last, bid, ask)
                    self.last_update = datetime.utcnow().isoformat()
                    return True

        except Exception as e:
            logger.debug(f"Ticker parse not handled for {self.exchange_name}: {e}")
        return False

    async def subscribe(self, symbols: List[str]) -> None:
        """Track desired ticker subscriptions and send subscription frames for exchanges that require it."""
        try:
            new_syms = set(symbols)
            self.subscribed_symbols |= new_syms
            # Binance all-tickers stream needs no per-symbol subscribe when using !ticker@arr
            if self.exchange_name.lower() == 'binance':
                return
            # Bybit subscribe
            if self.exchange_name.lower() == 'bybit' and self.connection:
                topics = [f"tickers.{sym}" for sym in new_syms]
                msg = {"op": "subscribe", "args": topics}
                await self.connection.send(json.dumps(msg))
            # Crypto.com subscribe
            if self.exchange_name.lower() == 'cryptocom' and self.connection:
                # Crypto.com expects instrument_name like BTC_USD; try to infer by inserting underscore before USD/USDC
                args = []
                for s in new_syms:
                    if s.endswith('USD'):
                        args.append({"instrument_name": f"{s[:-3]}_USD"})
                    elif s.endswith('USDC'):
                        args.append({"instrument_name": f"{s[:-4]}_USDC"})
                if args:
                    msg = {"id": int(time.time()), "method": "subscribe", "params": {"channels": ["ticker"]}, "nonce": int(time.time()*1000)}
                    # Some implementations require channels with instrument_name; keep simple here
                    await self.connection.send(json.dumps(msg))
        except Exception as e:
            logger.warning(f"Subscription error for {self.exchange_name}: {e}")

    async def unsubscribe(self, symbols: List[str]) -> None:
        try:
            remove_syms = set(symbols)
            self.subscribed_symbols -= remove_syms
            # Optional: send unsubscribe frames if needed for specific exchanges
            if self.exchange_name.lower() == 'bybit' and self.connection:
                topics = [f"tickers.{sym}" for sym in remove_syms]
                msg = {"op": "unsubscribe", "args": topics}
                await self.connection.send(json.dumps(msg))
            if self.exchange_name.lower() == 'cryptocom' and self.connection:
                # Send generic unsubscribe
                msg = {"id": int(time.time()), "method": "unsubscribe", "params": {"channels": ["ticker"]}, "nonce": int(time.time()*1000)}
                await self.connection.send(json.dumps(msg))
        except Exception as e:
            logger.warning(f"Unsubscribe error for {self.exchange_name}: {e}")
    
    async def _process_single_order_update(self, data: dict):
        """Process a single order update - FIXED VERSION"""
        try:
            # Extract order information based on exchange format
            order_id = self._extract_order_id(data)
            status = self._extract_order_status(data)
            
            if order_id and status:
                # Update last update time
                self.last_update = datetime.utcnow().isoformat()
                
                # Notify registered callbacks
                if order_id in self.order_callbacks:
                    callback = self.order_callbacks[order_id]
                    if asyncio.iscoroutinefunction(callback):
                        await callback(status, data)
                    else:
                        callback(status, data)
                
                logger.info(f"Order update from {self.exchange_name}: {order_id} -> {status}")
                
        except Exception as e:
            logger.error(f"Error processing single order update from {self.exchange_name}: {e}")
    
    def _extract_order_id(self, data: dict) -> Optional[str]:
        """Extract order ID from exchange-specific message format"""
        try:
            if not isinstance(data, dict):
                return None
                
            # Exchange-specific order ID extraction
            if self.exchange_name == 'binance':
                return data.get('i') or data.get('c') or data.get('orderId')  # Binance format
            elif self.exchange_name == 'cryptocom':
                return data.get('order_id') or data.get('id')  # Crypto.com format
            elif self.exchange_name == 'bybit':
                return data.get('orderId') or data.get('order_id')  # Bybit format
            else:
                return data.get('order_id') or data.get('id') or data.get('orderId')
        except Exception as e:
            logger.error(f"Error extracting order ID from {self.exchange_name}: {e}")
            return None
    
    def _extract_order_status(self, data: dict) -> Optional[str]:
        """Extract order status from exchange-specific message format"""
        try:
            if not isinstance(data, dict):
                return None
                
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
                else:
                    return status
                    
            return None
        except Exception as e:
            logger.error(f"Error extracting order status from {self.exchange_name}: {e}")
            return None
    
    def register_order_callback(self, order_id: str, callback):
        """Register callback for specific order updates"""
        self.order_callbacks[order_id] = callback
        logger.debug(f"Registered callback for order {order_id} on {self.exchange_name}")
    
    def unregister_order_callback(self, order_id: str):
        """Unregister callback for specific order"""
        if order_id in self.order_callbacks:
            del self.order_callbacks[order_id]
            logger.debug(f"Unregistered callback for order {order_id} on {self.exchange_name}")

class WebSocketManager:
    """Manages WebSocket connections for all exchanges"""
    
    def __init__(self):
        self.handlers: Dict[str, WebSocketExchangeHandler] = {}
    
    async def initialize_handler(self, exchange_name: str, exchange_config: dict):
        """Initialize WebSocket handler for an exchange"""
        try:
            handler = WebSocketExchangeHandler(exchange_name, exchange_config)
            self.handlers[exchange_name] = handler
            
            # Only connect if websocket_url is provided
            if exchange_config.get('websocket_url'):
                await handler.connect()
                logger.info(f"WebSocket handler initialized for {exchange_name}")
            else:
                logger.info(f"WebSocket handler created for {exchange_name} (no URL provided)")
                
        except Exception as e:
            logger.error(f"Failed to initialize WebSocket handler for {exchange_name}: {e}")
    
    def get_handler(self, exchange_name: str) -> Optional[WebSocketExchangeHandler]:
        """Get WebSocket handler for an exchange"""
        return self.handlers.get(exchange_name)
    
    async def shutdown_all(self):
        """Shutdown all WebSocket connections"""
        for handler in self.handlers.values():
            await handler.disconnect()
        self.handlers.clear()
        logger.info("All WebSocket handlers shutdown")
    
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

# In-memory ticker cache: key = f"{exchange}:{symbol_no_slash}", value dict(last,bid,ask,timestamp)
ticker_cache: Dict[str, Dict[str, Any]] = {}

def _normalize_symbol_key(exchange: str, symbol: str) -> str:
    # Symbol expected without slash by most callers (e.g., BTCUSD, BTCUSDC)
    normalized = ''.join(ch for ch in str(symbol) if ch.isalnum())
    return f"{exchange}:{normalized}"

def _update_ticker_cache(exchange: str, symbol: str, last: Any, bid: Any, ask: Any) -> None:
    try:
        key = _normalize_symbol_key(exchange, symbol)
        last_f = float(last) if last is not None else None
        bid_f = float(bid) if bid is not None else None
        ask_f = float(ask) if ask is not None else None
        if last_f is None:
            return
        ticker_cache[key] = {
            'last': last_f,
            'bid': bid_f,
            'ask': ask_f,
            'timestamp': datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.debug(f"Failed to update ticker cache for {exchange}:{symbol}: {e}")

# Data Models
class OrderRequest(BaseModel):
    exchange: str
    symbol: str
    order_type: str  # 'market' or 'limit'
    side: str  # 'buy' or 'sell'
    amount: float
    price: Optional[float] = None
    client_order_id: Optional[str] = None  # Phase 0: Support idempotency

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

class ExchangeManager:
    """FIXED Exchange Manager with proper error handling"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._initialize_exchanges()
        
    def _initialize_exchanges(self) -> None:
        """Initialize exchange connections - FIXED VERSION"""
        global exchanges
        exchange_configs = self.config.get('exchanges', {})
        
        for exchange_name, config in exchange_configs.items():
            try:
                logger.info(f"Initializing {exchange_name} exchange...")
                
                # Get exchange class
                exchange_class = getattr(ccxt, exchange_name)
                
                # Prepare configuration
                api_key = config.get('api_key', '')
                api_secret = config.get('api_secret', '')
                
                # Log credential status (without revealing actual values)
                logger.info(f"Exchange {exchange_name} credentials: API key {'✅' if api_key else '❌'}, Secret {'✅' if api_secret else '❌'}")
                
                exchange_config = {
                    'apiKey': api_key,
                    'secret': api_secret,
                    'sandbox': config.get('sandbox', False),
                    'enableRateLimit': True,
                    'rateLimit': 100,  # 100ms between requests
                }
                
                # Exchange-specific configurations
                if exchange_name == 'binance':
                    exchange_config.update({
                        'options': {
                            'defaultType': 'spot',
                            'warnOnFetchOpenOrdersWithoutSymbol': False  # Suppress rate limit warning
                        }
                    })
                elif exchange_name == 'bybit':
                    exchange_config.update({
                        'options': {
                            'defaultType': 'unified'  # Use unified trading account
                        }
                    })
                elif exchange_name == 'cryptocom':
                    exchange_config.update({
                        'options': {
                            'createMarketBuyOrderRequiresPrice': False
                        }
                    })
                    # Crypto.com specific: ensure credentials are properly set
                    logger.info(f"CryptoCom config: sandbox={exchange_config.get('sandbox')}, "
                              f"apiKey_length={len(exchange_config.get('apiKey', ''))}, "
                              f"secret_length={len(exchange_config.get('secret', ''))}")
                
                # CRITICAL FIX: Create exchange instance properly
                try:
                    exchange = exchange_class(exchange_config)
                    
                    # Verify the instance is properly created
                    if not hasattr(exchange, 'fetch_ticker'):
                        raise Exception(f"Invalid exchange instance for {exchange_name}")
                        
                    # Store exchange and its configuration
                    exchanges[exchange_name] = {
                        'instance': exchange,  # This should be a proper CCXT instance
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
                    
                    # Initialize WebSocket handler
                    websocket_config = config.copy()
                    websocket_config['websocket_url'] = config.get('websocket_url')
                    asyncio.create_task(websocket_manager.initialize_handler(exchange_name, websocket_config))
                    
                    logger.info(f"✅ Successfully initialized {exchange_name} exchange")
                    
                except Exception as init_error:
                    logger.error(f"❌ Failed to create {exchange_name} instance: {init_error}")
                    raise init_error
                    
            except Exception as e:
                logger.error(f"❌ Failed to initialize {exchange_name} exchange: {e}")
                
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
                    'exchange': exchange_name,
                    'operation': operation,
                    'error_message': str(error),
                    'severity': 'warning',
                    'timestamp': datetime.utcnow().isoformat(),
                    'resolved': False
                }
                await client.post(f"{database_service_url}/api/v1/alerts", json=alert_data)
        except Exception as alert_error:
            logger.warning(f"Failed to create alert for exchange error: {alert_error}")

async def get_config_from_service():
    """Get configuration from config service"""
    try:
        async with httpx.AsyncClient() as client:
            # Try the internal endpoint first for unmasked credentials
            response = await client.get(f"{config_service_url}/api/v1/config/exchanges/internal")
            if response.status_code == 200:
                return response.json()
            else:
                # Fallback to regular endpoint
                response = await client.get(f"{config_service_url}/api/v1/config/exchanges")
                if response.status_code == 200:
                    return response.json()
                else:
                    raise Exception(f"Config service returned {response.status_code}")
    except Exception as e:
        logger.error(f"Failed to get config from service: {e}")
        raise

# Status endpoint
@app.get("/status")
async def get_status():
    """Get service status"""
    return {
        "service": "exchange-service",
        "status": "running",
        "exchanges": {
            name: {
                "status": info['health']['status'],
                "last_check": info['health']['last_check'],
                "error_count": info['health']['error_count'],
                "websocket_connected": websocket_manager.get_handler(name).is_connected if websocket_manager.get_handler(name) else False
            }
            for name, info in exchanges.items()
        }
    }

async def initialize_exchanges():
    """Initialize exchange connections - FIXED VERSION"""
    global exchange_manager
    try:
        config = await get_config_from_service()
        exchange_manager = ExchangeManager({'exchanges': config})
        logger.info("✅ Exchange service initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize exchange service: {e}")
        raise

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
    if not exchange_manager or len(exchanges) == 0:
        raise HTTPException(status_code=503, detail="Exchange service not ready")
    return {"status": "ready", "exchanges": len(exchanges)}

# Market Data Endpoints
@app.get("/api/v1/market/ticker-live/{exchange}/{symbol:path}")
async def get_ticker_live(exchange: str, symbol: str, stale_threshold_seconds: int = 30):
    """Return last WS ticker if fresh; 204 if unavailable or stale."""
    try:
        key = _normalize_symbol_key(exchange, symbol)
        data = ticker_cache.get(key)
        if not data:
            return Response(status_code=204)
        ts = data.get('timestamp')
        if not ts:
            return Response(status_code=204)
        try:
            age = (datetime.utcnow() - datetime.fromisoformat(ts)).total_seconds()
        except Exception:
            age = 9999
        if age > stale_threshold_seconds:
            return Response(status_code=204)
        return {
            'symbol': symbol,
            'last': data.get('last'),
            'bid': data.get('bid'),
            'ask': data.get('ask'),
            'timestamp': data.get('timestamp'),
            'source': 'websocket'
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/api/v1/market/ticker/{exchange}/{symbol:path}")
async def get_ticker(exchange: str, symbol: str):
    """Get ticker information for a symbol - FIXED VERSION"""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    try:
        # Convert symbol format for different exchanges
        exchange_symbol = symbol
        if exchange == 'cryptocom':
            # Crypto.com needs symbols with slashes, convert from BTCUSD to BTC/USD
            if '/' not in symbol:
                if len(symbol) >= 6 and symbol.endswith('USD'):
                    base = symbol[:-3]
                    exchange_symbol = f"{base}/USD"
                elif len(symbol) >= 6 and symbol.endswith('USDC'):
                    base = symbol[:-4]
                    exchange_symbol = f"{base}/USDC"
                
            # Add :USD or :USDC suffix when needed without introducing double slashes
            if ':' not in exchange_symbol:
                if exchange_symbol.endswith('/USD'):
                    exchange_symbol = exchange_symbol.replace('/USD', '/USD:USD')
                elif exchange_symbol.endswith('/USDC'):
                    exchange_symbol = exchange_symbol.replace('/USDC', '/USDC:USDC')
        elif exchange in ['binance', 'bybit']:
            # Binance and Bybit need symbols with slashes, convert from BTCUSD to BTC/USD
            if len(symbol) >= 6 and symbol.endswith('USD'):
                base = symbol[:-3]
                exchange_symbol = f"{base}/USD"
            elif len(symbol) >= 6 and symbol.endswith('USDC'):
                base = symbol[:-4]
                exchange_symbol = f"{base}/USDC"
        
        await exchange_manager._rate_limit(exchange)
        
        # CRITICAL FIX: Ensure we get the actual CCXT instance
        exchange_instance = exchanges[exchange]['instance']
        
        # Verify the instance has the required methods
        if not hasattr(exchange_instance, 'fetch_ticker'):
            raise Exception(f"Invalid exchange instance for {exchange}: missing fetch_ticker method")
        
        # Retry logic with exponential backoff
        max_retries = 3
        retry_delay = 1  # Start with 1 second
        ticker = None
        
        for attempt in range(max_retries):
            try:
                start_time = time.time()
                # CRITICAL FIX: Make sure this is actually async and properly awaited
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
        
        return ticker
        
    except Exception as e:
        error_msg = str(e).lower()
        # Handle symbol not found errors with 404 instead of 500
        if any(phrase in error_msg for phrase in ['does not have market symbol', 'symbol not found', 'invalid symbol', 'market not found']):
            logger.warning(f"Symbol {exchange_symbol} not found on {exchange}: {e}")
            raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found on {exchange}")
        
        await exchange_manager._handle_exchange_error(exchange, e, f"get_ticker({symbol})")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/market/orderbook/{exchange}/{symbol:path}")
async def get_orderbook(exchange: str, symbol: str, limit: int = 20):
    """Get orderbook information for a symbol - FIXED VERSION"""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    try:
        # Convert symbol format for different exchanges
        exchange_symbol = symbol
        if exchange == 'cryptocom':
            # Crypto.com needs symbols with slashes, convert from BTCUSD to BTC/USD
            if '/' not in symbol:
                if len(symbol) >= 6 and symbol.endswith('USD'):
                    base = symbol[:-3]
                    exchange_symbol = f"{base}/USD"
                elif len(symbol) >= 7 and symbol.endswith('USDC'):
                    base = symbol[:-4]
                    exchange_symbol = f"{base}/USDC"
            # Add :USD or :USDC suffix when needed without introducing double slashes
            if ':' not in exchange_symbol:
                if exchange_symbol.endswith('/USD'):
                    exchange_symbol = exchange_symbol.replace('/USD', '/USD:USD')
                elif exchange_symbol.endswith('/USDC'):
                    exchange_symbol = exchange_symbol.replace('/USDC', '/USDC:USDC')
        elif exchange in ['binance', 'bybit']:
            # Binance and Bybit need symbols with slashes, convert from BTCUSD to BTC/USD
            if '/' not in symbol:
                if len(symbol) >= 6 and symbol.endswith('USD'):
                    base = symbol[:-3]
                    exchange_symbol = f"{base}/USD"
                elif len(symbol) >= 7 and symbol.endswith('USDC'):
                    base = symbol[:-4]
                    exchange_symbol = f"{base}/USDC"
        
        await exchange_manager._rate_limit(exchange)
        
        # CRITICAL FIX: Ensure we get the actual CCXT instance
        exchange_instance = exchanges[exchange]['instance']
        
        # Verify the instance has the required methods
        if not hasattr(exchange_instance, 'fetch_order_book'):
            raise Exception(f"Invalid exchange instance for {exchange}: missing fetch_order_book method")
        
        start_time = time.time()
        # CRITICAL FIX: Make sure this is actually async and properly awaited
        orderbook = await exchange_instance.fetch_order_book(exchange_symbol, limit=limit)
        response_time = time.time() - start_time
        
        # Update health
        exchanges[exchange]['health']['status'] = 'healthy'
        exchanges[exchange]['health']['response_time'] = response_time
        exchanges[exchange]['health']['last_check'] = datetime.utcnow()
        
        return orderbook
        
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, f"get_orderbook({symbol})")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/trading/orders/{exchange}")
async def get_open_orders(exchange: str, symbol: Optional[str] = None):
    """Get open orders for an exchange - FIXED VERSION"""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    try:
        await exchange_manager._rate_limit(exchange)
        
        # CRITICAL FIX: Ensure we get the actual CCXT instance
        exchange_instance = exchanges[exchange]['instance']
        
        # Verify the instance has the required methods
        if not hasattr(exchange_instance, 'fetch_open_orders'):
            raise Exception(f"Invalid exchange instance for {exchange}: missing fetch_open_orders method")
        
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

@app.get("/api/v1/account/balance/{exchange}")
async def get_balance(exchange: str):
    """Get account balance for an exchange - FIXED VERSION"""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    # Check if exchange has credentials
    exchange_info = exchanges[exchange]
    config = exchange_info.get('config', {})
    if not config.get('api_key') or not config.get('api_secret'):
        raise HTTPException(status_code=403, detail=f"Exchange {exchange} missing API credentials for balance operations")
    
    try:
        await exchange_manager._rate_limit(exchange)
        
        # CRITICAL FIX: Ensure we get the actual CCXT instance
        exchange_instance = exchanges[exchange]['instance']
        
        # Verify the instance has the required methods
        if not hasattr(exchange_instance, 'fetch_balance'):
            raise Exception(f"Invalid exchange instance for {exchange}: missing fetch_balance method")
        
        start_time = time.time()
        balance = await exchange_instance.fetch_balance()
        response_time = time.time() - start_time
        
        # BYBIT FIX: Handle null free balances in unified accounts
        if exchange.lower() == 'bybit' and balance.get('free'):
            for currency, free_amount in balance['free'].items():
                if free_amount is None and balance.get('total', {}).get(currency):
                    # Use total as free if free is null (unified account behavior)
                    balance['free'][currency] = balance['total'][currency]
                    logger.info(f"🔧 Bybit balance fix: {currency} free={balance['free'][currency]} (was null)")
        
        # Update health
        exchanges[exchange]['health']['status'] = 'healthy'
        exchanges[exchange]['health']['response_time'] = response_time
        exchanges[exchange]['health']['last_check'] = datetime.utcnow()
        
        return balance
        
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, "get_balance")
        raise HTTPException(status_code=500, detail=str(e))

# Trading Order Endpoints
@app.post("/api/v1/trading/order")
async def place_order(order_request: OrderRequest):
    """Place a trading order - FIXED VERSION"""
    exchange = order_request.exchange
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    # Check if exchange has credentials
    exchange_info = exchanges[exchange]
    config = exchange_info.get('config', {})
    if not config.get('api_key') or not config.get('api_secret'):
        raise HTTPException(status_code=403, detail=f"Exchange {exchange} missing API credentials for trading operations")
    
    try:
        await exchange_manager._rate_limit(exchange)
        
        # CRITICAL FIX: Ensure we get the actual CCXT instance
        exchange_instance = exchanges[exchange]['instance']
        
        # Verify the instance has the required methods
        if not hasattr(exchange_instance, 'create_order'):
            raise HTTPException(status_code=500, detail=f"Invalid exchange instance for {exchange}: missing create_order method")
        
        # Handle dust amounts that are below exchange minimums
        if exchange == 'binance':
            # Binance minimum order sizes vary by symbol - updated with correct minimums:
            min_amounts = {
                'NEO/USDC': 0.01,
                'BTC/USDT': 0.00001,
                'ETH/USDT': 0.0001,
                'BNB/USDT': 0.01,
                'LINK/USDC': 1.0,    # LINK minimum is actually 1.0 LINK (not 0.001)
                'LINK/USDT': 1.0     # LINK minimum is 1.0 LINK across all pairs
            }
            min_amount = min_amounts.get(order_request.symbol, 0.001)  # Default 0.001
            if order_request.amount < min_amount:
                # For dust amounts, return a descriptive error instead of blocking
                logger.warning(f"⚠️  Dust amount {order_request.amount} below minimum {min_amount} for {order_request.symbol} on {exchange}")
                raise HTTPException(status_code=422, 
                    detail=f"DUST_AMOUNT: Order amount {order_request.amount} below exchange minimum {min_amount} for {order_request.symbol}. Consider closing position through manual trade or accumulating more assets.")
        
        elif exchange == 'cryptocom':
            # Crypto.com minimum order validation - based on minimum notional values
            # Get current price to calculate notional value
            current_price = None
            try:
                ticker_response = await get_ticker(exchange, order_request.symbol)
                current_price = float(ticker_response.get('last', 0))
            except:
                logger.warning(f"Could not get current price for {order_request.symbol} on {exchange}")
                
            if current_price:
                notional_value = order_request.amount * current_price
                min_notional = 12.0  # Crypto.com minimum notional is ~$12 USD per their API docs
                if notional_value < min_notional:
                    logger.warning(f"⚠️  Order notional ${notional_value:.2f} below minimum ${min_notional} for {order_request.symbol} on {exchange}")
                    raise HTTPException(status_code=422, 
                        detail=f"DUST_AMOUNT: Order notional ${notional_value:.2f} below exchange minimum ${min_notional} for {order_request.symbol}. Consider accumulating larger position or manual trade.")
            
            # Also check absolute minimum amounts for specific assets
            min_amounts_crypto = {
                'BTC/USD': 0.00001,
                'ETH/USD': 0.0001,
                'ACH/USD': 1.0,     # ACH requires at least 1 token
                'ACT/USD': 1.0,     # ACT requires at least 1 token
                'ADA/USD': 1.0,     # ADA requires at least 1 token
                'ALGO/USD': 1.0,    # ALGO requires at least 1 token
                'APE/USD': 0.1,     # APE requires at least 0.1 token
                'AVAX/USD': 0.01,   # AVAX requires at least 0.01 token
                'DOGE/USD': 1.0,    # DOGE requires at least 1 token
                'DOT/USD': 0.1,     # DOT requires at least 0.1 token
                'LTC/USD': 0.001,   # LTC requires at least 0.001 token
                'MATIC/USD': 1.0,   # MATIC requires at least 1 token
                'SOL/USD': 0.001,   # SOL requires at least 0.001 token
                'TRX/USD': 1.0,     # TRX requires at least 1 token
                'XRP/USD': 1.0,     # XRP requires at least 1 token
                'default': 0.000001
            }
            min_amount = min_amounts_crypto.get(order_request.symbol, min_amounts_crypto['default'])
            if order_request.amount < min_amount:
                logger.warning(f"⚠️  Dust amount {order_request.amount} below minimum {min_amount} for {order_request.symbol} on {exchange}")
                
                # For sell orders, suggest dust consolidation strategies
                if order_request.side.lower() == 'sell':
                    if order_request.amount > 0:
                        shortage = min_amount - order_request.amount
                        raise HTTPException(status_code=422, 
                            detail=f"DUST_AMOUNT: Sell amount {order_request.amount} below exchange minimum {min_amount} for {order_request.symbol}. Short by {shortage:.6f} tokens. Consider: 1) Wait to accumulate {shortage:.6f} more tokens, 2) Manual trade on exchange, or 3) Accept small loss and keep as dust.")
                    else:
                        raise HTTPException(status_code=422, 
                            detail=f"DUST_AMOUNT: Cannot sell {order_request.amount} {order_request.symbol} - position too small. Consider manual trade or keep as dust.")
                else:
                    raise HTTPException(status_code=422, 
                        detail=f"DUST_AMOUNT: Order amount {order_request.amount} below exchange minimum {min_amount} for {order_request.symbol}. Consider larger position size.")
        
        # Convert symbol format for different exchanges
        exchange_symbol = order_request.symbol
        if exchange == 'cryptocom':
            # Crypto.com needs symbols with slashes, but only convert if no slash exists
            if '/' not in order_request.symbol:
                if len(order_request.symbol) >= 6 and order_request.symbol.endswith('USD'):
                    base = order_request.symbol[:-3]
                    exchange_symbol = f"{base}/USD"
                elif len(order_request.symbol) >= 7 and order_request.symbol.endswith('USDC'):
                    base = order_request.symbol[:-4]
                    exchange_symbol = f"{base}/USDC"
        elif exchange in ['binance', 'bybit']:
            # Binance and Bybit need symbols with slashes, but only convert if no slash exists
            if '/' not in order_request.symbol:
                if len(order_request.symbol) >= 6 and order_request.symbol.endswith('USD'):
                    base = order_request.symbol[:-3]
                    exchange_symbol = f"{base}/USD"
                elif len(order_request.symbol) >= 7 and order_request.symbol.endswith('USDC'):
                    base = order_request.symbol[:-4]
                    exchange_symbol = f"{base}/USDC"
        
        start_time = time.time()
        
        # Prepare exchange-specific params using exchange handler
        try:
            from core.exchange_handlers import exchange_handler_manager
            ccxt_params = exchange_handler_manager.get_order_params(exchange, order_request.order_type, order_request.side)
        except ImportError:
            ccxt_params = {}
        
        # Add client order ID if provided
        if order_request.client_order_id:
            if exchange.lower() == 'binance':
                ccxt_params['newClientOrderId'] = order_request.client_order_id
            elif exchange.lower() == 'cryptocom':
                ccxt_params['client_oid'] = order_request.client_order_id
            elif exchange.lower() == 'bybit':
                ccxt_params['orderLinkId'] = order_request.client_order_id
            logger.info(f"🆔 Using client_order_id: {order_request.client_order_id} for {exchange} (param: {ccxt_params})")
        
        try:
            # Place the order using CCXT
            if order_request.order_type == 'market':
                # Market order - handle exchange-specific requirements
                if exchange.lower() == 'bybit' and order_request.side.lower() == 'buy':
                    # Bybit market buy orders require createMarketBuyOrderRequiresPrice=False
                    ccxt_params['createMarketBuyOrderRequiresPrice'] = False
                    order = await exchange_instance.create_market_buy_order(
                        symbol=exchange_symbol,
                        amount=order_request.amount,
                        params=ccxt_params
                    )
                else:
                    # Standard market order for other exchanges or sell orders
                    order = await exchange_instance.create_market_order(
                        symbol=exchange_symbol,
                        side=order_request.side,
                        amount=order_request.amount,
                        params=ccxt_params
                    )
            elif order_request.order_type == 'limit':
                # Limit order
                if order_request.price is None:
                    raise HTTPException(status_code=400, detail="Price is required for limit orders")
                order = await exchange_instance.create_limit_order(
                    symbol=exchange_symbol,
                    side=order_request.side,
                    amount=order_request.amount,
                    price=order_request.price,
                    params=ccxt_params
                )
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported order type: {order_request.order_type}")
        
        except Exception as ccxt_error:
            # Handle CCXT errors gracefully to prevent 500 errors
            error_msg = str(ccxt_error)
            logger.error(f"❌ CCXT order creation failed for {exchange_symbol} on {exchange}: {error_msg}")
            
            # Ensure we never return empty error messages
            if not error_msg or error_msg.strip() == "":
                error_msg = f"Unknown error placing {order_request.order_type} {order_request.side} order for {exchange_symbol}"
            
            # Check if it's a minimum amount error
            if 'minimum' in error_msg.lower() or 'min' in error_msg.lower():
                raise HTTPException(status_code=422, detail=f"MINIMUM_AMOUNT_ERROR: {error_msg}")
            elif 'insufficient' in error_msg.lower() or 'balance' in error_msg.lower():
                raise HTTPException(status_code=422, detail=f"INSUFFICIENT_BALANCE: {error_msg}")
            elif 'precision' in error_msg.lower() or 'decimal' in error_msg.lower():
                raise HTTPException(status_code=422, detail=f"PRECISION_ERROR: {error_msg}")
            else:
                raise HTTPException(status_code=422, detail=f"ORDER_ERROR: {error_msg}")
        
        response_time = time.time() - start_time
        
        # Update health
        exchanges[exchange]['health']['status'] = 'healthy'
        exchanges[exchange]['health']['response_time'] = response_time
        exchanges[exchange]['health']['last_check'] = datetime.utcnow()
        
        # Register WebSocket callback for order updates if available
        if 'id' in order:
            handler = websocket_manager.get_handler(exchange)
            if handler and handler.is_connected:
                def order_callback(status, data):
                    logger.info(f"Order {order['id']} on {exchange} updated to {status}")
                
                handler.register_order_callback(order['id'], order_callback)
        
        return {"order": order, "exchange": exchange, "symbol": exchange_symbol}
        
    except HTTPException:
        # Re-raise HTTPExceptions (like 422 for dust amounts) without modification
        raise
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, f"place_order({order_request.symbol})")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/v1/trading/order/{exchange}/{order_id}")
async def cancel_order(exchange: str, order_id: str, symbol: str = None):
    """Cancel a trading order - COMPLETE ORDER MANAGEMENT"""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    # Check if exchange has credentials
    exchange_info = exchanges[exchange]
    config = exchange_info.get('config', {})
    if not config.get('api_key') or not config.get('api_secret'):
        raise HTTPException(status_code=403, detail=f"Exchange {exchange} missing API credentials for trading operations")
    
    try:
        await exchange_manager._rate_limit(exchange)
        
        # Get the actual CCXT instance
        exchange_instance = exchanges[exchange]['instance']
        
        # Verify the instance has the required methods
        if not hasattr(exchange_instance, 'cancel_order'):
            raise HTTPException(status_code=500, detail=f"Invalid exchange instance for {exchange}: missing cancel_order method")
        
        start_time = time.time()
        
        # Cancel the order using CCXT with improved error handling
        try:
            if symbol:
                # Some exchanges require symbol for cancellation
                result = await exchange_instance.cancel_order(order_id, symbol)
            else:
                result = await exchange_instance.cancel_order(order_id)
            
            response_time = time.time() - start_time
            
            # Update health
            exchanges[exchange]['health']['status'] = 'healthy'
            exchanges[exchange]['health']['response_time'] = response_time
            exchanges[exchange]['health']['last_check'] = datetime.utcnow()
            
            # Unregister WebSocket callback if it was registered
            handler = websocket_manager.get_handler(exchange)
            if handler:
                handler.unregister_order_callback(order_id)
            
            return {"result": result, "exchange": exchange, "order_id": order_id, "status": "cancelled"}
            
        except Exception as cancel_error:
            # Handle common cancellation errors gracefully
            error_message = str(cancel_error).lower()
            
            if any(phrase in error_message for phrase in ['order not found', 'does not exist', 'invalid order', 'already filled', 'already executed']):
                # Order was likely filled or already cancelled
                logger.warning(f"Order {order_id} on {exchange} cannot be cancelled - likely filled or expired: {cancel_error}")
                return {"result": None, "exchange": exchange, "order_id": order_id, "status": "already_processed", "message": "Order likely filled or expired"}
            else:
                # Re-raise other errors
                raise cancel_error
        
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, f"cancel_order({order_id})")
        raise HTTPException(status_code=500, detail=str(e))

# OHLCV Data Endpoints
@app.get("/api/v1/market/ohlcv/{exchange}/{symbol:path}")
async def get_ohlcv(exchange: str, symbol: str, timeframe: str = "1h", limit: int = 100):
    """Get OHLCV (candlestick) data for a symbol - REQUIRED FOR STRATEGY SERVICE"""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    try:
        # Convert symbol format for different exchanges
        exchange_symbol = symbol
        if exchange == 'cryptocom':
            # Crypto.com needs symbols with slashes, convert from CROUSD to CRO/USD
            if '/' not in symbol:
                if len(symbol) >= 6 and symbol.endswith('USD'):
                    base = symbol[:-3]
                    exchange_symbol = f"{base}/USD"
                elif len(symbol) >= 7 and symbol.endswith('USDC'):
                    base = symbol[:-4]
                    exchange_symbol = f"{base}/USDC"
            # Add :USD or :USDC suffix when needed without introducing double slashes
            if ':' not in exchange_symbol:
                if exchange_symbol.endswith('/USD'):
                    exchange_symbol = exchange_symbol.replace('/USD', '/USD:USD')
                elif exchange_symbol.endswith('/USDC'):
                    exchange_symbol = exchange_symbol.replace('/USDC', '/USDC:USDC')
        elif exchange in ['binance', 'bybit']:
            # Binance and Bybit need symbols with slashes, convert from BTCUSD to BTC/USD
            if '/' not in symbol:
                if len(symbol) >= 6 and symbol.endswith('USD'):
                    base = symbol[:-3]
                    exchange_symbol = f"{base}/USD"
                elif len(symbol) >= 7 and symbol.endswith('USDC'):
                    base = symbol[:-4]
                    exchange_symbol = f"{base}/USDC"
        
        await exchange_manager._rate_limit(exchange)
        
        # CRITICAL: Get the actual CCXT instance
        exchange_instance = exchanges[exchange]['instance']
        
        # Verify the instance has the required methods
        if not hasattr(exchange_instance, 'fetch_ohlcv'):
            raise Exception(f"Invalid exchange instance for {exchange}: missing fetch_ohlcv method")
        
        start_time = time.time()
        # Fetch OHLCV data using CCXT
        ohlcv = await exchange_instance.fetch_ohlcv(exchange_symbol, timeframe=timeframe, limit=limit)
        response_time = time.time() - start_time
        
        # Update health
        exchanges[exchange]['health']['status'] = 'healthy'
        exchanges[exchange]['health']['response_time'] = response_time
        exchanges[exchange]['health']['last_check'] = datetime.utcnow()
        
        # Convert to DataFrame format expected by strategies
        if ohlcv:
            df_data = {
                'timestamp': [candle[0] for candle in ohlcv],
                'open': [candle[1] for candle in ohlcv],
                'high': [candle[2] for candle in ohlcv],
                'low': [candle[3] for candle in ohlcv],
                'close': [candle[4] for candle in ohlcv],
                'volume': [candle[5] for candle in ohlcv]
            }
            return {"data": df_data, "symbol": exchange_symbol, "timeframe": timeframe}
        else:
            return {"data": None, "error": "No data available"}
        
    except Exception as e:
        error_msg = str(e).lower()
        # Handle symbol not found errors with 404 instead of 500
        if any(phrase in error_msg for phrase in ['does not have market symbol', 'symbol not found', 'invalid symbol', 'market not found']):
            logger.warning(f"Symbol {exchange_symbol} not found on {exchange}: {e}")
            raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found on {exchange}")
        
        await exchange_manager._handle_exchange_error(exchange, e, f"get_ohlcv({symbol}, {timeframe})")
        raise HTTPException(status_code=500, detail=str(e))

# Orders/Trades history endpoints for reconciliation
@app.get("/api/v1/trading/order/{exchange}/{order_id}")
async def get_order_by_id(exchange: str, order_id: str, symbol: Optional[str] = None):
    """Fetch a specific order by ID (optionally provide symbol for exchanges that require it)."""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    try:
        await exchange_manager._rate_limit(exchange)
        exchange_instance = exchanges[exchange]['instance']
        if not hasattr(exchange_instance, 'fetch_order'):
            raise HTTPException(status_code=500, detail=f"Invalid exchange instance for {exchange}: missing fetch_order method")
        # Some exchanges require symbol; try without first, then with symbol if provided
        try:
            order = await exchange_instance.fetch_order(id=order_id, symbol=symbol) if symbol else await exchange_instance.fetch_order(id=order_id)
        except Exception as e:
            if symbol:
                raise e
            else:
                raise HTTPException(status_code=400, detail=f"Symbol may be required to fetch order on {exchange}: {str(e)}")
        return {"order": order}
    except HTTPException:
        raise
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, f"get_order_by_id({order_id})")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/trading/orders/history/{exchange}")
async def get_orders_history(exchange: str, symbol: Optional[str] = None, since: Optional[int] = None, limit: Optional[int] = 100):
    """Fetch historical orders (closed/filled) for reconciliation."""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    try:
        await exchange_manager._rate_limit(exchange)
        exchange_instance = exchanges[exchange]['instance']
        if not hasattr(exchange_instance, 'fetch_orders'):
            raise HTTPException(status_code=500, detail=f"Invalid exchange instance for {exchange}: missing fetch_orders method")
        orders = await exchange_instance.fetch_orders(symbol=symbol, since=since, limit=limit)
        return {"orders": orders}
    except HTTPException:
        raise
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, "get_orders_history")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/trading/mytrades/{exchange}")
async def get_my_trades(exchange: str, symbol: Optional[str] = None, since: Optional[int] = None, limit: Optional[int] = 100):
    """Fetch trade fills (executions) for reconciliation."""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    try:
        await exchange_manager._rate_limit(exchange)
        exchange_instance = exchanges[exchange]['instance']
        if not hasattr(exchange_instance, 'fetch_my_trades'):
            raise HTTPException(status_code=500, detail=f"Invalid exchange instance for {exchange}: missing fetch_my_trades method")
        # Normalize/convert inputs per exchange quirks
        normalized_symbol = symbol
        if exchange.lower() == 'cryptocom':
            # Crypto.com rejects some limits; cap safely to 100
            try:
                if limit is None or limit <= 0:
                    limit = 100
                elif limit > 100:
                    limit = 100
            except Exception:
                limit = 100
            # Ensure symbol has slash format if provided without one
            if normalized_symbol and '/' not in normalized_symbol:
                if normalized_symbol.endswith('USD') and len(normalized_symbol) >= 6:
                    base = normalized_symbol[:-3]
                    normalized_symbol = f"{base}/USD"
                elif normalized_symbol.endswith('USDC') and len(normalized_symbol) >= 7:
                    base = normalized_symbol[:-4]
                    normalized_symbol = f"{base}/USDC"
        # Primary fetch
        try:
            trades = await exchange_instance.fetch_my_trades(symbol=normalized_symbol, since=since, limit=limit)
        except Exception as e:
            # Retry without limit if Crypto.com complains about invalid limit
            if exchange.lower() == 'cryptocom' and 'invalid limit' in str(e).lower():
                trades = await exchange_instance.fetch_my_trades(symbol=normalized_symbol, since=since)
            else:
                raise
        return {"trades": trades}
    except HTTPException:
        raise
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, "get_my_trades")
        raise HTTPException(status_code=500, detail=str(e))

# WebSocket status endpoints
@app.get("/api/v1/websocket/{exchange}/status")
async def get_websocket_status(exchange: str):
    """Get WebSocket connection status for an exchange"""
    handler = websocket_manager.get_handler(exchange)
    if not handler:
        return {"connected": False, "reason": "Handler not found"}
    
    return {
        "connected": handler.is_connected,
        "last_update": handler.last_update,
        "registered_callbacks": len(handler.order_callbacks)
    }

@app.post("/api/v1/websocket/{exchange}/subscribe")
async def subscribe_symbols(exchange: str, payload: Dict[str, Any]):
    """Subscribe to live tickers for given symbols (symbol format without slash, e.g., BTCUSD)."""
    handler = websocket_manager.get_handler(exchange)
    if not handler or not handler.is_connected:
        raise HTTPException(status_code=503, detail="WebSocket not connected")
    symbols = payload.get('symbols') or []
    if not isinstance(symbols, list) or not symbols:
        raise HTTPException(status_code=400, detail="symbols must be a non-empty list")
    await handler.subscribe(symbols)
    return {"subscribed": symbols}

@app.post("/api/v1/websocket/{exchange}/unsubscribe")
async def unsubscribe_symbols(exchange: str, payload: Dict[str, Any]):
    handler = websocket_manager.get_handler(exchange)
    if not handler or not handler.is_connected:
        raise HTTPException(status_code=503, detail="WebSocket not connected")
    symbols = payload.get('symbols') or []
    if not isinstance(symbols, list) or not symbols:
        raise HTTPException(status_code=400, detail="symbols must be a non-empty list")
    await handler.unsubscribe(symbols)
    return {"unsubscribed": symbols}

# Startup and shutdown events
@app.on_event("startup")
async def startup_event():
    """Initialize exchanges on startup"""
    await initialize_exchanges()

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    await websocket_manager.shutdown_all()
    
    # Close all CCXT async exchange instances
    for exchange_name, exchange_info in exchanges.items():
        try:
            if 'instance' in exchange_info and hasattr(exchange_info['instance'], 'close'):
                await exchange_info['instance'].close()
                logger.info(f"Closed {exchange_name} exchange instance")
        except Exception as e:
            logger.warning(f"Error closing {exchange_name} exchange: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8003)