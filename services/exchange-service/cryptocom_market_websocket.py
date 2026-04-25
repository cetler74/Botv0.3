"""
Crypto.com Market Data WebSocket Implementation - Version 1.0.0
Real-time ticker/price feeds for Crypto.com exchange
"""

import asyncio
import json
import logging
import time
import websockets
from datetime import datetime, timezone
from typing import Dict, Any, Set, Optional, Callable

logger = logging.getLogger(__name__)

class CryptocomMarketWebSocket:
    """
    Crypto.com Market Data WebSocket for real-time price feeds
    Subscribes to ticker channels and updates ticker cache
    """
    
    def __init__(self, 
                 websocket_url: str = "wss://stream.crypto.com/exchange/v1/market",
                 max_reconnect_attempts: int = 10,
                 reconnect_delay: int = 5,
                 heartbeat_interval: int = 20):
        """
        Initialize Crypto.com Market Data WebSocket
        
        Args:
            websocket_url: Crypto.com market data WebSocket URL
            max_reconnect_attempts: Maximum reconnection attempts
            reconnect_delay: Delay between reconnection attempts (seconds)
            heartbeat_interval: Heartbeat interval (seconds)
        """
        self.websocket_url = websocket_url
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_delay = reconnect_delay
        self.heartbeat_interval = heartbeat_interval
        
        # Connection management
        self.websocket = None
        self.is_connected = False
        self.is_running = False
        self.should_stop = False
        
        # Subscribed symbols
        self.subscribed_symbols: Set[str] = set()
        
        # Callbacks
        self.ticker_callbacks: list[Callable] = []
        self.error_callbacks: list[Callable] = []
        self.connection_callbacks: list[Callable] = []
        
        # Tasks
        self.main_task = None
        self.heartbeat_task = None
        
        # Metrics
        self.metrics = {
            "connection_attempts": 0,
            "successful_connections": 0,
            "messages_received": 0,
            "ticker_updates": 0,
            "errors": 0,
            "last_message_time": None,
            "uptime_start": None,
            "reconnections": 0
        }
        
        # Message ID counter
        self.message_id = 1
        
        logger.info("📊 Crypto.com Market Data WebSocket initialized")
    
    async def start(self) -> bool:
        """Start the WebSocket connection and event processing"""
        try:
            logger.info("🚀 Starting Crypto.com Market Data WebSocket")
            
            self.should_stop = False
            self.is_running = True
            self.metrics["uptime_start"] = datetime.now(timezone.utc).isoformat()
            
            # Start main connection task
            self.main_task = asyncio.create_task(self._connection_loop())
            
            logger.info("✅ Crypto.com Market Data WebSocket started")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error starting Crypto.com Market Data WebSocket: {e}")
            self.is_running = False
            return False
    
    async def stop(self):
        """Stop the WebSocket connection"""
        try:
            logger.info("🛑 Stopping Crypto.com Market Data WebSocket")
            
            self.should_stop = True
            self.is_running = False
            
            # Cancel tasks
            if self.heartbeat_task:
                self.heartbeat_task.cancel()
            if self.main_task:
                self.main_task.cancel()
            
            # Close WebSocket connection
            if self.websocket:
                await self.websocket.close()
                self.websocket = None
                self.is_connected = False
            
            logger.info("✅ Crypto.com Market Data WebSocket stopped")
            
        except Exception as e:
            logger.error(f"❌ Error stopping Crypto.com Market Data WebSocket: {e}")
    
    async def _connection_loop(self):
        """Main connection loop with automatic reconnection"""
        reconnect_attempt = 0
        
        while not self.should_stop and reconnect_attempt < self.max_reconnect_attempts:
            try:
                self.metrics["connection_attempts"] += 1
                
                # Connect to WebSocket
                logger.info(f"🔌 Connecting to Crypto.com Market Data WebSocket (attempt {reconnect_attempt + 1})")
                
                async with websockets.connect(
                    self.websocket_url,
                    ping_interval=self.heartbeat_interval,
                    ping_timeout=10,
                    close_timeout=10
                ) as websocket:
                    self.websocket = websocket
                    self.is_connected = True
                    self.metrics["successful_connections"] += 1
                    
                    if reconnect_attempt > 0:
                        self.metrics["reconnections"] += 1
                        
                    logger.info("✅ Connected to Crypto.com Market Data WebSocket")
                    reconnect_attempt = 0  # Reset on successful connection
                    
                    # Notify connection callbacks
                    for callback in self.connection_callbacks:
                        try:
                            await callback(True)
                        except Exception as e:
                            logger.error(f"❌ Connection callback error: {e}")
                    
                    # Subscribe to default symbols + every previously-added symbol.
                    # WS-FIX: on reconnect we used to only re-subscribe to the 10
                    # hard-coded defaults, silently dropping every dynamic
                    # subscription (e.g. ACT_USD added via /api/v1/websocket
                    # /cryptocom/subscribe for an open trade). Crypto.com closes
                    # idle connections periodically, so without this, ticker-live
                    # would 204 forever for any non-default pair after the first
                    # disconnect → trail-stops degrade to fallback prices.
                    await self._resubscribe_all_symbols()
                    
                    # Process messages
                    async for message in websocket:
                        try:
                            await self._process_message(message)
                        except Exception as e:
                            logger.error(f"❌ Error processing message: {e}")
                            self.metrics["errors"] += 1
                            
                            # Notify error callbacks
                            for callback in self.error_callbacks:
                                try:
                                    await callback(e)
                                except Exception as cb_e:
                                    logger.error(f"❌ Error callback error: {cb_e}")
                        
                        if self.should_stop:
                            break
            
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"🔌 Crypto.com Market Data WebSocket connection closed: {e}")
                self.is_connected = False
                
            except Exception as e:
                logger.error(f"❌ Crypto.com Market Data WebSocket connection error: {e}")
                self.metrics["errors"] += 1
                self.is_connected = False
                
                # Notify error callbacks
                for callback in self.error_callbacks:
                    try:
                        await callback(e)
                    except Exception as cb_e:
                        logger.error(f"❌ Error callback error: {cb_e}")
            
            # Notify disconnection
            for callback in self.connection_callbacks:
                try:
                    await callback(False)
                except Exception as e:
                    logger.error(f"❌ Connection callback error: {e}")
            
            # Wait before reconnecting
            if not self.should_stop and reconnect_attempt < self.max_reconnect_attempts:
                reconnect_attempt += 1
                delay = self.reconnect_delay * reconnect_attempt  # Exponential backoff
                logger.info(f"⏰ Reconnecting in {delay} seconds (attempt {reconnect_attempt}/{self.max_reconnect_attempts})")
                await asyncio.sleep(delay)
        
        logger.warning("❌ Crypto.com Market Data WebSocket connection loop ended")
    
    async def _subscribe_to_default_symbols(self):
        """Subscribe to default trading symbols"""
        default_symbols = [
            "DOTUSD", "BTCUSD", "ETHUSD", "ADAUSD", "ALGOUSD", 
            "CROUSD", "AIXBTUSD", "MATICUSD", "SOLUSD", "AVAXUSD"
        ]
        
        for symbol in default_symbols:
            await self.subscribe_to_ticker(symbol)

    async def _resubscribe_all_symbols(self):
        """Re-subscribe to every symbol previously added (defaults + dynamic).

        Called on every (re)connect. Crypto.com periodically closes idle WS
        connections; without this, all dynamically-added subscriptions
        (e.g. for open-trade pairs not in the default 10) are silently dropped.
        """
        # Snapshot existing set BEFORE we clear it. The subscribe_to_ticker()
        # call below adds to self.subscribed_symbols, so we copy first to avoid
        # mutating the iterable mid-loop.
        previously_subscribed = set(self.subscribed_symbols)

        # Always include the baseline defaults so a fresh connection still
        # gets the high-volume pairs.
        baseline_defaults = {
            "DOTUSD", "BTCUSD", "ETHUSD", "ADAUSD", "ALGOUSD",
            "CROUSD", "AIXBTUSD", "MATICUSD", "SOLUSD", "AVAXUSD",
        }
        target_symbols = previously_subscribed | baseline_defaults

        # Clear and re-subscribe so subscribe_to_ticker() actually re-sends the
        # subscription frame for every symbol (it skips duplicates by design).
        self.subscribed_symbols.clear()

        logger.info(
            f"📡 Re-subscribing to {len(target_symbols)} Crypto.com ticker(s) "
            f"after (re)connect: {sorted(target_symbols)}"
        )
        ok = 0
        for symbol in target_symbols:
            if await self.subscribe_to_ticker(symbol):
                ok += 1
        logger.info(f"📡 Crypto.com ticker re-subscription complete: {ok}/{len(target_symbols)} succeeded")
    
    async def subscribe_to_ticker(self, symbol: str):
        """Subscribe to ticker updates for a symbol"""
        if not self.is_connected or not self.websocket:
            logger.warning(f"Cannot subscribe to {symbol} - not connected")
            return False
        
        try:
            # Crypto.com ticker channel format: ticker.{instrument_name}
            channel = f"ticker.{symbol}"
            
            subscription_msg = {
                "id": self.message_id,
                "method": "subscribe",
                "params": {
                    "channels": [channel]
                },
                "nonce": int(time.time() * 1000)
            }
            
            await self.websocket.send(json.dumps(subscription_msg))
            self.message_id += 1
            self.subscribed_symbols.add(symbol)
            
            logger.info(f"📡 Subscribed to Crypto.com ticker: {symbol}")
            return True
            
        except websockets.exceptions.ConnectionClosed as e:
            # Connection can close normally (1000) between "is_connected" check and send.
            # Mark disconnected so caller can retry after reconnect loop.
            self.is_connected = False
            logger.warning(f"🔌 Subscription skipped for {symbol} - socket closed: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Error subscribing to ticker {symbol}: {e}")
            return False
    
    async def _process_message(self, message_str: str):
        """Process incoming WebSocket message"""
        try:
            message = json.loads(message_str)
            self.metrics["messages_received"] += 1
            self.metrics["last_message_time"] = datetime.now(timezone.utc).isoformat()
            
            # Check for subscription confirmations
            if message.get("method") == "subscribe":
                result = message.get("result", {})
                if result.get("subscription"):
                    logger.info(f"✅ Subscription confirmed: {result['subscription']}")
                return
            
            # Process ticker data
            if "result" in message and "subscription" in message["result"]:
                subscription = message["result"]["subscription"]
                if subscription.startswith("ticker."):
                    await self._handle_ticker_message(message)
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid JSON message: {e}")
        except Exception as e:
            logger.error(f"❌ Error processing message: {e}")
    
    async def _handle_ticker_message(self, message: Dict[str, Any]):
        """Handle ticker update message"""
        try:
            result = message.get("result", {})
            subscription = result.get("subscription", "")
            data = result.get("data", [])
            
            # Extract symbol from subscription (ticker.SYMBOL)
            if not subscription.startswith("ticker."):
                return
            
            symbol = subscription.replace("ticker.", "")
            
            for ticker_data in data:
                # Crypto.com ticker data fields:
                # h: 24h high, l: 24h low, a: latest price, v: volume
                # b: best bid, k: best ask
                
                last_price = float(ticker_data.get("a", 0))  # Latest price
                bid_price = float(ticker_data.get("b", 0))   # Best bid
                ask_price = float(ticker_data.get("k", 0))   # Best ask
                
                if last_price > 0:
                    # Update ticker cache
                    for callback in self.ticker_callbacks:
                        try:
                            await callback("cryptocom", symbol, last_price, bid_price, ask_price)
                        except Exception as e:
                            logger.error(f"❌ Ticker callback error: {e}")
                    
                    self.metrics["ticker_updates"] += 1
                    logger.debug(f"📊 Crypto.com ticker update: {symbol} = ${last_price:.8f}")
        
        except Exception as e:
            logger.error(f"❌ Error handling ticker message: {e}")
    
    def add_ticker_callback(self, callback: Callable):
        """Add ticker update callback"""
        self.ticker_callbacks.append(callback)
    
    def add_error_callback(self, callback: Callable):
        """Add error callback"""
        self.error_callbacks.append(callback)
    
    def add_connection_callback(self, callback: Callable):
        """Add connection status callback"""
        self.connection_callbacks.append(callback)
    
    def get_status(self) -> Dict[str, Any]:
        """Get WebSocket status"""
        return {
            "connected": self.is_connected,
            "running": self.is_running,
            "subscribed_symbols": list(self.subscribed_symbols),
            "websocket_url": self.websocket_url,
            "metrics": self.metrics.copy()
        }
    
    def reset_metrics(self):
        """Reset all metrics"""
        self.metrics = {
            "connection_attempts": 0,
            "successful_connections": 0,
            "messages_received": 0,
            "ticker_updates": 0,
            "errors": 0,
            "last_message_time": None,
            "uptime_start": datetime.now(timezone.utc).isoformat(),
            "reconnections": 0
        }
        logger.info("📊 Crypto.com Market Data WebSocket metrics reset")