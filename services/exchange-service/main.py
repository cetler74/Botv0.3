"""
Exchange Service for the Multi-Exchange Trading Bot - FIXED VERSION
Multi-exchange operations and market data management with proper WebSocket support
"""

import asyncio
import json
import logging
import os
import time
import uuid
import websockets
import yaml
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

import ccxt.async_support as ccxt
import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Response
from pydantic import BaseModel
from binance_websocket_integration import binance_websocket, router as binance_ws_router
# from test_price_integration import test_price_integration
from cryptocom_websocket_integration import cryptocom_websocket, router as cryptocom_ws_router
from bybit_websocket_integration import router as bybit_ws_router
from health_monitor import HealthMonitorService, binance_websocket_health_check, cryptocom_websocket_health_check, redis_health_check

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load configuration
def load_config():
    """Load configuration from config.yaml"""
    try:
        config_path = os.path.join(os.path.dirname(__file__), '../../config/config.yaml')
        with open(config_path, 'r') as file:
            return yaml.safe_load(file)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return {}

# Global config
config = load_config()
trading_config = config.get('trading', {})

app = FastAPI(title="Exchange Service", version="1.0.0")

# Include WebSocket routers
app.include_router(binance_ws_router)
app.include_router(cryptocom_ws_router)
app.include_router(bybit_ws_router)

# Global health monitor
health_monitor: Optional[HealthMonitorService] = None

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
        # Task lifecycle management
        self.listener_task = None
        self.reconnect_task = None
        self.should_stop = False
        
    async def connect(self) -> bool:
        """Establish WebSocket connection to exchange"""
        try:
            if not self.websocket_url:
                logger.warning(f"No WebSocket URL configured for {self.exchange_name}")
                return False
                
            logger.info(f"Connecting to {self.exchange_name} WebSocket: {self.websocket_url}")
            self.connection = await websockets.connect(
                self.websocket_url,
                open_timeout=60,
                close_timeout=15,
            )
            self.is_connected = True
            self.last_update = datetime.utcnow().isoformat()
            
            # Start listening for messages with proper task management
            if self.listener_task:
                self.listener_task.cancel()
            self.listener_task = asyncio.create_task(self._listen_for_messages())
            
            # Start automatic reconnection monitoring
            if self.reconnect_task:
                self.reconnect_task.cancel()
            self.reconnect_task = asyncio.create_task(self._monitor_connection())
            
            logger.info(f"✅ {self.exchange_name} WebSocket connected with listener task")
            return True

        except asyncio.CancelledError:
            self.is_connected = False
            logger.warning(
                "%s WebSocket connect cancelled (shutdown or parent task cancel)",
                self.exchange_name,
            )
            raise

        except Exception as e:
            self.is_connected = False
            logger.error(
                "Failed to connect to %s WebSocket (%s): %s: %r",
                self.exchange_name,
                self.websocket_url,
                type(e).__name__,
                e,
            )
            return False
    
    async def disconnect(self):
        """Close WebSocket connection and cleanup tasks"""
        self.should_stop = True
        
        # Cancel tasks
        if self.listener_task:
            self.listener_task.cancel()
            try:
                await self.listener_task
            except asyncio.CancelledError:
                pass
            self.listener_task = None
            
        if self.reconnect_task:
            self.reconnect_task.cancel()
            try:
                await self.reconnect_task
            except asyncio.CancelledError:
                pass
            self.reconnect_task = None
        
        # Close connection
        if self.connection:
            try:
                await self.connection.close()
            except:
                pass
            finally:
                self.connection = None
                self.is_connected = False
                logger.info(f"🔌 Disconnected from {self.exchange_name} WebSocket")
    
    async def _listen_for_messages(self):
        """Listen for incoming WebSocket messages with proper lifecycle management"""
        logger.info(f"🎧 Starting message listener for {self.exchange_name}")
        try:
            async for message in self.connection:
                if self.should_stop:
                    logger.info(f"🛑 Message listener stopped for {self.exchange_name}")
                    break
                    
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
            if not self.should_stop:
                logger.warning(f"⚠️ WebSocket connection closed unexpectedly for {self.exchange_name}")
            self.is_connected = False
        except asyncio.CancelledError:
            logger.info(f"🔄 Message listener cancelled for {self.exchange_name}")
            raise
        except Exception as e:
            logger.error(f"❌ Error in WebSocket listener for {self.exchange_name}: {e}")
            self.is_connected = False
        finally:
            logger.info(f"🔚 Message listener ended for {self.exchange_name}")
    
    async def _monitor_connection(self):
        """Monitor connection health and auto-reconnect if needed"""
        logger.info(f"📡 Starting connection monitor for {self.exchange_name}")
        
        while not self.should_stop:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                
                if not self.is_connected and not self.should_stop:
                    logger.warning(f"🔄 Connection lost for {self.exchange_name}, attempting reconnect...")
                    
                    # Attempt reconnection
                    try:
                        if self.connection:
                            try:
                                await self.connection.close()
                            except:
                                pass
                                
                        # Wait a bit before reconnecting
                        await asyncio.sleep(5)
                        
                        # Reconnect
                        logger.info(f"🔄 Reconnecting to {self.exchange_name} WebSocket...")
                        self.connection = await websockets.connect(
                            self.websocket_url,
                            open_timeout=60,
                            close_timeout=15,
                        )
                        self.is_connected = True
                        self.last_update = datetime.utcnow().isoformat()
                        
                        # Restart message listener
                        if self.listener_task:
                            self.listener_task.cancel()
                        self.listener_task = asyncio.create_task(self._listen_for_messages())
                        
                        logger.info(f"✅ Reconnected to {self.exchange_name} WebSocket")
                        
                        # Re-subscribe to symbols if any
                        if self.subscribed_symbols:
                            logger.info(f"🔄 Re-subscribing to {len(self.subscribed_symbols)} symbols for {self.exchange_name}")
                            await self._resubscribe_symbols()
                        
                    except Exception as reconnect_error:
                        logger.error(f"❌ Reconnection failed for {self.exchange_name}: {reconnect_error}")
                        await asyncio.sleep(30)  # Wait longer before next attempt
                        
            except asyncio.CancelledError:
                logger.info(f"🛑 Connection monitor cancelled for {self.exchange_name}")
                break
            except Exception as e:
                logger.error(f"❌ Error in connection monitor for {self.exchange_name}: {e}")
                await asyncio.sleep(60)
    
    async def _resubscribe_symbols(self):
        """Re-subscribe to all previously subscribed symbols after reconnection"""
        if not self.subscribed_symbols:
            return
            
        try:
            symbols_list = list(self.subscribed_symbols)
            logger.info(f"🔄 Re-subscribing to symbols for {self.exchange_name}: {symbols_list}")
            
            # Clear and re-subscribe to ensure clean state
            temp_symbols = self.subscribed_symbols.copy()
            self.subscribed_symbols.clear()
            await self.subscribe(symbols_list)
            
            logger.info(f"✅ Successfully re-subscribed to {len(symbols_list)} symbols for {self.exchange_name}")
            
        except Exception as e:
            logger.error(f"❌ Failed to re-subscribe symbols for {self.exchange_name}: {e}")
    
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
                        if last:  # Only log if we have a valid price
                            logger.info(f"🎯 Binance ticker: {symbol} = ${last} (bid: ${bid}, ask: ${ask})")
                    self.last_update = datetime.utcnow().isoformat()
                    return True
                elif isinstance(data, dict) and data.get('s') and data.get('c'):
                    symbol = data.get('s')
                    last = data.get('c')
                    bid = data.get('b')
                    ask = data.get('a')
                    _update_ticker_cache(self.exchange_name, symbol, last, bid, ask)
                    if last:  # Only log if we have a valid price
                        logger.info(f"🎯 Binance ticker: {symbol} = ${last} (bid: ${bid}, ask: ${ask})")
                    self.last_update = datetime.utcnow().isoformat()
                    return True

            # Bybit v5 public spot tickers
            if self.exchange_name.lower() == 'bybit':
                if isinstance(data, dict) and data.get('topic') and 'tickers' in str(data.get('topic')):
                    ticker_data = data.get('data')
                    if isinstance(ticker_data, dict):
                        # Bybit data is a single object, not an array
                        symbol = ticker_data.get('symbol')
                        last = ticker_data.get('lastPrice')
                        bid = ticker_data.get('bid1Price')
                        ask = ticker_data.get('ask1Price')
                        if symbol and last:
                            _update_ticker_cache(self.exchange_name, symbol, last, bid, ask)
                            logger.info(f"🎯 Bybit ticker: {symbol} = ${last} (bid: ${bid}, ask: ${ask})")
                    self.last_update = datetime.utcnow().isoformat()
                    return True

            # Crypto.com public ticker - handle both subscription responses and ticker data
            if self.exchange_name.lower() == 'cryptocom':
                # Handle subscription confirmations - also process initial ticker data
                if isinstance(data, dict) and data.get('method') == 'subscribe' and data.get('code') == 0:
                    result = data.get('result', {})
                    logger.info(f"Crypto.com subscription confirmed: {result.get('subscription', 'unknown')}")
                    
                    # Process initial ticker data from subscription response
                    initial_data = result.get('data', [])
                    if isinstance(initial_data, list) and initial_data:
                        for ticker_item in initial_data:
                            if isinstance(ticker_item, dict) and ticker_item.get('i'):
                                instrument = ticker_item.get('i')
                                last_price = ticker_item.get('a')
                                bid_price = ticker_item.get('b')
                                ask_price = ticker_item.get('k')
                                
                                if instrument and last_price:
                                    _update_ticker_cache(self.exchange_name, instrument, last_price, bid_price, ask_price)
                                    logger.info(f"🎯 Crypto.com initial ticker: {instrument} = ${last_price} (bid: ${bid_price}, ask: ${ask_price})")
                    
                    self.last_update = datetime.utcnow().isoformat()
                    return True
                
                # Handle direct ticker data format from current API (simple format)
                if isinstance(data, dict) and data.get('i'):  # 'i' = instrument name
                    instrument = data.get('i')
                    last_price = data.get('a')  # 'a' = latest trade price
                    bid_price = data.get('b')   # 'b' = best bid price  
                    ask_price = data.get('k')   # 'k' = best ask price
                    
                    if instrument and last_price:
                        # Store with the instrument format that matches our cache keys
                        _update_ticker_cache(self.exchange_name, instrument, last_price, bid_price, ask_price)
                        logger.info(f"🎯 Crypto.com ticker update: {instrument} = ${last_price} (bid: ${bid_price}, ask: ${ask_price})")
                        self.last_update = datetime.utcnow().isoformat()
                        return True
                
                # Handle legacy subscription format (method=subscription with params)
                if isinstance(data, dict) and data.get('method') == 'subscription':
                    params = data.get('params', {})
                    channel = params.get('channel', '')
                    if channel.startswith('ticker.'):
                        ticker_data = params.get('data', {})
                        if ticker_data:
                            # Extract instrument name from channel (e.g., ticker.AAVE_USD -> AAVE_USD)
                            instrument = channel.replace('ticker.', '')
                            last = ticker_data.get('a')  # last price
                            bid = ticker_data.get('b')   # bid price  
                            ask = ticker_data.get('k')   # ask price
                            
                            if instrument and last:
                                # Store with the instrument format that matches our cache keys (AAVE_USD)
                                _update_ticker_cache(self.exchange_name, instrument, last, bid, ask)
                                logger.info(f"🎯 Crypto.com legacy ticker update: {instrument} = ${last} (bid: ${bid}, ask: ${ask})")
                            
                        self.last_update = datetime.utcnow().isoformat()
                        return True
                
                # Fallback: Handle old format for compatibility
                if isinstance(data, dict) and data.get('result') and isinstance(data['result'], dict):
                    items = data['result'].get('data') or []
                    if isinstance(items, dict):
                        items = [items]
                    for item in items:
                        instr = item.get('i') or item.get('instrument_name') or item.get('s')
                        if not instr:
                            continue
                        # Keep underscore format for cache key consistency
                        symbol = instr.replace('/', '_') if '/' in instr else instr
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
            # Bybit subscribe (limit: 10 args per request)
            if self.exchange_name.lower() == 'bybit' and self.connection:
                # Bybit V5 SPOT topic format: tickers.{SYMBOL_NO_SLASH}, e.g. tickers.BTCUSDT
                # Normalise so callers can pass either "BTCUSDT" or "BTC/USDT".
                topics = [f"tickers.{sym.replace('/', '')}" for sym in new_syms]
                # Split into chunks of 10 to respect Bybit's limit
                for i in range(0, len(topics), 10):
                    chunk = topics[i:i+10]
                    msg = {"op": "subscribe", "args": chunk}
                    await self.connection.send(json.dumps(msg))
            # Crypto.com subscribe
            if self.exchange_name.lower() == 'cryptocom' and self.connection:
                # Crypto.com expects ticker channels in format: ticker.INSTRUMENT_NAME (e.g., ticker.AAVE_USD)
                channels = []
                for s in new_syms:
                    # Convert symbol format: AAVE/USD -> ticker.AAVE_USD, BTCUSDC -> ticker.BTC_USDC
                    if '/' in s:
                        # Symbol already has slash: AAVE/USD -> AAVE_USD
                        instrument = s.replace('/', '_')
                    elif s.endswith('USD'):
                        # Symbol like AAVEUSD -> AAVE_USD
                        instrument = f"{s[:-3]}_USD"
                    elif s.endswith('USDC'):
                        # Symbol like BTCUSDC -> BTC_USDC  
                        instrument = f"{s[:-4]}_USDC"
                    else:
                        instrument = s
                    
                    channels.append(f"ticker.{instrument}")
                
                if channels:
                    # Crypto.com WebSocket API format - simplified without id/nonce
                    msg = {"method": "subscribe", "params": {"channels": channels}}
                    await self.connection.send(json.dumps(msg))
                    logger.info(f"Crypto.com subscription request: {msg}")
        except Exception as e:
            logger.warning(f"Subscription error for {self.exchange_name}: {e}")

    async def unsubscribe(self, symbols: List[str]) -> None:
        try:
            remove_syms = set(symbols)
            self.subscribed_symbols -= remove_syms
            # Optional: send unsubscribe frames if needed for specific exchanges
            if self.exchange_name.lower() == 'bybit' and self.connection:
                topics = [f"tickers.{sym.replace('/', '')}" for sym in remove_syms]
                msg = {"op": "unsubscribe", "args": topics}
                await self.connection.send(json.dumps(msg))
            if self.exchange_name.lower() == 'cryptocom' and self.connection:
                # Unsubscribe from specific ticker channels
                channels = []
                for s in remove_syms:
                    if '/' in s:
                        instrument = s.replace('/', '_')
                    elif s.endswith('USD'):
                        instrument = f"{s[:-3]}_USD"
                    elif s.endswith('USDC'):
                        instrument = f"{s[:-4]}_USDC"
                    else:
                        instrument = s
                    channels.append(f"ticker.{instrument}")
                
                if channels:
                    # Crypto.com WebSocket API format - simplified without id/nonce  
                    msg = {"method": "unsubscribe", "params": {"channels": channels}}
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

# Optional Redis mirror of WS tickers (same key scheme as orchestrator ``ticker:live:{exchange|sym}``).
_ticker_redis_mirror: Optional[Any] = None
_ticker_redis_mirror_disabled: bool = False


def _get_ticker_redis_mirror():
    """Lazy sync Redis client for publishing ``ticker:live:*`` hashes from WebSocket updates."""
    global _ticker_redis_mirror, _ticker_redis_mirror_disabled
    if _ticker_redis_mirror_disabled:
        return None
    if _ticker_redis_mirror is not None:
        return _ticker_redis_mirror
    if os.getenv("TICKER_REDIS_MIRROR", "1").strip() == "0":
        _ticker_redis_mirror_disabled = True
        return None
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        _ticker_redis_mirror_disabled = True
        return None
    try:
        import redis as redis_sync

        _ticker_redis_mirror = redis_sync.Redis.from_url(url, decode_responses=True)
        _ticker_redis_mirror.ping()
        logger.info("✅ Ticker Redis mirror enabled (WS → Redis for orchestrator fast path)")
        return _ticker_redis_mirror
    except Exception as e:
        logger.warning("Ticker Redis mirror disabled: %s", e)
        _ticker_redis_mirror = None
        _ticker_redis_mirror_disabled = True
        return None


def _normalize_symbol_key(exchange: str, symbol: str) -> str:
    # Handle symbol normalization per exchange requirements
    if exchange.lower() == 'cryptocom':
        # For Crypto.com, keep underscore format for consistency with WebSocket data
        if '/' in symbol:
            normalized = symbol.replace('/', '_')
        else:
            normalized = symbol
    else:
        # For other exchanges, strip non-alphanumeric characters
        normalized = ''.join(ch for ch in str(symbol) if ch.isalnum())
    
    return f"{exchange}:{normalized}"


def _normalize_cryptocom_symbol_for_ccxt(symbol: str) -> str:
    """
    Map URL/path symbols to CCXT Crypto.com unified symbols.

    CCXT lists Crypto.com *spot* pairs as BASE/USD or BASE/USDC. The previous
    logic rewrote every ``.../USD`` into ``.../USD:USD``, which is invalid for
    spot (e.g. AI16Z/USD -> AI16Z/USD:USD) and triggers "does not have market symbol".
    Callers that truly need a settled/margin symbol can pass it with ``:`` already present.
    """
    s = (symbol or "").strip()
    if "/" not in s:
        # Check USDC before USD — e.g. ALICEUSDC also endswith "USD"
        if len(s) >= 6 and s.endswith("USDC"):
            base = s[:-4]
            s = f"{base}/USDC"
        elif len(s) >= 6 and s.endswith("USD"):
            base = s[:-3]
            s = f"{base}/USD"
    # Collapse mistaken or legacy double suffix from callers / old clients
    if s.endswith("/USD:USD"):
        s = s[: -len(":USD")]
    elif s.endswith("/USDC:USDC"):
        s = s[: -len(":USDC")]
    return s


def _update_ticker_cache(
    exchange: str,
    symbol: str,
    last: Any,
    bid: Any,
    ask: Any,
    *,
    redis_source: str = "websocket",
) -> None:
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
        rc = _get_ticker_redis_mirror()
        if rc:
            try:
                rk = "ticker:live:" + key.replace(":", "|")
                rc.hset(
                    rk,
                    mapping={
                        "last": str(last_f),
                        "bid": str(bid_f) if bid_f is not None else "",
                        "ask": str(ask_f) if ask_f is not None else "",
                        "timestamp": ticker_cache[key]["timestamp"],
                        "source": redis_source,
                    },
                )
                rc.expire(rk, 300)
            except Exception as ex:
                logger.debug("ticker redis mirror publish failed: %s", ex)
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
# CCXT clients with no API keys for public market data (OHLCV). Authenticated instances
# often fail from Docker (-2015 on Binance, IP mismatch on Bybit) even for public endpoints.
public_market_clients: Dict[str, Any] = {}
# One lock per exchange so Binance load_markets() does not block Bybit/Crypto tickers.
_public_market_client_locks: Dict[str, asyncio.Lock] = {}


def _public_market_lock_for(exchange_name: str) -> asyncio.Lock:
    lock = _public_market_client_locks.get(exchange_name)
    if lock is None:
        lock = asyncio.Lock()
        _public_market_client_locks[exchange_name] = lock
    return lock


async def get_public_market_client(exchange_name: str):
    """Lazy async CCXT client: same options/sandbox as live instance, no apiKey/secret."""
    global public_market_clients
    if exchange_name in public_market_clients:
        return public_market_clients[exchange_name]
    async with _public_market_lock_for(exchange_name):
        if exchange_name in public_market_clients:
            return public_market_clients[exchange_name]
        if exchange_name not in exchanges:
            raise HTTPException(status_code=404, detail=f"Exchange {exchange_name} not found")
        live = exchanges[exchange_name]["instance"]
        exchange_class = type(live)
        cfg: Dict[str, Any] = {
            "enableRateLimit": True,
            "rateLimit": getattr(live, "rateLimit", 100) or 100,
            "sandbox": bool(getattr(live, "sandbox", False)),
            # CCXT: milliseconds; avoids hanging the event loop when outbound is black-holed.
            "timeout": 25000,
        }
        opts = getattr(live, "options", None)
        if isinstance(opts, dict) and opts:
            cfg["options"] = dict(opts)
        client = exchange_class(cfg)
        try:
            await asyncio.wait_for(client.load_markets(), timeout=20.0)
        except Exception:
            try:
                await client.close()
            except Exception:
                pass
            raise
        public_market_clients[exchange_name] = client
        logger.info(
            "Initialized public market CCXT client for %s (OHLCV; no API credentials)",
            exchange_name,
        )
        return client
config_service_url = os.getenv("CONFIG_SERVICE_URL", "http://config-service:8001")
database_service_url = os.getenv(
    "DATABASE_SERVICE_URL", "http://database-service:8002"
)

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
                            'defaultType': 'spot',          # SPOT markets only (matches WS /v5/public/spot)
                            'accountsByType': {'spot': 'UNIFIED'},  # but route account ops through UTA
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
                    
                    # Initialize WebSocket handler with correct URLs
                    websocket_config = config.copy()
                    
                    # Add proper WebSocket URLs for each exchange
                    if exchange_name == 'binance':
                        websocket_config['websocket_url'] = 'wss://stream.binance.com:9443/ws/!ticker@arr'
                    elif exchange_name == 'cryptocom':
                        # Must match Exchange public market WS (see exchange-docs v1 rest-ws).
                        # v2/market was unreliable vs integration ``exchange/v1/market``.
                        websocket_config['websocket_url'] = (
                            'wss://stream.crypto.com/exchange/v1/market'
                        )
                    elif exchange_name == 'bybit':
                        websocket_config['websocket_url'] = 'wss://stream.bybit.com/v5/public/spot'
                    else:
                        websocket_config['websocket_url'] = config.get('websocket_url')  # Fallback
                    
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
        """Handle exchange errors and create alerts.

        PnL-FIX v3: Detect Bybit retCode:10010 ("Unmatched IP") and mark the
        exchange as 'auth_failed' so that:
          (a) the orchestrator can skip it cleanly and not trade on stale prices,
          (b) the log output isn't spammed every cycle (one loud warning is enough).
        """
        err_str = str(error)
        is_ip_whitelist = ('retCode":10010' in err_str or 'Unmatched IP' in err_str
                           or 'retCode":10003' in err_str or 'retCode":10007' in err_str)

        # Update exchange health
        if exchange_name in exchanges:
            h = exchanges[exchange_name].setdefault('health', {})
            h['error_count'] = h.get('error_count', 0) + 1
            if is_ip_whitelist:
                # Permanent until the user updates the whitelist. Log ONCE loudly.
                if h.get('status') != 'auth_failed':
                    import requests as _rq
                    try:
                        public_ip = _rq.get('https://api.ipify.org', timeout=3).text.strip()
                    except Exception:
                        public_ip = 'unknown'
                    logger.error(
                        f"🚨 [AUTH] {exchange_name} API key is IP-whitelisted and does NOT "
                        f"include the current public IP ({public_ip}). "
                        f"Fix: open the Bybit dashboard → API management → edit the key's IP "
                        f"whitelist to include {public_ip}, OR remove the whitelist entirely. "
                        f"Marking {exchange_name} as auth_failed — it will be skipped until restart."
                    )
                    h['status'] = 'auth_failed'
                    h['public_ip_seen'] = public_ip
                # Suppress the repeated ERROR line for this operation once auth_failed is set.
                logger.debug(f"[AUTH SUPPRESSED] {exchange_name}.{operation}: 10010 Unmatched IP")
                return
            else:
                h['status'] = 'degraded'

        error_msg = f"Exchange error on {exchange_name} during {operation}: {err_str}"
        logger.error(error_msg)

        # Best-effort alert: short timeout, never fail the HTTP handler. Python 3.11+
        # asyncio.CancelledError inherits from BaseException (not Exception), so a
        # bare ``except Exception`` lets CancelledError escape and crashes ASGI
        # when the client disconnects mid-request or under cancellation storms.
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                # Must match database-service ``Alert`` model (POST /api/v1/alerts).
                alert_data = {
                    "alert_id": str(uuid.uuid4()),
                    "level": "WARNING",
                    "category": "exchange",
                    "message": f"{operation}: {err_str[:4000]}",
                    "exchange": exchange_name,
                    "details": {"operation": operation, "error": err_str[:8000]},
                    "created_at": datetime.utcnow().isoformat() + "Z",
                    "resolved": False,
                }
                r = await client.post(
                    f"{database_service_url}/api/v1/alerts", json=alert_data
                )
                if r.status_code >= 400:
                    logger.debug(
                        "Alert POST returned %s: %s", r.status_code, r.text[:500]
                    )
        except asyncio.CancelledError:
            logger.debug(
                "Alert post cancelled for %s %s (client disconnect or task cancel)",
                exchange_name,
                operation,
            )
        except Exception as alert_error:
            logger.warning(
                "Failed to create alert for exchange error: %s", alert_error
            )

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
    """Get ticker information for a symbol - FIXED VERSION

    PnL-FIX v3: Prefer the WebSocket ticker cache (fresh < 15s) to avoid
    hammering the CCXT REST throttle queue (the root cause of the Bybit
    ticker 500 errors — ``throttle queue is over maxCapacity (2000)``).
    Fall back to REST only when no fresh WS tick is available.
    """
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")

    # Short-circuit: if the exchange is marked auth_failed, return 503 immediately.
    try:
        if exchanges.get(exchange, {}).get('health', {}).get('status') == 'auth_failed':
            raise HTTPException(status_code=503, detail=f"{exchange} auth_failed — check API key IP whitelist")
    except HTTPException:
        raise
    except Exception:
        pass

    # Try WebSocket cache first (fresh for 15s is plenty for entry decisions).
    try:
        key = _normalize_symbol_key(exchange, symbol)
        cached = ticker_cache.get(key)
        if cached and cached.get('timestamp'):
            try:
                age = (datetime.utcnow() - datetime.fromisoformat(cached['timestamp'])).total_seconds()
            except Exception:
                age = 9999
            if age <= 15 and cached.get('last'):
                return {
                    'symbol': symbol,
                    'last': cached.get('last'),
                    'bid': cached.get('bid'),
                    'ask': cached.get('ask'),
                    'high': cached.get('high'),
                    'low': cached.get('low'),
                    'volume': cached.get('volume'),
                    'timestamp': cached.get('timestamp'),
                    'source': 'websocket_cache'
                }
    except Exception as _cache_err:
        logger.debug(f"[ticker cache miss] {exchange}/{symbol}: {_cache_err}")

    try:
        # Convert symbol format for different exchanges
        exchange_symbol = symbol
        if exchange == 'cryptocom':
            exchange_symbol = _normalize_cryptocom_symbol_for_ccxt(symbol)
        elif exchange in ['binance', 'bybit']:
            # Binance and Bybit need symbols with slashes, convert from BTCUSD to BTC/USD.
            # Skip if the input already contains a slash (e.g. "TRX/USDC") — otherwise
            # the suffix-strip would produce malformed "TRX//USDC" and cause a 404.
            if '/' not in symbol:
                if len(symbol) >= 7 and symbol.endswith('USDC'):
                    base = symbol[:-4]
                    exchange_symbol = f"{base}/USDC"
                elif len(symbol) >= 7 and symbol.endswith('USDT'):
                    base = symbol[:-4]
                    exchange_symbol = f"{base}/USDT"
                elif len(symbol) >= 6 and symbol.endswith('USD'):
                    base = symbol[:-3]
                    exchange_symbol = f"{base}/USD"
        
        await exchange_manager._rate_limit(exchange)

        # Public market data only — same as OHLCV (avoids Binance -2015 / Bybit IP mismatch on keys)
        exchange_instance = await get_public_market_client(exchange)

        if not hasattr(exchange_instance, "fetch_ticker"):
            raise Exception(
                f"Invalid exchange instance for {exchange}: missing fetch_ticker method"
            )

        # Retry logic with exponential backoff
        max_retries = 3
        retry_delay = 1  # Start with 1 second
        ticker = None

        for attempt in range(max_retries):
            try:
                start_time = time.time()
                ticker = await asyncio.wait_for(
                    exchange_instance.fetch_ticker(exchange_symbol),
                    timeout=18.0,
                )
                response_time = time.time() - start_time
                break  # Success, exit retry loop
            except Exception as e:
                # PnL-FIX v3: don't retry CCXT throttle-queue overflows — they'll
                # just pile up more requests and make the problem worse. Return
                # the last-known WS cache value (even if slightly stale) or 503.
                err_lc = str(e).lower()
                if 'throttle queue' in err_lc or 'maxcapacity' in err_lc:
                    logger.warning(
                        f"[THROTTLE] {exchange} ticker for {exchange_symbol}: queue full, "
                        f"serving cached price if available"
                    )
                    try:
                        key = _normalize_symbol_key(exchange, symbol)
                        cached = ticker_cache.get(key)
                        if cached and cached.get('last'):
                            return {
                                'symbol': symbol,
                                'last': cached.get('last'),
                                'bid': cached.get('bid'),
                                'ask': cached.get('ask'),
                                'timestamp': cached.get('timestamp'),
                                'source': 'stale_cache_throttle_fallback'
                            }
                    except Exception:
                        pass
                    raise HTTPException(status_code=503, detail=f"{exchange} REST throttle queue full; no cached price available")

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
        
        # Check for test price override
        # test_price = await test_price_integration.get_test_price(exchange, symbol)
        # if test_price:
        #     # Override the price with test value
        #     ticker['last'] = test_price
        #     ticker['test_override'] = True
        #     ticker['original_price'] = ticker.get('last', test_price)
        #     logger.info(f"🧪 Test price override applied: {exchange}/{symbol} = ${test_price:.6f}")

        # Keep in-memory ticker cache + Redis ``ticker:live:*`` in sync when traffic is REST-heavy
        # (WebSocket-only setups already populate via _handle_ticker_update).
        try:
            last_px = ticker.get("last") or ticker.get("close")
            if last_px is not None and float(last_px) > 0:
                _update_ticker_cache(
                    exchange,
                    symbol,
                    last_px,
                    ticker.get("bid"),
                    ticker.get("ask"),
                    redis_source="rest",
                )
        except Exception:
            pass
        
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
    """Public order book only — must not use the signed CCXT instance (avoids Binance
    sapi/capital/config, Bybit asset/coin/query-info, etc. during load_markets / implicit
    private prefetch).
    """
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")

    try:
        exchange_symbol = symbol
        if exchange == "cryptocom":
            exchange_symbol = _normalize_cryptocom_symbol_for_ccxt(symbol)
        elif exchange in ("binance", "bybit"):
            if "/" not in symbol:
                if len(symbol) >= 7 and symbol.endswith("USDC"):
                    base = symbol[:-4]
                    exchange_symbol = f"{base}/USDC"
                elif len(symbol) >= 7 and symbol.endswith("USDT"):
                    base = symbol[:-4]
                    exchange_symbol = f"{base}/USDT"
                elif len(symbol) >= 6 and symbol.endswith("USD"):
                    base = symbol[:-3]
                    exchange_symbol = f"{base}/USD"

        await exchange_manager._rate_limit(exchange)

        exchange_instance = await get_public_market_client(exchange)
        if not hasattr(exchange_instance, "fetch_order_book"):
            raise Exception(
                f"Invalid exchange instance for {exchange}: missing fetch_order_book method"
            )

        start_time = time.time()
        orderbook = await asyncio.wait_for(
            exchange_instance.fetch_order_book(exchange_symbol, limit=limit),
            timeout=18.0,
        )
        response_time = time.time() - start_time

        exchanges[exchange]["health"]["status"] = "healthy"
        exchanges[exchange]["health"]["response_time"] = response_time
        exchanges[exchange]["health"]["last_check"] = datetime.utcnow()

        return orderbook

    except asyncio.TimeoutError as e:
        await exchange_manager._handle_exchange_error(
            exchange, e, f"get_orderbook({symbol})"
        )
        raise HTTPException(
            status_code=504,
            detail=f"Orderbook request timed out for {exchange}/{symbol}",
        ) from e
    except Exception as e:
        error_msg = str(e).lower()
        if any(
            phrase in error_msg
            for phrase in (
                "does not have market symbol",
                "symbol not found",
                "invalid symbol",
                "market not found",
            )
        ):
            logger.warning("Symbol %s not found on %s: %s", exchange_symbol, exchange, e)
            raise HTTPException(
                status_code=404, detail=f"Symbol {symbol} not found on {exchange}"
            )
        await exchange_manager._handle_exchange_error(
            exchange, e, f"get_orderbook({symbol})"
        )
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
            # Binance minimum order validation - now based on $200 minimum notional value
            # Get current price to calculate notional value
            current_price = None
            try:
                # Use symbol format without slashes for Binance ticker lookup (BTC/USDC -> BTCUSDC)
                ticker_symbol = order_request.symbol.replace('/', '')
                ticker_response = await get_ticker(exchange, ticker_symbol)
                current_price = float(ticker_response.get('last', 0))
            except Exception as e:
                logger.warning(f"Could not get current price for {order_request.symbol} on {exchange}: {e}")
                
            if current_price:
                notional_value = order_request.amount * current_price
                min_notional = trading_config.get('min_order_size_usd', {}).get('binance', 100.0)  # Get from config
                
                # IMPORTANT: Only apply minimum order value to BUY orders, not SELL orders
                # SELL orders should be allowed to close any existing position regardless of size
                if order_request.side.lower() == 'buy' and notional_value < min_notional:
                    logger.warning(f"⚠️  BUY order notional ${notional_value:.2f} below minimum ${min_notional} for {order_request.symbol} on {exchange}")
                    raise HTTPException(status_code=422, 
                        detail=f"MINIMUM_ORDER_VALUE: Order value ${notional_value:.2f} below Binance minimum ${min_notional} USD for {order_request.symbol}. Current amount {order_request.amount} at ${current_price:.4f} = ${notional_value:.2f}. Need at least {min_notional/current_price:.4f} tokens.")
                elif order_request.side.lower() == 'sell':
                    logger.info(f"✅ SELL order allowed: ${notional_value:.2f} value for {order_request.symbol} on {exchange} (no minimum for sells)")
            
            # Also check absolute minimum amounts for specific assets (fallback)
            min_amounts = {
                'BTC/USDC': 0.00001,  # BTC minimum
                'BTC/USDT': 0.00001,
                'ETH/USDC': 0.0001,   # ETH minimum
                'ETH/USDT': 0.0001,
                'BNB/USDT': 0.01,     # BNB minimum
                'LINK/USDC': 1.0,     # LINK minimum is 1.0 LINK
                'LINK/USDT': 1.0,
                'NEO/USDC': 0.01,     # NEO minimum
                'default': 0.001      # Conservative default
            }
            min_amount = min_amounts.get(order_request.symbol, min_amounts['default'])
            # IMPORTANT: Only apply minimum amount validation to BUY orders, not SELL orders
            if order_request.side.lower() == 'buy' and order_request.amount < min_amount:
                logger.warning(f"⚠️  BUY order amount {order_request.amount} below minimum {min_amount} for {order_request.symbol} on {exchange}")
                raise HTTPException(status_code=422, 
                    detail=f"MINIMUM_ORDER_VALUE: Amount {order_request.amount} below Binance minimum {min_amount} for {order_request.symbol}. Binance requires minimum order values of ${min_notional} USD.")
            elif order_request.side.lower() == 'sell':
                logger.info(f"✅ SELL order allowed: {order_request.amount} {order_request.symbol} on {exchange} (no minimum amount for sells)")
        
        elif exchange == 'cryptocom':
            # Crypto.com minimum order validation - now based on $200 minimum notional value
            # Get current price to calculate notional value
            current_price = None
            try:
                ticker_response = await get_ticker(exchange, order_request.symbol)
                current_price = float(ticker_response.get('last', 0))
            except:
                logger.warning(f"Could not get current price for {order_request.symbol} on {exchange}")
                
            if current_price:
                notional_value = order_request.amount * current_price
                min_notional = trading_config.get('min_order_size_usd', {}).get('cryptocom', 100.0)  # Get from config
                
                # IMPORTANT: Only apply minimum order value to BUY orders, not SELL orders
                if order_request.side.lower() == 'buy' and notional_value < min_notional:
                    logger.warning(f"⚠️  BUY order notional ${notional_value:.2f} below minimum ${min_notional} for {order_request.symbol} on {exchange}")
                    raise HTTPException(status_code=422, 
                        detail=f"MINIMUM_ORDER_VALUE: Order value ${notional_value:.2f} below Crypto.com minimum ${min_notional} USD for {order_request.symbol}. Current amount {order_request.amount} at ${current_price:.4f} = ${notional_value:.2f}. Need at least {min_notional/current_price:.4f} tokens.")
                elif order_request.side.lower() == 'sell':
                    logger.info(f"✅ SELL order allowed: ${notional_value:.2f} value for {order_request.symbol} on {exchange} (no minimum for sells)")
            
            # Also check absolute minimum amounts for specific assets (fallback validation)
            min_amounts_crypto = {
                'BTC/USD': 0.00001,   # BTC minimum
                'ETH/USD': 0.0001,    # ETH minimum  
                'ADA/USD': 250.0,     # ADA ~$0.80 = $200 minimum
                'ALGO/USD': 200.0,    # ALGO ~$1.00 = $200 minimum
                'AVAX/USD': 5.0,      # AVAX ~$40 = $200 minimum
                'DOGE/USD': 600.0,    # DOGE ~$0.33 = $200 minimum
                'DOT/USD': 50.0,      # DOT ~$4.00 = $200 minimum
                'LTC/USD': 2.0,       # LTC ~$100 = $200 minimum
                'MATIC/USD': 200.0,   # MATIC ~$1.00 = $200 minimum
                'SOL/USD': 1.0,       # SOL ~$200 = $200 minimum
                'XRP/USD': 100.0,     # XRP ~$2.00 = $200 minimum
                'default': 0.001      # Conservative default
            }
            min_amount = min_amounts_crypto.get(order_request.symbol, min_amounts_crypto['default'])
            # IMPORTANT: Only apply minimum amount validation to BUY orders, not SELL orders
            if order_request.side.lower() == 'buy' and order_request.amount < min_amount:
                logger.warning(f"⚠️  BUY order amount {order_request.amount} below minimum {min_amount} for {order_request.symbol} on {exchange}")
                raise HTTPException(status_code=422, 
                    detail=f"MINIMUM_ORDER_VALUE: Amount {order_request.amount} below Crypto.com minimum {min_amount} for {order_request.symbol}. Crypto.com requires minimum order values of ${min_notional} USD.")
            elif order_request.side.lower() == 'sell':
                logger.info(f"✅ SELL order allowed: {order_request.amount} {order_request.symbol} on {exchange} (no minimum amount for sells)")
        
        elif exchange == 'bybit':
            # Bybit minimum order validation - based on minimum notional values
            # Bybit requires minimum order values of approximately $5-10 USD for most spot pairs
            current_price = None
            try:
                ticker_response = await get_ticker(exchange, order_request.symbol)
                current_price = float(ticker_response.get('last', 0))
            except:
                logger.warning(f"Could not get current price for {order_request.symbol} on {exchange}")
                
            if current_price:
                notional_value = order_request.amount * current_price
                min_notional = trading_config.get('min_order_size_usd', {}).get('bybit', 75.0)  # Get from config
                if notional_value < min_notional:
                    logger.warning(f"⚠️  Order notional ${notional_value:.2f} below minimum ${min_notional} for {order_request.symbol} on {exchange}")
                    raise HTTPException(status_code=422, 
                        detail=f"INSUFFICIENT_BALANCE: Order value ${notional_value:.2f} below Bybit minimum ${min_notional} USD for {order_request.symbol}. Current amount {order_request.amount} at ${current_price:.4f} = ${notional_value:.2f}. Need at least {min_notional/current_price:.4f} tokens.")
            
            # Also check absolute minimum amounts for specific assets on Bybit
            min_amounts_bybit = {
                'BTC/USDC': 0.00001,  # BTC minimum
                'BTC/USDT': 0.00001,
                'ETH/USDC': 0.0001,   # ETH minimum
                'ETH/USDT': 0.0001,
                'DOT/USDC': 1.2,      # DOT minimum - needs ~$5 USD at $4.19 = 1.2 DOT
                'DOT/USDT': 1.2,
                'SOL/USDC': 0.01,     # SOL minimum
                'SOL/USDT': 0.01,
                'AVAX/USDC': 0.1,     # AVAX minimum
                'AVAX/USDT': 0.1,
                'MATIC/USDC': 5.0,    # MATIC minimum for ~$5 USD
                'MATIC/USDT': 5.0,
                'default': 0.001      # Conservative default
            }
            min_amount = min_amounts_bybit.get(order_request.symbol, min_amounts_bybit['default'])
            if order_request.amount < min_amount:
                logger.warning(f"⚠️  Dust amount {order_request.amount} below minimum {min_amount} for {order_request.symbol} on {exchange}")
                raise HTTPException(status_code=422, 
                    detail=f"INSUFFICIENT_BALANCE: Amount {order_request.amount} below Bybit minimum {min_amount} for {order_request.symbol}. Bybit requires minimum order values of ~$5 USD.")
        
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
        
        # Normalize CCXT order response for consistent orchestrator processing
        normalized_order = {
            'id': order.get('id'),
            'symbol': order.get('symbol', exchange_symbol),
            'type': order.get('type'),
            'side': order.get('side'),
            'amount': order.get('amount'),
            'price': order.get('price'),
            'status': order.get('status', 'open').lower(),  # Normalize to lowercase
            'filled': order.get('filled', 0),  # Amount filled
            'remaining': order.get('remaining', order.get('amount', 0)),
            'average': order.get('average'),  # Average execution price
            'timestamp': order.get('timestamp'),
            'datetime': order.get('datetime'),
            'fee': order.get('fee'),
            'trades': order.get('trades', []),
            'info': order.get('info', {})  # Raw exchange response
        }
        
        # Handle exchange-specific field mapping
        if exchange.lower() == 'cryptocom':
            # Crypto.com specific normalization
            info = order.get('info', {})
            if info:
                # Map crypto.com specific fields if needed
                if 'cum_qty' in info:
                    normalized_order['filled'] = float(info.get('cum_qty', 0))
                if 'avg_price' in info:
                    normalized_order['average'] = float(info.get('avg_price', 0)) if info.get('avg_price') else None
                if info.get('status') == 'FILLED':
                    normalized_order['status'] = 'filled'
                elif info.get('status') == 'PARTIALLY_FILLED':
                    normalized_order['status'] = 'open'
        
        elif exchange.lower() == 'binance':
            # Binance specific normalization
            info = order.get('info', {})
            if info:
                if 'executedQty' in info:
                    normalized_order['filled'] = float(info.get('executedQty', 0))
                if info.get('status') == 'FILLED':
                    normalized_order['status'] = 'filled'
                    
        elif exchange.lower() == 'bybit':
            # Bybit specific normalization
            info = order.get('info', {})
            if info:
                if 'cumExecQty' in info:
                    normalized_order['filled'] = float(info.get('cumExecQty', 0))
                if 'avgPrice' in info:
                    normalized_order['average'] = float(info.get('avgPrice', 0)) if info.get('avgPrice') else None
                if info.get('orderStatus') == 'Filled':
                    normalized_order['status'] = 'filled'
        
        logger.info(f"📋 Order response normalized: {exchange} order {normalized_order.get('id')} - status={normalized_order.get('status')}, filled={normalized_order.get('filled')}, avg={normalized_order.get('average')}")
        
        # Register WebSocket callback for order updates if available
        if 'id' in order:
            handler = websocket_manager.get_handler(exchange)
            if handler and handler.is_connected:
                def order_callback(status, data):
                    logger.info(f"Order {order['id']} on {exchange} updated to {status}")
                
                handler.register_order_callback(order['id'], order_callback)
        
        return {"order": normalized_order, "exchange": exchange, "symbol": exchange_symbol}
        
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

@app.put("/api/v1/trading/order/{exchange}/{order_id}")
async def modify_order(exchange: str, order_id: str, modification: dict):
    """
    Modify an existing order (price, quantity, etc.) - ORDER MODIFICATION IMPROVEMENT
    
    CCXT Support by Exchange:
    - Binance: ✅ editOrder() - Full support
    - Bybit: ✅ editOrder() - Full support  
    - Crypto.com: ❌ No support - will return 501
    
    Request body:
    {
        "symbol": "BTC/USDC",
        "new_price": 50000.0,
        "new_amount": 0.1  (optional)
    }
    """
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
        
        # Check if exchange supports order modification
        if not hasattr(exchange_instance, 'edit_order'):
            raise HTTPException(
                status_code=501, 
                detail=f"Exchange {exchange} does not support order modification. Use cancel-and-recreate instead."
            )
        
        # Extract modification parameters
        symbol = modification.get('symbol')
        new_price = modification.get('new_price')
        new_amount = modification.get('new_amount')
        
        if not symbol:
            raise HTTPException(status_code=400, detail="Symbol is required for order modification")
        
        if not new_price and not new_amount:
            raise HTTPException(status_code=400, detail="Either new_price or new_amount must be specified")
        
        # Convert symbol to exchange format  
        exchange_symbol = symbol
        
        # Load markets if not already loaded
        if not hasattr(exchange_instance, 'markets') or not exchange_instance.markets:
            await exchange_instance.load_markets()
        
        # Validate symbol exists
        if symbol not in exchange_instance.markets:
            available_symbols = list(exchange_instance.markets.keys())[:10]  # First 10 for reference
            raise HTTPException(
                status_code=400, 
                detail=f"Symbol {symbol} not found on {exchange}. Available symbols include: {available_symbols}"
            )
        
        start_time = time.time()
        
        # Get existing order details to determine side and current amount
        try:
            existing_order = await exchange_instance.fetch_order(order_id, exchange_symbol)
            order_side = existing_order.get('side', 'sell')  # Default to sell for trailing stops
            order_amount = existing_order.get('amount', new_amount)
            
            logger.info(f"🔄 Modifying order {order_id} on {exchange}: symbol={symbol}, side={order_side}, new_price={new_price}, new_amount={new_amount}")
            
            # Use existing amount if new_amount not specified
            if new_amount is None:
                new_amount = order_amount
            
            # Build edit parameters based on what's being modified
            edit_params = {}
            
            result = await exchange_instance.edit_order(
                id=order_id,
                symbol=exchange_symbol,
                type='limit',  # Most trailing stops are limit orders
                side=order_side,     # Use existing side
                amount=new_amount,   # New amount or existing amount
                price=new_price,     # New price
                params=edit_params
            )
            
            response_time = time.time() - start_time
            
            # Update health
            exchanges[exchange]['health']['status'] = 'healthy'
            exchanges[exchange]['health']['response_time'] = response_time
            exchanges[exchange]['health']['last_check'] = datetime.utcnow()
            
            logger.info(f"✅ Order {order_id} modified successfully on {exchange} - New price: ${new_price:.6f}" if new_price else f"✅ Order {order_id} modified successfully on {exchange}")
            
            return {
                "success": True,
                "order": result,
                "exchange": exchange,
                "order_id": order_id,
                "modifications": {
                    "new_price": new_price,
                    "new_amount": new_amount
                },
                "response_time": response_time
            }
            
        except Exception as edit_error:
            # Handle common modification errors gracefully
            error_message = str(edit_error).lower() if edit_error else ""
            
            if any(phrase in error_message for phrase in ['order not found', 'does not exist', 'invalid order', 'already filled', 'already executed']):
                # Order was likely filled or already cancelled
                logger.warning(f"Order {order_id} on {exchange} cannot be modified - likely filled or cancelled: {edit_error}")
                return {
                    "success": False,
                    "error": "order_not_modifiable",
                    "message": "Order likely filled, cancelled, or does not exist",
                    "exchange": exchange,
                    "order_id": order_id,
                    "fallback_required": True
                }
            elif any(phrase in error_message for phrase in ['not supported', 'not allowed', 'invalid modification']):
                # Exchange doesn't support this type of modification
                logger.warning(f"Order modification not supported for {order_id} on {exchange}: {edit_error}")
                return {
                    "success": False,
                    "error": "modification_not_supported",
                    "message": f"Exchange {exchange} does not support this type of order modification",
                    "exchange": exchange,
                    "order_id": order_id,
                    "fallback_required": True
                }
            else:
                # Re-raise other errors
                raise edit_error
        
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, f"modify_order({order_id})")
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
            exchange_symbol = _normalize_cryptocom_symbol_for_ccxt(symbol)
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

        # Use unsigned public client so OHLCV works when API keys are invalid or IP-restricted
        exchange_instance = await get_public_market_client(exchange)

        if not hasattr(exchange_instance, "fetch_ohlcv"):
            raise Exception(
                f"Invalid exchange instance for {exchange}: missing fetch_ohlcv method"
            )

        start_time = time.time()
        ohlcv = await exchange_instance.fetch_ohlcv(
            exchange_symbol, timeframe=timeframe, limit=limit
        )
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
        
        # Handle exchanges that require symbol for fetch_orders
        if exchange == 'binance' and not symbol:
            # Binance requires symbol for both fetch_orders and fetch_my_trades
            # Return empty result since we can't fetch all orders without symbol
            logger.warning(f"⚠️ Binance requires symbol for order history, returning empty result")
            return {"orders": []}
        else:
            # Standard fetch_orders for other exchanges or when symbol is provided
            if not hasattr(exchange_instance, 'fetch_orders'):
                raise HTTPException(status_code=500, detail=f"Invalid exchange instance for {exchange}: missing fetch_orders method")
            orders = await exchange_instance.fetch_orders(symbol=symbol, since=since, limit=limit)
            return {"orders": orders}
            
    except HTTPException:
        raise
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, "get_orders_history")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/market/info/{exchange}/{symbol:path}")
async def get_market_info(exchange: str, symbol: str):
    """Get market information including precision for a trading pair"""
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    try:
        await exchange_manager._rate_limit(exchange)
        exchange_instance = exchanges[exchange]['instance']
        
        # Convert symbol format for different exchanges
        exchange_symbol = symbol
        if exchange == 'cryptocom':
            exchange_symbol = _normalize_cryptocom_symbol_for_ccxt(symbol)
        elif exchange in ['binance', 'bybit']:
            if '/' not in symbol:
                if len(symbol) >= 6 and symbol.endswith('USD'):
                    base = symbol[:-3]
                    exchange_symbol = f"{base}/USD"
                elif len(symbol) >= 7 and symbol.endswith('USDC'):
                    base = symbol[:-4]
                    exchange_symbol = f"{base}/USDC"
        
        # Get market information
        markets = await exchange_instance.fetch_markets()
        market_info = None
        
        for market in markets:
            if market['symbol'] == exchange_symbol:
                market_info = market
                break
        
        if not market_info:
            raise HTTPException(status_code=404, detail=f"Market {exchange_symbol} not found")
        
        # Extract precision information
        precision_info = {
            'symbol': market_info['symbol'],
            'base': market_info['base'],
            'quote': market_info['quote'],
            'active': market_info['active'],
            'precision': {
                'amount': market_info.get('precision', {}).get('amount'),
                'price': market_info.get('precision', {}).get('price')
            },
            'limits': {
                'amount': market_info.get('limits', {}).get('amount'),
                'price': market_info.get('limits', {}).get('price'),
                'cost': market_info.get('limits', {}).get('cost')
            }
        }
        
        return precision_info
        
    except HTTPException:
        raise
    except Exception as e:
        await exchange_manager._handle_exchange_error(exchange, e, "get_market_info")
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
@app.get("/api/v1/websocket/monitor/status")
async def get_websocket_monitor_status():
    """Aggregated monitor payload (must be registered before ``/{exchange}/status``).

    Orchestrator's ``WebSocketPriceFeed`` calls this path; if only
    ``/api/v1/websocket/{exchange}/status`` exists, ``exchange=monitor`` matches first
    and returns ``Handler not found``.
    """
    return await get_all_websocket_status()


@app.get("/api/v1/websocket/{exchange}/status")
async def get_websocket_status(exchange: str):
    """Get WebSocket connection status for an exchange"""
    handler = websocket_manager.get_handler(exchange)
    if not handler:
        return {"connected": False, "reason": "Handler not found"}
    
    return {
        "connected": handler.is_connected,
        "last_update": handler.last_update,
        "registered_callbacks": len(handler.order_callbacks),
        "listener_task_running": handler.listener_task is not None and not handler.listener_task.done() if handler.listener_task else False,
        "reconnect_task_running": handler.reconnect_task is not None and not handler.reconnect_task.done() if handler.reconnect_task else False,
        "subscribed_symbols": len(handler.subscribed_symbols),
        "subscribed_symbols_list": list(handler.subscribed_symbols),
        "websocket_url": handler.websocket_url
    }

@app.post("/api/v1/websocket/{exchange}/restart")
async def restart_websocket_connection(exchange: str):
    """Emergency restart WebSocket connection for an exchange"""
    try:
        if exchange.lower() == "cryptocom":
            if cryptocom_websocket and hasattr(cryptocom_websocket, 'connection_manager'):
                logger.warning(f"🔧 Manual restart requested for {exchange} WebSocket")
                await cryptocom_websocket.connection_manager._emergency_restart()
                return {
                    "status": "restarted",
                    "exchange": exchange,
                    "message": "WebSocket connection restarted successfully"
                }
            else:
                return {
                    "status": "error",
                    "exchange": exchange,
                    "message": "WebSocket not available for restart"
                }
        else:
            return {
                "status": "error",
                "exchange": exchange,
                "message": f"Restart not implemented for {exchange}"
            }
    except Exception as e:
        logger.error(f"❌ Failed to restart {exchange} WebSocket: {e}")
        return {
            "status": "error",
            "exchange": exchange,
            "message": f"Restart failed: {str(e)}"
        }

@app.get("/api/v1/websocket/monitor/all")
async def get_all_websocket_status():
    """Get comprehensive WebSocket status for all exchanges with ticker data"""
    from datetime import datetime, timedelta
    
    result = {
        "timestamp": datetime.utcnow().isoformat(),
        "exchanges": {},
        "summary": {
            "total_exchanges": 0,
            "connected_exchanges": 0,
            "active_listeners": 0,
            "total_subscribed_symbols": 0,
            "total_ticker_cache_entries": 0
        }
    }
    
    for exchange_name, handler in websocket_manager.handlers.items():
        # Calculate time since last update
        last_update_age = None
        if handler.last_update:
            try:
                last_update_dt = datetime.fromisoformat(handler.last_update)
                last_update_age = (datetime.utcnow() - last_update_dt).total_seconds()
            except:
                last_update_age = None
        
        # Get ticker cache entries for this exchange
        exchange_tickers = {}
        ticker_count = 0
        for cache_key, cache_data in ticker_cache.items():
            if cache_key.startswith(f"{exchange_name}:"):
                symbol = cache_key.split(':', 1)[1]
                ticker_age = None
                if cache_data.get('timestamp'):
                    try:
                        ticker_dt = datetime.fromisoformat(cache_data['timestamp'])
                        ticker_age = (datetime.utcnow() - ticker_dt).total_seconds()
                    except:
                        ticker_age = None
                
                exchange_tickers[symbol] = {
                    "last_price": cache_data.get('last'),
                    "bid": cache_data.get('bid'),
                    "ask": cache_data.get('ask'),
                    "timestamp": cache_data.get('timestamp'),
                    "age_seconds": ticker_age
                }
                ticker_count += 1
        
        exchange_info = {
            "connected": handler.is_connected,
            "websocket_url": handler.websocket_url,
            "last_update": handler.last_update,
            "last_update_age_seconds": last_update_age,
            "listener_task_running": handler.listener_task is not None and not handler.listener_task.done() if handler.listener_task else False,
            "reconnect_task_running": handler.reconnect_task is not None and not handler.reconnect_task.done() if handler.reconnect_task else False,
            "subscribed_symbols": list(handler.subscribed_symbols),
            "subscribed_count": len(handler.subscribed_symbols),
            "registered_callbacks": len(handler.order_callbacks),
            "ticker_cache_entries": ticker_count,
            "cached_tickers": exchange_tickers
        }
        
        result["exchanges"][exchange_name] = exchange_info
        
        # Update summary
        result["summary"]["total_exchanges"] += 1
        if handler.is_connected:
            result["summary"]["connected_exchanges"] += 1
        if handler.listener_task and not handler.listener_task.done():
            result["summary"]["active_listeners"] += 1
        result["summary"]["total_subscribed_symbols"] += len(handler.subscribed_symbols)
        result["summary"]["total_ticker_cache_entries"] += ticker_count
    
    return result

@app.get("/api/v1/websocket/status")
async def get_websocket_status():
    """Get WebSocket connection status for all exchanges"""
    try:
        result = {
            "timestamp": datetime.utcnow().isoformat(),
            "exchanges": {}
        }
        
        for exchange_name in ['binance', 'bybit', 'cryptocom']:
            handler = websocket_manager.get_handler(exchange_name)
            if handler:
                result["exchanges"][exchange_name] = {
                    "connected": handler.is_connected,
                    "last_update": handler.last_update,
                    "subscribed_symbols": len(handler.subscribed_symbols)
                }
            else:
                result["exchanges"][exchange_name] = {
                    "connected": False,
                    "handler_exists": False
                }
        
        return result
    except Exception as e:
        logger.error(f"Error getting WebSocket status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/exchange/{exchange_name}/statistics")
async def get_exchange_statistics(exchange_name: str):
    """Get exchange-specific statistics"""
    try:
        if exchange_name not in exchanges:
            raise HTTPException(status_code=404, detail=f"Exchange {exchange_name} not found")
        
        exchange_info = exchanges[exchange_name]
        handler = websocket_manager.get_handler(exchange_name)
        
        stats = {
            "exchange": exchange_name,
            "status": exchange_info['health']['status'],
            "last_check": exchange_info['health']['last_check'],
            "error_count": exchange_info['health']['error_count'],
            "websocket_connected": handler.is_connected if handler else False,
            "subscribed_symbols": len(handler.subscribed_symbols) if handler else 0,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        return stats
    except Exception as e:
        logger.error(f"Error getting statistics for {exchange_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/websocket/monitor/ticker-cache")
async def get_ticker_cache_status():
    """Get detailed ticker cache information across all exchanges"""
    from datetime import datetime
    
    result = {
        "timestamp": datetime.utcnow().isoformat(),
        "total_entries": len(ticker_cache),
        "exchanges": {},
        "recent_updates": []
    }
    
    # Group by exchange
    for cache_key, cache_data in ticker_cache.items():
        if ':' not in cache_key:
            continue
            
        exchange_name, symbol = cache_key.split(':', 1)
        
        if exchange_name not in result["exchanges"]:
            result["exchanges"][exchange_name] = {
                "ticker_count": 0,
                "tickers": {}
            }
        
        # Calculate age
        ticker_age = None
        if cache_data.get('timestamp'):
            try:
                ticker_dt = datetime.fromisoformat(cache_data['timestamp'])
                ticker_age = (datetime.utcnow() - ticker_dt).total_seconds()
            except:
                ticker_age = None
        
        ticker_info = {
            "last_price": cache_data.get('last'),
            "bid": cache_data.get('bid'),
            "ask": cache_data.get('ask'),
            "timestamp": cache_data.get('timestamp'),
            "age_seconds": ticker_age
        }
        
        result["exchanges"][exchange_name]["tickers"][symbol] = ticker_info
        result["exchanges"][exchange_name]["ticker_count"] += 1
        
        # Track recent updates (less than 60 seconds old)
        if ticker_age and ticker_age < 60:
            result["recent_updates"].append({
                "exchange": exchange_name,
                "symbol": symbol,
                "age_seconds": ticker_age,
                "last_price": cache_data.get('last')
            })
    
    # Sort recent updates by age
    result["recent_updates"].sort(key=lambda x: x["age_seconds"] or 9999)
    
    return result

@app.post("/api/v1/websocket/{exchange}/subscribe")
async def subscribe_symbols(exchange: str, payload: Dict[str, Any]):
    """Subscribe to live tickers for given symbols (symbol format without slash, e.g., BTCUSD).

    WS-FIX (cryptocom 204 root cause): for cryptocom we ALSO push subscriptions
    through the long-lived ``cryptocom_websocket.market_websocket`` (the
    integration handler with heartbeats + reconnect), because the bare
    ``WebSocketManager`` connection to ``stream.crypto.com/exchange/v1/market`` is
    routinely closed by the exchange (close code 1000) between the
    ``is_connected`` check and the actual send, dropping subscribe frames on
    the floor. The integration handler is the source of truth for ticker
    updates and survives reconnects.
    """
    symbols = payload.get('symbols') or []
    if not isinstance(symbols, list) or not symbols:
        raise HTTPException(status_code=400, detail="symbols must be a non-empty list")

    pushed_anywhere = False

    # Try the standard WebSocketManager-based handler first for non-cryptocom exchanges.
    # For Crypto.com we prefer the integration market socket because the generic handler
    # is frequently closed by the server (close code 1000) between health check and send.
    handler = websocket_manager.get_handler(exchange)
    if exchange.lower() != "cryptocom" and handler and handler.is_connected:
        try:
            await handler.subscribe(symbols)
            pushed_anywhere = True
        except Exception as e:
            logger.warning(f"[ws subscribe] manager handler failed for {exchange}: {e}")

    # For cryptocom, push through the integration WS (resilient connection) first.
    if exchange.lower() == 'cryptocom':
        try:
            market_ws = getattr(cryptocom_websocket, 'market_websocket', None)
            if market_ws and getattr(market_ws, 'is_connected', False):
                pushed_count = 0
                for sym in symbols:
                    # Normalize to Crypto.com instrument format (BASE_QUOTE).
                    # E.g. "ACT/USD" -> "ACT_USD", "ACTUSD" -> "ACT_USD",
                    # "ACT_USD" stays "ACT_USD". This matches the subscription
                    # channel name "ticker.ACT_USD" that produces a cache key
                    # "cryptocom:ACT_USD" — the same key the orchestrator's
                    # ticker-live lookup computes.
                    s = str(sym).upper().strip()
                    if '/' in s:
                        instrument = s.replace('/', '_')
                    elif '_' in s:
                        instrument = s
                    elif s.endswith('USDC'):
                        instrument = f"{s[:-4]}_USDC"
                    elif s.endswith('USDT'):
                        instrument = f"{s[:-4]}_USDT"
                    elif s.endswith('USD'):
                        instrument = f"{s[:-3]}_USD"
                    else:
                        instrument = s
                    # Skip if already subscribed (the integration tracks its set).
                    if instrument in getattr(market_ws, 'subscribed_symbols', set()):
                        continue
                    try:
                        ok = await market_ws.subscribe_to_ticker(instrument)
                        if ok:
                            pushed_count += 1
                    except Exception as ie:
                        logger.warning(f"[ws subscribe] cryptocom integration subscribe failed for {instrument}: {ie}")
                if pushed_count:
                    logger.info(f"[ws subscribe] cryptocom integration: subscribed to {pushed_count} new ticker(s)")
                    pushed_anywhere = True
            # Fallback to generic handler only if integration channel was unavailable.
            elif handler and handler.is_connected:
                try:
                    await handler.subscribe(symbols)
                    pushed_anywhere = True
                    logger.info("[ws subscribe] cryptocom fallback via generic handler")
                except Exception as e:
                    logger.warning(f"[ws subscribe] cryptocom fallback handler failed: {e}")
        except Exception as e:
            logger.warning(f"[ws subscribe] cryptocom integration push failed: {e}")

    if not pushed_anywhere:
        mgr = websocket_manager.get_handler(exchange)
        cryptocom_mw = None
        if exchange.lower() == "cryptocom":
            cryptocom_mw = getattr(cryptocom_websocket, "market_websocket", None)
        raise HTTPException(
            status_code=503,
            detail={
                "message": f"{exchange} WebSocket not available for subscription",
                "manager_handler_present": mgr is not None,
                "manager_handler_connected": bool(mgr and mgr.is_connected),
                "cryptocom_market_ws_present": cryptocom_mw is not None,
                "cryptocom_market_ws_connected": bool(
                    cryptocom_mw and getattr(cryptocom_mw, "is_connected", False)
                ),
            },
        )

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

# Health Monitoring Endpoints
@app.get("/api/v1/health/system")
async def get_system_health():
    """Get comprehensive system health status"""
    if not health_monitor:
        raise HTTPException(status_code=503, detail="Health monitoring not initialized")
    
    try:
        system_health = await health_monitor.get_current_health()
        return system_health.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting system health: {e}")

@app.get("/api/v1/health/history")
async def get_health_history(hours: int = 1):
    """Get health history for specified number of hours"""
    if not health_monitor:
        raise HTTPException(status_code=503, detail="Health monitoring not initialized")
    
    try:
        history = health_monitor.get_health_history(hours)
        return {
            'history_hours': hours,
            'total_entries': len(history),
            'health_history': [h.to_dict() for h in history]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting health history: {e}")

@app.get("/api/v1/health/metrics")
async def get_health_metrics():
    """Get health monitoring metrics"""
    if not health_monitor:
        raise HTTPException(status_code=503, detail="Health monitoring not initialized")
    
    try:
        return health_monitor.get_metrics()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting health metrics: {e}")

@app.post("/api/v1/health/check")
async def perform_immediate_health_check():
    """Perform immediate health check (outside of monitoring loop)"""
    if not health_monitor:
        raise HTTPException(status_code=503, detail="Health monitoring not initialized")
    
    try:
        system_health = await health_monitor.perform_health_checks()
        return {
            'message': 'Immediate health check completed',
            'health_status': system_health.to_dict()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error performing health check: {e}")

@app.get("/api/v1/health/websocket/detailed")
async def get_detailed_websocket_health():
    """Get detailed WebSocket health information"""
    try:
        # Get Binance WebSocket status
        binance_status = binance_websocket.get_status()
        
        # Get WebSocket manager status for other exchanges
        websocket_statuses = {}
        for exchange_name in ['binance', 'bybit', 'cryptocom']:
            handler = websocket_manager.get_handler(exchange_name)
            if handler:
                websocket_statuses[exchange_name] = {
                    'connected': handler.is_connected,
                    'last_update': handler.last_update,
                    'subscribed_symbols': len(handler.subscribed_symbols)
                }
            else:
                websocket_statuses[exchange_name] = {
                    'connected': False,
                    'handler_exists': False
                }
        
        return {
            'binance_user_data_stream': binance_status,
            'exchange_websockets': websocket_statuses,
            'timestamp': datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting WebSocket health: {e}")

# Startup and shutdown events
async def _background_websocket_and_health_startup():
    """Binance listen-key + WS integrations can block on external I/O without timeouts.
    Running this after the process accepts traffic keeps ``GET /health`` reachable so
    Docker/orchestrator/dashboard do not see 'All connection attempts failed'."""
    global health_monitor

    try:
        logger.info("🚀 Initializing Binance WebSocket integration (background)")
        if await binance_websocket.initialize():
            if await binance_websocket.start():
                logger.info("✅ Binance WebSocket integration started successfully")
            else:
                logger.warning(
                    "⚠️ Binance WebSocket integration initialized but stream did not start "
                    "(listen key / user stream — REST tickers may still work)"
                )
        else:
            logger.warning("⚠️ Binance WebSocket integration failed to initialize")
    except Exception as e:
        logger.error(f"❌ Error initializing Binance WebSocket integration: {e}")

    try:
        logger.info("🏢 Initializing Crypto.com WebSocket integration (background)")
        if await cryptocom_websocket.initialize():
            if await cryptocom_websocket.start():
                logger.info("✅ Crypto.com WebSocket integration started successfully")
            else:
                logger.warning(
                    "⚠️ Crypto.com WebSocket initialized but start() returned false"
                )
        else:
            logger.warning("⚠️ Crypto.com WebSocket integration failed to initialize")
    except Exception as e:
        logger.error(f"❌ Error initializing Crypto.com WebSocket integration: {e}")

    try:
        logger.info("🚀 Initializing Bybit WebSocket integration (background)")
        from bybit_websocket_integration import (
            initialize_bybit_websocket,
            start_bybit_websocket_task,
        )

        initialize_bybit_websocket()
        await start_bybit_websocket_task()
        logger.info("✅ Bybit WebSocket background start task finished (see logs above for outcome)")
    except Exception as e:
        logger.error(f"❌ Error initializing Bybit WebSocket integration: {e}")

    try:
        logger.info("🏥 Initializing health monitoring system (background)")
        health_monitor = HealthMonitorService(check_interval=30)
        health_monitor.register_health_check(
            "binance-websocket", binance_websocket_health_check
        )
        health_monitor.register_health_check(
            "cryptocom-websocket", cryptocom_websocket_health_check
        )
        health_monitor.register_health_check("redis", redis_health_check)
        await health_monitor.start_monitoring()
        logger.info("✅ Health monitoring system started successfully")
    except Exception as e:
        logger.error(f"❌ Error initializing health monitoring: {e}")


@app.on_event("startup")
async def startup_event():
    """Initialize exchanges on startup — keep port open before long-running WS setup."""
    await initialize_exchanges()
    asyncio.create_task(_background_websocket_and_health_startup())
    logger.info(
        "✅ Exchange CCXT core ready; WebSocket + health monitor starting in background"
    )

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    await websocket_manager.shutdown_all()
    
    # Stop health monitoring
    if health_monitor:
        try:
            await health_monitor.stop_monitoring()
            logger.info("✅ Health monitoring stopped")
        except Exception as e:
            logger.error(f"❌ Error stopping health monitoring: {e}")
    
    # Stop WebSocket integrations
    try:
        await binance_websocket.stop()
        logger.info("✅ Binance WebSocket integration stopped")
    except Exception as e:
        logger.error(f"❌ Error stopping Binance WebSocket integration: {e}")
        
    try:
        await cryptocom_websocket.stop()
        logger.info("✅ Crypto.com WebSocket integration stopped")
    except Exception as e:
        logger.error(f"❌ Error stopping Crypto.com WebSocket integration: {e}")
    
    try:
        from bybit_websocket_integration import bybit_manager
        if bybit_manager:
            await bybit_manager.stop()
            logger.info("✅ Bybit WebSocket integration stopped")
    except Exception as e:
        logger.error(f"❌ Error stopping Bybit WebSocket integration: {e}")
    
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